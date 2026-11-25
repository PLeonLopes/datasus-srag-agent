import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from srag_agent.config import LLMProvider
from srag_agent.llm import LLMConfigurationError, create_chat_model, resolve_model_name
from srag_agent.llm.factory import DEFAULT_MODELS, OPENAI_COMPATIBLE_BASE_URLS

OPENAI_COMPATIBLE = [
    (LLMProvider.OPENAI, "openai_api_key"),
    (LLMProvider.OPENROUTER, "openrouter_api_key"),
    (LLMProvider.HUGGINGFACE, "huggingface_api_key"),
]


class TestResolveModelName:
    @pytest.mark.parametrize("provider", [LLMProvider.OLLAMA, LLMProvider.ANTHROPIC])
    def test_uses_provider_default_when_model_is_not_set(self, make_settings, provider):
        assert resolve_model_name(make_settings(llm_provider=provider)) == DEFAULT_MODELS[provider]

    def test_configured_model_overrides_default(self, make_settings):
        settings = make_settings(llm_provider=LLMProvider.OLLAMA, llm_model="llama3.1:8b")

        assert resolve_model_name(settings) == "llama3.1:8b"

    @pytest.mark.parametrize(("provider", "_field"), OPENAI_COMPATIBLE)
    def test_requires_explicit_model_for_providers_without_default(self, make_settings, provider, _field):
        with pytest.raises(LLMConfigurationError, match="SRAG_LLM_MODEL"):
            resolve_model_name(make_settings(llm_provider=provider))


class TestCreateChatModel:
    def test_ollama_needs_no_api_key(self, make_settings):
        settings = make_settings(llm_provider=LLMProvider.OLLAMA, ollama_base_url="http://ollama:11434")

        llm = create_chat_model(settings)

        assert isinstance(llm, ChatOllama)
        assert llm.model == DEFAULT_MODELS[LLMProvider.OLLAMA]
        assert llm.base_url == "http://ollama:11434"

    def test_anthropic_never_sends_temperature(self, make_settings):
        settings = make_settings(
            llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="chave-de-teste", llm_temperature=0.7
        )

        llm = create_chat_model(settings)

        assert isinstance(llm, ChatAnthropic)
        assert llm.model == DEFAULT_MODELS[LLMProvider.ANTHROPIC]
        assert llm.temperature is None

    @pytest.mark.parametrize(("provider", "key_field"), OPENAI_COMPATIBLE)
    def test_openai_compatible_providers_use_their_endpoint(self, make_settings, provider, key_field):
        settings = make_settings(llm_provider=provider, llm_model="modelo-x", **{key_field: "chave-de-teste"})

        llm = create_chat_model(settings)

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "modelo-x"
        assert llm.openai_api_base == OPENAI_COMPATIBLE_BASE_URLS[provider]
        assert llm.openai_api_key.get_secret_value() == "chave-de-teste"

    @pytest.mark.parametrize(
        ("provider", "env_var"),
        [
            (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY"),
            (LLMProvider.OPENAI, "OPENAI_API_KEY"),
            (LLMProvider.OPENROUTER, "OPENROUTER_API_KEY"),
            (LLMProvider.HUGGINGFACE, "HF_TOKEN"),
        ],
    )
    def test_missing_api_key_names_the_env_var(self, make_settings, provider, env_var):
        settings = make_settings(llm_provider=provider, llm_model="modelo-x")

        with pytest.raises(LLMConfigurationError, match=env_var):
            create_chat_model(settings)

    def test_blank_api_key_is_rejected(self, make_settings):
        settings = make_settings(llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="   ")

        with pytest.raises(LLMConfigurationError, match="ANTHROPIC_API_KEY"):
            create_chat_model(settings)
