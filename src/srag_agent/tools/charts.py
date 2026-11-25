"""Gráficos do relatório: casos diários (30 dias) e mensais (12 meses).

Os períodos ainda incompletos (atraso de notificação, mês corrente) ficam destacados com cor mais clara,
hachura e legenda, para nunca serem lidos como queda real.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from srag_agent.data.database import reference_date
from srag_agent.data.schema import CASES_TABLE
from srag_agent.tools.metrics import MATURATION_DAYS

DAILY_DAYS = 30
MONTHS = 12
DAILY_CHART_FILE = "casos_diarios.png"
MONTHLY_CHART_FILE = "casos_mensais.png"

_SERIES = "#2a78d6"
_INCOMPLETE = "#86b6ef"
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_MUTED = "#898781"
_GRID = "#e4e3df"


@dataclass(frozen=True)
class ChartsResult:
    daily_path: Path
    monthly_path: Path
    daily_cases: dict[str, int]  # "AAAA-MM-DD" -> casos
    monthly_cases: dict[str, int]  # "AAAA-MM" -> casos
    first_incomplete_day: str
    incomplete_month: str


def daily_counts(connection: sqlite3.Connection, reference: date) -> pd.Series:
    start = reference - timedelta(days=DAILY_DAYS - 1)
    rows = connection.execute(
        f"SELECT symptom_date, COUNT(*) FROM {CASES_TABLE} "  # noqa: S608
        "WHERE symptom_date BETWEEN ? AND ? GROUP BY symptom_date",
        (start.isoformat(), reference.isoformat()),
    ).fetchall()
    days = pd.date_range(start, reference, freq="D").strftime("%Y-%m-%d")
    return pd.Series(dict(rows), dtype="int64").reindex(days, fill_value=0)


def monthly_counts(connection: sqlite3.Connection, reference: date) -> pd.Series:
    last_month = pd.Period(reference, freq="M")
    months = pd.period_range(last_month - (MONTHS - 1), last_month, freq="M").strftime("%Y-%m")
    rows = connection.execute(
        f"SELECT substr(symptom_date, 1, 7) AS month, COUNT(*) FROM {CASES_TABLE} "  # noqa: S608
        "WHERE month BETWEEN ? AND ? GROUP BY month",
        (months[0], months[-1]),
    ).fetchall()
    return pd.Series(dict(rows), dtype="int64").reindex(months, fill_value=0)


def generate_charts(connection: sqlite3.Connection, out_dir: Path) -> ChartsResult:
    reference = reference_date(connection)
    first_incomplete_day = (reference - timedelta(days=MATURATION_DAYS - 1)).isoformat()
    daily = daily_counts(connection, reference)
    monthly = monthly_counts(connection, reference)

    out_dir.mkdir(parents=True, exist_ok=True)
    daily_path = out_dir / DAILY_CHART_FILE
    monthly_path = out_dir / MONTHLY_CHART_FILE
    _bar_chart(
        labels=[f"{day[8:]}/{day[5:7]}" for day in daily.index],
        values=daily.to_list(),
        incomplete=[day >= first_incomplete_day for day in daily.index],
        title=f"Casos de SRAG por dia de início dos sintomas (últimos {DAILY_DAYS} dias)",
        incomplete_label=f"Últimos {MATURATION_DAYS} dias: incompletos (atraso de notificação)",
        path=daily_path,
        label_every=3,
    )
    _bar_chart(
        labels=[f"{month[5:]}/{month[2:4]}" for month in monthly.index],
        values=monthly.to_list(),
        incomplete=[month == monthly.index[-1] for month in monthly.index],
        title=f"Casos de SRAG por mês de início dos sintomas (últimos {MONTHS} meses)",
        incomplete_label="Mês corrente: incompleto",
        path=monthly_path,
        label_every=1,
    )
    return ChartsResult(
        daily_path=daily_path,
        monthly_path=monthly_path,
        daily_cases={day: int(value) for day, value in daily.items()},
        monthly_cases={month: int(value) for month, value in monthly.items()},
        first_incomplete_day=first_incomplete_day,
        incomplete_month=monthly.index[-1],
    )


def _bar_chart(
    labels: list[str],
    values: list[int],
    incomplete: list[bool],
    title: str,
    incomplete_label: str,
    path: Path,
    label_every: int,
) -> None:
    figure = Figure(figsize=(10, 4.2), dpi=150, facecolor=_SURFACE)
    axes = figure.add_subplot()
    axes.set_facecolor(_SURFACE)

    positions = range(len(values))
    bars = axes.bar(
        positions,
        values,
        width=0.8,
        color=[_INCOMPLETE if flag else _SERIES for flag in incomplete],
        edgecolor=_SURFACE,
        linewidth=0,
    )
    for bar, flag in zip(bars, incomplete, strict=True):
        if flag:
            bar.set_hatch("///")
            bar.set_edgecolor(_SERIES)

    axes.set_title(title, loc="left", fontsize=11, color=_INK, pad=28)
    axes.set_ylabel("Casos", color=_MUTED, fontsize=9)
    axes.set_xticks(list(positions)[::label_every], labels[::label_every], fontsize=8, color=_MUTED)
    axes.tick_params(axis="y", labelsize=8, colors=_MUTED, length=0)
    axes.tick_params(axis="x", length=0)
    axes.yaxis.set_major_formatter(lambda value, _: f"{value:,.0f}".replace(",", "."))
    axes.grid(axis="y", color=_GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(_GRID)

    legend = [
        Patch(facecolor=_SERIES, label="Dados consolidados"),
        Patch(facecolor=_INCOMPLETE, edgecolor=_SERIES, hatch="///", label=incomplete_label),
    ]
    # Acima da área de plotagem: nunca cobre uma barra.
    axes.legend(
        handles=legend,
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        labelcolor=_INK,
        borderaxespad=0.3,
    )
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
