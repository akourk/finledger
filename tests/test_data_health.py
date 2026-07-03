"""Tests for src/analytics/data_health.py — pipeline integrity diagnostics.

Each check has a positive case (synthetic data that triggers the issue)
and a negative case (clean data, no issue).  Pin both so a refactor
can't silently disable detection.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Empty cache dir — checks that read price_cache_meta.json
    gracefully no-op when the file doesn't exist."""
    return tmp_path


def test_clean_data_returns_no_issues(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[],
        history=[
            {"date": "2024-01-01", "total": 1000, "by_account_group": {"X": 1000},
             "priced_pct": 1.0},
        ],
        analytics={},
        cache_dir=cache_dir,
    )
    assert issues == []


def test_future_dated_txn_flagged_high(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[{"date": "2099-01-01", "action": "Buy", "symbol": "AAPL",
               "account_group": "Robinhood"}],
        holdings_by_account=[], history=[], analytics={},
        cache_dir=cache_dir,
    )
    assert any(i["kind"] == "future_dated_txns" and i["severity"] == "high"
               for i in issues)


def test_negative_cost_basis_flagged_high(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[
            {"symbol": "AAPL", "account_group": "Robinhood",
             "quantity": 5, "cost_basis": -100.0},
        ],
        history=[], analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "negative_cost_basis" and i["severity"] == "high"
               for i in issues)


def test_snapshot_rollup_mismatch_flagged_high(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[], holdings_by_account=[],
        history=[{
            "date": "2024-01-01", "total": 1000.0,
            "by_account_group": {"A": 600, "B": 200},  # sum=800, not 1000
            "priced_pct": 1.0,
        }],
        analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "snapshot_rollup_mismatch" and i["severity"] == "high"
               for i in issues)


def test_value_qty_price_mismatch_flagged_high(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[{
            "symbol": "AAPL", "account_group": "Robinhood",
            "quantity": 10, "price": 100.0, "value": 950.0,  # expected 1000
            "cost_basis": 500,
        }],
        history=[], analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "value_qty_price_mismatch" and i["severity"] == "high"
               for i in issues)


def test_open_options_past_expiration_flagged_warn(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[], holdings_by_account=[], history=[],
        analytics={"options": {"open_contracts": [
            {"symbol": "AAPL 1/1/2020 Call $100", "expiry": "2020-01-01"},
        ]}},
        cache_dir=cache_dir,
    )
    assert any(i["kind"] == "expired_options_still_open"
               and i["severity"] == "warn" for i in issues)


def test_negative_per_account_contributions_no_holdings_flagged_as_missing_funding(cache_dir):
    """Negative net_contributed with NO remaining holdings = likely
    missing-data case, surface as ``missing_funding_leg`` info."""
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[
            {"date": "2020-01-01", "action": "Withdrawal",
             "account_group": "Coinbase", "symbol": "USD", "amount": 5000.0},
        ],
        holdings_by_account=[], history=[], analytics={},
        cache_dir=cache_dir,
    )
    assert any(i["kind"] == "missing_funding_leg"
               and i["severity"] == "info" for i in issues)


def test_negative_per_account_contributions_with_appreciated_holdings_flagged_as_gains_withdrawn(cache_dir):
    """Negative net_contributed but holdings worth more than the
    withdrawal explains → user just withdrew gains, not a data bug."""
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[
            # Buy $1k of crypto, withdraw $5k after appreciation
            {"date": "2017-01-01", "action": "Buy",
             "account_group": "Coinbase", "symbol": "BTC-USD",
             "amount": 1000.0, "description": "using bank account ****"},
            {"date": "2020-01-01", "action": "Withdrawal",
             "account_group": "Coinbase", "symbol": "USD", "amount": 5000.0},
        ],
        holdings_by_account=[
            {"account_group": "Coinbase", "symbol": "BTC-USD",
             "quantity": 0.01, "value": 600.0},
        ],
        history=[], analytics={}, cache_dir=cache_dir,
    )
    # Net contributed = +1000 (Buy bank-funded) − 5000 (Withdrawal) = −4000.
    # Holdings = $600.  Implied P&L = $600 − (−4000) = +$4600 → gains withdrawn.
    assert any(i["kind"] == "gains_withdrawn"
               and i["severity"] == "info" for i in issues)


