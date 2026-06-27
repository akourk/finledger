"""Sanity tests for the centralized action catalog.

These pin down the invariants downstream modules depend on, so a stray
edit to src/actions.py doesn't silently break (e.g.) the basis walker.
"""

from __future__ import annotations


def test_every_canonical_action_has_a_basis_effect():
    from src.actions import ACTIONS, BASIS_EFFECTS
    for name in ACTIONS:
        assert name in BASIS_EFFECTS, f"missing basis effect for {name}"


def test_subtract_actions_includes_known_movers():
    """Don't drop the actions everyone relies on for negative balance flow."""
    from src.actions import SUBTRACT_ACTIONS
    expected = {"Sell", "Withdrawal", "Transfer Out", "Distribution",
                "Fee", "Tax", "Option Sell", "Option Expire", "Option Exercise",
                "Contribution Reversal"}
    missing = expected - SUBTRACT_ACTIONS
    assert not missing, f"SUBTRACT_ACTIONS lost members: {missing}"


def test_cash_flow_in_includes_contributions():
    from src.actions import CASH_ADD_ACTIONS
    assert "Contribution" in CASH_ADD_ACTIONS
    assert "Deposit" in CASH_ADD_ACTIONS


def test_cash_flow_out_includes_reversal():
    from src.actions import CASH_SUB_ACTIONS
    assert "Withdrawal" in CASH_SUB_ACTIONS
    assert "Distribution" in CASH_SUB_ACTIONS
    assert "Contribution Reversal" in CASH_SUB_ACTIONS


def test_basis_effects_match_legacy_classifications():
    """These specific classifications encode invariants from years of
    bug fixes — pin them down."""
    from src.actions import BASIS_EFFECTS
    assert BASIS_EFFECTS["Buy"] == "add"
    assert BASIS_EFFECTS["Sell"] == "remove"
    assert BASIS_EFFECTS["Split"] == "split"
    assert BASIS_EFFECTS["Spinoff"] == "zero_basis"
    assert BASIS_EFFECTS["Conversion"] == "zero_basis"
    # USD-side cash flows that the symbol-aware override catches first;
    # action-level classification is what handles the crypto / share
    # cases that DO need basis tracking.
    assert BASIS_EFFECTS["Deposit"] == "transfer_in", \
        "Deposit basis must remain transfer_in (Coinbase wallet arrivals)"
    assert BASIS_EFFECTS["Withdrawal"] == "ignore", \
        "Withdrawal basis must remain ignore (USD cash out)"
    assert BASIS_EFFECTS["Contribution Reversal"] == "remove"


def test_neutral_action_is_neutral_everywhere():
    from src.actions import ACTIONS
    a = ACTIONS["Neutral"]
    assert a.balance == "neutral"
    assert a.basis == "ignore"
    assert a.cash_flow == "ignore"


def test_robinhood_roc_futswp_misc_classification():
    """FUTSWP (event-contract inter-entity cash transfer), ROC (return
    of capital), and MISC (promotional cash reward) used to fall
    through normalize untyped and trip the action_catalog_coverage
    data-health flag.  Pin their intended handling:

    - FUTSWP → Event Contract Transfer: fully inert (neutral/ignore/
      ignore) so prediction-markets activity is excluded from every
      performance metric and net_contributed.
    - ROC → Return of Capital: no share/cash-flow effect, not income.
    - MISC → Reward: counts as reward income.
    """
    from src.normalize import normalize_action
    from src.actions import ACTIONS, CASH_ADD_ACTIONS, CASH_SUB_ACTIONS
    from src.analytics._shared import INCOME_ACTION_KINDS

    def norm(code):
        return normalize_action(
            {"action": code, "account_group": "Robinhood", "symbol": "USD"})

    # Every raw code resolves to a catalog action (clears the flag).
    for code in ("FUTSWP", "ROC", "MISC"):
        assert norm(code) in ACTIONS, f"{code} not mapped to a catalog action"

    ect = ACTIONS[norm("FUTSWP")]
    assert (ect.name, ect.balance, ect.basis, ect.cash_flow) == \
        ("Event Contract Transfer", "neutral", "ignore", "ignore")
    # Excluded from cash-flow accounting entirely.
    assert ect.name not in CASH_ADD_ACTIONS and ect.name not in CASH_SUB_ACTIONS

    roc = ACTIONS[norm("ROC")]
    assert (roc.name, roc.balance, roc.basis, roc.cash_flow) == \
        ("Return of Capital", "neutral", "ignore", "neutral")
    assert roc.name not in INCOME_ACTION_KINDS, "ROC is not income"
    assert roc.name not in CASH_ADD_ACTIONS and roc.name not in CASH_SUB_ACTIONS

    assert norm("MISC") == "Reward"
    assert INCOME_ACTION_KINDS.get("Reward") == "rewards"


