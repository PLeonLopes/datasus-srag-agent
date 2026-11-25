"""Grafo do agente (LangGraph).

    orquestrador (LLM + tools) -><- tools (código, auditadas)
        → completude (código: força as tools obrigatórias que o modelo não chamou)
        → redator (LLM) → guardrails (código; refaz com feedback ou usa texto de reserva)
        → montagem (código)

O LLM decide a ordem das tools e o que investigar com SQL; o código garante o mínimo obrigatório,
limita o custo (rodadas de tools e tentativas) e valida o texto antes de ele entrar no relatório.
"""

import json
import re
import time
from collections.abc import Callable
from typing import Annotated, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from srag_agent.agent.prompts import WRITER_FEEDBACK, WRITER_SYSTEM, WRITER_TASK
from srag_agent.agent.report import Analysis, ReportContext, fallback_explanation, render_report
from srag_agent.agent.toolkit import RunToolkit
from srag_agent.governance.audit import AuditLog
from srag_agent.governance.guardrails import check_explanation
from srag_agent.llm.errors import is_transient, status_code

MAX_TOOL_ROUNDS = 4
MAX_WRITER_ATTEMPTS = 2
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class ReportState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    orchestrator_failed: bool
    draft: str | None
    attempts: int
    feedback: list[str]
    analysis: Analysis
    report_markdown: str


def build_graph(
    llm: BaseChatModel,
    toolkit: RunToolkit,
    audit: AuditLog,
    context: ReportContext,
):
    orchestrator_llm = llm.bind_tools(toolkit.tools())
    call_llm = _llm_caller(audit)

    def orchestrator(state: ReportState) -> dict:
        response = call_llm(
            "orquestrador", orchestrator_llm, state["messages"], state.get("tool_rounds", 0) + 1
        )
        if response is None:
            return {"orchestrator_failed": True}
        return {"messages": [response]}

    def after_orchestrator(state: ReportState) -> str:
        last = state["messages"][-1]
        if state.get("orchestrator_failed") or not isinstance(last, AIMessage) or not last.tool_calls:
            return "completeness"
        if state.get("tool_rounds", 0) >= MAX_TOOL_ROUNDS:
            audit.record("decision.tool_round_limit", "degraded", max_rounds=MAX_TOOL_ROUNDS)
            return "completeness"
        return "tools"

    def tools(state: ReportState) -> dict:
        messages = []
        for call in state["messages"][-1].tool_calls:
            content, ok = toolkit.run(call["name"], call.get("args") or {})
            messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="success" if ok else "error",
                )
            )
        return {"messages": messages, "tool_rounds": state.get("tool_rounds", 0) + 1}

    def completeness(state: ReportState) -> dict:
        for name in toolkit.missing_required():
            audit.record("decision.required_tool_forced", "degraded", tool=name, reason="o modelo não chamou")
            toolkit.run(name, {})
        return {}

    def writer(state: ReportState) -> dict:
        attempt = state.get("attempts", 0) + 1
        messages = _writer_messages(toolkit, context, state)
        response = call_llm("redator", llm, messages, attempt)
        draft = None if response is None else _text(response.content)
        return {"draft": draft, "attempts": attempt, "feedback": []}

    def guardrails(state: ReportState) -> dict:
        draft, attempt = state.get("draft"), state["attempts"]
        if draft is None:
            return _fallback("LLM indisponível")
        metrics = toolkit.evidence.metrics
        growth = [metrics.case_growth.value_pct, metrics.case_growth_short.value_pct]
        result = check_explanation(
            draft, toolkit.evidence.for_guardrail(), [g for g in growth if g is not None]
        )
        audit.record(
            "guardrail.output",
            "ok" if result.passed else "blocked",
            attempt=attempt,
            violations=result.violations,
        )
        if result.passed:
            return {"analysis": Analysis(draft, generated_by_llm=True, attempts=attempt)}
        if attempt < MAX_WRITER_ATTEMPTS:
            return {"feedback": result.violations}
        return _fallback(f"o texto do modelo foi recusado pelos guardrails em {attempt} tentativas")

    def _fallback(reason: str) -> dict:
        audit.record("decision.fallback_explanation", "degraded", reason=reason)
        text = fallback_explanation(toolkit.evidence.metrics)
        return {"analysis": Analysis(text, generated_by_llm=False, attempts=0, fallback_reason=reason)}

    def after_guardrails(state: ReportState) -> str:
        return "assemble" if state.get("analysis") else "writer"

    def assemble(state: ReportState) -> dict:
        return {"report_markdown": render_report(context, toolkit.evidence, state["analysis"])}

    graph = StateGraph(ReportState)
    graph.add_node("orchestrator", _audited(audit, "orchestrator", orchestrator))
    graph.add_node("tools", _audited(audit, "tools", tools))
    graph.add_node("completeness", _audited(audit, "completeness", completeness))
    graph.add_node("writer", _audited(audit, "writer", writer))
    graph.add_node("guardrails", _audited(audit, "guardrails", guardrails))
    graph.add_node("assemble", _audited(audit, "assemble", assemble))

    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges(
        "orchestrator", after_orchestrator, {"tools": "tools", "completeness": "completeness"}
    )
    graph.add_edge("tools", "orchestrator")
    graph.add_edge("completeness", "writer")
    graph.add_edge("writer", "guardrails")
    graph.add_conditional_edges("guardrails", after_guardrails, {"writer": "writer", "assemble": "assemble"})
    graph.add_edge("assemble", END)
    return graph.compile()


