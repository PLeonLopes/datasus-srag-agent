from datetime import date

from srag_agent.data.profile import code_distributions, profile_columns, render_markdown
from srag_agent.data.synthetic import DECOY_COLUMN, generate_snapshots


def test_eda_publishes_only_aggregates(tmp_path):
    paths = generate_snapshots(tmp_path, date(2026, 11, 23), n_cases=300)

    total_rows, profiles = profile_columns(paths)
    distributions = code_distributions(paths)
    markdown = render_markdown(
        total_rows, profiles, distributions, [p.name for p in paths], date(2026, 11, 25)
    )

    assert total_rows == 300
    decoy = next(profile for profile in profiles if profile.name == DECOY_COLUMN)
    assert decoy.filled_pct == 100.0
    assert not decoy.in_allowlist
    assert DECOY_COLUMN not in distributions
    assert "SINTETICO-" not in markdown  # valores da coluna fora da allowlist nunca aparecem
    assert abs(sum(distributions["EVOLUCAO"].values()) - 100) < 0.5
