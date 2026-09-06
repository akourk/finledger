"""Historical price cache with yfinance fallback.

Cache layout:

- `cache/prices/{SYMBOL}.json`  — one shard per symbol:
                                   `{"symbol": ..., "prices": {YYYY-MM-DD: close}}`.
                                   Only symbols with new data are rewritten on
                                   save; delete a shard to force a refetch (also
                                   delete the symbol's meta entry).  The symbol
                                   INSIDE the file is authoritative — filenames
                                   are sanitized for Windows.  Values are stored
                                   rounded to 6 significant digits.  The legacy
                                   monolithic `cache/price_cache.json` is still
                                   read (then replaced) on first load.
- `cache/price_cache_meta.json` — per-symbol fetch state (covered range,
                                   last fetch time, failure count, retry-after,
                                   tombstone).

Prices in the cache are yfinance's `Close` column (split-adjusted close
on each trading day).  Note: yfinance's Close is *always* split-adjusted
regardless of `auto_adjust`; `auto_adjust=False` just avoids the extra
dividend adjustment.  We use `auto_adjust=False` so the stored price is
the split-adjusted market close (matches what a chart would show), not a
dividend-reinvestment-adjusted total-return number.

**Benchmark exception — total return, computed locally.**  Symbols in
`_TOTAL_RETURN_SYMBOLS` (SPY/BND/VXUS) and scaled-proxy targets need
DIVIDEND-adjusted values: the benchmark is a hypothetical with no txn
ledger, so reinvested dividends must be baked into the price for an
apples-to-apples comparison (real positions capture them via
Dividend → Reinvest → higher share count).  We store plain `Close` for
these symbols like everything else and cache their dividend events
(`cache/dividends_cache.json`); `get_price` applies the adjustment at
read time — close × ∏(1 − div/prev_close) over ex-dates AFTER the
queried date (the standard CRSP suffix-product, normalized so the
latest date equals the raw close).  This keeps the stored series
stable and incremental.  The previous design fetched `Adj Close`,
which yfinance re-normalizes whole-series whenever a new dividend is
announced — forcing a weekly full-range refetch and drifting every
historical benchmark value run-to-run.  If the dividends cache is
missing (fetch failure), the factor product is 1.0 and the symbol
degrades to price-return until the next successful refresh.

Because yfinance's historical prices are in *today's share basis*, a raw
balance multiplied by a historical price gives a wrong answer for any
asset that had splits after the balance date.  We correct this by
fetching each symbol's split history (`cache/splits_cache.json`) and,
at each sample date, scaling the as-of-date balance by the product of
split ratios for splits strictly after that date.  That gives a
today-basis balance which cancels cleanly against the today-basis
adjusted price.  See `split_factor_since` / `split_adjust_qty`.

Failure handling uses exponential backoff (1d → 2d → 4d → 8d → 16d, capped
at 30d) plus a tombstone after 5 consecutive failures.  Delisted / renamed
/ invalid tickers stop getting retried forever.

Weekend / holiday lookups walk backward to the most recent prior trading
day within a 7-day window.
"""

from . import clock
import json
import math
import re
from bisect import bisect_right
from datetime import date, datetime, time as dt_time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CACHE_DIR, CRYPTO_SYMBOLS, SYMBOL_MAP, load_json_cache

# Prices are sharded one JSON file per symbol under PRICES_DIR — only
# symbols with new data get rewritten on save, git diffs scope to the
# symbols that actually changed, and "delete to force a refetch" is a
# single file.  PRICE_CACHE_FILE is the legacy pre-sharding monolith:
# still read on load (shards override per symbol) and removed after the
# first successful shard write.
PRICES_DIR           = CACHE_DIR / "prices"
PRICE_CACHE_FILE     = CACHE_DIR / "price_cache.json"
PRICE_META_FILE      = CACHE_DIR / "price_cache_meta.json"
SPLITS_CACHE_FILE    = CACHE_DIR / "splits_cache.json"
DIVIDENDS_CACHE_FILE = CACHE_DIR / "dividends_cache.json"
PROXY_MAP_FILE       = CACHE_DIR / "symbol_proxy_map.json"

# Stored prices are rounded to this many significant digits on write —
# full float reprs inflate the shards ~30-40% for precision no consumer
# uses (sub-cent on a $100 stock; SHIB-scale prices keep 6 significant
# digits regardless of magnitude).
_PRICE_SIG_DIGITS = 6

# Weekend / holiday lookback window for get_price.
_LOOKBACK_DAYS = 7

# Backoff configuration.
_MAX_FAILURES      = 5
_BACKOFF_BASE_DAYS = 1
_BACKOFF_CAP_DAYS  = 30

# "No data returned" is only treated as a real failure when the requested
# range spans at least this many days — avoids penalizing weekend fetches
# that legitimately have nothing to return.
_NO_DATA_MIN_RANGE_DAYS = 7


def _now_utc() -> datetime:
    """Current instant, timezone-aware UTC.

    The single clock seam for freshness reasoning — tests monkeypatch
    this rather than adding a freezegun dependency.  ``_today`` derives
    from it, so pinning one pins both.  (The ``clock.now(fallback=datetime.now)`` calls
    elsewhere in this module stamp ``last_fetch`` in naive local time;
    that format is what the dashboard parses, so leave it alone.)
    """
    return clock.now(timezone.utc, fallback=datetime.now)


def _today() -> date:
    """Local calendar date.

    Everything that reasons about coverage freshness goes through here:
    ``covered_end`` is capped at this date, and settle horizons are
    compared against it.
    """
    return clock.as_of_date() or _now_utc().astimezone().date()


# ---------------------------------------------------------------------------
# Settle awareness
#
# A price is worth refetching only while it can still CHANGE.  yfinance's
# daily bar carries a live price during market hours, so a bar fetched at
# 11:00 is a provisional mark that will be replaced by the real close —
# but fin used to store it as the close and never look again.  Knowing
# when each asset class goes final is what lets us refetch until then and
# skip entirely afterwards.
#
# Times are deliberately LATE.  Getting it wrong in the "not settled yet"
# direction costs one redundant fetch that returns the same number;
# getting it wrong the other way leaves a stale figure the user trusts.
# That asymmetry is why half-days (13:00 ET close the day after
# Thanksgiving, Christmas Eve) need no calendar: we simply keep treating
# the bar as live until the normal cutoff.
# ---------------------------------------------------------------------------

try:
    _MARKET_TZ = ZoneInfo("America/New_York")
except Exception:   # pragma: no cover - no IANA tz database available
    # pandas (via yfinance) hard-requires `tzdata`, so this should never
    # fire.  If it somehow does, degrade to "nothing intraday is ever
    # final" rather than taking the module down for a nicety.
    _MARKET_TZ = None

# 16:00 ET close plus tape-settle slack.
_EQUITY_SETTLE_ET = dt_time(16, 20)
# Mutual funds strike NAV in the evening; a run between the equity close
# and this cutoff would otherwise mix today's equity closes with
# YESTERDAY's fund NAVs in one snapshot.
_FUND_SETTLE_ET = dt_time(18, 0)


def _settle_class(symbol: str) -> str:
    """``crypto`` / ``fund`` / ``equity`` — which settle rule applies.

    Companion to ``_classify_no_fetch`` (which decides whether we fetch
    at all); keep the two aligned when either one's rules change.

    Classification is deliberately biased toward ``fund``: a fund
    mistaken for an equity gets marked final before its NAV strikes,
    which is the one failure mode that produces a wrong number instead
    of a redundant fetch.
    """
    entry = _proxy_entry(symbol)
    if entry is not None:
        # Several proxies ARE mutual funds (VFIAX, VIVLX) — the proxy is
        # what actually gets fetched, so it owns the settle rule.
        symbol = entry[0]
    symbol = (symbol or "").strip()
    if symbol.endswith("-USD") or symbol in CRYPTO_SYMBOLS:
        return "crypto"
    # Multi-word "tickers" are fund display names (same rule as
    # _classify_no_fetch), and the US convention for a mutual-fund
    # ticker is five characters ending in X — SWPPX, VFIAX, FSELX.
    if " " in symbol:
        return "fund"
    if len(symbol) == 5 and symbol.isalpha() and symbol.endswith("X"):
        return "fund"
    # Everything else, ETFs included — they trade intraday and settle
    # with the tape.
    return "equity"


def _settle_horizon(symbol: str, now: datetime | None = None) -> date:
    """The newest date whose bar for ``symbol`` can no longer change.

    Anything after this is still live, so a cached value for it is a
    provisional mark rather than a close.
    """
    now = now or _now_utc()
    cls = _settle_class(symbol)
    if cls == "crypto":
        # Crypto bars are UTC-dated and keep moving until the UTC day is
        # over — which is why an evening local run legitimately receives
        # a bar dated tomorrow.
        return now.astimezone(timezone.utc).date() - timedelta(days=1)
    if _MARKET_TZ is None:
        return _today() - timedelta(days=1)
    market_now = now.astimezone(_MARKET_TZ)
    cutoff = _FUND_SETTLE_ET if cls == "fund" else _EQUITY_SETTLE_ET
    day = market_now.date()
    if market_now.time() < cutoff:
        day -= timedelta(days=1)
    # Weekends have no bar to settle; walk back the same way the fetch
    # range does.  Market holidays aren't modelled — a holiday just
    # leaves the day looking settled with no data to show for it, which
    # is the same no-op the fetch path already tolerates.
    return _last_trading_day(day)

# Controls yfinance's `auto_adjust`.  MUST be False; see module docstring
# for the reasoning.  If this changes, the on-disk cache is invalidated on
# next load because the values would be incompatible with existing entries.
_AUTO_ADJUST = False

