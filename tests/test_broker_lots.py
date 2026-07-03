"""Report-directed lot relief (src/broker_lots.py + basis/history).

The broker's gain/loss report names the exact lots each disposal
consumed.  These tests pin: report parsing, date-directed consumption
(overriding the method order), the per-unit fallback for wrap-rescaled
lots, hint sharing across same-day sells, walker-copy isolation, and
basis↔history parity.
"""

from __future__ import annotations

import pytest


def _write_gainloss(path):
    path.write_text(
        "This report includes all taxable activity...\n"
        "\n"
        "Gain/loss report\n"
        "User,00000000-0000-0000-0000-000000000000,sam@example.com\n"
        "\n"
        "Transaction Type,Transaction ID,Tax lot ID,Asset name,Amount,"
        "Date Acquired,Cost basis (USD),Date of Disposition,"
        "Proceeds (USD),Gains (Losses) (USD),Holding period (Days),"
        "Data source\n"
        "Sell,tx-1,lot-1,ETH,2.0,09/02/2021,7600.00,06/25/2026,"
        "4800.00,-2800.00,1757,Customer provided\n"
        "Sell,tx-1,lot-2,ETH,1.0,03/01/2024,2000.00,06/25/2026,"
        "2400.00,400.00,846,Coinbase\n",
        encoding="utf-8")


def test_parse_gainloss_report(tmp_path):
    from src.broker_lots import load_disposal_lots
    _write_gainloss(tmp_path / "Coinbase-0-CB-GAINLOSSCSV.csv")
    lots = load_disposal_lots(tmp_path)
    assert lots is not None
    key = ("Coinbase", "ETH-USD", "2026-06-25")
    assert key in lots
    hints = lots[key]
    assert len(hints) == 2
    assert hints[0]["acquired"] == "2021-09-02"
    assert hints[0]["qty"] == 2.0
    assert hints[0]["per_unit"] == pytest.approx(3800.0)
    # No report file → None
    assert load_disposal_lots(tmp_path / "nowhere") is None


def _txn(date, action, qty, price=0.0, amount=0.0, symbol="ETH-USD"):
    return {"date": date, "account": "Coinbase", "account_group": "Coinbase",
            "account_type": "Taxable", "symbol": symbol, "action": action,
            "quantity": qty, "price": price, "fees": 0.0, "amount": amount,
            "description": "", "source": "test"}


def _pool_txns():
    """Three ETH lots: cheap 2020, expensive 2021, mid 2024."""
    return [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2024-03-01", "Buy", 1.0, 2000.0, 2000.0),
    ]


def _hints(**over):
    h = {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
         "per_unit": 3800.0}
    h.update(over)
    return {("Coinbase", "ETH-USD", "2026-06-25"): [h]}


def test_directed_consume_overrides_method_order(isolated_workdir):
    """FIFO would consume the cheap 2020 lot; the report hint directs
    the sale to the 2021 lot."""
    from src.basis import compute_basis_default
    txns = _pool_txns() + [_txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0)]
    plain = compute_basis_default([dict(t) for t in txns])
    assert plain["realized_total"] == pytest.approx(4800.0 - 400.0)

    directed = compute_basis_default(txns, disposal_lots=_hints())
    assert directed["realized_total"] == pytest.approx(4800.0 - 7600.0)
    sell = txns[-1]
    assert sell["cost_basis"] == pytest.approx(7600.0)
    assert sell["lot_breakdown"][0]["date_acquired"] == "2021-09-02"


def test_directed_consume_per_unit_fallback(isolated_workdir):
    """When the acquired date doesn't exist in the pool (date drift),
    the per-unit basis match finds the lot."""
    from src.basis import compute_basis_default
    txns = _pool_txns() + [_txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0)]
    hints = _hints(acquired="2021-06-15")   # wrong date, right per-unit
    out = compute_basis_default(txns, disposal_lots=hints)
    assert out["realized_total"] == pytest.approx(4800.0 - 7600.0)


def test_directed_consume_unmatched_falls_back(isolated_workdir):
    """A hint matching nothing (wrong date AND wrong per-unit) falls
    back to the account's method order — never drops the disposal."""
    from src.basis import compute_basis_default
    txns = _pool_txns() + [_txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0)]
    hints = _hints(acquired="2019-01-01", per_unit=99999.0)
    out = compute_basis_default(txns, disposal_lots=hints)
    # FIFO fallback: cheap 2020 lot.
    assert out["realized_total"] == pytest.approx(4800.0 - 400.0)


