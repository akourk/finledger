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
import math
import re
from datetime import datetime
from pathlib import Path

from ..config import CACHE_DIR, load_json_cache
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
        _ticker_renames = load_json_cache(
            CACHE_DIR / "ticker_renames.json", {})
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

class CSVValue(str):
    """A cell carrying location metadata, without exposing its contents in errors."""

    def __new__(cls, value, source, line, field):
        obj = super().__new__(cls, value)
        obj.location = f"{source}: row {line}, column {field}"
        return obj


_substantive_rows = 0


def take_substantive_rows():
    """Count actual data rows, excluding explicitly recognized information."""
    global _substantive_rows
    count = _substantive_rows
    _substantive_rows = 0
    return count


def skip_informational_row():
    """Exclude a documented pending/zero-value row from empty-import guards."""
    global _substantive_rows
    _substantive_rows -= 1


def read_csv_rows(lines, source, *, required=(), nonblank=(), line_offset=0):
    """Read CSV cells with precise numeric error locations and header checks."""
    global _substantive_rows
    from itertools import chain
    iterator = iter(lines)
    for first in iterator:
        if first.strip():
            iterator = chain([first], iterator)
            break
        line_offset += 1
    reader = csv.DictReader(iterator)
    if len(reader.fieldnames or ()) != len(set(reader.fieldnames or ())):
        raise ValueError(f"{source}: duplicate CSV column names")
    missing = set(required) - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"{source}: missing required CSV column(s): "
                         + ", ".join(sorted(missing)))
    for row in reader:
        if None in row:
            raise ValueError(f"{source}: row {reader.line_num + line_offset}: "
                             "more cells than header columns")
        if not any(str(value or "").strip() for value in row.values()):
            continue
        if any(value is None for key, value in row.items() if key in required):
            raise ValueError(f"{source}: row {reader.line_num + line_offset}: "
                             "missing required cells")
        for field in nonblank:
            if not str(row.get(field) or "").strip():
                raise ValueError(f"{source}: row {reader.line_num + line_offset}, "
                                 f"column {field}: required value is blank")
        _substantive_rows += 1
        yield {key: CSVValue(value or "", source,
                            reader.line_num + line_offset, key)
               for key, value in row.items()}


def _num(val: str) -> float:
    """Parse finite broker numbers; blank optional cells alone mean zero."""
    location = getattr(val, "location", "numeric field")
    if val is None or not str(val).strip():
        return 0.0
    val = str(val).strip()
    if val.startswith("(") and val.endswith(")"):
        val = "-" + val[1:-1]
    val = re.sub(r"^([+-]?)\$", r"\1", val)
    # Keep legitimate thousands groups and scientific notation while refusing
    # misplaced separators ('1,2') and numeric prefixes ('123typo').
    if not re.fullmatch(r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", val):
        raise ValueError(f"{location}: invalid number; fix the CSV before exporting")
    val = val.replace(",", "")
    try:
        number = float(val)
    except (ValueError, TypeError):
        raise ValueError(f"{location}: invalid number; fix the CSV before exporting") from None
    if not math.isfinite(number):
        raise ValueError(f"{location}: number must be finite; fix the CSV before exporting")
    return number


# ---------------------------------------------------------------------------
# Dropped-row accounting
# ---------------------------------------------------------------------------
# Every parser drops a row whose date won't parse, with a bare
# ``continue``.  That is right for one odd row but silent for all of
# them, so a broker changing its date format empties a file without a
# word (see F-017 / docs/AUDIT.md).
#
# Each parser calls exactly ONE ``_date_*`` helper, once per row, inside
# that try/except, and none of them tries several formats speculatively.
# So a raise from these helpers IS a dropped row, exactly — which makes
# this the one place that can count drops without touching any parser's
# logic.  The helpers re-raise unchanged; only the tally is new.
_date_parse_failures = 0


def _count_date_failure(val) -> None:
    """Tally a row about to be dropped for an unparseable date.

    A BLANK value is not counted.  Trailing blank lines and spacer rows
    are structural, not a format problem, and counting them would put a
    permanent false warning on files that legitimately contain them.
    """
    global _date_parse_failures
    if (val or "").strip() or isinstance(val, CSVValue):
        _date_parse_failures += 1


def take_date_failures() -> int:
    """Return the number of rows dropped since the last call, and reset.

    Read once per file by ``parse_all_files``; the reset is what keeps
    one file's drops from being attributed to the next.
    """
    global _date_parse_failures
    n = _date_parse_failures
    _date_parse_failures = 0
    return n


def _date_mdy(val: str) -> str:
    """Parse M/D/YYYY → YYYY-MM-DD, handling 'as of' suffix."""
    raw = val
    val = val.strip().strip('"')
    if " as of " in val:
        val = val.split(" as of ")[0]
    try:
        return datetime.strptime(val, "%m/%d/%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        _count_date_failure(raw)
        raise


def _date_ymd(val: str) -> str:
    """Parse YYYY-MM-DD → YYYY-MM-DD (validate)."""
    try:
        return datetime.strptime(val.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        _count_date_failure(val)
        raise


def _date_dmy(val: str) -> str:
    """Parse D/M/YYYY → YYYY-MM-DD."""
    try:
        return datetime.strptime(val.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        _count_date_failure(val)
        raise


def _date_iso(val: str) -> str:
    """Parse ISO datetime like '2026-01-19 17:02:44 UTC' → YYYY-MM-DD."""
    raw = val
    val = val.strip().replace(" UTC", "").replace("Z", "")
    if "T" in val:
        val = val.split(".")[0]
    try:
        return datetime.fromisoformat(val).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        _count_date_failure(raw)
        raise


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