def test_priced_coverage_below_95pct_flagged_info(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[], holdings_by_account=[],
        history=[{"date": "2020-01-01", "total": 1000,
                  "by_account_group": {"A": 1000}, "priced_pct": 0.85}],
        analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "snapshot_coverage_gap"
               and i["severity"] == "info" for i in issues)


def test_orphan_zero_qty_basis_flagged_warn(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[{
            "symbol": "DEAD", "account_group": "Robinhood",
            "quantity": 0, "cost_basis": 50.0, "value": 0,
        }],
        history=[], analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "zero_qty_with_basis"
               and i["severity"] == "warn" for i in issues)


def test_lot_queue_parity_check_passes_on_clean_data(cache_dir):
    """The basis walker's running cost-basis sum (derived from txn
    annotations) must match the holdings-table cost_basis for every
    held (account, symbol).  Clean fixture: walked basis = held basis,
    no violation."""
    from src.analytics.data_health import compute_data_health
    txns = [
        {"date": "2024-01-01", "action": "Buy", "symbol": "AAPL",
         "account_group": "Robinhood", "basis_effect": "add",
         "cost_basis": 1000.0},
    ]
    holdings = [{"symbol": "AAPL", "account_group": "Robinhood",
                 "quantity": 10, "cost_basis": 1000.0,
                 "value": 1500.0, "price": 150.0}]
    issues = compute_data_health(
        txns=txns, holdings_by_account=holdings, history=[],
        analytics={}, cache_dir=cache_dir,
    )
    assert not any(i["kind"] == "lot_queue_parity_drift" for i in issues)


def test_lot_queue_parity_handles_transfer_out(cache_dir):
    """Real-data shape: a Buy followed by an unpaired Transfer Out
    (sending crypto to an external wallet).  The basis walker
    correctly removes the lot, and the parity check should agree
    — verifying that ``derive_basis_by_key_from_txns`` honors
    ``basis_effect="transfer_out"``.

    A real bug, fixed 2026-04-26: the parity check was only counting
    'add' / 'remove' effects, missing transfer_out.  Result: every
    crypto-to-cold-storage send showed phantom drift on the user's
    real Coinbase data."""
    from src.analytics.data_health import compute_data_health
    txns = [
        {"date": "2017-05-30", "action": "Buy", "symbol": "BTC-USD",
         "account_group": "Coinbase", "basis_effect": "add",
         "cost_basis": 600.00, "quantity": 0.2679},
        {"date": "2017-05-30", "action": "Transfer Out", "symbol": "BTC-USD",
         "account_group": "Coinbase", "basis_effect": "transfer_out",
         "cost_basis": 600.00, "quantity": 0.2679},
        # Later buy that's still held
        {"date": "2025-01-21", "action": "Buy", "symbol": "BTC-USD",
         "account_group": "Coinbase", "basis_effect": "add",
         "cost_basis": 8000.00, "quantity": 0.0912},
    ]
    holdings = [{
        "symbol": "BTC-USD", "account_group": "Coinbase",
        "quantity": 0.0912, "cost_basis": 8000.00,
        "value": 10000.0, "price": 109649.0,
    }]
    issues = compute_data_health(
        txns=txns, holdings_by_account=holdings, history=[],
        analytics={}, cache_dir=cache_dir,
    )
    parity_issues = [i for i in issues if i["kind"] == "lot_queue_parity_drift"]
    assert not parity_issues, (
        f"transfer_out should be honored as a basis removal — "
        f"unexpected drift: {parity_issues}"
    )


