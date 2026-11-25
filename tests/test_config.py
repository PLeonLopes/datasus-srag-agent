from pathlib import Path

import pytest
from pydantic import ValidationError

from srag_agent.config import LLMProvider, Settings


def test_defaults_use_local_provider_and_relative_paths(make_settings):
    settings = make_settings()

    assert settings.llm_provider is LLMProvider.OLLAMA
    assert settings.llm_model is None
    assert settings.database_path == Path("data/srag.db")
    assert settings.raw_data_dir == Path("data/raw")
    assert settings.audit_log_path == Path("logs/audit.jsonl")


def test_reads_options_with_srag_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SRAG_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("SRAG_LLM_MODEL", "algum/modelo:free")

    settings = Settings(_env_file=None)

    assert settings.llm_provider is LLMProvider.OPENROUTER
    assert settings.llm_model == "algum/modelo:free"


@pytest.mark.parametrize(
    ("env_var", "field"),
    [
        ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("OPENAI_API_KEY", "openai_api_key"),
        ("OPENROUTER_API_KEY", "openrouter_api_key"),
        ("HF_TOKEN", "huggingface_api_key"),
    ],
)
def test_reads_api_keys_from_standard_env_vars(monkeypatch: pytest.MonkeyPatch, env_var, field):
    monkeypatch.setenv(env_var, "valor-de-teste")

    settings = Settings(_env_file=None)

    assert getattr(settings, field).get_secret_value() == "valor-de-teste"


def test_api_keys_never_appear_in_repr(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "nao-deve-vazar")

    settings = Settings(_env_file=None)

    assert "nao-deve-vazar" not in repr(settings)
    assert "nao-deve-vazar" not in str(settings.model_dump())


def test_rejects_unknown_provider(make_settings):
    with pytest.raises(ValidationError):
        make_settings(llm_provider="provedor-inexistente")


@pytest.mark.parametrize(
    "overrides", [{"llm_temperature": -0.1}, {"llm_max_tokens": 0}, {"llm_max_retries": -1}]
)
def test_rejects_out_of_range_llm_options(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)
