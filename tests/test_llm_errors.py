import httpx
import pytest

from fakes import HttpStatusError
from srag_agent.llm.errors import is_transient, status_code


class RateLimitError(Exception):
    """Mesmo nome da classe dos SDKs da OpenAI e da Anthropic."""


@pytest.mark.parametrize(
    "error",
    [
        HttpStatusError(429),
        HttpStatusError(500),
        HttpStatusError(503),
        HttpStatusError(408),
        RateLimitError("limite"),
        httpx.ConnectError("sem conexão"),
        httpx.ReadTimeout("timeout"),
        TimeoutError(),
    ],
)
def test_transient_errors(error):
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [HttpStatusError(400), HttpStatusError(401), HttpStatusError(403), HttpStatusError(404), ValueError()],
)
def test_configuration_errors_are_not_transient(error):
    assert not is_transient(error)


def test_status_code_reads_attribute_or_response():
    response_error = httpx.HTTPStatusError(
        "erro", request=httpx.Request("GET", "https://x"), response=httpx.Response(429)
    )

    assert status_code(HttpStatusError(401)) == 401
    assert status_code(response_error) == 429
    assert status_code(ValueError()) is None
