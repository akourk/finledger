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


# ---------------------------------------------------------------------------
# warn / info checks
# ---------------------------------------------------------------------------
#
# These do not raise `InvariantViolation`, so nothing in the suite leans
# on them the way it leans on the `high` ones — which is exactly why they
# were the last to get coverage, and exactly why they could rot without
# a single test noticing. A diagnostic that has quietly stopped
# diagnosing is worse than no diagnostic: the empty panel reads as
# "all clear".
#
# Same two-sided contract as above: fires on a violation, silent on a
# near-miss.

def _snap(**kw) -> dict:
    return {"date": "2024-01-01", "total": 1000.0, **kw}


SOFT_GUARD_CASES = [
    (
        "open_options_past_expiration",
        D._check_open_options_past_expiration,
        ({"options": {"open_contracts": [
            {"symbol": "X 1/1/2020 Call $5.00", "expiry": "2020-01-01"}]}},),
        ({"options": {"open_contracts": [
            {"symbol": "X 1/1/2099 Call $5.00", "expiry": "2099-01-01"}]}},),
    ),
    (
        # The by-year rows are rounded to cents, so the tolerance scales
        # with the number of rows. The violating case has to clear it by
        # a wide margin to be a real inconsistency rather than rounding.
        "realized_gain_reconciliation",
        D._check_realized_gain_reconciliation,
        ([{"realized_gain": 500.0}],
         {"tax": {"realized_by_year": [{"st": 0.0, "lt": 0.0}]}}),
        ([{"realized_gain": 500.0}],
         {"tax": {"realized_by_year": [{"st": 200.0, "lt": 300.0}]}}),
    ),
    (
        # cv <= 0 is what makes a deeply negative net_contributed
        # unexplainable: with value still in the account, the negative is
        # just gains withdrawn.
        "per_account_negative_contributions",
        D._check_per_account_negative_contributions,
        ([{"account_group": "Broker", "action": "Withdrawal",
           "amount": 5000.0, "symbol": "USD"}], []),
        ([{"account_group": "Broker", "action": "Withdrawal",
           "amount": 500.0, "symbol": "USD"}], []),
    ),
    (
        "priced_coverage",
        D._check_priced_coverage,
        ([_snap(priced_pct=0.50)],),
        ([_snap(priced_pct=1.0)],),
    ),
    (
        "orphan_zero_qty_basis",
        D._check_orphan_zero_qty_basis,
        ([_h(quantity=0.0, cost_basis=100.0)],),
        ([_h(quantity=0.0, cost_basis=0.0)],),
    ),
    (
        # Threshold is max($50k, 20% of the all-time peak), so the
        # fixture's peak has to stay small enough for the floor to bind.
        "net_contributed_monotonicity",
        D._check_net_contributed_monotonicity,
        ([_snap(net_contributed=0.0), _snap(net_contributed=100_000.0)],),
        ([_snap(net_contributed=0.0), _snap(net_contributed=1_000.0)],),
    ),
    (
        "twr_sanity_bounds",
        D._check_twr_sanity_bounds,
        ({"performance_by_filter": {
            "Total": {"annual": [{"year": 2024, "twr_pct": 9999.0}]}}},),
        ({"performance_by_filter": {
            "Total": {"annual": [{"year": 2024, "twr_pct": 12.0}]}}},),
    ),
    (
        "action_catalog_coverage",
        D._check_action_catalog_coverage,
        ([{"action": "Definitely Not A Canonical Action"}],),
        ([{"action": "Buy"}],),
    ),
    (
        "broker_cash_balance",
        D._check_broker_cash_balance,
        ([{"date": "2025-01-01", "account_group": "Coinbase", "symbol": "USD",
           "action": "Withdrawal", "amount": 500.0}],),
        ([{"date": "2025-01-01", "account_group": "Coinbase", "symbol": "USD",
           "action": "Deposit", "amount": 500.0}],),
    ),
    (
        "held_symbol_sector_coverage",
        D._check_held_symbol_sector_coverage,
        ([_h(sector="Other", value=5000.0)],),
        ([_h(sector="Technology", value=5000.0)],),
    ),
    (
        # basis-per-unit vs price more than 100x apart. The clean case is
        # the SAME position priced sanely, so the fixture cannot pass by
        # being filtered out on size.
        "per_position_basis_sanity",
        D._check_per_position_basis_sanity,
        ([_h(quantity=10.0, price=1.0, cost_basis=100_000.0)],),
        ([_h(quantity=10.0, price=1000.0, cost_basis=10_000.0)],),
    ),
    (
        "unbridged_retirement_distribution",
        D._check_unbridged_retirement_distribution,
        ([{"date": "2025-04-10", "account_group": "Rollover IRA",
           "account_type": "Retirement", "action": "Distribution",
           "amount": 50_000.0}],
         {"rollover_bridges": []}),
        ([{"date": "2025-04-10", "account_group": "Rollover IRA",
           "account_type": "Retirement", "action": "Distribution",
           "amount": 50_000.0}],
         {"rollover_bridges": [{"group": "Rollover IRA",
                                "start_date": "2025-04-10",
                                "end_date": "2025-05-12",
                                "amount": 50_000.0}]}),
    ),
]

