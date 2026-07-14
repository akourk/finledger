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


# ---------------------------------------------------------------------------
# Robinhood consolidated 1099 → stock lot-relief hints
# ---------------------------------------------------------------------------

def _write_rh_1099(path):
    path.write_text(
        "1099-DIV,ACCOUNT NUMBER,TAX YEAR,ORDINARY DIV,QUALIFIED DIV\n"
        "1099-DIV,X,2024,10.00,10.00\n"
        "1099-INT,ACCOUNT NUMBER,TAX YEAR,PAYER RTN,INT INCOME\n"
        "1099-INT,X,2024,,5.00\n"
        "1099-B,ACCOUNT NUMBER,TAX YEAR,DATE ACQUIRED,SALE DATE,DESCRIPTION,"
        "SHARES,COST BASIS,SALES PRICE,TERM,WASH AMT DISALLOWED\n"
        "1099-B,X,2024,20190601,20240301,APPLE INC. COMMON STOCK,10,1000.00,2000.00,LONG,0\n"
        "1099-B,X,2024,20230601,20240301,APPLE INC. COMMON STOCK,5,900.00,1000.00,SHORT,0\n"
        "1099-B,X,2024,20240101,20240401,NVDA 06/21/2024 CALL $120.00,1,300.00,500.00,SHORT,0\n",
        encoding="utf-8")


def _rh_sell(date, symbol, qty, amount):
    return {"date": date, "account": "Robinhood", "account_group": "Robinhood",
            "account_type": "Taxable", "symbol": symbol, "action": "Sell",
            "quantity": qty, "price": 0.0, "fees": 0.0, "amount": amount,
            "description": "", "source": "test"}


def test_robinhood_1099_multi_section_parse(tmp_path):
    from src.broker_lots import _parse_1099b_rows
    _write_rh_1099(tmp_path / "robinhood-1099-2024.csv")
    rows = _parse_1099b_rows(tmp_path / "robinhood-1099-2024.csv")
    # Only the three 1099-B data rows (DIV/INT ignored); dates normalized.
    assert len(rows) == 3
    aapl = [r for r in rows if "APPLE" in r["desc"]]
    assert len(aapl) == 2
    assert aapl[0]["acquired"] == "2019-06-01"
    assert aapl[0]["sold"] == "2024-03-01"


def test_robinhood_1099_lots_resolves_symbol_by_txn(tmp_path):
    from src.broker_lots import load_robinhood_1099_lots
    _write_rh_1099(tmp_path / "robinhood-1099-2024.csv")
    # fin Sell of 15 AAPL on the sale date, proceeds 3000 (=2000+1000).
    txns = [_rh_sell("2024-03-01", "AAPL", 15.0, 3000.0)]
    hints = load_robinhood_1099_lots(tmp_path, txns)
    assert hints is not None
    key = ("Robinhood", "AAPL", "2024-03-01")
    assert key in hints
    lots = hints[key]
    assert len(lots) == 2                      # two tax lots
    assert {round(l["per_unit"], 2) for l in lots} == {100.0, 180.0}
    # The option row was skipped entirely.
    assert not any(k[1].endswith("$120.00") or "NVDA" in k[1] for k in hints)


def test_robinhood_1099_directs_realized_gain(isolated_workdir):
    """End-to-end: with the 1099-B hint, fin's AAPL sale consumes the
    lot the report names — changing which basis is realized vs FIFO."""
    from src.broker_lots import load_robinhood_1099_lots
    from src.basis import compute_basis_default
    # fin pool: a cheap 2019 lot + a pricier 2023 lot, both held.
    txns = [
        _rh_sell("2019-06-01", "AAPL", 0, 0),   # placeholder replaced below
    ]
    txns = [
        {"date": "2019-06-01", "account_group": "Robinhood", "account": "Robinhood",
         "account_type": "Taxable", "symbol": "AAPL", "action": "Buy",
         "quantity": 10, "price": 100.0, "fees": 0, "amount": 1000.0,
         "description": "", "source": "t"},
        {"date": "2023-06-01", "account_group": "Robinhood", "account": "Robinhood",
         "account_type": "Taxable", "symbol": "AAPL", "action": "Buy",
         "quantity": 5, "price": 180.0, "fees": 0, "amount": 900.0,
         "description": "", "source": "t"},
        _rh_sell("2024-03-01", "AAPL", 5.0, 1000.0),   # sell 5 sh
    ]
    # Report says the 2024-03-01 sale of 5 sh consumed the 2023 lot.
    hints = {("Robinhood", "AAPL", "2024-03-01"): [
        {"acquired": "2023-06-01", "qty": 5, "qty_left": 5, "per_unit": 180.0}]}
    directed = compute_basis_default(txns, disposal_lots=hints)
    # Directed → basis 900 (2023 lot): realized 1000 − 900 = 100.
    assert directed["realized_total"] == pytest.approx(100.0)
    plain = compute_basis_default([dict(t) for t in txns])
    # FIFO → cheap 2019 lot (basis 500): realized 1000 − 500 = 500.
    assert plain["realized_total"] == pytest.approx(500.0)


