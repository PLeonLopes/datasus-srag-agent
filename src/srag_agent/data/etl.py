"""Carga dos arquivos de SRAG (Parquet) no SQLite, lendo só as colunas da allowlist.

Uso: `uv run python -m srag_agent.data.etl [--raw-dir data/raw] [--db data/srag.db]`
"""

import argparse
import json
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from srag_agent.config import get_settings
from srag_agent.data.download import file_sha256, parse_snapshot_filename
from srag_agent.data.schema import (
    AGE_GROUP_BINS,
    AGE_GROUP_LABELS,
    AGE_UNIT_COLUMN,
    AGE_UNIT_IN_YEARS,
    AGE_VALUE_COLUMN,
    CASES_COLUMNS,
    CASES_TABLE,
    CATEGORICAL_COLUMNS,
    MAX_AGE_YEARS,
    METADATA_TABLE,
    SOURCE_COLUMNS,
    STATE_COLUMN,
    STATES,
    SYMPTOM_DATE_COLUMN,
)
from srag_agent.data.synthetic import is_synthetic

logger = logging.getLogger(__name__)

# Margem antes de 1º de janeiro: a semana epidemiológica 1 pode começar no fim de dezembro.
EPI_YEAR_MARGIN = timedelta(days=7)


class EtlError(RuntimeError):
    """Falha na seleção, validação ou carga dos dados."""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    data_year: int
    snapshot_date: date


@dataclass(frozen=True)
class LoadSummary:
    loaded_at: str
    synthetic: bool
    sources: list[dict]
    rows_read: int
    rows_loaded: int
    rows_dropped_invalid_date: int
    invalid_values: dict[str, int]
    min_symptom_date: str
    max_symptom_date: str


def select_source_files(raw_dir: Path) -> list[SourceFile]:
    """Snapshot mais recente de cada ano; snapshots antigos ficam no disco, mas não são lidos."""
    latest: dict[int, SourceFile] = {}
    for path in sorted(raw_dir.glob("*.parquet")):
        parsed = parse_snapshot_filename(path.name)
        if parsed is None:
            continue
        source = SourceFile(path, *parsed)
        current = latest.get(source.data_year)
        if current is None or source.snapshot_date > current.snapshot_date:
            latest[source.data_year] = source
    if not latest:
        raise EtlError(f"Nenhum arquivo INFLUD*.parquet em {raw_dir}")
    return [latest[year] for year in sorted(latest)]


def transform(raw: pd.DataFrame, min_date: date, max_date: date) -> tuple[pd.DataFrame, dict[str, int]]:
    """Converte códigos em rótulos, deriva a faixa etária e descarta datas fora do período.

    Retorna os casos e, por coluna da fonte, quantos valores preenchidos eram inválidos (viraram nulo).
    """
    symptom_date = pd.to_datetime(raw[SYMPTOM_DATE_COLUMN], errors="coerce").dt.normalize()
    valid_date = symptom_date.between(pd.Timestamp(min_date), pd.Timestamp(max_date))

    cases = pd.DataFrame({"symptom_date": symptom_date.dt.strftime("%Y-%m-%d")})
    invalid: dict[str, int] = {}

    for source, (target, mapping) in CATEGORICAL_COLUMNS.items():
        codes = _clean_codes(raw[source])
        cases[target] = codes.map(mapping)
        invalid[source] = int((codes.notna() & cases[target].isna()).sum())

    state_source, state_target = STATE_COLUMN
    states = _clean_codes(raw[state_source]).str.upper()
    cases[state_target] = states.where(states.isin(STATES))
    invalid[state_source] = int((states.notna() & cases[state_target].isna()).sum())

    cases["age_group"], invalid[AGE_VALUE_COLUMN] = _age_groups(raw[AGE_VALUE_COLUMN], raw[AGE_UNIT_COLUMN])

    cases = cases.loc[valid_date, CASES_COLUMNS].sort_values("symptom_date", ignore_index=True)
    return cases, invalid