def test_lot_queue_parity_handles_zero_basis(cache_dir):
    """Reward / Spinoff / unpaired-Transfer-In txns add shares with
    cb=0 via ``basis_effect="zero_basis"`` or ``"transfer_in_unpaired"``.
    The lot-queue parity check must accept those as net-zero
    additions to running basis."""
    from src.analytics.data_health import compute_data_health
    txns = [
        {"date": "2024-01-01", "action": "Reward", "symbol": "ETH-USD",
         "account_group": "Coinbase", "basis_effect": "zero_basis",
         "cost_basis": 0.0, "quantity": 0.05},
        {"date": "2024-06-01", "action": "Buy", "symbol": "ETH-USD",
         "account_group": "Coinbase", "basis_effect": "add",
         "cost_basis": 1500.0, "quantity": 0.5},
    ]
    holdings = [{
        "symbol": "ETH-USD", "account_group": "Coinbase",
        "quantity": 0.55, "cost_basis": 1500.0,
        "value": 1700.0, "price": 3090.91,
    }]
    issues = compute_data_health(
        txns=txns, holdings_by_account=holdings, history=[],
        analytics={}, cache_dir=cache_dir,
    )
    parity_issues = [i for i in issues if i["kind"] == "lot_queue_parity_drift"]
    assert not parity_issues, parity_issues


def test_lot_queue_parity_drift_flagged_high(cache_dir):
    """Walked basis ≠ held basis → high-severity violation."""
    from src.analytics.data_health import compute_data_health
    txns = [
        {"date": "2024-01-01", "action": "Buy", "symbol": "AAPL",
         "account_group": "Robinhood", "basis_effect": "add",
         "cost_basis": 1000.0},
    ]
    # Holdings claim a different basis — should trip the check
    holdings = [{"symbol": "AAPL", "account_group": "Robinhood",
                 "quantity": 10, "cost_basis": 5000.0,
                 "value": 1500.0, "price": 150.0}]
    issues = compute_data_health(
        txns=txns, holdings_by_account=holdings, history=[],
        analytics={}, cache_dir=cache_dir,
    )
    drift = [i for i in issues if i["kind"] == "lot_queue_parity_drift"]
    assert len(drift) == 1
    assert drift[0]["severity"] == "high"


def test_check_invariants_raises_on_high_severity(cache_dir):
    """``check_invariants`` is the gate used by pipeline runs +
    test runs.  High-severity violations must raise; warn / info pass."""
    from src.analytics.data_health import check_invariants, InvariantViolation
    import pytest as _pytest

    # Future-dated txn = high severity → must raise
    with _pytest.raises(InvariantViolation):
        check_invariants(
            txns=[{"date": "2099-01-01", "action": "Buy", "symbol": "AAPL"}],
            holdings_by_account=[], history=[], analytics={},
            cache_dir=cache_dir,
        )


def test_check_invariants_silent_on_clean_data(cache_dir):
    from src.analytics.data_health import check_invariants
    # Should not raise
    check_invariants(
        txns=[],
        holdings_by_account=[],
        history=[{"date": "2024-01-01", "total": 1000,
                  "by_account_group": {"X": 1000}, "priced_pct": 1.0}],
        analytics={},
        cache_dir=cache_dir,
    )


def test_check_invariants_does_not_raise_on_warn_or_info_only(cache_dir):
    """Warn/info issues are diagnostic only — they must not raise.
    This pins that env-var-driven assertions stay lightweight enough
    to run in production without crashing on minor coverage gaps."""
    from src.analytics.data_health import check_invariants
    # zero_qty_with_basis is `warn`, not `high`
    check_invariants(
        txns=[],
        holdings_by_account=[{
            "symbol": "DEAD", "account_group": "Robinhood",
            "quantity": 0, "cost_basis": 50.0, "value": 0,
        }],
        history=[], analytics={}, cache_dir=cache_dir,
    )


