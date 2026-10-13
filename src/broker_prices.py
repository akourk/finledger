"""Read private, dated broker marks without creating ledger transactions.

The optional CSV has Date,Symbol,Price columns. Marks are observed per-unit
prices in the share units of their date; valuation owns date/split selection.
This module neither writes caches nor changes holdings, quantities, or basis.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .config import normalize_symbol
from .parsers._helpers import _num, read_csv_rows


_SOURCE = "broker-prices.csv"
_COLUMNS = ("Date", "Symbol", "Price")


def load_broker_prices(path: Path) -> dict[str, dict[str, float]]:
    """Load all marks atomically; no file means no broker observations.

    Dates must be YYYY-MM-DD and prices finite/nonnegative; an explicit zero
    can be a worthless option mark. Repeated identical symbol/date marks are
    idempotent, while conflicting prices reject the complete input. Additional
    named columns are ignored. Errors name the standard input and row/column,
    never the supplied path, symbol, or cell contents.
    """
    try:
        stream = Path(path).open(newline="", encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError:
        raise OSError(f"{_SOURCE}: unable to read price input") from None

    prices: dict[str, dict[str, float]] = {}
    try:
        with stream:
            rows = read_csv_rows(stream, _SOURCE, required=_COLUMNS,
                                 nonblank=_COLUMNS, strict=True)
            for row in rows:
                raw_day = row["Date"].strip()
                try:
                    day = date.fromisoformat(raw_day).isoformat()
                except ValueError:
                    raise ValueError(f"{row['Date'].location}: expected YYYY-MM-DD") from None
                if day != raw_day:
                    raise ValueError(f"{row['Date'].location}: expected YYYY-MM-DD")
                symbol = normalize_symbol(row["Symbol"])
                if any(ord(character) < 32 for character in symbol):
                    raise ValueError(f"{row['Symbol'].location}: invalid symbol")
                price = _num(row["Price"])
                if price < 0:
                    raise ValueError(f"{row['Price'].location}: price must be nonnegative")
                history = prices.setdefault(symbol, {})
                if day in history and history[day] != price:
                    raise ValueError(f"{row['Price'].location}: conflicting price for this symbol/date")
                history[day] = price
    except UnicodeError:
        raise ValueError(f"{_SOURCE}: expected UTF-8 CSV text") from None
    except csv.Error:
        raise ValueError(f"{_SOURCE}: malformed CSV quoting") from None
    except OSError:
        raise OSError(f"{_SOURCE}: unable to read price input") from None
    return {symbol: dict(sorted(history.items()))
            for symbol, history in sorted(prices.items())}
