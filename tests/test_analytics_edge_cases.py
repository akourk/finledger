"""Edge-case guards for the analytics modules.

The e2e snapshot test covers the happy path / composition.  These pin the
degenerate inputs (empty history, single snapshot, zero values) that the
modules must survive without raising and — critically — without emitting
NaN / Infinity, which would propagate as a bare literal into the dashboard
JSON (see the price-cache NaN class of bug)."""

from __future__ import annotations

import json
import math

import pytest


def test_chain_link_uses_trailing_not_alltime_peak():
    """The small-base skip must use the TRAILING peak, not the window's
    all-time peak.  Otherwise a full-history chain skips every early month
    (when the portfolio was small but was the user's entire capital),
    silently dropping the early-years return and making the lifetime TWR
    disagree with the per-year table.

    Scenario: +20% early (on a $100 base), then a $50k contribution, then
    +10%.  With an all-time-peak threshold the early +20% (base $100 < 1%
    of $55k) is wrongly skipped → ~+10%.  With a trailing peak it's
    captured → (1.2)(1.1) - 1 = +32%.
    """
    from src.analytics._shared import _chain_link_return
    snaps = [
        {"date": "2020-06-30", "v": 100.0},
        {"date": "2020-12-31", "v": 120.0},     # +20% (base $100)
        {"date": "2021-06-30", "v": 50120.0},   # +$50k contribution
        {"date": "2021-12-31", "v": 55132.0},   # +10%
    ]
    txns = [{"date": "2021-03-15", "account_group": "X",
             "account_type": "Retirement", "action": "Contribution",
             "symbol": "FOO", "quantity": 1, "price": 50000.0, "amount": 50000.0}]
    r = _chain_link_return(snaps, lambda h: h["v"], txns, None)
    assert r == pytest.approx(0.32, abs=0.01)


def _assert_finite(obj, path="root"):
    """Recursively assert no float in `obj` is NaN or Infinity."""
    if isinstance(obj, float):
        assert math.isfinite(obj), f"non-finite float at {path}: {obj}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_finite(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_finite(v, f"{path}[{i}]")


def _assert_json_safe(obj):
    """The payload must serialize with allow_nan=False (no NaN/inf)."""
    json.dumps(obj, allow_nan=False)


# --- drawdown -------------------------------------------------------------

@pytest.mark.parametrize("history", [
    [],
    [{"date": "2025-01-01", "total": 0}],
    [{"date": "2025-01-01", "total": 1000}],
    [{"date": "2025-01-01", "total": 0}, {"date": "2025-02-01", "total": 0}],
])
def test_drawdown_degenerate(history):
    from src.analytics.drawdown import compute_drawdown
    out = compute_drawdown(history)
    _assert_finite(out)
    _assert_json_safe(out)


# --- monthly_pnl ----------------------------------------------------------

@pytest.mark.parametrize("history", [
    [],
    [{"date": "2025-01-01", "total": 0, "net_contributed": 0}],
    [{"date": "2025-01-01", "total": 1000, "net_contributed": 1000}],
])
def test_monthly_pnl_degenerate(history):
    from src.analytics.monthly_pnl import compute_monthly_pnl
    out = compute_monthly_pnl(history, [])
    _assert_finite(out)
    _assert_json_safe(out)


# --- monte_carlo ----------------------------------------------------------

def test_monte_carlo_zero_balance_zero_years():
    from src.analytics.monte_carlo import compute_monte_carlo
    out = compute_monte_carlo(current_balance=0.0, annual_contribution=0.0,
                              years_to_retirement=0)
    _assert_finite(out)
    _assert_json_safe(out)


def test_monte_carlo_with_cash_and_fi_threshold():
    from src.analytics.monte_carlo import compute_monte_carlo
    out = compute_monte_carlo(current_balance=1000.0, annual_contribution=100.0,
                              years_to_retirement=5, cash_balance=500.0,
                              fi_threshold=10000.0)
    _assert_finite(out)
    _assert_json_safe(out)


# --- concentration --------------------------------------------------------

@pytest.mark.parametrize("holdings", [
    [],
    [{"symbol": "USD", "sector": "Cash", "value": 0, "account_group": "X",
      "account_type": "Savings"}],
    # All-cash portfolio → sector_total is 0 (cash excluded from sectors).
    [{"symbol": "USD", "sector": "Cash", "value": 5000, "account_group": "X",
      "account_type": "Savings"}],
])
def test_concentration_degenerate(holdings):
    from src.analytics.concentration import compute_concentration
    out = compute_concentration(holdings)
    _assert_finite(out)
    _assert_json_safe(out)


# --- daily_pnl ------------------------------------------------------------

@pytest.mark.parametrize("history", [
    [],
    [{"date": "2025-01-01", "total": 0}],
])
def test_daily_pnl_degenerate(history):
    from src.analytics.daily_pnl import compute_daily_pnl
    out = compute_daily_pnl(history, [])
    _assert_finite(out)
    _assert_json_safe(out)


# --- rebalancing ----------------------------------------------------------

def test_rebalancing_zero_value_holdings():
    from src.analytics.rebalancing import compute_rebalancing
    # Targets defined but every holding is worthless → None (no div-by-zero).
    out = compute_rebalancing(
        [{"symbol": "X", "sector": "Tech", "value": 0}],
        [{"bucket": "Tech", "pct": 100}])
    assert out is None
