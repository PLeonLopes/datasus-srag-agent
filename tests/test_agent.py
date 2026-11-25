import json
import sqlite3
from contextlib import closing
from datetime import date

import httpx
import pytest
from langchain_core.messages import AIMessage

from fakes import FakeNewsProvider, HttpStatusError, ScriptedChatModel, tool_calls
from srag_agent.agent.graph import MAX_TOOL_ROUNDS, _text
from srag_agent.agent.runner import PreflightError, run_report
from srag_agent.config import Settings
from srag_agent.governance.audit import read_events, verify_chain

TODAY = date(2026, 11, 25)
VALID_ANALYSIS = (
    "Na janela de 28 dias, a evolução dos casos segue o que mostram a tabela e os gráficos. "
    "As notícias destacam casos graves de covid-19 em alguns estados, um recorte que o total nacional "
    "não mostra sozinho. Os dados dos últimos 14 dias ainda estão incompletos."
)
INVALID_ANALYSIS = "Os casos vão aumentar 37,4% e recomendamos procurar um médico."
PRONTO = AIMessage("PRONTO")
ALL_REQUIRED = tool_calls(("obter_metricas", {}), ("gerar_graficos", {}), ("buscar_noticias", {}))


@pytest.fixture
def settings(synthetic_db, tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=synthetic_db.parent,
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
        llm_provider="openrouter",
        llm_model="modelo/teste:free",
        openrouter_api_key="sk-or-segredo-de-teste",
    )


def _run(settings, *responses, news=None):
    llm = ScriptedChatModel(responses=list(responses))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    result = run_report(
        settings, today=TODAY, llm=llm, news_provider=news or FakeNewsProvider(), http_client=client
    )
    return result, llm, read_events(settings.audit_log_path)


def _events(events, name):
    return [event for event in events if event["event"] == name]


class TestHappyPath:
    def test_agent_uses_tools_investigates_and_writes_report(self, settings):
        sql = "SELECT classification, COUNT(*) AS casos FROM cases GROUP BY classification"
        result, llm, events = _run(
            settings,
            ALL_REQUIRED,
            tool_calls(("consultar_sql", {"consulta": sql})),
            PRONTO,
            AIMessage(VALID_ANALYSIS),
        )

        report = result.report_path.read_text(encoding="utf-8")
        assert result.generated_by_llm
        assert len(llm.calls) == 4
        assert "Texto gerado por IA (`openrouter/modelo/teste:free`)" in report
        assert VALID_ANALYSIS in report
        assert "## Investigações do agente (SQL)" in report
        assert (result.report_path.parent / "casos_diarios.png").exists()
        assert [event["status"] for event in _events(events, "guardrail.output")] == ["ok"]
        assert _events(events, "run.end")[0]["status"] == "ok"
        assert verify_chain(settings.audit_log_path).valid

    def test_audit_records_prompts_tools_and_news(self, settings):
        _, _, events = _run(settings, ALL_REQUIRED, PRONTO, AIMessage(VALID_ANALYSIS))

        llm_calls = _events(events, "llm.call")
        assert [call["data"]["node"] for call in llm_calls] == ["orquestrador", "orquestrador", "redator"]
        assert llm_calls[-1]["data"]["response"] == VALID_ANALYSIS
        assert "<noticias_nao_confiaveis>" in llm_calls[-1]["data"]["prompt"][1]["content"]
        news_event = _events(events, "tool.buscar_noticias")[0]
        assert news_event["data"]["result"]["items"][0]["link"] == "https://exemplo.com.br/noticia-12345"
        assert _events(events, "run.start")[0]["data"]["sources"][0]["sha256"]

    def test_api_key_never_reaches_audit_or_report(self, settings):
        result, _, _ = _run(settings, ALL_REQUIRED, PRONTO, AIMessage(VALID_ANALYSIS))

        assert "sk-or-segredo-de-teste" not in settings.audit_log_path.read_text(encoding="utf-8")
        assert "sk-or-segredo-de-teste" not in result.report_path.read_text(encoding="utf-8")

    def test_each_run_gets_its_own_folder(self, settings):
        first, _, _ = _run(settings, ALL_REQUIRED, PRONTO, AIMessage(VALID_ANALYSIS))
        second, _, _ = _run(settings, ALL_REQUIRED, PRONTO, AIMessage(VALID_ANALYSIS))

        assert first.report_path != second.report_path
        assert first.report_path.exists() and second.report_path.exists()

    def test_structured_data_matches_report(self, settings):
        result, _, _ = _run(settings, ALL_REQUIRED, PRONTO, AIMessage(VALID_ANALYSIS))

        data = json.loads((result.report_path.parent / "relatorio.json").read_text(encoding="utf-8"))
        assert data["analysis"] == {
            "text": VALID_ANALYSIS,
            "generated_by_llm": True,
            "attempts": 1,
            "fallback_reason": "",
        }
        assert data["context"]["run_id"] == result.run_id
        assert data["metrics"]["reference_date"] == "2026-11-23"
        assert len(data["charts"]["casos_diarios"]) == 30
        assert "sk-or-segredo-de-teste" not in json.dumps(data)
        assert (result.report_path.parent / "relatorio.pdf").read_bytes().startswith(b"%PDF")


