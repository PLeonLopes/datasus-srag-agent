"""Dados sintéticos no formato dos arquivos do Open DATASUS, para testes e modo demo.

Os arquivos levam uma marca nos metadados do Parquet, para nunca serem confundidos com dados reais.
Uso: `uv run python -m srag_agent.data.synthetic [--dest data/demo] [--cases 50000]`
"""

import argparse
import logging
import math
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

SYNTHETIC_METADATA_KEY = b"srag_agent.synthetic"
DECOY_COLUMN = "NU_NOTIFIC"  # fora da allowlist: o ETL nunca deve lê-la

# Proporções aproximadas dos dados reais (EDA de 2025-2026), com nulos como None.
_CODE_DISTRIBUTIONS: dict[str, dict[str | None, float]] = {
    "EVOLUCAO": {"1": 0.82, "2": 0.06, "3": 0.025, "9": 0.02, None: 0.075},
    "HOSPITAL": {"1": 0.96, "2": 0.02, "9": 0.005, None: 0.015},
    "UTI": {"1": 0.26, "2": 0.635, "9": 0.01, None: 0.095},
    "VACINA": {"1": 0.25, "2": 0.675, "9": 0.02, None: 0.055},
    "VACINA_COV": {"1": 0.52, "2": 0.47, "9": 0.01},
    "CLASSI_FIN": {"1": 0.13, "2": 0.33, "3": 0.015, "4": 0.44, "5": 0.04, None: 0.045},
    "CS_SEXO": {"M": 0.52, "F": 0.48},
}
_STATES = ["SP", "MG", "PR", "RJ", "RS", "BA", "SC", "GO", "PE", "CE", "DF", "PA", "AM", "ES", "MA"]


def generate_cases(start: date, end: date, n_cases: int, seed: int = 42) -> pd.DataFrame:
    """Casos com sazonalidade (pico no outono e inverno) e códigos nas proporções reais."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    seasonality = 1 + 0.8 * np.cos(2 * math.pi * (days.day_of_year.to_numpy() - 150) / 365.25)
    symptom_dates = days[rng.choice(len(days), size=n_cases, p=seasonality / seasonality.sum())]

    data: dict[str, object] = {"DT_SIN_PRI": symptom_dates}
    for column, distribution in _CODE_DISTRIBUTIONS.items():
        codes = list(distribution)
        weights = np.array(list(distribution.values()))
        data[column] = rng.choice(np.array(codes, dtype=object), size=n_cases, p=weights / weights.sum())
    data["SG_UF_NOT"] = rng.choice(_STATES, size=n_cases)

    age_unit = rng.choice(["1", "2", "3"], size=n_cases, p=[0.015, 0.255, 0.73])
    max_by_unit = {"1": 30, "2": 12, "3": 100}
    data["TP_IDADE"] = age_unit
    data["NU_IDADE_N"] = [int(rng.integers(0, max_by_unit[unit])) for unit in age_unit]
    data[DECOY_COLUMN] = [f"SINTETICO-{index:08d}" for index in range(n_cases)]

    return pd.DataFrame(data).sort_values("DT_SIN_PRI", ignore_index=True)


def write_snapshot(cases: pd.DataFrame, dest_dir: Path, data_year: int, snapshot_date: date) -> Path:
    """Grava os casos do ano com o nome no padrão do DATASUS e a marca de dado sintético."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    year_cases = cases[cases["DT_SIN_PRI"].dt.year == data_year]
    table = pa.Table.from_pandas(year_cases, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), SYNTHETIC_METADATA_KEY: b"true"})
    path = dest_dir / f"INFLUD{data_year % 100:02d}-{snapshot_date:%d-%m-%Y}.parquet"
    pq.write_table(table, path)
    return path


def generate_snapshots(dest_dir: Path, today: date, n_cases: int, seed: int = 42) -> list[Path]:
    """Um arquivo por ano (anterior e corrente), cobrindo de 1º de janeiro do ano anterior até hoje."""
    cases = generate_cases(date(today.year - 1, 1, 1), today, n_cases, seed)
    return [write_snapshot(cases, dest_dir, year, today) for year in (today.year - 1, today.year)]


def is_synthetic(path: Path) -> bool:
    metadata = pq.read_schema(path).metadata or {}
    return metadata.get(SYNTHETIC_METADATA_KEY) == b"true"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gera dados sintéticos de SRAG para testes e demonstração.")
    parser.add_argument("--dest", type=Path, default=Path("data/demo"))
    parser.add_argument("--cases", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for path in generate_snapshots(args.dest, date.today(), args.cases, args.seed):
        logger.info("Gerado: %s", path)


if __name__ == "__main__":
    main()
