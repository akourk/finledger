"""USAA Victory Capital Roth IRA."""

from __future__ import annotations

import re
from pathlib import Path

from ._helpers import read_csv_rows
from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# USAA Victory Capital
# ---------------------------------------------------------------------------
# Headers: Account, Currency, Date, Symbol, Action, Quantity, unitPrice,
#          Fee, Subtotal, Note

def parse_usaa(filepath: Path) -> list[Transaction]:
    txns = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = read_csv_rows(f, filepath.name, nonblank=('Symbol', 'Action'), required=('Date', 'Symbol', 'Action', 'Quantity', 'unitPrice', 'Subtotal'))
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            action = (row.get("Action") or "").strip()
            if not symbol or not action:
                continue

            try:
                date = _date_dmy(row["Date"])
            except (ValueError, KeyError):
                continue

            qty    = _num(row.get("Quantity", ""))
            price  = _num(row.get("unitPrice", ""))
            fees   = _num(row.get("Fee", ""))
            amount = _num(row.get("Subtotal", ""))
            note   = (row.get("Note") or "").strip()

            # Dividend rows record shares paid out, immediately followed by a
            # matching Buy (reinvestment).  To avoid double-counting shares,
            # reclassify the Dividend row as a USD cash event: qty = dollar
            # amount, symbol = USD.
            if action == "Dividend":
                txns.append(_txn(
                    date=date,
                    account="USAA Roth IRA",
                    symbol="USD",
                    action=action,
                    quantity=amount,
                    price=1.0,
                    fees=fees,
                    amount=amount,
                    description=note,
                    source=filepath.name,
                ))
            else:
                txns.append(_txn(
                    date=date,
                    account="USAA Roth IRA",
                    symbol=symbol,
                    action=action,
                    quantity=qty,
                    price=price,
                    fees=fees,
                    amount=amount,
                    description=note,
                    source=filepath.name,
                ))
    return txns
