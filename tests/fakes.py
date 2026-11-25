"""Dublês para testar o agente sem rede e sem LLM real."""

from datetime import datetime
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from srag_agent.tools.news import NewsItem, NewsResult


class ScriptedChatModel(BaseChatModel):
    """Devolve as respostas na ordem dada; um item `Exception` é lançado no lugar da resposta."""

    responses: list[Any]
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("O LLM foi chamado mais vezes do que o roteiro previa")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def tool_calls(*names_and_args: tuple[str, dict]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{index}-{name}"}
            for index, (name, args) in enumerate(names_and_args)
        ],
    )


class FakeNewsProvider:
    def __init__(self, result: NewsResult | None = None) -> None:
        self.result = result or NewsResult(
            items=[
                NewsItem(
                    title="Fiocruz aponta aumento de casos graves de covid-19 em cinco estados",
                    source="Jornal Teste",
                    published="2026-11-22",
                    link="https://exemplo.com.br/noticia-12345",
                    summary="",
                )
            ]
        )
        self.calls = 0

    def fetch(self, client: httpx.Client, now: datetime) -> NewsResult:
        self.calls += 1
        return self.result


class HttpStatusError(Exception):
    """Erro de API com `status_code`, como os dos SDKs dos provedores."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