def test_robinhood_1099_income_rows(tmp_path):
    """DIV box 1a + INT box 1 sum into one auto Reconcile Income row per
    tax year, shaped like a parsed metadata row."""
    from src.broker_lots import load_robinhood_1099_income
    _write_rh_1099(tmp_path / "robinhood-1099-2024.csv")
    rows = load_robinhood_1099_income(tmp_path)
    assert rows == [{
        "kind": "income", "account_group": "Robinhood", "date": "2024",
        "amount": 15.0,
        "note": "auto: robinhood-1099-2024.csv (1099-DIV 1a + 1099-INT box 1)",
    }]


def test_merge_auto_reconcile_manual_row_wins(tmp_path):
    """A hand-entered Reconcile Income row for the same account/year
    suppresses the auto row; other years still merge in."""
    from src.broker_lots import (load_robinhood_1099_income,
                                 merge_auto_reconcile_rows)
    _write_rh_1099(tmp_path / "robinhood-1099-2024.csv")
    auto = load_robinhood_1099_income(tmp_path)
    manual = [{"kind": "income", "account_group": "Robinhood",
               "date": "2024", "amount": 14.5, "note": "hand-corrected"}]
    merged = merge_auto_reconcile_rows(manual, auto)
    assert len(merged) == 1 and merged[0]["amount"] == 14.5
    # Different kind for the same year still merges.
    manual2 = [{"kind": "realized", "account_group": "Robinhood",
                "date": "2024", "amount": 100.0, "note": ""}]
    merged2 = merge_auto_reconcile_rows(manual2, auto)
    assert len(merged2) == 2


def test_wrap_directed_by_future_sale_demand(tmp_path):
    """A wrap must move the lots the report's LATER destination sales
    name — not the HIFO pick.  Here CBETH is sold a month after the
    wrap; the report says that sale consumed a 2020-acquired lot, so
    the wrap must carry the cheap 2020 ETH lot into CBETH even though
    HIFO would move the expensive 2021 lot."""
    (tmp_path / "Coinbase-0-CB-GAINLOSSCSV.csv").write_text(
        "Gain/loss report\n"
        "\n"
        "Transaction Type,Transaction ID,Tax lot ID,Asset name,Amount,"
        "Date Acquired,Cost basis (USD),Date of Disposition,"
        "Proceeds (USD),Gains (Losses) (USD),Holding period (Days),"
        "Data source\n"
        "Sell,tx-9,lot-9,CBETH,2.0,05/10/2020,400.00,04/01/2024,"
        "6000.00,5600.00,1400,Coinbase\n",
        encoding="utf-8")
    from src.broker_lots import load_disposal_lots
    from src.basis import compute_basis_default
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        # Wrap 2 ETH → 2 CBETH on 2024-03-01; sale is a month later.
        _txn("2024-03-01", "Wrap Asset Out", 2.0),
        _txn("2024-03-01", "Wrap Asset In", 2.0, symbol="CBETH-USD"),
        _txn("2024-04-01", "Sell", 2.0, 3000.0, 6000.0, symbol="CBETH-USD"),
    ]
    lots = load_disposal_lots(tmp_path)
    compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                          disposal_lots=lots)
    sale = txns[-1]
    # Directed: consumed the 2020 lot's $400 basis → gain 5600 (matches
    # the report).  Undirected HIFO wrap would have moved the 2021 lot
    # ($7600 basis → gain -1600).
    assert sale["cost_basis"] == pytest.approx(400.0, abs=0.01)
    assert sale["realized_gain"] == pytest.approx(5600.0, abs=0.01)
    assert sale["lot_breakdown"][0]["date_acquired"] == "2020-05-10"


