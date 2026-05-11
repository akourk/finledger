"""Voya 401K — includes CONTRIBUTION REVERSAL + TRANSFER direction split."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Voya Atos 401K
# ---------------------------------------------------------------------------
# Has metadata header rows. Actual headers (when found):
#   Activity Date, Fund, Activity, Money Source, # of Units, Unit Price, Amount

def parse_voya_401k(filepath: Path) -> list[Transaction]:
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Find the actual header line
    header_idx = 0
    for i, line in enumerate(lines):
        if "Activity Date" in line:
            header_idx = i
            break

    txns = []
    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        fund = (row.get("Fund") or "").strip()
        activity = (row.get("Activity") or "").strip()
        if not fund or not activity:
            continue

        try:
            date = _date_ymd(row["Activity Date"])
        except (ValueError, KeyError):
            continue

        money_source = (row.get("Money Source") or "").strip()

        qty = _num(row.get("# of Units", ""))
        amount = _num(row.get("Amount", ""))

        # Split ambiguous actions by sign before abs()
        if activity == "TRANSFER":
            activity = "TRANSFER IN" if qty >= 0 else "TRANSFER OUT"
        elif activity == "DIVIDEND" and qty < 0:
            activity = "DIVIDEND REVERSAL"
        elif activity == "CONTRIBUTION" and (qty < 0 or amount < 0):
            # Voya emits negative CONTRIBUTION rows when the employer or
            # plan administrator reverses a prior contribution (e.g. a
            # payroll error).  Without this rename these would double-
            # count: the parser would `abs()` the units and the balance
            # walker would treat it as an ADD, so the balance ends up
            # 2 * |units| too high.  Normalizes to "Contribution Reversal"
            # which the rest of the pipeline treats as a shares-removing,
            # cash-outflow event (undoes the original).
            activity = "CONTRIBUTION REVERSAL"

        txns.append(_txn(
            date=date,
            account="Voya 401K",
            symbol=fund,
            action=activity,
            quantity=abs(qty),
            price=abs(_num(row.get("Unit Price", ""))),
            fees=0.0,
            amount=abs(amount),
            description=money_source,
            source=filepath.name,
        ))
    return txns