# Symbols valued as TOTAL RETURN (split + dividend adjusted).  Used for
# benchmark comparisons where we don't have a txn ledger to handle
# dividends (the benchmark is a hypothetical, not a real position) —
# plus any symbol used as a SCALED proxy, since scaled proxies exist
# to value funds we can't see directly (CITs, private 401K funds) and
# those invariably reinvest distributions internally into NAV.  See
# _is_total_return_symbol() for the dynamic lookup.
#
# The STORED series is plain Close for every symbol; total-return
# symbols additionally cache their dividend events and get the
# dividend adjustment applied at read time (see module docstring and
# _tr_factor_after).  Everything NOT total-return stays price-only —
# dividends on real positions are tracked via the txn ledger
# (Dividend → Reinvest → more shares).
_TOTAL_RETURN_SYMBOLS = frozenset({"SPY", "BND", "VXUS"})

# How often (in days) to deep-refresh things that don't get caught
# by the incremental daily fetch path:
#   - Splits on symbols whose price cache is already covered through
#     today (the existing splits-on-fetch-extension hook misses them)
#   - Tombstoned symbols — give a delisted-or-broken ticker another
#     chance every quarter or so in case it was relisted / fixed
_DEEP_REFRESH_DAYS = 7

# In-memory state.
_prices:    dict[str, dict[str, float]] | None  = None
_meta:      dict | None                         = None
_splits:    dict[str, list[list]] | None        = None
_dividends: dict[str, list[list]] | None        = None
_proxy:     dict | None                         = None
# Per-symbol dirty tracking for the sharded price store: only symbols
# in _prices_dirty_syms get their shard rewritten on save; symbols in
# _prices_deleted_syms get their shard unlinked.  Mutually exclusive
# (see _mark_prices_dirty / _mark_prices_deleted).
_prices_dirty_syms:   set[str] = set()
_prices_deleted_syms: set[str] = set()
_legacy_prices_pending_delete = False
_meta_dirty      = False
_splits_dirty    = False
_dividends_dirty = False
_proxy_dirty     = False


def _mark_prices_dirty(sym: str) -> None:
    _prices_dirty_syms.add(sym)
    _prices_deleted_syms.discard(sym)


def _mark_prices_deleted(sym: str) -> None:
    _prices_deleted_syms.add(sym)
    _prices_dirty_syms.discard(sym)

# Lazily-built per-symbol total-return factor tables:
# {symbol: (sorted_ex_dates, suffix_products)}.  Derived from _prices +
# _dividends; dropped whenever either changes for the symbol.
_tr_factor_cache: dict[str, tuple[list[str], list[float]]] = {}

# Multi-anchor series for SCALED proxies: {mapped_symbol: (sorted_dates,
# prices)} of EVERY observed transaction price — for a 401K fund that's
# a fresh real NAV every payroll.  Built in-memory by
# ensure_proxy_anchors each run (both pipeline paths call it) and NEVER
# persisted: the dates are effectively the user's paycheck calendar.
# get_price's scaled branch anchors each lookup to the NEAREST real
# price, capping proxy tracking drift at the gap between observations
# (~2 weeks) instead of the years since the single persisted anchor.
_proxy_anchor_series: dict[str, tuple[list[str], list[float]]] = {}


def reset_caches() -> None:
    """Clear in-memory cache state so the next access re-reads from
    disk.  Used by tests that run the pipeline in isolated tmp dirs —
    without this, state leaks between tests.  Production code never
    needs to call this; the caches are loaded once per process.
    """
    global _prices, _meta, _splits, _dividends, _proxy
    global _meta_dirty, _splits_dirty, _dividends_dirty, _proxy_dirty
    global _legacy_prices_pending_delete
    _prices = None
    _meta = None
    _splits = None
    _dividends = None
    _proxy = None
    _prices_dirty_syms.clear()
    _prices_deleted_syms.clear()
    _legacy_prices_pending_delete = False
    _meta_dirty = False
    _splits_dirty = False
    _dividends_dirty = False
    _proxy_dirty = False
    _tr_factor_cache.clear()
    _proxy_anchor_series.clear()

# yfinance is imported lazily so dry-runs don't pay the import cost.
_yf = None


def _lazy_yf():
    global _yf
    if _yf is None:
        try:
            import yfinance as yf  # noqa: import inside function by design
            _yf = yf
        except ImportError:
            _yf = False
    return _yf or None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

# Windows reserved device names — a shard file literally named CON.json
# is unwritable/hazardous on Windows, and real tickers can collide with
# these (CON trades on the NYSE).
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _shard_path(sym: str) -> Path:
    """Filesystem path for a symbol's price shard.

    The filename is best-effort readable; the symbol INSIDE the file is
    authoritative on load, so the name only has to be safe and unique.
    Symbols that sanitize lossily, aren't uppercase (case-insensitive
    filesystems would collide FOO/foo), or hit a Windows reserved device
    name get a crc32 suffix to guarantee uniqueness.
    """
    safe = "".join(c if (c.isalnum() or c in ".-_") else "_" for c in sym)
    needs_suffix = (
        not safe
        or safe != sym
        or sym != sym.upper()
        or safe.split(".")[0].upper() in _WINDOWS_RESERVED
    )
    if needs_suffix:
        import zlib
        safe = f"{safe or 'SYM'}-{zlib.crc32(sym.encode('utf-8')):08x}"
    return PRICES_DIR / f"{safe}.json"


def _load_prices() -> dict[str, dict[str, float]]:
    global _prices, _legacy_prices_pending_delete
    if _prices is None:
        _prices = {}
        # Legacy pre-sharding monolith: read it first (shards override
        # per symbol), mark everything dirty so the first save writes
        # the full shard set, and remove the monolith after that write.
        if PRICE_CACHE_FILE.exists():
            try:
                with open(PRICE_CACHE_FILE, "r", encoding="utf-8") as f:
                    legacy = json.load(f)
                if isinstance(legacy, dict):
                    _prices.update(legacy)
            except (OSError, json.JSONDecodeError):
                pass
            for sym in _prices:
                _mark_prices_dirty(sym)
            _legacy_prices_pending_delete = True
        if PRICES_DIR.exists():
            for fp in sorted(PRICES_DIR.glob("*.json")):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                except (OSError, json.JSONDecodeError):
                    print(f"  Note: unreadable price shard {fp.name} — skipped")
                    continue
                sym = doc.get("symbol") if isinstance(doc, dict) else None
                series = doc.get("prices") if isinstance(doc, dict) else None
                if isinstance(sym, str) and isinstance(series, dict):
                    _prices[sym] = series
        _migrate_legacy_keys()
        _invalidate_if_policy_changed()
        _migrate_tr_close_v2()
    return _prices


def _load_meta() -> dict:
    global _meta
    if _meta is None:
        _meta = load_json_cache(
            PRICE_META_FILE,
            {"version": 1, "auto_adjusted": _AUTO_ADJUST, "symbols": {}})
        _meta.setdefault("symbols", {})
    return _meta


def _invalidate_if_policy_changed() -> None:
    """If the cache on disk was built with a different `auto_adjust` setting,
    wipe price data and reset per-symbol coverage so everything gets
    refetched with the current policy.  Keeps the failure / backoff state
    (if a ticker was bad before, it's likely still bad).
    """
    global _meta_dirty
    meta = _load_meta()
    disk_adjusted = meta.get("auto_adjusted")
    if disk_adjusted is None:
        # No policy recorded — assume it matches, just annotate.
        meta["auto_adjusted"] = _AUTO_ADJUST
        _meta_dirty = True
        return
    if bool(disk_adjusted) == bool(_AUTO_ADJUST):
        return
    # Mismatch — wipe prices and clear per-symbol coverage.
    print(f"  Note: price cache was built with auto_adjust={disk_adjusted};"
          f" invalidating (code requires auto_adjust={_AUTO_ADJUST})")
    for sym in list(_prices):
        del _prices[sym]
        _mark_prices_deleted(sym)
    for _sym, entry in meta["symbols"].items():
        entry.pop("covered_start", None)
        entry.pop("covered_end", None)
    meta["auto_adjusted"] = _AUTO_ADJUST
    _meta_dirty = True


def _load_splits() -> dict[str, list[list]]:
    global _splits
    if _splits is None:
        _splits = load_json_cache(SPLITS_CACHE_FILE, {})
    return _splits


def _load_dividends() -> dict[str, list[list]]:
    """Dividend events per total-return symbol:
    ``{symbol: [[ISO_ex_date, amount], ...]}``.  Only symbols that
    `_is_total_return_symbol` classifies get entries."""
    global _dividends
    if _dividends is None:
        if DIVIDENDS_CACHE_FILE.exists():
            with open(DIVIDENDS_CACHE_FILE, "r", encoding="utf-8") as f:
                _dividends = json.load(f)
        else:
            _dividends = {}
    return _dividends


def _load_proxy_map() -> dict:
    """Hand-editable symbol → proxy mapping.

    See ``cache/symbol_proxy_map.json`` for schema (and the in-file
    _comment_* keys for method semantics).  Returns {} if the file
    doesn't exist yet; users create it to map unfetchable symbols to
    real tickers.  Keys prefixed with ``_`` are metadata (comments);
    real entries have a ``proxy`` field.
    """
    global _proxy
    if _proxy is None:
        if PROXY_MAP_FILE.exists():
            with open(PROXY_MAP_FILE, "r", encoding="utf-8") as f:
                _proxy = json.load(f)
        else:
            _proxy = {}
    return _proxy


