import os

import pytest

from srag_agent.config import Settings

_PROVIDER_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes nunca usam configuração nem chaves reais da máquina."""
    for name in list(os.environ):
        if name.startswith("SRAG_") or name in _PROVIDER_ENV_VARS:
            monkeypatch.delenv(name)


@pytest.fixture
def make_settings():
    def _make(**overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    return _make
