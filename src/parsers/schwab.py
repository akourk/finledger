"""Schwab (Rollover IRA, Roth IRA, Roth Contributory IRA)."""

from __future__ import annotations

import re
from pathlib import Path

from ._helpers import read_csv_rows
from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Schwab (new format — Rollover IRA & Roth IRA)
# ---------------------------------------------------------------------------
# Headers: Date, Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount

def _schwab_account_from_filename(filepath: Path) -> str:
    name = filepath.name.lower()
    if "rollover_ira" in name or "rolloverira" in name or "schwab-rollover-ira" in name:
        return "Schwab Rollover IRA"
    if "roth_contributory_ira" in name or "rothcontributoryira" in name or "schwab-roth-ira" in name:
        return "Schwab Roth IRA"
    return "Schwab"


def parse_schwab(filepath: Path) -> list[Transaction]:
    txns = []
    account = _schwab_account_from_filename(filepath)

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = read_csv_rows(f, filepath.name, nonblank=('Action',), required=('Date', 'Action', 'Symbol', 'Quantity', 'Price', 'Amount'))
        for row in reader:
            action = (row.get("Action") or "").strip()
            if not action:
                continue

            try:
                date = _date_mdy(row["Date"])
            except (ValueError, KeyError):
                continue

            txns.append(_txn(
                date=date,
                account=account,
                symbol=(row.get("Symbol") or "").strip(),
                action=action,
                quantity=abs(_num(row.get("Quantity", ""))),
                price=abs(_num(row.get("Price", ""))),
                fees=abs(_num(row.get("Fees & Comm", ""))),
                amount=abs(_num(row.get("Amount", ""))),
                description=(row.get("Description") or "").strip(),
                source=filepath.name,
            ))
    return txns
