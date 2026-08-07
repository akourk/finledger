"""Every high-severity data_health check must actually FIRE.

These checks are the suite's own safety net: `conftest.py` sets
`FIN_ASSERT_INVARIANTS=1`, so `check_invariants` promotes each
high-severity issue to a hard `InvariantViolation` and every other test
in the suite leans on that.  Before this module, **no test had ever
tripped a single one of them** — the checks ran on every pipeline test
and returned clean every time, so a change that quietly disabled a
detector would ship green.  (Verified with coverage: not one of the
violation-constructing branches was executed by the suite.)

Each check gets two assertions:

* a deliberately-violating input -> MUST report an issue
* a clean near-miss             -> MUST stay silent

The near-miss is the half that stops this file from degenerating into
"assert the function returns something".  A check hard-wired to always
fire would pass the first assertion and fail the second.

All figures here are synthetic round numbers.  Never put real portfolio
values in a test fixture (see CLAUDE.md).
"""

from __future__ import annotations

import pytest

from src.analytics import data_health as D


def _h(**kw) -> dict:
    """A holdings_by_account row with the identity fields filled in."""
    return {"account_group": "Broker", "symbol": "SYM", **kw}


# (id, check_fn, violating_args, clean_args)
GUARD_CASES = [
    (
        "future_dated",
        D._check_future_dated,
        ([{"date": "2099-01-01", "account_group": "Broker",
           "action": "Buy", "symbol": "SYM"}],),
        ([{"date": "2020-01-01", "account_group": "Broker",
           "action": "Buy", "symbol": "SYM"}],),
    ),
    (
        # A recognised file with data rows that parsed to ZERO: an entire
        # account absent from the portfolio, with no transaction left
        # behind to notice it.  Clean is an EMPTY report — the check only
        # ever receives files that already had a finding, so there is no
        # "clean file" entry shape.  The warn-vs-high split has its own
        # tests in test_parser_silent_drop.py.
        "parser_dropped_rows",
        D._check_parser_dropped_rows,
        ([{"file": "broker-1.csv", "broker": "robinhood", "parsed": 0,
           "dropped": 12, "empty_with_data": True}],),
        ([],),
    ),
    (
        "negative_cost_basis",
        D._check_negative_cost_basis,
        ([_h(cost_basis=-100.0)],),
        ([_h(cost_basis=100.0)],),
    ),
    (
        "snapshot_rollup",
        D._check_snapshot_rollup,
        ([{"date": "2024-01-01", "total": 1000.0,
           "by_account_group": {"Broker": 1.0}}],),
        ([{"date": "2024-01-01", "total": 1000.0,
           "by_account_group": {"Broker": 1000.0}}],),
    ),
    (
        "lots_holdings_basis_parity",
        D._check_lots_holdings_basis_parity,
        ({"lots": {"positions": [_h(cost_basis=100.0)]}}, [_h(cost_basis=200.0)]),
        ({"lots": {"positions": [_h(cost_basis=100.0)]}}, [_h(cost_basis=100.0)]),
    ),
    (
        "value_qty_price_consistency",
        D._check_value_qty_price_consistency,
        ([_h(quantity=10.0, price=10.0, value=5000.0)],),
        ([_h(quantity=10.0, price=10.0, value=100.0)],),
    ),
    (
        "lot_queue_parity",
        D._check_lot_queue_parity,
        ([], [_h(quantity=5.0, cost_basis=999.0)]),
        ([], [_h(quantity=5.0, cost_basis=0.0)]),
    ),
    (
        "history_holdings_basis_parity",
        D._check_history_holdings_basis_parity,
        ([{"date": "2024-01-01", "positions": [_h(cost_basis=100.0, quantity=5.0)]}],
         [_h(cost_basis=500.0, quantity=5.0)]),
        ([{"date": "2024-01-01", "positions": [_h(cost_basis=100.0, quantity=5.0)]}],
         [_h(cost_basis=100.0, quantity=5.0)]),
    ),
    (
        "cash_flow_conservation",
        D._check_cash_flow_conservation,
        ([{"cash_flow": 100.0}], {"net_contributed": 5000.0}),
        ([{"cash_flow": 100.0}], {"net_contributed": 100.0}),
    ),
    (
        "snapshot_date_monotonicity",
        D._check_snapshot_date_monotonicity,
        ([{"date": "2024-05-01"}, {"date": "2024-01-01"}],),
        ([{"date": "2024-01-01"}, {"date": "2024-05-01"}],),
    ),
    (
        "holding_days_non_negative",
        D._check_holding_days_non_negative,
        ([{"date": "2024-01-01", "symbol": "SYM", "action": "Sell",
           "holding_days": -5}],),
        ([{"date": "2024-01-01", "symbol": "SYM", "action": "Sell",
           "holding_days": 5}],),
    ),
]

_IDS = [c[0] for c in GUARD_CASES]


class TestHighSeverityGuardsFire:

    @pytest.mark.parametrize("_id,fn,bad_args,_clean", GUARD_CASES, ids=_IDS)
    def test_fires_on_violating_input(self, _id, fn, bad_args, _clean):
        issues = fn(*bad_args)
        assert issues, (
            f"{fn.__name__} stayed SILENT on a deliberately-violating input — "
            "a detector that cannot detect"
        )
        assert all(i["severity"] == "high" for i in issues), (
            f"{fn.__name__} reported a non-high severity; check_invariants "
            "only promotes 'high', so this would no longer be a hard error"
        )
        for i in issues:
            assert i.get("kind"), "every issue needs a stable `kind`"
            assert i.get("message"), "every issue needs a human-readable message"

    @pytest.mark.parametrize("_id,fn,_bad,clean_args", GUARD_CASES, ids=_IDS)
    def test_silent_on_clean_input(self, _id, fn, _bad, clean_args):
        assert fn(*clean_args) == [], (
            f"{fn.__name__} fired on CLEAN input — a check that always fires "
            "protects nothing and trains the reader to ignore it"
        )


def test_every_high_severity_check_is_covered_here():
    """Guard against a new high-severity check landing unprotected.

    Enumerates the module's `_check_*` functions that can emit
    `severity: "high"` and asserts each one appears above.  Without
    this, adding check #11 silently reverts this file's guarantee for
    that check.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(D))
    high_severity_checks = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("_check_"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            for k, v in zip(sub.keys, sub.values):
                if (isinstance(k, ast.Constant) and k.value == "severity"
                        and isinstance(v, ast.Constant) and v.value == "high"):
                    high_severity_checks.add(node.name)

    covered = {fn.__name__ for _id, fn, _b, _c in GUARD_CASES}
    missing = high_severity_checks - covered
    assert not missing, (
        "high-severity data_health check(s) with no fire/near-miss test: "
        f"{sorted(missing)} — add them to GUARD_CASES"
    )
