"""`build_analytics` must survive degenerate ledgers without emitting NaN.

Segment 5 item 3. Individual analytics modules already have degenerate
tests (drawdown, monthly_pnl, monte_carlo, concentration, daily_pnl,
rebalancing). What was missing is a check on the **composed payload**:
27 blocks built from each other's output, on inputs where many of them
divide by something that can be zero.

The central assertion is `json.dumps(payload, allow_nan=False)`. That is
not a stylistic check — CLAUDE.md spells out why:

    A single NaN reaches the dashboard as a `NaN` **total**, not a parse
    error, because Python's json.dump writes a bare NaN literal which is
    valid JavaScript.

So a NaN anywhere in this payload is silently rendered rather than
caught, and `allow_nan=False` is the one line that covers all 27 blocks
at once. `Infinity` is caught the same way — CLAUDE.md notes the top
tax bracket's `room_left` must be `None`, never `inf`, for this reason.

Synthetic round numbers only.
"""

from __future__ import annotations

import json

import pytest


def _txn(**kw) -> dict:
    base = {
        "date": "2024-03-01", "account": "acct", "account_group": "Broker",
        "account_type": "Taxable", "symbol": "SYM", "action": "Buy",
        "quantity": 1.0, "price": 100.0, "fees": 0.0, "amount": 100.0,
        "description": "", "source": "t.csv",
    }
    base.update(kw)
    return base


def _snap(d: str, total: float, **kw) -> dict:
    base = {
        "date": d, "total": total,
        "by_account_group": {"Broker": total} if total else {},
        "by_account_type": {"Taxable": total} if total else {},
        "by_sector": {"Technology": total} if total else {},
        "total_cost_basis": total, "cost_basis_by_group": {},
        "cost_basis_by_type": {}, "net_contributed": total,
        "priced_pct": 1.0, "positions": [],
        "benchmark_spy": total, "benchmark_bnd": total,
        "benchmark_vxus": total, "benchmark_60_40": total,
    }
    base.update(kw)
    return base


def _holding(**kw) -> dict:
    base = {"symbol": "SYM", "account_group": "Broker",
            "account_type": "Taxable", "quantity": 1.0, "price": 100.0,
            "value": 100.0, "cost_basis": 100.0, "unrealized_gain": 0.0,
            "sector": "Technology"}
    base.update(kw)
    return base


# (id, txns, history, holdings, holdings_by_account)
SCENARIOS = [
    ("completely_empty", [], [], [], []),
    ("one_transaction_no_history", [_txn()], [], [], []),
    ("history_with_a_single_snapshot", [_txn()],
     [_snap("2024-03-31", 100.0)], [_holding()], [_holding()]),
    ("all_values_zero", [_txn(quantity=0.0, price=0.0, amount=0.0)],
     [_snap("2024-03-31", 0.0), _snap("2024-04-30", 0.0)],
     [_holding(quantity=0.0, price=0.0, value=0.0, cost_basis=0.0)],
     [_holding(quantity=0.0, price=0.0, value=0.0, cost_basis=0.0)]),
    ("every_transaction_on_one_day",
     [_txn(), _txn(action="Sell", realized_gain=5.0, holding_days=0),
      _txn(action="Dividend", amount=1.0)],
     [_snap("2024-03-31", 100.0), _snap("2024-04-30", 100.0)],
     [_holding()], [_holding()]),
    ("fully_withdrawn_account",
     [_txn(), _txn(date="2024-06-01", action="Sell", quantity=1.0,
                   amount=120.0, realized_gain=20.0, holding_days=92)],
     [_snap("2024-03-31", 100.0), _snap("2024-06-30", 0.0)],
     [], []),
    ("zero_cost_basis_position", [_txn(amount=0.0, price=0.0)],
     [_snap("2024-03-31", 100.0), _snap("2024-04-30", 100.0)],
     [_holding(cost_basis=0.0, unrealized_gain=100.0)],
     [_holding(cost_basis=0.0, unrealized_gain=100.0)]),
    ("history_but_no_holdings", [_txn()],
     [_snap("2024-03-31", 100.0), _snap("2024-04-30", 110.0)], [], []),
]

_IDS = [s[0] for s in SCENARIOS]


@pytest.fixture
def build(isolated_workdir):
    from src.analytics import build_analytics
    return build_analytics


