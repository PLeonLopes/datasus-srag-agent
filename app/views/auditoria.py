"""Página da trilha de auditoria: integridade, execuções e decisões do agente, em painéis."""

import json

import streamlit as st

from srag_agent.config import get_settings
from srag_agent.formatting import integer
from srag_agent.governance.audit import read_events, verify_chain
from srag_agent.ui.audit_view import EVENT_LABELS, describe, summarize_runs, timeline

STATUS = {"ok": "✅ ok", "degraded": "⚠️ degradado", "blocked": "⛔ bloqueado", "error": "❌ erro"}

settings = get_settings()

st.title("Trilha de auditoria")
st.caption(
    f"Cada decisão do agente fica em `{settings.audit_log_path.as_posix()}` (JSON Lines, só acréscimo). "
    "Cada linha guarda o hash da anterior: editar ou apagar uma linha quebra a cadeia."
)

chain = verify_chain(settings.audit_log_path)
events = read_events(settings.audit_log_path)
runs = summarize_runs(events)
reports = [run for run in runs if run.kind == "relatório"]

kpis = st.columns(4)
kpis[0].metric(
    "Integridade",
    "Íntegra" if chain.valid else "Quebrada",
    border=True,
    help="Recalcula o hash de cada evento e confere o encadeamento e o arquivo .head.",
)
kpis[1].metric("Eventos verificados", integer(chain.events), border=True)
kpis[2].metric(
    "Relatórios com análise por IA",
    f"{sum(1 for r in reports if r.generated_by_llm)} de {len(reports)}",
    border=True,
    help="Os demais usaram o texto de reserva (LLM indisponível ou texto recusado).",
)
kpis[3].metric(
    "Tokens usados",
    integer(sum(run.total_tokens for run in runs)),
    border=True,
    help="Soma das chamadas registradas. Execuções antigas não têm a contagem.",
)
if not chain.valid:
    with st.expander("Onde a cadeia quebrou", expanded=True):
        for problem in chain.problems:
            st.markdown(f"- {problem}")

if not runs:
    st.info("Nenhuma execução registrada ainda.")
    st.stop()

st.subheader("Execuções")
st.caption("Clique numa linha para ver os detalhes.")
selection = st.dataframe(
    [
        {
            "Status": STATUS.get(run.status, run.status),
            "Início (UTC)": run.started_at[:16].replace("T", " "),
            "Tipo": run.kind,
            "Modelo": run.model or "-",
            "IA": {True: "sim", False: "não", None: "-"}[run.generated_by_llm],
            "LLM": run.llm_calls,
            "Tokens": run.total_tokens or None,
            "Duração (s)": run.duration_s,
            "Execução": run.run_id,
        }
        for run in runs
    ],
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="runs_table",
)
rows = selection.selection.rows if selection else []
run = runs[rows[0]] if rows else runs[0]
run_events = [event for event in events if event["run_id"] == run.run_id]

st.subheader(f"Execução `{run.run_id}`")
st.caption(
    f"{run.kind} · {STATUS.get(run.status, run.status)} · {run.model or 'sem LLM'} · {run.duration_s} s"
)

llm_calls = [event for event in run_events if event["event"] == "llm.call"]
tools = [event for event in run_events if event["event"].startswith("tool.")]
guardrails = [event for event in run_events if event["event"].startswith("guardrail.")]
tabs = st.tabs(
    [
        "🧭 Decisões",
        f"🤖 Chamadas ao LLM ({len(llm_calls)})",
        f"🛠️ Ferramentas ({len(tools)})",
        f"🛡️ Guardrails ({len(guardrails)})",
        "{ } Eventos brutos",
    ]
)

with tabs[0]:
    st.caption("O que aconteceu, em ordem. As etapas técnicas do grafo ficam nos eventos brutos.")
    st.dataframe(
        [
            {
                "Hora": row.time,
                "Status": STATUS.get(row.status, row.status),
                "Evento": row.event,
                "Detalhe": row.detail,
            }
            for row in timeline(run_events)
            if not row.event.startswith("step.") or run.kind != "relatório"
        ],
        hide_index=True,
        width="stretch",
        column_config={"Detalhe": st.column_config.TextColumn(width="large")},
    )

with tabs[1]:
    if not llm_calls:
        st.info("Nenhuma chamada ao LLM nesta execução.")
    for event in llm_calls:
        data = event["data"]
        tokens = (data.get("usage") or {}).get("total_tokens")
        title = f"{data['node']} #{data['attempt']} · {data['duration_ms']} ms"
        with st.expander(title + (f" · {integer(tokens)} tokens" if isinstance(tokens, int) else "")):
            prompt_tab, response_tab = st.tabs(["Prompt", "Resposta"])
            with prompt_tab:
                for message in data["prompt"]:
                    st.markdown(f"**{message['role']}**")
                    st.code(message["content"], language=None, wrap_lines=True, height=220)
            with response_tab:
                if data["tool_calls"]:
                    st.json(data["tool_calls"])
                if data["response"]:
                    st.markdown(data["response"])

with tabs[2]:
    if not tools:
        st.info("Nenhuma ferramenta chamada nesta execução.")
    for event in tools:
        with st.expander(
            f"{STATUS.get(event['status'], '')} · {EVENT_LABELS.get(event['event'], event['event'])}"
            f" · {describe(event)[:90]}"
        ):
            st.json(event["data"], expanded=1)

with tabs[3]:
    if not guardrails:
        st.info("Nenhum guardrail registrado nesta execução.")
    for event in guardrails:
        label = EVENT_LABELS.get(event["event"], event["event"])
        attempt = f" (tentativa {event['data']['attempt']})" if "attempt" in event["data"] else ""
        st.markdown(f"**{label}{attempt}:** {STATUS.get(event['status'], event['status'])}")
        problems = [
            *(event["data"].get("violations") or event["data"].get("blocked_reasons") or []),
            *(event["data"].get("warnings") or []),
        ]
        for problem in problems:
            st.markdown(f"- {problem}")

with tabs[4]:
    st.download_button(
        "Baixar eventos desta execução (.jsonl)",
        data="\n".join(json.dumps(event, ensure_ascii=False) for event in run_events) + "\n",
        file_name=f"auditoria_{run.run_id}.jsonl",
        icon=":material/file_download:",
    )
    st.json(run_events, expanded=False)
