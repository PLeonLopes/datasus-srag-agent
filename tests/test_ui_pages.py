"""Páginas do Streamlit com AppTest: sem navegador, sem rede e sem LLM real."""

from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

from fakes import FakeNewsProvider, ScriptedChatModel, tool_calls
from srag_agent.agent import runner
from srag_agent.config import get_settings

ANALYSIS = "Na janela de 28 dias, a evolução dos casos segue a tabela. Os dados de 14 dias estão incompletos."


@pytest.fixture
def app_env(synthetic_db, tmp_path, monkeypatch):
    """Aponta a configuração para o banco sintético e pastas temporárias."""
    monkeypatch.setenv("SRAG_DATA_DIR", str(synthetic_db.parent))
    monkeypatch.setenv("SRAG_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SRAG_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SRAG_LLM_PROVIDER", "ollama")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def scripted_run(monkeypatch):
    """Troca o `run_report` da página por um que usa o LLM roteirizado e notícias falsas."""
    real_run_report = runner.run_report

    def fake_run_report(settings, progress=None):
        llm = ScriptedChatModel(
            responses=[
                tool_calls(("obter_metricas", {}), ("gerar_graficos", {}), ("buscar_noticias", {})),
                AIMessage("PRONTO"),
                AIMessage(ANALYSIS),
            ]
        )
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        return real_run_report(
            settings, llm=llm, news_provider=FakeNewsProvider(), http_client=client, progress=progress
        )

    monkeypatch.setattr(runner, "run_report", fake_run_report)


APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _app(page: str) -> AppTest:
    return AppTest.from_file(APP_DIR / "views" / page, default_timeout=120)


def _click(app: AppTest, label: str) -> AppTest:
    return next(button for button in app.button if button.label == label).click().run()


def test_report_page_shows_data_status_without_reports(app_env):
    app = _app("relatorio.py").run()

    assert not app.exception
    assert any("Dados até **23/11/2026**" in markdown.value for markdown in app.sidebar.markdown)
    assert any("sintéticos" in warning.value for warning in app.sidebar.warning)
    assert app.info[0].value == "Nenhum relatório gerado ainda. Use o botão acima."


def test_generate_button_runs_agent_and_shows_panels(app_env, scripted_run):
    app = _click(_app("relatorio.py").run(), "Gerar novo relatório")

    assert not app.exception
    assert [tab.label for tab in app.tabs][:4] == [
        "📈 Gráficos",
        "🧠 Análise",
        "🔎 Investigações SQL (0)",
        "📰 Notícias (1)",
    ]
    assert [metric.label for metric in app.metric][:2] == [
        "Aumento de casos (28 dias)",
        "Aumento de casos (7 dias)",
    ]
    assert any(ANALYSIS in markdown.value for markdown in app.markdown)
    assert len(list((app_env / "reports").glob("*/relatorio.json"))) == 1


def test_old_report_without_data_file_falls_back_to_markdown(app_env, scripted_run):
    _click(_app("relatorio.py").run(), "Gerar novo relatório")
    next((app_env / "reports").glob("*/relatorio.json")).unlink()

    app = _app("relatorio.py").run()

    assert not app.exception
    assert app.info[0].value.startswith("Relatório gerado antes dos painéis")
    assert any(expander.label == "Tabela" for expander in app.expander)


def test_audit_page_lists_run_after_generation(app_env, scripted_run):
    _click(_app("relatorio.py").run(), "Gerar novo relatório")

    audit_page = _app("auditoria.py").run()

    assert not audit_page.exception
    assert (audit_page.metric[0].label, audit_page.metric[0].value) == ("Integridade", "Íntegra")
    assert audit_page.tabs[1].label == "🤖 Chamadas ao LLM (3)"
    assert any(expander.label.startswith("redator #1") for expander in audit_page.expander)


def test_audit_page_without_events(app_env):
    app = _app("auditoria.py").run()

    assert not app.exception
    assert app.info[0].value == "Nenhuma execução registrada ainda."


def test_app_opens_on_report_page(app_env):
    app = AppTest.from_file(APP_DIR / "streamlit_app.py", default_timeout=120).run()

    assert not app.exception
    assert app.title[0].value == "Relatório de SRAG"


def test_report_offers_pdf_and_markdown_downloads(app_env, scripted_run):
    app = _click(_app("relatorio.py").run(), "Gerar novo relatório")

    assert [button.label for button in app.get("download_button")] == ["Baixar PDF", "Baixar Markdown"]
