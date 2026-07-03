"""Broker-reported disposal lots — report-directed lot relief.

Coinbase's tax center exports a per-lot gain/loss report (kept in
``data/`` as reference; the scanner classifies it ``skip``).  Each row
is one TAX LOT consumed by a disposal: asset, quantity, date acquired,
cost basis, date of disposition.  That is exactly the information fin's
lot walker otherwise has to GUESS via a lot-relief method (FIFO/HIFO) —
and two defensible orderings can diverge on which lots survive, drifting
fin's realized gains and remaining-pool basis away from what the broker
reports to the IRS (observed: a customer-provided 2021 lot survived in
Coinbase's pool to 2026 while fin's HIFO consumed it in 2024).

This module parses the report into per-disposal-day "hints":

    {(account_group, symbol, iso_sell_date): [
        {"acquired": "YYYY-MM-DD", "qty": float, "qty_left": float,
         "per_unit": float},
    ...], ...}

``basis._consume_lots_directed`` consumes pool lots matching each
hint (by acquired date, then near-date, then per-unit basis), falling
back to the account's normal method order for anything unmatched.  This
is purely a CONSUME-ORDER strategy — booked basis is always the actual
pool-lot basis, so balance↔basis parity and the annotation
reconstruction (`derive_basis_by_key_from_txns`) hold unchanged.

The report is Coinbase-format; hints are keyed to the Coinbase account
group.  Wraps (ETH↔CBETH) preserve lot acquired-dates through
``_rescale_lots``, so a report hint naming a 2021 acquisition finds the
wrapped lot in the destination symbol's queue.
"""

from __future__ import annotations

import csv
from pathlib import Path

# The account group the Coinbase tax-center report describes.
_REPORT_ACCOUNT_GROUP = "Coinbase"

# Header signature — matches the scanner's skip rule for this file.
_LOT_ID_COL = "tax lot id"


def _norm_symbol(asset: str) -> str:
    from .config import CRYPTO_SYMBOLS, SYMBOL_MAP
    a = SYMBOL_MAP.get(asset, asset)
    if (a in CRYPTO_SYMBOLS or asset in CRYPTO_SYMBOLS) and not a.endswith("-USD"):
        return a + "-USD"
    return a


def _iso(s: str) -> str:
    """Normalize the report's MM/DD/YYYY (or ISO) date to YYYY-MM-DD."""
    s = (s or "").strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            m, d, y = parts
            return f"{y[:4]}-{int(m):02d}-{int(d):02d}"
    return s[:10]


