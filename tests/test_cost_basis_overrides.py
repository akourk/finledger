"""Tests for the cost-basis override matcher (metadata `Cost Basis` rows)."""

from __future__ import annotations

from src.cost_basis_overrides import match_and_stamp
from src.basis import compute_basis_default


def _ov(account, date, amount, qty, asset, index=None):
    return {"account_group": account, "date": date, "amount": amount,
            "qty": qty, "asset": asset, "index": index}


def _tin(date, qty, price=2000.0, symbol="ETH-USD"):
    return {"account_group": "Coinbase", "symbol": symbol, "action": "Transfer In",
            "account_type": "Taxable", "date": date, "quantity": qty,
            "price": price, "fees": 0.0, "amount": 0.0, "description": "",
            "source": "t"}


def test_override_stamps_unpaired_transfer_in():
    """A row matching an unpaired external transfer-in stamps the basis."""
    txns = [_tin("2021-07-09", 5.1234567)]
    applied, warn = match_and_stamp(
        txns, [_ov("Coinbase", "2021-07-09", 12000.00, 5.1234567, "ETH")])
    assert applied == 1
    assert txns[0]["basis_override"] == 12000.00
    assert warn == []
    # …and the walker uses it instead of the FMV guess.
    txns2 = [_tin("2021-07-09", 5.1234567),
             {"account_group": "Coinbase", "symbol": "ETH-USD", "action": "Sell",
              "account_type": "Taxable", "date": "2024-01-01", "quantity": 5.1234567,
              "price": 3000.0, "fees": 0.0, "amount": 15370.37, "description": "",
              "source": "t"}]
    match_and_stamp(txns2, [_ov("Coinbase", "2021-07-09", 12000.00, 5.1234567, "ETH")])
    res = compute_basis_default(txns2)
    # realized = 15370.37 proceeds − 12000.00 override basis (not the FMV guess)
    assert res["realized_total"] == round(15370.37 - 12000.00, 2) or \
        abs(res["realized_total"] - (15370.37 - 12000.00)) < 0.01


def test_date_tolerance_and_no_match_warns():
    """±2-day tolerance matches; a qty with no lot warns (typo / count
    mismatch) rather than silently mis-applying."""
    txns = [_tin("2021-07-09", 5.1234567)]
    # off by 1 day → still matches
    applied, warn = match_and_stamp(
        txns, [_ov("Coinbase", "2021-07-08", 12000.00, 5.1234567, "ETH")])
    assert applied == 1 and warn == []
    # a qty that exists nowhere → warning, no crash
    txns2 = [_tin("2021-07-09", 5.1234567)]
    applied2, warn2 = match_and_stamp(
        txns2, [_ov("Coinbase", "2021-07-09", 100.0, 99.0, "ETH")])
    assert applied2 == 0
    assert any("no matching" in w for w in warn2)
    assert "basis_override" not in txns2[0]


def test_consume_one_to_one_no_reuse():
    """Two rows with the same (date, qty) claim two distinct lots."""
    txns = [_tin("2018-01-02", 2.0), _tin("2018-01-02", 2.0)]
    applied, warn = match_and_stamp(txns, [
        _ov("Coinbase", "2018-01-02", 1722.0, 2.0, "ETH"),
        _ov("Coinbase", "2018-01-02", 1700.0, 2.0, "ETH"),
    ])
    assert applied == 2
    stamped = sorted(t["basis_override"] for t in txns)
    assert stamped == [1700.0, 1722.0]
