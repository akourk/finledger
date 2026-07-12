"""Robinhood Apex-era (2017–2018) hand-entered transactions.

Robinhood cleared through Apex before moving to self-clearing in late
2018, and its transaction-CSV exports don't reach back into the Apex
era — those trades exist only in the old 1099 PDFs.  This parser
ingests a hand-maintained CSV transcribed from them:

    Date,Security Description,CUSIP,Transaction Description,Quantity,Price,Amount

Conventions (documented for future hand entry):

- ``Date`` is MM/DD/YYYY.
- ``Security Description`` becomes the symbol as entered.  Use the
  plain ticker where known, or fin's option-symbol format
  (``AMD 11/9/2018 Put $15.50``) so the leg pairs with its sell /
  expiration in the modern CSVs.  A full security name also works —
  it stays its own symbol, which is fine for positions that open AND
  close inside this file (net zero, realized computes locally).
- ``Transaction Description`` PURCHASE / SELL map to Buy / Sell;
  anything else passes through to normalize.py title-cased.
- The CUSIP column is embedded into the description using Robinhood's
  own ``CUSIP: X`` marker, so ``cusips.py`` collision detection can
  suggest a name→ticker rename rule once the same security appears in
  a modern export (add it to ``cache/ticker_renames.json`` and the
  rename layer collapses the symbols).
"""

from __future__ import annotations

import csv
from pathlib import Path

from ._helpers import Transaction, _date_mdy, _num, _txn

_ACTION_MAP = {"PURCHASE": "Buy", "BUY": "Buy", "SELL": "Sell", "SALE": "Sell"}


def parse_robinhood_apex(filepath: Path) -> list[Transaction]:
    txns: list[Transaction] = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sec = " ".join((row.get("Security Description") or "").split())
            raw_action = (row.get("Transaction Description") or "").strip()
            if not sec or not raw_action:
                continue
            try:
                date = _date_mdy(row.get("Date", ""))
            except (ValueError, TypeError):
                continue
            action = _ACTION_MAP.get(raw_action.upper(), raw_action.title())
            # Option legs (symbol in fin's "TICKER M/D/YYYY Call/Put $K"
            # format) use the option action names so the Options tab's
            # open/close pairing and the modern CSVs' STC/OEXP legs see
            # them — a plain Buy would add to the balance but never
            # register as the contract's opening leg.
            if (" Call " in sec or " Put " in sec) and action in ("Buy", "Sell"):
                action = f"Option {action}"
            cusip = (row.get("CUSIP") or "").strip()
            desc = f"{raw_action} {sec}"
            if cusip:
                desc += f" CUSIP: {cusip}"
            txns.append(_txn(
                date=date,
                account="Robinhood",
                symbol=sec,
                action=action,
                quantity=abs(_num(row.get("Quantity", ""))),
                price=abs(_num(row.get("Price", ""))),
                fees=0.0,
                amount=abs(_num(row.get("Amount", ""))),
                description=desc,
                source=filepath.name,
            ))
    return txns
