import copy
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest

from fakes import FakeNewsProvider
from srag_agent.agent.pdf import render_pdf
from srag_agent.agent.report import Analysis, ReportContext, report_data
from srag_agent.agent.toolkit import RunToolkit
from srag_agent.data.database import connect_readonly
from srag_agent.governance.audit import AuditLog

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def run_folder_and_data(synthetic_db, tmp_path_factory):
    folder = tmp_path_factory.mktemp("execucao")
    with closing(connect_readonly(synthetic_db)) as connection, httpx.Client() as client:
        toolkit = RunToolkit(
            connection, folder, FakeNewsProvider(), client, AuditLog(folder / "a.jsonl"), NOW
        )
        for name in ("obter_metricas", "gerar_graficos", "buscar_noticias"):
            toolkit.run(name, {})
        toolkit.run(
            "consultar_sql", {"consulta": "SELECT state, COUNT(*) AS casos FROM cases GROUP BY state"}
        )
        context = ReportContext(
            "run123",
            NOW,
            "openrouter",
            "modelo:free",
            [{"filename": "f.parquet", "sha256": "a" * 64}],
            ["Aviso."],
        )
        data = report_data(context, toolkit.evidence, Analysis("Análise com acentuação: ção, é, ü.", True, 1))
    return folder, data


def test_renders_pdf_with_charts_sql_and_news(run_folder_and_data):
    folder, data = run_folder_and_data

    pdf = render_pdf(data, folder)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 50_000  # inclui os dois gráficos


def test_renders_minimal_report_without_optional_sections(run_folder_and_data, tmp_path):
    _, data = run_folder_and_data
    minimal = copy.deepcopy(data)
    minimal["sql"], minimal["news"] = [], None
    minimal["analysis"] = {
        "text": "Reserva.",
        "generated_by_llm": False,
        "attempts": 0,
        "fallback_reason": "LLM indisponível",
    }

    pdf = render_pdf(minimal, tmp_path)  # pasta sem os PNGs dos gráficos

    assert pdf.startswith(b"%PDF")


def test_long_sql_results_are_truncated(run_folder_and_data, tmp_path):
    _, data = run_folder_and_data
    big = copy.deepcopy(data)
    big["sql"] = [
        {
            "query": "SELECT 1",
            "columns": ["n"],
            "rows": [[index] for index in range(200)],
            "suppressed_groups": 2,
        }
    ]

    assert render_pdf(big, tmp_path).startswith(b"%PDF")