# ---------------------------------------------------------------------------
# Per-lot splitting: group stamping + multi-lot push
# ---------------------------------------------------------------------------

def test_subset_summing_to():
    from src.broker_lots import _subset_summing_to
    items = [(0, 3.0), (1, 11.0), (2, 5.0)]
    # Full set preferred when it fits.
    assert sorted(_subset_summing_to(items, 19.0, 0.01)) == [0, 1, 2]
    # Proper subset found by backtracking.
    assert sorted(_subset_summing_to(items, 14.0, 0.01)) == [0, 1]
    assert sorted(_subset_summing_to(items, 8.0, 0.01)) == [0, 2]
    # Nothing sums to the target.
    assert _subset_summing_to(items, 4.0, 0.01) is None


def test_stamp_acquisition_basis_group_match(isolated_workdir):
    """Several report rows summing to ONE fin txn's quantity stamp the
    total AND a per-piece breakdown (basis_override_lots) -- the broker's
    inventory keeps per-lot granularity where fin records one arrival."""
    from src.broker_lots import stamp_acquisition_basis
    txns = [_txn("2017-06-01", "Buy", 14.0, 220.0, 3080.0)]
    rows = [
        {"symbol": "ETH-USD", "date": "2017-06-01", "qty": 3.0,
         "basis": 660.0, "type": "Buy"},
        {"symbol": "ETH-USD", "date": "2017-06-01", "qty": 11.0,
         "basis": 2431.0, "type": "Buy"},
    ]
    stamped, unmatched = stamp_acquisition_basis(txns, rows)
    assert stamped == 1
    assert unmatched == 0
    t = txns[0]
    assert t["basis_override"] == pytest.approx(3091.0)
    pieces = t["basis_override_lots"]
    assert len(pieces) == 2
    assert sum(p["qty"] for p in pieces) == pytest.approx(14.0)
    assert {round(p["basis"], 2) for p in pieces} == {660.0, 2431.0}


def test_multi_lot_push_keeps_flavors_for_directed_sale(isolated_workdir):
    """A stamped multi-piece arrival pushes one lot per piece, so a
    directed sale can consume the exact per-unit flavor the report
    names instead of a blended average."""
    from src.basis import compute_basis_default, state_to_holdings
    buy = _txn("2021-09-02", "Buy", 3.0, 0.0, 0.0)
    buy["basis_override"] = 11042.0
    buy["basis_override_lots"] = [
        {"qty": 1.0, "basis": 3442.0},     # cheap flavor
        {"qty": 2.0, "basis": 7600.0},     # expensive flavor (3800/u)
    ]
    txns = [buy, _txn("2024-01-10", "Sell", 1.0, 2500.0, 2500.0)]
    hints = {("Coinbase", "ETH-USD", "2024-01-10"): [
        {"acquired": "2021-09-02", "qty": 1.0, "qty_left": 1.0,
         "per_unit": 3442.0}]}
    state = compute_basis_default(txns, disposal_lots=hints)
    sale = txns[-1]
    # Exact flavor, not the 3680.67/u blend.
    assert sale["cost_basis"] == pytest.approx(3442.0)
    holdings = state_to_holdings(state, "fifo")
    eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
    assert eth[0]["cost_basis"] == pytest.approx(7600.0)


# ---------------------------------------------------------------------------
# Rebase consume preference + wallet-move no-op
# ---------------------------------------------------------------------------

