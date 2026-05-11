"""Coinbase & Coinbase Pro / GDAX parsers."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Coinbase
# ---------------------------------------------------------------------------
# Headers (after metadata): ID, Timestamp, Transaction Type, Asset,
#   Quantity Transacted, Price Currency, Price at Transaction, Subtotal,
#   Total (inclusive of fees and/or spread), Fees and/or Spread, Notes

def _read_coinbase_rows(filepath: Path) -> list[dict]:
    """Read Coinbase CSV, skipping metadata header lines."""
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()

    header_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("ID,") or line.startswith('"ID"'):
            header_idx = i
            break
        if "Timestamp" in line and "Transaction Type" in line:
            header_idx = i
            break

    return list(csv.DictReader(lines[header_idx:]))


def parse_coinbase(filepath: Path) -> list[Transaction]:
    rows = _read_coinbase_rows(filepath)
    txns = []
    for row in rows:
        action = (row.get("Transaction Type") or "").strip()
        asset = (row.get("Asset") or "").strip().upper()
        if not action or not asset:
            continue

        try:
            date = _date_iso(row["Timestamp"])
        except (ValueError, KeyError):
            continue

        qty = _num(row.get("Quantity Transacted", ""))

        # Split ambiguous actions by qty sign before abs()
        _DIRECTIONAL = {
            "Convert", "Wrap Asset", "Unwrap",
            "Retail Staking Transfer", "Retail Unstaking Transfer",
            "Transfer",
        }
        if action in _DIRECTIONAL:
            action = f"{action} Out" if qty < 0 else f"{action} In"

        notes = (row.get("Notes") or "").strip()
        price = abs(_num(row.get("Price at Transaction", "")))
        fees = abs(_num(row.get("Fees and/or Spread", "")))
        amount = abs(_num(row.get("Total (inclusive of fees and/or spread)", "")))

        # Parse the description for Convert Out rows:
        # "Converted X FROM to Y TO" — decide whether to synthesize a Buy leg
        # or mark as neutral (ETH ↔ ETH2 is the same underlying asset).
        _ETH_VARIANTS = {"ETH", "ETH2"}
        synth = None
        if action == "Convert Out":
            m = re.search(
                r"Converted\s+[\d.]+\s+(\w+)\s+to\s+([\d.]+)\s+(\w+)",
                notes, re.IGNORECASE,
            )
            if m:
                from_tok = m.group(1).upper()
                to_qty   = abs(float(m.group(2)))
                to_tok   = m.group(3).upper()
                if from_tok in _ETH_VARIANTS and to_tok in _ETH_VARIANTS:
                    # Same underlying — mark as neutral (no balance change)
                    action = "Convert Out Neutral"
                else:
                    # Different assets — synthesize a Convert In (Buy) for the
                    # acquired asset.  Symbol normalization in main.py will
                    # expand the raw ticker to e.g. ETH-USD.
                    synth = _txn(
                        date=date,
                        account="Coinbase",
                        symbol=to_tok,
                        action="Convert In",
                        quantity=to_qty,
                        price=0.0,
                        fees=0.0,
                        amount=0.0,
                        description=notes,
                        source=filepath.name,
                    )

        txns.append(_txn(
            date=date,
            account="Coinbase",
            symbol=asset,
            action=action,
            quantity=abs(qty),
            price=price,
            fees=fees,
            amount=amount,
            description=notes,
            source=filepath.name,
        ))
        if synth:
            txns.append(synth)
    return txns


# ---------------------------------------------------------------------------
# Coinbase Pro / GDAX
# ---------------------------------------------------------------------------
# Headers: portfolio, type, time, amount, balance, amount/balance unit,
#          transfer id, trade id, order id

def parse_coinbase_pro(filepath: Path) -> list[Transaction]:
    """Parse Coinbase Pro / GDAX account CSV.

    Match rows come in pairs sharing a trade id: one crypto leg and one USD leg.
    We pair them to compute price = abs(usd_amount) / abs(crypto_qty).
    The crypto leg becomes the real transaction; the USD leg is kept as a
    companion row (action stays 'match', symbol 'USD') so nothing is lost.
    Fee rows are standalone and get amount = abs(fee).
    """
    rows_by_type: dict[str, list] = {"match": [], "fee": [], "other": []}
    raw_rows = []

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_type = (row.get("type") or "").strip()
            if not row_type:
                continue
            try:
                date = _date_iso(row["time"])
            except (ValueError, KeyError):
                continue

            unit = (row.get("amount/balance unit") or "").strip()
            amount = _num(row.get("amount", ""))
            trade_id = (row.get("trade id") or "").strip()

            parsed = {
                "_date": date,
                "_unit": unit,
                "_amount": amount,
                "_trade_id": trade_id,
                "_type": row_type,
                "_source": filepath.name,
            }
            raw_rows.append(parsed)

    # Group match rows by trade_id for pairing
    from collections import defaultdict
    match_groups: dict[str, list] = defaultdict(list)
    non_match_rows = []

    for r in raw_rows:
        if r["_type"] == "match" and r["_trade_id"]:
            match_groups[r["_trade_id"]].append(r)
        else:
            non_match_rows.append(r)

    txns = []

    # Process match pairs
    for trade_id, legs in match_groups.items():
        crypto_legs = [l for l in legs if l["_unit"] != "USD"]
        usd_legs = [l for l in legs if l["_unit"] == "USD"]

        # Compute price from pairing
        usd_amount = sum(l["_amount"] for l in usd_legs) if usd_legs else 0.0
        crypto_qty = sum(l["_amount"] for l in crypto_legs) if crypto_legs else 0.0

        price = abs(usd_amount / crypto_qty) if crypto_qty else 0.0

        # Emit crypto leg(s) with computed price
        for leg in crypto_legs:
            raw_qty = leg["_amount"]
            leg_action = "buy" if raw_qty >= 0 else "sell"
            abs_qty = abs(raw_qty)
            txns.append(_txn(
                date=leg["_date"],
                account="Coinbase Pro",
                symbol=leg["_unit"],
                action=leg_action,
                quantity=abs_qty,
                price=round(price, 8),
                fees=0.0,
                amount=round(abs_qty * price, 8),
                description=f"trade_id={trade_id}",
                source=leg["_source"],
            ))

        # Emit USD leg(s) as companion rows (price=1 since it's cash).
        # CRITICAL: these are the cash settlement leg of a trade match —
        # NOT external deposits/withdrawals.  Use distinct action names
        # so they map to "Trade Settle In/Out" downstream and stay out
        # of cash-flow accounting (Deposit/Withdrawal would inflate
        # net_contributed and the SPY benchmark series with phantom
        # external money).  Bug fixed: see actions.py "Trade Settle In".
        for leg in usd_legs:
            raw_amt = leg["_amount"]
            leg_action = "trade_settle_in" if raw_amt >= 0 else "trade_settle_out"
            txns.append(_txn(
                date=leg["_date"],
                account="Coinbase Pro",
                symbol="USD",
                action=leg_action,
                quantity=abs(raw_amt),
                price=1.0,
                fees=0.0,
                amount=abs(raw_amt),
                description=f"trade_id={trade_id}",
                source=leg["_source"],
            ))

    # Process non-match rows (deposits, withdrawals, fees)
    for r in non_match_rows:
        amount = r["_amount"]
        if r["_type"] == "fee":
            txns.append(_txn(
                date=r["_date"],
                account="Coinbase Pro",
                symbol=r["_unit"],
                action=r["_type"],
                quantity=0.0,
                price=0.0,
                fees=abs(amount),
                amount=abs(amount),
                description=f"trade_id={r['_trade_id']}" if r["_trade_id"] else "",
                source=r["_source"],
            ))
        else:
            txns.append(_txn(
                date=r["_date"],
                account="Coinbase Pro",
                symbol=r["_unit"],
                action=r["_type"],
                quantity=abs(amount),
                price=0.0,
                fees=0.0,
                amount=abs(amount),
                description=f"trade_id={r['_trade_id']}" if r["_trade_id"] else "",
                source=r["_source"],
            ))

    return txns
