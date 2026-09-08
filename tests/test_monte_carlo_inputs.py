"""Projection assumptions use calendar time, independent of snapshot cadence."""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _history(dates):
    start = date(2021, 1, 1)
    return [{"date": d.isoformat(),
             "net_contributed": 1_000.0 + (d - start).days * 4.0}
            for d in dates]


def test_annual_contribution_is_independent_of_snapshot_cadence():
    from src.analytics.monte_carlo import trailing_annual_contribution

    start, end = date(2021, 1, 1), date(2024, 1, 1)
    daily = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    cadences = [
        daily,
        [d for d in daily if d.day == 1],
        [d for d in daily if d.day in (1, 15)],
        [start, date(2021, 3, 9), date(2022, 8, 20), end],
    ]
    for dates in cadences:
        assert trailing_annual_contribution(_history(dates), 0) == pytest.approx(1461.0)


def test_contribution_window_excludes_old_funding_and_uses_actual_dates():
    from src.analytics.monte_carlo import trailing_annual_contribution

    history = [{"date": "2015-01-01", "net_contributed": 0.0}]
    history += _history([date(2021, 1, 1), date(2024, 1, 1)])
    assert trailing_annual_contribution(history, 0) == pytest.approx(1461.0)
    shorter = _history([date(2023, 7, 1), date(2024, 1, 1)])
    assert trailing_annual_contribution(shorter, 0) == pytest.approx(1461.0)


def test_contribution_window_handles_leap_day_and_incomplete_history():
    from src.analytics.monte_carlo import trailing_annual_contribution

    leap_window = _history([date(2021, 2, 28), date(2024, 2, 29)])
    assert trailing_annual_contribution(leap_window, 0) == pytest.approx(1461.0)
    assert trailing_annual_contribution([], 500.0) == 500.0
    assert trailing_annual_contribution([{"date": "invalid"}], 500.0) == 500.0
    assert trailing_annual_contribution(leap_window[:1], 500.0) == 500.0
    assert trailing_annual_contribution([
        {"date": "2023-01-01", "net_contributed": 2_000.0},
        {"date": "2024-01-01", "net_contributed": 1_000.0},
    ], 500.0) == 0.0


@pytest.mark.parametrize("as_of, expected_horizon", [
    ("2024-06-14", 34), ("2024-06-15", 33),
])
def test_projection_inputs_follow_the_pinned_calendar(
    isolated_workdir, monkeypatch, as_of, expected_horizon,
):
    from src import analytics

    monkeypatch.setenv("FIN_AS_OF_DATE", as_of)
    calls = []

    def capture_projection(**kwargs):
        calls.append(kwargs)
        return {"bands": [], "summary": {}}

    monkeypatch.setattr(analytics, "compute_monte_carlo", capture_projection)
    history = _history([date(2021, 1, 1), date(2024, 1, 1)])
    for row in history:
        row.update(total=10_000.0, positions=[], by_sector={},
                   by_account_group={}, by_account_type={})
    analytics.build_analytics(
        [], history, [], [],
        retirement_meta={"birthday": "1990-06-15", "retirement_age": 67},
    )

    assert len(calls) == 2
    assert all(c["years_to_retirement"] == expected_horizon for c in calls)
    assert calls[1]["annual_contribution"] == pytest.approx(1461.0)
