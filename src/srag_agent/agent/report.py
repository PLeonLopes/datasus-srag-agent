"""Montagem do relatório em Markdown.

Números, tabelas, gráficos e links vêm do código; o LLM só escreve a análise.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime

from srag_agent.agent.toolkit import Evidence, charts_payload
from srag_agent.formatting import br_date as _date
from srag_agent.formatting import integer as _int
from srag_agent.formatting import percent as _pct
from srag_agent.tools.charts import DAILY_CHART_FILE, MONTHLY_CHART_FILE, ChartsResult
from srag_agent.tools.metrics import (
    GROWTH_WINDOW_DAYS,
    MATURATION_DAYS,
    RATE_WINDOW_DAYS,
    SHORT_GROWTH_WINDOW_DAYS,
    GrowthMetric,
    MetricsReport,
    RateMetric,
    Window,
)
from srag_agent.tools.news import NewsResult
from srag_agent.tools.sql import MIN_GROUP_SIZE

REPORT_FILE = "relatorio.md"
DATA_FILE = "relatorio.json"  # os mesmos dados do Markdown, estruturados para a interface


@dataclass(frozen=True)
class ReportContext:
    run_id: str
    generated_at: datetime
    provider: str
    model: str
    sources: list[dict]
    warnings: list[str]


@dataclass(frozen=True)
class Analysis:
    text: str
    generated_by_llm: bool
    attempts: int
    fallback_reason: str = ""


def render_report(context: ReportContext, evidence: Evidence, analysis: Analysis) -> str:
    metrics = evidence.metrics
    if metrics is None or evidence.charts is None:
        raise ValueError("Métricas e gráficos são obrigatórios para montar o relatório")
    sections = [
        _header(context, metrics),
        _metrics_section(metrics),
        _charts_section(evidence.charts),
        _analysis_section(context, analysis),
        _sql_section(evidence.sql),
        _news_section(evidence.news),
        _methodology_section(),
        _traceability_section(context),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def report_data(context: ReportContext, evidence: Evidence, analysis: Analysis) -> dict:
    return {
        "context": {**asdict(context), "generated_at": context.generated_at.isoformat(timespec="seconds")},
        "metrics": evidence.metrics.to_dict() if evidence.metrics else None,
        "charts": charts_payload(evidence.charts) if evidence.charts else None,
        "sql": evidence.sql,
        "news": evidence.news.to_dict() if evidence.news else None,
        "analysis": asdict(analysis),
    }


def fallback_explanation(metrics: MetricsReport) -> str:
    """Análise gerada por regras, usada quando o LLM falha ou não passa nos guardrails."""
    growth = metrics.case_growth
    short = metrics.case_growth_short
    parts = [
        f"Nos {GROWTH_WINDOW_DAYS} dias até {_date(metrics.data_end)}, foram registrados "
        f"{_int(growth.current_cases)} casos de SRAG, {_direction(growth)} em relação aos "
        f"{GROWTH_WINDOW_DAYS} dias anteriores ({_int(growth.previous_cases)} casos). "
        f"Na comparação de {SHORT_GROWTH_WINDOW_DAYS} dias, o resultado foi {_direction(short)}."
    ]
    parts.append(
        f"Nos últimos {RATE_WINDOW_DAYS} dias consolidados, a mortalidade entre os casos com desfecho foi de "
        f"{_pct(metrics.mortality.value_pct)}, com {_pct(metrics.pending_outcome_pct)} dos casos ainda sem "
        f"desfecho; {_pct(metrics.icu_admission.value_pct)} dos internados com a informação registrada "
        f"passaram pela UTI. Entre os casos que informaram, {_pct(metrics.flu_vaccination.value_pct)} "
        f"tinham vacina contra a gripe e {_pct(metrics.covid_vaccination.value_pct)} contra a COVID-19."
    )
    return "\n\n".join(parts)


def _header(context: ReportContext, metrics: MetricsReport) -> str:
    lines = [
        f"# Relatório de SRAG: dados até {_date(metrics.reference_date)}",
        "",
        f"Gerado em {context.generated_at:%d/%m/%Y %H:%M} (UTC) · execução `{context.run_id}` · "
        f"modelo `{context.provider}/{context.model}` · fonte: Open DATASUS (SIVEP-Gripe)",
    ]
    lines += [f"\n> **Atenção:** {warning}" for warning in context.warnings]
    return "\n".join(lines)


def _metrics_section(metrics: MetricsReport) -> str:
    growth, short = metrics.case_growth, metrics.case_growth_short
    rows = [
        (
            f"Taxa de aumento de casos ({GROWTH_WINDOW_DAYS} dias)",
            _pct(growth.value_pct),
            f"{_int(growth.current_cases)} casos contra {_int(growth.previous_cases)} no período anterior",
            _window(growth.current_window),
        ),
        (
            f"Taxa de aumento de casos ({SHORT_GROWTH_WINDOW_DAYS} dias)",
            _pct(short.value_pct),
            f"{_int(short.current_cases)} casos contra {_int(short.previous_cases)} no período anterior",
            _window(short.current_window),
        ),
        _rate_row(
            "Taxa de mortalidade",
            metrics.mortality,
            "{numerator} óbitos por SRAG em {denominator} casos com desfecho "
            f"({_pct(metrics.pending_outcome_pct)} dos casos ainda sem desfecho)",
        ),
        _rate_row(
            "Taxa de internação em UTI",
            metrics.icu_admission,
            "{numerator} em UTI de {denominator} internados com UTI informada",
        ),
        _rate_row(
            "Vacinação contra gripe (entre os casos)",
            metrics.flu_vaccination,
            "{numerator} vacinados de {denominator} casos que informaram",
        ),
        _rate_row(
            "Vacinação contra COVID-19 (entre os casos)",
            metrics.covid_vaccination,
            "{numerator} vacinados de {denominator} casos que informaram",
        ),
    ]
    table = ["| Métrica | Valor | Base | Período |", "|---|---|---|---|"]
    table += [f"| {name} | **{value}** | {base} | {period} |" for name, value, base, period in rows]
    notes = [
        "",
        f"> Todas as janelas terminam {MATURATION_DAYS} dias antes da data dos dados, porque os casos "
        "recentes ainda estão sendo notificados. A taxa de UTI mede a gravidade dos internados, **não** a "
        "ocupação de leitos, e a vacinação é medida entre os casos de SRAG, **não** na população: a base "
        "não tem número de leitos nem população.",
    ]
    return "## Métricas\n\n" + "\n".join(table + notes)


def _charts_section(charts: ChartsResult) -> str:
    daily_rows = "\n".join(
        f"| {_date(date.fromisoformat(day))} | {_int(n)} |" for day, n in charts.daily_cases.items()
    )
    monthly_rows = "\n".join(
        f"| {month[5:]}/{month[:4]} | {_int(n)} |" for month, n in charts.monthly_cases.items()
    )
    return (
        "## Casos diários (últimos 30 dias)\n\n"
        f"![Casos diários]({DAILY_CHART_FILE})\n\n"
        f"Dias a partir de {_date(date.fromisoformat(charts.first_incomplete_day))} "
        "ainda estão incompletos.\n\n"
        "<details><summary>Tabela</summary>\n\n| Dia | Casos |\n|---|---|\n"
        f"{daily_rows}\n\n</details>\n\n"
        "## Casos mensais (últimos 12 meses)\n\n"
        f"![Casos mensais]({MONTHLY_CHART_FILE})\n\n"
        "O mês corrente ainda está incompleto.\n\n"
        "<details><summary>Tabela</summary>\n\n| Mês | Casos |\n|---|---|\n"
        f"{monthly_rows}\n\n</details>"
    )


def _analysis_section(context: ReportContext, analysis: Analysis) -> str:
    if analysis.generated_by_llm:
        origin = (
            f"Texto gerado por IA (`{context.provider}/{context.model}`) e aprovado pelos guardrails "
            f"automáticos na tentativa {analysis.attempts}. Revisão humana recomendada."
        )
    else:
        origin = f"Texto gerado por regras, **sem IA** ({analysis.fallback_reason})."
    return f"## Análise do cenário\n\n> {origin}\n\n{analysis.text.strip()}"


def _sql_section(queries: list[dict]) -> str:
    if not queries:
        return ""
    blocks = ["## Investigações do agente (SQL)"]
    for query in queries:
        header = "| " + " | ".join(query["columns"]) + " |\n|" + "---|" * len(query["columns"])
        rows = "\n".join("| " + " | ".join(_cell(value) for value in row) + " |" for row in query["rows"])
        note = (
            f"\n\n{query['suppressed_groups']} grupo(s) com menos de {MIN_GROUP_SIZE} casos omitido(s)."
            if query["suppressed_groups"]
            else ""
        )
        blocks.append(f"```sql\n{query['query'].strip()}\n```\n\n{header}\n{rows}{note}")
    return "\n\n".join(blocks)


def _news_section(news: NewsResult | None) -> str:
    if news is None or not news.items:
        return "## Notícias consultadas\n\nNenhuma notícia disponível nesta execução."
    lines = ["## Notícias consultadas", ""]
    lines += [
        f"- [{item.title}]({item.link}) · {item.source} · {_date(date.fromisoformat(item.published))}"
        for item in news.items
    ]
    if news.failed_feeds:
        failed = ", ".join(failure.feed for failure in news.failed_feeds)
        lines += ["", f"Fontes indisponíveis nesta execução: {failed}."]
    lines += ["", "As manchetes são tratadas como dado não confiável e filtradas antes de chegar ao modelo."]
    return "\n".join(lines)


def _methodology_section() -> str:
    return (
        "## Metodologia e limitações\n\n"
        "- Data do caso: início dos sintomas (`DT_SIN_PRI`); o arquivo do ano corrente é atualizado "
        "semanalmente e os números de dias recentes ainda mudam.\n"
        "- Mortalidade provisória: casos sem desfecho não entram no denominador e podem mudar o valor.\n"
        "- Apenas 10 das 194 colunas da fonte são lidas; a idade exata vira faixa etária e consultas "
        f"livres omitem grupos com menos de {MIN_GROUP_SIZE} casos.\n"
        "- O texto da análise é verificado por guardrails automáticos (números, tendência, ausência de "
        "conselhos médicos e previsões), mas não substitui a revisão por um profissional."
    )


def _traceability_section(context: ReportContext) -> str:
    files = "\n".join(
        f"- `{source['filename']}` (sha256 `{source['sha256'][:16]}…`)" for source in context.sources
    )
    return (
        "## Rastreabilidade\n\n"
        f"Execução `{context.run_id}`: todas as decisões do agente estão na trilha de auditoria "
        "(`logs/audit.jsonl`). Arquivos de dados usados:\n\n"
        f"{files}"
    )


def _rate_row(name: str, rate: RateMetric, base_template: str) -> tuple[str, str, str, str]:
    base = base_template.format(numerator=_int(rate.numerator), denominator=_int(rate.denominator))
    return name, _pct(rate.value_pct), base, _window(rate.window)


def _direction(growth: GrowthMetric) -> str:
    if growth.value_pct is None:
        return "sem base de comparação"
    if growth.value_pct > 0:
        return f"um aumento de {_pct(growth.value_pct)}"
    if growth.value_pct < 0:
        return f"uma queda de {_pct(abs(growth.value_pct))}"
    return "estável (0,0%)"


def _window(window: Window) -> str:
    return f"{_date(window.start)} a {_date(window.end)}"


def _cell(value: object) -> str:
    if value is None:
        return "não informado"
    if isinstance(value, float):
        return f"{value:.1f}".replace(".", ",")
    if isinstance(value, int):
        return _int(value)
    return str(value).replace("|", "/")
