"""Configuração da aplicação, lida de variáveis de ambiente (e de um `.env` local, se existir)."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    HUGGINGFACE = "huggingface"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SRAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # aceita Settings(anthropic_api_key=...) além do alias
    )

    # LLM
    llm_provider: LLMProvider = LLMProvider.OLLAMA
    llm_model: str | None = None  # vazio -> padrão do provedor
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=16_000, gt=0)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    ollama_base_url: str = "http://localhost:11434"

    # Chaves de API (nomes padrão de cada provedor, sem prefixo)
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    huggingface_api_key: SecretStr | None = Field(default=None, validation_alias="HF_TOKEN")

    # Artefatos locais
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    logs_dir: Path = Path("logs")

    @property
    def raw_data_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "srag.db"

    @property
    def audit_log_path(self) -> Path:
        return self.logs_dir / "audit.jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