def test_severity_ordering(cache_dir):
    """Output must be sorted with high first, then warn, then info."""
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[
            # info: negative per-account
            {"date": "2020-01-01", "action": "Withdrawal",
             "account_group": "Coinbase", "symbol": "USD", "amount": 5000.0},
        ],
        holdings_by_account=[
            # high: negative basis
            {"symbol": "AAPL", "account_group": "Robinhood",
             "quantity": 5, "cost_basis": -100.0, "value": 100, "price": 20},
        ],
        history=[], analytics={}, cache_dir=cache_dir,
    )
    severities = [i["severity"] for i in issues]
    rank = {"high": 0, "warn": 1, "info": 2}
    assert severities == sorted(severities, key=lambda s: rank[s])


def test_history_holdings_basis_parity_flagged_high(cache_dir):
    """Latest snapshot cost basis disagreeing with the holdings table
    (per account/symbol) is a high-severity walker-drift signal."""
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[
            {"symbol": "CBETH-USD", "account_group": "Coinbase",
             "quantity": 1.9, "cost_basis": 5000.0, "price": 3100.0,
             "value": 5890.0},
        ],
        history=[
            {"date": "2024-01-31", "total": 5890.0,
             "by_account_group": {"Coinbase": 5890.0}, "priced_pct": 1.0,
             "positions": [
                 {"account_group": "Coinbase", "symbol": "CBETH-USD",
                  "quantity": 1.9, "cost_basis": 0.0, "value": 5890.0},
             ]},
        ],
        analytics={}, cache_dir=cache_dir,
    )
    assert any(i["kind"] == "history_holdings_basis_parity"
               and i["severity"] == "high" for i in issues)


def test_history_holdings_basis_parity_clean_when_matching(cache_dir):
    from src.analytics.data_health import compute_data_health
    issues = compute_data_health(
        txns=[],
        holdings_by_account=[
            {"symbol": "CBETH-USD", "account_group": "Coinbase",
             "quantity": 1.9, "cost_basis": 5000.0, "price": 3100.0,
             "value": 5890.0},
        ],
        history=[
            {"date": "2024-01-31", "total": 5890.0,
             "by_account_group": {"Coinbase": 5890.0}, "priced_pct": 1.0,
             "positions": [
                 {"account_group": "Coinbase", "symbol": "CBETH-USD",
                  "quantity": 1.9, "cost_basis": 5000.0, "value": 5890.0},
             ]},
        ],
        analytics={}, cache_dir=cache_dir,
    )
    assert not any(i["kind"] == "history_holdings_basis_parity" for i in issues)


def test_realized_drift_tolerates_per_year_rounding():
    """The by-year realized rows are rounded to cents; summing many of
    them drifts from the unrounded txn total by a few cents — that's
    rounding, not a real inconsistency, and must not warn."""
    from src.analytics.data_health import _check_realized_gain_reconciliation
    # 9 year rows each carrying a rounded st gain; the unrounded txn
    # total is a hair off the sum of the rounded rows.
    ry = [{"year": str(2017 + i), "st": round(100.0 + i * 0.333, 2), "lt": 0.0}
          for i in range(9)]
    ay = sum(r["st"] for r in ry)
    txns = [{"realized_gain": ay + 0.02}]   # 2-cent rounding drift
    assert _check_realized_gain_reconciliation(txns, {"tax": {"realized_by_year": ry}}) == []


def test_realized_drift_flags_material_gap():
    """A real inconsistency (well beyond per-row rounding) still warns."""
    from src.analytics.data_health import _check_realized_gain_reconciliation
    ry = [{"year": "2024", "st": 1000.0, "lt": 500.0}]
    txns = [{"realized_gain": 2000.0}]      # $500 off — not rounding
    issues = _check_realized_gain_reconciliation(txns, {"tax": {"realized_by_year": ry}})
    assert len(issues) == 1
    assert issues[0]["kind"] == "realized_gain_drift"
    assert issues[0]["severity"] == "warn"
