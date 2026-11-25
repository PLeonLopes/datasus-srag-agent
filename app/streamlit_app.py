"""Interface do agente de SRAG. Uso: `uv run streamlit run app/streamlit_app.py` (na raiz do repositório)."""

import streamlit as st

st.set_page_config(
    page_title="SRAG · Agente de relatórios", page_icon=":material/monitor_heart:", layout="wide"
)

navigation = st.navigation(
    [
        st.Page("views/relatorio.py", title="Relatório", icon=":material/description:", default=True),
        st.Page("views/auditoria.py", title="Auditoria", icon=":material/fact_check:"),
    ]
)
navigation.run()
