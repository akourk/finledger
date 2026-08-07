"""Parser package — one module per broker.

Import :func:`parse_all_files` for the normal pipeline entry point.
Individual parsers are importable by name (``from src.parsers import
parse_robinhood``) for unit testing.

Helpers and the ticker-rename layer live in ``_helpers`` and are
re-exported here for backward compatibility with code that still
imports from :mod:`src.parsers` directly.
"""

from __future__ import annotations

from pathlib import Path

from ..scanner import detect_broker
from ._helpers import (
    Transaction,
    apply_ticker_rename,
    _load_ticker_renames,
    _num, _date_mdy, _date_ymd, _date_dmy, _date_iso, _txn,
    take_date_failures,
)
from .apple_savings import parse_apple_savings
from .coinbase import parse_coinbase, parse_coinbase_pro
from .manual import parse_manual
from .robinhood import parse_robinhood
from .robinhood_apex import parse_robinhood_apex
from .schwab import parse_schwab
from .usaa import parse_usaa
from .vanguard import parse_vanguard_401k
from .voya import parse_voya_401k

__all__ = [
    "Transaction", "apply_ticker_rename",
    "parse_all_files",
    "parse_apple_savings", "parse_coinbase", "parse_coinbase_pro",
    "parse_manual", "parse_robinhood", "parse_robinhood_apex",
    "parse_schwab", "parse_usaa",
    "parse_vanguard_401k", "parse_voya_401k",
]


_PARSERS = {
    "robinhood": parse_robinhood,
    "robinhood_apex": parse_robinhood_apex,
    "coinbase": parse_coinbase,
    "coinbase_pro": parse_coinbase_pro,
    "schwab_rollover": parse_schwab,
    "schwab_roth": parse_schwab,
    "vanguard_401k": parse_vanguard_401k,
    "voya_401k": parse_voya_401k,
    "usaa": parse_usaa,
    "apple_savings": parse_apple_savings,
    "manual": parse_manual,
}


# A broker file with more substantive lines than this, that still parses
# to nothing, is assumed to have DATA the parser failed to read rather
# than to be legitimately empty.  Set well above the longest known
# preamble (Voya writes six lines before its header) so the warning can
# never fire on an empty-but-valid file — an empty
# `manual-adjustments.csv` is the common case and must stay quiet.  The
# cost of the conservative threshold is that losing a handful of rows
# stays silent; the catastrophe this guards is a real export of hundreds
# of rows going to zero.
_EMPTY_FILE_LINE_ALLOWANCE = 8

# Per-file parse findings from the most recent ``parse_all_files`` call.
# Rebuilt from scratch on every call, so it always describes the run that
# produced the transactions currently in hand — never a stale one.
_parse_report: list[dict] = []


def parse_report() -> list[dict]:
    """Findings from the last ``parse_all_files``: files that dropped
    rows, and files that parsed to zero despite carrying data.

    Read (not consumed) — unlike ``take_date_failures``, which is a
    per-file tally that MUST be cleared between files.  This one is
    consulted once at the end of a run by
    ``analytics.data_health``, and a consuming read would mean whichever
    caller asked first won.

    Empty is the normal case and means every recognised file parsed
    cleanly.  A file that parses to zero has no transactions to
    reconstruct it from, so this out-of-band record is the only trace
    that it existed at all.
    """
    return [dict(r) for r in _parse_report]


def _has_unread_data(path: Path) -> bool:
    """True when a file carries more content than a header/preamble.

    Used only to decide whether a zero-row parse deserves a warning.
    """
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            substantive = sum(
                1 for line in fh
                if line.strip() and not line.lstrip().startswith("#")
            )
    except OSError:
        return False
    return substantive > _EMPTY_FILE_LINE_ALLOWANCE


def parse_all_files(data_dir: Path) -> list[Transaction]:
    """Parse every supported CSV in ``data_dir``.  Unknown / skipped
    files are silently ignored; each parser's output is tagged with
    the ``source`` filename for traceability.

    A recognised broker file that yields NO transactions is called out
    loudly.  Every parser drops an unparseable row with a bare
    ``continue`` — which is the right call for one odd row, but means a
    change to the broker's date format takes the whole file to zero in
    complete silence, and that account simply disappears from the
    portfolio.  Nothing downstream reliably catches it: the What's-Changed
    panel reports the drop only after the fact, and the empty-run guard
    only fires when EVERY file is empty, so one missing broker sails
    through.
    """
    global _parse_report
    _parse_report = []
    all_txns: list[Transaction] = []
    for csv_file in sorted(data_dir.glob("*.csv")):
        broker = detect_broker(csv_file)
        if broker in ("skip", "unknown"):
            continue
        parser_fn = _PARSERS.get(broker)
        if parser_fn is None:
            continue
        take_date_failures()          # discard any tally from earlier work
        txns = parser_fn(csv_file)
        dropped = take_date_failures()
        print(f"  {csv_file.name}: {len(txns)} transactions ({broker})")
        if dropped:
            print(f"  !! WARNING: {csv_file.name} — {dropped} row(s) were "
                  f"DROPPED because their date could not be parsed. Those "
                  f"transactions are missing from the portfolio. If the "
                  f"count is large, the '{broker}' export format has "
                  f"probably changed.")
        empty_with_data = not txns and _has_unread_data(csv_file)
        if empty_with_data:
            print(f"  !! WARNING: {csv_file.name} was detected as "
                  f"'{broker}' and has data rows, but parsed to ZERO "
                  f"transactions.  The export format has probably changed "
                  f"— rows whose date does not match the expected format "
                  f"are dropped silently.  This account will be MISSING "
                  f"from the portfolio.")
        # Tier 3: keep the finding, don't just print it.  The console is
        # the one place a daily run's output is least likely to be read,
        # and this is the highest-severity failure mode there is — an
        # account silently absent from the portfolio.
        if dropped or empty_with_data:
            _parse_report.append({
                "file": csv_file.name,
                "broker": broker,
                "parsed": len(txns),
                "dropped": dropped,
                "empty_with_data": empty_with_data,
            })
        all_txns.extend(txns)
    return all_txns
