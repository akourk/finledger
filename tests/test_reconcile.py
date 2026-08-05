"""Tests for analytics/reconcile.py — fin computed vs broker-reported."""

from __future__ import annotations

from src.analytics.reconcile import compute_reconciliation


def _history():
    """Snapshot dates only.

    Balance rows are computed by walking the ledger to the statement
    date, so the snapshots' *values* are irrelevant — history is
    consulted purely for its last date, which bounds what fin can
    answer for (a statement dated past it gets "nodata").
    """
    return [
        {"date": "2025-12-31", "by_account_group": {}},
        {"date": "2026-01-31", "by_account_group": {}},
        {"date": "2026-05-31", "by_account_group": {}},
    ]


def _balance_txns():
    """Ledger backing the balance tests.

    ``ZZFUND`` is deliberately not a real ticker: the price cache can't
    resolve it, so ``_value_at_date`` falls back to the last transaction
    price and the tests stay deterministic without a stubbed fetch.
    100 units @ $2,000 = $200,000 held from 2026-01-05 onward.
    """
    return [
        {"account_group": "Roth IRA", "symbol": "ZZFUND", "date": "2026-01-05",
         "action": "Buy", "quantity": 100.0, "price": 2000.0, "amount": 200000.0},
    ]


def _txns():
    return _balance_txns() + [
        # Robinhood 2025 equity realized: +1000 and -200 = +800 net
        {"account_group": "Robinhood", "symbol": "AAPL", "date": "2025-03-01",
         "realized_gain": 1000.0},
        {"account_group": "Robinhood", "symbol": "TSLA", "date": "2025-06-01",
         "realized_gain": -200.0},
        # A §1256 contract (SPXW) — must be excluded from "realized",
        # counted under "section_1256"
        {"account_group": "Robinhood", "symbol": "SPXW 12/20/2025 Put $5000.00",
         "date": "2025-09-01", "realized_gain": 1234.56},
        # Income: dividend 50 + interest 20 + lending 5 = 75 (brokers bundle
        # stock-lending "substitute interest" into the 1099-INT).
        {"account_group": "Robinhood", "symbol": "AAPL", "date": "2025-02-01",
         "action": "Dividend", "amount": 50.0},
        {"account_group": "Robinhood", "symbol": "USD", "date": "2025-02-01",
         "action": "Interest", "amount": 20.0},
        {"account_group": "Robinhood", "symbol": "AAPL", "date": "2025-03-01",
         "action": "Lending", "amount": 5.0},
        # Reward must NOT count toward income (it's other_income / 1099-MISC)
        {"account_group": "Robinhood", "symbol": "FOO", "date": "2025-02-01",
         "action": "Reward", "amount": 999.0},
    ]


def test_returns_none_without_reconcile_rows():
    assert compute_reconciliation(_txns(), _history(), None) is None
    assert compute_reconciliation(_txns(), _history(), []) is None


def test_balance_walks_ledger_to_statement_date():
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "balance", "account_group": "Roth IRA",
         "date": "2026-05-31", "amount": 200065.00, "note": "stmt"},
    ])
    row = rec["rows"][0]
    assert row["computed"] == 200000.00
    assert row["delta"] == round(200000.00 - 200065.00, 2)
    assert row["status"] == "ok"   # ~0.03% off


def test_balance_excludes_activity_after_the_statement_date():
    """REGRESSION: a balance row is as-of its own date.

    History is sampled semimonthly (15th / EOM / today), so a statement
    dated the 4th used to resolve to the NEAREST snapshot — today's, one
    day later — and swept up every transaction in between.  A deposit
    made the day AFTER the statement printed as a break of exactly that
    deposit's size.
    """
    txns = _balance_txns() + [
        {"account_group": "Roth IRA", "symbol": "ZZFUND", "date": "2026-08-05",
         "action": "Buy", "quantity": 5.0, "price": 2000.0, "amount": 10000.0},
    ]
    history = _history() + [
        {"date": "2026-07-31", "by_account_group": {}},
        {"date": "2026-08-05", "by_account_group": {}},   # nearest to 08-04
    ]
    rec = compute_reconciliation(txns, history, [
        {"kind": "balance", "account_group": "Roth IRA",
         "date": "2026-08-04", "amount": 200000.00, "note": "stmt"},
    ])
    row = rec["rows"][0]
    assert row["computed"] == 200000.00   # not 210000 — the 08-05 buy is later
    assert row["delta"] == 0.0
    assert row["status"] == "ok"


def test_balance_after_last_snapshot_is_nodata():
    """A statement dated past fin's final snapshot has no answer — say so
    rather than quietly reporting today's positions against it."""
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "balance", "account_group": "Roth IRA",
         "date": "2027-01-31", "amount": 200000.00},
    ])
    row = rec["rows"][0]
    assert row["computed"] is None
    assert row["status"] == "nodata"


def test_realized_excludes_section_1256():
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "realized", "account_group": "Robinhood",
         "date": "2025", "amount": 800.0, "note": "1099-B"},
        {"kind": "section_1256", "account_group": "Robinhood",
         "date": "2025", "amount": 1234.56, "note": "1099-B line 11"},
    ])
    by_kind = {r["kind"]: r for r in rec["rows"]}
    assert by_kind["realized"]["computed"] == 800.0      # excludes SPXW
    assert by_kind["realized"]["status"] == "ok"
    assert by_kind["section_1256"]["computed"] == 1234.56  # SPXW only
    assert by_kind["section_1256"]["status"] == "ok"


