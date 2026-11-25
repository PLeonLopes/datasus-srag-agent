"""Catálogo de modelos por provedor, para escolher numa lista em vez de digitar o nome exato.

OpenRouter: API pública de modelos (sem chave), filtrada pelos que suportam tool calling.
Ollama: modelos instalados no servidor local. Demais: lista fixa de sugestões.
"""

from dataclasses import dataclass

import httpx

from srag_agent.config import LLMProvider

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT_SECONDS = 10.0

# Modelos com execução real registrada neste projeto (ver auditoria).
TESTED_MODELS = frozenset({"nvidia/nemotron-3-ultra-550b-a55b:free"})

STATIC_SUGGESTIONS: dict[LLMProvider, list[str]] = {
    LLMProvider.ANTHROPIC: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    LLMProvider.OLLAMA: ["qwen3:8b", "llama3.1:8b"],
    LLMProvider.OPENAI: [],
    LLMProvider.HUGGINGFACE: [],
}


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    free: bool = False
    tested: bool = False


class CatalogUnavailableError(RuntimeError):
    """A lista do provedor não pôde ser obtida; a interface oferece digitar o nome."""


def list_models(
    provider: LLMProvider, client: httpx.Client, ollama_base_url: str = "http://localhost:11434"
) -> list[ModelOption]:
    if provider is LLMProvider.OPENROUTER:
        return openrouter_models(client)
    if provider is LLMProvider.OLLAMA:
        return ollama_models(client, ollama_base_url)
    return [_option(model_id) for model_id in STATIC_SUGGESTIONS[provider]]


def openrouter_models(client: httpx.Client) -> list[ModelOption]:
    """Modelos com tool calling; testados primeiro, depois gratuitos, depois por nome."""
    payload = _get_json(client, OPENROUTER_MODELS_URL)
    options = []
    for model in payload.get("data", []):
        if "tools" not in (model.get("supported_parameters") or []):
            continue
        model_id = model["id"]
        free = model_id.endswith(":free")
        context = model.get("context_length")
        details = ["grátis" if free else "pago"] + ([f"{_tokens(context)} de contexto"] if context else [])
        options.append(_option(model_id, free=free, details=details))
    return sorted(options, key=lambda option: (not option.tested, not option.free, option.id))


def ollama_models(client: httpx.Client, base_url: str) -> list[ModelOption]:
    payload = _get_json(client, f"{base_url.rstrip('/')}/api/tags")
    return [_option(model["name"], details=["local"]) for model in payload.get("models", [])]


def _option(model_id: str, free: bool = False, details: list[str] | None = None) -> ModelOption:
    tested = model_id in TESTED_MODELS
    parts = (["✓ testado"] if tested else []) + (details or [])
    label = f"{model_id} ({' · '.join(parts)})" if parts else model_id
    return ModelOption(model_id, label, free=free, tested=tested)


def _get_json(client: httpx.Client, url: str) -> dict:
    try:
        response = client.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogUnavailableError(
            f"Não foi possível listar os modelos ({type(error).__name__})"
        ) from error


def _tokens(count: int) -> str:
    return f"{count // 1_000_000}M" if count >= 1_000_000 else f"{count // 1000}k"
