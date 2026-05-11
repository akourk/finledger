"""Apple Savings HYSA."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Apple Savings
# ---------------------------------------------------------------------------
# Headers: Account, Date, Currency, Symbol, Action, Quantity, unitPrice,
#          Fee, Subtotal, Note

def parse_apple_savings(filepath: Path) -> list[Transaction]:
    txns = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = (row.get("Action") or "").strip()
            if not action:
                continue

            try:
                date = _date_ymd(row["Date"])
            except (ValueError, KeyError):
                continue

            txns.append(_txn(
                date=date,
                account="Apple Savings",
                symbol="USD",
                action=action,
                quantity=_num(row.get("Quantity", "")),
                price=_num(row.get("unitPrice", "")),
                fees=_num(row.get("Fee", "")),
                amount=_num(row.get("Subtotal", "")),
                description=(row.get("Note") or "").strip(),
                source=filepath.name,
            ))
    return txns