def build_database(raw_dir: Path, db_path: Path) -> LoadSummary:
    sources = select_source_files(raw_dir)
    synthetic_flags = {is_synthetic(source.path) for source in sources}
    if len(synthetic_flags) > 1:
        raise EtlError("Arquivos reais e sintéticos misturados na mesma pasta")

    raw = pd.concat(
        [pd.read_parquet(source.path, columns=SOURCE_COLUMNS) for source in sources], ignore_index=True
    )
    min_date = date(min(source.data_year for source in sources), 1, 1) - EPI_YEAR_MARGIN
    max_date = max(source.snapshot_date for source in sources)
    cases, invalid = transform(raw, min_date, max_date)
    if cases.empty:
        raise EtlError("Nenhum caso com data de sintomas válida")

    summary = LoadSummary(
        loaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        synthetic=synthetic_flags.pop(),
        sources=[_describe_source(source) for source in sources],
        rows_read=len(raw),
        rows_loaded=len(cases),
        rows_dropped_invalid_date=len(raw) - len(cases),
        invalid_values=invalid,
        min_symptom_date=cases["symptom_date"].iloc[0],
        max_symptom_date=cases["symptom_date"].iloc[-1],
    )
    _write_database(cases, summary, db_path)
    return summary


def cases_table_ddl() -> str:
    """DDL com CHECK por coluna: o próprio banco recusa valores fora do contrato."""
    allowed: dict[str, list[str]] = {
        target: sorted(set(mapping.values())) for target, mapping in CATEGORICAL_COLUMNS.values()
    }
    allowed[STATE_COLUMN[1]] = sorted(STATES)
    allowed["age_group"] = AGE_GROUP_LABELS

    definitions = ["symptom_date TEXT NOT NULL CHECK (date(symptom_date) = symptom_date)"]
    for column in CASES_COLUMNS[1:]:
        values = ", ".join(f"'{value}'" for value in allowed[column])
        definitions.append(f"{column} TEXT CHECK ({column} IN ({values}))")
    return f"CREATE TABLE {CASES_TABLE} (\n    " + ",\n    ".join(definitions) + "\n)"


def main(argv: Sequence[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Carrega os dados de SRAG no SQLite.")
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_data_dir)
    parser.add_argument("--db", type=Path, default=settings.database_path)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = build_database(args.raw_dir, args.db)
    logger.info(
        "%s casos carregados em %s (%s descartados por data inválida)%s",
        summary.rows_loaded,
        args.db,
        summary.rows_dropped_invalid_date,
        " [DADOS SINTÉTICOS]" if summary.synthetic else "",
    )
    logger.info("Período: %s a %s", summary.min_symptom_date, summary.max_symptom_date)
    logger.info("Valores inválidos por coluna: %s", summary.invalid_values)


def _clean_codes(series: pd.Series) -> pd.Series:
    codes = series.astype("string").str.strip()
    return codes.mask(codes == "")


def _age_groups(values: pd.Series, units: pd.Series) -> tuple[pd.Series, int]:
    unit_in_years = _clean_codes(units).map(AGE_UNIT_IN_YEARS).astype(float)
    # float comum: com tipos nullable, a comparação com nulo daria <NA> e a contagem de inválidos falharia
    years = pd.to_numeric(values, errors="coerce").astype(float) * unit_in_years
    valid = years.between(0, MAX_AGE_YEARS)
    groups = pd.cut(years.where(valid), bins=AGE_GROUP_BINS, labels=AGE_GROUP_LABELS, right=False)
    invalid = int((pd.notna(values) & ~valid).sum())
    return groups.astype(object).where(groups.notna(), None), invalid


def _describe_source(source: SourceFile) -> dict:
    digest, size = file_sha256(source.path)
    return {
        "filename": source.path.name,
        "data_year": source.data_year,
        "snapshot_date": source.snapshot_date.isoformat(),
        "sha256": digest,
        "size_bytes": size,
    }


def _write_database(cases: pd.DataFrame, summary: LoadSummary, db_path: Path) -> None:
    """Escreve num arquivo temporário e troca no fim: um erro nunca deixa o banco pela metade."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = db_path.with_name(db_path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute(cases_table_ddl())
            cases.to_sql(CASES_TABLE, connection, if_exists="append", index=False, chunksize=50_000)
            connection.execute(f"CREATE INDEX idx_{CASES_TABLE}_symptom_date ON {CASES_TABLE} (symptom_date)")
            connection.execute(f"CREATE TABLE {METADATA_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany(
                f"INSERT INTO {METADATA_TABLE} (key, value) VALUES (?, ?)",  # noqa: S608
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in asdict(summary).items()],
            )
            connection.commit()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(db_path)


if __name__ == "__main__":
    main()
