"""Relatório em PDF, montado a partir dos dados estruturados da execução (`relatorio.json`).

Usa as fontes DejaVu que já vêm com o matplotlib: cobrem acentos e símbolos sem baixar nada.
"""

from pathlib import Path

import matplotlib
from fpdf import FPDF

from srag_agent.formatting import br_date, integer, metric_cards
from srag_agent.tools.charts import DAILY_CHART_FILE, MONTHLY_CHART_FILE
from srag_agent.tools.sql import MIN_GROUP_SIZE

PDF_FILE = "relatorio.pdf"
MAX_SQL_ROWS = 30
CHART_ASPECT = 4.2 / 10  # altura / largura dos PNGs de `tools.charts`
_FONTS = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_INK = (11, 11, 11)
_MUTED = (90, 90, 90)
_ACCENT = (42, 120, 214)
_WARNING = (180, 95, 0)

METHODOLOGY = [
    "Data do caso: início dos sintomas. As janelas terminam 14 dias antes da data dos dados, porque os casos "
    "recentes ainda estão sendo notificados.",
    "Internação em UTI: mede a gravidade dos internados, não a ocupação de leitos (a base não tem leitos).",
    "Vacinação: medida entre os casos de SRAG, não na população (a base não tem a população).",
    "Mortalidade: provisória; casos sem desfecho não entram no denominador.",
    "Dados sensíveis: só 10 das 194 colunas da fonte são lidas; a idade exata vira faixa etária.",
    "Números calculados pelo código; o LLM só escreve a análise, conferida por guardrails automáticos. "
    "Revisão humana recomendada.",
]


class _ReportPDF(FPDF):
    def __init__(self, run_id: str) -> None:
        super().__init__(format="A4")
        self.run_id = run_id
        self.add_font("DejaVu", "", _FONTS / "DejaVuSans.ttf")
        self.add_font("DejaVu", "B", _FONTS / "DejaVuSans-Bold.ttf")
        self.add_font("DejaVuMono", "", _FONTS / "DejaVuSansMono.ttf")
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(16, 16, 16)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DejaVu", size=8)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, f"SRAG · execução {self.run_id} · página {self.page_no()}/{{nb}}", align="C")

    def heading(self, text: str) -> None:
        self.ln(3)
        self.set_font("DejaVu", "B", 13)
        self.set_text_color(*_ACCENT)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*_INK)

    def paragraph(self, text: str, size: int = 10, color: tuple = _INK) -> None:
        self.set_font("DejaVu", size=size)
        self.set_text_color(*color)
        self.multi_cell(0, 5, text, new_x="LMARGIN", new_y="NEXT", markdown=True)
        self.set_text_color(*_INK)


def render_pdf(data: dict, folder: Path) -> bytes:
    """PDF do relatório; `folder` é a pasta da execução, com os gráficos."""
    context, analysis = data["context"], data["analysis"]
    pdf = _ReportPDF(context["run_id"])
    pdf.add_page()
    _cover(pdf, data)
    _metrics(pdf, data["metrics"])
    _charts(pdf, folder)
    pdf.heading("Análise do cenário")
    origin = (
        f"Texto gerado por IA ({context['provider']}/{context['model']}) e aprovado pelos guardrails na "
        f"tentativa {analysis['attempts']}."
        if analysis["generated_by_llm"]
        else f"Texto gerado por regras, sem IA ({analysis['fallback_reason']})."
    )
    pdf.paragraph(origin, size=8, color=_MUTED)
    pdf.ln(1)
    pdf.paragraph(analysis["text"])
    _sql(pdf, data["sql"])
    _news(pdf, data["news"])
    pdf.heading("Metodologia e limitações")
    for item in METHODOLOGY:
        pdf.paragraph(f"• {item}", size=9)
    pdf.heading("Rastreabilidade")
    pdf.paragraph(
        f"Execução {context['run_id']}: todas as decisões do agente estão em logs/audit.jsonl.", size=9
    )
    for source in context["sources"]:
        pdf.paragraph(f"• {source['filename']} (sha256 {source['sha256'][:16]}…)", size=9)
    return bytes(pdf.output())


