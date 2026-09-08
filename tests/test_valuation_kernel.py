"""The shared valuation kernel — the rules five call sites used to hold
their own copies of.

`src/valuation.py` exists because "what is this position worth on this
date" was answered independently in `history`'s snapshot walker,
`history.compute_daily_totals`, `_shared._value_at_date`,
`analytics/daily_pnl` and `analytics/header`. They agreed on the shape
and drifted on the details — CLAUDE.md's response was an invariant
("if you change a basis rule, change BOTH walkers") plus a skill to
enforce it by hand, which is a process fix for a structural problem.

These tests pin the kernel itself, so the rules are asserted once
rather than once per site. The per-site parity tests
(`test_value_at_date_parity.py`, `test_daily_totals_parity.py`,
`test_option_floor_parity.py`) still matter: they pin that each site
CALLS the kernel. This module pins what the kernel does.

Synthetic tickers and round numbers only.
"""

from __future__ import annotations

import json

import pytest

DATE = "2024-06-14"
OPT = "FOO 12/18/2026 Call $100.00"


def _seed(workdir, *, prices=None, splits=None):
    cache = workdir / "cache"
    (cache / "prices").mkdir(parents=True, exist_ok=True)
    for sym, series in (prices or {}).items():
        (cache / "prices" / f"{sym}.json").write_text(
            json.dumps({"symbol": sym, "prices": series}), encoding="utf-8")
    if splits is not None:
        (cache / "splits_cache.json").write_text(json.dumps(splits),
                                                 encoding="utf-8")


@pytest.fixture
def kernel(isolated_workdir):
    from src import prices as P
    from src import valuation as V

    def go(**seed):
        P.reset_caches()
        _seed(isolated_workdir, **seed)
        return V
    return go


class TestTheLadder:
    """Cash, then cache, then transaction price, then nothing."""

    def test_cash_is_face_value(self, kernel):
        V = kernel()
        m = V.mark("USD", 250.0, DATE)
        assert m.value == 250.0
        assert m.price == 1.0
        assert m.source == "cash"

    def test_a_cached_price_wins(self, kernel):
        V = kernel(prices={"AAA": {DATE: 20.0}})
        m = V.mark("AAA", 10.0, DATE, {"AAA": 99.0})
        assert m.value == pytest.approx(200.0), (
            "the transaction-price fallback beat a live cache price"
        )
        assert m.source == "cache"

    def test_the_transaction_price_covers_a_cache_miss(self, kernel):
        V = kernel()
        m = V.mark("AAA", 10.0, DATE, {"AAA": 15.0})
        assert m.value == pytest.approx(150.0)
        assert m.source == "txn"

    def test_no_price_anywhere_is_unpriced_not_zero(self, kernel):
        """A missing price must not read as a worthless position — the
        caller reports it through `priced_pct` instead."""
        V = kernel()
        m = V.mark("AAA", 10.0, DATE, {})
        assert m.value is None, (
            f"an unpriceable position valued at {m.value} — substituting a "
            "number here silently understates the portfolio"
        )
        assert m.priced is False

    def test_a_zero_price_is_not_a_price(self, kernel):
        """A $0 transaction price (corp-action receipt at no cost) must
        not mark the position at zero."""
        V = kernel()
        assert V.mark("AAA", 10.0, DATE, {"AAA": 0.0}).value is None


class TestSplitRestatement:
    """yfinance's Close is always split-adjusted, so the cache series is
    in today's share basis and an as-of-date quantity has to be restated
    forward to match. CLAUDE.md: never multiply a historical balance by
    a historical cache price without it."""

    SPLIT = {"AAA": [["2024-09-01", 2.0]]}

    def test_an_as_of_quantity_is_restated(self, kernel):
        V = kernel(prices={"AAA": {DATE: 50.0}}, splits=self.SPLIT)
        m = V.mark("AAA", 10.0, DATE, restate_qty=True)
        assert m.qty == pytest.approx(20.0)
        assert m.value == pytest.approx(1000.0), (
            "the pre-split balance was not restated, so it was valued at "
            "the post-split price without the matching share count"
        )

    def test_a_today_basis_quantity_is_not_restated(self, kernel):
        """`daily_pnl` and `header` read TODAY's positions, which are
        already in today's basis. Restating them double-adjusts every
        date before a recent split."""
        V = kernel(prices={"AAA": {DATE: 50.0}}, splits=self.SPLIT)
        m = V.mark("AAA", 10.0, DATE, restate_qty=False)
        assert m.qty == pytest.approx(10.0)
        assert m.value == pytest.approx(500.0)

    def test_restatement_never_applies_to_the_txn_fallback(self, kernel):
        """Transaction prices are as-of-trade, so the balance beside them
        needs no correction — applying one would scale a position that
        was never mispriced."""
        V = kernel(splits=self.SPLIT)
        m = V.mark("AAA", 10.0, DATE, {"AAA": 50.0}, restate_qty=True)
        assert m.qty == pytest.approx(10.0), (
            "a split factor was applied on top of an as-of-trade price"
        )
        assert m.value == pytest.approx(500.0)

    def test_the_fixture_split_is_actually_after_the_date(self, kernel):
        """Not-vacuous guard: with the split before the date the factor
        is 1.0 and restate_qty True/False are indistinguishable."""
        V = kernel(prices={"AAA": {DATE: 50.0}}, splits=self.SPLIT)
        assert V.mark("AAA", 10.0, DATE, restate_qty=True).qty != \
            V.mark("AAA", 10.0, DATE, restate_qty=False).qty