class TestDegeneratePayload:

    @pytest.mark.parametrize("_id,txns,history,holdings,hba", SCENARIOS,
                             ids=_IDS)
    def test_does_not_raise(self, build, _id, txns, history, holdings, hba):
        out = build(txns, history, holdings, hba)
        assert isinstance(out, dict) and out

    @pytest.mark.parametrize("_id,txns,history,holdings,hba", SCENARIOS,
                             ids=_IDS)
    def test_payload_carries_no_nan_or_infinity(self, build, _id, txns,
                                                history, holdings, hba):
        """The assertion this module exists for.

        `allow_nan=False` makes json.dumps raise on NaN or ±Infinity
        anywhere in the tree. In production those serialize silently into
        the export and render as `NaN` on the dashboard.
        """
        out = build(txns, history, holdings, hba)
        try:
            json.dumps(out, allow_nan=False)
        except ValueError as exc:  # "Out of range float values..."
            pytest.fail(
                f"analytics payload for {_id!r} contains NaN/Infinity: {exc}"
            )

    @pytest.mark.parametrize("_id,txns,history,holdings,hba", SCENARIOS,
                             ids=_IDS)
    def test_payload_is_json_round_trippable(self, build, _id, txns, history,
                                             holdings, hba):
        """The export is embedded verbatim into the dashboard HTML, so
        anything unserializable breaks the page rather than the run."""
        out = build(txns, history, holdings, hba)
        assert json.loads(json.dumps(out)) is not None


class TestZeroValuePortfolioReturnsCleanEmpties:
    """A zero-value portfolio must produce empty concentration lists,
    not a full set of entries all reading 0%.

    `concentration.py` carries THREE overlapping protections, and it is
    worth knowing which does the work:

    1. the accumulation loop skips any holding whose value is not a
       positive number — this is the one that matters, and it means a
       zero-value portfolio never populates the breakdowns at all;
    2. an early return on `total <= 0`;
    3. an inner `denom > 0` fallback inside `_entries`.

    Given (1), the early return in (2) is **redundant** — removing it
    produces an identical result, which mutation confirms rather than
    contradicts. That is dead defensive code, not a gap, and no test can
    or should distinguish it.

    So this pins the observable CONTRACT — zero portfolio in, clean
    empties out — independent of which guard delivers it.
    """

    def test_all_zero_holdings_yield_empty_concentration(self,
                                                         isolated_workdir):
        from src.analytics.concentration import compute_concentration

        zero = [_holding(quantity=0.0, price=0.0, value=0.0, cost_basis=0.0)]
        out = compute_concentration(zero)

        assert out["positions"] == []
        assert out["sectors"] == []
        assert out["account_groups"] == []
        assert out["account_types"] == []
        assert out["herfindahl"] == 0
        assert out["top_5_concentration"] == 0
        assert out["flags"] == []

    def test_a_real_portfolio_still_produces_entries(self, isolated_workdir):
        """Near-miss: the empty result must come from the zero total, not
        from the function being broken."""
        from src.analytics.concentration import compute_concentration

        out = compute_concentration([_holding(value=100.0)])
        assert out["positions"], "a non-empty portfolio produced no entries"
        assert out["positions"][0]["pct"] == pytest.approx(100.0)


class TestTheNanCheckIsNotVacuous:
    """A check that has never rejected anything is worth nothing until it
    has been seen to reject something."""

    def test_allow_nan_false_rejects_a_nan(self):
        with pytest.raises(ValueError):
            json.dumps({"x": float("nan")}, allow_nan=False)

    def test_allow_nan_false_rejects_infinity(self):
        with pytest.raises(ValueError):
            json.dumps({"x": float("inf")}, allow_nan=False)

    def test_allow_nan_false_accepts_ordinary_numbers(self):
        json.dumps({"x": 1.5, "y": 0.0, "z": -3}, allow_nan=False)

    def test_a_nan_nested_deep_in_the_tree_is_still_caught(self):
        """The payload nests several levels; a shallow check would miss
        a NaN inside e.g. analytics.tax.rate_estimates_by_year[Y]."""
        deep = {"a": {"b": [{"c": {"d": [1.0, float("nan")]}}]}}
        with pytest.raises(ValueError):
            json.dumps(deep, allow_nan=False)