def _cover(pdf: _ReportPDF, data: dict) -> None:
    context, metrics = data["context"], data["metrics"]
    pdf.set_font("DejaVu", "B", 18)
    pdf.cell(0, 10, "Relatório de SRAG", new_x="LMARGIN", new_y="NEXT")
    pdf.paragraph(
        f"Dados até {br_date(metrics['reference_date'])} (consolidados até {br_date(metrics['data_end'])}) · "
        f"gerado em {br_date(context['generated_at'][:10])} · fonte: Open DATASUS (SIVEP-Gripe)",
        size=9,
        color=_MUTED,
    )
    for warning in context["warnings"]:
        pdf.paragraph(f"Atenção: {warning}", size=9, color=_WARNING)


def _metrics(pdf: _ReportPDF, metrics: dict) -> None:
    pdf.heading("Métricas")
    pdf.set_font("DejaVu", size=9)
    with pdf.table(col_widths=(52, 22, 104), first_row_as_headings=True, line_height=5.5) as table:
        table.row(["Métrica", "Valor", "Base e definição"])
        for card in metric_cards(metrics):
            table.row([card.label, card.value, f"{card.detail}. {card.help}"])


def _charts(pdf: _ReportPDF, folder: Path) -> None:
    for title, filename in (
        ("Casos diários (últimos 30 dias)", DAILY_CHART_FILE),
        ("Casos mensais (últimos 12 meses)", MONTHLY_CHART_FILE),
    ):
        image = folder / filename
        if image.is_file():
            # Título e gráfico juntos: se não couberem no resto da página, vão para a próxima.
            if pdf.get_y() + pdf.epw * CHART_ASPECT + 14 > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.heading(title)
            pdf.image(str(image), w=pdf.epw)
    pdf.paragraph("Barras claras e hachuradas: período ainda incompleto (atraso de notificação).", 8, _MUTED)


def _sql(pdf: _ReportPDF, queries: list[dict]) -> None:
    if not queries:
        return
    pdf.heading("Investigações do agente (SQL)")
    for query in queries:
        pdf.set_font("DejaVuMono", size=8)
        pdf.set_fill_color(242, 242, 240)
        pdf.multi_cell(0, 4.5, query["query"].strip(), fill=True, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.set_font("DejaVu", size=8)
        with pdf.table(first_row_as_headings=True, line_height=4.5) as table:
            table.row([str(column) for column in query["columns"]])
            for row in query["rows"][:MAX_SQL_ROWS]:
                table.row([_cell(value) for value in row])
        notes = []
        if len(query["rows"]) > MAX_SQL_ROWS:
            notes.append(f"{len(query['rows']) - MAX_SQL_ROWS} linhas não exibidas")
        if query["suppressed_groups"]:
            notes.append(
                f"{query['suppressed_groups']} grupo(s) com menos de {MIN_GROUP_SIZE} casos omitido(s)"
            )
        if notes:
            pdf.paragraph("; ".join(notes) + ".", 8, _MUTED)
        pdf.ln(2)


def _news(pdf: _ReportPDF, news: dict | None) -> None:
    pdf.heading("Notícias consultadas")
    if not news or not news["items"]:
        pdf.paragraph("Nenhuma notícia disponível nesta execução.", size=9)
        return
    for item in news["items"]:
        pdf.set_font("DejaVu", size=9)
        pdf.set_text_color(*_ACCENT)
        pdf.multi_cell(0, 5, f"• {item['title']}", link=item["link"], new_x="LMARGIN", new_y="NEXT")
        pdf.paragraph(f"   {item['source']} · {br_date(item['published'])}", size=8, color=_MUTED)
    pdf.paragraph(
        "As manchetes são tratadas como dado não confiável e filtradas antes de chegar ao modelo.", 8, _MUTED
    )


def _cell(value: object) -> str:
    if value is None:
        return "não informado"
    if isinstance(value, int) and not isinstance(value, bool):
        return integer(value)
    if isinstance(value, float):
        return f"{value:.1f}".replace(".", ",")
    return str(value)
