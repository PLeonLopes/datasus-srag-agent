import os
import time
from pathlib import Path

import pytest

from fakes import HttpStatusError
from srag_agent.governance.audit import AuditLog, read_events
from srag_agent.ui.audit_view import describe, summarize_runs, timeline
from srag_agent.ui.reports import chart_frame, explain_error, list_reports, metric_cards, split_report

REPORT = """# Relatório

Texto inicial.

![Casos diários](casos_diarios.png)

<details><summary>Tabela</summary>

| Dia | Casos |
|---|---|
| 01/11/2026 | 3 |

</details>

![fora da pasta](../../segredo.png)

## Análise
Fim."""


class TestSplitReport:
    def test_turns_images_and_details_into_segments(self):
        segments = split_report(REPORT)

        assert [segment.kind for segment in segments] == ["markdown", "image", "expander", "markdown"]
        assert (segments[1].content, segments[1].title) == ("casos_diarios.png", "Casos diários")
        assert segments[2].title == "Tabela"
        assert "| 01/11/2026 | 3 |" in segments[2].content

    def test_image_outside_report_folder_stays_as_text(self):
        last = split_report(REPORT)[-1]

        assert "../../segredo.png" in last.content


def test_list_reports_newest_first_and_ignores_other_folders(tmp_path):
    for name in ("2026-11-23_aaa111", "2026-11-23_bbb222", "outra-pasta"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "relatorio.md").write_text("# r", encoding="utf-8")
    old = tmp_path / "2026-11-23_aaa111" / "relatorio.md"
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (tmp_path / "2026-11-24_ccc333").mkdir()  # sem relatorio.md

    reports = list_reports(tmp_path)

    assert [report.run_id for report in reports] == ["bbb222", "aaa111"]
    assert "dados até 23/11/2026" in reports[0].label


def test_list_reports_on_missing_folder(tmp_path):
    assert list_reports(tmp_path / "nao-existe") == []


def _sample_log(path: Path) -> list[dict]:
    report_run = AuditLog(path, run_id="run-relatorio")
    report_run.record("guardrail.input", blocked_reasons=[], warnings=[])
    report_run.record("run.start", provider="openrouter", model="m:free", reference_date="2026-11-23")
    report_run.record(
        "llm.call",
        node="orquestrador",
        attempt=1,
        duration_ms=100,
        prompt=[],
        response="",
        tool_calls=[{"name": "obter_metricas", "args": {}}],
        usage={"total_tokens": 120},
    )
    report_run.record(
        "llm.call",
        node="redator",
        attempt=1,
        duration_ms=200,
        prompt=[],
        response="Texto da análise",
        tool_calls=[],
        usage={"total_tokens": "[REDACTED]"},  # formato de execuções antigas
    )
    report_run.record("guardrail.output", "blocked", attempt=1, violations=["contém previsão"])
    report_run.record("run.end", "degraded", report_path="reports/x/relatorio.md", generated_by_llm=False)
    data_run = AuditLog(path, run_id="run-dados")
    data_run.record("step.data.download", duration_ms=10)
    data_run.record("step.data.etl", "error", duration_ms=5, error="EtlError")
    return read_events(path)


def test_summarize_runs(tmp_path):
    runs = {run.run_id: run for run in summarize_runs(_sample_log(tmp_path / "audit.jsonl"))}

    report = runs["run-relatorio"]
    assert (report.kind, report.status, report.model) == ("relatório", "degraded", "openrouter/m:free")
    assert (report.llm_calls, report.total_tokens, report.generated_by_llm) == (2, 120, False)
    data = runs["run-dados"]
    assert (data.kind, data.status, data.llm_calls) == ("atualização de dados", "error", 0)


def test_timeline_describes_decisions(tmp_path):
    events = [event for event in _sample_log(tmp_path / "audit.jsonl") if event["run_id"] == "run-relatorio"]

    rows = timeline(events)

    details = {row.event: row.detail for row in rows}
    assert details["Chamada ao LLM"] == "redator #1 (200 ms): Texto da análise"
    assert details["Guardrail de saída"] == "contém previsão"
    assert details["Início da execução"] == "openrouter/m:free · dados até 2026-11-23"
    assert rows[2].detail == "orquestrador #1 (100 ms): pediu obter_metricas"


def test_describe_sql_results_and_errors():
    ok = {
        "event": "tool.consultar_sql",
        "data": {"result": {"query": "SELECT state, COUNT(*)", "rows": [[1], [2]], "suppressed_groups": 3}},
    }
    rejected = {
        "event": "tool.consultar_sql",
        "data": {"query": "SELECT *", "error": "Só consultas agregadas"},
    }

    assert describe(ok) == "SELECT state, COUNT(*) → 2 linhas, 3 grupos omitidos"
    assert describe(rejected) == "SELECT * → Só consultas agregadas"


PANEL_METRICS = {
    "case_growth": {"value_pct": -9.0, "current_cases": 14173, "previous_cases": 15567},
    "case_growth_short": {"value_pct": None, "current_cases": 3, "previous_cases": 0},
    "mortality": {"value_pct": 5.0},
    "pending_outcome_pct": 21.6,
    "icu_admission": {"value_pct": 29.1, "numerator": 13058, "denominator": 44808},
    "flu_vaccination": {"value_pct": 38.8, "denominator": 48372},
    "covid_vaccination": {"value_pct": 40.0, "denominator": 50380},
}


class TestPanels:
    def test_metric_cards_format_values_and_explain_limits(self):
        cards = metric_cards(PANEL_METRICS)

        assert [(card.label, card.value) for card in cards][:2] == [
            ("Aumento de casos (28 dias)", "-9,0%"),
            ("Aumento de casos (7 dias)", "sem dados"),
        ]
        assert cards[0].detail == "14.173 contra 15.567"
        assert cards[2].detail == "21,6% ainda sem desfecho"
        assert "Não é a ocupação de leitos" in cards[3].help

    def test_chart_frame_marks_incomplete_periods(self):
        frame = chart_frame({"2026-11-08": 5, "2026-11-10": 2}, "2026-11-10", monthly=False)

        assert frame[["Período", "Casos", "Situação"]].to_dict("records") == [
            {"Período": "08/11/2026", "Casos": 5, "Situação": "Consolidado"},
            {"Período": "10/11/2026", "Casos": 2, "Situação": "Incompleto"},
        ]
        assert chart_frame({"2026-11": 7}, "2026-11", monthly=True)["Período"].tolist() == ["11/2026"]

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(401, "chave do provedor foi recusada"), (404, "modelo não existe"), (429, "cota do provedor")],
    )
    def test_explain_error_in_portuguese(self, status, expected):
        assert expected in explain_error(HttpStatusError(status))

    def test_saved_report_reads_data_file_when_present(self, tmp_path):
        folder = tmp_path / "2026-11-23_abc123"
        folder.mkdir()
        (folder / "relatorio.md").write_text("# r", encoding="utf-8")
        report = list_reports(tmp_path)[0]

        assert report.data() is None
        (folder / "relatorio.json").write_text('{"analysis": {"text": "x"}}', encoding="utf-8")
        assert report.data() == {"analysis": {"text": "x"}}
