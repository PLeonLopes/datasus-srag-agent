"""Métricas do relatório, calculadas em código: o LLM interpreta os números, nunca os calcula.

Todas as janelas terminam em `data de referência - MATURATION_DAYS`, porque os casos recentes ainda
estão sendo notificados e fariam a curva parecer em queda.
"""

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

from srag_agent.data.database import load_cases, reference_date

MATURATION_DAYS = 14
GROWTH_WINDOW_DAYS = 28
SHORT_GROWTH_WINDOW_DAYS = 7
RATE_WINDOW_DAYS = 90  # desfechos e internações demoram: janela maior que a de crescimento

_CLOSED_OUTCOMES = ("recovered", "srag_death", "other_death")
_ANSWERED = ("yes", "no")


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    @classmethod
    def ending_at(cls, end: date, days: int) -> "Window":
        return cls(start=end - timedelta(days=days - 1), end=end)

    def contains(self, dates: pd.Series) -> pd.Series:
        return dates.between(pd.Timestamp(self.start), pd.Timestamp(self.end))


@dataclass(frozen=True)
class GrowthMetric:
    value_pct: float | None  # None quando a janela anterior não tem casos
    current_cases: int
    previous_cases: int
    current_window: Window
    previous_window: Window


@dataclass(frozen=True)
class RateMetric:
    value_pct: float | None  # None quando o denominador é zero
    numerator: int
    denominator: int
    window: Window


@dataclass(frozen=True)
class MetricsReport:
    reference_date: date
    data_end: date
    case_growth: GrowthMetric
    case_growth_short: GrowthMetric
    mortality: RateMetric
    pending_outcome_pct: float | None
    icu_admission: RateMetric
    flu_vaccination: RateMetric
    covid_vaccination: RateMetric

    def to_dict(self) -> dict:
        """Versão serializável (datas ISO), usada no prompt e na auditoria."""
        return _isoformat_dates(asdict(self))


def compute_metrics(cases: pd.DataFrame, reference: date) -> MetricsReport:
    data_end = reference - timedelta(days=MATURATION_DAYS)
    rate_window = Window.ending_at(data_end, RATE_WINDOW_DAYS)
    recent = cases[rate_window.contains(cases["symptom_date"])]

    closed = recent[recent["outcome"].isin(_CLOSED_OUTCOMES)]
    hospitalized = recent[(recent["hospitalized"] == "yes") & recent["icu"].isin(_ANSWERED)]
    flu_answered = recent[recent["flu_vaccine"].isin(_ANSWERED)]
    covid_answered = recent[recent["covid_vaccine"].isin(_ANSWERED)]

    return MetricsReport(
        reference_date=reference,
        data_end=data_end,
        case_growth=_growth(cases, data_end, GROWTH_WINDOW_DAYS),
        case_growth_short=_growth(cases, data_end, SHORT_GROWTH_WINDOW_DAYS),
        mortality=_rate(int((closed["outcome"] == "srag_death").sum()), len(closed), rate_window),
        pending_outcome_pct=_pct(int(recent["outcome"].isna().sum()), len(recent)),
        icu_admission=_rate(int((hospitalized["icu"] == "yes").sum()), len(hospitalized), rate_window),
        flu_vaccination=_rate(
            int((flu_answered["flu_vaccine"] == "yes").sum()), len(flu_answered), rate_window
        ),
        covid_vaccination=_rate(
            int((covid_answered["covid_vaccine"] == "yes").sum()), len(covid_answered), rate_window
        ),
    )


def collect_metrics(connection: sqlite3.Connection) -> MetricsReport:
    """Lê do banco só o período necessário e calcula as métricas."""
    reference = reference_date(connection)
    data_end = reference - timedelta(days=MATURATION_DAYS)
    earliest = min(
        data_end - timedelta(days=2 * GROWTH_WINDOW_DAYS - 1),
        data_end - timedelta(days=RATE_WINDOW_DAYS - 1),
    )
    return compute_metrics(load_cases(connection, earliest, data_end), reference)


def _growth(cases: pd.DataFrame, end: date, days: int) -> GrowthMetric:
    current_window = Window.ending_at(end, days)
    previous_window = Window.ending_at(current_window.start - timedelta(days=1), days)
    current = int(current_window.contains(cases["symptom_date"]).sum())
    previous = int(previous_window.contains(cases["symptom_date"]).sum())
    value = round(100 * (current - previous) / previous, 1) if previous else None
    return GrowthMetric(value, current, previous, current_window, previous_window)


def _rate(numerator: int, denominator: int, window: Window) -> RateMetric:
    return RateMetric(_pct(numerator, denominator), numerator, denominator, window)


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _isoformat_dates(value):
    if isinstance(value, dict):
        return {key: _isoformat_dates(item) for key, item in value.items()}
    if isinstance(value, date):
        return value.isoformat()
    return value
