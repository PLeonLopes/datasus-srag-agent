from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from srag_agent.data.download import parse_snapshot_filename
from srag_agent.data.schema import SOURCE_COLUMNS
from srag_agent.data.synthetic import DECOY_COLUMN, generate_cases, generate_snapshots, is_synthetic

TODAY = date(2026, 11, 23)


def test_generated_cases_have_source_columns_within_period():
    cases = generate_cases(date(2025, 1, 1), TODAY, n_cases=500)

    assert set(SOURCE_COLUMNS) <= set(cases.columns)
    assert DECOY_COLUMN in cases.columns
    assert len(cases) == 500
    assert cases["DT_SIN_PRI"].between(pd.Timestamp("2025-01-01"), pd.Timestamp(TODAY)).all()


def test_generation_is_reproducible_with_same_seed():
    first = generate_cases(date(2025, 1, 1), TODAY, n_cases=200, seed=7)
    second = generate_cases(date(2025, 1, 1), TODAY, n_cases=200, seed=7)

    pd.testing.assert_frame_equal(first, second)


def test_snapshots_follow_datasus_naming_and_are_marked_synthetic(tmp_path):
    paths = generate_snapshots(tmp_path, TODAY, n_cases=500)

    assert [parse_snapshot_filename(path.name) for path in paths] == [(2025, TODAY), (2026, TODAY)]
    assert all(is_synthetic(path) for path in paths)


def test_plain_parquet_is_not_marked_synthetic(tmp_path):
    path = tmp_path / "INFLUD26-23-11-2026.parquet"
    pq.write_table(pa.table({"DT_SIN_PRI": [1]}), path)

    assert not is_synthetic(path)
