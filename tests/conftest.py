import os
from datetime import date
from pathlib import Path

import pytest

from srag_agent.config import Settings
from srag_agent.data.etl import build_database
from srag_agent.data.synthetic import generate_snapshots

SNAPSHOT_DATE = date(2026, 11, 23)

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


@pytest.fixture(scope="session")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Banco montado pelo ETL com dados sintéticos (snapshot de 23/11/2026)."""
    base = tmp_path_factory.mktemp("synthetic")
    generate_snapshots(base / "raw", SNAPSHOT_DATE, n_cases=5_000)
    db_path = base / "srag.db"
    build_database(base / "raw", db_path)
    return db_path
