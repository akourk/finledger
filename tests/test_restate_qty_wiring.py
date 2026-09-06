"""Each valuation site must pass the RIGHT quantity basis to the kernel.

`valuation.mark` takes `restate_qty`, and the flag is the difference
between a correct historical value and one off by the entire split
ratio:

* **True** — the quantity is a balance walked to a PAST date, still in
  that date's share basis. yfinance's `Close` is always split-adjusted,
  so the cached series is in TODAY's basis and the share count has to be
  scaled forward to match.
* **False** — the quantity already came from today's positions
  (`daily_pnl`, `header` reprice today's holdings at an earlier date).
  Restating there double-adjusts every date before a recent split.

`test_valuation_kernel.py` pins that the kernel honours the flag. This
module pins that each site *passes the right one*, which is a different
claim and was not covered anywhere: flipping the flag at three separate
sites left all 909 tests green.

**Why it hid, and why the fixture looks like this.** Split history comes
from yfinance's `splits_cache.json`, never from transaction data — so no
snapshot bundle and no CSV fixture can carry one. Every existing test
therefore ran with `split_factor_since` returning 1.0 for every symbol,
which makes True and False produce identical numbers. The sample even
has a `Stock Split` transaction row, which reaches the *lot* rescale and
says nothing about this. Seeding the splits cache directly is the only
way to make the choice observable.

Same shape as the two traps recorded in docs/AUDIT.md — a rule that
redistributes without changing a total needs a later sale; a config row
with one candidate needs two lots. Here, a flag with only one reachable
input value needs a split.

Synthetic tickers and round numbers only.
"""

from __future__ import annotations

import json

import pytest

SYM = "AAA"
SPLIT_DATE = "2024-07-01"
BEFORE = "2024-06-15"          # a valuation date BEFORE the split
#                                (the 15th: compute_history samples
#                                 semimonthly, so an arbitrary day has
#                                 no snapshot to assert against)
RATIO = 4.0

# 10 shares held before a 4:1 split are 40 in today's basis, and
# yfinance restates the pre-split close to a quarter of what it was.
# Both bases must value the position at the same dollars; only one of
# them is arithmetically correct for a given quantity.
HELD = 10.0
RESTATED_CLOSE = 25.0          # pre-split close of 100, restated
CORRECT_VALUE = HELD * RATIO * RESTATED_CLOSE      # 1000
UNRESTATED_VALUE = HELD * RESTATED_CLOSE           # 250 — the bug


@pytest.fixture
def split_world(isolated_workdir):
    """A cache where AAA splits 4:1 after the valuation date."""
    from src import prices as P

    P.reset_caches()
    cache = isolated_workdir / "cache"
    (cache / "prices").mkdir(parents=True, exist_ok=True)
    (cache / "prices" / f"{SYM}.json").write_text(
        json.dumps({"symbol": SYM,
                    "prices": {BEFORE: RESTATED_CLOSE,
                               "2024-06-14": RESTATED_CLOSE}}),
        encoding="utf-8")
    (cache / "splits_cache.json").write_text(
        json.dumps({SYM: [[SPLIT_DATE, RATIO]]}), encoding="utf-8")
    return isolated_workdir


def _buy(date=BEFORE, qty=HELD):
    return {"date": "2024-01-05", "account": "Broker",
            "account_group": "Broker", "account_type": "Taxable",
            "symbol": SYM, "action": "Buy", "quantity": qty,
            "price": 100.0, "fees": 0.0, "amount": qty * 100.0,
            "description": "", "source": "t.csv"}


class TestTheFixtureIsDiscriminating:
    """Not-vacuous guard, and the whole reason this module exists.

    If the split does not land after the valuation date, the factor is
    1.0, both bases agree, and every assertion below passes against a
    site that hard-codes either one.
    """

    def test_the_split_factor_is_not_one(self, split_world):
        from src.prices import split_factor_since
        assert split_factor_since(SYM, BEFORE) == pytest.approx(RATIO)

    def test_the_two_bases_give_different_answers(self):
        assert CORRECT_VALUE != pytest.approx(UNRESTATED_VALUE)