def _llm_caller(audit: AuditLog) -> Callable[[str, object, list[BaseMessage], int], AIMessage | None]:
    """Chama o LLM registrando prompt, resposta e duração; devolve None em falha temporária."""

    def call(node: str, runnable, messages: list[BaseMessage], attempt: int) -> AIMessage | None:
        started = time.perf_counter()
        try:
            response = runnable.invoke(messages)
        except Exception as error:
            transient = is_transient(error)
            audit.record(
                "llm.unavailable" if transient else "llm.error",
                "degraded" if transient else "error",
                node=node,
                attempt=attempt,
                error=type(error).__name__,
                http_status=status_code(error),
                message=str(error)[:300],  # diferencia cota da conta de limite do provedor
            )
            if not transient:
                raise
            return None
        audit.record(
            "llm.call",
            node=node,
            attempt=attempt,
            duration_ms=round((time.perf_counter() - started) * 1000),
            prompt=[{"role": message.type, "content": _text(message.content)} for message in messages],
            response=_text(response.content),
            tool_calls=[{"name": call["name"], "args": call.get("args")} for call in response.tool_calls],
            usage=response.usage_metadata,
        )
        return response

    return call


def _writer_messages(toolkit: RunToolkit, context: ReportContext, state: ReportState) -> list[BaseMessage]:
    evidence = toolkit.evidence
    payload = evidence.for_guardrail()
    task = WRITER_TASK.format(
        reference_date=evidence.metrics.reference_date.isoformat(),
        data_end=evidence.metrics.data_end.isoformat(),
        metrics=json.dumps(payload["metrics"], ensure_ascii=False, indent=1),
        charts=json.dumps(payload["charts"], ensure_ascii=False),
        sql=json.dumps(payload["sql"], ensure_ascii=False) if payload["sql"] else "Nenhuma investigação.",
        news=evidence.news.format_for_prompt() if evidence.news else "Notícias indisponíveis nesta execução.",
    )
    messages: list[BaseMessage] = [SystemMessage(WRITER_SYSTEM), HumanMessage(task)]
    if state.get("feedback") and state.get("draft"):
        violations = "\n".join(f"- {violation}" for violation in state["feedback"])
        messages += [AIMessage(state["draft"]), HumanMessage(WRITER_FEEDBACK.format(violations=violations))]
    return messages


def _audited(
    audit: AuditLog, name: str, node: Callable[[ReportState], dict]
) -> Callable[[ReportState], dict]:
    def wrapper(state: ReportState) -> dict:
        with audit.step(name):
            return node(state)

    return wrapper


def _text(content: str | list) -> str:
    """Só o texto da resposta: sem blocos de raciocínio (`thinking` da Anthropic, `<think>` do Qwen)."""
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) and block.get("type") == "text" else ""
            for block in content
        )
    return _THINK_BLOCK.sub("", content).strip()