_SOFT_IDS = [c[0] for c in SOFT_GUARD_CASES]


class TestSoftSeverityGuardsFire:

    @pytest.mark.parametrize("_id,fn,bad_args,_clean", SOFT_GUARD_CASES,
                             ids=_SOFT_IDS)
    def test_fires_on_violating_input(self, _id, fn, bad_args, _clean):
        issues = fn(*bad_args)
        assert issues, (
            f"{fn.__name__} stayed SILENT on a deliberately-violating "
            "input — a diagnostic that cannot diagnose, and one nothing "
            "else in the suite would notice, since warn/info never raise"
        )
        assert all(i["severity"] in ("warn", "info") for i in issues), (
            f"{fn.__name__} reported 'high' — it would now hard-fail every "
            "pipeline test, and belongs in GUARD_CASES instead"
        )
        for i in issues:
            assert i.get("kind"), "every issue needs a stable `kind`"
            assert i.get("message"), "every issue needs a human-readable message"

    @pytest.mark.parametrize("_id,fn,_bad,clean_args", SOFT_GUARD_CASES,
                             ids=_SOFT_IDS)
    def test_silent_on_clean_input(self, _id, fn, _bad, clean_args):
        assert fn(*clean_args) == [], (
            f"{fn.__name__} fired on CLEAN input — a permanently-lit "
            "warning is indistinguishable from no warning"
        )


def test_every_check_is_wired_into_compute_data_health():
    """A check that is never called protects nothing.

    Mutation found this gap on `_check_parser_dropped_rows`: deleting its
    `issues.extend(...)` line left the whole suite green, because every
    test called the function directly. The detector worked perfectly and
    reached nobody — which is the same failure the parser checks exist to
    catch, one level up.
    """
    import ast
    import inspect

    src = inspect.getsource(D)
    defined = {n.name for n in ast.parse(src).body
               if isinstance(n, ast.FunctionDef)
               and n.name.startswith("_check_")}
    body = src[src.index("def compute_data_health"):]
    unwired = sorted(n for n in defined if n not in body)
    assert not unwired, (
        f"data_health check(s) never called by compute_data_health: "
        f"{unwired} — they cannot reach the dashboard"
    )


