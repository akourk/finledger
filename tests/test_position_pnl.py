"""Per-position trailing-window P&L (src/analytics/position_pnl.py).

Pins the flow-adjusted definition — the one thing about this module a
reader could reasonably get wrong.  "Open P&L over a window" is only
unambiguous while the share count is constant; these tests fix what
happens when it is not:

* a position untouched in the window reduces to the constant-share
  market move (the easy case, and the one every other case must agree
  with at the boundary);
* a position opened INSIDE the window reports ``value - cost``, never a
  full window of movement it did not participate in;
* a partially-sold position counts only the shares still open, because
  the column is labelled *open* P&L;
* an unpriceable date yields ``None``, never ``0`` — a confident zero is
  the failure mode worth a test.

Every case pins an ``as_of`` explicitly, so none of this drifts with the
calendar.
"""

from __future__ import annotations

from datetime import date

import pytest


AS_OF = "2024-06-14"          # a Friday


def _holding(acct, sym, price, cost_basis, value=None, acct_type="Taxable",
             qty=0.0):
    unreal = (round(value - cost_basis, 2)
              if (value is not None and cost_basis is not None) else None)
    return {"account_group": acct, "account_type": acct_type,
            "symbol": sym, "quantity": qty, "price": price,
            "value": value, "cost_basis": cost_basis,
            "unrealized_gain": unreal}


def _lot(d, qty, basis_per_share):
    return {"date": d, "qty": qty, "basis_per_share": basis_per_share}


def _seed(symbol, series):
    from src.prices import _load_prices
    _load_prices()[symbol] = dict(series)


def _win(out, symbol, key, *, acct=None):
    rows = out["by_account"] if acct else out["by_symbol"]
    for r in rows:
        if r["symbol"] == symbol and (acct is None
                                      or r["account_group"] == acct):
            return r["windows"][key]
    raise AssertionError(f"no row for {symbol}")


# ---------------------------------------------------------------------------
# Boundary arithmetic
# ---------------------------------------------------------------------------

def test_months_back_clamps_short_months():
    from src.analytics.position_pnl import _months_back
    # Mar 31 - 1mo has no Feb 31 to land on; clamp down, don't roll over
    # into March 2nd (which would make the window a day SHORT of a month
    # in the wrong direction).
    assert _months_back(date(2024, 3, 31), 1) == date(2024, 2, 29)
    assert _months_back(date(2023, 3, 31), 1) == date(2023, 2, 28)
    assert _months_back(date(2024, 1, 31), 1) == date(2023, 12, 31)
    assert _months_back(date(2024, 5, 15), 3) == date(2024, 2, 15)
    assert _months_back(date(2024, 2, 29), 12) == date(2023, 2, 28)


def test_window_boundaries_are_the_close_measured_from():
    from src.analytics.position_pnl import window_start_dates
    b = window_start_dates(date(2024, 6, 14))
    # 1D measures from YESTERDAY's close, which is what makes a lot
    # acquired today "in-window" and therefore worth `mark - cost`.
    assert b["1d"] == "2024-06-13"
    assert b["1w"] == "2024-06-07"
    assert b["1m"] == "2024-05-14"
    assert b["3m"] == "2024-03-14"
    # YTD measures from the prior year's final close, so everything
    # bought this year is in-window.
    assert b["ytd"] == "2023-12-31"
    assert b["1y"] == "2023-06-14"


# ---------------------------------------------------------------------------
# The definition
# ---------------------------------------------------------------------------

def test_untouched_position_is_the_constant_share_market_move(stub_prices):
    """The easy case every other case must agree with at the boundary."""
    from src.analytics.position_pnl import compute_position_pnl
    _seed("AAA", {"2024-06-13": 100.0, "2024-06-14": 110.0})
    state = {"lots": {("Broker", "AAA"): [_lot("2020-01-01", 10.0, 40.0)]}}
    holdings = [_holding("Broker", "AAA", 110.0, 400.0, value=1100.0, qty=10.0)]

    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    w = _win(out, "AAA", "1d", acct="Broker")
    assert w["pnl"] == pytest.approx(100.0)        # 10 x (110 - 100)
    assert w["start_value"] == pytest.approx(1000.0)
    assert w["pct"] == pytest.approx(10.0)


def test_position_opened_inside_the_window_reports_value_minus_cost(stub_prices):
    """A stock bought this morning shows `mark - cost`, not a full day of
    movement it missed.  It needs no historical price at all."""
    from src.analytics.position_pnl import compute_position_pnl
    # Deliberately NO price seeded before today: if the module reached
    # for a boundary price here it would report None and fail.
    _seed("NEW", {"2024-06-14": 110.0})
    state = {"lots": {("Broker", "NEW"): [_lot(AS_OF, 10.0, 100.0)]}}
    holdings = [_holding("Broker", "NEW", 110.0, 1000.0, value=1100.0, qty=10.0)]

    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    w = _win(out, "NEW", "1d", acct="Broker")
    assert w["pnl"] == pytest.approx(100.0)        # 1100 - 1000
    assert w["start_value"] == pytest.approx(1000.0)   # cost, not a mark


