"""Unit tests for ``balance_anchor`` — statement true-up for hand-kept
cash accounts, plus the effective-dated savings APR lookup.

The motivating case: Apple Card Daily Cash lands in Apple Savings in
small irregular amounts that never reach the hand-maintained CSV, so
fin's balance drifts low by roughly $50/month forever.

All figures here are synthetic round numbers — never real portfolio
data (this repo is public).
"""

from __future__ import annotations

import pytest

# NOTE: `src.*` imports live INSIDE each test.  conftest purges
# sys.modules per test, so a module-level import would bind function
# objects from collection time whose `config` module is a *different*
# object than the one the fixture patches — the anchor would then read
# an un-patched ACCOUNT_TYPES and silently skip every account.


def _txn(date, action, amount, group="Apple Savings", symbol="USD"):
    return {
        "date": date, "account": group, "account_group": group,
        "account_type": "Savings", "symbol": symbol, "action": action,
        "raw_action": action, "quantity": amount, "price": 1.0,
        "fees": 0.0, "amount": amount, "description": "", "source": "manual.csv",
    }


@pytest.fixture
def savings_groups(monkeypatch):
    """Map the synthetic groups used here onto real account types."""
    from src import config
    monkeypatch.setitem(config.ACCOUNT_TYPES, "Apple Savings", "Savings")
    monkeypatch.setitem(config.ACCOUNT_TYPES, "Robinhood", "Taxable")


def test_cash_balance_walks_deposits_and_withdrawals():
    from src.balance_anchor import cash_balance_at
    txns = [
        _txn("2024-01-01", "Deposit", 5000.0),
        _txn("2024-02-01", "Withdrawal", 1000.0),
        _txn("2024-03-01", "Interest", 20.0),
    ]
    assert cash_balance_at(txns, "Apple Savings", "2024-01-15") == pytest.approx(5000.0)
    assert cash_balance_at(txns, "Apple Savings", "2024-02-15") == pytest.approx(4000.0)
    assert cash_balance_at(txns, "Apple Savings", "2024-12-31") == pytest.approx(4020.0)


def test_anchor_books_the_drift_as_cash_back(savings_groups):
    from src.balance_anchor import apply_balance_anchors, cash_balance_at
    txns = [_txn("2024-01-01", "Deposit", 5000.0)]
    anchors = [{"account_group": "Apple Savings", "date": "2024-06-30",
                "amount": 5100.0, "note": "Daily Cash"}]
    out = apply_balance_anchors(txns, anchors, verbose=False)

    synth = [t for t in out if t["source"] == "auto-balance-anchor"]
    assert len(synth) == 1
    assert synth[0]["action"] == "Cash Back"
    assert synth[0]["amount"] == pytest.approx(100.0)
    assert synth[0]["symbol"] == "USD"
    assert synth[0]["date"] == "2024-06-30"
    # Balance now matches the statement exactly.
    assert cash_balance_at(out, "Apple Savings", "2024-06-30") == pytest.approx(5100.0)


def test_successive_anchors_book_only_new_drift(savings_groups):
    """Each anchor must see the rows earlier anchors synthesized —
    otherwise re-anchoring double-books the whole history every time."""
    from src.balance_anchor import apply_balance_anchors, cash_balance_at
    txns = [_txn("2024-01-01", "Deposit", 5000.0)]
    anchors = [
        {"account_group": "Apple Savings", "date": "2024-06-30", "amount": 5100.0},
        {"account_group": "Apple Savings", "date": "2024-12-31", "amount": 5250.0},
    ]
    out = apply_balance_anchors(txns, anchors, verbose=False)
    synth = sorted((t for t in out if t["source"] == "auto-balance-anchor"),
                   key=lambda t: t["date"])
    assert [round(t["amount"], 2) for t in synth] == [100.0, 150.0]
    assert cash_balance_at(out, "Apple Savings", "2024-12-31") == pytest.approx(5250.0)