def _proxy_entry(symbol: str) -> tuple[str, str, str | None, float | None] | None:
    """Return ``(proxy, method, anchor_date, anchor_price)`` for the
    mapped symbol, or None if no mapping exists.  Scaled entries
    without an anchor return (proxy, "scaled", None, None) — the caller
    treats this as "no price available".
    """
    pm = _load_proxy_map()
    entry = pm.get(symbol)
    if not entry or not isinstance(entry, dict) or "proxy" not in entry:
        return None
    method = entry.get("method") or "direct"
    return (entry["proxy"], method,
            entry.get("anchor_date"),
            entry.get("anchor_price"))


def build_display_map() -> dict[str, str]:
    """Return ``{original_symbol: display_name}`` for every proxy-mapped
    entry.  Display is the entry's ``display`` field if present, else the
    proxy ticker — both shorter than multi-word fund names and keep
    column widths manageable in the dashboard.

    Unmapped symbols don't appear in the returned dict (the dashboard
    falls back to the raw symbol for those).
    """
    pm = _load_proxy_map()
    out: dict[str, str] = {}
    for sym, entry in pm.items():
        if not isinstance(entry, dict) or "proxy" not in entry:
            continue   # skip _comment entries
        out[sym] = entry.get("display") or entry["proxy"]
    return out


def _is_total_return_symbol(sym: str) -> bool:
    """Whether `sym` should be fetched/stored as total-return (Adj Close).

    True when:
    - `sym` is in the hardcoded benchmark list (SPY), or
    - `sym` is used as a SCALED proxy by any proxy-map entry.  Scaled
      proxies exist to drive valuations for funds we can't see directly
      (CITs, private 401K funds, closed tickers).  Those funds
      invariably reinvest distributions internally — the NAV rises with
      total return, not price return — so the proxy's return stream
      has to include dividends to match.  Direct proxies (ticker
      aliases like BRK.B → BRK-B) don't need this: the user holds the
      actual fund and captures dividends via Reinvest txns, so
      price-only is correct there.
    """
    if sym in _TOTAL_RETURN_SYMBOLS:
        return True
    pm = _load_proxy_map()
    for entry in pm.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("proxy") == sym and entry.get("method") == "scaled":
            return True
    return False


def _round_price(v: float) -> float:
    """Round to _PRICE_SIG_DIGITS significant digits for storage."""
    try:
        return float(f"{v:.{_PRICE_SIG_DIGITS}g}")
    except (TypeError, ValueError):
        return v


def save_caches() -> None:
    """Write all caches back to disk (only what's dirty).

    Prices are sharded: only symbols marked dirty get their shard file
    rewritten, and symbols marked deleted get theirs unlinked.  The
    legacy monolithic ``price_cache.json`` (if it was read this process)
    is removed after the first successful shard write.
    """
    global _meta_dirty, _splits_dirty, _dividends_dirty, _proxy_dirty
    global _legacy_prices_pending_delete
    if (_prices_dirty_syms or _prices_deleted_syms) and _prices is not None:
        PRICES_DIR.mkdir(parents=True, exist_ok=True)
        for sym in sorted(_prices_dirty_syms):
            series = _prices.get(sym)
            if series is None:
                continue
            doc = {
                "symbol": sym,
                "prices": {d: _round_price(p) for d, p in series.items()},
            }
            with open(_shard_path(sym), "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=1, sort_keys=True, ensure_ascii=False)
        for sym in sorted(_prices_deleted_syms):
            try:
                _shard_path(sym).unlink()
            except OSError:
                pass
        _prices_dirty_syms.clear()
        _prices_deleted_syms.clear()
        if _legacy_prices_pending_delete:
            try:
                PRICE_CACHE_FILE.unlink()
            except OSError:
                pass
            _legacy_prices_pending_delete = False
    if _dividends_dirty and _dividends is not None:
        DIVIDENDS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DIVIDENDS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_dividends, f, indent=2, sort_keys=True, ensure_ascii=False)
        _dividends_dirty = False
    if _meta_dirty and _meta is not None:
        PRICE_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PRICE_META_FILE, "w", encoding="utf-8") as f:
            json.dump(_meta, f, indent=2, sort_keys=True, ensure_ascii=False)
        _meta_dirty = False
    if _splits_dirty and _splits is not None:
        SPLITS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SPLITS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_splits, f, indent=2, sort_keys=True, ensure_ascii=False)
        _splits_dirty = False
    if _proxy_dirty and _proxy is not None:
        PROXY_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROXY_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(_proxy, f, indent=2, sort_keys=True, ensure_ascii=False)
        _proxy_dirty = False


def ensure_proxy_anchors(txns: list[dict], *, verbose: bool = True) -> None:
    """Anchor scaled proxy-map entries to the user's own txn prices.

    Two layers:

    1. **Persisted single anchor** (``anchor_date`` / ``anchor_price``
       on the map entry): the first observed non-zero txn price, kept
       for back-compat and as the fallback when no in-memory series is
       available (auto-populated only when missing).
    2. **In-memory multi-anchor series** (``_proxy_anchor_series``):
       EVERY observed txn price for the mapped symbol, rebuilt each run
       and never persisted.  ``get_price`` anchors each scaled lookup
       to the NEAREST real observation, so proxy tracking drift is
       bounded by the gap between observations (biweekly for a 401K
       fund with payroll contributions) instead of accumulating for
       years from the single first anchor.  (On real data this proved
       the user's CIT tracks VFIAX almost perfectly — the residual
       statement-vs-computed deltas are statement composition, e.g.
       year-end accruals, not valuation drift — but it guards the
       valuation against any future divergence at negligible cost.)
    """
    global _proxy, _proxy_dirty
    pm = _load_proxy_map()
    # All observed txn prices per scaled-mapped symbol (last one wins
    # per date — a same-day exchange pair carries one NAV anyway).
    observed: dict[str, dict[str, float]] = {}
    for t in txns:
        sym = t.get("symbol", "")
        if not sym or sym not in pm:
            continue
        entry = pm[sym]
        if not isinstance(entry, dict) or entry.get("method") != "scaled":
            continue
        price = float(t.get("price", 0) or 0)
        d = t.get("date", "")
        if price <= 0 or not d:
            continue
        observed.setdefault(sym, {})[d] = price

    for sym, series in observed.items():
        dates = sorted(series)
        _proxy_anchor_series[sym] = (dates, [series[d] for d in dates])
        entry = pm[sym]
        if not (entry.get("anchor_price") and entry.get("anchor_date")):
            d, p = dates[0], series[dates[0]]
            entry["anchor_date"]  = d
            entry["anchor_price"] = round(p, 4)
            _proxy_dirty = True
            if verbose:
                print(f"  proxy anchor: {sym} -> {entry['proxy']} "
                      f"(anchor {d} @ ${p:.2f})")
        if verbose and len(dates) > 1:
            print(f"  proxy anchors: {sym} -> {entry['proxy']} "
                  f"({len(dates)} observed prices, "
                  f"{dates[0]}..{dates[-1]})")


def _nearest_anchor(symbol: str, iso_date: str) -> tuple[str, float] | None:
    """The observed (date, price) closest to ``iso_date`` for a scaled
    proxy symbol, or None when no in-memory series exists."""
    series = _proxy_anchor_series.get(symbol)
    if not series:
        return None
    dates, prices = series
    i = bisect_right(dates, iso_date)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(dates):
            gap = abs((_parse_iso(dates[j]) - _parse_iso(iso_date)).days)
            if best is None or gap < best[0]:
                best = (gap, dates[j], prices[j])
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------------------
# One-time migration: bare crypto keys → canonical -USD keys
# ---------------------------------------------------------------------------

def _migrate_legacy_keys() -> None:
    """Fold legacy bare-ticker crypto entries into canonical `-USD` keys.

    The pipeline now normalizes crypto to `BTC-USD`, `ETH-USD`, etc.  The
    pre-existing cache keyed the same data on bare tickers (`BTC`, `ETH`, …).
    Merge them so lookups against the post-normalization symbol hit the full
    history.  Also folds explicit `SYMBOL_MAP` remaps (e.g. `ETH2 → ETH-USD`).
    """
    global _meta_dirty
    meta = _load_meta()
    if meta.get("migrated_v1"):
        return

    def _merge(src_key: str, dst_key: str) -> None:
        if src_key == dst_key or src_key not in _prices:
            return
        src = _prices.pop(src_key)
        _mark_prices_deleted(src_key)
        _mark_prices_dirty(dst_key)
        dst = _prices.setdefault(dst_key, {})
        # Richer source dominates collisions; otherwise fill only empty dates.
        # "Richer" = 10× more entries (catches single-date ghost rows).
        if len(src) > 10 * max(len(dst), 1):
            for dt, px in src.items():
                dst[dt] = px
        else:
            for dt, px in src.items():
                dst.setdefault(dt, px)

    for sym in sorted(CRYPTO_SYMBOLS):
        _merge(sym, f"{sym}-USD")
    for src, dst in SYMBOL_MAP.items():
        _merge(src, dst)

    meta["migrated_v1"] = True
    _meta_dirty = True