def test_mixed_lots_sum_both_branches(stub_prices):
    """Old shares contribute market movement, new shares contribute
    gain-over-cost — the whole point of doing this per lot."""
    from src.analytics.position_pnl import compute_position_pnl
    _seed("MIX", {"2024-06-13": 100.0, "2024-06-14": 110.0})
    state = {"lots": {("Broker", "MIX"): [
        _lot("2020-01-01", 10.0, 40.0),    # held across: 10 x 10 = 100
        _lot(AS_OF, 5.0, 105.0),           # bought today: 5 x (110-105) = 25
    ]}}
    holdings = [_holding("Broker", "MIX", 110.0, 925.0, value=1650.0, qty=15.0)]

    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    w = _win(out, "MIX", "1d", acct="Broker")
    assert w["pnl"] == pytest.approx(125.0)
    # Start value: yesterday's mark on the old shares + cost of the new.
    assert w["start_value"] == pytest.approx(1000.0 + 525.0)


def test_only_open_lots_count_so_realized_stays_out(stub_prices):
    """A partially-sold position reports the shares still held.  Realized
    gain belongs to the Performance tab, not to a column labelled
    *open* — the sold shares must not leave a trace here."""
    from src.analytics.position_pnl import compute_position_pnl
    _seed("SOLD", {"2024-06-13": 100.0, "2024-06-14": 110.0})
    # Started at 10 shares, sold 6 today; the walker leaves 4 open.
    state = {"lots": {("Broker", "SOLD"): [_lot("2020-01-01", 4.0, 40.0)]}}
    holdings = [_holding("Broker", "SOLD", 110.0, 160.0, value=440.0, qty=4.0)]
    txns = [{"date": AS_OF, "account_group": "Broker", "symbol": "SOLD",
             "action": "Sell"}]

    out = compute_position_pnl(state, holdings, txns, as_of=AS_OF)
    row = out["by_account"][0]
    assert row["windows"]["1d"]["pnl"] == pytest.approx(40.0)   # 4 x 10
    # ...and the row is flagged, because that figure omits the realized
    # gain on the six shares that left.
    assert "1d" in row["traded_in"]
    assert "1y" in row["traded_in"]


def test_unpriceable_boundary_is_none_not_zero(stub_prices):
    """A fund whose historical close we cannot resolve must report an
    unknown move, not a confident flat one."""
    from src.analytics.position_pnl import compute_position_pnl
    # No cache entry at all — the module passes no txn-price fallback,
    # so `mark` has nothing to resolve from.
    state = {"lots": {("401K", "Some Fund Name"): [
        _lot("2020-01-01", 10.0, 40.0)]}}
    holdings = [_holding("401K", "Some Fund Name", 110.0, 400.0,
                         value=1100.0, qty=10.0, acct_type="Retirement")]

    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    row = out["by_account"][0]
    assert all(row["windows"][w] is None for w in ("1d", "1w", "1y"))
    # The all-time column still works — it needs no historical price.
    assert row["open_pnl"] == pytest.approx(700.0)
    assert row["open_pnl_pct"] == pytest.approx(175.0)


def test_cash_reports_no_window_figures(stub_prices):
    """A savings balance's gain is accrued interest against principal,
    which arrives as transactions rather than price movement.  Zero
    would read as a flat position rather than an inapplicable column."""
    from src.analytics.position_pnl import compute_position_pnl
    holdings = [_holding("Apple Savings", "USD", 1.0, 5000.0,
                         value=5100.0, qty=5100.0, acct_type="Savings")]
    out = compute_position_pnl({"lots": {}}, holdings, [], as_of=AS_OF)
    row = out["by_account"][0]
    assert all(v is None for v in row["windows"].values())
    assert row["open_pnl"] == pytest.approx(100.0)


