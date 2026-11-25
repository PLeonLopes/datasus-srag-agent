"""EDA das colunas da fonte, publicando só agregados.

Todas as colunas: tipo e % preenchido. Distribuição de códigos só para as categóricas da allowlist,
para que nenhum valor de coluna sensível apareça no documento.
Uso: `uv run python -m srag_agent.data.profile [--raw-dir data/raw] [--output docs/eda_colunas.md]`
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from srag_agent.config import get_settings
from srag_agent.data.etl import select_source_files
from srag_agent.data.schema import CATEGORICAL_COLUMNS, SOURCE_COLUMNS, STATE_COLUMN


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    filled_pct: float
    in_allowlist: bool


def profile_columns(paths: Sequence[Path]) -> tuple[int, list[ColumnProfile]]:
    """Total de linhas e o perfil de cada coluna, lendo uma coluna por vez."""
    schema = pq.read_schema(paths[0])
    total_rows = sum(pq.ParquetFile(path).metadata.num_rows for path in paths)
    profiles = []
    for name, dtype in zip(schema.names, schema.types, strict=True):
        filled = sum(_filled_count(_read_column(path, name)) for path in paths)
        profiles.append(ColumnProfile(name, str(dtype), 100 * filled / total_rows, name in SOURCE_COLUMNS))
    return total_rows, profiles


def code_distributions(paths: Sequence[Path]) -> dict[str, dict[str, float]]:
    """% de cada código nas colunas categóricas da allowlist (nulos como `vazio`)."""
    columns = [*CATEGORICAL_COLUMNS, STATE_COLUMN[0]]
    distributions = {}
    for column in columns:
        values = pd.concat([_read_column(path, column) for path in paths], ignore_index=True)
        codes = values.astype("string").str.strip().replace("", pd.NA).fillna("vazio")
        distributions[column] = (codes.value_counts(normalize=True) * 100).round(1).to_dict()
    return distributions


def render_markdown(
    total_rows: int,
    profiles: Sequence[ColumnProfile],
    distributions: dict[str, dict[str, float]],
    files: Sequence[str],
    generated_on: date,
) -> str:
    lines = [
        "# EDA das colunas do SRAG",
        "",
        f"Gerado em {generated_on:%d/%m/%Y} por `srag_agent.data.profile` a partir de: "
        + ", ".join(f"`{name}`" for name in files)
        + f". Total: {total_rows:,} linhas e {len(profiles)} colunas.".replace(",", "."),
        "",
        "Só agregados: nenhum valor individual é publicado, e a distribuição de códigos aparece apenas",
        "para colunas categóricas da allowlist.",
        "",
        "## Colunas da allowlist: distribuição dos códigos (%)",
        "",
    ]
    for column, distribution in distributions.items():
        values = ", ".join(f"`{code}`: {pct}" for code, pct in distribution.items())
        lines.append(f"- **{column}**: {values}")

    lines += [
        "",
        "## Todas as colunas",
        "",
        "| Coluna | Tipo | Preenchido (%) | Allowlist |",
        "|---|---|---|---|",
    ]
    for profile in profiles:
        mark = "sim" if profile.in_allowlist else ""
        lines.append(f"| `{profile.name}` | {profile.dtype} | {profile.filled_pct:.1f} | {mark} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gera a EDA das colunas (só agregados).")
    parser.add_argument("--raw-dir", type=Path, default=get_settings().raw_data_dir)
    parser.add_argument("--output", type=Path, default=Path("docs/eda_colunas.md"))
    args = parser.parse_args(argv)

    paths = [source.path for source in select_source_files(args.raw_dir)]
    total_rows, profiles = profile_columns(paths)
    markdown = render_markdown(
        total_rows, profiles, code_distributions(paths), [path.name for path in paths], date.today()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"EDA gravada em {args.output}")


def _read_column(path: Path, column: str) -> pd.Series:
    return pq.read_table(path, columns=[column]).column(0).to_pandas()


def _filled_count(values: pd.Series) -> int:
    filled = values.notna()
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        filled &= values.astype("string").str.strip().ne("").fillna(False)
    return int(filled.sum())


if __name__ == "__main__":
    main()
