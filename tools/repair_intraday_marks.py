"""Re-fetch cached daily bars that were captured mid-session.

WHY THIS EXISTS
---------------
Until the settle-awareness work (see ``PLAN-price-freshness.md``), the
price cache decided whether to fetch by comparing the requested end
against ``covered_end``.  The first run of any day set
``covered_end == today``, so every later run that day was a no-op — and
because ``_missing_ranges`` only ever fetches FORWARD, that day's bar
was never revisited.

yfinance's daily bar carries a LIVE price during market hours.  So every
day the pipeline was first run while the market was open left an
intraday mark stored as if it were that day's close, permanently.

Measured on the author's cache: ~13% of dates in the affected window,
off by up to ~4% on a volatile name.  The contamination is bounded to
the period of daily runs — anything fetched as a historical backfill
(a range ending in the past) was already settled and is correct.

WHAT IT DOES
------------
Rewinds each symbol's ``covered_end`` / ``settled_through`` to
``--since``, so the next pipeline run sees a gap and refetches that
window through the normal batched path.  It does NOT delete anything:
``_apply_fetch_success`` merges, so a symbol whose refetch fails or
returns nothing (delisted, tombstoned) keeps every value it already had.
Deleting shards instead would permanently lose the history of tickers
yfinance can no longer serve.

USAGE
-----
    python -m tools.repair_intraday_marks --audit --since 2026-04-01
    python -m tools.repair_intraday_marks --since 2026-04-01
    python -m src.main

``--audit`` fetches nothing but a sample and reports how many cached
dates disagree with a fresh pull, so you can decide whether a repair is
warranted before rewriting coverage state.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import prices as p   # noqa: E402


# Beyond the cache's 6-significant-digit storage rounding — anything
# larger is a genuinely different number, not a serialization artifact.
_REL_TOLERANCE = 2e-5


def _parse_iso(s: str) -> date:
    return date.fromisoformat(s)


def audit(symbols: list[str], since: date, until: date) -> int:
    """Compare cached values against a fresh pull.  Read-only."""
    cached = p._load_prices()
    fresh = p._batch_fetch_ranges(symbols, since, until)
    if fresh is None:
        print("batched fetch unavailable (yfinance missing?)")
        return 1

    bad_dates: dict[str, int] = {}
    total = differing = 0
    for sym in symbols:
        got = (fresh.get(sym) or {}).get("prices") or {}
        have = cached.get(sym) or {}
        for d, new in got.items():
            old = have.get(d)
            if old is None:
                continue
            total += 1
            if old and abs(old - new) / abs(old) > _REL_TOLERANCE:
                differing += 1
                bad_dates[d] = bad_dates.get(d, 0) + 1

    pct = differing / total * 100 if total else 0.0
    print(f"  {differing}/{total} cached dates disagree with a fresh pull "
          f"({pct:.1f}%) across {len(symbols)} symbol(s)")
    if bad_dates:
        ds = sorted(bad_dates)
        print(f"  {len(ds)} distinct dates, {ds[0]} .. {ds[-1]}")
        print("  (these are the days a run first happened mid-session)")
    return 0


def rewind(since: date, *, dry_run: bool = False) -> int:
    """Rewind coverage state so the next run refetches [since, today]."""
    meta = p._load_meta()
    cutoff = (since - timedelta(days=1)).isoformat()
    touched = 0
    for sym, entry in meta.get("symbols", {}).items():
        ce = entry.get("covered_end")
        if not ce or ce <= cutoff:
            continue   # already ends before the window; nothing to redo
        if not dry_run:
            entry["covered_end"] = cutoff
            st = entry.get("settled_through")
            if st and st > cutoff:
                entry["settled_through"] = cutoff
        touched += 1

    verb = "would rewind" if dry_run else "rewound"
    print(f"  {verb} {touched} symbol(s) to covered_end={cutoff}")
    if dry_run:
        return 0
    p._meta_dirty = True
    p.save_caches()
    print("  meta saved — run `python -m src.main` to refetch the window")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", required=True,
                    help="first date to re-fetch (ISO). Everything from "
                         "here to today is re-pulled on the next run.")
    ap.add_argument("--audit", action="store_true",
                    help="report how many cached dates disagree with a "
                         "fresh pull; changes nothing")
    ap.add_argument("--symbols", default="SPY,AAPL,NVDA,TSLA,MSFT",
                    help="comma-separated sample for --audit")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what a rewind would touch, without "
                         "writing the meta file")
    args = ap.parse_args()

    since = _parse_iso(args.since)
    if args.audit:
        # Stop at the last fully settled day so an unsettled bar isn't
        # reported as a disagreement.
        until = p._settle_horizon("AAPL")
        print(f"Auditing {since} .. {until}")
        return audit([s.strip() for s in args.symbols.split(",") if s.strip()],
                     since, until)

    print(f"Rewinding price coverage to {since}")
    return rewind(since, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
