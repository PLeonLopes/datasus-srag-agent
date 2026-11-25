"""Tools do agente ligadas a uma execução: guardam as evidências e registram cada chamada na auditoria."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from srag_agent.agent.prompts import TOOL_DESCRIPTIONS
from srag_agent.governance.audit import AuditLog
from srag_agent.tools.charts import ChartsResult, generate_charts
from srag_agent.tools.metrics import MetricsReport, collect_metrics
from srag_agent.tools.news import NewsProvider, NewsResult
from srag_agent.tools.sql import SqlExecutionError, SqlToolError, describe_schema, run_query

MAX_SQL_QUERIES = 3
REQUIRED_TOOLS = ("obter_metricas", "gerar_graficos", "buscar_noticias")


@dataclass
class Evidence:
    metrics: MetricsReport | None = None
    charts: ChartsResult | None = None
    news: NewsResult | None = None
    sql: list[dict] = field(default_factory=list)

    def for_guardrail(self) -> dict:
        """Tudo o que foi entregue ao redator: a fonte dos números permitidos no texto."""
        return {
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "charts": _charts_payload(self.charts) if self.charts else None,
            "sql": self.sql,
            # Sem os links: números dentro de URLs não podem virar números "permitidos" no texto.
            "news": [
                {"title": item.title, "summary": item.summary, "source": item.source}
                for item in (self.news.items if self.news else [])
            ],
        }


class RunToolkit:
    def __init__(
        self,
        connection: sqlite3.Connection,
        out_dir: Path,
        news_provider: NewsProvider,
        http_client: httpx.Client,
        audit: AuditLog,
        now: datetime,
    ) -> None:
        self.connection = connection
        self.out_dir = out_dir
        self.news_provider = news_provider
        self.http_client = http_client
        self.audit = audit
        self.now = now
        self.evidence = Evidence()

    def tools(self) -> list[BaseTool]:
        """Definições para `bind_tools`; a execução passa por `run` para ser auditada."""
        return [
            StructuredTool.from_function(
                lambda: self.run("obter_metricas", {}),
                name="obter_metricas",
                description=TOOL_DESCRIPTIONS["obter_metricas"],
            ),
            StructuredTool.from_function(
                lambda: self.run("gerar_graficos", {}),
                name="gerar_graficos",
                description=TOOL_DESCRIPTIONS["gerar_graficos"],
            ),
            StructuredTool.from_function(
                lambda: self.run("buscar_noticias", {}),
                name="buscar_noticias",
                description=TOOL_DESCRIPTIONS["buscar_noticias"],
            ),
            StructuredTool.from_function(
                _sql_signature(self),
                name="consultar_sql",
                description=TOOL_DESCRIPTIONS["consultar_sql"].format(schema=describe_schema()),
            ),
        ]

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Executa a tool e devolve (conteúdo para o modelo, sucesso)."""
        handlers = {
            "obter_metricas": self._metrics,
            "gerar_graficos": self._charts,
            "buscar_noticias": self._news,
            "consultar_sql": lambda: self._sql(str(args.get("consulta", ""))),
        }
        handler = handlers.get(name)
        if handler is None:
            self.audit.record(f"tool.{name}", "blocked", args=args, reason="tool desconhecida")
            return f"ERRO: a ferramenta '{name}' não existe.", False
        return handler()

    def missing_required(self) -> list[str]:
        present = {
            "obter_metricas": self.evidence.metrics,
            "gerar_graficos": self.evidence.charts,
            "buscar_noticias": self.evidence.news,
        }
        return [name for name in REQUIRED_TOOLS if present[name] is None]

    def _metrics(self) -> tuple[str, bool]:
        if self.evidence.metrics is None:
            self.evidence.metrics = collect_metrics(self.connection)
            self.audit.record("tool.obter_metricas", result=self.evidence.metrics.to_dict())
        return _json(self.evidence.metrics.to_dict()), True

    def _charts(self) -> tuple[str, bool]:
        if self.evidence.charts is None:
            self.evidence.charts = generate_charts(self.connection, self.out_dir)
            self.audit.record("tool.gerar_graficos", result=_charts_payload(self.evidence.charts))
        return _json(_charts_payload(self.evidence.charts)), True

    def _news(self) -> tuple[str, bool]:
        if self.evidence.news is None:
            self.evidence.news = self.news_provider.fetch(self.http_client, self.now)
            status = "degraded" if self.evidence.news.failed_feeds else "ok"
            self.audit.record("tool.buscar_noticias", status, result=self.evidence.news.to_dict())
        return self.evidence.news.format_for_prompt(), True

    def _sql(self, query: str) -> tuple[str, bool]:
        if len(self.evidence.sql) >= MAX_SQL_QUERIES:
            self.audit.record("tool.consultar_sql", "blocked", query=query, reason="limite de consultas")
            return f"ERRO: limite de {MAX_SQL_QUERIES} consultas por execução atingido.", False
        try:
            result = run_query(self.connection, query)
        except SqlToolError as error:
            status = "error" if isinstance(error, SqlExecutionError) else "blocked"
            self.audit.record("tool.consultar_sql", status, query=query, error=str(error))
            return f"ERRO: {error}", False
        payload = {"query": query, **result.to_dict()}
        self.evidence.sql.append(payload)
        self.audit.record("tool.consultar_sql", result=payload)
        return _json(payload), True


def _sql_signature(toolkit: RunToolkit):
    def consultar_sql(consulta: str) -> tuple[str, bool]:
        """Consulta SQL agregada sobre a tabela `cases`."""
        return toolkit.run("consultar_sql", {"consulta": consulta})

    return consultar_sql


def _charts_payload(charts: ChartsResult) -> dict:
    return {
        "casos_diarios": charts.daily_cases,
        "casos_mensais": charts.monthly_cases,
        "primeiro_dia_incompleto": charts.first_incomplete_day,
        "mes_incompleto": charts.incomplete_month,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