def test_same_day_sells_share_hints(isolated_workdir):
    """Two same-day sells consume the day's hint list progressively —
    the second sell doesn't re-consume the same hint."""
    from src.basis import compute_basis_default
    txns = _pool_txns() + [
        _txn("2026-06-25", "Sell", 1.0, 2400.0, 2400.0),
        _txn("2026-06-25", "Sell", 1.0, 2400.0, 2400.0),
    ]
    out = compute_basis_default(txns, disposal_lots=_hints())
    # Both sells drew from the 2021 lot (qty 2 hint): total basis 7600.
    assert out["realized_total"] == pytest.approx(4800.0 - 7600.0)


def test_walker_copies_hints_between_walks(isolated_workdir):
    """Each walk starts from fresh hint state — running the walker twice
    with the same dict must give identical results."""
    from src.basis import compute_basis_default
    hints = _hints()
    t1 = _pool_txns() + [_txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0)]
    t2 = [dict(t) for t in t1]
    r1 = compute_basis_default(t1, disposal_lots=hints)
    r2 = compute_basis_default(t2, disposal_lots=hints)
    assert r1["realized_total"] == pytest.approx(r2["realized_total"])


def test_directed_consume_through_wrap(isolated_workdir):
    """A hint naming the original acquisition date finds the lot even
    after a wrap moved it to another symbol (wraps preserve dates)."""
    from src.basis import compute_basis_default
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2026-01-10", "Wrap Asset Out", 2.0, price=2400.0),
        _txn("2026-01-10", "Wrap Asset In", 1.8, price=2600.0,
             symbol="CBETH-USD"),
        _txn("2026-06-25", "Sell", 1.8, 2700.0, 4860.0,
             symbol="CBETH-USD"),
    ]
    # Report says the CBETH sale consumed the 2021-09-02 lot.
    hints = {("Coinbase", "CBETH-USD", "2026-06-25"): [
        {"acquired": "2021-09-02", "qty": 1.8, "qty_left": 1.8,
         "per_unit": 7600.0 / 1.8}]}
    out = compute_basis_default(txns, disposal_lots=hints,
                                account_methods={"Coinbase": "hifo"})
    # HIFO wrap carried the 2021 lot (highest basis) into CBETH, dates
    # preserved; the directed sale consumed it → basis 7600.
    sell = txns[-1]
    assert sell["cost_basis"] == pytest.approx(7600.0)
    assert sell["lot_breakdown"][0]["date_acquired"] == "2021-09-02"