class TestAsOfDateSitesRestate:
    """A balance walked to a past date is in that date's share basis."""

    def test_compute_history_restates(self, split_world):
        from src.history import compute_history

        snaps = compute_history([_buy()], {SYM: "Technology"})
        at = [s for s in snaps if s["date"] == BEFORE]
        assert at, "no snapshot on the valuation date"
        assert at[0]["total"] == pytest.approx(CORRECT_VALUE), (
            f"snapshot valued the position at {at[0]['total']}; a "
            f"pre-split balance priced at a restated close must be "
            f"scaled forward ({CORRECT_VALUE}), not left raw "
            f"({UNRESTATED_VALUE})"
        )

    def test_compute_daily_totals_restates(self, split_world):
        from src.history import compute_daily_totals

        totals = dict(compute_daily_totals([_buy()]))
        assert totals.get(BEFORE) == pytest.approx(CORRECT_VALUE)

    def test_value_at_date_restates(self, split_world):
        from src.analytics._shared import _value_at_date, _balance_sort_key

        txns = sorted([_buy()], key=_balance_sort_key)
        assert _value_at_date(txns, BEFORE, None, []) == \
            pytest.approx(CORRECT_VALUE)

    def test_the_three_as_of_sites_agree(self, split_world):
        """The property the kernel exists to guarantee. They are
        compared against each other as well as against the expected
        figure, because a shared wrong answer is the failure mode a
        per-site assertion cannot see."""
        from src.history import compute_history, compute_daily_totals
        from src.analytics._shared import _value_at_date, _balance_sort_key

        txns = [_buy()]
        snap = [s for s in compute_history(txns, {SYM: "Technology"})
                if s["date"] == BEFORE][0]["total"]
        daily = dict(compute_daily_totals(txns))[BEFORE]
        vad = _value_at_date(sorted(txns, key=_balance_sort_key), BEFORE, None, [])
        assert snap == pytest.approx(daily) == pytest.approx(vad)


class TestTodayBasisSitesDoNotRestate:
    """`daily_pnl` and `header` reprice TODAY's positions at an earlier
    date. Those quantities are already in today's basis, so restating
    them applies the split twice."""

    def _history(self):
        """Minimal history whose last snapshot carries today's position.

        Quantity is the POST-split count, which is what a real snapshot
        for today would hold.
        """
        return [{"date": BEFORE, "total": 0.0, "positions": [
            {"account_group": "Broker", "symbol": SYM,
             "quantity": HELD * RATIO, "price": RESTATED_CLOSE,
             "value": HELD * RATIO * RESTATED_CLOSE, "cost_basis": 0.0},
        ]}]

    def test_header_does_not_restate(self, split_world):
        from src.analytics.header import compute_header_summary

        out = compute_header_summary([_buy()], self._history(), [])
        assert out is not None
        # Yesterday's value = today's share count x yesterday's close.
        # Restating would multiply it by the split ratio again.
        yest = out["value"] - out["change_1d"]
        assert yest == pytest.approx(HELD * RATIO * RESTATED_CLOSE), (
            f"yesterday's total came out at {yest}; today's share count "
            "was scaled forward a second time"
        )

    def test_daily_pnl_does_not_restate(self, split_world):
        from src.analytics.daily_pnl import compute_daily_pnl

        series = compute_daily_pnl(self._history(), [_buy()],
                                   window_days=3)
        assert series, "daily P&L produced no points"
        for pt in series:
            assert pt["value"] == pytest.approx(
                HELD * RATIO * RESTATED_CLOSE), (
                f"daily P&L valued the position at {pt['value']} — a "
                "today-basis quantity must not be restated"
            )
