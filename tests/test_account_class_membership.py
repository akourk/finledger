"""Account-class membership comes from the user's metadata, not a literal.

`_shared` used to answer "is this a savings account?" from a hardcoded
frozenset while every other site in the codebase — history,
pipeline_stages, balance_anchor, `_value_at_date` — read
`config.ACCOUNT_TYPES`.  A second savings account the user had declared
was therefore savings to the Holdings table and investment capital to
`_account_filter_sets`, which put a HYSA inside the "Investments" filter
that exists precisely to keep HYSA yield out of equity-benchmark
comparisons.

These pin the fix and, just as importantly, the shape of it: membership
is ADDITIVE.  Metadata that classifies only some accounts must not
silently reclassify the rest — dropping a 401K out of the retirement
class would mis-state contributions, Roth eligibility and the Monte
Carlo horizon, which is a far worse failure than the one being fixed.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def account_types():
    """Mutate `config.ACCOUNT_TYPES` in place and restore afterwards.

    In place because that is what `main()` does (`ACCOUNT_TYPES.update`),
    and because the module-level `from ..config import ACCOUNT_TYPES`
    bindings elsewhere would not see a rebind.
    """
    from src.config import ACCOUNT_TYPES
    before = dict(ACCOUNT_TYPES)
    yield ACCOUNT_TYPES
    ACCOUNT_TYPES.clear()
    ACCOUNT_TYPES.update(before)


def _holdings(*groups):
    return [{"account_group": g, "symbol": "AAA", "value": 100.0}
            for g in groups]


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def test_declared_savings_account_joins_the_savings_class(account_types):
    from src.analytics._shared import savings_groups
    account_types.update({"Some Credit Union": "Savings"})
    assert "Some Credit Union" in savings_groups()


def test_membership_is_additive_not_replacing(account_types):
    """Metadata that classifies only some accounts must leave the rest
    where they were."""
    from src.analytics._shared import (
        savings_groups, retirement_groups,
        _DEFAULT_SAVINGS_GROUPS, _DEFAULT_RETIREMENT_GROUPS,
    )
    account_types.update({"Some Credit Union": "Savings"})
    assert _DEFAULT_SAVINGS_GROUPS <= savings_groups()
    # A 401K the user never wrote an Account Type row for must not fall
    # out of the retirement class just because someone else's account
    # got one.
    assert _DEFAULT_RETIREMENT_GROUPS <= retirement_groups()


def test_no_metadata_falls_back_to_the_defaults(account_types):
    """`config.ACCOUNT_TYPES` is empty until `main()` applies the parsed
    metadata, so a bare import path must still classify something."""
    from src.analytics._shared import (
        savings_groups, retirement_groups,
        _DEFAULT_SAVINGS_GROUPS, _DEFAULT_RETIREMENT_GROUPS,
    )
    account_types.clear()
    assert savings_groups() == _DEFAULT_SAVINGS_GROUPS
    assert retirement_groups() == _DEFAULT_RETIREMENT_GROUPS


def test_membership_is_read_at_call_time(account_types):
    """The classification must not be a module-level snapshot — this
    module is imported long before `main()` loads the metadata."""
    from src.analytics._shared import savings_groups
    account_types.clear()
    assert "Late Arrival" not in savings_groups()
    account_types["Late Arrival"] = "Savings"
    assert "Late Arrival" in savings_groups()


# ---------------------------------------------------------------------------
# What the classification is actually for
# ---------------------------------------------------------------------------

def test_declared_savings_is_excluded_from_investments_and_taxable(account_types):
    """The bug, stated as a test: a declared savings account inside
    "Investments" dilutes exactly the equity-benchmark comparison that
    filter exists to keep clean."""
    from src.analytics._shared import _account_filter_sets
    account_types.update({
        "Robinhood": "Taxable", "Coinbase": "Taxable",
        "Some Credit Union": "Savings", "Apple Savings": "Savings",
    })
    filters = _account_filter_sets(
        _holdings("Robinhood", "Coinbase", "Some Credit Union", "Apple Savings"))
    assert "Some Credit Union" not in filters["Investments"]
    assert "Some Credit Union" not in filters["Taxable"]
    assert filters["Taxable"] == frozenset({"Robinhood", "Coinbase"})


def test_savings_filter_exists_for_two_or_more_savings_accounts(account_types):
    """Without it the Holdings tab's Savings row has no filter to match:
    one savings account is covered by its own per-account filter, two or
    more were covered by nothing."""
    from src.analytics._shared import _account_filter_sets
    account_types.update({
        "Robinhood": "Taxable",
        "Apple Savings": "Savings", "Some Credit Union": "Savings",
    })
    filters = _account_filter_sets(
        _holdings("Robinhood", "Apple Savings", "Some Credit Union"))
    assert filters["Savings"] == frozenset({"Apple Savings", "Some Credit Union"})


def test_single_savings_account_needs_no_combined_filter(account_types):
    """Symmetric with Retirement and Taxable — its own per-account filter
    already covers the same set."""
    from src.analytics._shared import _account_filter_sets
    account_types.update({"Robinhood": "Taxable", "Apple Savings": "Savings"})
    filters = _account_filter_sets(_holdings("Robinhood", "Apple Savings"))
    assert "Savings" not in filters
    assert filters["Apple Savings"] == frozenset({"Apple Savings"})


def test_every_holdings_group_is_covered_by_some_filter(account_types):
    """The Holdings tab matches a grouped row to a filter by ACCOUNT-GROUP
    SET, so a class whose set no filter carries silently renders no
    return.  Each of the three type classes must have a covering set."""
    from src.analytics._shared import _account_filter_sets
    groups = {"Robinhood": "Taxable", "Coinbase": "Taxable",
              "401K": "Retirement", "Roth IRA": "Retirement",
              "Apple Savings": "Savings", "Some Credit Union": "Savings"}
    account_types.update(groups)
    filters = _account_filter_sets(_holdings(*groups))
    sets = {frozenset(v) for v in filters.values() if v}
    by_type: dict[str, set] = {}
    for g, t in groups.items():
        by_type.setdefault(t, set()).add(g)
    for t, want in by_type.items():
        assert frozenset(want) in sets, f"no filter covers the {t} class"


def test_declared_retirement_account_gets_its_contributions_classified(account_types):
    """Contribution classification gates on retirement membership, so a
    custom-named retirement account was invisible to Roth eligibility and
    the contributions-by-year table."""
    from src.analytics._shared import classify_retirement_contribution
    txn = {"account_group": "My Solo 401k", "action": "Contribution",
           "amount": 1000.0, "date": "2024-03-01"}
    assert classify_retirement_contribution(txn)["is_contrib"] is False
    account_types.update({"My Solo 401k": "Retirement"})
    got = classify_retirement_contribution(txn)
    assert got["is_contrib"] is True
    assert got["amount"] == pytest.approx(1000.0)
    assert got["year"] == "2024"
