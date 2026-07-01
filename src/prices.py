"""Historical price cache with yfinance fallback.

Two-file cache design:

- `cache/price_cache.json`      — `{symbol: {YYYY-MM-DD: close_price}}`
- `cache/price_cache_meta.json` — per-symbol fetch state (covered range,
                                   last fetch time, failure count, retry-after,
                                   tombstone).

Prices in the cache are yfinance's `Close` column (split-adjusted close
on each trading day).  Note: yfinance's Close is *always* split-adjusted
regardless of `auto_adjust`; `auto_adjust=False` just avoids the extra
dividend adjustment.  We use `auto_adjust=False` so the stored price is
the split-adjusted market close (matches what a chart would show), not a
dividend-reinvestment-adjusted total-return number.

**Benchmark exception.**  Symbols listed in `_TOTAL_RETURN_SYMBOLS` (only
SPY as of now) are fetched as `Adj Close` — split AND dividend adjusted —
so the benchmark reflects total return including reinvested dividends.
Our portfolio positions capture reinvested dividends via the txn ledger
(Dividend → Reinvest → higher share count), and the benchmark is a
hypothetical with no ledger, so it needs dividend adjustment built in
for an apples-to-apples comparison.  Because yfinance re-normalizes the
whole Adj Close series every time a new dividend is announced, we
force a full-range refetch for these symbols on every pipeline run.

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

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import CACHE_DIR, CRYPTO_SYMBOLS, SYMBOL_MAP

PRICE_CACHE_FILE  = CACHE_DIR / "price_cache.json"
PRICE_META_FILE   = CACHE_DIR / "price_cache_meta.json"
SPLITS_CACHE_FILE = CACHE_DIR / "splits_cache.json"
PROXY_MAP_FILE    = CACHE_DIR / "symbol_proxy_map.json"

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

# Controls yfinance's `auto_adjust`.  MUST be False; see module docstring
# for the reasoning.  If this changes, the on-disk cache is invalidated on
# next load because the values would be incompatible with existing entries.
_AUTO_ADJUST = False

# Symbols to fetch as TOTAL RETURN (split + dividend adjusted).  Used for
# benchmark comparisons where we don't have a txn ledger to handle
# dividends (the benchmark is a hypothetical, not a real position) —
# plus any symbol used as a SCALED proxy, since scaled proxies exist
# to value funds we can't see directly (CITs, private 401K funds) and
# those invariably reinvest distributions internally into NAV.  See
# _is_total_return_symbol() for the dynamic lookup.
#
# Everything NOT treated as total-return is fetched as price-only
# (Close) — dividends on real positions are tracked via the txn
# ledger (Dividend → Reinvest → more shares).
#
# NOTE: yfinance's Adj Close is normalized to the most recent close, so
# every historical value shifts slightly whenever a new dividend is
# announced.  To keep the stored series internally consistent, we
# force a full-range refetch for these symbols on every run (see
# ensure_coverage).
_TOTAL_RETURN_SYMBOLS = frozenset({"SPY", "BND", "VXUS"})

# How often (in days) to do a full re-fetch of total-return series.
# Adj Close re-normalizes whenever a new dividend is announced; for
# quarterly payers like SPY, weekly catches every dividend within a
# few days of the ex-date.  Between full refreshes, the normal
# missing-gap fetch path appends only the new days.
_TOTAL_RETURN_REFRESH_DAYS = 7

# How often (in days) to deep-refresh things that don't get caught
# by the incremental daily fetch path:
#   - Splits on symbols whose price cache is already covered through
#     today (the existing splits-on-fetch-extension hook misses them)
#   - Tombstoned symbols — give a delisted-or-broken ticker another
#     chance every quarter or so in case it was relisted / fixed
_DEEP_REFRESH_DAYS = 7

# In-memory state.
_prices: dict[str, dict[str, float]] | None    = None
_meta:   dict | None                            = None
_splits: dict[str, list[list]] | None           = None
_proxy:  dict | None                            = None
_prices_dirty = False
_meta_dirty   = False
_splits_dirty = False
_proxy_dirty  = False


def reset_caches() -> None:
    """Clear in-memory cache state so the next access re-reads from
    disk.  Used by tests that run the pipeline in isolated tmp dirs —
    without this, state leaks between tests.  Production code never
    needs to call this; the caches are loaded once per process.
    """
    global _prices, _meta, _splits, _proxy
    global _prices_dirty, _meta_dirty, _splits_dirty, _proxy_dirty
    _prices = None
    _meta = None
    _splits = None
    _proxy = None
    _prices_dirty = False
    _meta_dirty = False
    _splits_dirty = False
    _proxy_dirty = False

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

def _load_prices() -> dict[str, dict[str, float]]:
    global _prices
    if _prices is None:
        if PRICE_CACHE_FILE.exists():
            with open(PRICE_CACHE_FILE, "r", encoding="utf-8") as f:
                _prices = json.load(f)
        else:
            _prices = {}
        _migrate_legacy_keys()
        _invalidate_if_policy_changed()
    return _prices


def _load_meta() -> dict:
    global _meta
    if _meta is None:
        if PRICE_META_FILE.exists():
            with open(PRICE_META_FILE, "r", encoding="utf-8") as f:
                _meta = json.load(f)
        else:
            _meta = {"version": 1, "auto_adjusted": _AUTO_ADJUST, "symbols": {}}
        _meta.setdefault("symbols", {})
    return _meta


def _invalidate_if_policy_changed() -> None:
    """If the cache on disk was built with a different `auto_adjust` setting,
    wipe price data and reset per-symbol coverage so everything gets
    refetched with the current policy.  Keeps the failure / backoff state
    (if a ticker was bad before, it's likely still bad).
    """
    global _prices_dirty, _meta_dirty
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
    for _sym, entry in meta["symbols"].items():
        entry.pop("covered_start", None)
        entry.pop("covered_end", None)
    meta["auto_adjusted"] = _AUTO_ADJUST
    _prices_dirty = True
    _meta_dirty = True


def _load_splits() -> dict[str, list[list]]:
    global _splits
    if _splits is None:
        if SPLITS_CACHE_FILE.exists():
            with open(SPLITS_CACHE_FILE, "r", encoding="utf-8") as f:
                _splits = json.load(f)
        else:
            _splits = {}
    return _splits


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


def save_caches() -> None:
    """Write all four caches back to disk (only if dirty)."""
    global _prices_dirty, _meta_dirty, _splits_dirty, _proxy_dirty
    if _prices_dirty and _prices is not None:
        PRICE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PRICE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_prices, f, indent=2, sort_keys=True, ensure_ascii=False)
        _prices_dirty = False
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
    """Auto-populate ``anchor_date`` + ``anchor_price`` for any proxy
    map entries using ``method: "scaled"`` that don't have them yet.

    The anchor is the first observed non-zero transaction price for
    the mapped symbol — fin's ground truth for what that share was
    actually worth when the user held it.  From there, the proxy's
    return stream drives valuations.
    """
    global _proxy, _proxy_dirty
    pm = _load_proxy_map()
    # Find first observed txn price per symbol
    first_price: dict[str, tuple[str, float]] = {}
    for t in txns:
        sym = t.get("symbol", "")
        if not sym or sym not in pm:
            continue
        entry = pm[sym]
        if not isinstance(entry, dict) or entry.get("method") != "scaled":
            continue
        if entry.get("anchor_price") and entry.get("anchor_date"):
            continue   # already anchored
        price = float(t.get("price", 0) or 0)
        if price <= 0:
            continue
        d = t.get("date", "")
        if not d:
            continue
        prev = first_price.get(sym)
        if prev is None or d < prev[0]:
            first_price[sym] = (d, price)

    if not first_price:
        return
    for sym, (d, p) in first_price.items():
        pm[sym]["anchor_date"]  = d
        pm[sym]["anchor_price"] = round(p, 4)
        _proxy_dirty = True
        if verbose:
            print(f"  proxy anchor: {sym} -> {pm[sym]['proxy']} "
                  f"(anchor {d} @ ${p:.2f})")


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
    global _prices_dirty, _meta_dirty
    meta = _load_meta()
    if meta.get("migrated_v1"):
        return

    def _merge(src_key: str, dst_key: str) -> None:
        global _prices_dirty
        if src_key == dst_key or src_key not in _prices:
            return
        src = _prices.pop(src_key)
        _prices_dirty = True
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
            # falls back to the last_txn_price.
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
            # Defensive: an older cache (written before _fetch_range
            # filtered NaN) may still hold a NaN entry.  Treat it as a
            # gap and keep walking back to a real prior close instead of
            # propagating NaN into value/unrealized everywhere.
            if isinstance(val, float) and math.isnan(val):
                continue
            return val
    return None


def get_series(symbol: str, start, end) -> dict[str, float]:
    """Return {date: price} entries within [start, end] (inclusive)."""
    prices = _load_prices()
    series = prices.get(symbol, {})
    start_s = _parse_iso(start).isoformat()
    end_s   = _parse_iso(end).isoformat()
    return {d: p for d, p in series.items() if start_s <= d <= end_s}


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


def _record_success(symbol: str, start: date, end: date, now: datetime) -> None:
    global _meta_dirty
    meta = _load_meta()
    entry = meta["symbols"].setdefault(symbol, {})
    cs = entry.get("covered_start")
    ce = entry.get("covered_end")
    entry["covered_start"] = start.isoformat() if not cs else min(cs, start.isoformat())
    entry["covered_end"]   = end.isoformat()   if not ce else max(ce, end.isoformat())
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
    global _prices_dirty, _meta_dirty
    yf = _lazy_yf()
    if yf is None:
        return 0

    # Expand proxies, dedupe, drop unfetchable
    targets: list[str] = []
    seen: set[str] = set()
    now = datetime.now()
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
        # Pick the column matching the symbol's price-flavor (Adj Close
        # for total-return tickers, Close otherwise) and walk back to
        # the most recent non-NaN row.
        col = "Adj Close" if _is_total_return_symbol(sym) else "Close"
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
        _prices_dirty = True
        # Bump covered_end forward if we have a fresher date
        meta = _load_meta()
        entry = meta["symbols"].setdefault(sym, {})
        ce = entry.get("covered_end")
        if not ce or dt_str > ce:
            entry["covered_end"] = dt_str
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
    # Total-return benchmarks (and scaled-proxy targets) read "Adj Close"
    # (split+dividend-adjusted); everything else reads raw "Close"
    # (split-adjusted only).  See _is_total_return_symbol for why.
    col = "Adj Close" if _is_total_return_symbol(symbol) else "Close"
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
    """
    end = _last_trading_day(end)
    meta = _load_meta()
    entry = meta["symbols"].get(symbol)
    if not entry or not entry.get("covered_start"):
        return [(start, end)] if start <= end else []
    cs = _parse_iso(entry["covered_start"])
    ce = _parse_iso(entry["covered_end"])
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
    global _prices_dirty, _meta_dirty
    prices = _load_prices()
    meta = _load_meta()
    if prices.pop(symbol, None) is not None:
        _prices_dirty = True
    entry = meta.get("symbols", {}).get(symbol)
    if entry:
        entry.pop("covered_start", None)
        entry.pop("covered_end", None)
        entry.pop("last_full_refresh", None)
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
    now = datetime.now()
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
    symbol names to force-refresh today's price for, even if the cache
    claims today is already covered.  Used by ``--refresh-prices`` mode
    on held + benchmark symbols only — passing every symbol-ever would
    re-fetch hundreds of closed positions for nothing.  Doesn't bypass
    backoff or no-fetch rules — a tombstoned symbol still won't be hit.
    """
    force_today_set: set[str] = set(force_today_for or set())
    global _prices_dirty, _splits_dirty, _meta_dirty
    prices = _load_prices()
    start_d = _parse_iso(start)
    end_d   = _parse_iso(end)
    now     = datetime.now()
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
        # Total-return symbols: Adj Close values re-normalize every
        # time a new dividend is announced, so the entire series has
        # to be refetched together to stay internally consistent.
        # SPY pays quarterly, so a weekly cadence catches every
        # dividend within a few days of the ex-date — meanwhile we
        # avoid hammering yfinance with 2243-day refetches every run.
        # Between full refreshes, fall through to the normal
        # missing-gap path (incremental: just append today's close).
        if _is_total_return_symbol(sym):
            meta = _load_meta()
            entry = meta["symbols"].get(sym, {})
            last_full = entry.get("last_full_refresh")
            stale = True
            if last_full:
                try:
                    age = (now.date() - _parse_iso(last_full)).days
                    stale = age >= _TOTAL_RETURN_REFRESH_DAYS
                except (ValueError, TypeError):
                    stale = True
            if stale:
                to_fetch.append((sym, [(start_d, end_d)]))
                # Drop the existing entry so the merged-update doesn't
                # leave stale (differently-normalized) values behind.
                prices.pop(sym, None)
                meta["symbols"].pop(sym, None)
                _meta_dirty = True
                continue
            # else: fall through to incremental fetch path
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
    for sym, gaps in to_fetch:
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
            prices.setdefault(sym, {}).update(merged)
            _prices_dirty = True
            _record_success(sym, actual_start, actual_end, now)
            # Stamp the full-refresh date on total-return symbols so
            # the next-N-days check in the dispatch loop above can
            # skip the full refetch until the timer expires.
            if _is_total_return_symbol(sym):
                meta = _load_meta()
                meta["symbols"][sym]["last_full_refresh"] = now.date().isoformat()
                _meta_dirty = True
            fetched_ok += 1
            # Refresh splits alongside the price fetch — new splits always
            # coincide with new trading days, so whenever we extend price
            # coverage we may have a new split to record.
            try:
                new_splits = _fetch_splits(sym)
                splits_cache = _load_splits()
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