def _num(s: str) -> float:
    try:
        return float((s or "0").replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def _is_gainloss_file(path: Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                if _LOT_ID_COL in line.lower():
                    return True
    except OSError:
        pass
    return False


def load_disposal_lots(data_dir: Path) -> dict[tuple[str, str, str], list[dict]] | None:
    """Find + parse gain/loss report(s) in ``data_dir``.

    Returns the hint dict described in the module docstring, or ``None``
    when no report file exists.  Multiple report files merge (rows
    accumulate per key) — harmless if the user drops overlapping
    exports, since hints direct consume ORDER; exhausted hints simply
    fall back to the method order.
    """
    if not data_dir.exists():
        return None
    out: dict[tuple[str, str, str], list[dict]] = {}
    found = False
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_gainloss_file(p):
            continue
        found = True
        for row in _parse_gainloss(p):
            out.setdefault(
                (_REPORT_ACCOUNT_GROUP, row["symbol"], row["sold"]),
                []).append({
                    "acquired": row["acquired"],
                    "qty":      row["qty"],
                    "qty_left": row["qty"],
                    "per_unit": row["per_unit"],
                })
    return out if found else None


def _parse_gainloss(path: Path) -> list[dict]:
    """Parse one report: skip the preamble, locate the header row, and
    yield one dict per lot row."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hdr_idx = None
    for i, r in enumerate(rows[:20]):
        if any(_LOT_ID_COL == (c or "").strip().lower() for c in r):
            hdr_idx = i
            break
    if hdr_idx is None:
        return []
    hdr = [c.strip() for c in rows[hdr_idx]]
    col = {c.lower(): i for i, c in enumerate(hdr)}

    def _get(r, name):
        i = col.get(name)
        return r[i] if (i is not None and i < len(r)) else ""

    out: list[dict] = []
    for r in rows[hdr_idx + 1:]:
        if len(r) < len(hdr) - 2:
            continue
        qty = _num(_get(r, "amount"))
        basis = _num(_get(r, "cost basis (usd)"))
        acquired = _iso(_get(r, "date acquired"))
        sold = _iso(_get(r, "date of disposition"))
        asset = (_get(r, "asset name") or "").strip()
        if qty <= 0 or not asset or not sold:
            continue
        out.append({
            "symbol":   _norm_symbol(asset),
            "qty":      qty,
            "acquired": acquired,
            "sold":     sold,
            "per_unit": basis / qty if qty > 0 else 0.0,
        })
    return out


def hints_for(disposal_lots: dict | None, account_group: str, symbol: str,
              date: str) -> list[dict] | None:
    """Hint lots for a disposal: the exact day's list PLUS the adjacent
    days' lists, concatenated in (exact, −1, +1) priority.

    Report timestamps are UTC and systematically straddle fin's txn
    dates — a multi-day sell run can land such that fin's date X holds
    the report's X−1 lots while a (smaller) X key also exists.  Exact-
    only lookup then returns the wrong day's short list and the bulk of
    the disposal falls back to the method order.  Concatenation makes
    the lookup shift-tolerant: the returned list holds the SHARED hint
    dicts (``qty_left`` mutations persist across lookups), so multiple
    sells across adjacent days consume the same hint pool exactly once.
    """
    if not disposal_lots:
        return None
    from datetime import date as _d, timedelta
    try:
        base = _d.fromisoformat((date or "")[:10])
    except ValueError:
        return disposal_lots.get((account_group, symbol, date))
    combined: list[dict] = []
    for delta in (0, -1, 1):
        k = (account_group, symbol,
             (base + timedelta(days=delta)).isoformat())
        combined.extend(disposal_lots.get(k) or [])
    return combined or None


def copy_disposal_lots(disposal_lots: dict | None) -> dict | None:
    """Deep-copy the hint dict.  Each walker (basis, history, refresh
    re-walk) mutates ``qty_left`` as it consumes — every walk must start
    from a fresh copy or the second walk finds the hints exhausted."""
    if not disposal_lots:
        return None
    return {k: [dict(h) for h in v] for k, v in disposal_lots.items()}


# ---------------------------------------------------------------------------
# RAWTX — broker-reported ACQUISITION basis
# ---------------------------------------------------------------------------
# The tax-center "raw transactions" report carries Coinbase's own cost
# basis (incl. fees/spread) for every acquisition — including the
# customer-provided basis on receives and the carried basis on the
# ETH2-deprecation conversions that fin's Neutral treatment can't see.
# `stamp_acquisition_basis` auto-applies these onto fin's lot-creating
# txns (user `Cost Basis` metadata rows stamp first and always win).

_RAWTX_ACQ_COL = "asset acquired"
_RAWTX_DISP_COL = "asset disposed"

# Acquisition types whose basis we adopt.  Wrap/Unwrap/Stake/Unstake
# are carries fin already handles; Reward(s)/Credit are hundreds of
# micro-lots whose FMV fin already computes from the same price.
# Convert rows are included ONLY when they cross symbols (see the
# same-symbol exclusion below): measured on real data, excluding them
# entirely starves fin's pool of basis it has no other source for
# (2024 flipped +16k), while including same-symbol conversions
# double-injects carried lots at phantom dates.  The residual
# imperfection this leaves — Coinbase dates convert-carried lots by
# their ORIGINAL acquisition, fin by the convert date — is the known
# remaining source of per-year timing drift (see CLAUDE.md).
_ACQ_TYPES = {"Buy", "Receive", "Convert", "Converted to", "Airdrop",
              "Transfer"}

# fin actions that create lots (mirror cost_basis_overrides._ADD_ACTIONS).
_LOT_ADD_ACTIONS = {"Buy", "Transfer In", "Deposit", "Convert In",
                    "Reinvest"}


def _is_rawtx_file(path: Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            head = (f.readline() or "").lower()
        return _RAWTX_ACQ_COL in head and _RAWTX_DISP_COL in head
    except OSError:
        return False


def load_acquisition_lots(data_dir: Path) -> list[dict] | None:
    """Parse RAWTX report(s): one dict per acquisition row we adopt —
    ``{symbol, date, qty, basis, type}``, sorted by date."""
    if not data_dir.exists():
        return None
    out: list[dict] = []
    found = False
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_rawtx_file(p):
            continue
        found = True
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                typ = (r.get("Transaction Type") or "").strip()
                if typ not in _ACQ_TYPES:
                    continue
                asset = (r.get("Asset Acquired") or "").strip()
                qty = _num(r.get(
                    "Quantity Acquired (Bought, Received, etc)"))
                basis = _num(r.get(
                    "Cost Basis (incl. fees and/or spread) (USD)"))
                if not asset or qty <= 0 or basis <= 0:
                    continue
                # Same-pool conversions (ETH2→ETH deprecation etc.,
                # where disposed and acquired normalize to one symbol)
                # are carries fin already handles as Neutral — and the
                # customer-provided basis for those lots arrives via
                # the report's Receive rows at the correct per-unit.
                # Stamping the Convert row too would inject the SAME
                # lots again at a DIFFERENT per-unit than the one every
                # downstream disposal actually prices against (observed
                # on real data as a materially higher realized figure).
                disposed = (r.get(
                    "Asset Disposed (Sold, Sent, etc)") or "").strip()
                if disposed and _norm_symbol(disposed) == _norm_symbol(asset):
                    continue
                out.append({
                    "symbol": _norm_symbol(asset),
                    "date":   _iso((r.get("Date & time") or "")[:10]),
                    "qty":    qty,
                    "basis":  basis,
                    "type":   typ,
                })
    if not found:
        return None
    out.sort(key=lambda r: r["date"])
    return out


def stamp_acquisition_basis(txns: list[dict],
                            acq_rows: list[dict] | None) -> tuple[int, int]:
    """Stamp broker acquisition basis onto matching fin txns.

    Two match classes, both (Coinbase group, symbol, date ±2d, qty ±2%),
    each broker row claiming at most one txn, txns already stamped by a
    user `Cost Basis` row are never overwritten:

    1. Lot-creating txns (Buy / Transfer In / Deposit / Convert In /
       Reinvest) → plain ``basis_override`` (the walker's `_ov` /
       rebase branches pick it up).
    2. **Neutral txns** (fin's pooled ETH↔ETH2 conversions, incl. the
       ETH2 deprecation) → ``basis_override`` on a Neutral row, which
       the walker treats as a same-pool REBASE at zero gain — exactly
       how Coinbase's engine reports these (gain-0 dispositions whose
       acquired side carries the customer-provided basis).

    Returns ``(stamped_count, unmatched_count)``.
    """
    if not acq_rows:
        return 0, 0
    from datetime import date as _d, timedelta

    def _pd(s):
        try:
            return _d.fromisoformat((s or "")[:10])
        except ValueError:
            return None

    claimed: set[int] = set()
    stamped = 0
    unmatched = 0
    for row in acq_rows:
        rd = _pd(row["date"])
        if rd is None:
            continue
        pick = None
        for t in txns:
            if id(t) in claimed or t.get("basis_override") is not None:
                continue
            if t.get("account_group") != _REPORT_ACCOUNT_GROUP:
                continue
            if t.get("symbol") != row["symbol"]:
                continue
            act = t.get("action") or ""
            if act not in _LOT_ADD_ACTIONS and act != "Neutral":
                continue
            td = _pd(t.get("date", ""))
            if td is None or abs((td - rd).days) > 2:
                continue
            tq = float(t.get("quantity", 0) or 0)
            if abs(tq - row["qty"]) > max(1e-6, row["qty"] * 0.02):
                continue
            pick = t
            break
        if pick is None:
            unmatched += 1
            continue
        claimed.add(id(pick))
        pick["basis_override"] = float(row["basis"])
        stamped += 1
    return stamped, unmatched
