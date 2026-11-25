import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from srag_agent.governance.audit import (
    GENESIS_HASH,
    REDACTED,
    AuditLog,
    read_events,
    sanitize,
    verify_chain,
)


@pytest.fixture
def log_path(tmp_path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


def _rewrite_lines(path: Path, transform) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(transform(lines)) + "\n", encoding="utf-8")


class TestRecord:
    def test_events_are_chained_and_numbered(self, log_path):
        audit = AuditLog(log_path, run_id="run-1")

        first = audit.record("run.start", provider="ollama")
        second = audit.record("step.metrics", rows=10)

        assert (first["seq"], second["seq"]) == (1, 2)
        assert first["prev_hash"] == GENESIS_HASH
        assert second["prev_hash"] == first["hash"]
        assert read_events(log_path) == [first, second]

    def test_chain_continues_across_runs(self, log_path):
        AuditLog(log_path, run_id="run-1").record("run.start")
        second_run = AuditLog(log_path, run_id="run-2").record("run.start")

        assert second_run["seq"] == 2
        assert [event["run_id"] for event in read_events(log_path, run_id="run-2")] == ["run-2"]
        assert verify_chain(log_path).valid

    def test_rejects_unknown_status(self, log_path):
        with pytest.raises(ValueError, match="Status inválido"):
            AuditLog(log_path).record("x", status="talvez")

    def test_step_records_duration_and_extra_data(self, log_path):
        audit = AuditLog(log_path)

        with audit.step("charts", files=2) as extra:
            extra["status"] = "degraded"
            extra["reason"] = "sem dados"

        event = read_events(log_path)[-1]
        assert event["event"] == "step.charts"
        assert event["status"] == "degraded"
        assert event["data"]["files"] == 2
        assert event["data"]["reason"] == "sem dados"
        assert isinstance(event["data"]["duration_ms"], int)

    def test_step_records_error_and_reraises(self, log_path):
        audit = AuditLog(log_path)

        with pytest.raises(RuntimeError), audit.step("news"):
            raise RuntimeError("falhou")

        event = read_events(log_path)[-1]
        assert (event["status"], event["data"]["error"]) == ("error", "RuntimeError")


class TestSanitize:
    def test_redacts_secrets_by_type_and_key_name(self):
        data = {
            "api_key": "sk-123",
            "HF_TOKEN": "hf-abc",
            "settings": {"password": "x", "model": "qwen3:8b"},
            "secret_value": SecretStr("oculto"),
            "provider": "anthropic",
        }

        assert sanitize(data) == {
            "api_key": REDACTED,
            "HF_TOKEN": REDACTED,
            "settings": {"password": REDACTED, "model": "qwen3:8b"},
            "secret_value": REDACTED,
            "provider": "anthropic",
        }

    def test_converts_dataclasses_dates_and_paths(self):
        @dataclass
        class Item:
            day: date
            path: Path

        assert sanitize(Item(date(2026, 11, 23), Path("data/srag.db"))) == {
            "day": "2026-11-23",
            "path": "data/srag.db",
        }

    def test_secret_passed_to_record_never_reaches_the_file(self, log_path):
        AuditLog(log_path).record("llm.config", api_key="sk-nao-pode-vazar")

        assert "sk-nao-pode-vazar" not in log_path.read_text(encoding="utf-8")


class TestVerifyChain:
    @pytest.fixture
    def filled_log(self, log_path) -> Path:
        audit = AuditLog(log_path)
        for index in range(4):
            audit.record("event", index=index)
        return log_path

    def test_untouched_log_is_valid(self, filled_log):
        report = verify_chain(filled_log)

        assert (report.valid, report.events, report.problems) == (True, 4, [])

    def test_detects_edited_line(self, filled_log):
        def edit(lines):
            entry = json.loads(lines[1])
            entry["data"]["index"] = 99
            lines[1] = json.dumps(entry)
            return lines

        _rewrite_lines(filled_log, edit)

        report = verify_chain(filled_log)
        assert not report.valid
        assert any("linha 2: conteúdo alterado" in problem for problem in report.problems)

    def test_detects_deleted_line(self, filled_log):
        _rewrite_lines(filled_log, lambda lines: lines[:1] + lines[2:])

        report = verify_chain(filled_log)
        assert not report.valid
        assert any("prev_hash" in problem for problem in report.problems)

    def test_detects_truncated_end_through_head_file(self, filled_log):
        _rewrite_lines(filled_log, lambda lines: lines[:-1])

        report = verify_chain(filled_log)
        assert not report.valid
        assert any(".head" in problem for problem in report.problems)

    def test_missing_log_is_valid_and_empty(self, tmp_path):
        assert verify_chain(tmp_path / "nao-existe.jsonl").events == 0
