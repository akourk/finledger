"""Coinbase Convert handling — the synthesized Convert In leg must carry
FMV basis (the conversion's USD value), not $0.

Bug this pins: the parser synthesized the acquired-asset leg with
``price=0, amount=0``, so the basis walker pushed a $0-basis lot.  The
Convert Out correctly realized gain at FMV, and then the later sale of
the acquired asset realized the ENTIRE converted value again —
double-counted gain, disagreeing with the broker's 1099-DA (which
reports FMV basis for the acquired asset).
"""

from __future__ import annotations

import pytest

from src.parsers.coinbase import parse_coinbase


_COINBASE_HEADER = (
    "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,Subtotal,"
    "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
)


def _write_coinbase_csv(path, rows: list[str]) -> None:
    path.write_text(_COINBASE_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_convert_in_synth_carries_fmv_basis(isolated_workdir):
    csv = isolated_workdir / "data" / "coinbase-1.csv"
    _write_coinbase_csv(csv, [
        # Convert 1 BTC → 15 ETH at a $30,000 USD value.
        'x1,2021-01-01T12:00:00Z,Convert,BTC,-1.0,USD,30000.00,29900.00,'
        '30000.00,100.00,"Converted 1 BTC to 15 ETH"',
    ])
    txns = parse_coinbase(csv)
    assert [t["action"] for t in txns] == ["Convert Out", "Convert In"]
    out_leg, in_leg = txns

    assert out_leg["symbol"] == "BTC"
    assert out_leg["amount"] == pytest.approx(30000.0)

    # The synth leg carries the conversion's USD value as its basis.
    assert in_leg["symbol"] == "ETH"
    assert in_leg["quantity"] == pytest.approx(15.0)
    assert in_leg["amount"] == pytest.approx(30000.0)
    assert in_leg["price"] == pytest.approx(2000.0)


def test_convert_then_sell_realizes_economic_gain_once(isolated_workdir):
    """Buy 1 BTC @$10k → convert to 15 ETH @$30k → sell ETH @$30k.
    Economic gain is $20k, realized entirely at the conversion; the
    later sale at unchanged value realizes $0 more."""
    from src.basis import compute_basis_default

    csv = isolated_workdir / "data" / "coinbase-1.csv"
    _write_coinbase_csv(csv, [
        'x1,2020-01-01T12:00:00Z,Buy,BTC,1.0,USD,10000.00,9950.00,'
        '10000.00,50.00,"Bought 1 BTC using bank account ****1234"',
        'x2,2021-01-01T12:00:00Z,Convert,BTC,-1.0,USD,30000.00,29900.00,'
        '30000.00,100.00,"Converted 1 BTC to 15 ETH"',
    ])
    txns = parse_coinbase(csv)
    for t in txns:
        t["account_group"] = "Coinbase"
        t["quantity"] = abs(float(t["quantity"]))
        t["amount"] = abs(float(t["amount"]))
    # Normalize action names the way main.py's step 4d would.
    from src.normalize import normalize_action
    for t in txns:
        t["raw_action"] = t["action"]
        t["action"] = normalize_action(t)
    # Sell the 15 ETH later at the same $30k value.
    txns.append({
        "date": "2021-06-01", "account_group": "Coinbase", "symbol": "ETH",
        "action": "Sell", "quantity": 15.0, "price": 2000.0, "amount": 30000.0,
    })

    compute_basis_default(txns)
    total_realized = sum(t.get("realized_gain") or 0 for t in txns)
    assert total_realized == pytest.approx(20000.0)

    sell = txns[-1]
    assert sell["cost_basis"] == pytest.approx(30000.0)
    assert sell["realized_gain"] == pytest.approx(0.0)
