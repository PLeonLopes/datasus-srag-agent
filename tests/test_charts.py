import sqlite3
from contextlib import closing
from datetime import date

from srag_agent.data.database import connect_readonly
from srag_agent.data.etl import cases_table_ddl
from srag_agent.tools.charts import DAILY_DAYS, MONTHS, daily_counts, generate_charts, monthly_counts

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
REFERENCE = date(2026, 11, 23)


def test_generates_both_charts_with_their_data(synthetic_db, tmp_path):
    with closing(connect_readonly(synthetic_db)) as connection:
        result = generate_charts(connection, tmp_path)

    assert result.daily_path.read_bytes().startswith(PNG_SIGNATURE)
    assert result.monthly_path.read_bytes().startswith(PNG_SIGNATURE)
    assert len(result.daily_cases) == DAILY_DAYS
    assert len(result.monthly_cases) == MONTHS
    assert list(result.daily_cases)[-1] == "2026-11-23"
    assert min(result.monthly_cases) == "2025-12"
    assert result.incomplete_month == "2026-11"
    assert result.first_incomplete_day == "2026-11-10"


def test_periods_without_cases_appear_as_zero():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(cases_table_ddl())
        connection.executemany(
            "INSERT INTO cases (symptom_date) VALUES (?)", [("2026-11-20",), ("2026-11-20",), ("2026-01-15",)]
        )

        daily = daily_counts(connection, REFERENCE)
        monthly = monthly_counts(connection, REFERENCE)

    assert len(daily) == DAILY_DAYS
    assert daily["2026-11-20"] == 2
    assert daily.sum() == 2
    assert len(monthly) == MONTHS
    assert (monthly["2026-01"], monthly["2026-11"], monthly["2026-05"]) == (1, 2, 0)
