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


def test_chain_link_carries_unabsorbed_flow_from_skipped_periods():
    """A deposit whose cash sits invisible (USD in a non-Savings account
    isn't tracked) and gets INVESTED in a later month must not book that
    investment as market gain.

    Scenario: $2,000 portfolio; $5,000 deposited in January (flow-guard
    skips the period — flow >> balance — and the snapshot value doesn't
    move because the cash is untracked); the $5,000 is invested Feb 1 so
    the Feb snapshot jumps to $7,000 with zero February flow.  Without
    the unabsorbed-flow carry the chain booked Feb as +250% pure gain.
    With it, the carried $5,000 nets the jump out to ~0%.
    """
    from src.analytics._shared import _chain_link_return
    snaps = [
        {"date": "2022-12-31", "v": 2000.0},
        {"date": "2023-01-31", "v": 2000.0},   # deposit invisible (cash)
        {"date": "2023-02-28", "v": 7000.0},   # deposit invested Feb 1
        {"date": "2023-03-31", "v": 7350.0},   # +5% real market month
    ]
    txns = [{"date": "2023-01-15", "account_group": "Robinhood",
             "account_type": "Taxable", "action": "Deposit",
             "symbol": "USD", "quantity": 0, "price": 0, "amount": 5000.0}]
    r = _chain_link_return(snaps, lambda h: h["v"], txns, None)
    # Only March's +5% is a real return; Jan is skipped (flow noise) and
    # Feb's jump is the carried deposit, not gain.  Allow Dietz midpoint
    # slack on the Feb period but rule out anything like the old +250%.
    assert r == pytest.approx(0.05, abs=0.08)


def test_chain_link_absorbed_flow_carries_nothing():
    """The mirror case: when a skipped period's flow IS visible in its
    ending value (contribution + buy land in the same snapshot — the
    normal case), nothing carries; the next clean period is measured
    against the already-grown base."""
    from src.analytics._shared import _chain_link_return
    snaps = [
        {"date": "2023-01-31", "v": 1000.0},
        {"date": "2023-02-28", "v": 7000.0},   # +$6k contributed AND invested
        {"date": "2023-03-31", "v": 7700.0},   # +10% real market month
    ]
    txns = [{"date": "2023-02-10", "account_group": "X",
             "account_type": "Retirement", "action": "Contribution",
             "symbol": "FOO", "quantity": 1, "price": 6000.0,
             "amount": 6000.0}]
    r = _chain_link_return(snaps, lambda h: h["v"], txns, None)
    assert r == pytest.approx(0.10, abs=0.01)


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


def test_monte_carlo_cash_bucket_not_double_counted():
    """The all-accounts scenario's cash bucket must count Apple Savings
    once — its balance appears in BOTH by_account_group['Apple Savings']
    and by_sector['Cash'], and summing both inverted the cash/equity
    split ($50k cash + $100k equity became $100k cash + $50k equity)."""
    from src.analytics import build_analytics
    history = [{
        "date": "2026-06-30", "total": 150000.0,
        "by_account_group": {"Apple Savings": 50000.0, "Robinhood": 100000.0},
        "by_account_type": {"Savings": 50000.0, "Taxable": 100000.0},
        "by_sector": {"Cash": 50000.0, "Technology": 100000.0},
        "net_contributed": 120000.0, "priced_pct": 1.0, "positions": [],
        "total_cost_basis": 120000.0,
    }]
    meta = {"birthday": "1990-06-15", "retirement_age": 67,
            "annual_expenses": [{"date": "2026-01-01", "amount": 40000}]}
    out = build_analytics([], history, [], [], retirement_meta=meta)
    mc = out["monte_carlo"]
    assert mc is not None
    s = mc["all_accounts"]["summary"]
    assert s["starting_cash"] == pytest.approx(50000.0)
    assert s["starting_equity"] == pytest.approx(100000.0)


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


# --- pipeline_stages: trivial-symbol filter ---------------------------------