class TestCodeControls:
    def test_required_tools_are_forced_when_model_skips_them(self, settings):
        result, _, events = _run(settings, PRONTO, AIMessage(VALID_ANALYSIS))

        forced = [event["data"]["tool"] for event in _events(events, "decision.required_tool_forced")]
        assert forced == ["obter_metricas", "gerar_graficos", "buscar_noticias"]
        assert result.generated_by_llm

    def test_tool_rounds_are_limited(self, settings):
        sql = "SELECT COUNT(*) FROM cases"
        loop = [tool_calls(("consultar_sql", {"consulta": sql})) for _ in range(MAX_TOOL_ROUNDS + 1)]

        _, llm, events = _run(settings, *loop, AIMessage(VALID_ANALYSIS))

        assert len(_events(events, "decision.tool_round_limit")) == 1
        assert [e["status"] for e in _events(events, "tool.consultar_sql")] == ["ok", "ok", "ok", "blocked"]
        assert len(llm.calls) == MAX_TOOL_ROUNDS + 2

    def test_unknown_tool_is_rejected_and_audited(self, settings):
        _, llm, events = _run(settings, tool_calls(("apagar_banco", {})), PRONTO, AIMessage(VALID_ANALYSIS))

        assert _events(events, "tool.apagar_banco")[0]["status"] == "blocked"
        tool_message = llm.calls[1][-1]
        assert tool_message.status == "error"


class TestGuardrailLoop:
    def test_rejected_text_is_rewritten_with_feedback(self, settings):
        result, llm, events = _run(
            settings, ALL_REQUIRED, PRONTO, AIMessage(INVALID_ANALYSIS), AIMessage(VALID_ANALYSIS)
        )

        assert result.generated_by_llm
        assert [event["status"] for event in _events(events, "guardrail.output")] == ["blocked", "ok"]
        feedback = llm.calls[-1][-1].content
        assert "recusado pela verificação automática" in feedback
        assert "previsão" in feedback
        assert "aprovado pelos guardrails automáticos na tentativa 2" in result.report_path.read_text("utf-8")

    def test_fallback_after_two_rejections(self, settings):
        result, _, events = _run(
            settings, ALL_REQUIRED, PRONTO, AIMessage(INVALID_ANALYSIS), AIMessage(INVALID_ANALYSIS)
        )

        report = result.report_path.read_text(encoding="utf-8")
        assert not result.generated_by_llm
        assert "**sem IA**" in report
        assert INVALID_ANALYSIS not in report
        assert len(_events(events, "decision.fallback_explanation")) == 1
        assert _events(events, "run.end")[0]["status"] == "degraded"


class TestFailures:
    def test_transient_llm_failure_falls_back_without_extra_calls(self, settings):
        result, llm, events = _run(settings, HttpStatusError(429), HttpStatusError(503))

        assert not result.generated_by_llm
        assert len(llm.calls) == 2
        assert len(_events(events, "llm.unavailable")) == 2
        assert "LLM indisponível" in result.report_path.read_text(encoding="utf-8")

    def test_configuration_error_stops_the_run(self, settings):
        with pytest.raises(HttpStatusError):
            _run(settings, HttpStatusError(401))

        events = read_events(settings.audit_log_path)
        assert _events(events, "llm.error")[0]["data"]["http_status"] == 401
        assert _events(events, "run.end")[0]["status"] == "error"

    def test_preflight_blocks_database_without_recent_cases(self, settings, synthetic_db, tmp_path):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        with (
            closing(sqlite3.connect(synthetic_db)) as source,
            closing(sqlite3.connect(old_dir / "srag.db")) as copy,
        ):
            source.backup(copy)
            copy.execute("DELETE FROM cases WHERE symptom_date >= '2026-10-01'")
            copy.commit()
        blocked_settings = settings.model_copy(update={"data_dir": old_dir})

        with pytest.raises(PreflightError):
            _run(blocked_settings)

        events = read_events(blocked_settings.audit_log_path)
        assert _events(events, "guardrail.input")[0]["status"] == "blocked"
        assert _events(events, "run.end")[0]["status"] == "blocked"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("<think>raciocínio</think>\nTexto final", "Texto final"),
        ([{"type": "thinking", "thinking": "x"}, {"type": "text", "text": "Texto"}], "Texto"),
        ("  simples  ", "simples"),
    ],
)
def test_text_drops_reasoning_blocks(content, expected):
    assert _text(content) == expected
