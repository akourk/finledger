"""Manual adjustments (hand-edited corrections)."""

from __future__ import annotations

import re
from pathlib import Path

from ._helpers import read_csv_rows
from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Manual Adjustments
# ---------------------------------------------------------------------------
# Headers: Account, Date, Type, Symbol, Quantity, Price, Amount, Description
# Lines starting with # are comments.

def parse_manual(filepath: Path) -> list[Transaction]:
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        lines = ["\n" if line.startswith("#") else line for line in f]

    txns = []
    reader = read_csv_rows(lines, filepath.name, nonblank=('Account', 'Type'), required=('Account', 'Date', 'Type', 'Symbol', 'Quantity', 'Price', 'Amount'))
    for row in reader:
        account = (row.get("Account") or "").strip()
        action = (row.get("Type") or "").strip()
        if not account or not action:
            continue

        try:
            date = _date_ymd(row["Date"])
        except (ValueError, KeyError):
            continue

        txns.append(_txn(
            date=date,
            account=account,
            symbol=(row.get("Symbol") or "").strip(),
            action=action,
            quantity=_num(row.get("Quantity", "")),
            price=_num(row.get("Price", "")),
            fees=0.0,
            amount=_num(row.get("Amount", "")),
            description=(row.get("Description") or "").strip(),
            source=filepath.name,
        ))
    return txns
