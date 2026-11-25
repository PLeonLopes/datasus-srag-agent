"""Página de geração e leitura dos relatórios, organizada em painéis."""

from datetime import date

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from srag_agent.agent.runner import PreflightError, run_report
from srag_agent.config import LLMProvider, Settings, get_settings
from srag_agent.data.database import DatabaseError
from srag_agent.data.pipeline import refresh_data
from srag_agent.formatting import br_date, integer
from srag_agent.governance.audit import AuditLog
from srag_agent.llm.catalog import CatalogUnavailableError, ModelOption, list_models
from srag_agent.llm.factory import CREDENTIALS, DEFAULT_MODELS, LLMConfigurationError
from srag_agent.ui.reports import (
    NODE_LABELS,
    SavedReport,
    chart_frame,
    explain_error,
    list_reports,
    metric_cards,
    split_report,
)
from srag_agent.ui.status import data_status

OTHER_MODEL = "__outro__"
SERIES_COLORS = ["#3987e5", "#5a6b85"]  # consolidado, incompleto (mais apagado)

settings = get_settings()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_models(provider: str, ollama_base_url: str) -> tuple[list[ModelOption], str | None]:
    """Lista de modelos por 1 hora: trocar de provedor não refaz a consulta."""
    with httpx.Client() as client:
        try:
            return list_models(LLMProvider(provider), client, ollama_base_url), None
        except CatalogUnavailableError as error:
            return [], str(error)


# ---------------------------------------------------------------- barra lateral
def sidebar_data() -> bool:
    st.markdown("#### Dados")
    status = data_status(settings, date.today())
    if status is None:
        st.warning("Os dados ainda não foram baixados.")
    else:
        st.markdown(f"Dados até **{br_date(status.reference_date)}** · {integer(status.rows_loaded)} casos")
        for reason in status.blocked_reasons:
            st.error(reason)
        for warning in status.warnings:
            st.warning(warning)
    if st.button(
        "Atualizar dados",
        icon=":material/download:",
        width="stretch",
        help="Baixa o snapshot mais recente do Open DATASUS (cerca de 45 MB) e recarrega o banco.",
    ):
        with st.spinner("Baixando e carregando os dados…"):
            try:
                refresh_data(settings, AuditLog(settings.audit_log_path))
            except Exception as error:  # detalhes ficam na auditoria
                st.error(f"Falha ao atualizar: {explain_error(error)}")
                return False
        st.rerun()
    return bool(status and status.passed)


def sidebar_model() -> Settings:
    st.markdown("#### Modelo de linguagem")
    providers = [provider.value for provider in LLMProvider]
    provider = LLMProvider(
        st.selectbox("Provedor", providers, index=providers.index(settings.llm_provider.value))
    )

    options, error = cached_models(provider.value, settings.ollama_base_url)
    if provider is LLMProvider.OPENROUTER and st.toggle("Só modelos gratuitos", value=True):
        options = [option for option in options if option.free]
    if error:
        st.caption(f"{error}: digite o nome do modelo.")

    labels = {option.id: option.label for option in options} | {OTHER_MODEL: "Outro (digitar o nome)"}
    preferred = settings.llm_model if provider == settings.llm_provider else DEFAULT_MODELS.get(provider)
    choices = list(labels)
    chosen = st.selectbox(
        "Modelo",
        choices,
        index=choices.index(preferred) if preferred in choices else 0,
        format_func=labels.get,
        help="Só aparecem modelos com tool calling, que o agente exige.",
    )
    model = st.text_input("Nome do modelo", value=preferred or "") if chosen == OTHER_MODEL else chosen

    retries = st.number_input(
        "Retentativas do SDK",
        min_value=0,
        max_value=3,
        value=0 if provider is LLMProvider.OPENROUTER else settings.llm_max_retries,
        help="Use 0 no OpenRouter gratuito: cada retentativa consome a cota.",
    )
    credential = CREDENTIALS.get(provider)
    if credential is None:
        st.caption("Execução local, sem chave.")
    elif getattr(settings, credential.settings_field):
        st.caption(f"Chave `{credential.env_var}` configurada.")
    else:
        st.warning(f"Defina `{credential.env_var}` no `.env`.")
    st.caption("Cada relatório usa até 7 requisições ao LLM.")
    return settings.model_copy(
        update={"llm_provider": provider, "llm_model": model or None, "llm_max_retries": retries}
    )