def test_income_is_dividends_interest_and_lending():
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 90.0, "note": "1099-DIV+INT"},
    ])
    row = rec["rows"][0]
    # 50 div + 20 int + 5 lending = 75; reward (1099-MISC) excluded.
    assert row["computed"] == 75.0
    assert row["delta"] == -15.0
    assert row["status"] == "off"      # $15 / $90 = 17% → off


def test_other_income_is_rewards_and_lending_not_dividends():
    """Crypto 1099-MISC 'Other Income' = rewards + lending, a different
    bucket than div+int — reconciled via kind 'other_income'."""
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "other_income", "account_group": "Robinhood",
         "date": "2025", "amount": 1004.0, "note": "1099-MISC"},
    ])
    row = rec["rows"][0]
    assert row["computed"] == 1004.0   # reward 999 + lending 5; div/int excluded
    assert row["status"] == "ok"
    assert row["label"] == "Other income 2025"


def test_balance_band_looser_than_form_figures():
    """A ~1.06% gap is 'ok' for a balance (proxy drift / price-timing
    noise) but 'warn' for an exact form figure like income."""
    hist = [{"date": "2026-03-31", "by_account_group": {}}]
    txns = [{"account_group": "401K", "symbol": "ZZCIT", "date": "2026-01-05",
             "action": "Buy", "quantity": 100.0, "price": 593.664,
             "amount": 59366.40}]
    rec = compute_reconciliation(txns, hist, [
        {"kind": "balance", "account_group": "401K",
         "date": "2026-03-31", "amount": 60000.00}])
    assert rec["rows"][0]["computed"] == 59366.40
    assert rec["rows"][0]["status"] == "ok"     # ~1.06% balance → ok

    rec2 = compute_reconciliation(
        [{"account_group": "X", "date": "2025-01-01",
          "action": "Dividend", "amount": 59366.40}],
        [],
        [{"kind": "income", "account_group": "X",
          "date": "2025", "amount": 60000.00}])
    assert rec2["rows"][0]["status"] == "warn"  # same ~1.06% on income → warn


def test_summary_counts_statuses():
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "balance", "account_group": "Roth IRA",
         "date": "2026-05-31", "amount": 200000.00},          # ok
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 90.0},                     # off
    ])
    assert rec["summary"]["total"] == 2
    assert rec["summary"]["ok"] == 1
    assert rec["summary"]["off"] == 1


def test_rows_carry_date_for_drilldown():
    """Every reconcile row exports its `date` (year for form kinds, ISO
    date for balance) so the dashboard drill-down can re-derive the
    composing txn set without parsing the display label."""
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "realized", "account_group": "Robinhood",
         "date": "2025", "amount": 800.0},
        {"kind": "balance", "account_group": "Roth IRA",
         "date": "2026-05-31", "amount": 200000.00},
    ])
    by_kind = {r["kind"]: r for r in rec["rows"]}
    assert by_kind["realized"]["date"] == "2025"
    assert by_kind["balance"]["date"] == "2026-05-31"


def test_expected_delta_renders_explained():
    """An `[expected ±N.NN]` Note token bands the RESIDUAL: a documented
    delta that still holds renders 'explained' instead of off."""
    rec = compute_reconciliation(_txns(), _history(), [
        # fin computes 75; form says 69.06 -> delta +5.94, all expected
        # (a K-1 entity's distributions the 1099-DIV can't see).
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 69.06,
         "note": "1099-DIV+INT [expected +5.94] EPD K-1"},
    ])
    row = rec["rows"][0]
    assert row["status"] == "explained"
    assert row["delta"] == 5.94
    assert row["expected"] == 5.94
    assert "residual +0.00" in row["detail"]
    assert rec["summary"]["explained"] == 1
    assert rec["summary"]["off"] == 0


def test_stale_expectation_reflags_on_residual():
    """An explanation must never mask NEW drift: when the actual delta
    moves away from the expectation, the row re-flags at the residual's
    severity."""
    rec = compute_reconciliation(_txns(), _history(), [
        # fin computes 75; form says 50 -> delta +25, but only +5.94 is
        # documented -> residual +19.06 -> off.
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 50.0,
         "note": "[expected +5.94] EPD K-1"},
    ])
    row = rec["rows"][0]
    assert row["status"] == "off"
    assert "residual +19.06" in row["detail"]


def test_note_without_token_unchanged():
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 90.0,
         "note": "expected to be close"},   # prose, not a token
    ])
    assert rec["rows"][0]["status"] == "off"
    assert rec["rows"][0]["expected"] is None


def test_expected_rows_export_residual():
    """Rows with an [expected] token export `residual` — the panel's
    Δ column shows it (the unexplained remainder) instead of the raw
    delta, which moves to a tooltip."""
    rec = compute_reconciliation(_txns(), _history(), [
        {"kind": "income", "account_group": "Robinhood",
         "date": "2025", "amount": 69.06,
         "note": "[expected +5.94] EPD K-1"},
        {"kind": "realized", "account_group": "Robinhood",
         "date": "2025", "amount": 800.0, "note": "plain"},
    ])
    by_kind = {r["kind"]: r for r in rec["rows"]}
    assert by_kind["income"]["residual"] == 0.0
    assert by_kind["realized"]["residual"] is None
