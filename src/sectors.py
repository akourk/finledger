"""Sector classification — cache-first, yfinance fallback.

The cache is a plain `symbol → sector` JSON file kept under `cache/`.  It is
loaded lazily, mutated in memory, and saved once at the end of a run so we
don't hammer disk on every lookup.

Lookup order for a symbol:

1. Hard-coded rules (USD → Cash, `-USD` suffix → Cryptocurrency,
   multi-word names → Mutual Funds).
2. Cache hit.
3. yfinance `.info` — pick `sector`; if missing, map `quoteType` to
   ETFs / Mutual Funds / Cryptocurrency.
4. Fallback to "Other" (cached, so we don't refetch).

The cache file is editable by hand — if yfinance returns a bad sector,
just edit the JSON.  Values land in the cache whether fetched or
fallen-back so no symbol gets looked up twice in one run.
"""

import json
from pathlib import Path

from .config import CACHE_DIR, CASH_SYMBOLS

SECTOR_CACHE_FILE = CACHE_DIR / "sector_cache.json"

# In-memory cache + dirty flag.  Lazy-loaded on first access.
_cache: dict[str, str] | None = None


def reset_cache() -> None:
    """Clear the in-memory sector cache so next access re-reads from
    disk.  Used by tests; see prices.reset_caches."""
    global _cache, _dirty
    _cache = None
    _dirty = False
_dirty: bool = False

# yfinance imported lazily so we don't pay the import cost on dry-run.
_yf = None


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        if SECTOR_CACHE_FILE.exists():
            with open(SECTOR_CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        else:
            _cache = {}
    return _cache


def save_cache() -> None:
    """Write cache back to disk if anything changed this run."""
    global _dirty
    if not _dirty or _cache is None:
        return
    SECTOR_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SECTOR_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2, sort_keys=True, ensure_ascii=False)
    _dirty = False


def _lazy_yf():
    """Import yfinance on first use so we don't pay the startup cost
    on runs that don't need a fetch."""
    global _yf
    if _yf is None:
        try:
            import yfinance as yf  # noqa: import inside function by design
            _yf = yf
        except ImportError:
            _yf = False  # sentinel: tried and failed
    return _yf or None


def _classify_no_fetch(symbol: str) -> str | None:
    """Return a sector without hitting the network, or None if we need to fetch."""
    if not symbol:
        return "Other"
    if symbol in CASH_SYMBOLS or symbol == "USD":
        return "Cash"
    # Anything suffixed -USD came from our crypto normalization step.
    if symbol.endswith("-USD"):
        return "Cryptocurrency"
    # Robinhood option contracts are stored with their description as the
    # symbol — e.g. "NDXP 4/22/2026 Call $20,000.00".  Catch these before
    # the generic multi-word rule.
    if " Call " in symbol or " Put " in symbol or symbol.endswith(" OPTION"):
        return "Options"
    # Multi-word "symbols" are really fund display names (e.g. "VANG US
    # GROWTH ADM", "Vanguard Employee Benefit Index Fund") — Yahoo can't
    # resolve them.  Treat as Mutual Funds.
    if " " in symbol:
        return "Mutual Funds"
    return None


def _fetch_from_yfinance(symbol: str) -> str:
    """One-shot yfinance lookup.  Returns a sector string (never raises)."""
    yf = _lazy_yf()
    if yf is None:
        return "Other"
    # Preferred/delisted Robinhood placeholders end in ^ — strip and try again.
    probe = symbol.rstrip("^")
    try:
        info = yf.Ticker(probe).info or {}
    except Exception:
        return "Other"
    sector = (info.get("sector") or "").strip()
    if sector:
        return sector
    qt = (info.get("quoteType") or "").upper()
    if qt == "ETF":
        return "ETFs"
    if qt == "MUTUALFUND":
        return "Mutual Funds"
    if qt == "CRYPTOCURRENCY":
        return "Cryptocurrency"
    return "Other"


def get_sector(symbol: str) -> str:
    """Return the sector for `symbol`.  Uses cache, fetches on miss."""
    global _dirty
    quick = _classify_no_fetch(symbol)
    if quick is not None:
        return quick

    cache = _load_cache()
    if symbol in cache:
        return cache[symbol]

    # Try base symbol first (handles AKRO^ → AKRO type stripping).
    probe = symbol.rstrip("^")
    if probe != symbol and probe in cache:
        sector = cache[probe]
    else:
        sector = _fetch_from_yfinance(symbol)

    cache[symbol] = sector
    _dirty = True
    return sector


def enrich_holdings(rows: list[dict], *, verbose: bool = True) -> list[dict]:
    """Add a `sector` field to each row (in place) and return the list.

    Counts how many are cache-miss fetches so we can surface progress on
    runs that touch new symbols.
    """
    cache = _load_cache()
    to_fetch = [r["symbol"] for r in rows
                if _classify_no_fetch(r["symbol"]) is None
                and r["symbol"] not in cache]

    if verbose and to_fetch:
        print(f"  Fetching sector for {len(to_fetch)} new symbol(s): "
              f"{', '.join(to_fetch[:10])}"
              f"{'...' if len(to_fetch) > 10 else ''}")

    for r in rows:
        r["sector"] = get_sector(r["symbol"])
    return rows