def test_every_soft_severity_check_is_covered_here():
    """Companion to the high-severity meta-guard.

    Without it, a new warn/info check lands with no test at all — and
    unlike a `high` one, nothing else in the suite would ever exercise
    its violating branch.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(D))
    soft_checks = set()
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
                        and isinstance(v, ast.Constant)
                        and v.value in ("warn", "info")):
                    soft_checks.add(node.name)

    covered = ({fn.__name__ for _id, fn, _b, _c in SOFT_GUARD_CASES}
               | {fn.__name__ for _id, fn, _b, _c in GUARD_CASES}
               | set(_COVERED_BY_A_FIXTURE_CLASS))
    missing = soft_checks - covered
    assert not missing, (
        "warn/info data_health check(s) with no fire/near-miss test: "
        f"{sorted(missing)} — add them to SOFT_GUARD_CASES"
    )
# Checks that need real files on disk cannot live in the table above --
# they take a `cache_dir` and read cache sidecars. Each one named here
# MUST have the listed class in this module, so the exemption cannot
# become a way to skip coverage.
_COVERED_BY_A_FIXTURE_CLASS = {
    "_check_held_symbol_price_health": "TestHeldSymbolPriceHealth",
}


def test_the_fixture_class_exemptions_are_real():
    import sys

    mod = sys.modules[__name__]
    for check, cls in _COVERED_BY_A_FIXTURE_CLASS.items():
        assert hasattr(mod, cls), (
            f"{check} claims coverage from {cls}, which does not exist -- "
            "the exemption is now a hole"
        )


class TestHeldSymbolPriceHealth:
    """`_check_held_symbol_price_health` needs real cache files on disk,
    so it cannot live in the table above — it takes a `cache_dir` and
    reads the price-cache meta sidecar and the proxy map.

    Two findings, both `warn`/`info`, both about a HELD position:
    a symbol the fetcher keeps failing on, and one whose coverage has
    gone stale. Silence is the normal state, which is what makes the
    near-miss half load-bearing: a permanently-lit coverage warning
    trains the reader to ignore the panel that also carries "an entire
    account is missing".
    """

    def _cache(self, workdir, *, meta: dict, proxies: dict | None = None):
        import json

        cache = workdir / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "price_cache_meta.json").write_text(
            json.dumps({"symbols": meta}), encoding="utf-8")
        if proxies is not None:
            (cache / "symbol_proxy_map.json").write_text(
                json.dumps(proxies), encoding="utf-8")
        return cache

    def _held(self, symbol="AAA"):
        return [{"account_group": "Broker", "symbol": symbol,
                 "quantity": 10.0, "value": 1000.0}]

    def _kinds(self, issues):
        return {i["kind"] for i in issues}

    def test_a_failing_held_symbol_is_reported(self, isolated_workdir):
        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 3,
                                          "last_error": "no data"}})
        issues = D._check_held_symbol_price_health(self._held(), cache)
        assert "price_fetch_failing" in self._kinds(issues)

    def test_a_tombstoned_held_symbol_is_reported(self, isolated_workdir):
        """Tombstoned means fin has stopped retrying — the position will
        never get a price again without intervention."""
        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 0,
                                          "tombstone": True}})
        issues = D._check_held_symbol_price_health(self._held(), cache)
        assert "price_fetch_failing" in self._kinds(issues)

    def test_a_healthy_symbol_is_silent(self, isolated_workdir):
        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 0}})
        assert D._check_held_symbol_price_health(self._held(), cache) == []

    def test_a_proxied_symbol_is_not_reported_as_failing(self,
                                                         isolated_workdir):
        """A proxy is the documented fix for an unfetchable symbol. Once
        one is mapped, the failure count on the original is expected —
        flagging it would keep the warning lit forever."""
        cache = self._cache(
            isolated_workdir,
            meta={"AAA": {"failure_count": 3}},
            proxies={"AAA": {"proxy": "BBB", "method": "direct"}})
        issues = D._check_held_symbol_price_health(self._held(), cache)
        assert "price_fetch_failing" not in self._kinds(issues)

    def test_a_no_fetch_symbol_is_skipped(self, isolated_workdir):
        """Multi-word display names never reach yfinance, so a failure
        count on one is stale meta from before the classifier covered
        it — not a live problem."""
        cache = self._cache(isolated_workdir,
                            meta={"FIDELITY 500 INDEX": {"failure_count": 5}})
        issues = D._check_held_symbol_price_health(
            self._held("FIDELITY 500 INDEX"), cache)
        assert "price_fetch_failing" not in self._kinds(issues)

    def test_stale_coverage_is_reported(self, isolated_workdir):
        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 0,
                                          "covered_end": "2020-01-01"}})
        issues = D._check_held_symbol_price_health(self._held(), cache)
        assert "price_coverage_stale" in self._kinds(issues) or issues, (
            "a symbol whose coverage ended years ago was not reported"
        )

    def test_current_coverage_is_silent(self, isolated_workdir):
        from datetime import date

        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 0,
                                          "covered_end": date.today().isoformat()}})
        assert D._check_held_symbol_price_health(self._held(), cache) == []

    def test_a_position_not_held_is_ignored(self, isolated_workdir):
        """The check is scoped to what you actually own — a failing
        symbol you sold years ago is not worth a warning."""
        cache = self._cache(isolated_workdir,
                            meta={"AAA": {"failure_count": 3}})
        closed = [{"account_group": "Broker", "symbol": "AAA",
                   "quantity": 0.0, "value": 0.0}]
        assert D._check_held_symbol_price_health(closed, cache) == []

    def test_a_missing_meta_file_is_not_an_error(self, isolated_workdir):
        """A first run has no sidecar. Absent is normal, not a finding."""
        cache = isolated_workdir / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        assert D._check_held_symbol_price_health(self._held(), cache) == []

    def test_a_corrupt_meta_file_degrades_quietly(self, isolated_workdir):
        """Unlike the loaders in `prices`, which raise on purpose, this
        is a diagnostic — it must not take down a run that would
        otherwise succeed."""
        cache = isolated_workdir / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "price_cache_meta.json").write_text('{"symbols":',
                                                     encoding="utf-8")
        assert D._check_held_symbol_price_health(self._held(), cache) == []


class TestValueQtyPriceChecksNegativeQuantities:
    """The check gated on ``qty > 0``, exempting every negative row.

    Those are reachable: an orphan OEXP / OEXCS whose opening BTO
    predates the CSV window pushes a contract balance below zero, and
    ``is_dust`` drops only the UNPRICED ones — a priced negative
    position reaches the holdings table.  So the exemption covered the
    rows most likely to be mis-signed, which is how a check ends up
    passing over the thing it exists to find (docs/AUDIT.md F-035, same
    shape as the ``symbol == "USD"`` skip next door).

    Synthetic round numbers only.
    """

    OPT = "ACME 12/18/2026 Call $100.00"

    def test_a_mis_signed_negative_position_is_flagged(self):
        """value carries the wrong SIGN — qty is -2 contracts but the
        row values them as if long.  Exactly what a dropped minus in a
        valuation site produces, and previously invisible."""
        issues = D._check_value_qty_price_consistency(
            [_h(symbol=self.OPT, quantity=-2.0, price=3.0, value=600.0)])
        assert issues, "a negative-quantity row with the wrong sign must flag"
        assert issues[0]["kind"] == "value_qty_price_mismatch"
        assert issues[0]["severity"] == "high"

    def test_a_correctly_valued_negative_position_is_clean(self):
        """-2 contracts × $3.00 premium × 100 = -$600.  The check must
        not simply flag everything negative — that would be the same
        blindness with the opposite default."""
        assert D._check_value_qty_price_consistency(
            [_h(symbol=self.OPT, quantity=-2.0, price=3.0, value=-600.0)]) == []

    def test_a_plain_negative_share_position_is_checked_too(self):
        """Not an options-only rule — the ×100 multiplier is what
        differs, not whether the row gets looked at.  -10 × $4 = -$40."""
        assert D._check_value_qty_price_consistency(
            [_h(quantity=-10.0, price=4.0, value=-400.0)]), (
            "a negative share position with a 10x value error must flag")
        assert D._check_value_qty_price_consistency(
            [_h(quantity=-10.0, price=4.0, value=-40.0)]) == []

    def test_the_relative_tolerance_is_not_negative_for_a_short(self):
        """`expected * 0.001` on a negative expectation is a NEGATIVE
        bound; passed to max() it is inert, but a later reorder would
        make every short row flag.  abs() pins the intent."""
        # A drift just inside the relative band on a large short.
        assert D._check_value_qty_price_consistency(
            [_h(quantity=-10_000.0, price=100.0, value=-1_000_000.0 - 50.0)]) == []
