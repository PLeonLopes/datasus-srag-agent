"""Acesso somente leitura ao banco de SRAG."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from srag_agent.data.schema import CASES_COLUMNS, CASES_TABLE, METADATA_TABLE


class DatabaseError(RuntimeError):
    """Banco ausente ou fora do formato esperado."""


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Conexão que não consegue escrever: modo `ro` na URI e `query_only` por garantia."""
    if not db_path.exists():
        raise DatabaseError(f"Banco não encontrado em {db_path}: rode `python -m srag_agent.data.etl`")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_load_metadata(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(f"SELECT key, value FROM {METADATA_TABLE}").fetchall()  # noqa: S608
    return {key: json.loads(value) for key, value in rows}


def reference_date(connection: sqlite3.Connection) -> date:
    """Data do snapshot mais recente carregado: as janelas partem dela, não de hoje."""
    sources = read_load_metadata(connection).get("sources") or []
    if not sources:
        raise DatabaseError("Banco sem metadados de carga")
    return max(date.fromisoformat(source["snapshot_date"]) for source in sources)


def load_cases(connection: sqlite3.Connection, start: date, end: date) -> pd.DataFrame:
    """Casos com data de sintomas entre `start` e `end` (inclusive)."""
    query = (
        f"SELECT {', '.join(CASES_COLUMNS)} FROM {CASES_TABLE} "  # noqa: S608
        "WHERE symptom_date BETWEEN ? AND ?"
    )
    cases = pd.read_sql_query(query, connection, params=(start.isoformat(), end.isoformat()))
    cases["symptom_date"] = pd.to_datetime(cases["symptom_date"])
    return cases
