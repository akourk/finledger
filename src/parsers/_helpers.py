"""All broker CSV parsers — extract raw data with minimal transformation.

Each parser returns a list of :class:`~src.schema.Transaction` dicts
with a common 10-field core schema:
    date        str (ISO: YYYY-MM-DD)
    account     str (broker/account name)
    symbol      str (ticker or asset)
    action      str (raw action string from the broker)
    quantity    float
    price       float
    fees        float
    amount      float
    description str (notes, memo, etc.)
    source      str (filename)
    cusip       Optional[str]  (auto-extracted from description)

See :mod:`src.schema` for the full typed shape.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from ..config import CACHE_DIR
from ..scanner import detect_broker
from ..schema import Transaction


# ---------------------------------------------------------------------------
# Ticker rename layer
# ---------------------------------------------------------------------------
# Some brokers (Robinhood in particular) retroactively relabel old CSV
# rows after a corporate action — the same physical event shows up with
# different symbols across exports taken at different times.  Example:
# Avidity Biosciences was originally ticker RNA; after Novartis'
# acquisition announcement the ticker became RNAM, and Robinhood
# rewrote older SLIP rows from RNA→RNAM.  Worse, a separate spun-off
# entity (Atrium) later took over the RNA ticker, so "RNA" means
# different things before vs. after the spinoff.
#
# The ticker_renames.json file lets the user hand-curate these
# corrections with optional date boundaries so the reuse case (old
# RNA→new RNAM; new RNA=different entity) stays correct.  Applied at
# PARSE time so the cross-file dedup pass sees one consistent symbol.

_ticker_renames: dict | None = None


def reset_ticker_renames_cache() -> None:
    """Clear the in-memory ticker-renames cache so next access re-reads
    from disk.  Used by tests; see prices.reset_caches."""
    global _ticker_renames
    _ticker_renames = None


def _load_ticker_renames() -> dict:
    global _ticker_renames
    if _ticker_renames is None:
        path = CACHE_DIR / "ticker_renames.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _ticker_renames = json.load(f)
        else:
            _ticker_renames = {}
    return _ticker_renames


def apply_ticker_rename(account: str, symbol: str, date: str) -> str:
    """Return the canonical symbol for `(account, symbol, date)` given
    the loaded rename rules.  Falls through to the original symbol if
    no rule applies (the common case)."""
    if not symbol:
        return symbol
    rules = _load_ticker_renames().get(account, [])
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("from") != symbol:
            continue
        before = rule.get("before_date")
        after = rule.get("after_date")
        if before and date >= before:
            continue
        if after and date < after:
            continue
        return rule.get("to") or symbol
    return symbol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _num(val: str) -> float:
    """Parse a string to float, handling $, commas, parens for negatives."""
    if not val or not val.strip():
        return 0.0
    val = val.strip().replace(",", "").replace("$", "")
    if val.startswith("(") and val.endswith(")"):
        val = "-" + val[1:-1]
    try:
        return float(val)
    except ValueError:
        return 0.0


def _date_mdy(val: str) -> str:
    """Parse M/D/YYYY → YYYY-MM-DD, handling 'as of' suffix."""
    val = val.strip().strip('"')
    if " as of " in val:
        val = val.split(" as of ")[0]
    return datetime.strptime(val, "%m/%d/%Y").strftime("%Y-%m-%d")


def _date_ymd(val: str) -> str:
    """Parse YYYY-MM-DD → YYYY-MM-DD (validate)."""
    return datetime.strptime(val.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


def _date_dmy(val: str) -> str:
    """Parse D/M/YYYY → YYYY-MM-DD."""
    return datetime.strptime(val.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")


def _date_iso(val: str) -> str:
    """Parse ISO datetime like '2026-01-19 17:02:44 UTC' → YYYY-MM-DD."""
    val = val.strip().replace(" UTC", "").replace("Z", "")
    if "T" in val:
        val = val.split(".")[0]
    return datetime.fromisoformat(val).strftime("%Y-%m-%d")


def _txn(
    date: str,
    account: str,
    symbol: str,
    action: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
    description: str,
    source: str,
    cusip: str | None = None,
) -> Transaction:
    """Build a :class:`Transaction` dict.  Applies the ticker rename
    layer so retroactive relabeling (same physical event, different
    symbol in newer vs. older CSV exports) collapses to one canonical
    symbol before the cross-file dedup pass runs.

    ``cusip`` is captured opportunistically from broker descriptions
    (Robinhood embeds them) so downstream tooling can detect renames
    via CUSIP collisions.  See src/cusips.py.  Falls back to
    auto-extraction from the description if not passed explicitly.
    """
    if cusip is None and description:
        from ..cusips import extract_cusip
        cusip = extract_cusip(description)
    return {
        "date": date,
        "account": account,
        "symbol": apply_ticker_rename(account, symbol, date),
        "action": action,
        "quantity": quantity,
        "price": price,
        "fees": fees,
        "amount": amount,
        "description": description,
        "source": source,
        "cusip": cusip,
    }

__all__ = [
    "Transaction", "apply_ticker_rename",
    "_load_ticker_renames",
    "_num", "_date_mdy", "_date_ymd", "_date_dmy", "_date_iso",
    "_txn", "_parse_option_qty", "_option_contract_symbol",
    "_OPTION_ACTIONS",
]