# ---------------------------------------------------------------- geração
def generate(run_settings: Settings) -> None:
    with st.status("Gerando o relatório…", expanded=True) as status:
        try:
            result = run_report(run_settings, progress=lambda node: status.write(NODE_LABELS.get(node, node)))
        except (PreflightError, LLMConfigurationError, DatabaseError) as error:
            status.update(label="Execução bloqueada", state="error")
            st.error(str(error))
            return
        except Exception as error:  # detalhes completos na página de auditoria
            status.update(label="Falha na execução", state="error")
            st.error(f"Não foi possível gerar o relatório: {explain_error(error)}")
            return
        origin = "análise por IA" if result.generated_by_llm else "texto de reserva, sem IA"
        status.update(label=f"Relatório pronto ({origin})", state="complete", expanded=False)
    st.session_state["selected_run"] = result.run_id


# ---------------------------------------------------------------- painéis
def bar_chart(frame: pd.DataFrame) -> None:
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("Período:N", sort=alt.SortField("ordem"), title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("Casos:Q", title=None),
            color=alt.Color(
                "Situação:N",
                scale=alt.Scale(domain=["Consolidado", "Incompleto"], range=SERIES_COLORS),
                legend=alt.Legend(orient="top", title=None),
            ),
            tooltip=["Período", "Casos", "Situação"],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")


def header(report: SavedReport, data: dict) -> None:
    context, analysis = data["context"], data["analysis"]
    badges = st.container(horizontal=True)
    with badges:
        if analysis["generated_by_llm"]:
            st.badge(
                f"Análise por IA · guardrails aprovaram na {analysis['attempts']}ª tentativa",
                icon=":material/verified:",
                color="green",
            )
        else:
            st.badge(
                f"Texto de reserva, sem IA · {analysis['fallback_reason']}",
                icon=":material/warning:",
                color="orange",
            )
        st.badge(f"{context['provider']}/{context['model']}", icon=":material/smart_toy:", color="gray")
        st.badge(f"Execução {report.run_id}", icon=":material/fingerprint:", color="gray")
    for warning in context["warnings"]:
        st.warning(warning)

    cards = metric_cards(data["metrics"])
    for row in (cards[:3], cards[3:]):
        for column, card in zip(st.columns(3), row, strict=True):
            column.metric(card.label, card.value, help=card.help, border=True)
            column.caption(card.detail)


def charts_tab(charts: dict) -> None:
    daily = chart_frame(charts["casos_diarios"], charts["primeiro_dia_incompleto"], monthly=False)
    monthly = chart_frame(charts["casos_mensais"], charts["mes_incompleto"], monthly=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### Casos por dia (últimos 30 dias)")
        bar_chart(daily)
    with right:
        st.markdown("##### Casos por mês (últimos 12 meses)")
        bar_chart(monthly)
    st.caption(
        f"Data de início dos sintomas. A partir de {br_date(charts['primeiro_dia_incompleto'])} "
        "os dados ainda estão sendo notificados: a queda no fim das barras não é real."
    )
    with st.expander("Ver os números"):
        tables = st.columns(2)
        tables[0].dataframe(daily[["Período", "Casos", "Situação"]], hide_index=True, width="stretch")
        tables[1].dataframe(monthly[["Período", "Casos", "Situação"]], hide_index=True, width="stretch")


def analysis_tab(data: dict) -> None:
    analysis = data["analysis"]
    if analysis["generated_by_llm"]:
        st.caption(
            "Escrita pelo LLM e conferida pelos guardrails: todo número existe nos dados, a tendência bate "
            "com o sinal e não há previsões nem conselhos médicos. Revisão humana recomendada."
        )
    else:
        st.caption(f"Texto gerado por regras, sem IA ({analysis['fallback_reason']}).")
    with st.container(border=True):
        st.markdown(analysis["text"])


def sql_tab(queries: list[dict]) -> None:
    if not queries:
        st.info("O agente não precisou investigar com SQL nesta execução.")
        return
    st.caption(
        "Consultas escritas pelo agente, validadas pelos guardrails de SQL (só leitura, só agregados, "
        "grupos com menos de 5 casos omitidos)."
    )
    for index, query in enumerate(queries, start=1):
        with st.expander(f"Consulta {index}: {len(query['rows'])} linhas", expanded=index == 1):
            st.code(query["query"].strip(), language="sql")
            st.dataframe(
                pd.DataFrame(query["rows"], columns=query["columns"]), hide_index=True, width="stretch"
            )
            if query["suppressed_groups"]:
                st.caption(f"{query['suppressed_groups']} grupo(s) omitido(s) por terem menos de 5 casos.")


def news_tab(news: dict | None) -> None:
    if not news or not news["items"]:
        st.info("Nenhuma notícia disponível nesta execução.")
        return
    for item in news["items"]:
        st.markdown(
            f"**[{item['title']}]({item['link']})**  \n{item['source']} · {br_date(item['published'])}"
        )
    st.caption(
        f"Filtradas antes de chegar ao modelo: {news['dropped_suspicious']} suspeitas de injeção de prompt, "
        f"{news['dropped_irrelevant']} irrelevantes e {news['dropped_old']} antigas descartadas."
        + (
            f" Fontes indisponíveis: {', '.join(f['feed'] for f in news['failed_feeds'])}."
            if news["failed_feeds"]
            else ""
        )
    )


def method_tab(data: dict) -> None:
    st.markdown(
        "- **Data do caso:** início dos sintomas. As janelas terminam 14 dias antes da data dos dados, "
        "porque os casos recentes ainda estão sendo notificados.\n"
        "- **Internação em UTI:** mede a gravidade dos internados, **não** a ocupação de leitos.\n"
        "- **Vacinação:** medida entre os casos de SRAG, **não** na população.\n"
        "- **Mortalidade:** provisória; casos sem desfecho não entram no denominador.\n"
        "- **Dados sensíveis:** só 10 das 194 colunas da fonte são lidas; a idade vira faixa etária.\n"
        "- **Números:** calculados pelo código; o LLM só escreve a análise, conferida pelos guardrails."
    )
    st.markdown("**Arquivos de dados usados**")
    for source in data["context"]["sources"]:
        st.markdown(f"- `{source['filename']}` · sha256 `{source['sha256'][:16]}…`")


def downloads(report: SavedReport) -> None:
    name = f"relatorio_{report.reference_date}_{report.run_id}"
    buttons = st.container(horizontal=True)
    pdf = report.pdf()
    if pdf:
        buttons.download_button(
            "Baixar PDF",
            data=pdf,
            file_name=f"{name}.pdf",
            mime="application/pdf",
            icon=":material/picture_as_pdf:",
            type="primary",
        )
    buttons.download_button(
        "Baixar Markdown",
        data=report.path.read_text(encoding="utf-8"),
        file_name=f"{name}.md",
        icon=":material/file_download:",
    )


def markdown_report(report: SavedReport) -> None:
    for segment in split_report(report.path.read_text(encoding="utf-8")):
        if segment.kind == "image":
            image = report.path.parent / segment.content
            if image.is_file():
                st.image(str(image), caption=segment.title)
        elif segment.kind == "expander":
            with st.expander(segment.title):
                st.markdown(segment.content)
        else:
            st.markdown(segment.content)


def show_report(report: SavedReport) -> None:
    data = report.data()
    downloads(report)
    if data is None:
        st.info("Relatório gerado antes dos painéis: exibido no formato completo.")
        markdown_report(report)
        return
    header(report, data)
    news_count = len(data["news"]["items"]) if data["news"] else 0
    tabs = st.tabs(
        [
            "📈 Gráficos",
            "🧠 Análise",
            f"🔎 Investigações SQL ({len(data['sql'])})",
            f"📰 Notícias ({news_count})",
            "📘 Como ler",
            "📄 Relatório completo",
        ]
    )
    with tabs[0]:
        charts_tab(data["charts"])
    with tabs[1]:
        analysis_tab(data)
    with tabs[2]:
        sql_tab(data["sql"])
    with tabs[3]:
        news_tab(data["news"])
    with tabs[4]:
        method_tab(data)
    with tabs[5]:
        markdown_report(report)


# ---------------------------------------------------------------- página
with st.sidebar:
    st.markdown("## 🫁 SRAG · Agente")
    st.caption(
        "Relatórios sobre Síndrome Respiratória Aguda Grave com dados do Open DATASUS e notícias recentes."
    )
    ready = sidebar_data()
    run_settings = sidebar_model()

st.title("Relatório de SRAG")
st.caption("Os números vêm do código; o LLM escreve só a análise, verificada por guardrails.")

if st.button("Gerar novo relatório", type="primary", disabled=not ready, icon=":material/play_arrow:"):
    generate(run_settings)

reports = list_reports(settings.reports_dir)
if not reports:
    st.info("Nenhum relatório gerado ainda. Use o botão acima.")
    st.stop()

run_ids = [report.run_id for report in reports]
selected = st.session_state.get("selected_run")
chosen = st.selectbox(
    "Relatório",
    reports,
    index=run_ids.index(selected) if selected in run_ids else 0,
    format_func=lambda report: report.label,
)
st.divider()
show_report(chosen)
