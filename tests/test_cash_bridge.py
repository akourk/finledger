"""Unit tests for ``cash_bridge`` — reconstructed broker cash.

fin deliberately doesn't track USD for non-Savings accounts because
most broker exports underreport cash.  For groups whose export IS
complete, the balance is reconstructed and added to history snapshots;
getting a sign wrong here invents portfolio value out of nothing, so
these pin the classification rather than the resulting figures.

All amounts are synthetic round numbers — never real portfolio data.
"""

from __future__ import annotations

import pytest

from src.cash_bridge import (
    BRIDGED_GROUPS, all_series, balance_at, usd_effect, usd_series,
)


def _t(date, action, amount, symbol="USD", group="Robinhood", source="rh.csv"):
    return {"date": date, "account_group": group, "action": action,
            "symbol": symbol, "amount": amount, "source": source}


def test_only_registered_groups_are_bridged():
    """A group not in the registry contributes nothing.  Adding one is
    a claim about that broker's data completeness (see module docstring)
    — an unvalidated group would inflate portfolio value."""
    assert "Robinhood" in BRIDGED_GROUPS and "Coinbase" in BRIDGED_GROUPS
    assert usd_effect(_t("2024-01-01", "Sell", 500.0, group="Roth IRA")) == 0.0
    assert usd_effect(_t("2024-01-01", "Sell", 500.0, group="401K")) == 0.0
    assert all_series([_t("2024-01-01", "Sell", 500.0, group="Roth IRA")]) == {}


@pytest.mark.parametrize("action,expected", [
    ("Deposit", +1),
    ("Sell", +1),
    ("Option Sell", +1),
    ("Option Exercise", +1),
    ("Dividend", +1),
    ("Interest", +1),
    ("Lending", +1),
    ("Return of Capital", +1),
    ("Event Contract Transfer", +1),
    ("Buy", -1),
    ("Option Buy", -1),
    ("Withdrawal", -1),
    ("Fee", -1),
    ("Tax", -1),
    ("Return of Capital Reversal", -1),
    ("Event Contract Transfer Out", -1),
    # Share-only events never move cash.
    ("Option Expire", 0),
    ("Split", 0),
    ("Spinoff", 0),
    ("Merger", 0),
    ("Neutral", 0),
])
def test_robinhood_cash_direction(action, expected):
    assert usd_effect(_t("2024-01-01", action, 100.0)) == expected * 100.0


def test_share_credits_do_not_count_as_cash():
    """Reward and Conversion each appear BOTH as a cash credit (USD
    symbol) and as a share credit — a free-share promo, an Apex→RHS
    migration — where ``amount`` is share value, not cash.  Only the
    USD-symbol form moves the balance."""
    assert usd_effect(_t("2024-01-01", "Reward", 5.0, symbol="USD")) == 5.0
    assert usd_effect(_t("2024-01-01", "Reward", 5.0, symbol="BRK.B")) == 0.0
    assert usd_effect(_t("2024-01-01", "Conversion", 100.0, symbol="USD")) == 100.0
    assert usd_effect(_t("2024-01-01", "Conversion", 100.0, symbol="S")) == 0.0


def test_inflows_apply_before_outflows_within_a_date():
    """Brokers routinely list a day's buys ahead of the deposit that
    funded them.  Ordering by the raw list would drive the running
    balance negative mid-day; the clamp in balance_at would then hide
    the real balance on exactly the days it matters."""
    txns = [
        _t("2024-01-10", "Buy", 9000.0),      # listed first...
        _t("2024-01-10", "Deposit", 10000.0),  # ...but funded by this
    ]
    series = usd_series(txns, "Robinhood")
    assert balance_at(series, "2024-01-10") == pytest.approx(1000.0)


def test_balance_at_walks_back_and_clamps():
    txns = [
        _t("2024-01-10", "Deposit", 1000.0),
        _t("2024-02-10", "Buy", 400.0),
    ]
    series = usd_series(txns, "Robinhood")
    assert balance_at(series, "2024-01-05") == 0.0      # before any activity
    assert balance_at(series, "2024-01-20") == pytest.approx(1000.0)  # walk back
    assert balance_at(series, "2024-06-01") == pytest.approx(600.0)

    # Negative excursions clamp: a brokerage balance can't go negative
    # without margin, which fin doesn't model.  Understating beats
    # inventing value.
    assert balance_at(usd_series([_t("2024-01-10", "Buy", 50.0)],
                                 "Robinhood"), "2024-01-10") == 0.0


def test_round_trip_preserves_the_gain():
    """The core reason the bridge exists: buy → sell must leave the
    realized gain visible as cash instead of vanishing.  Without it the
    sale reads as a value drop with no offsetting external flow, and
    Modified-Dietz books a phantom market loss."""
    txns = [
        _t("2024-01-01", "Deposit", 10000.0),
        _t("2024-01-02", "Buy", 10000.0, symbol="AAPL"),
        _t("2024-06-01", "Sell", 12500.0, symbol="AAPL"),
    ]
    series = usd_series(txns, "Robinhood")
    assert balance_at(series, "2024-01-02") == pytest.approx(0.0)
    assert balance_at(series, "2024-06-01") == pytest.approx(12500.0)


