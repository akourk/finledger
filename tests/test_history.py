"""Unit tests for ``history.compute_history``.

The history walker is the most consequential function in the pipeline:
TWR / FIRE / drawdown / monthly P&L all consume its snapshots.
Previously only the end-to-end pipeline-snapshot test exercised it,
which is fragile (any number drift forces an assertion update).  These
unit tests pin the high-leverage invariants directly with synthetic
inputs so refactor regressions surface immediately.
"""

from __future__ import annotations

import pytest


def _txn(date, account_group, account_type, symbol, action, qty,
         amount=0.0, price=0.0, raw_action=None, source="manual.csv",
         description="", account=None):
    """Build a normalized txn dict.  Mirrors what main.py produces
    after parsing + normalization + account tagging."""
    return {
        "date": date,
        "account": account or account_group,
        "account_group": account_group,
        "account_type": account_type,
        "symbol": symbol,
        "action": action,
        "raw_action": raw_action or action,
        "quantity": qty,
        "price": price,
        "fees": 0.0,
        "amount": amount,
        "description": description,
        "source": source,
    }


def _populate_cache(stub_prices, symbols_dates):
    """Run the symbols through ensure_coverage so the stubbed
    ``_fetch_range`` populates the on-disk cache.  ``symbols_dates``
    is ``{symbol: [date1, date2, ...]}`` — fetch range is the min-to-
    max for each symbol.
    """
    from datetime import datetime
    from src.prices import ensure_coverage
    for sym, dates in symbols_dates.items():
        if not dates:
            continue
        start = datetime.strptime(min(dates), "%Y-%m-%d").date()
        end   = datetime.strptime(max(dates), "%Y-%m-%d").date()
        ensure_coverage([sym], start, end)


def test_history_smoke_buy_then_hold(stub_prices):
    """Single Buy + monthly snapshots → value tracks the price curve."""
    from src.history import compute_history

    stub_prices.set("AAPL", {
        "2024-01-31": 180.0,
        "2024-02-29": 185.0,
        "2024-03-31": 190.0,
        "2024-04-30": 195.0,
    })
    _populate_cache(stub_prices, {"AAPL": ["2024-01-31", "2024-04-30"]})
    txns = [
        _txn("2024-01-15", "Robinhood", "Taxable", "AAPL", "Buy", 10,
             amount=1700.0, price=170.0),
    ]
    sector_of = {"AAPL": "Technology"}
    history = compute_history(txns, sector_of)
    assert len(history) >= 4

    by_date = {h["date"]: h for h in history}
    assert by_date["2024-01-31"]["total"] == pytest.approx(1800.0)
    assert by_date["2024-04-30"]["total"] == pytest.approx(1950.0)
    assert by_date["2024-04-30"]["by_account_group"]["Robinhood"] == pytest.approx(1950.0)
    assert by_date["2024-04-30"]["by_account_type"]["Taxable"] == pytest.approx(1950.0)
    assert by_date["2024-04-30"]["by_sector"]["Technology"] == pytest.approx(1950.0)


def test_history_buy_then_sell_zeroes_position(stub_prices):
    """Buy → Sell sequence drives the position balance back to zero;
    final snapshot total drops to zero (taxable USD not tracked)."""
    from src.history import compute_history

    stub_prices.set("AAPL", {
        "2024-01-31": 180.0,
        "2024-02-29": 200.0,
        "2024-03-31": 195.0,
    })
    _populate_cache(stub_prices, {"AAPL": ["2024-01-31", "2024-03-31"]})
    txns = [
        _txn("2024-01-15", "Robinhood", "Taxable", "AAPL", "Buy", 10,
             amount=1700.0, price=170.0),
        # After this, balance = 0; USD proceeds aren't tracked for non-Savings.
        _txn("2024-02-20", "Robinhood", "Taxable", "AAPL", "Sell", 10,
             amount=2000.0, price=200.0),
    ]
    history = compute_history(txns, {"AAPL": "Technology"})
    by_date = {h["date"]: h for h in history}
    # Pre-sell snapshot: still holding 10 AAPL @ $180 = $1800
    assert by_date["2024-01-31"]["total"] == pytest.approx(1800.0)
    # Post-sell snapshot: zero position, no USD tracking → $0 total.
    # This is the artifact the Coinbase bridge fixes; for Robinhood
    # we accept it (data isn't complete enough for a similar fix).
    assert by_date["2024-03-31"]["total"] == pytest.approx(0.0)