def _migrate_tr_close_v2() -> None:
    """One-time wipe of total-return symbols' cached series.

    Before the local total-return feature, SPY/BND/VXUS (and scaled-proxy
    targets) were stored as yfinance ``Adj Close`` — split AND dividend
    adjusted.  The store is now plain ``Close`` for every symbol with the
    dividend adjustment applied at read time, so the old values are in
    the wrong basis.  Wipe them (and their coverage meta) so the next
    ``ensure_coverage`` refetches as Close.
    """
    global _meta_dirty
    meta = _load_meta()
    if meta.get("migrated_tr_close_v2"):
        return
    for sym in list(_prices):
        if _is_total_return_symbol(sym):
            del _prices[sym]
            _mark_prices_deleted(sym)
            entry = meta["symbols"].get(sym)
            if entry:
                entry.pop("covered_start", None)
                entry.pop("covered_end", None)
                entry.pop("last_full_refresh", None)
    meta["migrated_tr_close_v2"] = True
    _meta_dirty = True


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def _parse_iso(d) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def get_price(symbol: str, on_date) -> float | None:
    """Return the adjusted close price for `symbol` on `on_date`.

    Walks backward up to `_LOOKBACK_DAYS` to the most recent prior trading
    day.  Returns None if no price is available in that window.

    Consults ``symbol_proxy_map.json`` first — mapped symbols resolve
    via their proxy's price stream (see _proxy_entry for method
    semantics).
    """
    entry = _proxy_entry(symbol)
    if entry is not None:
        proxy, method, anchor_date, anchor_price = entry
        if method == "direct":
            return get_price(proxy, on_date)
        if method == "scaled":
            # Scaled needs both an anchor (ground-truth txn price) and
            # the proxy's price on both the anchor date and the query
            # date.  Any missing piece → return None so the caller
            # falls back to the last_txn_price.  Prefer the observed
            # txn price NEAREST the query date (multi-anchor series —
            # see ensure_proxy_anchors) over the persisted first-price
            # anchor: drift is then bounded by the observation gap.
            near = _nearest_anchor(symbol, _parse_iso(on_date).isoformat())
            if near is not None:
                anchor_date, anchor_price = near
            if not anchor_date or anchor_price is None:
                return None
            now_px = get_price(proxy, on_date)
            if now_px is None:
                return None
            anchor_px = get_price(proxy, anchor_date)
            if anchor_px is None or anchor_px <= 0:
                return None
            return anchor_price * (now_px / anchor_px)
        # Unknown method — fall through to the normal path (treat as not proxied)
    if _classify_no_fetch(symbol):
        return None
    prices = _load_prices()
    series = prices.get(symbol)
    if not series:
        return None
    target = _parse_iso(on_date)
    for i in range(_LOOKBACK_DAYS + 1):
        probe = (target - timedelta(days=i)).isoformat()
        if probe in series:
            val = series[probe]
            # Treat any entry that is not a finite real number as a GAP
            # and keep walking back to a real prior close.
            #
            # NaN is the original case: an older cache (written before
            # _fetch_range filtered NaN) may still hold one, and
            # propagating it puts NaN into value/unrealized everywhere —
            # and `json.dump` writes a bare NaN literal, which is valid
            # JavaScript, so it renders rather than failing.
            #
            # The type check matters just as much.  These shards are
            # documented as hand-editable ("plain JSON … delete freely"),
            # and a hand-edit that QUOTES a number returned the string
            # unchanged: `val *= _tr_factor_after(...)` below and every
            # downstream `qty * price` then operate on a str.  bool is
            # excluded explicitly because it is an int subclass and
            # `True` is not a price.
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            if math.isnan(val) or math.isinf(val):
                continue
            if _is_total_return_symbol(symbol):
                val *= _tr_factor_after(symbol, probe)
            return val
    return None


def get_series(symbol: str, start, end) -> dict[str, float]:
    """Return {date: price} entries within [start, end] (inclusive).

    Total-return symbols get the same dividend adjustment as get_price.
    NOTE: unlike get_price, this does NOT route through the proxy map
    or filter legacy NaN entries — its only consumer (daily_pnl) uses
    it for trading-day detection.
    """
    prices = _load_prices()
    series = prices.get(symbol, {})
    start_s = _parse_iso(start).isoformat()
    end_s   = _parse_iso(end).isoformat()
    out = {d: p for d, p in series.items() if start_s <= d <= end_s}
    if out and _is_total_return_symbol(symbol):
        out = {d: p * _tr_factor_after(symbol, d) for d, p in out.items()}
    return out


_CORP_ACTION_SUFFIXES = ("^", "+", ".U", ".W", ".WS", "-W", "-WS")


def _classify_no_fetch(symbol: str) -> bool:
    """Symbols we never try to fetch (cash, fund display names,
    corp-action stubs, empty)."""
    if not symbol:
        return True
    if symbol == "USD":
        return True
    # Multi-word "tickers" are really fund display names; yfinance can't
    # resolve them (same rule as sectors._classify_no_fetch).
    if " " in symbol:
        return True
    # Corp-action stub symbols — Robinhood uses suffixes like "^" for
    # CVRs (Contingent Value Rights from M&A), "+" for warrants,
    # ".U"/".W"/".WS"/"-W" for SPAC units & warrants.  These are
    # legitimate securities the user holds but yfinance has no price
    # feed for them.  Skipping the fetch keeps them out of the
    # failing-tickers alert and avoids burning the failure_count /
    # tombstone backoff on something that will never resolve.
    if symbol.endswith(_CORP_ACTION_SUFFIXES):
        return True
    return False


# ---------------------------------------------------------------------------
# Fetch / backoff
# ---------------------------------------------------------------------------

def _should_skip(symbol: str, now: datetime) -> bool:
    entry = _load_meta()["symbols"].get(symbol)
    if not entry:
        return False
    if entry.get("tombstone"):
        return True
    retry_after = entry.get("retry_after")
    if retry_after and now.date() < _parse_iso(retry_after):
        return True
    return False


def _record_failure(symbol: str, error: str, now: datetime) -> None:
    global _meta_dirty
    meta = _load_meta()
    entry = meta["symbols"].setdefault(symbol, {})
    entry["failure_count"] = entry.get("failure_count", 0) + 1
    entry["last_fetch"]    = now.isoformat(timespec="seconds")
    entry["last_error"]    = error
    days = min(_BACKOFF_CAP_DAYS,
               _BACKOFF_BASE_DAYS * (2 ** (entry["failure_count"] - 1)))
    entry["retry_after"] = (now.date() + timedelta(days=days)).isoformat()
    if entry["failure_count"] >= _MAX_FAILURES:
        entry["tombstone"] = True
    _meta_dirty = True


def _cap_covered_end(value: str) -> str:
    """Clamp a ``covered_end`` claim to the local calendar date.

    A bar dated in the FUTURE is real data and stays in the shard —
    crypto bars are UTC-dated, so an evening local run legitimately
    receives tomorrow's bar and ``get_price`` should return it once that
    date arrives.  What must never happen is the *coverage claim*
    running ahead of the local calendar: ``_missing_ranges`` compares
    the requested end against ``covered_end``, so a covered_end of
    tomorrow makes the whole of tomorrow look already-fetched and the
    symbol is skipped for an entire local day.
    """
    return min(value, _today().isoformat())


def _record_settled_through(entry: dict, symbol: str) -> None:
    """Record how far into ``entry``'s coverage the bars are FINAL.

    ``covered_end`` alone can't answer "is this value done moving?" —
    yfinance's daily bar carries a live price during market hours, so a
    bar fetched at 11:00 is a provisional mark stored as if it were the
    close.  What makes a cached date final is that we fetched it AFTER
    that date settled, which is exactly ``min(covered_end, horizon)``.

    Both bounds are load-bearing.  The horizon alone would claim
    settlement for dates we hold no data for; ``covered_end`` alone
    would call this morning's live mark a close.  Neither input moves
    backwards in normal operation, so the result doesn't either — no
    max() against the stored value is needed, and adding one would only
    let the claim outrun coverage when the meta file is hand-edited
    (which it is designed to be).
    """
    covered_end = entry.get("covered_end")
    if not covered_end:
        return
    entry["settled_through"] = min(covered_end,
                                   _settle_horizon(symbol).isoformat())


def _record_success(symbol: str, start: date, end: date, now: datetime) -> None:
    global _meta_dirty
    meta = _load_meta()
    entry = meta["symbols"].setdefault(symbol, {})
    cs = entry.get("covered_start")
    ce = entry.get("covered_end")
    end_s = end.isoformat()
    entry["covered_start"] = start.isoformat() if not cs else min(cs, start.isoformat())
    # Cap AFTER the max() so a future-dated claim left by an older
    # cache heals itself on the next successful fetch.
    entry["covered_end"]   = _cap_covered_end(end_s if not ce else max(ce, end_s))
    _record_settled_through(entry, symbol)
    entry["last_fetch"]    = now.isoformat(timespec="seconds")
    entry["failure_count"] = 0
    entry.pop("retry_after", None)
    entry.pop("last_error", None)
    entry.pop("tombstone", None)
    _meta_dirty = True


def _fetch_splits(symbol: str) -> list[list]:
    """Fetch split history for `symbol`.  Returns `[[ISO_date, ratio], ...]`.

    Ratio is `new_shares / old_shares` — so 2.0 for a 2:1 forward split,
    0.1 for a 1:10 reverse split.  Raises on API error; returns [] on
    no-data.
    """
    yf = _lazy_yf()
    if yf is None:
        raise RuntimeError("yfinance not installed — pip install yfinance")
    probe = symbol.rstrip("^")
    ticker = yf.Ticker(probe)
    splits = ticker.splits
    if splits is None or splits.empty:
        return []
    out: list[list] = []
    for idx, ratio in splits.items():
        try:
            dt_str = idx.date().isoformat()
        except AttributeError:
            dt_str = str(idx)[:10]
        out.append([dt_str, float(ratio)])
    return out


def _fetch_dividends(symbol: str) -> list[list]:
    """Fetch dividend history for `symbol`.  Returns
    ``[[ISO_ex_date, amount], ...]``.  Raises on API error; returns []
    on no-data."""
    yf = _lazy_yf()
    if yf is None:
        raise RuntimeError("yfinance not installed — pip install yfinance")
    ticker = yf.Ticker(symbol)
    divs = ticker.dividends
    if divs is None or divs.empty:
        return []
    out: list[list] = []
    for idx, amount in divs.items():
        try:
            dt_str = idx.date().isoformat()
        except AttributeError:
            dt_str = str(idx)[:10]
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            continue
        if math.isnan(amt) or amt <= 0:
            continue
        out.append([dt_str, amt])
    return out


