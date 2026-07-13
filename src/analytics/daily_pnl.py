"""Recent daily P&L bars.

History snapshots are monthly + today, so a "true" daily series isn't
available without daily snapshots.  Instead we compute the most
recent month-or-so of pseudo-daily moves by walking back from today's
positions and re-pricing at recent dates from the price cache —
similar to ``compute_header_summary`` but stretched into a series.

Result: ``[{date, value, change, change_pct}, ...]`` for the last
``window_days`` trading days.

Skipped if the cache doesn't have enough recent prices to produce a
meaningful series (e.g. brand-new install where everything's stub).
"""

from __future__ import annotations

from datetime import datetime, timedelta


def compute_daily_pnl(history: list[dict],
                      txns: list[dict],
                      *, window_days: int = 30) -> list[dict]:
    if not history:
        return []

    from ..config import CASH_SYMBOLS
    from ..prices import get_price, get_series

    # Use today's positions as the constant share count, reprice at
    # recent dates.  Same trick as compute_header_summary; means we
    # only show "market movement" component, not same-day cash flows.
    today_positions = (history[-1].get("positions") or [])
    today_str = history[-1].get("date") or ""
    if not today_positions or not today_str:
        return []

    # Build last-txn-price fallback for symbols the cache can't price
    last_txn_price: dict[str, float] = {}
    for t in txns:
        p = float(t.get("price", 0) or 0)
        sym = t.get("symbol", "")
        if sym and p > 0:
            last_txn_price[sym] = p

    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    series = []
    prev_total = None

    # Trading-day detection: a date only earns a bar if at least one
    # held symbol has an ACTUAL close cached for that exact date.
    # Without this, get_price's 7-day walk-back fills weekends and
    # holidays with the prior close, emitting flat zero-change bars
    # (except crypto, which trades 7 days and legitimately keeps its
    # weekend dates).  Prefetch each symbol's series once for the
    # window rather than probing per (date × symbol).
    window_start = (today - timedelta(days=window_days)).isoformat()
    series_by_sym: dict[str, dict] = {}
    for pos in today_positions:
        sym = pos.get("symbol", "")
        if sym and sym not in CASH_SYMBOLS and sym not in series_by_sym:
            series_by_sym[sym] = get_series(sym, window_start, today_str)
    # If the cache covers nothing (fresh install, all txn-price
    # fallbacks), keep the old emit-every-day behaviour rather than
    # producing an empty chart.
    have_cache = any(series_by_sym.values())

    # Walk back window_days calendar days (we'll skip dates with no
    # price coverage — handles weekends/holidays without us doing a
    # full trading-calendar lookup).
    for offset in range(window_days, -1, -1):
        d = today - timedelta(days=offset)
        d_iso = d.isoformat()
        if have_cache and not any(d_iso in s for s in series_by_sym.values()):
            continue   # no symbol actually closed on this date
        total = 0.0
        had_any_price = False
        for pos in today_positions:
            sym = pos.get("symbol", "")
            qty = float(pos.get("quantity", 0) or 0)
            if abs(qty) < 1e-9:
                continue
            if sym in CASH_SYMBOLS:
                total += qty
                had_any_price = True
                continue
            px = get_price(sym, d_iso)
            if px is None:
                fb = last_txn_price.get(sym)
                if fb is None:
                    continue
                from ..config import contract_multiplier
                total += qty * fb * contract_multiplier(sym)
            else:
                # No split_factor_since: `qty` is TODAY's position (already
                # today-basis) and cached prices are today-basis, so qty × px
                # is correct as-is.  The factor is only for as-of-date
                # balances (see history.py) — applying it here would
                # double-adjust every date before a recent split.
                total += qty * px
            had_any_price = True
        if not had_any_price:
            continue
        change = (total - prev_total) if prev_total is not None else 0.0
        change_pct = (change / prev_total * 100) if (prev_total and prev_total > 0) else 0.0
        series.append({
            "date":       d_iso,
            "value":      round(total, 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 4),
        })
        prev_total = total

    # Drop the first entry's change=0 placeholder; user wants moves
    return series[1:] if series else []