def test_history_dust_filter_excludes_sub_penny(stub_prices):
    """Positions with |qty × price| < $0.01 must be dropped from
    snapshot positions (mirrors main.py's _is_dust)."""
    from src.history import compute_history

    stub_prices.set("DUST", {"2024-01-31": 0.0001})
    _populate_cache(stub_prices, {"DUST": ["2024-01-31"]})
    txns = [
        _txn("2024-01-15", "Robinhood", "Taxable", "DUST", "Buy", 0.01,
             amount=0.01, price=0.0001),
    ]
    history = compute_history(txns, {"DUST": "Other"})
    by_date = {h["date"]: h for h in history}
    # 0.01 qty × $0.0001 = $0.000001 → dust.
    assert by_date["2024-01-31"]["total"] == pytest.approx(0.0)
    assert not any(p["symbol"] == "DUST" for p in by_date["2024-01-31"]["positions"])


def test_history_apple_savings_tracks_usd_balance(stub_prices):
    """USD in Savings accounts IS tracked.  Deposit / Interest / Withdrawal
    feed the running balance and surface in `by_account_group`."""
    from src.history import compute_history

    txns = [
        _txn("2024-01-15", "Apple Savings", "Savings", "USD", "Deposit", 5000,
             amount=5000.0, price=1.0),
        _txn("2024-02-15", "Apple Savings", "Savings", "USD", "Interest", 50,
             amount=50.0, price=1.0),
        _txn("2024-03-15", "Apple Savings", "Savings", "USD", "Withdrawal", 1000,
             amount=1000.0, price=1.0),
    ]
    history = compute_history(txns, {"USD": "Cash"})
    by_date = {h["date"]: h for h in history}
    assert by_date["2024-01-31"]["by_account_group"]["Apple Savings"] == pytest.approx(5000.0)
    assert by_date["2024-02-29"]["by_account_group"]["Apple Savings"] == pytest.approx(5050.0)
    assert by_date["2024-03-31"]["by_account_group"]["Apple Savings"] == pytest.approx(4050.0)
    # Cash sector total tracks alongside.
    assert by_date["2024-03-31"]["by_sector"]["Cash"] == pytest.approx(4050.0)


def test_history_coinbase_usd_bridge_during_sell_buy_gap(stub_prices):
    """The Coinbase USD bridge must surface USD value at snapshots
    that fall between a Sell and the next Buy/Withdrawal.  Without
    the bridge, the Sell→Buy gap shows $0 portfolio value (artifact).
    """
    from src.history import compute_history

    stub_prices.set("BTC-USD", {
        "2024-01-31": 40000.0,
        "2024-02-29": 50000.0,   # Sell happens mid-Feb
        "2024-03-31": 45000.0,   # Gap month: holding USD
        "2024-04-30": 60000.0,
    })
    _populate_cache(stub_prices, {"BTC-USD": ["2024-01-31", "2024-04-30"]})
    txns = [
        _txn("2024-01-10", "Coinbase", "Taxable", "BTC-USD", "Buy", 1.0,
             amount=40000.0, price=40000.0,
             description="Bought 1 BTC for 40000 USD using bank account ****",
             source="coinbase-1.csv"),
        _txn("2024-02-15", "Coinbase", "Taxable", "BTC-USD", "Sell", 1.0,
             amount=50000.0, price=50000.0,
             raw_action="Advanced Trade Sell",
             source="coinbase-1.csv"),
        # User redeploys mid-April after holding USD a month
        _txn("2024-04-15", "Coinbase", "Taxable", "BTC-USD", "Buy", 1.0,
             amount=50000.0, price=50000.0,
             description="Bought 1 BTC for 50000 USD using USD Wallet",
             source="coinbase-1.csv"),
    ]
    history = compute_history(txns, {"BTC-USD": "Cryptocurrency"})
    by_date = {h["date"]: h for h in history}

    # Jan 31: holding 1 BTC @ $40k = $40k.
    assert by_date["2024-01-31"]["total"] == pytest.approx(40000.0)
    # Feb 29: sold mid-Feb, holding $50k USD; bridge surfaces it.
    assert by_date["2024-02-29"]["total"] == pytest.approx(50000.0)
    # Mar 31: still holding USD (no Buy/Withdrawal yet); bridge persists.
    assert by_date["2024-03-31"]["total"] == pytest.approx(50000.0)
    # April 30: USD redeployed mid-April, BTC now $60k.
    assert by_date["2024-04-30"]["total"] == pytest.approx(60000.0)
    # USD position appears in the gap-month positions list.
    mar_positions = by_date["2024-03-31"]["positions"]
    usd_pos = [p for p in mar_positions if p["account_group"] == "Coinbase"
               and p["symbol"] == "USD"]
    assert len(usd_pos) == 1
    assert usd_pos[0]["value"] == pytest.approx(50000.0)


