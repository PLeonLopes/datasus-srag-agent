import sqlite3
from contextlib import closing
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from srag_agent.data.database import connect_readonly, read_load_metadata
from srag_agent.data.etl import EtlError, build_database, select_source_files, transform
from srag_agent.data.schema import CASES_COLUMNS, CASES_TABLE, SOURCE_COLUMNS
from srag_agent.data.synthetic import DECOY_COLUMN, generate_snapshots

TODAY = date(2026, 11, 23)
MIN_DATE = date(2024, 12, 25)


def _raw(**overrides) -> pd.DataFrame:
    """Uma linha válida no formato da fonte; `overrides` altera colunas específicas."""
    row = {
        "DT_SIN_PRI": pd.Timestamp("2026-05-10"),
        "EVOLUCAO": "2",
        "HOSPITAL": "1",
        "UTI": "1",
        "VACINA": "2",
        "VACINA_COV": "1",
        "CLASSI_FIN": "5",
        "CS_SEXO": "F",
        "SG_UF_NOT": "SP",
        "NU_IDADE_N": 67,
        "TP_IDADE": "3",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _write_plain_parquet(path, frame: pd.DataFrame) -> None:
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)


class TestTransform:
    def test_maps_codes_to_labels(self):
        cases, invalid = transform(_raw(), MIN_DATE, TODAY)

        assert cases.to_dict("records") == [
            {
                "symptom_date": "2026-05-10",
                "state": "SP",
                "age_group": "60-79",
                "outcome": "srag_death",
                "hospitalized": "yes",
                "icu": "yes",
                "flu_vaccine": "no",
                "covid_vaccine": "yes",
                "classification": "covid19",
                "sex": "female",
            }
        ]
        assert set(invalid.values()) == {0}

    def test_output_never_contains_exact_age(self):
        cases, _ = transform(_raw(), MIN_DATE, TODAY)

        assert list(cases.columns) == CASES_COLUMNS
        assert "NU_IDADE_N" not in cases.columns

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [(10, "1", "0-4"), (11, "2", "0-4"), (5, "3", "5-19"), (80, "3", "80+"), (39, "3", "20-39")],
    )
    def test_derives_age_group_from_value_and_unit(self, value, unit, expected):
        cases, _ = transform(_raw(NU_IDADE_N=value, TP_IDADE=unit), MIN_DATE, TODAY)

        assert cases.loc[0, "age_group"] == expected

    @pytest.mark.parametrize(("value", "unit"), [(150, "3"), (-1, "3"), (30, "7")])
    def test_invalid_age_becomes_null_and_is_counted(self, value, unit):
        cases, invalid = transform(_raw(NU_IDADE_N=value, TP_IDADE=unit), MIN_DATE, TODAY)

        assert cases.loc[0, "age_group"] is None
        assert invalid["NU_IDADE_N"] == 1

    def test_unknown_codes_become_null_and_are_counted(self):
        cases, invalid = transform(_raw(EVOLUCAO="7", SG_UF_NOT="XX", CS_SEXO=" "), MIN_DATE, TODAY)

        assert pd.isna(cases.loc[0, "outcome"])
        assert pd.isna(cases.loc[0, "state"])
        assert pd.isna(cases.loc[0, "sex"])
        assert invalid["EVOLUCAO"] == 1
        assert invalid["SG_UF_NOT"] == 1
        assert invalid["CS_SEXO"] == 0  # vazio é ausente, não inválido

    @pytest.mark.parametrize("symptom_date", ["2019-03-01", "2027-01-01", None])
    def test_drops_rows_with_date_outside_period(self, symptom_date):
        raw = _raw(DT_SIN_PRI=pd.Timestamp(symptom_date) if symptom_date else pd.NaT)

        cases, _ = transform(raw, MIN_DATE, TODAY)

        assert cases.empty


class TestSelectSourceFiles:
    def test_picks_latest_snapshot_per_year_and_ignores_other_files(self, tmp_path):
        for name in (
            "INFLUD25-16-11-2026.parquet",
            "INFLUD25-23-11-2026.parquet",
            "INFLUD26-23-11-2026.parquet",
            "outro-arquivo.parquet",
        ):
            (tmp_path / name).touch()

        sources = select_source_files(tmp_path)

        assert [source.path.name for source in sources] == [
            "INFLUD25-23-11-2026.parquet",
            "INFLUD26-23-11-2026.parquet",
        ]

    def test_raises_when_there_is_no_source_file(self, tmp_path):
        with pytest.raises(EtlError, match="Nenhum arquivo"):
            select_source_files(tmp_path)


class TestBuildDatabase:
    @pytest.fixture
    def synthetic_dir(self, tmp_path):
        directory = tmp_path / "raw"
        generate_snapshots(directory, TODAY, n_cases=2_000)
        return directory

    def test_loads_all_cases_and_records_provenance(self, synthetic_dir, tmp_path):
        db_path = tmp_path / "srag.db"

        summary = build_database(synthetic_dir, db_path)

        with closing(connect_readonly(db_path)) as connection:
            count = connection.execute(f"SELECT COUNT(*) FROM {CASES_TABLE}").fetchone()[0]  # noqa: S608
            metadata = read_load_metadata(connection)
        assert count == summary.rows_loaded == 2_000
        assert metadata["synthetic"] is True
        assert [source["filename"] for source in metadata["sources"]] == [
            "INFLUD25-23-11-2026.parquet",
            "INFLUD26-23-11-2026.parquet",
        ]
        assert all(len(source["sha256"]) == 64 for source in metadata["sources"])

    def test_never_loads_columns_outside_allowlist(self, synthetic_dir, tmp_path):
        db_path = tmp_path / "srag.db"

        build_database(synthetic_dir, db_path)

        with sqlite3.connect(db_path) as connection:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({CASES_TABLE})")]
        assert columns == CASES_COLUMNS
        assert DECOY_COLUMN not in columns
        assert DECOY_COLUMN not in SOURCE_COLUMNS

    def test_database_rejects_values_outside_contract(self, synthetic_dir, tmp_path):
        db_path = tmp_path / "srag.db"
        build_database(synthetic_dir, db_path)

        with sqlite3.connect(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO {CASES_TABLE} (symptom_date, outcome) VALUES ('2026-01-01', 'zumbi')"  # noqa: S608
            )

    def test_refuses_to_mix_real_and_synthetic_files(self, synthetic_dir, tmp_path):
        (synthetic_dir / "INFLUD26-23-11-2026.parquet").unlink()
        _write_plain_parquet(synthetic_dir / "INFLUD26-24-11-2026.parquet", _raw())

        with pytest.raises(EtlError, match="misturados"):
            build_database(synthetic_dir, tmp_path / "srag.db")

    def test_failed_load_keeps_previous_database(self, synthetic_dir, tmp_path):
        db_path = tmp_path / "srag.db"
        build_database(synthetic_dir, db_path)
        before = db_path.read_bytes()
        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        _write_plain_parquet(broken_dir / "INFLUD26-24-11-2026.parquet", _raw(DT_SIN_PRI=pd.NaT))

        with pytest.raises(EtlError):
            build_database(broken_dir, db_path)

        assert db_path.read_bytes() == before
        assert not db_path.with_name(db_path.name + ".tmp").exists()