def test_rebase_wallet_move_noop_preserves_acquired_date(isolated_workdir):
    """An intra-group move whose override equals the tracked lot's basis
    is a wallet move: the lot survives verbatim -- original acquired
    date included -- so a later report hint naming that date finds it.
    (Re-dating the lot at the move forced the hint onto its per-unit
    fallback, where a near-priced decoy lot could win.)"""
    from src.basis import compute_basis_default
    txns = [
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2024-03-01", "Buy", 2.0, 3850.0, 7700.0),   # decoy within 2%/u
        _txn("2024-03-04", "Transfer In", 2.0),
        _txn("2024-03-04", "Transfer Out", 2.0),
        _txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0),
    ]
    txns[2]["basis_override"] = 7600.0        # == the 2021 lot's basis
    hints = {("Coinbase", "ETH-USD", "2026-06-25"): [
        {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
         "per_unit": 3800.0}]}
    compute_basis_default(txns, disposal_lots=hints)
    sale = txns[-1]
    assert sale["cost_basis"] == pytest.approx(7600.0)
    assert sale["lot_breakdown"][0]["date_acquired"] == "2021-09-02"
    # Move legs still annotate offsetting rebase deltas.
    assert txns[2]["basis_effect"] == "rebase_in"
    assert txns[2]["cost_basis"] == pytest.approx(7600.0)
    assert txns[3]["basis_effect"] == "rebase_out"
    assert txns[3]["cost_basis"] == pytest.approx(7600.0)


def test_rebase_avoids_same_day_rebase_lot(isolated_workdir):
    """Two same-day rebases (the ETH2-deprecation Neutral, then an
    intra-group transfer): the transfer's consume must take the
    pre-existing lots, NOT the lot the deprecation just pushed -- HIFO
    happily ate the fresh deprecation lot, destroying a flavor the
    report's later disposals name."""
    from src.basis import compute_basis_default, state_to_holdings
    txns = [
        _txn("2020-01-10", "Buy", 4.0, 200.0, 800.0),        # ETH2-era units
        _txn("2020-01-15", "Buy", 3.0, 100.0, 300.0),        # old cheap
        _txn("2021-09-02", "Neutral", 4.0, 3442.0, 0.0),     # deprecation
        _txn("2021-09-02", "Transfer In", 3.0),
        _txn("2021-09-02", "Transfer Out", 3.0),
        _txn("2024-01-10", "Sell", 4.0, 2500.0, 10000.0),
    ]
    txns[2]["basis_override"] = 13768.0        # 4 @ 3442
    txns[3]["basis_override"] = 11400.0        # 3 @ 3800 customer-provided
    hints = {("Coinbase", "ETH-USD", "2024-01-10"): [
        {"acquired": "2021-09-02", "qty": 4.0, "qty_left": 4.0,
         "per_unit": 3442.0}]}
    state = compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                                  disposal_lots=hints)
    sale = txns[-1]
    # The full deprecation flavor survived for the directed sale.
    assert sale["cost_basis"] == pytest.approx(13768.0)
    # What remains is exactly the customer-provided arrival.
    holdings = state_to_holdings(state, "fifo")
    eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
    assert eth[0]["cost_basis"] == pytest.approx(11400.0)


# ---------------------------------------------------------------------------
# Reservation -- future report demand steers undirected fallbacks
# ---------------------------------------------------------------------------

def test_reserved_future_demand_cutoff_and_symbol_scope():
    from src.broker_lots import reserved_future_demand
    lots = {
        ("Coinbase", "ETH-USD", "2026-06-25"): [
            {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
             "per_unit": 3800.0}],
        ("Coinbase", "SOL-USD", "2026-01-01"): [
            {"acquired": "2021-09-02", "qty": 5.0, "qty_left": 5.0,
             "per_unit": 10.0}],
    }
    # Strictly-after cutoff.
    assert reserved_future_demand(lots, "Coinbase", "2026-06-25") is None
    both = reserved_future_demand(lots, "Coinbase", "2023-01-10")
    assert both["2021-09-02"] == pytest.approx(7.0)
    # Symbol scoping.
    eth_only = reserved_future_demand(lots, "Coinbase", "2023-01-10",
                                      symbols={"ETH-USD"})
    assert eth_only["2021-09-02"] == pytest.approx(2.0)
    # Exhausted hints don't reserve.
    lots[("Coinbase", "ETH-USD", "2026-06-25")][0]["qty_left"] = 0.0
    assert reserved_future_demand(lots, "Coinbase", "2023-01-10",
                                  symbols={"ETH-USD"}) is None


