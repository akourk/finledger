"""Per-lot inventory export (src/analytics/lots.py) + its consumers.

Pins: grouping/sorting, dust folding (totals still include folded
lots), the option contract multiplier at lot scale, lt_relevant
classification, the no-NaN guarantee, the Tax tab's lt_horizon staying
a faithful filtered view, the data-health parity check, and the
lt_horizon-driven long_term_soon alert.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest


def _holding(acct, sym, price, cost_basis, acct_type="Taxable"):
    return {"account_group": acct, "account_type": acct_type,
            "symbol": sym, "quantity": 0.0, "price": price,
            "value": None, "cost_basis": cost_basis,
            "unrealized_gain": None}


def _iso_days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def test_open_lots_grouping_totals_and_sort(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    state = {"lots": {
        ("Broker", "AAA"): [
            {"date": "2023-05-01", "qty": 2.0, "basis_per_share": 100.0},
            {"date": "2021-01-01", "qty": 1.0, "basis_per_share": 50.0},
        ],
        ("Broker", "USD"): [
            {"date": "2023-01-01", "qty": 500.0, "basis_per_share": 1.0}],
    }}
    holdings = [_holding("Broker", "AAA", 120.0, 250.0)]
    out = compute_open_lots(state, holdings)
    assert out["total_lots"] == 2
    assert len(out["positions"]) == 1          # USD excluded
    p = out["positions"][0]
    assert (p["account_group"], p["symbol"]) == ("Broker", "AAA")
    assert p["quantity"] == pytest.approx(3.0)
    assert p["cost_basis"] == pytest.approx(250.0)
    assert p["lt_relevant"] is True
    # Lots sorted by acquired date ascending.
    assert [l["date"] for l in p["lots"]] == ["2021-01-01", "2023-05-01"]
    l0 = p["lots"][0]
    assert l0["value"] == pytest.approx(120.0)
    assert l0["unrealized_gain"] == pytest.approx(70.0)
    assert l0["is_long_term"] is True


def test_open_lots_dust_folding_keeps_totals(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    state = {"lots": {
        ("Broker", "BBB"): (
            [{"date": "2024-01-01", "qty": 5.0, "basis_per_share": 10.0}]
            + [{"date": f"2024-02-{d:02d}", "qty": 0.001,
                "basis_per_share": 10.0} for d in range(1, 11)]
        ),
    }}
    holdings = [_holding("Broker", "BBB", 12.0, 50.1)]
    out = compute_open_lots(state, holdings)
    p = out["positions"][0]
    # Ten $0.012-value lots folded; one visible lot remains.
    assert len(p["lots"]) == 1
    assert p["micro"]["count"] == 10
    assert p["micro"]["qty"] == pytest.approx(0.01)
    assert p["micro"]["cost_basis"] == pytest.approx(0.1)
    # Position totals still include the folded lots (parity!).
    assert p["quantity"] == pytest.approx(5.01)
    assert p["cost_basis"] == pytest.approx(50.1)
    assert out["total_lots"] == 1


def test_open_lots_option_contract_multiplier(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    opt = "FOO 12/18/2026 Call $100.00"
    state = {"lots": {
        ("Broker", opt): [
            {"date": "2026-01-05", "qty": 2.0, "basis_per_share": 150.0}],
    }}
    holdings = [_holding("Broker", opt, 2.0, 300.0)]   # $2.00 premium
    out = compute_open_lots(state, holdings)
    p = out["positions"][0]
    assert p["lt_relevant"] is False               # option contract
    l0 = p["lots"][0]
    # 2 contracts × $2.00 premium × 100 = $400, NOT $4.
    assert l0["value"] == pytest.approx(400.0)
    assert l0["unrealized_gain"] == pytest.approx(100.0)


def test_open_lots_retirement_not_lt_relevant(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    state = {"lots": {
        ("MyIRA", "CCC"): [
            {"date": "2020-06-01", "qty": 1.0, "basis_per_share": 10.0}],
    }}
    holdings = [_holding("MyIRA", "CCC", 15.0, 10.0, acct_type="Retirement")]
    out = compute_open_lots(state, holdings)
    assert out["positions"][0]["lt_relevant"] is False


def test_open_lots_never_emits_nan(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    state = {"lots": {
        # Zero-basis lot (reward) → unrealized_pct must be None, not inf.
        ("Broker", "DDD"): [
            {"date": "2024-01-01", "qty": 2.0, "basis_per_share": 0.0}],
        # Unpriced symbol → value / unrealized None.
        ("Broker", "EEE"): [
            {"date": "", "qty": 3.0, "basis_per_share": 5.0}],
    }}
    holdings = [_holding("Broker", "DDD", 10.0, 0.0),
                _holding("Broker", "EEE", 0.0, 15.0)]
    out = compute_open_lots(state, holdings)
    blob = json.dumps(out)
    assert "NaN" not in blob and "Infinity" not in blob
    ddd = next(p for p in out["positions"] if p["symbol"] == "DDD")
    assert ddd["lots"][0]["unrealized_pct"] is None
    eee = next(p for p in out["positions"] if p["symbol"] == "EEE")
    # Undated + unpriced lot still carries its basis; period fields None.
    assert eee["lots"][0]["value"] is None
    assert eee["lots"][0]["days_held"] is None
    assert eee["cost_basis"] == pytest.approx(15.0)


def test_lt_horizon_is_filtered_view(isolated_workdir):
    """tax.lt_horizon keeps its exact row shape and exclusions
    (retirement, options, undated) while sourcing from lots.py."""
    from src.config import ACCOUNT_TYPES
    ACCOUNT_TYPES["MyIRA"] = "Retirement"
    from src.analytics.tax import _compute_lt_horizon
    opt = "FOO 12/18/2026 Call $100.00"
    st_days = 200
    state = {"lots": {
        ("Broker", "AAA"): [
            {"date": _iso_days_ago(st_days), "qty": 2.0,
             "basis_per_share": 100.0},
            {"date": "", "qty": 1.0, "basis_per_share": 40.0},   # undated
        ],
        ("MyIRA", "AAA"): [
            {"date": _iso_days_ago(500), "qty": 1.0,
             "basis_per_share": 90.0}],
        ("Broker", opt): [
            {"date": _iso_days_ago(30), "qty": 1.0,
             "basis_per_share": 150.0}],
    }}
    holdings = [{"symbol": "AAA", "price": 120.0},
                {"symbol": opt, "price": 2.0}]
    rows = _compute_lt_horizon(state, holdings)
    assert len(rows) == 1                       # taxable dated stock only
    r = rows[0]
    assert set(r) == {"account_group", "symbol", "open_date",
                      "lt_eligible_date", "days_held", "days_to_lt",
                      "is_long_term", "qty", "cost_basis", "price",
                      "value", "unrealized_gain"}
    assert r["days_held"] == st_days
    assert r["is_long_term"] is False
    assert r["value"] == pytest.approx(240.0)
    assert r["unrealized_gain"] == pytest.approx(40.0)


def test_lt_eligible_date_leap_day(isolated_workdir):
    from src.analytics.lots import _lt_eligible_date
    # Feb 29 lot → anniversary maps to Feb 28 next year, +1 = Mar 1.
    assert _lt_eligible_date(date(2024, 2, 29)) == date(2025, 3, 1)
    assert _lt_eligible_date(date(2024, 3, 1)) == date(2025, 3, 2)


def test_lots_holdings_parity_check(isolated_workdir):
    from src.analytics.data_health import _check_lots_holdings_basis_parity
    holdings = [_holding("Broker", "AAA", 120.0, 250.0)]
    ok = {"lots": {"positions": [
        {"account_group": "Broker", "symbol": "AAA", "cost_basis": 250.02}]}}
    assert _check_lots_holdings_basis_parity(ok, holdings) == []
    bad = {"lots": {"positions": [
        {"account_group": "Broker", "symbol": "AAA", "cost_basis": 240.0}]}}
    issues = _check_lots_holdings_basis_parity(bad, holdings)
    assert len(issues) == 1
    assert issues[0]["kind"] == "lots_holdings_basis_parity"
    assert issues[0]["severity"] == "high"


def test_alerts_long_term_soon_reads_lt_horizon(isolated_workdir):
    from src.analytics.alerts import compute_alerts
    tax = {"lt_horizon": [
        # Two ST lots of the same symbol within 60d — one alert, soonest
        # day, gains summed.
        {"symbol": "AAA", "is_long_term": False, "days_to_lt": 30,
         "unrealized_gain": 500.0},
        {"symbol": "AAA", "is_long_term": False, "days_to_lt": 10,
         "unrealized_gain": 400.0},
        # LT lot — ignored.
        {"symbol": "BBB", "is_long_term": True, "days_to_lt": -100,
         "unrealized_gain": 9000.0},
        # Small gain — ignored.
        {"symbol": "CCC", "is_long_term": False, "days_to_lt": 5,
         "unrealized_gain": 20.0},
    ]}
    alerts = compute_alerts(txns=[], holdings_by_account=[], history=[],
                            tax_analytics=tax, concentration={},
                            price_meta={}, cusip_collisions=[])
    lt = [a for a in alerts if a["kind"] == "long_term_soon"]
    assert len(lt) == 1
    assert "AAA" in lt[0]["message"]
    assert "10 day(s)" in lt[0]["message"]
    assert "$900" in lt[0]["message"]


def test_open_lots_empty_state(isolated_workdir):
    from src.analytics.lots import compute_open_lots
    assert compute_open_lots(None, []) is None
    assert compute_open_lots({}, []) is None
    out = compute_open_lots({"lots": {}}, [])
    assert out["positions"] == [] and out["total_lots"] == 0