def test_external_funding_synth_covers_truncated_history():
    """Broker exports are often truncated at the first TRADE, not the
    first ACH — you cannot buy stock with a $0 balance, so the funding
    happened even though no row records it.

    Without the synth the reconstructed balance runs negative,
    ``balance_at`` clamps it to 0, and the bridge contributes nothing
    across exactly the early window where the account is small and
    every percentage swing gets amplified into the chain-linked
    lifetime TWR.
    """
    from src.cash_bridge import synthesize_external_funding

    txns = [
        _t("2017-10-06", "Buy", 80.0, symbol="MSFT"),   # no funding row
        _t("2017-10-27", "Sell", 100.0, symbol="MSFT"),
    ]
    out = synthesize_external_funding(txns, "Robinhood")
    synth = [t for t in out if t["source"].startswith("auto-reconcile")]
    assert len(synth) == 1
    assert synth[0]["amount"] == pytest.approx(80.0)
    assert synth[0]["action"] == "Deposit"
    assert synth[0]["symbol"] == "USD"

    # Balance is now non-negative throughout, so nothing is clamped
    # away: the full sale proceeds (returned basis + gain) survive as
    # cash instead of the position value simply vanishing.
    series = usd_series(out, "Robinhood")
    assert balance_at(series, "2017-10-06") == pytest.approx(0.0)
    assert balance_at(series, "2017-10-27") == pytest.approx(100.0)

    # Without the synth the same sale is invisible until it climbs back
    # above zero — the clamp swallows it.
    unfunded = usd_series(txns, "Robinhood")
    assert balance_at(unfunded, "2017-10-06") == 0.0   # truly -80, clamped
    assert balance_at(unfunded, "2017-10-27") == pytest.approx(20.0)


def test_external_funding_synth_ignores_recycled_cash():
    """Only NEW cumulative lows are external funding.  A dip that later
    inflows already cover is recycled cash — synthesizing for it would
    inflate net_contributed and understate return."""
    from src.cash_bridge import synthesize_external_funding

    txns = [
        _t("2024-01-01", "Deposit", 1000.0),
        _t("2024-02-01", "Buy", 1000.0, symbol="AAPL"),
        _t("2024-03-01", "Sell", 1200.0, symbol="AAPL"),
        _t("2024-04-01", "Buy", 1100.0, symbol="MSFT"),   # covered by the sale
    ]
    out = synthesize_external_funding(txns, "Robinhood")
    assert [t for t in out if t["source"].startswith("auto-reconcile")] == []


def test_external_funding_synth_leaves_total_return_unchanged():
    """The synth raises reconstructed cash and net_contributed by the
    same amount, so it must not move total return — it recovers a
    missing deposit, it does not create profit."""
    from src.cash_bridge import synthesize_external_funding
    from src.basis import txn_external_cash_flow

    txns = [_t("2017-10-06", "Buy", 80.0, symbol="MSFT")]
    out = synthesize_external_funding(txns, "Robinhood")
    added_flow = sum(txn_external_cash_flow(t) for t in out
                     if t["source"].startswith("auto-reconcile"))
    # Compare the RAW running balance (balance_at clamps, which would
    # mask the very difference under test).
    added_cash = (usd_series(out, "Robinhood")[-1][1]
                  - usd_series(txns, "Robinhood")[-1][1])
    assert added_flow == pytest.approx(80.0)
    assert added_cash == pytest.approx(80.0)


def test_bridge_reaches_the_holdings_table_too():
    """The bridge must feed the Holdings build, not just the history
    snapshots.

    The Overview header reads the latest snapshot (bridge applied) while
    the Holdings table, allocation donut, sector split and concentration
    view are all built from `balances` (bridge NOT applied).  Without
    this injection those two halves of the dashboard disagree by the
    full bridged amount.

    It stayed invisible while Coinbase was the only bridged group —
    its reconstructed balance ends at ~$0 by construction — so a
    brokerage account simply holding uninvested cash is the first case
    that exposes it.
    """
    from src.cash_bridge import inject_into_holdings

    txns = [
        _t("2024-01-01", "Deposit", 10000.0),
        _t("2024-01-02", "Buy", 10000.0, symbol="AAPL"),
        _t("2024-06-01", "Sell", 12500.0, symbol="AAPL"),
    ]
    balances: dict = {}
    last_prices: dict = {}
    basis: dict = {}
    injected = inject_into_holdings(txns, balances, last_prices, basis,
                                    "2024-06-30")

    assert injected == pytest.approx(12500.0)
    assert balances[("Robinhood", "USD")] == pytest.approx(12500.0)
    # Cash basis is face value, matching what the snapshot walker adds
    # to total_cost_basis — so the row shows $0 unrealized, not a
    # phantom gain equal to the whole balance.
    assert basis[("Robinhood", "USD")] == pytest.approx(12500.0)
    assert last_prices["USD"] == 1.0


def test_holdings_injection_skips_sub_threshold_balances():
    from src.cash_bridge import inject_into_holdings

    txns = [
        _t("2024-01-01", "Deposit", 1000.0),
        _t("2024-01-02", "Buy", 999.60, symbol="AAPL"),
    ]
    balances: dict = {}
    last_prices: dict = {}
    basis: dict = {}
    injected = inject_into_holdings(txns, balances, last_prices, basis,
                                    "2024-06-30")
    assert injected == 0.0
    assert balances == {} and basis == {}
    # No phantom USD price when nothing was injected.
    assert "USD" not in last_prices


def test_groups_are_tracked_independently():
    txns = [
        _t("2024-01-01", "Deposit", 1000.0, group="Robinhood"),
        _t("2024-01-01", "Deposit", 700.0, group="Coinbase"),
    ]
    series = all_series(txns)
    assert balance_at(series["Robinhood"], "2024-01-01") == pytest.approx(1000.0)
    assert balance_at(series["Coinbase"], "2024-01-01") == pytest.approx(700.0)
