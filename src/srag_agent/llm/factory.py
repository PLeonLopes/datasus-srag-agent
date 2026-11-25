"""Fábrica de chat models: o provedor é escolhido por configuração.

OpenRouter e Hugging Face expõem APIs compatíveis com a da OpenAI e usam `ChatOpenAI`.
"""

from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from srag_agent.config import LLMProvider, Settings

# Provedores com catálogo instável (OpenAI, OpenRouter, Hugging Face) exigem SRAG_LLM_MODEL.
DEFAULT_MODELS: dict[LLMProvider, str] = {
    LLMProvider.OLLAMA: "qwen3:8b",
    LLMProvider.ANTHROPIC: "claude-opus-5-5",
}


@dataclass(frozen=True)
class _Credential:
    settings_field: str
    env_var: str


CREDENTIALS: dict[LLMProvider, _Credential] = {
    LLMProvider.ANTHROPIC: _Credential("anthropic_api_key", "ANTHROPIC_API_KEY"),
    LLMProvider.OPENAI: _Credential("openai_api_key", "OPENAI_API_KEY"),
    LLMProvider.OPENROUTER: _Credential("openrouter_api_key", "OPENROUTER_API_KEY"),
    LLMProvider.HUGGINGFACE: _Credential("huggingface_api_key", "HF_TOKEN"),
}

OPENAI_COMPATIBLE_BASE_URLS: dict[LLMProvider, str | None] = {
    LLMProvider.OPENAI: None,
    LLMProvider.OPENROUTER: "https://openrouter.ai/api/v1",
    LLMProvider.HUGGINGFACE: "https://router.huggingface.co/v1",
}


class LLMConfigurationError(ValueError):
    """Configuração de LLM inválida ou incompleta."""


def resolve_model_name(settings: Settings) -> str:
    if settings.llm_model:
        return settings.llm_model
    default = DEFAULT_MODELS.get(settings.llm_provider)
    if default is None:
        raise LLMConfigurationError(
            f"Defina SRAG_LLM_MODEL: o provedor '{settings.llm_provider}' não tem modelo padrão."
        )
    return default


def create_chat_model(settings: Settings) -> BaseChatModel:
    model = resolve_model_name(settings)
    provider = settings.llm_provider

    if provider is LLMProvider.OLLAMA:
        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=settings.llm_temperature,
            num_predict=settings.llm_max_tokens,
            client_kwargs={"timeout": settings.llm_timeout_seconds},
        )

    api_key = _require_api_key(settings)

    if provider is LLMProvider.ANTHROPIC:
        # Sem `temperature`: os modelos Claude atuais respondem 400 a parâmetros de amostragem.
        return ChatAnthropic(
            model=model,
            api_key=api_key,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=OPENAI_COMPATIBLE_BASE_URLS[provider],
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _require_api_key(settings: Settings) -> SecretStr:
    credential = CREDENTIALS[settings.llm_provider]
    api_key: SecretStr | None = getattr(settings, credential.settings_field)
    if api_key is None or not api_key.get_secret_value().strip():
        raise LLMConfigurationError(
            f"Defina a variável de ambiente {credential.env_var} para usar o provedor "
            f"'{settings.llm_provider}'."
        )
    return api_key