def test_history_mirrors_directed_consume(stub_prices):
    """The snapshot walker consumes the same report-directed lots — the
    latest snapshot's remaining basis matches the basis walker."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("ETH-USD", {"2026-06-30": 2400.0})
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0),
    ]
    hints = _hints()
    history = compute_history([dict(t) for t in txns],
                              {"ETH-USD": "Cryptocurrency"},
                              disposal_lots=hints)
    last = history[-1]
    eth = [p for p in last["positions"] if p["symbol"] == "ETH-USD"]
    assert len(eth) == 1
    # The 2021 lot was consumed → cheap 2020 lot remains.
    assert eth[0]["cost_basis"] == pytest.approx(400.0)

    fifo = compute_basis_default(txns, disposal_lots=hints)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(
        sum(p["cost_basis"] for p in last["positions"]
            if p["symbol"] != "USD"), abs=0.02)


# ---------------------------------------------------------------------------
# RAWTX acquisition basis + the Neutral rebase
# ---------------------------------------------------------------------------

def _write_rawtx(path):
    # Comma-containing column names are quoted, exactly as Coinbase
    # emits them.
    path.write_text(
        'Transaction ID,Transaction Type,Date & time,Asset Acquired,'
        '"Quantity Acquired (Bought, Received, etc)",'
        'Cost Basis (incl. fees and/or spread) (USD),Data Source,'
        '"Asset Disposed (Sold, Sent, etc)",Quantity Disposed,'
        'Proceeds (excl. fees and/or spread) (USD)\n'
        "id-1,Buy,2020-05-10T10:00:00Z,ETH,2,410.00,Coinbase,,,\n"
        "id-2,Convert,2021-09-02T12:00:00Z,ETH,4,13768.00,Coinbase,ETH2,4,\n"
        "id-3,Receive,2021-07-09T09:00:00Z,ETH,1,2100.00,Customer provided,,,\n"
        "id-4,Wrap,2024-02-29T08:00:00Z,CBETH,1.9,6000.00,Coinbase,ETH,2,\n"
        "id-5,Convert,2022-03-05T10:00:00Z,ETH,6,16019.95,Coinbase,BTC,0.4,\n",
        encoding="utf-8")


def test_load_acquisition_lots(tmp_path):
    from src.broker_lots import load_acquisition_lots
    _write_rawtx(tmp_path / "Coinbase-0-CB-RAWTX.csv")
    rows = load_acquisition_lots(tmp_path)
    assert rows is not None
    types = {r["type"] for r in rows}
    # Wrap excluded (carry); the ETH2→ETH Convert excluded (same-symbol
    # conversion — its basis arrives via the Receive rows); the BTC→ETH
    # Convert (cross-symbol) included.
    assert types == {"Buy", "Convert", "Receive"}
    conv = [r for r in rows if r["type"] == "Convert"]
    assert len(conv) == 1
    assert conv[0]["date"] == "2022-03-05"
    assert conv[0]["basis"] == pytest.approx(16019.95)
    assert not [r for r in rows if r["date"] == "2021-09-02"]


def test_stamp_acquisition_basis_respects_user_rows(isolated_workdir):
    from src.broker_lots import stamp_acquisition_basis
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-07-09", "Transfer In", 1.0, 0.0, 0.0),
    ]
    txns[1]["basis_override"] = 1999.0       # user Cost Basis row won already
    rows = [
        {"symbol": "ETH-USD", "date": "2020-05-10", "qty": 2.0,
         "basis": 410.0, "type": "Buy"},
        {"symbol": "ETH-USD", "date": "2021-07-09", "qty": 1.0,
         "basis": 2100.0, "type": "Receive"},
    ]
    stamped, unmatched = stamp_acquisition_basis(txns, rows)
    assert stamped == 1                       # only the Buy
    assert unmatched == 1                     # Receive txn already claimed
    assert txns[0]["basis_override"] == 410.0
    assert txns[1]["basis_override"] == 1999.0   # untouched


def test_neutral_rebase_zero_gain(isolated_workdir):
    """A Neutral same-pool conversion (ETH2 deprecation) stamped with a
    broker basis rebases the pool at zero realized gain."""
    from src.basis import (compute_basis_default,
                           derive_basis_by_key_from_txns,
                           state_to_holdings)
    txns = [
        _txn("2020-01-15", "Buy", 4.0, 130.0, 520.0),      # cheap ETH2-era
        _txn("2021-09-02", "Neutral", 4.0, 3442.0, 0.0),   # deprecation
        _txn("2024-01-10", "Sell", 4.0, 2500.0, 10000.0),
    ]
    txns[1]["basis_override"] = 13768.0
    state = compute_basis_default(txns)
    # Rebase booked no gain; the sale realizes vs the rebased basis.
    assert state["realized_total"] == pytest.approx(10000.0 - 13768.0)
    assert txns[1]["basis_effect"] == "rebase_neutral"
    assert txns[1].get("realized_gain") in (None, 0)
    # Net-delta annotation reconstructs the pool change exactly.
    by_key = derive_basis_by_key_from_txns(txns)
    assert by_key[("Coinbase", "ETH-USD")] == pytest.approx(0.0, abs=0.01)
    holdings = state_to_holdings(state, "fifo")
    assert not [h for h in holdings if h["symbol"] == "ETH-USD"]  # sold out


def test_neutral_without_override_stays_noop(isolated_workdir):
    from src.basis import compute_basis_default
    txns = [
        _txn("2020-01-15", "Buy", 4.0, 130.0, 520.0),
        _txn("2021-09-02", "Neutral", 4.0, 3442.0, 0.0),
        _txn("2024-01-10", "Sell", 4.0, 2500.0, 10000.0),
    ]
    state = compute_basis_default(txns)
    assert state["realized_total"] == pytest.approx(10000.0 - 520.0)


def test_history_mirrors_neutral_rebase(stub_prices):
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("ETH-USD", {"2024-06-30": 2400.0})
    txns = [
        _txn("2020-01-15", "Buy", 4.0, 130.0, 520.0),
        _txn("2021-09-02", "Neutral", 4.0, 3442.0, 0.0),
    ]
    txns[1]["basis_override"] = 13768.0
    history = compute_history([dict(t) for t in txns],
                              {"ETH-USD": "Cryptocurrency"})
    last = history[-1]
    eth = [p for p in last["positions"] if p["symbol"] == "ETH-USD"]
    assert eth[0]["cost_basis"] == pytest.approx(13768.0)

    fifo = compute_basis_default(txns)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(13768.0)
