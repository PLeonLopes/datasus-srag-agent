"""Formatação em português do Brasil, compartilhada pelo relatório e pela interface."""

from dataclasses import dataclass
from datetime import date


def percent(value: float | None) -> str:
    return "sem dados" if value is None else f"{value:.1f}%".replace(".", ",")


def integer(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def br_date(value: date | str) -> str:
    return f"{date.fromisoformat(value) if isinstance(value, str) else value:%d/%m/%Y}"


@dataclass(frozen=True)
class MetricCard:
    label: str
    value: str
    detail: str
    help: str


def metric_cards(metrics: dict) -> list[MetricCard]:
    growth, short = metrics["case_growth"], metrics["case_growth_short"]
    mortality, icu = metrics["mortality"], metrics["icu_admission"]
    flu, covid = metrics["flu_vaccination"], metrics["covid_vaccination"]
    return [
        MetricCard(
            "Aumento de casos (28 dias)",
            percent(growth["value_pct"]),
            f"{integer(growth['current_cases'])} contra {integer(growth['previous_cases'])}",
            "Casos dos últimos 28 dias consolidados contra os 28 anteriores.",
        ),
        MetricCard(
            "Aumento de casos (7 dias)",
            percent(short["value_pct"]),
            f"{integer(short['current_cases'])} contra {integer(short['previous_cases'])}",
            "Sinal rápido: últimos 7 dias consolidados contra os 7 anteriores.",
        ),
        MetricCard(
            "Mortalidade",
            percent(mortality["value_pct"]),
            f"{percent(metrics['pending_outcome_pct'])} ainda sem desfecho",
            "Óbitos por SRAG entre os casos com desfecho (90 dias). Provisória: "
            "casos sem desfecho podem mudá-la.",
        ),
        MetricCard(
            "Internação em UTI",
            percent(icu["value_pct"]),
            f"{integer(icu['numerator'])} de {integer(icu['denominator'])} internados",
            "Proporção de internados que foram para a UTI. "
            "Não é a ocupação de leitos: a base não tem leitos.",
        ),
        MetricCard(
            "Vacina gripe (casos)",
            percent(flu["value_pct"]),
            f"{integer(flu['denominator'])} casos informaram",
            "Vacinação contra gripe entre os casos de SRAG. Não é a cobertura da população.",
        ),
        MetricCard(
            "Vacina COVID-19 (casos)",
            percent(covid["value_pct"]),
            f"{integer(covid['denominator'])} casos informaram",
            "Vacinação contra COVID-19 entre os casos de SRAG. Não é a cobertura da população.",
        ),
    ]