def test_history_total_equals_sum_of_by_account_group(stub_prices):
    """Snapshot-level rollup invariant: sum(by_account_group) == total
    for every snapshot.  Catches accidental double-counting or
    omission when the Coinbase bridge or basis walker is changed."""
    from src.history import compute_history

    stub_prices.set("AAPL", {"2024-01-31": 180.0, "2024-02-29": 190.0})
    stub_prices.set("VOO",  {"2024-01-31": 450.0, "2024-02-29": 460.0})
    _populate_cache(stub_prices, {
        "AAPL": ["2024-01-31", "2024-02-29"],
        "VOO":  ["2024-01-31", "2024-02-29"],
    })
    txns = [
        _txn("2024-01-10", "Robinhood", "Taxable", "AAPL", "Buy", 10,
             amount=1700.0, price=170.0),
        _txn("2024-01-12", "Roth IRA",  "Retirement", "VOO", "Buy", 5,
             amount=2200.0, price=440.0),
        _txn("2024-01-20", "Apple Savings", "Savings", "USD", "Deposit", 3000,
             amount=3000.0, price=1.0),
    ]
    history = compute_history(txns, {"AAPL": "Technology", "VOO": "Mutual Funds",
                                      "USD": "Cash"})
    for h in history:
        sum_groups = sum(h["by_account_group"].values())
        assert sum_groups == pytest.approx(h["total"], abs=0.01), (
            f"by_account_group sum != total at {h['date']}: "
            f"{sum_groups:.2f} vs {h['total']:.2f}"
        )


def test_history_priced_pct_one_when_all_have_prices(stub_prices):
    """priced_pct == 1.0 when every position has a cache price.
    Catches cases where the cache lookup silently returns None for
    symbols we expected to have data."""
    from src.history import compute_history

    stub_prices.set("AAPL", {"2024-01-31": 180.0})
    _populate_cache(stub_prices, {"AAPL": ["2024-01-31"]})
    txns = [
        _txn("2024-01-10", "Robinhood", "Taxable", "AAPL", "Buy", 10,
             amount=1700.0, price=170.0),
    ]
    history = compute_history(txns, {"AAPL": "Technology"})
    by_date = {h["date"]: h for h in history}
    assert by_date["2024-01-31"]["priced_pct"] == pytest.approx(1.0)


