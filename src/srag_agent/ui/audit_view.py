"""Visões legíveis da trilha de auditoria: resumo por execução e linha do tempo de decisões."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

EVENT_LABELS = {
    "guardrail.input": "Guardrail de entrada",
    "run.start": "Início da execução",
    "run.end": "Fim da execução",
    "llm.call": "Chamada ao LLM",
    "llm.unavailable": "LLM indisponível",
    "llm.error": "Erro do LLM",
    "tool.obter_metricas": "Tool: métricas",
    "tool.gerar_graficos": "Tool: gráficos",
    "tool.buscar_noticias": "Tool: notícias",
    "tool.consultar_sql": "Tool: SQL",
    "decision.required_tool_forced": "Decisão: tool forçada pelo código",
    "decision.tool_round_limit": "Decisão: limite de rodadas de tools",
    "decision.fallback_explanation": "Decisão: texto de reserva (sem IA)",
    "guardrail.output": "Guardrail de saída",
}


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    kind: str
    started_at: str
    status: str
    model: str
    reference_date: str
    generated_by_llm: bool | None
    llm_calls: int
    total_tokens: int
    duration_s: float | None


@dataclass(frozen=True)
class TimelineRow:
    time: str
    event: str
    status: str
    detail: str


def summarize_runs(events: list[dict]) -> list[RunSummary]:
    """Uma linha por execução, da mais recente para a mais antiga."""
    by_run: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_run[event["run_id"]].append(event)
    summaries = [_summarize(run_id, run_events) for run_id, run_events in by_run.items()]
    return sorted(summaries, key=lambda summary: summary.started_at, reverse=True)


def timeline(events: list[dict]) -> list[TimelineRow]:
    return [
        TimelineRow(
            time=_clock(event["ts"]),
            event=EVENT_LABELS.get(event["event"], event["event"]),
            status=event["status"],
            detail=describe(event),
        )
        for event in events
    ]


def describe(event: dict) -> str:
    """Resumo de uma linha do que o evento registrou."""
    name, data = event["event"], event["data"]
    if name == "llm.call":
        tools = ", ".join(call["name"] for call in data.get("tool_calls") or [])
        what = f"pediu {tools}" if tools else _preview(data.get("response", ""))
        return f"{data['node']} #{data['attempt']} ({data['duration_ms']} ms): {what}"
    if name in ("llm.unavailable", "llm.error"):
        return f"{data.get('node')}: HTTP {data.get('http_status')} · {data.get('message', '')}"
    if name == "tool.consultar_sql":
        result = data.get("result")
        query = _preview(data.get("query") or (result or {}).get("query", ""), 90)
        if result is None:
            return f"{query} → {data.get('error') or data.get('reason')}"
        suppressed = result["suppressed_groups"]
        return f"{query} → {len(result['rows'])} linhas" + (
            f", {suppressed} grupos omitidos" if suppressed else ""
        )
    if name == "tool.buscar_noticias":
        result = data["result"]
        failed = ", ".join(failure["feed"] for failure in result["failed_feeds"])
        return (
            f"{len(result['items'])} notícias; descartadas: {result['dropped_suspicious']} suspeitas, "
            f"{result['dropped_irrelevant']} irrelevantes, {result['dropped_old']} antigas"
            + (f"; falharam: {failed}" if failed else "")
        )
    if name == "tool.gerar_graficos":
        result = data["result"]
        return (
            f"{len(result['casos_diarios'])} dias e {len(result['casos_mensais'])} meses; "
            f"incompletos a partir de {result['primeiro_dia_incompleto']}"
        )
    if name == "tool.obter_metricas":
        return f"aumento de casos (28 dias): {data['result']['case_growth']['value_pct']}%"
    if name == "guardrail.output":
        return "aprovado" if not data.get("violations") else "; ".join(data["violations"])
    if name == "guardrail.input":
        return "; ".join(data.get("blocked_reasons", []) + data.get("warnings", [])) or "aprovado"
    if name == "run.start":
        return f"{data.get('provider')}/{data.get('model')} · dados até {data.get('reference_date')}"
    if name == "run.end":
        return data.get("report_path") or f"{data.get('error')}: {data.get('message', '')}"
    if name.startswith("decision."):
        return data.get("reason") or data.get("tool") or ""
    if name.startswith("step."):
        return f"{data.get('duration_ms')} ms" + (f" · erro: {data['error']}" if "error" in data else "")
    return ""


def _summarize(run_id: str, events: list[dict]) -> RunSummary:
    first = {event["event"]: event for event in reversed(events)}
    start = first.get("run.start", {}).get("data", {})
    end = first.get("run.end")
    llm_calls = [event for event in events if event["event"] == "llm.call"]
    is_report = "run.start" in first or "guardrail.input" in first
    return RunSummary(
        run_id=run_id,
        kind="relatório" if is_report else "atualização de dados",
        started_at=events[0]["ts"],
        status=end["status"] if end else _worst_status(events),
        model=f"{start['provider']}/{start['model']}" if start else "",
        reference_date=start.get("reference_date", ""),
        generated_by_llm=end["data"].get("generated_by_llm") if end else None,
        llm_calls=len(llm_calls),
        total_tokens=sum(_tokens(event) for event in llm_calls),
        duration_s=_seconds_between(events[0]["ts"], events[-1]["ts"]),
    )


def _tokens(event: dict) -> int:
    # Execuções antigas têm a contagem redigida ("[REDACTED]"): o log é só de acréscimo, então tolera.
    total = (event["data"].get("usage") or {}).get("total_tokens")
    return total if isinstance(total, int) else 0


def _worst_status(events: list[dict]) -> str:
    order = ["ok", "degraded", "blocked", "error"]
    return max((event["status"] for event in events), key=order.index)


def _seconds_between(start: str, end: str) -> float:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)


def _clock(timestamp: str) -> str:
    return f"{datetime.fromisoformat(timestamp):%d/%m %H:%M:%S}"


def _preview(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
