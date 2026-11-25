import httpx
import pytest

from srag_agent.config import LLMProvider
from srag_agent.llm.catalog import (
    OPENROUTER_MODELS_URL,
    CatalogUnavailableError,
    list_models,
    openrouter_models,
)

OPENROUTER_PAYLOAD = {
    "data": [
        {"id": "z-lab/pago", "supported_parameters": ["tools"], "context_length": 128_000},
        {
            "id": "a-lab/gratis:free",
            "supported_parameters": ["tools", "temperature"],
            "context_length": 32_000,
        },
        {
            "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "supported_parameters": ["tools"],
            "context_length": 1_000_000,
        },
        {"id": "b-lab/sem-tools:free", "supported_parameters": ["temperature"]},
    ]
}


def _client(routes: dict[str, httpx.Response], requested: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(request)
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openrouter_lists_only_tool_models_tested_and_free_first():
    client = _client({OPENROUTER_MODELS_URL: httpx.Response(200, json=OPENROUTER_PAYLOAD)})

    options = openrouter_models(client)

    assert [option.id for option in options] == [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "a-lab/gratis:free",
        "z-lab/pago",
    ]
    assert options[0].label == "nvidia/nemotron-3-ultra-550b-a55b:free (✓ testado · grátis · 1M de contexto)"
    assert options[2].label == "z-lab/pago (pago · 128k de contexto)"
    assert [option.free for option in options] == [True, True, False]


def test_openrouter_catalog_is_requested_without_api_key():
    requested: list[httpx.Request] = []
    client = _client({OPENROUTER_MODELS_URL: httpx.Response(200, json=OPENROUTER_PAYLOAD)}, requested)

    openrouter_models(client)

    assert "authorization" not in {name.lower() for name in requested[0].headers}


def test_ollama_lists_installed_models():
    client = _client(
        {"http://ollama:11434/api/tags": httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})}
    )

    options = list_models(LLMProvider.OLLAMA, client, "http://ollama:11434/")

    assert [(option.id, option.label) for option in options] == [("qwen3:8b", "qwen3:8b (local)")]


def test_static_suggestions_for_anthropic():
    options = list_models(LLMProvider.ANTHROPIC, _client({}))

    assert options[0].id == "claude-opus-5-5"


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, text="não é json")])
def test_unavailable_catalog_raises_clear_error(response):
    with pytest.raises(CatalogUnavailableError, match="Não foi possível listar"):
        openrouter_models(_client({OPENROUTER_MODELS_URL: response}))