def test_history_cost_basis_matches_holdings_exactly(stub_prices):
    """Latest-snapshot cost basis must equal the FIFO basis walker's
    output for live holdings.  This is the invariant that drove
    pulling the basis pairing logic into history.py — without it,
    the dashboard's history-chart cost-basis line diverged from the
    Holdings table for accounts with cross-account transfers."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("AAPL", {
        "2024-01-31": 180.0, "2024-02-29": 190.0, "2024-03-31": 200.0,
    })
    _populate_cache(stub_prices, {"AAPL": ["2024-01-31", "2024-03-31"]})
    txns = [
        _txn("2024-01-10", "Robinhood", "Taxable", "AAPL", "Buy", 10,
             amount=1700.0, price=170.0),
        _txn("2024-02-15", "Robinhood", "Taxable", "AAPL", "Buy", 5,
             amount=900.0, price=180.0),
    ]
    history = compute_history(txns, {"AAPL": "Technology"})
    fifo = compute_basis_default(txns)
    holdings = state_to_holdings(fifo, "fifo")
    expected_basis = sum(h["cost_basis"] for h in holdings)
    last = history[-1]
    assert last["total_cost_basis"] == pytest.approx(expected_basis, abs=0.02)


def test_history_wrap_carries_basis_to_destination(stub_prices):
    """ETH→CBETH wrap must carry the source basis to the destination
    symbol in the snapshot lot walker, matching basis.py.  Regression:
    the wrap basis-carrying fix landed in basis.py only, so the latest
    snapshot reported CBETH basis $0 while the Holdings table said
    $5,000."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("ETH-USD",   {"2022-01-31": 2600.0, "2022-06-30": 3000.0})
    stub_prices.set("CBETH-USD", {"2022-06-30": 3100.0})
    _populate_cache(stub_prices, {
        "ETH-USD":   ["2022-01-31", "2022-06-30"],
        "CBETH-USD": ["2022-06-30"],
    })
    txns = [
        _txn("2022-01-05", "Coinbase", "Taxable", "ETH-USD", "Buy", 2.0,
             amount=5000.0, price=2500.0),
        _txn("2022-06-01", "Coinbase", "Taxable", "ETH-USD",
             "Wrap Asset Out", 2.0, price=3000.0),
        _txn("2022-06-01", "Coinbase", "Taxable", "CBETH-USD",
             "Wrap Asset In", 1.9, price=3100.0),
    ]
    history = compute_history(txns, {"ETH-USD": "Cryptocurrency",
                                     "CBETH-USD": "Cryptocurrency"})
    last = history[-1]
    cbeth = [p for p in last["positions"] if p["symbol"] == "CBETH-USD"]
    assert len(cbeth) == 1
    assert cbeth[0]["cost_basis"] == pytest.approx(5000.0)
    assert last["total_cost_basis"] == pytest.approx(5000.0)

    # And it agrees with the basis walker's view exactly.
    fifo = compute_basis_default(txns)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(
        last["total_cost_basis"], abs=0.02)


def test_history_unpaired_transfer_in_uses_fmv_basis(stub_prices):
    """An external Transfer In with a recorded spot price gets FMV basis
    in the snapshot walker (matches basis.py), not $0."""
    from src.history import compute_history

    stub_prices.set("BTC-USD", {"2023-01-31": 25000.0})
    _populate_cache(stub_prices, {"BTC-USD": ["2023-01-31"]})
    txns = [
        _txn("2023-01-10", "Coinbase", "Taxable", "BTC-USD", "Transfer In",
             0.5, price=20000.0),
    ]
    history = compute_history(txns, {"BTC-USD": "Cryptocurrency"})
    last = history[-1]
    pos = [p for p in last["positions"] if p["symbol"] == "BTC-USD"]
    assert len(pos) == 1
    assert pos[0]["cost_basis"] == pytest.approx(10000.0)   # 0.5 × $20k FMV


def test_history_honours_basis_override(stub_prices):
    """A metadata Cost Basis override stamped onto a lot-creating txn
    must flow into snapshot cost basis, same as the basis walker."""
    from src.history import compute_history

    stub_prices.set("ETH-USD", {"2023-01-31": 1800.0})
    _populate_cache(stub_prices, {"ETH-USD": ["2023-01-31"]})
    t = _txn("2023-01-10", "Coinbase", "Taxable", "ETH-USD", "Transfer In",
             2.0, price=1500.0)
    t["basis_override"] = 1234.56   # user-supplied off-platform basis
    history = compute_history([t], {"ETH-USD": "Cryptocurrency"})
    pos = [p for p in history[-1]["positions"] if p["symbol"] == "ETH-USD"]
    assert pos[0]["cost_basis"] == pytest.approx(1234.56)


