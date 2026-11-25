import sqlite3
from contextlib import closing

import pytest

from srag_agent.data.database import connect_readonly, read_load_metadata
from srag_agent.data.etl import cases_table_ddl
from srag_agent.tools import sql as sql_tool
from srag_agent.tools.sql import (
    MAX_ROWS,
    SqlExecutionError,
    SqlRejectedError,
    describe_schema,
    run_query,
    validate_and_rewrite,
)


@pytest.fixture
def connection(synthetic_db):
    with closing(connect_readonly(synthetic_db)) as connection:
        yield connection


@pytest.fixture
def small_db():
    """Banco em memória com contagens controladas por UF."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(cases_table_ddl())
        rows = [("2026-10-01", "SP")] * 7 + [("2026-10-01", "AC")] * 2
        connection.executemany("INSERT INTO cases (symptom_date, state) VALUES (?, ?)", rows)
        yield connection


class TestValidation:
    @pytest.mark.parametrize(
        ("sql", "message"),
        [
            ("SELECT * FROM cases", "agregadas"),
            ("SELECT state FROM cases", "agregadas"),
            ("DELETE FROM cases", "SELECT"),
            ("DROP TABLE cases", "SELECT"),
            ("PRAGMA table_info(cases)", "SELECT"),
            ("ATTACH DATABASE 'x.db' AS x", "SELECT"),
            ("SELECT COUNT(*) FROM cases; DELETE FROM cases", "exatamente uma"),
            ("SELECT COUNT(*) FROM load_metadata", "tabela `cases`"),
            ("SELECT COUNT(*) FROM sqlite_master", "tabela `cases`"),
            ("SELECT COUNT(*) FROM main.cases", "tabela `cases`"),
            ("SELECT COUNT(*) FROM (SELECT * FROM cases)", "Subconsultas"),
            ("WITH t AS (SELECT * FROM cases) SELECT COUNT(*) FROM t", "Subconsultas"),
            ("SELECT COUNT(*) FROM cases UNION SELECT COUNT(*) FROM cases", "SELECT"),
            ("SELECT state, COUNT(*) OVER () FROM cases", "Subconsultas"),
            ("SELECT COUNT(*) FROM cases LIMIT abc", "LIMIT"),
            ("SELEC COUNT(*) FROM", "SQL inválido"),
        ],
    )
    def test_rejects_unsafe_or_non_aggregate_queries(self, sql, message):
        with pytest.raises(SqlRejectedError, match=message):
            validate_and_rewrite(sql)

    def test_adds_group_size_and_default_limit(self):
        rewritten = validate_and_rewrite("SELECT state, COUNT(*) FROM cases GROUP BY state")

        assert "AS _group_size" in rewritten
        assert rewritten.endswith(f"LIMIT {MAX_ROWS}")

    def test_caps_limit_above_maximum(self):
        assert validate_and_rewrite("SELECT COUNT(*) FROM cases LIMIT 5000").endswith(f"LIMIT {MAX_ROWS}")
        assert validate_and_rewrite("SELECT COUNT(*) FROM cases LIMIT 10").endswith("LIMIT 10")


class TestExecution:
    def test_runs_aggregate_query(self, connection):
        result = run_query(connection, "SELECT outcome, COUNT(*) AS casos FROM cases GROUP BY outcome")

        assert result.columns == ["outcome", "casos"]
        assert sum(row[1] for row in result.rows) == 5_000
        assert "_group_size" not in result.columns

    def test_suppresses_groups_smaller_than_minimum(self, small_db):
        result = run_query(small_db, "SELECT state, COUNT(*) AS n FROM cases GROUP BY state")

        assert result.rows == [["SP", 7]]
        assert result.suppressed_groups == 1

    def test_suppresses_single_group_filtered_to_few_cases(self, small_db):
        result = run_query(small_db, "SELECT COUNT(*) FROM cases WHERE state = 'AC'")

        assert result.rows == []
        assert result.suppressed_groups == 1

    def test_zero_count_is_not_suppressed(self, small_db):
        result = run_query(small_db, "SELECT COUNT(*) FROM cases WHERE state = 'RJ'")

        assert result.rows == [[0]]

    def test_engine_blocks_functions_outside_allowlist(self, connection):
        with pytest.raises(SqlExecutionError, match="not authorized"):
            run_query(connection, "SELECT COUNT(*) FROM cases WHERE load_extension('x') IS NULL")

    def test_engine_reports_unknown_column(self, connection):
        with pytest.raises(SqlExecutionError, match="no such column"):
            run_query(connection, "SELECT cpf, COUNT(*) FROM cases GROUP BY cpf")

    def test_query_is_interrupted_after_timeout(self, connection, monkeypatch):
        monkeypatch.setattr(sql_tool, "TIMEOUT_SECONDS", -1.0)

        with pytest.raises(SqlExecutionError, match="interrupted"):
            run_query(connection, "SELECT state, COUNT(*) FROM cases GROUP BY state")

    def test_connection_is_restored_after_query(self, connection):
        with pytest.raises(SqlExecutionError):
            run_query(connection, "SELECT COUNT(*) FROM cases WHERE load_extension('x') IS NULL")

        assert read_load_metadata(connection)["synthetic"] is True  # authorizer foi removido


def test_schema_description_lists_columns_and_rules():
    description = describe_schema()

    assert "symptom_date" in description
    assert "srag_death" in description
    assert "menos de 5 casos" in description
