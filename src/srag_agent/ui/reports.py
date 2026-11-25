"""Relatórios salvos e divisão do Markdown em blocos que o Streamlit renderiza sem HTML.

O relatório contém texto do LLM e manchetes externas, então nunca é renderizado com `unsafe_allow_html`:
`<details>` vira um expander e `![...](arquivo.png)` vira `st.image`.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath

import pandas as pd

from srag_agent.agent.pdf import PDF_FILE, render_pdf
from srag_agent.agent.report import DATA_FILE, REPORT_FILE
from srag_agent.formatting import MetricCard, br_date, metric_cards  # noqa: F401 (reexportados)
from srag_agent.llm.errors import status_code

NODE_LABELS = {
    "orchestrator": "Orquestrador (LLM) decidindo os próximos passos",
    "tools": "Executando ferramentas",
    "completeness": "Conferindo as ferramentas obrigatórias",
    "writer": "Redator (LLM) escrevendo a análise",
    "guardrails": "Verificando o texto com os guardrails",
    "assemble": "Montando o relatório",
}

_RUN_FOLDER = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<run_id>[0-9a-f]+)$")
_IMAGE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)\s*$")
_DETAILS = re.compile(r"<details><summary>(?P<title>.*?)</summary>(?P<body>.*?)</details>", re.DOTALL)


@dataclass(frozen=True)
class SavedReport:
    run_id: str
    reference_date: str
    path: Path
    created_at: datetime

    @property
    def label(self) -> str:
        return f"{self.created_at:%d/%m/%Y %H:%M} · dados até {br_date(self.reference_date)} · {self.run_id}"

    def data(self) -> dict | None:
        """Dados estruturados da execução; relatórios antigos não têm e são exibidos só como Markdown."""
        path = self.path.parent / DATA_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def pdf(self) -> bytes | None:
        """PDF gravado na execução ou, se faltar, gerado agora a partir dos dados estruturados."""
        path = self.path.parent / PDF_FILE
        if path.is_file():
            return path.read_bytes()
        data = self.data()
        return render_pdf(data, self.path.parent) if data else None


def chart_frame(series: dict[str, int], incomplete_from: str, monthly: bool) -> pd.DataFrame:
    """Tabela para o gráfico: período, casos e se o período ainda está incompleto."""
    rows = []
    for period, cases in series.items():
        incomplete = period >= incomplete_from
        label = f"{period[5:]}/{period[:4]}" if monthly else br_date(period)
        rows.append(
            {
                "Período": label,
                "ordem": period,
                "Casos": cases,
                "Situação": "Incompleto" if incomplete else "Consolidado",
            }
        )
    return pd.DataFrame(rows)


def explain_error(error: Exception) -> str:
    """Motivo de uma falha em português, para o aviso na tela."""
    status = status_code(error)
    if status in (401, 403):
        return "a chave do provedor foi recusada; confira o `.env`"
    if status == 404:
        return "o modelo não existe nesse provedor; escolha outro na lista"
    if status in (402, 429):
        return "cota do provedor esgotada ou modelo gratuito sobrecarregado; tente outro modelo ou mais tarde"
    return f"{type(error).__name__}: {str(error)[:200]}"


@dataclass(frozen=True)
class Segment:
    kind: str  # "markdown" | "image" | "expander"
    content: str
    title: str = ""


def list_reports(reports_dir: Path) -> list[SavedReport]:
    """Relatórios salvos, do mais recente para o mais antigo."""
    if not reports_dir.exists():
        return []
    reports = []
    for folder in reports_dir.iterdir():
        match = _RUN_FOLDER.match(folder.name)
        report = folder / REPORT_FILE
        if match and report.is_file():
            created = datetime.fromtimestamp(report.stat().st_mtime)
            reports.append(SavedReport(match["run_id"], match["date"], report, created))
    return sorted(reports, key=lambda report: report.created_at, reverse=True)


def split_report(markdown: str) -> list[Segment]:
    segments: list[Segment] = []
    position = 0
    for match in _DETAILS.finditer(markdown):
        segments += _split_images(markdown[position : match.start()])
        segments.append(Segment("expander", match["body"].strip(), match["title"].strip()))
        position = match.end()
    segments += _split_images(markdown[position:])
    return [segment for segment in segments if segment.content.strip()]


def _split_images(markdown: str) -> list[Segment]:
    segments: list[Segment] = []
    buffer: list[str] = []
    for line in markdown.splitlines():
        image = _IMAGE.match(line)
        # Só nomes de arquivo simples: nada de caminhos que saiam da pasta do relatório.
        if image and PurePath(image["src"]).name == image["src"]:
            segments.append(Segment("markdown", "\n".join(buffer)))
            segments.append(Segment("image", image["src"], image["alt"]))
            buffer = []
        else:
            buffer.append(line)
    segments.append(Segment("markdown", "\n".join(buffer)))
    return segments
