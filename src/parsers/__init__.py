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
)
from .apple_savings import parse_apple_savings
from .coinbase import parse_coinbase, parse_coinbase_pro
from .manual import parse_manual
from .robinhood import parse_robinhood
from .schwab import parse_schwab
from .usaa import parse_usaa
from .vanguard import parse_vanguard_401k
from .voya import parse_voya_401k

__all__ = [
    "Transaction", "apply_ticker_rename",
    "parse_all_files",
    "parse_apple_savings", "parse_coinbase", "parse_coinbase_pro",
    "parse_manual", "parse_robinhood", "parse_schwab", "parse_usaa",
    "parse_vanguard_401k", "parse_voya_401k",
]


_PARSERS = {
    "robinhood": parse_robinhood,
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


def parse_all_files(data_dir: Path) -> list[Transaction]:
    """Parse every supported CSV in ``data_dir``.  Unknown / skipped
    files are silently ignored; each parser's output is tagged with
    the ``source`` filename for traceability.
    """
    all_txns: list[Transaction] = []
    for csv_file in sorted(data_dir.glob("*.csv")):
        broker = detect_broker(csv_file)
        if broker in ("skip", "unknown"):
            continue
        parser_fn = _PARSERS.get(broker)
        if parser_fn is None:
            continue
        txns = parser_fn(csv_file)
        print(f"  {csv_file.name}: {len(txns)} transactions ({broker})")
        all_txns.extend(txns)
    return all_txns
