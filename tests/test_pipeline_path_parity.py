"""Both pipeline paths must produce the same realized gains.

``python -m src.main`` parses the CSVs and walks cost basis; ``python -m
src.main --refresh-prices`` skips all that and reloads
``exports/transactions.json``.  Same transactions in, so the same lots
must come out — but the two paths hand the lot walker its txn list in
different orders (parse order vs. the export's date+account sort), and
the walker's sort has genuine ties: several same-day rows in one symbol.
Python's sort is stable, so ties used to resolve to "whatever order the
caller passed", and the refresh path silently relieved different lots.
On a real portfolio that moved a single account-year's realized gains by
thousands of dollars, which surfaced as spurious "off" rows in the
Overview reconciliation panel and wrong Tax-tab figures.

The fix is ``seq`` (see ``pipeline_stages.assign_ingest_seq``): the
ingest index, exported with each txn and used as the walkers' final
sort tie-break, which makes their order total and reproducible.

The fixture below is built to trip the old bug rather than merely
exercise the paths — see ``_write_wrap_ordering_trap``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _write_wrap_ordering_trap(tmp: Path) -> None:
    """A portfolio whose same-day rows the two paths order differently.

    ``walk_balances`` sorts by the ACTION's direction (``Unwrap Out``
    subtracts, so it sorts last within the day) while the basis walker
    sorts by the BASIS EFFECT (``wrap_out`` carries basis rather than
    removing it, so it ties with the day's Buy).  That disagreement is
    what lets the incoming list order leak into the result:

      * parse order  — Unwrap Out first: it consumes the only lot in
        the pool, the $500/unit one.
      * export order — the Buy first: the pool now also holds the
        $1500/unit lot, and HIFO takes *that* one instead.

    Same transactions, two different lots relieved, and the gain on the
    eventual ETH sale differs by the spread between them.
    """
    with open(tmp / "data" / "manual-adjustments.csv", "w",
              newline="", encoding="utf-8") as f:
        f.write("Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n")
        # Cheap lot — the only one in the pool on the unwrap date.
        f.write("Crypto Wallet,2024-01-10,Buy,CBETH,2,500,1000,cheap lot\n")
        # The unwrap is written BEFORE the same-day buy, so parse order
        # and the export's re-sort disagree about which comes first.
        f.write("Crypto Wallet,2024-02-05,Unwrap Out,CBETH,2,,,unwrap out\n")
        f.write("Crypto Wallet,2024-02-05,Unwrap In,ETH,2,,,unwrap in\n")
        # Expensive lot, same day.
        f.write("Crypto Wallet,2024-02-05,Buy,CBETH,2,1500,3000,pricey lot\n")
        # Realize the carried basis.
        f.write("Crypto Wallet,2024-06-01,Sell,ETH,2,4000,8000,exit\n")
        # --- Two lots simultaneously in the pool, priced 5x apart ------
        # Without this the `Lot Method,,,Crypto,HIFO` row below is INERT.
        # Every other consumption in this fixture has exactly ONE
        # candidate lot available at the time (the unwrap runs before the
        # same-day pricey buy), and with one candidate FIFO and HIFO pick
        # identically — so the method never got to choose and mutating
        # `account_methods` in EITHER pipeline path changed nothing.
        # That is what let F-028 hide: a fixture that looked like it
        # covered lot methods because it carried the metadata row.
        #
        #   FIFO -> relieves the 2024-03 lot (200)  -> +1300 gain
        #   HIFO -> relieves the 2024-04 lot (1000) -> +500 gain
        f.write("Crypto Wallet,2024-03-01,Buy,LOTA,10,20,200,cheap lot\n")
        f.write("Crypto Wallet,2024-04-01,Buy,LOTA,10,100,1000,dear lot\n")
        f.write("Crypto Wallet,2024-05-01,Sell,LOTA,10,150,1500,partial exit\n")
        # A Savings account and a Retirement account, so the fixture
        # also covers account-type-dependent logic.  Savings USD is the
        # one place holdings basis comes from `cash_principal` rather
        # than the lot walker, and interest must stay OUT of principal
        # (it's the gain).
        f.write("HYSA,2024-01-05,Deposit,USD,5000,1,5000,deposit\n")
        f.write("HYSA,2024-07-01,Interest,USD,200,1,200,interest\n")
        f.write("Retirement Acct,2024-01-05,Buy,VOO,10,100,1000,contribution\n")
        # Sub-cent amounts, repeated.  Brokers report fractional-share
        # and crypto fills to more than two decimals, and the per-txn
        # `cost_basis` annotation is rounded to cents for readability —
        # so a position built from many such fills is the case where
        # reconstructing basis by summing those annotations drifts from
        # the lot walker's own total.  Ten rows at +$0.003 of rounding
        # each puts the two a full 3 cents apart, which is what makes
        # `test_holdings_cost_basis_matches` below non-vacuous.
        for i in range(10):
            f.write(f"Retirement Acct,2024-02-{i + 1:02d},Buy,DUST,3,10.0023,"
                    f"30.007,fractional fill {i + 1}\n")

    with open(tmp / "data" / "metadata.csv", "w",
              newline="", encoding="utf-8") as f:
        f.write("Type,Date,Amount,Symbol,Note\n")
        f.write("Account Group,,,Crypto Wallet,Crypto\n")
        f.write("Account Type,,,Crypto,Taxable\n")
        f.write("Account Group,,,HYSA,HYSA\n")
        f.write("Account Type,,,HYSA,Savings\n")
        f.write("Account Group,,,Retirement Acct,Retire\n")
        f.write("Account Type,,,Retire,Retirement\n")
        # HIFO makes the two candidate lots distinguishable: FIFO would
        # take the cheap lot under either ordering.
        f.write("Lot Method,,,Crypto,HIFO\n")


class _Run:
    """Both paths' exported payloads, plus any invariant violations.

    Violations are captured rather than propagated so the parity
    assertions below get to run and report the *specific* divergence.
    A desynced pair of lot walkers trips ``check_invariants`` first
    (conftest sets ``FIN_ASSERT_INVARIANTS``), and "1 pipeline invariant
    violated" at fixture-setup time says far less about what broke than
    "realized gains differ".  ``test_no_invariant_violations`` reports
    them in their own right.
    """

    def __init__(self, full: dict, refresh: dict, violations: list):
        self.full = full
        self.refresh = refresh
        self.violations = violations


def _run(argv: list[str]) -> Exception | None:
    from src.analytics.data_health import InvariantViolation
    argv_save = sys.argv
    sys.argv = ["fin"] + argv
    try:
        from src.main import main
        main()
        return None
    except InvariantViolation as exc:
        # Raised at the very end of both paths, after the export is
        # written — so the payload the parity tests need still exists.
        return exc
    finally:
        sys.argv = argv_save


def _export(workdir: Path) -> dict:
    path = workdir / "exports" / "transactions.json"
    assert path.exists(), "pipeline did not produce transactions.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def both_paths(isolated_workdir, stub_prices):
    """Run the full pipeline, then ``--refresh-prices``, over one
    fixture."""
    _write_wrap_ordering_trap(isolated_workdir)
    for sym in ("CBETH-USD", "ETH-USD"):
        stub_prices.set(sym, {
            "2024-01-10": 500.0, "2024-02-05": 1500.0,
            "2024-06-01": 4000.0, "2024-12-31": 4000.0,
        })
        stub_prices.set_sector(sym, "Cryptocurrency")
    stub_prices.set("LOTA", {
        "2024-03-01": 20.0, "2024-04-01": 100.0,
        "2024-05-01": 150.0, "2024-12-31": 150.0,
    })
    stub_prices.set_sector("LOTA", "Cryptocurrency")

    violations = []
    for argv in (["--skip-rename"], ["--refresh-prices"]):
        if argv[0] == "--refresh-prices":
            # Simulate a FRESH PROCESS.  ACCOUNT_GROUPS / ACCOUNT_TYPES
            # are module-level dicts that ship empty and metadata.csv
            # fills in; the full run above leaves them populated, so an
            # in-process second run would inherit them and never
            # exercise the ordering that matters.  That is exactly why
            # the "refresh labels every account Taxable" bug survived a
            # green suite — clearing them here is what makes this
            # fixture representative of `python -m src.main
            # --refresh-prices` on the command line.
            from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
            ACCOUNT_GROUPS.clear()
            ACCOUNT_TYPES.clear()
        exc = _run(argv)
        if exc is not None:
            violations.append((argv[0], exc))
        if argv[0] == "--skip-rename":
            full = _export(isolated_workdir)
    return _Run(full, _export(isolated_workdir), violations)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _realized_total(data: dict) -> float:
    return sum(float(t.get("realized_gain") or 0)
               for t in data["transactions"])


def _realized_by_account_year(data: dict) -> dict:
    out: dict[tuple[str, str], float] = {}
    for t in data["transactions"]:
        rg = t.get("realized_gain")
        if not isinstance(rg, (int, float)):
            continue
        key = (t.get("account_group"), (t.get("date") or "")[:4])
        out[key] = round(out.get(key, 0.0) + rg, 6)
    return out


def _txn_identity(t: dict) -> tuple:
    """Everything that identifies a row except the walker's own output."""
    return (t.get("date"), t.get("account"), t.get("symbol"), t.get("action"),
            round(float(t.get("quantity") or 0), 8),
            round(float(t.get("amount") or 0), 2), t.get("description"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRealizedGainParity:
    def test_realized_total_matches(self, both_paths):
        assert _realized_total(both_paths.refresh) == pytest.approx(
            _realized_total(both_paths.full), abs=0.005), (
            "--refresh-prices realized a different total than the full "
            "pipeline on identical transactions — the two paths are "
            "relieving different lots (see pipeline_stages.assign_ingest_seq)"
        )

    def test_realized_by_account_and_year_matches(self, both_paths):
        """Per account-year, because that's the grain the Overview
        reconciliation panel and the Tax tab report at — offsetting
        year-to-year drift would hide in the grand total."""
        assert _realized_by_account_year(both_paths.refresh) == \
            _realized_by_account_year(both_paths.full)

    def test_tax_analytics_realized_by_year_matches(self, both_paths):
        """The Tax tab reads ``analytics.tax.realized_by_year``, which
        splits ST/LT per lot — so it can drift even when the per-txn
        totals agree."""
        f = (both_paths.full.get("analytics") or {}).get(
            "tax", {}).get("realized_by_year")
        r = (both_paths.refresh.get("analytics") or {}).get(
            "tax", {}).get("realized_by_year")
        assert f, "fixture realized nothing — the test would be vacuous"
        assert r == f

    def test_per_txn_cost_basis_matches(self, both_paths):
        """Realized gain is basis-driven; pin the basis annotations too
        so a compensating error can't pass the totals check."""
        f = {_txn_identity(t): (t.get("cost_basis"), t.get("realized_gain"))
             for t in both_paths.full["transactions"]}
        r = {_txn_identity(t): (t.get("cost_basis"), t.get("realized_gain"))
             for t in both_paths.refresh["transactions"]}
        assert r == f

    def test_no_invariant_violations(self, both_paths):
        """Desynced walkers also trip the high-severity data-health
        checks (lot-queue parity, snapshot rollup).  Report that
        separately from the realized figures above."""
        assert both_paths.violations == [], "\n".join(
            f"{path}: {exc}" for path, exc in both_paths.violations)


class TestAccountMetadataParity:
    """``config`` ships ACCOUNT_GROUPS / ACCOUNT_TYPES EMPTY; metadata.csv
    fills them in.  Any stage that asks "is this account Savings /
    Retirement?" is therefore wrong until ``parse_metadata`` has run.

    The refresh path used to load metadata two-thirds of the way down —
    after the balance walk, the cash-principal stage and the holdings
    build had all already consulted an empty map.  Every holding came
    back labelled "Taxable" (the fallback) and every Savings account
    lost its cash principal, so the same portfolio reported different
    account-type splits and a different cost-basis total depending on
    which command was run last.
    """

    def _by_key(self, data):
        return {(h["account_group"], h["symbol"]): h
                for h in data["holdings_by_account"]}

    def test_account_types_match(self, both_paths):
        f = {g: h["account_type"]
             for g, h in ((k[0], v) for k, v in self._by_key(both_paths.full).items())}
        r = {g: h["account_type"]
             for g, h in ((k[0], v) for k, v in self._by_key(both_paths.refresh).items())}
        assert r == f
        # Not vacuous: the fixture has a non-Taxable account, so a
        # fallback-to-Taxable regression actually shows up here.
        assert set(f.values()) - {"Taxable"}, \
            "fixture lost its non-Taxable accounts — this test would be vacuous"

    def test_basis_totals_match(self, both_paths):
        """`basis_totals` is the one dict a published figure reads, so
        the two pipeline paths must agree on it exactly.  It is built
        from `holdings` plus the walker's realized accumulator, and the
        refresh path rebuilds both — precisely the shape that has
        diverged here before."""
        r = both_paths.refresh.get("basis_totals")
        f = both_paths.full.get("basis_totals")
        assert r, "refresh path emitted no basis_totals"
        assert f, "full path emitted no basis_totals"
        # Only the price-INDEPENDENT fields.  Re-pricing is the refresh
        # path's entire purpose, so `value` and `unrealized_gain` are
        # meant to move; `cost_basis` and `realized_gain` come from lot
        # state and must not.  Same split the sibling parity tests use.
        for key in ("cost_basis", "realized_gain", "method", "unpriced_count"):
            assert r.get(key) == f.get(key), (
                f"basis_totals[{key!r}] differs: refresh={r.get(key)!r} "
                f"full={f.get(key)!r}"
            )
        assert r["value"] == pytest.approx(
            r["cost_basis"] + r["unrealized_gain"], abs=0.02), (
            "basis_totals is internally inconsistent on the refresh path")
        assert f["value"] == pytest.approx(
            f["cost_basis"] + f["unrealized_gain"], abs=0.02), (
            "basis_totals is internally inconsistent on the full path")

    def test_holdings_cost_basis_matches(self, both_paths):
        f = {k: h.get("cost_basis") for k, h in self._by_key(both_paths.full).items()}
        r = {k: h.get("cost_basis") for k, h in self._by_key(both_paths.refresh).items()}
        assert r == f

    def test_savings_cash_principal_survives_both_paths(self, both_paths):
        """Savings USD is the one holding whose basis comes from
        ``compute_cash_principal`` rather than the lot walker, and that
        stage silently returns nothing when ACCOUNT_TYPES is empty."""
        for label, data in (("full", both_paths.full),
                            ("refresh", both_paths.refresh)):
            usd = [h for h in data["holdings_by_account"]
                   if h["symbol"] == "USD" and h["account_type"] == "Savings"]
            assert usd, f"{label}: Savings USD holding missing"
            # Principal only — the $200 of interest is the GAIN, and
            # folding it into basis would make the account read 0%.
            assert usd[0]["cost_basis"] == pytest.approx(5000.0), label


class TestIngestSeq:
    def test_seq_survives_the_json_round_trip(self, both_paths):
        """``seq`` is the only record of the order the full pipeline
        walked in — ``export._INTERNAL_FIELDS`` must not strip it."""
        for data, label in ((both_paths.full, "full"),
                            (both_paths.refresh, "refresh")):
            seqs = [t.get("seq") for t in data["transactions"]]
            assert all(isinstance(s, int) for s in seqs), \
                f"{label} export dropped the `seq` field"
            assert len(set(seqs)) == len(seqs), \
                f"{label} export has duplicate `seq` values"

    def test_both_paths_agree_on_the_walk_order(self, both_paths):
        """Not just the same numbers — the same sequence, so the
        order-sensitive basis stampers (broker_lots, cost_basis_overrides)
        match rows the same way on both paths too."""
        def order(d):
            return [_txn_identity(t) for t in
                    sorted(d["transactions"], key=lambda t: t["seq"])]
        assert order(both_paths.refresh) == order(both_paths.full)

    def test_refresh_refuses_an_export_without_seq(self, isolated_workdir,
                                                   both_paths):
        """A pre-``seq`` export can't be ordered faithfully.  Refusing
        beats guessing: guessing is the bug this whole module is about."""
        path = isolated_workdir / "exports" / "transactions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for t in data["transactions"]:
            t.pop("seq", None)
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            _run(["--refresh-prices"])
        assert exc.value.code == 1


class TestFixtureActuallyTrips:
    """Guard against the parity tests above going vacuous.

    They only prove something if the fixture really does order these
    rows two different ways.  Assert the shape that makes it a trap:
    a wrap-out and a lot-creating buy on the same day and symbol, which
    the balance walker and the basis walker sort in opposite orders.
    """

    def test_same_day_wrap_and_buy_share_a_sort_tie(self, both_paths):
        from src.actions import SUBTRACT_ACTIONS
        from src.basis import _sort_key

        same_day = [t for t in both_paths.full["transactions"]
                    if t.get("date") == "2024-02-05"
                    and t.get("symbol") == "CBETH-USD"]
        actions = {t["action"] for t in same_day}
        assert {"Buy", "Unwrap Out"} <= actions

        buy = next(t for t in same_day if t["action"] == "Buy")
        wrap = next(t for t in same_day if t["action"] == "Unwrap Out")
        # The basis walker ties them (seq excluded — that IS the fix)...
        assert _sort_key(buy)[:4] == _sort_key(wrap)[:4]
        # ...while the balance walker puts the buy strictly first.
        assert buy["action"] not in SUBTRACT_ACTIONS
        assert wrap["action"] in SUBTRACT_ACTIONS

    def test_the_two_candidate_lots_have_different_basis(self, both_paths):
        """If both lots cost the same, which one HIFO relieves wouldn't
        change the realized gain and the parity tests couldn't fail."""
        buys = [t for t in both_paths.full["transactions"]
                if t.get("symbol") == "CBETH-USD" and t.get("action") == "Buy"]
        assert len({round(float(t["price"]), 4) for t in buys}) == 2