def test_option_window_applies_the_contract_multiplier(stub_prices):
    """Quantity is CONTRACTS and price is the per-share premium, so a
    window move scales by 100 exactly like every other valuation site."""
    from src.analytics.position_pnl import compute_position_pnl
    sym = "META 12/18/2026 Call $800.00"
    state = {"lots": {("Broker", sym): [_lot("2024-01-02", 2.0, 300.0)]}}
    holdings = [_holding("Broker", sym, 12.0, 600.0, value=2400.0, qty=2.0)]
    # Options are never cache-priced (multi-word symbols aren't fetched),
    # so the boundary mark comes from the underlying's intrinsic floor.
    _seed("META", {"2024-06-13": 810.0, "2024-06-14": 812.0})

    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    w = _win(out, sym, "1d", acct="Broker")
    # Boundary intrinsic = 810 - 800 = 10/share; now 12/share.
    # 2 contracts x (12 - 10) x 100 = 400.
    assert w["pnl"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------

def test_by_symbol_sums_across_accounts(stub_prices):
    from src.analytics.position_pnl import compute_position_pnl
    _seed("DUP", {"2024-06-13": 100.0, "2024-06-14": 110.0})
    state = {"lots": {
        ("Broker", "DUP"):   [_lot("2020-01-01", 10.0, 40.0)],
        ("Roth IRA", "DUP"): [_lot("2021-01-01", 5.0, 60.0)],
    }}
    holdings = [
        _holding("Broker", "DUP", 110.0, 400.0, value=1100.0, qty=10.0),
        _holding("Roth IRA", "DUP", 110.0, 300.0, value=550.0, qty=5.0,
                 acct_type="Retirement"),
    ]
    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    assert len(out["by_symbol"]) == 1
    row = out["by_symbol"][0]
    assert row["accounts"] == ["Broker", "Roth IRA"]
    assert row["quantity"] == pytest.approx(15.0)
    assert row["value"] == pytest.approx(1650.0)
    assert row["windows"]["1d"]["pnl"] == pytest.approx(150.0)  # 15 x 10
    assert row["windows"]["1d"]["pct"] == pytest.approx(10.0)


def test_by_symbol_window_is_unknown_when_a_leg_is(stub_prices):
    """Partial coverage is not a number.  One unpriceable leg makes the
    symbol's window unknown rather than quietly short."""
    from src.analytics.position_pnl import compute_position_pnl
    state = {"lots": {
        ("Broker", "HALF"):   [_lot("2020-01-01", 10.0, 40.0)],
        ("Roth IRA", "HALF"): [_lot("2021-01-01", 5.0, 60.0)],
    }}
    holdings = [
        _holding("Broker", "HALF", 110.0, 400.0, value=1100.0, qty=10.0),
        _holding("Roth IRA", "HALF", 110.0, 300.0, value=550.0, qty=5.0,
                 acct_type="Retirement"),
    ]
    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)
    assert out["by_symbol"][0]["windows"]["1d"] is None


def test_windows_are_ordered_and_labelled(stub_prices):
    from src.analytics.position_pnl import compute_position_pnl
    holdings = [_holding("Broker", "AAA", 1.0, 1.0, value=1.0, qty=1.0)]
    out = compute_position_pnl({"lots": {}}, holdings, [], as_of=AS_OF)
    assert [w["key"] for w in out["windows"]] == \
        ["1d", "1w", "1m", "3m", "ytd", "1y"]
    assert [w["label"] for w in out["windows"]] == \
        ["1D", "1W", "1M", "3M", "YTD", "1Y"]
    assert out["as_of"] == AS_OF


def test_empty_holdings_yields_nothing(stub_prices):
    from src.analytics.position_pnl import compute_position_pnl
    assert compute_position_pnl({"lots": {}}, [], [], as_of=AS_OF) is None


def test_board_rows_anchor_to_the_holdings_table(stub_prices):
    """Every board row has a Holdings row and carries that row's own
    level figures.  The board is an ARRANGEMENT of the holdings data,
    not a second derivation of it — if these ever diverge, one of the
    two tables is lying about which positions exist."""
    from src.analytics.position_pnl import compute_position_pnl
    _seed("AAA", {"2024-06-13": 100.0, "2024-06-14": 110.0})
    state = {"lots": {
        ("Broker", "AAA"):   [_lot("2020-01-01", 10.0, 40.0)],
        ("Roth IRA", "AAA"): [_lot("2021-01-01", 5.0, 60.0)],
    }}
    holdings = [
        _holding("Broker", "AAA", 110.0, 400.0, value=1100.0, qty=10.0),
        _holding("Roth IRA", "AAA", 110.0, 300.0, value=550.0, qty=5.0,
                 acct_type="Retirement"),
        _holding("Apple Savings", "USD", 1.0, 5000.0, value=5100.0,
                 qty=5100.0, acct_type="Savings"),
    ]
    out = compute_position_pnl(state, holdings, [], as_of=AS_OF)

    keys_in = {(h["account_group"], h["symbol"]) for h in holdings}
    keys_out = {(r["account_group"], r["symbol"]) for r in out["by_account"]}
    assert keys_in == keys_out
    for h, r in zip(holdings, out["by_account"]):
        assert r["value"] == h["value"]
        assert r["price"] == h["price"]
        assert r["cost_basis"] == h["cost_basis"]
        assert r["open_pnl"] == h["unrealized_gain"]
    # ...and the symbol rollup sums the same money, cash included.
    assert sum(r["value"] for r in out["by_symbol"]) == pytest.approx(
        sum(h["value"] for h in holdings))
