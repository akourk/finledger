"""Corporate-action helpers — pure functions extracted from the
broker parsers so each pattern is testable in isolation and shareable
if another broker ever ships the same codes.

Robinhood's CSV uses a small vocabulary of corp-action codes that the
default Buy/Sell parsing path can't handle on its own:

- ``MRGS``   — merger surrender / receive (S suffix vs. no-S)
- ``MRGC``   — cash received in a cash-only merger
- ``MRGR``   — merger receive (rare; same as MRGS no-S)
- ``CIL``    — cash in lieu of fractional shares
- ``SPR``    — stock split or spinoff (S suffix vs. no-S)
- ``SOFF``   — spinoff (alternative code; treated like SPR no-S)
- ``CONV``   — account-migration / class conversion
- ``LIQ``    — cash liquidation (often CVR payouts)
- ``OEXCS``  — option exercise; pairs with OCC for cash settlement
- ``OEXP``   — option expiration

This module owns the pooling / pairing / classification logic.  The
parser still drives the row-by-row emission since it knows broker-
specific column layouts; it just calls into these helpers for the
non-trivial decisions.
"""

from __future__ import annotations

import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Quantity helpers
# ---------------------------------------------------------------------------

def parse_qty_with_surrender_marker(qty_raw: str) -> tuple[float, bool]:
    """Parse Robinhood's ``"{N}"`` (received) or ``"{N}S"`` (surrendered)
    quantity notation.

    Returns ``(qty, is_surrender)`` — qty is always non-negative; the
    boolean indicates whether the row represents shares LEAVING the
    account (S suffix) vs. shares ARRIVING.
    """
    raw = (qty_raw or "").strip()
    if not raw:
        return (0.0, False)
    is_surrender = raw.upper().endswith("S")
    body = raw[:-1].strip() if is_surrender else raw
    try:
        return (float(body), is_surrender)
    except ValueError:
        return (0.0, is_surrender)


# ---------------------------------------------------------------------------
# Description parsers (CIL / LIQ embed qty + price in their text)
# ---------------------------------------------------------------------------

_CIL_RE = re.compile(
    r"CIL on\s+([\d.]+)\s*@\s*\$?([\d,.]+)\s*-\s*(\S+)",
)
_LIQ_RE = re.compile(
    r"Cash Liquidation\s+([\d.]+)\s+shares?\s+at\s+([\d.]+)",
    re.IGNORECASE,
)


def parse_cil_description(desc: str) -> tuple[float, float]:
    """Extract ``(qty, per_share_price)`` from a Robinhood CIL
    description like ``"CIL on 0.723 @ $100.00 - AMD"``.  Returns
    ``(0, 0)`` if the description doesn't match.
    """
    m = _CIL_RE.match(desc or "")
    if not m:
        return (0.0, 0.0)
    qty = float(m.group(1))
    px  = float(m.group(2).replace(",", ""))
    return (qty, px)


def parse_liq_description(desc: str) -> tuple[float, float]:
    """Extract ``(qty, per_share_price)`` from a Robinhood LIQ
    description like ``"Cash Liquidation 11 shares at 0.01500000"``."""
    m = _LIQ_RE.match(desc or "")
    if not m:
        return (0.0, 0.0)
    return (float(m.group(1)), float(m.group(2)))


# ---------------------------------------------------------------------------
# Pool indexers — used to pair related rows across the CSV
# ---------------------------------------------------------------------------

def build_mrgc_pool(rows: list[tuple[str, dict]]) -> dict[tuple[str, str], list[dict]]:
    """Index MRGC (cash received in merger) rows by ``(date, symbol)``.

    Each entry is a list because a ticker can theoretically receive
    cash on the same day from multiple sub-events; in practice it's
    almost always 1.
    """
    pool: dict[tuple[str, str], list[dict]] = {}
    for date, row in rows:
        if (row.get("Trans Code") or "").strip() != "MRGC":
            continue
        sym = (row.get("Instrument") or "").strip()
        pool.setdefault((date, sym), []).append(row)
    return pool


def build_occ_pool(rows: list[tuple[str, dict]]) -> dict[tuple[str, str], list[dict]]:
    """Index OCC (Option Maturity: Cash Component) rows by
    ``(date, underlying)`` so each OEXCS can find its paired cash leg."""
    pool: dict[tuple[str, str], list[dict]] = {}
    for date, row in rows:
        if (row.get("Trans Code") or "").strip() != "OCC":
            continue
        sym = (row.get("Instrument") or "").strip()
        pool.setdefault((date, sym), []).append(row)
    return pool


def build_mrgs_receive_dates(rows: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """Map receipt dates for MRGS mergers and SXCH stock exchanges.

    The CIL handler uses these no-S quantities to recognize fractional
    cash-outs separately from the whole shares already received.
    """
    out: dict[str, list[str]] = {}
    for date, row in rows:
        if (row.get("Trans Code") or "").strip() not in ("MRGS", "SXCH"):
            continue
        qty_raw = (row.get("Quantity", "") or "").strip()
        if not qty_raw or qty_raw.upper().endswith("S"):
            continue
        sym = (row.get("Instrument") or "").strip()
        if sym:
            out.setdefault(sym, []).append(date)
    return out


def build_held_at_some_point(rows: list[tuple[str, dict]]) -> set[str]:
    """Set of symbols that have any share-ADDING event in the CSV
    (Buy, REINV, MRGS/SXCH/SPR-receive). CIL or merger surrender on
    a symbol NOT in this set means the user never held the shares (a
    spin-off warrant or pre-rename ticker), so proceeds should route
    to a USD Dividend rather than creating a phantom negative balance.
    """
    held: set[str] = set()
    for _date, row in rows:
        code = (row.get("Trans Code") or "").strip()
        qty  = (row.get("Quantity", "") or "").strip()
        sym  = (row.get("Instrument") or "").strip()
        if not sym:
            continue
        if code in ("Buy", "REINV"):
            held.add(sym)
        elif code in ("MRGS", "SPR", "SXCH") and qty and not qty.upper().endswith("S"):
            held.add(sym)
    return held


# ---------------------------------------------------------------------------
# Cross-row classifications
# ---------------------------------------------------------------------------

def is_cil_from_recent_merger(symbol: str, cil_date: str,
                              mrgs_receive_dates: dict[str, list[str]],
                              window_days: int = 30) -> bool:
    """Return True if ``cil_date`` is within ``window_days`` AFTER any
    MRGS-receive on ``symbol``.  Indicates the CIL is the
    fractional-share cash-out from a stock-for-stock merger (those
    fractional shares were never actually issued, so the CIL is
    income — not a sale of held shares).
    """
    try:
        cil_d = datetime.strptime(cil_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    for mrgs_date in mrgs_receive_dates.get(symbol, []):
        try:
            md = datetime.strptime(mrgs_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = (cil_d - md).days
        if 0 <= delta <= window_days:
            return True
    return False


def is_option_description(desc: str) -> bool:
    """Heuristic: does the description name an option contract?  Used
    to route CONV / similar generic codes to the option-contract
    symbol so they don't inflate the underlying stock's balance."""
    if not desc:
        return False
    return " Call " in desc or " Put " in desc