def test_history_respects_account_lot_method(stub_prices):
    """With a HIFO override for an account, the snapshot walker must
    relieve the same lots as the annotated basis walk — otherwise the
    latest snapshot's remaining basis diverges from the holdings table."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("BTC-USD", {"2024-01-31": 45000.0, "2024-02-29": 50000.0})
    _populate_cache(stub_prices, {"BTC-USD": ["2024-01-31", "2024-02-29"]})
    txns = [
        _txn("2024-01-05", "Coinbase", "Taxable", "BTC-USD", "Buy", 1.0,
             amount=20000.0, price=20000.0),
        _txn("2024-01-06", "Coinbase", "Taxable", "BTC-USD", "Buy", 1.0,
             amount=40000.0, price=40000.0),
        # HIFO relieves the $40k lot; FIFO would relieve the $20k lot.
        _txn("2024-02-10", "Coinbase", "Taxable", "BTC-USD", "Sell", 1.0,
             amount=50000.0, price=50000.0),
    ]
    methods = {"Coinbase": "hifo"}
    history = compute_history(txns, {"BTC-USD": "Cryptocurrency"},
                              account_methods=methods)
    last = history[-1]
    pos = [p for p in last["positions"] if p["symbol"] == "BTC-USD"]
    assert pos[0]["cost_basis"] == pytest.approx(20000.0)   # $40k lot relieved

    fifo = compute_basis_default(txns, account_methods=methods)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(
        pos[0]["cost_basis"], abs=0.02)


def test_history_empty_input_returns_empty(stub_prices):
    from src.history import compute_history
    assert compute_history([], {}) == []
    # Txns with no dates → no usable history.
    assert compute_history([{"date": "", "symbol": "AAPL"}], {}) == []


def test_history_intra_group_rebase_matches_basis_walker(stub_prices):
    """An intra-group transfer pair with a user basis_override rebases
    the lot queue in the snapshot walker too — the latest snapshot's
    cost basis must match the Holdings table exactly (the
    history_holdings_basis_parity invariant)."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("ETH-USD", {"2021-09-30": 3400.0, "2024-01-31": 2400.0})
    _populate_cache(stub_prices, {
        "ETH-USD": ["2021-09-30", "2024-01-31"],
    })
    txns = [
        _txn("2021-01-05", "Coinbase", "Taxable", "ETH-USD", "Buy", 10.0,
             amount=1000.0, price=100.0),
        _txn("2021-09-02", "Coinbase", "Taxable", "ETH-USD",
             "Transfer In", 10.0),
        _txn("2021-09-02", "Coinbase", "Taxable", "ETH-USD",
             "Transfer Out", 10.0),
        _txn("2024-01-15", "Coinbase", "Taxable", "ETH-USD", "Sell", 5.0,
             amount=20000.0, price=4000.0),
    ]
    txns[1]["basis_override"] = 38000.0

    history = compute_history(txns, {"ETH-USD": "Cryptocurrency"})
    last = history[-1]
    eth = [p for p in last["positions"] if p["symbol"] == "ETH-USD"]
    assert len(eth) == 1
    assert eth[0]["cost_basis"] == pytest.approx(19000.0)

    # Snapshot non-cash basis == basis walker's view exactly.  (The
    # snapshot total ALSO carries the Coinbase implicit-USD bridge —
    # the sale proceeds as cash — which the lot walker rightly doesn't;
    # compare positions excluding USD.)
    snap_noncash = sum(p["cost_basis"] for p in last["positions"]
                       if p["symbol"] != "USD")
    fifo = compute_basis_default(txns)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(
        snap_noncash, abs=0.02)


