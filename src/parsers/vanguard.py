"""Vanguard SF 401K."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Vanguard SF 401K
# ---------------------------------------------------------------------------
# Headers: Date, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note,
#          Account, Currency

def parse_vanguard_401k(filepath: Path) -> list[Transaction]:
    txns = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            action = (row.get("Action") or "").strip()
            if not symbol or not action:
                continue

            try:
                date = _date_ymd(row["Date"])
            except (ValueError, KeyError):
                continue

            txns.append(_txn(
                date=date,
                account="Vanguard 401K",
                symbol=symbol,
                action=action,
                quantity=_num(row.get("Quantity", "")),
                price=_num(row.get("unitPrice", "")),
                fees=_num(row.get("Fee", "")),
                amount=_num(row.get("Subtotal", "")),
                description=(row.get("Note") or "").strip(),
                source=filepath.name,
            ))
    return txns
