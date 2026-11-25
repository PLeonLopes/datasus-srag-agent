import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import pytest

from srag_agent.data.database import DatabaseError, connect_readonly
from srag_agent.tools.metrics import MATURATION_DAYS, collect_metrics, compute_metrics

REFERENCE = date(2026, 11, 23)
DATA_END = REFERENCE - timedelta(days=MATURATION_DAYS)  # 2026-11-09


def _cases(*rows: dict) -> pd.DataFrame:
    defaults = {
        "outcome": "recovered",
        "hospitalized": "yes",
        "icu": "no",
        "flu_vaccine": "no",
        "covid_vaccine": "no",
    }
    frame = pd.DataFrame([{**defaults, **row} for row in rows])
    frame["symptom_date"] = pd.to_datetime(frame["symptom_date"])
    return frame


def _on(day: date, count: int, **columns) -> list[dict]:
    return [{"symptom_date": day.isoformat(), **columns} for _ in range(count)]


class TestCaseGrowth:
    def test_compares_last_28_days_with_previous_28(self):
        cases = _cases(*_on(DATA_END, 30), *_on(DATA_END - timedelta(days=28), 20))

        growth = compute_metrics(cases, REFERENCE).case_growth

        assert (growth.current_cases, growth.previous_cases, growth.value_pct) == (30, 20, 50.0)
        assert growth.current_window.end == DATA_END
        assert growth.previous_window.end == DATA_END - timedelta(days=28)

    def test_ignores_cases_after_maturation_cutoff(self):
        cases = _cases(*_on(DATA_END, 10), *_on(DATA_END + timedelta(days=1), 500))

        assert compute_metrics(cases, REFERENCE).case_growth.current_cases == 10

    def test_growth_is_none_without_previous_cases(self):
        cases = _cases(*_on(DATA_END, 10))

        assert compute_metrics(cases, REFERENCE).case_growth.value_pct is None


class TestRates:
    def test_mortality_uses_only_closed_outcomes_and_reports_pending(self):
        cases = _cases(
            *_on(DATA_END, 1, outcome="srag_death"),
            *_on(DATA_END, 3, outcome="recovered"),
            *_on(DATA_END, 2, outcome="unknown"),
            *_on(DATA_END, 4, outcome=None),
        )

        metrics = compute_metrics(cases, REFERENCE)

        assert (metrics.mortality.numerator, metrics.mortality.denominator) == (1, 4)
        assert metrics.mortality.value_pct == 25.0
        assert metrics.pending_outcome_pct == 40.0

    def test_icu_rate_counts_hospitalized_cases_with_answer(self):
        cases = _cases(
            *_on(DATA_END, 1, icu="yes"),
            *_on(DATA_END, 3, icu="no"),
            *_on(DATA_END, 5, icu="unknown"),
            *_on(DATA_END, 7, icu="yes", hospitalized="no"),
        )

        icu = compute_metrics(cases, REFERENCE).icu_admission

        assert (icu.numerator, icu.denominator, icu.value_pct) == (1, 4, 25.0)

    def test_vaccination_excludes_unknown_and_missing(self):
        cases = _cases(
            *_on(DATA_END, 2, flu_vaccine="yes", covid_vaccine="yes"),
            *_on(DATA_END, 2, flu_vaccine="no", covid_vaccine="unknown"),
            *_on(DATA_END, 6, flu_vaccine=None, covid_vaccine="no"),
        )

        metrics = compute_metrics(cases, REFERENCE)

        assert (metrics.flu_vaccination.denominator, metrics.flu_vaccination.value_pct) == (4, 50.0)
        assert (metrics.covid_vaccination.denominator, metrics.covid_vaccination.value_pct) == (8, 25.0)

    def test_rates_ignore_cases_outside_90_day_window(self):
        cases = _cases(
            *_on(DATA_END, 1, outcome="srag_death"),
            *_on(DATA_END - timedelta(days=90), 9, outcome="recovered"),
        )

        assert compute_metrics(cases, REFERENCE).mortality.value_pct == 100.0

    def test_rate_is_none_without_denominator(self):
        cases = _cases(*_on(DATA_END, 3, outcome=None))

        assert compute_metrics(cases, REFERENCE).mortality.value_pct is None


def test_collect_metrics_from_database_is_json_serializable(synthetic_db):
    with closing(connect_readonly(synthetic_db)) as connection:
        metrics = collect_metrics(connection)

    payload = json.loads(json.dumps(metrics.to_dict()))
    assert payload["reference_date"] == "2026-11-23"
    assert payload["data_end"] == "2026-11-09"
    assert payload["case_growth"]["current_cases"] > 0


def test_collect_metrics_requires_load_metadata(tmp_path):
    db_path = tmp_path / "vazio.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE load_metadata (key TEXT, value TEXT)")
        connection.commit()

    with closing(connect_readonly(db_path)) as connection, pytest.raises(DatabaseError):
        collect_metrics(connection)