class TestOptionContracts:
    """Quantity is CONTRACTS and price is the per-share premium, so every
    valuation scales by 100."""

    def test_the_multiplier_applies_to_the_txn_price(self, kernel):
        V = kernel()
        m = V.mark(OPT, 2.0, DATE, {OPT: 3.0})
        assert m.value == pytest.approx(600.0), (
            "2 contracts at a $3 premium are $600, not $6"
        )

    def test_the_multiplier_applies_to_a_cached_price_too(self, kernel,
                                                          monkeypatch):
        """The asymmetry this module was built to remove.

        Three of the five original sites applied the multiplier on the
        transaction-price branch and omitted it on the cache branch.

        It is **unreachable today**, and deliberately tested anyway.
        `get_price` is gated by `_classify_no_fetch`, and option symbols
        are multi-word, so the cache branch cannot currently fire for a
        contract — which is why the omission never showed up as a bug.
        The kernel's contract is still "if a price comes back for this
        symbol, scale it", and that has to hold on its own rather than
        by depending on a gate two modules away staying shut. Stubbing
        the lookup is the only way to state it.
        """
        monkeypatch.setattr("src.valuation.get_price",
                            lambda sym, on: 3.0 if sym == OPT else None)
        V = kernel()
        m = V.mark(OPT, 2.0, DATE)
        assert m.source == "cache"
        assert m.value == pytest.approx(600.0), (
            f"a cache-priced contract valued at {m.value} — the x100 "
            "multiplier was dropped on this branch"
        )

    def test_a_plain_stock_is_not_scaled(self, kernel):
        """Near-miss: the multiplier keys off the option symbol shape, so
        an ordinary ticker must come through at 1x."""
        V = kernel(prices={"AAA": {DATE: 3.0}})
        assert V.mark("AAA", 2.0, DATE).value == pytest.approx(6.0)


class TestTheIntrinsicFloor:
    """yfinance cannot quote contracts, so an open option marks at its
    last traded premium — which goes stale between trades. The floor
    lifts it to intrinsic value from the underlying's cached price, so a
    deep-ITM contract tracks its underlying instead of sitting flat."""

    # Underlying at 150 vs a 100 strike -> 50 of intrinsic per share.
    UNDERLYING = {"FOO": {DATE: 150.0}}

    def test_intrinsic_lifts_a_stale_premium(self, kernel):
        V = kernel(prices=self.UNDERLYING)
        m = V.mark(OPT, 1.0, DATE, {OPT: 4.0})
        assert m.source == "intrinsic"
        assert m.value == pytest.approx(5000.0), (
            "a deep-ITM contract stayed at its purchase premium — the "
            "entire intrinsic-over-cost gap reprints as phantom movement"
        )

    def test_the_floor_only_raises(self, kernel):
        """A premium above intrinsic is time value, and real. The floor
        must never mark it down."""
        V = kernel(prices=self.UNDERLYING)
        m = V.mark(OPT, 1.0, DATE, {OPT: 80.0})
        assert m.price == pytest.approx(80.0)
        assert m.source == "txn"

    def test_the_floor_applies_with_no_premium_at_all(self, kernel):
        """An option with no recorded trade price still marks at
        intrinsic rather than dropping out of the total."""
        V = kernel(prices=self.UNDERLYING)
        m = V.mark(OPT, 1.0, DATE, {})
        assert m.value == pytest.approx(5000.0)

    def test_an_out_of_the_money_contract_keeps_its_premium(self, kernel):
        V = kernel(prices={"FOO": {DATE: 80.0}})
        m = V.mark(OPT, 1.0, DATE, {OPT: 4.0})
        assert m.price == pytest.approx(4.0)

    def test_a_stock_is_never_floored(self, kernel):
        """`BBB` is uncached, so it takes the same fallback branch an
        option would — and must come through at its transaction price,
        because `option_intrinsic` has nothing to say about a ticker
        that is not a contract."""
        V = kernel(prices=self.UNDERLYING)
        m = V.mark("BBB", 1.0, DATE, {"BBB": 4.0})
        assert m.price == pytest.approx(4.0)
        assert m.source == "txn"