def _refresh_dividends(symbol: str, *, verbose: bool = True) -> None:
    """Full-history dividend refresh for a total-return symbol.

    Non-fatal on fetch failure (keeps prior cached events — the factor
    table just stays slightly stale until the next successful refresh).
    """
    global _dividends_dirty
    try:
        new_divs = _fetch_dividends(symbol)
    except Exception:
        return
    divs_cache = _load_dividends()
    if divs_cache.get(symbol) != new_divs:
        divs_cache[symbol] = new_divs
        _dividends_dirty = True
        _tr_factor_cache.pop(symbol, None)
        if verbose:
            print(f"    {symbol}: {len(new_divs)} dividend event(s) cached")


def _prev_close(series: dict[str, float], ex_iso: str) -> float | None:
    """Close on the trading day strictly BEFORE `ex_iso` (walks back up
    to the usual lookback window)."""
    d = _parse_iso(ex_iso)
    for i in range(1, _LOOKBACK_DAYS + 2):
        probe = (d - timedelta(days=i)).isoformat()
        val = series.get(probe)
        if val is None:
            continue
        if isinstance(val, float) and math.isnan(val):
            continue
        return val
    return None


def _tr_factor_after(symbol: str, iso_date: str) -> float:
    """Total-return adjustment for `symbol` as of `iso_date`: the product
    of ``1 − div/prev_close`` over dividend ex-dates strictly AFTER the
    date.  The latest date's factor is 1.0 (adjusted == raw close), so
    the series is normalized exactly like yfinance's Adj Close and
    ratios between any two dates match it.

    Missing pieces degrade gracefully: no dividends cached → 1.0
    (price return); an ex-date whose prior close isn't in the price
    series (before coverage starts) is skipped — those factors only
    matter for dates we can't price anyway.
    """
    cached = _tr_factor_cache.get(symbol)
    if cached is None:
        series = _load_prices().get(symbol) or {}
        factors: list[tuple[str, float]] = []
        for ex_iso, amount in _load_dividends().get(symbol) or []:
            prev = _prev_close(series, ex_iso)
            if prev is None or prev <= 0:
                continue
            f = 1.0 - amount / prev
            if f <= 0:   # defensive — a dividend >= the share price
                continue
            factors.append((ex_iso, f))
        factors.sort()
        ex_dates = [d for d, _ in factors]
        suffix = [1.0] * (len(factors) + 1)
        for i in range(len(factors) - 1, -1, -1):
            suffix[i] = suffix[i + 1] * factors[i][1]
        cached = (ex_dates, suffix)
        _tr_factor_cache[symbol] = cached
    ex_dates, suffix = cached
    return suffix[bisect_right(ex_dates, iso_date)]


def fetch_latest_close_batch(symbols: list[str], *,
                              verbose: bool = True) -> int:
    """One-shot batched yfinance fetch for the most recent close.

    Used by ``--refresh-prices`` mode to update today's price for many
    symbols in a single HTTP roundtrip — ~3s for 150 symbols vs ~30s
    if we call ``Ticker.history`` one at a time.

    For each symbol, the cache is updated with the most recent date's
    close (could be today's intraday last-close during market hours, or
    the prior trading day if markets are closed / data hasn't posted).

    Skips ``_classify_no_fetch`` and tombstoned symbols.  Honors the
    proxy map (a mapped symbol contributes its proxy to the batch).
    Returns the number of symbols whose cache was updated.

    Failures are silent — a partial batch (yfinance returned no data
    for some tickers) just leaves those symbols' cached prices alone.
    The next full pipeline run will retry via ``ensure_coverage``.
    """
    global _meta_dirty
    yf = _lazy_yf()
    if yf is None:
        return 0

    # Expand proxies, dedupe, drop unfetchable
    targets: list[str] = []
    seen: set[str] = set()
    now = clock.now(fallback=datetime.now)
    for sym in symbols:
        if _classify_no_fetch(sym):
            continue
        entry = _proxy_entry(sym)
        if entry is not None and entry[1] == "direct":
            tgt = entry[0]
        else:
            tgt = sym
        if tgt in seen:
            continue
        if _should_skip(tgt, now):
            continue
        seen.add(tgt)
        targets.append(tgt)

    if not targets:
        return 0

    # yfinance batched download.  period="5d" gives us a few extra days
    # in case today's data hasn't posted yet (markets closed / pre-open
    # / mutual funds posting NAV in the evening).  We keep just the
    # most-recent date's close.
    try:
        df = yf.download(
            tickers=" ".join(targets),
            period="5d",
            auto_adjust=_AUTO_ADJUST,
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception as e:
        if verbose:
            print(f"  batched fetch failed: {e}")
        return 0
    if df is None or df.empty:
        return 0

    prices = _load_prices()
    updated = 0
    today_iso = now.date().isoformat()
    for sym in targets:
        # Single-symbol case: yfinance returns a flat DataFrame.
        # Multi-symbol case: top-level column is the ticker.
        try:
            if len(targets) == 1:
                sub = df
            else:
                sub = df[sym]
        except (KeyError, AttributeError):
            continue
        if sub is None or sub.empty:
            continue
        # Always Close (total-return adjustment happens at read time);
        # walk back to the most recent non-NaN row.
        col = "Close"
        if col not in sub.columns:
            continue
        series = sub[col].dropna()
        if series.empty:
            continue
        idx = series.index[-1]
        try:
            dt_str = idx.date().isoformat()
        except AttributeError:
            dt_str = str(idx)[:10]
        try:
            val = float(series.iloc[-1])
        except (TypeError, ValueError):
            continue
        prices.setdefault(sym, {})[dt_str] = val
        _mark_prices_dirty(sym)
        _tr_factor_cache.pop(sym, None)
        # Bump covered_end forward if we have a fresher date.  Capped at
        # the local date: crypto's UTC-dated bar can be tomorrow's, and
        # claiming coverage of tomorrow skips the symbol for a whole
        # local day (see _cap_covered_end).  The bar itself is kept.
        meta = _load_meta()
        entry = meta["symbols"].setdefault(sym, {})
        ce = entry.get("covered_end")
        if not ce or dt_str > ce:
            entry["covered_end"] = _cap_covered_end(dt_str)
        _record_settled_through(entry, sym)
        entry["last_fetch"] = now.isoformat(timespec="seconds")
        entry["failure_count"] = 0
        entry.pop("retry_after", None)
        entry.pop("last_error", None)
        entry.pop("tombstone", None)
        _meta_dirty = True
        updated += 1

    if verbose:
        print(f"  batched fetch: {updated}/{len(targets)} symbols refreshed "
              f"(latest dates: {today_iso} or earlier)")
    return updated


def split_factor_since(symbol: str, as_of_date) -> float:
    """Return the product of split ratios for splits strictly AFTER `as_of_date`.

    Use this to convert an as-of-date share count to today's-basis share
    count so it can be multiplied against yfinance's (split-adjusted)
    historical price::

        today_basis_qty = as_of_qty * split_factor_since(sym, date)
        value = today_basis_qty * price_on(date)   # price_on = yf Close

    Returns 1.0 for symbols with no splits (the common case).

    For direct-proxied symbols (e.g. BRK.B → BRK-B), forwards the lookup
    to the proxy so any split history applies to the user's holdings.
    Scaled proxies DON'T forward — the scaling already incorporates
    proxy splits into the return ratio, and the user's shares of the
    original fund aren't affected by the proxy fund's splits.
    """
    entry = _proxy_entry(symbol)
    if entry is not None and entry[1] == "direct":
        symbol = entry[0]
    splits = _load_splits().get(symbol)
    if not splits:
        return 1.0
    target = _parse_iso(as_of_date).isoformat()
    factor = 1.0
    for d, r in splits:
        if d > target:
            factor *= r
    return factor


def split_adjust_qty(symbol: str, qty: float, as_of_date) -> float:
    """Convenience: apply `split_factor_since` to `qty`."""
    return qty * split_factor_since(symbol, as_of_date)


# ---------------------------------------------------------------------------
# Option contracts — symbol parse + intrinsic-value floor
# ---------------------------------------------------------------------------

_OPTION_SYM_RE = re.compile(
    r"^(\S+)\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(Call|Put)\s+\$([\d,.]+)"
)


@lru_cache(maxsize=None)
def parse_option_symbol(sym: str) -> dict | None:
    """Parse ``"META 12/18/2026 Call $800.00"`` into components.

    Returns ``None`` if the symbol doesn't match the Robinhood format.
    Lives here (not analytics) so the pricing layer and both pipeline
    paths can use it; ``analytics.options._parse_option_symbol`` is an
    alias.  Cached — the distinct symbol set is small and the history
    walkers call this per (day × symbol).  Callers must treat the
    returned dict as read-only (it's the cache entry).
    """
    if not sym:
        return None
    m = _OPTION_SYM_RE.match(sym)
    if not m:
        return None
    under, mo, d, y, kind, strike_raw = m.groups()
    try:
        strike = float(strike_raw.replace(",", ""))
    except ValueError:
        return None
    return {
        "underlying": under,
        "expiry": f"{y}-{int(mo):02d}-{int(d):02d}",
        "type": kind,
        "strike": strike,
    }


def option_intrinsic(symbol: str, on_date) -> float | None:
    """Per-share intrinsic value of an option contract on ``on_date``,
    from the UNDERLYING's cached price: ``max(0, S − K)`` for calls,
    ``max(0, K − S)`` for puts.

    Used as a valuation FLOOR for open options: yfinance can't price
    the contracts themselves, so fin values them at their last-traded
    premium — which goes stale between trades and can wildly
    undervalue a deep-ITM contract whose underlying has since moved.
    Intrinsic tracks the underlying's cached close, so the floor stays
    current even when the option hasn't traded in months.  Time value
    is still not modeled (the floor only ever raises the price).

    Returns ``None`` when the symbol isn't a parsable option contract
    or the underlying has no cached price for the date.

    SPLIT BASIS: the cached close is split-adjusted to TODAY's share
    basis, but the strike is written in the AS-OF-TRADE basis — the
    comparison must happen in the strike's basis, so the price is
    scaled back by ``split_factor_since`` (actual as-traded price =
    adjusted × factor; 1.0 for dates with no later splits, i.e. every
    "today" lookup).  Without this, an underlying that later
    reverse-split values historical CALLS at phantom hundreds per
    share (adjusted price ≫ old-basis strike), and forward splits do
    the same to PUTS — the bug class that showed a $2.50 ACB call at
    $500k+ on a 2019 snapshot.
    """
    parsed = parse_option_symbol(symbol or "")
    if not parsed:
        return None
    s = get_price(parsed["underlying"], on_date)
    if s is None or s <= 0:
        return None
    s *= split_factor_since(parsed["underlying"], on_date)
    if parsed["type"] == "Call":
        return max(0.0, s - parsed["strike"])
    return max(0.0, parsed["strike"] - s)


def option_underlyings(symbols) -> set[str]:
    """Underlying tickers of any option-contract symbols in ``symbols``.
    main.py adds these to the fetch set so the intrinsic floor has an
    underlying price to read even when the user never held the
    underlying directly."""
    out: set[str] = set()
    for sym in symbols:
        p = parse_option_symbol(sym or "")
        if p:
            out.add(p["underlying"])
    return out


def price_source_symbol(symbol: str) -> str | None:
    """The ticker whose cache entry actually backs ``symbol``'s price.

    Proxied symbols resolve to their proxy; an option contract resolves
    to the underlying its intrinsic floor reads.  Returns ``None`` for
    anything the cache never fetches (cash, fund display names with no
    proxy, corp-action stubs) — those are priced from transaction
    history, so no fetch timestamp describes them.
    """
    entry = _proxy_entry(symbol)
    if entry is not None:
        symbol = entry[0]
    else:
        parsed = parse_option_symbol(symbol or "")
        if parsed:
            symbol = parsed["underlying"]
    if _classify_no_fetch(symbol):
        return None
    return symbol


def last_fetch_at(symbol: str) -> str | None:
    """ISO timestamp of the last successful fetch backing ``symbol``, or
    ``None`` when nothing in the cache does.

    This is the only accurate freshness signal fin has: ``covered_end``
    is a date, and a date says nothing about whether the bar behind it
    is this morning's stale open or the settled close.
    """
    target = price_source_symbol(symbol)
    if not target:
        return None
    return (_load_meta()["symbols"].get(target) or {}).get("last_fetch")


def any_provisional(symbols) -> bool:
    """True when any of ``symbols`` is currently marked from a bar that
    can still change — a mid-session price stored as if it were a close.

    A symbol with no ``settled_through`` at all is NOT flagged.  The
    fetch path treats that as unsettled because refetching is the safe
    error there; here the safe error is the opposite way.  Flagging
    every symbol we simply haven't fetched recently (a tombstoned
    ticker, one on backoff) would leave the caveat permanently lit and
    stop meaning anything — and ``oldest_last_fetch`` already describes
    that case more accurately.
    """
    meta = _load_meta()["symbols"]
    for sym in symbols:
        target = price_source_symbol(sym)
        if not target:
            continue
        entry = meta.get(target) or {}
        settled, covered = entry.get("settled_through"), entry.get("covered_end")
        if settled and covered and covered > settled:
            return True
    return False


def oldest_last_fetch(symbols) -> str | None:
    """The OLDEST ``last_fetch`` across ``symbols`` — a staleness floor.

    Deliberately oldest, not newest: one ticker refreshed a second ago
    says nothing about the fund whose NAV last landed yesterday, and the
    number the user is looking at is only as fresh as its stalest input.
    """
    stamps = [s for s in (last_fetch_at(sym) for sym in symbols) if s]
    return min(stamps) if stamps else None


def apply_option_intrinsic_floor(last_prices: dict[str, float],
                                 symbols, on_date) -> int:
    """Floor each option symbol's entry in ``last_prices`` at its
    intrinsic value on ``on_date``.  Only ever raises a price (an OTM
    intrinsic of 0 never overwrites a real premium, and never creates
    a 0 entry).  Returns the number of symbols floored.  Shared by
    main() and the refresh path — the last_prices build is duplicated
    across both."""
    n = 0
    for sym in symbols:
        iv = option_intrinsic(sym, on_date)
        if iv is None:
            continue
        if iv > (last_prices.get(sym) or 0):
            last_prices[sym] = iv
            n += 1
    return n


def _fetch_range(symbol: str, start: date, end: date) -> dict[str, float]:
    """One-shot yfinance fetch for [start, end] inclusive.

    Returns {YYYY-MM-DD: close}.  Raises on API error; returns {} on empty.
    """
    yf = _lazy_yf()
    if yf is None:
        raise RuntimeError("yfinance not installed — pip install yfinance")
    probe = symbol.rstrip("^")  # Robinhood delisted marker
    ticker = yf.Ticker(probe)
    hist = ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
        auto_adjust=_AUTO_ADJUST,
    )
    if hist is None or hist.empty:
        return {}
    # Always raw "Close" (split-adjusted only) — total-return symbols
    # get their dividend adjustment applied at read time, never stored.
    col = "Close"
    out: dict[str, float] = {}
    for idx, row in hist.iterrows():
        try:
            dt_str = idx.date().isoformat()
        except AttributeError:
            dt_str = str(idx)[:10]
        val = row.get(col)
        if val is None:
            continue
        # yfinance emits NaN for rows where the close hasn't posted yet
        # (e.g. the most-recent day during/just after market hours, or a
        # holiday row).  float(NaN) is still NaN and would poison the
        # cache — and from there every value/unrealized figure that
        # multiplies by this price.  Skip it; the backward walk in
        # get_price falls through to the prior real close.
        fval = float(val)
        if math.isnan(fval):
            continue
        out[dt_str] = fval
    return out