def test_sample_dates_semimonthly_preserves_eom_boundaries():
    """Semimonthly = the monthly EOM set with mid-month (15th) points
    inserted between.  EOM preservation is a hard contract: monthly_pnl's
    latest-in-month picker and annual returns' year boundaries assume
    the same month-end dates the monthly cadence produced.
    """
    from src.history import _sample_dates

    monthly = _sample_dates("2024-01-10", "2024-04-20", "month")
    semi    = _sample_dates("2024-01-10", "2024-04-20", "semimonthly")

    # Every monthly sample survives unchanged (incl. leap-year Feb 29).
    assert set(monthly) <= set(semi)
    assert "2024-02-29" in semi
    # Mid-month points appear for each month inside the range.
    assert {"2024-01-15", "2024-02-15", "2024-03-15", "2024-04-15"} <= set(semi)
    assert len(semi) == len(monthly) + 4
    # Sorted, ends at `last`, no duplicates.
    assert semi == sorted(semi)
    assert semi[-1] == "2024-04-20"
    assert len(semi) == len(set(semi))
    # Range boundaries are respected: a 15th before `first` is excluded.
    late_start = _sample_dates("2024-01-16", "2024-02-20", "semimonthly")
    assert "2024-01-15" not in late_start


def test_daily_totals_track_price_curve(stub_prices):
    """compute_daily_totals values every calendar day from the daily
    price cache; weekends carry the prior close (get_price walkback)."""
    from src.history import compute_daily_totals

    stub_prices.set("AAPL", {
        "2024-01-15": 100.0,   # Monday
        "2024-01-16": 110.0,
        "2024-01-17": 90.0,
        "2024-01-18": 105.0,
        "2024-01-19": 108.0,   # Friday
    })
    _populate_cache(stub_prices, {"AAPL": ["2024-01-15", "2024-01-19"]})
    txns = [_txn("2024-01-15", "Robinhood", "Taxable", "AAPL", "Buy", 10,
                 amount=1000.0, price=100.0)]
    out = dict(compute_daily_totals(txns))
    assert out["2024-01-15"] == pytest.approx(1000.0)
    assert out["2024-01-16"] == pytest.approx(1100.0)
    assert out["2024-01-17"] == pytest.approx(900.0)   # the daily dip
    assert out["2024-01-20"] == pytest.approx(1080.0)  # Saturday carries Friday
    # Axis runs through today (txn-price fallback once cache ends).
    from datetime import datetime
    assert datetime.now().date().isoformat() in dict(compute_daily_totals(txns))


def test_drawdown_daily_resolution_catches_intra_sample_dip(stub_prices):
    """A crash that recovers between snapshot dates is invisible to the
    snapshot walk but must appear in the daily-resolution headline
    stats — while the exported series stays at snapshot cadence."""
    from src.analytics.drawdown import compute_drawdown

    history = [
        {"date": "2024-01-31", "total": 100000.0},
        {"date": "2024-02-29", "total": 102000.0},
        {"date": "2024-03-31", "total": 104000.0},
    ]
    daily = ([("2024-01-31", 100000.0), ("2024-02-10", 101000.0),
              ("2024-02-15", 60000.0),   # intra-month crash…
              ("2024-02-20", 99000.0),   # …recovered by month-end
              ("2024-02-29", 102000.0), ("2024-03-31", 104000.0)])

    out = compute_drawdown(history, None, daily_totals=daily)
    assert out["resolution"] == "daily"
    assert out["max_drawdown"] == pytest.approx(-0.40594, abs=1e-4)
    assert out["max_drawdown_window"]["trough_date"] == "2024-02-15"
    assert out["max_drawdown_window"]["peak_date"] == "2024-02-10"
    # Exported series stays snapshot-cadence (3 points, no dip visible).
    assert len(out["series"]) == 3
    assert all(p["drawdown_pct"] == 0.0 for p in out["series"])

    # Without daily data the dip is invisible and resolution says so.
    out2 = compute_drawdown(history, None)
    assert out2["resolution"] == "snapshot"
    assert out2["max_drawdown"] == 0.0