def test_trivial_filter_keeps_fractional_but_material_positions():
    """A closed 0.8 BTC position is < 1 'share' but was worth thousands —
    it must NOT be classed trivial (its price fetch is needed to value
    past snapshots).  A 0.7-share $3 warrant stub stays trivial."""
    from src.pipeline_stages import compute_position_endings
    txns = [
        # 0.8 BTC bought at $30k and fully sold — fractional but material.
        {"date": "2023-01-05", "symbol": "BTC-USD", "action": "Buy",
         "quantity": 0.8, "price": 30000.0, "amount": 24000.0},
        {"date": "2023-06-05", "symbol": "BTC-USD", "action": "Sell",
         "quantity": 0.8, "price": 35000.0, "amount": 28000.0},
        # Spinoff warrant stub: 0.7 shares at ~$4, sold — trivial.
        {"date": "2023-02-01", "symbol": "ENVXW", "action": "Buy",
         "quantity": 0.5000, "price": 4.0, "amount": 2.86},
        {"date": "2023-02-10", "symbol": "ENVXW", "action": "Sell",
         "quantity": 0.5000, "price": 4.2, "amount": 3.0},
    ]
    ends, trivial = compute_position_endings(txns)
    assert "BTC-USD" not in trivial
    assert "ENVXW" in trivial
    assert ends["BTC-USD"] == "2023-06-05"


def test_daily_pnl_skips_non_trading_days(isolated_workdir):
    """Dates where NO held symbol has an actual cached close (weekends,
    market holidays) must not emit bars — get_price's 7-day walk-back
    used to fill them with the prior close, producing flat zero-change
    entries.  Crypto symbols with weekend closes keep those dates."""
    import json as _json
    cache = isolated_workdir / "cache" / "price_cache.json"
    # Mon 2025-01-06 .. Fri 01-10, then Mon 01-13.  11th/12th = weekend.
    series = {f"2025-01-{d:02d}": 100.0 + d for d in (6, 7, 8, 9, 10, 13)}
    cache.write_text(_json.dumps({"FOO": series}), encoding="utf-8")
    from src.analytics.daily_pnl import compute_daily_pnl
    history = [{"date": "2025-01-13", "total": 113.0,
                "positions": [{"symbol": "FOO", "quantity": 1.0,
                               "value": 113.0}]}]
    out = compute_daily_pnl(history, [], window_days=10)
    dates = {r["date"] for r in out}
    assert "2025-01-11" not in dates
    assert "2025-01-12" not in dates
    assert "2025-01-13" in dates
    assert len(dates) >= 3   # the weekday run survived


def test_crypto_unrealized_ignores_unpriced_coins():
    """A coin with basis but no price (value None) must not drag
    total_unrealized down by its full basis."""
    from src.analytics.crypto import compute_crypto_analytics
    holdings = [
        {"symbol": "BTC-USD", "quantity": 1, "value": 50000.0,
         "cost_basis": 30000.0, "unrealized_gain": 20000.0},
        {"symbol": "DEADCOIN-USD", "quantity": 100, "value": None,
         "cost_basis": 5000.0, "unrealized_gain": None},
    ]
    out = compute_crypto_analytics([], holdings)
    assert out["stats"]["total_unrealized"] == 20000.0


def test_income_calendar_excludes_future_dated_income():
    """A future-dated income row (bad parse / post-dated broker entry)
    must not add a phantom month to the trailing-12mo chart."""
    from datetime import date, timedelta
    from src.analytics.income_calendar import compute_income_calendar
    recent = (date.today() - timedelta(days=10)).isoformat()
    future = (date.today() + timedelta(days=40)).isoformat()
    txns = [
        {"date": recent, "action": "Dividend", "symbol": "VTI", "amount": 10.0},
        {"date": future, "action": "Dividend", "symbol": "VTI", "amount": 99.0},
    ]
    out = compute_income_calendar(txns, [])
    months = {r["month"] for r in out["monthly_last_12mo"]}
    assert future[:7] not in months
    assert sum(r["amount"] for r in out["monthly_last_12mo"]) == 10.0
    assert out["ttm_actual"] == 10.0