def test_anchor_is_cash_flow_in_not_income():
    """Cash Back must count as CONTRIBUTED capital, never as return or
    taxable income.  Card cash back is a purchase rebate: booking it as
    return would overstate the savings account's performance, and
    booking it as income would put a non-taxable rebate into AGI."""
    from src.actions import ACTIONS, CASH_ADD_ACTIONS, INCOME_ACTION_KINDS
    from src.basis import txn_external_cash_flow

    a = ACTIONS["Cash Back"]
    assert (a.balance, a.basis, a.cash_flow) == ("add", "ignore", "in")
    assert "Cash Back" in CASH_ADD_ACTIONS
    assert "Cash Back" not in INCOME_ACTION_KINDS, "cash back is a rebate, not income"
    assert txn_external_cash_flow(
        {"action": "Cash Back", "amount": 100.0,
         "account_group": "Apple Savings"}) == 100.0


def test_anchor_refuses_non_cash_accounts(savings_groups, capsys):
    """A delta on a securities account could be a pricing error, a
    missing split, or a basis bug — plugging it would paper over
    exactly the failures this codebase works to surface."""
    from src.balance_anchor import apply_balance_anchors
    txns = [_txn("2024-01-01", "Deposit", 5000.0, group="Robinhood")]
    anchors = [{"account_group": "Robinhood", "date": "2024-06-30",
                "amount": 99999.0}]
    out = apply_balance_anchors(txns, anchors, verbose=True)
    assert [t for t in out if t["source"] == "auto-balance-anchor"] == []
    assert "only Savings" in capsys.readouterr().out


def test_anchor_refuses_negative_drift(savings_groups, capsys):
    """fin ABOVE the statement means fin has transactions the account
    doesn't — a data bug to investigate, not drift to absorb."""
    from src.balance_anchor import apply_balance_anchors
    txns = [_txn("2024-01-01", "Deposit", 5000.0)]
    anchors = [{"account_group": "Apple Savings", "date": "2024-06-30",
                "amount": 4000.0}]
    out = apply_balance_anchors(txns, anchors, verbose=True)
    assert [t for t in out if t["source"] == "auto-balance-anchor"] == []
    assert "ABOVE" in capsys.readouterr().out


def test_anchor_ignores_sub_penny_drift(savings_groups):
    from src.balance_anchor import apply_balance_anchors
    txns = [_txn("2024-01-01", "Deposit", 5000.0)]
    anchors = [{"account_group": "Apple Savings", "date": "2024-06-30",
                "amount": 5000.001}]
    out = apply_balance_anchors(txns, anchors, verbose=False)
    assert [t for t in out if t["source"] == "auto-balance-anchor"] == []


def test_no_anchors_is_a_no_op():
    from src.balance_anchor import apply_balance_anchors
    txns = [_txn("2024-01-01", "Deposit", 5000.0)]
    assert apply_balance_anchors(txns, [], verbose=False) == txns


def test_apr_lookup_is_effective_dated():
    from src.balance_anchor import apr_at
    rows = [
        {"account_group": "Apple Savings", "date": "2024-01-01", "rate": 0.045},
        {"account_group": "Apple Savings", "date": "2026-01-01", "rate": 0.030},
    ]
    assert apr_at(rows, "Apple Savings", "2024-06-01") == pytest.approx(0.045)
    assert apr_at(rows, "Apple Savings", "2026-06-01") == pytest.approx(0.030)
    assert apr_at(rows, "Apple Savings", "2023-06-01") is None   # before any row
    assert apr_at(rows, "Other", "2026-06-01") is None           # wrong account


def test_apr_accepts_percent_or_decimal(tmp_path):
    """`3.0` and `0.030` must both mean 3.0% — a user reading the rate
    off a banking app will type it either way."""
    from src.metadata import parse_metadata

    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Savings APR,2026-01-01,0.030,Apple Savings,decimal\n"
        "Savings APR,2025-01-01,4.1,Apple Savings,percent\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    by_date = {r["date"]: r["rate"] for r in meta["savings_apr"]}
    assert by_date["2026-01-01"] == pytest.approx(0.030)
    assert by_date["2025-01-01"] == pytest.approx(0.041)


def test_balance_anchor_row_parses(tmp_path):
    from src.metadata import parse_metadata

    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Balance Anchor,2026-08-04,12345.67,Apple Savings,Daily Cash\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    assert meta["balance_anchors"] == [{
        "account_group": "Apple Savings", "date": "2026-08-04",
        "amount": 12345.67, "note": "Daily Cash",
    }]
