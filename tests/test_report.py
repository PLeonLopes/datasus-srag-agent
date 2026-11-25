from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest

from fakes import FakeNewsProvider
from srag_agent.agent.report import Analysis, ReportContext, fallback_explanation, render_report
from srag_agent.agent.toolkit import RunToolkit
from srag_agent.data.database import connect_readonly
from srag_agent.governance.audit import AuditLog
from srag_agent.governance.guardrails import check_explanation

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


@pytest.fixture
def toolkit(synthetic_db, tmp_path):
    with closing(connect_readonly(synthetic_db)) as connection, httpx.Client() as client:
        toolkit = RunToolkit(
            connection, tmp_path / "out", FakeNewsProvider(), client, AuditLog(tmp_path / "audit.jsonl"), NOW
        )
        for name in ("obter_metricas", "gerar_graficos", "buscar_noticias"):
            toolkit.run(name, {})
        yield toolkit


def _context(warnings=()) -> ReportContext:
    sources = [{"filename": "INFLUD26-23-11-2026.parquet", "sha256": "a" * 64}]
    return ReportContext("run123", NOW, "openrouter", "modelo:free", sources, list(warnings))


def test_fallback_text_passes_the_same_guardrails(toolkit):
    metrics = toolkit.evidence.metrics
    text = fallback_explanation(metrics)

    result = check_explanation(
        text,
        toolkit.evidence.for_guardrail(),
        [metrics.case_growth.value_pct, metrics.case_growth_short.value_pct],
    )

    assert result.passed, result.violations


def test_report_contains_all_sections_and_provenance(toolkit):
    report = render_report(
        _context(), toolkit.evidence, Analysis("Análise.", generated_by_llm=True, attempts=1)
    )

    for heading in (
        "# Relatório de SRAG: dados até 23/11/2026",
        "## Métricas",
        "## Casos diários (últimos 30 dias)",
        "## Casos mensais (últimos 12 meses)",
        "## Análise do cenário",
        "## Notícias consultadas",
        "## Metodologia e limitações",
        "## Rastreabilidade",
    ):
        assert heading in report
    assert "![Casos diários](casos_diarios.png)" in report
    assert "**não** a ocupação de leitos" in report
    assert "`INFLUD26-23-11-2026.parquet` (sha256 `aaaaaaaaaaaaaaaa…`)" in report
    assert "[Fiocruz aponta aumento" in report


def test_report_shows_warnings_and_fallback_origin(toolkit):
    analysis = Analysis("Texto.", generated_by_llm=False, attempts=0, fallback_reason="LLM indisponível")

    report = render_report(
        _context(["Dados sintéticos: o relatório não descreve a situação real."]), toolkit.evidence, analysis
    )

    assert "> **Atenção:** Dados sintéticos" in report
    assert "Texto gerado por regras, **sem IA** (LLM indisponível)." in report


class TestToolkit:
    def test_repeated_calls_reuse_results(self, toolkit):
        provider = toolkit.news_provider

        toolkit.run("buscar_noticias", {})
        toolkit.run("obter_metricas", {})

        assert provider.calls == 1
        assert toolkit.missing_required() == []

    def test_guardrail_evidence_excludes_news_links(self, toolkit):
        news = toolkit.evidence.for_guardrail()["news"]

        assert news[0]["title"].startswith("Fiocruz")
        assert "link" not in news[0]

    def test_sql_error_goes_back_to_model_as_message(self, toolkit):
        content, ok = toolkit.run("consultar_sql", {"consulta": "SELECT * FROM cases"})

        assert not ok
        assert content.startswith("ERRO: Só consultas agregadas")
        assert toolkit.evidence.sql == []

    def test_tool_definitions_describe_the_schema(self, toolkit):
        tools = {tool.name: tool for tool in toolkit.tools()}

        assert set(tools) == {"obter_metricas", "gerar_graficos", "buscar_noticias", "consultar_sql"}
        assert "srag_death" in tools["consultar_sql"].description
        assert list(tools["consultar_sql"].args) == ["consulta"]