def _batch_fetch_ranges(symbols: list[str], start: date, end: date) -> dict | None:
    """One batched ``yf.download`` over [start, end] for many symbols.

    Returns ``{sym: {"prices": {ISO: close}, "split_event": bool|None}}``
    containing ONLY symbols that returned at least one usable row.
    ``split_event`` is True when a split posted inside the fetched window
    (caller must do a full splits refresh + invalidation), False when the
    window provably had none (the download's "Stock Splits" column was
    all zero), None when the column wasn't available (caller treats as
    unknown → full refresh, same as the serial path).

    Returns ``None`` when batching is unavailable (yfinance missing) or
    the download failed as a whole — the caller falls back to the
    per-symbol serial path, which owns retry/failure bookkeeping.
    """
    yf = _lazy_yf()
    if yf is None:
        return None
    try:
        df = yf.download(
            tickers=" ".join(symbols),
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yf end is exclusive
            auto_adjust=_AUTO_ADJUST,
            actions=True,            # adds Dividends + Stock Splits columns
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception:
        return None
    if df is None or df.empty:
        # Download worked but no rows at all (weekend / holiday range).
        return {}
    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            sub = df[sym] if len(symbols) > 1 else df
        except (KeyError, AttributeError):
            continue
        if sub is None or sub.empty:
            continue
        # Always Close — total-return adjustment happens at read time.
        col = "Close"
        if col not in sub.columns:
            continue
        data: dict[str, float] = {}
        for idx, val in sub[col].items():
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(fval):
                continue   # close not posted yet — same rule as _fetch_range
            try:
                dt_str = idx.date().isoformat()
            except AttributeError:
                dt_str = str(idx)[:10]
            data[dt_str] = fval
        if not data:
            continue
        if "Stock Splits" in sub.columns:
            try:
                split_event = bool((sub["Stock Splits"].fillna(0) != 0).any())
            except Exception:
                split_event = None
        else:
            split_event = None
        out[sym] = {"prices": data, "split_event": split_event}
    return out


def _apply_fetch_success(sym: str, merged: dict[str, float],
                         actual_start: date, actual_end: date,
                         now: datetime, *, verbose: bool,
                         splits_known_clean: bool = False) -> None:
    """Merge fetched prices into the cache and update all bookkeeping.

    Shared by the serial and batched paths of ``ensure_coverage``.
    ``splits_known_clean=True`` means the fetched window provably had no
    split event (batched downloads carry a Stock Splits column), so the
    per-symbol full splits refetch is skipped when a splits entry already
    exists.  New splits always coincide with new trading days, so the
    window check is sufficient for catching them at fetch time;
    historical split REVISIONS are caught by the weekly deep refresh
    either way (``revalidate_stale_caches``).
    """
    global _meta_dirty, _splits_dirty
    prices = _load_prices()
    prices.setdefault(sym, {}).update(merged)
    _mark_prices_dirty(sym)
    _tr_factor_cache.pop(sym, None)
    _record_success(sym, actual_start, actual_end, now)
    # Total-return symbols: refresh dividend events alongside the price
    # fetch.  New ex-dates always fall on new trading days, so extending
    # price coverage is exactly when a new event can appear; the weekly
    # deep refresh backstops revisions.
    if _is_total_return_symbol(sym):
        _refresh_dividends(sym, verbose=verbose)
    # Refresh splits alongside the price fetch — new splits always
    # coincide with new trading days, so whenever we extend price
    # coverage we may have a new split to record.
    splits_cache = _load_splits()
    if not (splits_known_clean and sym in splits_cache):
        try:
            new_splits = _fetch_splits(sym)
            old_splits = splits_cache.get(sym)
            if old_splits != new_splits:
                splits_cache[sym] = new_splits
                _splits_dirty = True
                # Split history changed under an existing cache: the
                # previously-cached ranges are in the pre-split basis
                # (only the gap we just fetched is post-split).  Wipe
                # the symbol so the next run refetches the whole range
                # in one consistent basis; until then get_price
                # returns None and consumers fall back / report via
                # priced_pct rather than being wrong by the ratio.
                _invalidate_prices_for_split_change(
                    sym, old_splits, new_splits, verbose=verbose)
        except Exception:
            # Splits fetch failures are non-fatal — keep prior cached
            # splits (if any) and carry on.
            pass
    if verbose:
        sp = _load_splits().get(sym, [])
        sp_note = f", {len(sp)} split(s)" if sp else ""
        print(f"    {sym}: +{len(merged)} days{sp_note}")


def _last_trading_day(d: date) -> date:
    """Walk ``d`` back to the most recent weekday.  US market holidays
    aren't handled — the cost of a one-off failed fetch on a holiday
    is the same "no data short range" no-op we already tolerate, so
    a full market calendar isn't worth the dependency."""
    while d.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
        d -= timedelta(days=1)
    return d


def _missing_ranges(symbol: str, start: date, end: date) -> list[tuple[date, date]]:
    """Return the date ranges needed to extend coverage to [start, end].

    The ``end`` date is clamped back to the most recent weekday — asking
    yfinance for weekend data produces no results (markets closed) but
    every extra request adds noise to the console and a small network
    cost.  Mutual funds publish their NAV ~1-2 hours after Friday's
    close, so by the time we run over a weekend, Friday's price is the
    correct "latest close" anyway.

    **A date is only covered once its bar can no longer change.**
    ``covered_end`` records what we ASKED for, not what has settled, so
    on its own it froze prices at whatever the first run of the day
    captured — and a bar fetched mid-session is a live mark stored as if
    it were the close.  Effective coverage is therefore
    ``min(covered_end, settled_through)``: a symbol whose newest bar is
    still live yields a gap and gets refetched, and one that is fully
    settled yields nothing at all, so an evening run does no network
    work.  Historical requests are unaffected — an ``end`` in the past
    compares against the same range as before.

    A missing ``settled_through`` (any cache written before settle
    awareness landed) falls back to "today is never settled", which is
    the prior behaviour — so old caches keep working with no migration.
    """
    end = _last_trading_day(end)
    meta = _load_meta()
    entry = meta["symbols"].get(symbol)
    if not entry or not entry.get("covered_start"):
        return [(start, end)] if start <= end else []
    cs = _parse_iso(entry["covered_start"])
    settled = entry.get("settled_through")
    horizon = (_parse_iso(settled) if settled
               else _today() - timedelta(days=1))
    ce = min(_parse_iso(entry["covered_end"]), horizon)
    gaps: list[tuple[date, date]] = []
    if start < cs:
        gaps.append((start, cs - timedelta(days=1)))
    if end > ce:
        gaps.append((ce + timedelta(days=1), end))
    return gaps


def _invalidate_prices_for_split_change(symbol: str, old_splits, new_splits,
                                        *, verbose: bool = True) -> bool:
    """Drop a symbol's cached prices + coverage meta when its split
    history CHANGES (a new split appeared, or a recorded one was revised).

    yfinance rescales the ENTIRE historical Close series when a split
    happens, so every previously-cached value for the symbol is still in
    the old (pre-split) basis.  Mixing those with post-split fetches —
    while ``split_factor_since`` assumes a uniform today-basis series —
    silently mis-values every pre-split snapshot by the split ratio.
    Wiping the cache forces a clean refetch in the new adjusted basis
    (this run if the caller fetches afterwards, else the next run; the
    ``priced_pct`` canary reflects the gap honestly in the meantime).

    Only fires on a *change* from a previously-recorded history
    (``old_splits is not None`` and differs).  First-time backfills don't
    invalidate — the cached prices were fetched with those historical
    splits already baked in.
    """
    if old_splits is None or old_splits == new_splits:
        return False
    global _meta_dirty
    prices = _load_prices()
    meta = _load_meta()
    if prices.pop(symbol, None) is not None:
        _mark_prices_deleted(symbol)
    _tr_factor_cache.pop(symbol, None)
    entry = meta.get("symbols", {}).get(symbol)
    if entry:
        entry.pop("covered_start", None)
        entry.pop("covered_end", None)
        entry.pop("settled_through", None)
        _meta_dirty = True
    if verbose:
        print(f"    {symbol}: split history changed — cached prices "
              f"invalidated; will refetch in the new adjusted basis")
    return True


def revalidate_stale_caches(symbols: list[str], *,
                             verbose: bool = True,
                             force: bool = False) -> None:
    """Periodic deep refresh of cached data that the daily-fetch path
    doesn't naturally re-validate:

    1. **Splits cache** — `_fetch_splits` is normally only re-run
       alongside a price-cache extension, so a symbol whose prices are
       already covered through today won't get its splits re-checked.
       If a stock splits AFTER you've run the pipeline, history-walker
       balance scaling silently breaks.  Re-pulling splits weekly for
       all held symbols closes that window.

    2. **Tombstoned symbols** — after 5 consecutive failed fetches we
       stop trying; that's correct for delisted tickers but wrong for
       (rare) re-listings.  Clearing the tombstone every
       ``_DEEP_REFRESH_DAYS`` lets the symbol have another shot at a
       successful fetch.

    Called from ``main.py`` before ``ensure_coverage`` so any newly-
    discovered split is in the splits cache before the history walker
    needs it.  The check is throttled by a top-level
    ``last_deep_refresh`` field in the meta file — daily runs skip the
    work; weekly runs do it once.

    Pass ``force=True`` to bypass the throttle (e.g. for an explicit
    ``--refresh-caches`` CLI flag — not implemented yet but trivial).
    """
    global _meta_dirty, _splits_dirty
    meta = _load_meta()
    last = meta.get("last_deep_refresh")
    now = clock.now(fallback=datetime.now)
    if not force and last:
        try:
            age_days = (now.date() - _parse_iso(last)).days
            if age_days < _DEEP_REFRESH_DAYS:
                return   # not stale yet
        except (ValueError, TypeError):
            pass   # corrupted timestamp → treat as stale

    # 1. Refresh splits for every non-tombstoned, fetchable symbol.
    splits_cache = _load_splits()
    splits_changes = 0
    skipped = 0
    for sym in symbols:
        if _classify_no_fetch(sym):
            skipped += 1
            continue
        # Resolve proxies — the splits live under the proxy ticker
        entry = _proxy_entry(sym)
        target = entry[0] if (entry is not None and entry[1] == "direct") else sym
        try:
            new_splits = _fetch_splits(target)
        except Exception:
            continue   # network blip; next run will retry
        old_splits = splits_cache.get(target)
        if old_splits != new_splits:
            splits_cache[target] = new_splits
            _splits_dirty = True
            splits_changes += 1
            # A changed split history means every cached price for the
            # symbol is in the old basis — wipe so ensure_coverage
            # (which main.py calls right after this) refetches cleanly.
            _invalidate_prices_for_split_change(target, old_splits,
                                                new_splits, verbose=verbose)

    # 1b. Refresh dividend events for total-return symbols — the
    #     fetch-time refresh only fires when price coverage extends, so
    #     a revised historical dividend (rare, but ex-date corrections
    #     happen) would otherwise never be picked up.
    for sym in symbols:
        if _classify_no_fetch(sym):
            continue
        entry = _proxy_entry(sym)
        target = entry[0] if (entry is not None and entry[1] in ("direct", "scaled")) else sym
        if _is_total_return_symbol(target):
            _refresh_dividends(target, verbose=verbose)

    # 2. Clear tombstones so previously-given-up symbols get another
    #    chance on the next ensure_coverage call.  We don't fetch
    #    them now (that's ensure_coverage's job); we just unblock.
    untombstoned = 0
    for sym, entry in meta.get("symbols", {}).items():
        if entry.get("tombstone"):
            entry.pop("tombstone", None)
            entry.pop("retry_after", None)
            entry["failure_count"] = 0
            _meta_dirty = True
            untombstoned += 1

    meta["last_deep_refresh"] = now.date().isoformat()
    _meta_dirty = True

    if verbose and (splits_changes or untombstoned):
        bits = []
        if splits_changes:
            bits.append(f"{splits_changes} split histor{'y' if splits_changes == 1 else 'ies'} updated")
        if untombstoned:
            bits.append(f"{untombstoned} tombstone(s) cleared")
        print(f"  Deep cache refresh: {', '.join(bits)}")


def ensure_coverage(symbols: list[str], start, end, *,
                    verbose: bool = True,
                    symbol_end_overrides: dict[str, str] | None = None,
                    force_today_for: set[str] | None = None) -> None:
    """Fetch any missing [start, end] coverage for the given symbols.

    Respects per-symbol backoff so previously-failed tickers don't keep
    hitting the network every run.

    Proxied symbols (see ``symbol_proxy_map.json``) are transparently
    expanded to their proxies — we fetch the proxy, then get_price
    routes lookups through the mapping.

    ``symbol_end_overrides``: optional ``{symbol: ISO-date}`` map.  When
    a symbol appears here, its fetch range ends at the override date
    rather than the global ``end``.  Used by main.py to clamp closed-
    out positions (RNAM, LUNA-USD) to their last-held date — we still
    want their historical prices for past snapshots, but stop asking
    yfinance for "today" on a position you no longer hold.

    ``force_today_for``: optional set of original (pre-proxy-expansion)
    symbol names to force-refresh the latest trading day for, even if
    the cache claims it is already covered.  ``main.py`` passes held +
    benchmark + option-underlying symbols — passing every symbol-ever
    would re-fetch hundreds of closed positions for nothing.  This is
    the layer that covers what ``_missing_ranges``' "today is never
    covered" clamp cannot: over a weekend or holiday the requested end
    walks back to the last trading day, which IS settled-looking, so
    only an explicit force re-pulls (e.g. a mutual-fund NAV that hadn't
    posted when Friday evening's run went out).  Doesn't bypass backoff
    or no-fetch rules — a tombstoned symbol still won't be hit.
    """
    force_today_set: set[str] = set(force_today_for or set())
    global _splits_dirty, _meta_dirty
    prices = _load_prices()
    start_d = _parse_iso(start)
    end_d   = _parse_iso(end)
    now     = clock.now(fallback=datetime.now)
    overrides = symbol_end_overrides or {}

    # Expand proxied symbols to their proxies, dedupe.  A mapped symbol
    # contributes its proxy to the fetch list and is itself skipped —
    # its own ticker is unfetchable by design.
    # ``force_today_for_targets``: post-expansion set, used inside the
    # gap loop below.  An original-name match (e.g. "BRK.B" in
    # ``force_today_set``) propagates to the proxy ("BRK-B").
    proxy_hits = 0
    expanded: list[str] = []
    seen: set[str] = set()
    force_today_for_targets: set[str] = set()
    for sym in symbols:
        entry = _proxy_entry(sym)
        if entry is not None:
            proxy_hits += 1
            tgt = entry[0]
        else:
            tgt = sym
        if sym in force_today_set or tgt in force_today_set:
            force_today_for_targets.add(tgt)
        if tgt not in seen:
            seen.add(tgt)
            expanded.append(tgt)
    symbols = expanded

    to_fetch: list[tuple[str, list[tuple[date, date]]]] = []
    splits_to_backfill: list[str] = []
    already_cached = 0
    on_backoff = 0
    not_fetchable = 0
    splits_cache = _load_splits()
    for sym in symbols:
        if _classify_no_fetch(sym):
            not_fetchable += 1
            continue
        if _should_skip(sym, now):
            on_backoff += 1
            continue
        # Total-return symbols follow the same incremental gap path as
        # everything else — the store is plain Close and the dividend
        # adjustment happens at read time, so nothing re-normalizes.
        # (_apply_fetch_success refreshes their dividend events whenever
        # coverage extends.)
        # Use the per-symbol end override (closed-position clamp) if
        # set, else the global end.  Note: the override applies to the
        # POST-proxy-expansion symbol; if the user has a proxy
        # (foo→VFIAX) on a closed position, the override key is "foo"
        # not VFIAX, so it doesn't accidentally truncate VFIAX's
        # coverage when other consumers still need today's price.
        sym_end = overrides.get(sym)
        if sym_end:
            try:
                effective_end = _parse_iso(sym_end)
            except (ValueError, TypeError):
                effective_end = end_d
        else:
            effective_end = end_d
        gaps = _missing_ranges(sym, start_d, effective_end)
        # force_today_for: also append a 1-day fetch for the last
        # trading day, but ONLY for symbols the caller asked to refresh
        # (held + benchmarks in --refresh-prices mode).  Closed
        # positions and historical-only symbols are deliberately not
        # re-fetched today — re-pulling a delisted ticker just produces
        # network noise.
        if (sym in force_today_for_targets
                and effective_end >= end_d):
            today_d = _last_trading_day(end_d)
            already_in_gaps = any(g[0] <= today_d <= g[1] for g in gaps)
            if not already_in_gaps:
                gaps = list(gaps) + [(today_d, today_d)]
        if gaps:
            to_fetch.append((sym, gaps))
        else:
            already_cached += 1
            # Fully covered by the price cache, but splits cache might be
            # stale / missing (e.g. for cache entries from before the splits
            # feature landed).  Backfill on first encounter.
            if sym not in splits_cache:
                splits_to_backfill.append(sym)

    if verbose:
        parts = []
        if already_cached:
            parts.append(f"{already_cached} already cached")
        if on_backoff:
            parts.append(f"{on_backoff} on backoff")
        if not_fetchable:
            parts.append(f"{not_fetchable} not fetchable")
        if proxy_hits:
            parts.append(f"{proxy_hits} via proxy map")
        suffix = f"  ({', '.join(parts)})" if parts else ""
        if to_fetch:
            print(f"  Fetching price history for {len(to_fetch)} symbol(s){suffix}")
        elif parts:
            print(f"  Price cache up to date{suffix}")

    fetched_ok = 0
    fetched_fail = 0

    # Group symbols by identical gap signature.  On a typical daily run
    # every held symbol shares the same short "since last run" gap, so a
    # single batched download serves the whole group (one HTTP roundtrip
    # vs one per symbol).  Groups of one keep the per-symbol path, and
    # any symbol the batch can't serve falls back to it too — the serial
    # path owns retry/failure bookkeeping.
    by_gaps: dict[tuple, list[str]] = {}
    for sym, gaps in to_fetch:
        by_gaps.setdefault(tuple(gaps), []).append(sym)

    serial_fetch: list[tuple[str, list[tuple[date, date]]]] = []
    for gaps_key, group in sorted(by_gaps.items()):
        gaps = list(gaps_key)
        if len(group) < 2:
            serial_fetch.extend((s, gaps) for s in group)
            continue
        actual_start = min(g[0] for g in gaps)
        actual_end   = max(g[1] for g in gaps)
        range_days   = (actual_end - actual_start).days
        results: dict[str, dict] | None = {}
        for gs, ge in gaps:
            part = _batch_fetch_ranges(group, gs, ge)
            if part is None:
                results = None
                break
            for sym, res in part.items():
                agg = results.setdefault(sym, {"prices": {}, "split_event": False})
                agg["prices"].update(res["prices"])
                se, agg_se = res["split_event"], agg["split_event"]
                # True dominates (split seen); None (unknown) dominates False.
                agg["split_event"] = (True if (se is True or agg_se is True)
                                      else None if (se is None or agg_se is None)
                                      else False)
        if results is None:
            serial_fetch.extend((s, gaps) for s in group)
            continue
        for sym in group:
            res = results.get(sym)
            if res:
                _apply_fetch_success(
                    sym, res["prices"], actual_start, actual_end, now,
                    verbose=verbose,
                    splits_known_clean=(res["split_event"] is False))
                fetched_ok += 1
            elif range_days >= _NO_DATA_MIN_RANGE_DAYS:
                # No rows for a long range could be a bad ticker or a
                # batch artifact — let the serial path decide (and record
                # any failure with an accurate error message).
                serial_fetch.append((sym, gaps))
            else:
                # Short range, no rows — weekend / holiday / mutual-fund
                # NAV not yet posted.  Same free pass as the serial path.
                if verbose:
                    print(f"    {sym}: no data (short range, not penalized)")

    for sym, gaps in serial_fetch:
        actual_start = min(g[0] for g in gaps)
        actual_end   = max(g[1] for g in gaps)
        range_days   = (actual_end - actual_start).days
        merged: dict[str, float] = {}
        error: str | None = None
        try:
            for gs, ge in gaps:
                data = _fetch_range(sym, gs, ge)
                merged.update(data)
        except Exception as e:  # network, parse, auth — treat all as fetch failure
            error = str(e)[:200]

        if error is not None:
            _record_failure(sym, error, now)
            fetched_fail += 1
            if verbose:
                print(f"    {sym}: fetch error — {error}")
        elif merged:
            _apply_fetch_success(sym, merged, actual_start, actual_end, now,
                                 verbose=verbose)
            fetched_ok += 1
        elif range_days >= _NO_DATA_MIN_RANGE_DAYS:
            _record_failure(sym, "no data returned", now)
            fetched_fail += 1
            if verbose:
                print(f"    {sym}: no data returned (range {range_days}d)")
        else:
            # Small range with no data — likely a weekend / holiday, or
            # a mutual fund whose EOD NAV hasn't posted yet (mutual
            # funds publish once per day, typically ~1-2 hours after
            # market close).  Not a real failure; next run picks it up.
            if verbose:
                today_iso = now.date().isoformat()
                hint = ""
                if actual_end.isoformat() == today_iso:
                    hint = " — likely today's close / mutual-fund NAV not yet posted"
                else:
                    hint = " — likely weekend/holiday"
                print(f"    {sym}: no data (short range, not penalized){hint}")

    if verbose and (fetched_ok or fetched_fail):
        print(f"  Fetch summary: {fetched_ok} ok, {fetched_fail} failed")

    # One-time splits backfill for symbols whose prices are already cached
    # but whose split history was never fetched.
    if splits_to_backfill:
        if verbose:
            print(f"  Backfilling splits for {len(splits_to_backfill)} symbol(s)")
        backfilled_with_splits = 0
        for sym in splits_to_backfill:
            try:
                new_splits = _fetch_splits(sym)
            except Exception:
                new_splits = []
            splits_cache[sym] = new_splits
            _splits_dirty = True
            if new_splits:
                backfilled_with_splits += 1
        if verbose and backfilled_with_splits:
            print(f"  Backfill complete: {backfilled_with_splits} of "
                  f"{len(splits_to_backfill)} had split events")
