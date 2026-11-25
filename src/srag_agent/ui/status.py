"""Estado dos dados para a interface (início e barra lateral)."""

from contextlib import closing
from dataclasses import dataclass, field
from datetime import date

from srag_agent.config import Settings
from srag_agent.data.database import DatabaseError, connect_readonly, read_load_metadata
from srag_agent.governance.guardrails import check_database


@dataclass(frozen=True)
class DataStatus:
    reference_date: date | None
    rows_loaded: int
    passed: bool
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def data_status(settings: Settings, today: date) -> DataStatus | None:
    """None quando os dados ainda não foram baixados."""
    try:
        with closing(connect_readonly(settings.database_path)) as connection:
            check = check_database(connection, today)
            rows = read_load_metadata(connection)["rows_loaded"]
    except DatabaseError:
        return None
    return DataStatus(check.reference_date, rows, check.passed, check.blocked_reasons, check.warnings)
