"""SQL livre do agente, com guardrails em camadas.

1. Parser (sqlglot): um único SELECT agregado sobre `cases`, sem subconsultas, CTEs, UNION ou window.
2. Reescrita: acrescenta COUNT(*) para suprimir grupos pequenos e impõe LIMIT.
3. Motor (sqlite3): conexão somente leitura e authorizer que nega tudo além de ler `cases`.
4. Execução: timeout e limite de linhas.

Sem subconsultas, o COUNT(*) acrescentado mede exatamente quantos casos há em cada grupo, o que torna a
supressão de grupos pequenos (risco de reidentificação) uma garantia, e não uma aproximação.
"""

import sqlite3
import time
from dataclasses import asdict, dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from srag_agent.data.schema import AGE_GROUP_LABELS, CASES_COLUMNS, CASES_TABLE, CATEGORICAL_COLUMNS, STATES

MAX_ROWS = 200
MIN_GROUP_SIZE = 5
TIMEOUT_SECONDS = 5.0
GROUP_SIZE_COLUMN = "_group_size"

ALLOWED_FUNCTIONS = frozenset(
    {
        "count", "sum", "avg", "min", "max", "total", "round", "abs", "coalesce", "ifnull", "nullif",
        "iif", "date", "strftime", "julianday", "substr", "substring", "lower", "upper", "length",
    }
)  # fmt: skip

_FORBIDDEN_NODES = (exp.Subquery, exp.CTE, exp.Window, exp.Union, exp.Intersect, exp.Except)


class SqlToolError(ValueError):
    """Erro devolvido ao agente para que ele corrija a consulta."""


class SqlRejectedError(SqlToolError):
    """A consulta violou um guardrail e não foi executada."""


class SqlExecutionError(SqlToolError):
    """A consulta passou nos guardrails, mas o SQLite recusou ou interrompeu a execução."""


@dataclass(frozen=True)
class SqlResult:
    executed_sql: str
    columns: list[str]
    rows: list[list]
    suppressed_groups: int
    limit_reached: bool

    def to_dict(self) -> dict:
        return asdict(self)


def validate_and_rewrite(sql: str) -> str:
    """Valida a consulta e devolve a versão que será executada (com contagem de grupo e LIMIT)."""
    try:
        statements = [statement for statement in sqlglot.parse(sql, read="sqlite") if statement is not None]
    except ParseError as error:
        raise SqlRejectedError(f"SQL inválido: {error}") from error
    if len(statements) != 1:
        raise SqlRejectedError("Envie exatamente uma consulta.")

    query = statements[0]
    if not isinstance(query, exp.Select):
        raise SqlRejectedError("Só consultas SELECT são permitidas.")
    if query.find(*_FORBIDDEN_NODES):
        raise SqlRejectedError("Subconsultas, CTEs, UNION e window functions não são permitidas.")

    tables = list(query.find_all(exp.Table))
    if not tables or any(table.name != CASES_TABLE or table.db or table.catalog for table in tables):
        raise SqlRejectedError(f"Só a tabela `{CASES_TABLE}` pode ser consultada.")

    is_aggregate = bool(query.args.get("group")) or any(
        expression.find(exp.AggFunc) for expression in query.expressions
    )
    if not is_aggregate:
        raise SqlRejectedError("Só consultas agregadas (GROUP BY ou COUNT/SUM/AVG...) são permitidas.")

    rewritten = query.select(exp.alias_(exp.Count(this=exp.Star()), GROUP_SIZE_COLUMN), copy=True)
    return rewritten.limit(_limit(query), copy=True).sql(dialect="sqlite")


def run_query(connection: sqlite3.Connection, sql: str) -> SqlResult:
    executed_sql = validate_and_rewrite(sql)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    connection.set_authorizer(_authorizer)
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        cursor = connection.execute(executed_sql)
        columns = [description[0] for description in cursor.description]
        raw_rows = cursor.fetchall()
    except sqlite3.DatabaseError as error:
        raise SqlExecutionError(f"O SQLite recusou a consulta: {error}") from error
    finally:
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)

    size_index = columns.index(GROUP_SIZE_COLUMN)
    kept = [row for row in raw_rows if not 0 < row[size_index] < MIN_GROUP_SIZE]
    return SqlResult(
        executed_sql=executed_sql,
        columns=[column for index, column in enumerate(columns) if index != size_index],
        rows=[[value for index, value in enumerate(row) if index != size_index] for row in kept],
        suppressed_groups=len(raw_rows) - len(kept),
        limit_reached=len(raw_rows) >= MAX_ROWS,
    )


def describe_schema() -> str:
    """Descrição da tabela para o agente: colunas e valores possíveis."""
    values = {target: sorted(set(mapping.values())) for target, mapping in CATEGORICAL_COLUMNS.values()}
    values["state"] = sorted(STATES)
    values["age_group"] = AGE_GROUP_LABELS
    lines = [f"Tabela `{CASES_TABLE}` (um caso de SRAG por linha; NULL = não informado):"]
    lines.append("- symptom_date: data de início dos sintomas, texto 'AAAA-MM-DD'")
    lines += [f"- {column}: {', '.join(values[column])}" for column in CASES_COLUMNS[1:]]
    lines.append(
        f"Regras: um único SELECT agregado, sem subconsultas; grupos com menos de {MIN_GROUP_SIZE} casos "
        f"são omitidos; no máximo {MAX_ROWS} linhas."
    )
    return "\n".join(lines)


def _limit(query: exp.Select) -> int:
    limit = query.args.get("limit")
    if limit is None:
        return MAX_ROWS
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise SqlRejectedError("LIMIT precisa ser um número inteiro.")
    return min(int(value.name), MAX_ROWS)


def _authorizer(
    action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and arg1 == CASES_TABLE:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in ALLOWED_FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
