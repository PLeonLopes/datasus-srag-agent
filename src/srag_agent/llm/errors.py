"""Classificação de erros de LLM, independente do provedor.

Temporário (limite de requisições, 5xx, timeout, sem conexão): o relatório segue com texto de reserva.
Configuração (chave inválida, modelo inexistente, parâmetro recusado): a execução para, porque um texto
de reserva esconderia uma configuração quebrada.
"""

import httpx

_TRANSIENT_STATUS = frozenset({408, 409, 429})
_TRANSIENT_CLASS_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
    }
)


def status_code(error: BaseException) -> int | None:
    code = getattr(error, "status_code", None)
    if code is None:
        code = getattr(getattr(error, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def is_transient(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError | TimeoutError | ConnectionError):
        return True
    if any(cls.__name__ in _TRANSIENT_CLASS_NAMES for cls in type(error).__mro__):
        return True
    code = status_code(error)
    return code is not None and (code in _TRANSIENT_STATUS or code >= 500)
