"""Execução completa de um relatório: guardrail de entrada, grafo, gravação e auditoria.

Uso: `uv run python -m srag_agent.agent [--provider openrouter --model google/gemma-4-31b-it:free]`
"""

import argparse
import hashlib
import logging
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from srag_agent.agent.graph import ReportState, build_graph
from srag_agent.agent.prompts import ORCHESTRATOR_SYSTEM, ORCHESTRATOR_TASK
from srag_agent.agent.report import REPORT_FILE, ReportContext
from srag_agent.agent.toolkit import RunToolkit
from srag_agent.config import LLMProvider, Settings, get_settings
from srag_agent.data.database import connect_readonly, read_load_metadata
from srag_agent.governance.audit import AuditLog
from srag_agent.governance.guardrails import check_database
from srag_agent.llm import create_chat_model, resolve_model_name
from srag_agent.tools.metrics import MATURATION_DAYS
from srag_agent.tools.news import NewsProvider, RssNewsProvider

logger = logging.getLogger(__name__)

USER_AGENT = "datasus-srag-agent/0.1"
RECURSION_LIMIT = 40


class PreflightError(RuntimeError):
    """O guardrail de entrada bloqueou a execução."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    report_path: Path
    generated_by_llm: bool
    warnings: list[str]


def run_report(
    settings: Settings | None = None,
    *,
    today: date | None = None,
    llm: BaseChatModel | None = None,
    news_provider: NewsProvider | None = None,
    http_client: httpx.Client | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunResult:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    today = today or now.date()
    audit = AuditLog(settings.audit_log_path)

    try:
        with closing(connect_readonly(settings.database_path)) as connection:
            check = check_database(connection, today)
            audit.record(
                "guardrail.input",
                "ok" if check.passed else "blocked",
                blocked_reasons=check.blocked_reasons,
                warnings=check.warnings,
            )
            if not check.passed:
                raise PreflightError("; ".join(check.blocked_reasons))

            llm = llm or create_chat_model(settings)
            model = resolve_model_name(settings)
            sources = read_load_metadata(connection)["sources"]
            audit.record(
                "run.start",
                provider=settings.llm_provider,
                model=model,
                reference_date=check.reference_date,
                synthetic=check.synthetic,
                sources=sources,
            )

            out_dir = settings.reports_dir / f"{check.reference_date:%Y-%m-%d}_{audit.run_id}"
            context = ReportContext(
                audit.run_id, now, str(settings.llm_provider), model, sources, check.warnings
            )
            with http_client or _default_http_client() as client:
                toolkit = RunToolkit(
                    connection, out_dir, news_provider or RssNewsProvider(), client, audit, now
                )
                graph = build_graph(llm, toolkit, audit, context)
                final_state = _run_graph(graph, _initial_state(check.reference_date), progress)
    except Exception as error:
        audit.record(
            "run.end",
            "blocked" if isinstance(error, PreflightError) else "error",
            error=type(error).__name__,
            message=str(error)[:500],
        )
        raise

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / REPORT_FILE
    report_path.write_text(final_state["report_markdown"], encoding="utf-8")
    analysis = final_state["analysis"]
    audit.record(
        "run.end",
        "ok" if analysis.generated_by_llm else "degraded",
        report_path=report_path,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        generated_by_llm=analysis.generated_by_llm,
    )
    return RunResult(audit.run_id, report_path, analysis.generated_by_llm, check.warnings)


def _initial_state(reference: date) -> ReportState:
    data_end = date.fromordinal(reference.toordinal() - MATURATION_DAYS)
    task = ORCHESTRATOR_TASK.format(reference_date=reference.isoformat(), data_end=data_end.isoformat())
    return {"messages": [SystemMessage(ORCHESTRATOR_SYSTEM), HumanMessage(task)]}


def _run_graph(graph, initial: ReportState, progress: Callable[[str], None] | None) -> ReportState:
    final_state: ReportState = initial
    for mode, chunk in graph.stream(
        initial, config={"recursion_limit": RECURSION_LIMIT}, stream_mode=["updates", "values"]
    ):
        if mode == "updates" and progress:
            for node in chunk:
                progress(node)
        elif mode == "values":
            final_state = chunk
    return final_state


def _default_http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15.0)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gera o relatório de SRAG com o agente.")
    parser.add_argument("--provider", choices=[provider.value for provider in LLMProvider])
    parser.add_argument("--model")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    overrides = {
        key: value for key, value in {"llm_provider": args.provider, "llm_model": args.model}.items() if value
    }
    settings = Settings(**overrides) if overrides else get_settings()

    result = run_report(settings, progress=lambda node: logger.info("etapa concluída: %s", node))
    for warning in result.warnings:
        logger.warning(warning)
    origin = "IA" if result.generated_by_llm else "texto de reserva (sem IA)"
    logger.info("Relatório (%s): %s · execução %s", origin, result.report_path, result.run_id)


if __name__ == "__main__":
    main()