def test_action_colors_complete():
    """Every catalog entry has a color so the dashboard never gets a
    fallback gray for a known action."""
    from src.actions import ACTIONS, ACTION_COLORS
    for name in ACTIONS:
        assert ACTION_COLORS[name].startswith("#"), \
            f"{name} has no color"


def test_external_cash_flow_helper_handles_all_carve_outs():
    """``basis.txn_external_cash_flow`` is the single source of truth for
    cash-flow accounting.  Three carve-outs (Contribution Reversal as
    cash-out, Roth/Rollover IRA Distribution as no-op, USAA Buy with
    contribution-marker description as cash-in) must all be honored —
    each was a real bug at some point.  Pin the behavior here so it
    can't silently regress."""
    from src.basis import txn_external_cash_flow

    # Simple inflow
    assert txn_external_cash_flow({
        "action": "Deposit", "amount": 1000.0, "account_group": "Robinhood",
    }) == 1000.0
    assert txn_external_cash_flow({
        "action": "Contribution", "amount": 500.0, "account_group": "401K",
    }) == 500.0

    # Simple outflow
    assert txn_external_cash_flow({
        "action": "Withdrawal", "amount": 250.0, "account_group": "Apple Savings",
    }) == -250.0

    # Carve-out 1: Contribution Reversal counts as outflow (catalog says
    # cash_flow=out, so it MUST be picked up — was missed by the old
    # local-set in basis.compute_cash_summary)
    assert txn_external_cash_flow({
        "action": "Contribution Reversal", "amount": 75.0,
        "account_group": "Rollover IRA",
    }) == -75.0

    # Carve-out 2: Distribution on Roth/Rollover IRA = 0 (rollover, not
    # a real withdrawal)
    assert txn_external_cash_flow({
        "action": "Distribution", "amount": 5000.0,
        "account_group": "Rollover IRA",
    }) == 0.0
    assert txn_external_cash_flow({
        "action": "Distribution", "amount": 5000.0,
        "account_group": "Roth IRA",
    }) == 0.0
    # But Distribution on a non-rollover account IS a withdrawal
    assert txn_external_cash_flow({
        "action": "Distribution", "amount": 5000.0,
        "account_group": "401K",
    }) == -5000.0

    # Carve-out 3: USAA Buy with contribution-marker description = inflow
    assert txn_external_cash_flow({
        "action": "Buy", "amount": 3000.0, "account_group": "Roth IRA",
        "description": "PRIOR YEAR CONTRIBUTION",
    }) == 3000.0
    assert txn_external_cash_flow({
        "action": "Buy", "amount": 6000.0, "account_group": "Roth IRA",
        "description": "Current Year Contribution",  # case-insensitive
    }) == 6000.0
    # But a regular Buy is NOT a cash flow event
    assert txn_external_cash_flow({
        "action": "Buy", "amount": 3000.0, "account_group": "Roth IRA",
        "description": "REINVEST LT CAP GAIN DIST",
    }) == 0.0
    assert txn_external_cash_flow({
        "action": "Buy", "amount": 3000.0, "account_group": "Robinhood",
    }) == 0.0

    # Trade Settle In/Out are intra-account, NOT cash flow events
    assert txn_external_cash_flow({
        "action": "Trade Settle In", "amount": 500.0,
        "account_group": "Coinbase",
    }) == 0.0


def test_trade_settle_actions_are_cash_flow_neutral():
    """Coinbase Pro's match-pair USD legs map to Trade Settle In/Out.
    These MUST stay out of CASH_ADD_ACTIONS / CASH_SUB_ACTIONS — they're
    intra-account cash settlement, not external money in/out.  If they
    ever leaked into those sets, net_contributed and the SPY benchmark
    series would inflate by every crypto trade amount."""
    from src.actions import (
        ACTIONS, CASH_ADD_ACTIONS, CASH_SUB_ACTIONS, BASIS_EFFECTS,
    )
    for name in ("Trade Settle In", "Trade Settle Out"):
        assert name in ACTIONS, f"{name} missing from catalog"
        assert name not in CASH_ADD_ACTIONS, \
            f"{name} must not be a cash-in action"
        assert name not in CASH_SUB_ACTIONS, \
            f"{name} must not be a cash-out action"
        assert BASIS_EFFECTS[name] == "ignore", \
            f"{name} basis effect must be 'ignore'"
    assert ACTIONS["Trade Settle In"].balance  == "add"
    assert ACTIONS["Trade Settle Out"].balance == "subtract"