def test_sale_fallback_avoids_future_demanded_lot(isolated_workdir):
    """An UNDIRECTED sale (no hints for its day) must not consume the
    lot a future report disposal names -- HIFO fallbacks were eating the
    customer-provided 2021 lot years before the report's sale relieved
    it."""
    from src.basis import compute_basis_default
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2023-01-10", "Sell", 2.0, 2000.0, 4000.0),   # no hints
        _txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0),   # hinted
    ]
    hints = {("Coinbase", "ETH-USD", "2026-06-25"): [
        {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
         "per_unit": 3800.0}]}
    compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                          disposal_lots=hints)
    # 2023 sale deflected off the reserved 2021 lot onto the cheap one...
    assert txns[2]["realized_gain"] == pytest.approx(4000.0 - 400.0)
    # ...so the 2026 directed sale realizes the report's figure.
    assert txns[3]["realized_gain"] == pytest.approx(4800.0 - 7600.0)
    assert txns[3]["lot_breakdown"][0]["date_acquired"] == "2021-09-02"


def test_reservation_scoped_to_symbol_family(isolated_workdir):
    """Future demand for an UNRELATED symbol must not deflect this
    pool's consumption (a same-date buy of another ticker would
    otherwise spuriously shift realized gains -- observed on Robinhood
    stock sales)."""
    from src.basis import compute_basis_default
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2021-09-02", "Buy", 5.0, 10.0, 50.0, symbol="SOL-USD"),
        _txn("2023-01-10", "Sell", 2.0, 2000.0, 4000.0),
    ]
    hints = {("Coinbase", "SOL-USD", "2026-01-01"): [
        {"acquired": "2021-09-02", "qty": 5.0, "qty_left": 5.0,
         "per_unit": 10.0}]}
    compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                          disposal_lots=hints)
    # HIFO takes the expensive ETH lot -- SOL demand doesn't reserve it.
    assert txns[3]["realized_gain"] == pytest.approx(4000.0 - 7600.0)


def test_reservation_yields_on_shortfall(isolated_workdir):
    """When nothing else covers the quantity, reserved lots ARE
    consumed -- reservation is a preference, never a balance error."""
    from src.basis import compute_basis_default
    txns = [
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2023-01-10", "Sell", 2.0, 2000.0, 4000.0),
    ]
    hints = {("Coinbase", "ETH-USD", "2026-06-25"): [
        {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
         "per_unit": 3800.0}]}
    state = compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                                  disposal_lots=hints)
    assert txns[1]["realized_gain"] == pytest.approx(4000.0 - 7600.0)
    assert not state["lots"][("Coinbase", "ETH-USD")]


def test_history_mirrors_reservation_and_move_noop(stub_prices):
    """Snapshot walker parity for the two new consume rules: the
    reservation deflection AND the wallet-move no-op must leave the
    same remaining basis as the annotated walk."""
    from src.history import compute_history
    from src.basis import compute_basis_default, state_to_holdings

    stub_prices.set("ETH-USD", {"2026-06-30": 2400.0})
    txns = [
        _txn("2020-05-10", "Buy", 2.0, 200.0, 400.0),
        _txn("2021-09-02", "Buy", 2.0, 3800.0, 7600.0),
        _txn("2023-01-10", "Sell", 1.0, 2000.0, 2000.0),   # undirected
        _txn("2024-03-04", "Transfer In", 2.0),      # move of the 2021 lot
        _txn("2024-03-04", "Transfer Out", 2.0),
        _txn("2026-06-25", "Sell", 2.0, 2400.0, 4800.0),   # hinted
    ]
    txns[3]["basis_override"] = 7600.0
    hints = {("Coinbase", "ETH-USD", "2026-06-25"): [
        {"acquired": "2021-09-02", "qty": 2.0, "qty_left": 2.0,
         "per_unit": 3800.0}]}
    history = compute_history([dict(t) for t in txns],
                              {"ETH-USD": "Cryptocurrency"},
                              account_methods={"Coinbase": "hifo"},
                              disposal_lots=hints)
    last = history[-1]
    snap_noncash = sum(p["cost_basis"] for p in last["positions"]
                       if p["symbol"] != "USD")
    fifo = compute_basis_default(txns, account_methods={"Coinbase": "hifo"},
                                 disposal_lots=hints)
    holdings = state_to_holdings(fifo, "fifo")
    assert sum(h["cost_basis"] for h in holdings) == pytest.approx(
        snap_noncash, abs=0.02)
    # And both preserved the 2021 lot for the directed sale: the 2023
    # sale (HIFO would take the 2021 lot) was deflected to the 2020 lot.
    eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
    assert eth[0]["cost_basis"] == pytest.approx(200.0)

