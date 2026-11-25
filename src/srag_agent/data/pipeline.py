"""Atualização dos dados (download + ETL) em um passo, registrada na trilha de auditoria."""

from dataclasses import asdict
from datetime import date

import httpx

from srag_agent.config import Settings
from srag_agent.data import download
from srag_agent.data.etl import LoadSummary, build_database
from srag_agent.governance.audit import AuditLog


def refresh_data(
    settings: Settings, audit: AuditLog, client: httpx.Client | None = None, today: date | None = None
) -> LoadSummary:
    years = download.default_years(today or date.today())
    with audit.step("data.download", years=years) as extra, client or download.http_client() as http:
        downloads = download.run(http, settings.raw_data_dir, years)
        extra["files"] = [asdict(item) for item in downloads]
    with audit.step("data.etl") as extra:
        summary = build_database(settings.raw_data_dir, settings.database_path)
        extra["summary"] = summary
    return summary