class TestTheDustFilter:
    """Moved here from `pipeline_stages` because the history walkers need
    it and their inline copy had already drifted — it kept the small
    negative fractional positions `is_dust` drops, while its comment
    claimed the two matched exactly."""

    def test_a_real_position_is_not_dust(self):
        from src.valuation import is_dust
        assert is_dust(10.0, 100.0) is False

    def test_a_sub_penny_value_is_dust(self):
        from src.valuation import is_dust
        assert is_dust(1e-6, 1.0) is True

    def test_a_small_negative_fraction_is_dust(self):
        """The clause history's copy was missing. These are unpaired
        corporate-action surrenders, not short positions."""
        from src.valuation import is_dust
        assert is_dust(-0.5, 50.0) is True

    def test_a_large_negative_position_is_not_dust(self):
        """Near-miss: a real short must survive, so the artifact rule is
        bounded by both share count and dollar value."""
        from src.valuation import is_dust
        assert is_dust(-5.0, 100.0) is False
        assert is_dust(-0.5, 500.0) is False

    def test_unpriced_negatives_are_dust(self):
        from src.valuation import is_dust
        assert is_dust(-3.0, 0.0) is True

    def test_unpriced_tiny_quantities_are_dust(self):
        from src.valuation import is_dust
        assert is_dust(1e-9, 0.0) is True
        assert is_dust(1.0, 0.0) is False

    def test_pipeline_stages_re_exports_the_same_function(self):
        """`main.py` imports it from there; the two names must not be
        allowed to become two functions again."""
        from src import pipeline_stages, valuation
        assert pipeline_stages.is_dust is valuation.is_dust

    def test_mark_is_dust_uses_the_raw_quantity(self, kernel):
        """A restated quantity must not be what the thresholds see: a
        pre-split balance scaled forward could clear a share-count
        threshold it should not."""
        from src.valuation import mark_is_dust

        V = kernel(prices={"AAA": {DATE: 50.0}},
                   splits={"AAA": [["2024-09-01", 1000.0]]})
        m = V.mark("AAA", -0.0005, DATE)
        assert m.qty == pytest.approx(-0.5)
        assert mark_is_dust(m, -0.0005) is True

    @pytest.mark.parametrize(
        "qty, factor, expected_value, expected_dust",
        [
            (0.0001, 1000.0, 5.0, False),
            (0.001, 0.01, 0.0005, True),
            (-0.005, 1000.0, -250.0, False),
            (-0.005, 100.0, -25.0, True),
        ],
    )
    def test_dollar_thresholds_use_split_restated_value(
        self, kernel, qty, factor, expected_value, expected_dust,
    ):
        V = kernel(prices={"AAA": {DATE: 50.0}},
                   splits={"AAA": [["2024-09-01", factor]]})
        m = V.mark("AAA", qty, DATE)
        assert m.value == pytest.approx(expected_value)
        assert V.mark_is_dust(m, qty) is expected_dust

    def test_dollar_thresholds_include_the_contract_multiplier(self, kernel):
        V = kernel()
        m = V.mark(OPT, 1.0, DATE, {OPT: 0.005})
        assert m.value == pytest.approx(0.5)
        assert V.mark_is_dust(m, 1.0) is False

    def test_holdings_and_marks_keep_the_same_low_premium_contract(self, kernel):
        from src.pipeline_stages import build_holdings

        V = kernel()
        holdings, by_account = build_holdings(
            {("Robinhood", OPT): 1.0}, {OPT: 0.005},
            {("Robinhood", OPT): 1.0}, {},
        )
        assert len(holdings) == len(by_account) == 1
        assert holdings[0]["value"] == V.mark(OPT, 1.0, DATE, {OPT: 0.005}).value


class TestThePriceMemo:
    """A day walker asks for one symbol once per account group holding
    it. The memo collapses those without changing any answer."""

    def test_the_memo_returns_the_same_value(self, kernel):
        V = kernel(prices={"AAA": {DATE: 20.0}})
        memo: dict = {}
        a = V.mark("AAA", 10.0, DATE, price_cache=memo)
        b = V.mark("AAA", 10.0, DATE, price_cache=memo)
        assert a == b
        assert memo == {"AAA": 20.0}

    def test_a_miss_is_memoized_too(self, kernel):
        """Otherwise every unpriceable symbol re-runs the lookback walk
        on every account group, every day."""
        V = kernel()
        memo: dict = {}
        V.mark("AAA", 10.0, DATE, {}, price_cache=memo)
        assert memo == {"AAA": None}

    def test_the_memo_matches_the_unmemoized_result(self, kernel):
        V = kernel(prices={"AAA": {DATE: 20.0}})
        assert V.mark("AAA", 3.0, DATE) == \
            V.mark("AAA", 3.0, DATE, price_cache={})
