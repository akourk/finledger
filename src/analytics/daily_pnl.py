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
from .price_fallbacks import current_position_price_series, current_position_quantities


def compute_daily_pnl(history: list[dict],
                      txns: list[dict],
                      *, window_days: int = 30) -> list[dict]:
    if not history:
        return []

    from ..config import CASH_SYMBOLS
    from ..prices import get_series
    from ..valuation import QTY_EPSILON, mark

    # Use today's positions as the constant share count, reprice at
    # recent dates.  Same trick as compute_header_summary; means we
    # only show "market movement" component, not same-day cash flows.
    today_str = history[-1].get("date") or ""
    if not today_str:
        return []
    today_positions = current_position_quantities(history[-1])
    if not today_positions or any(p.get("valuation_issue") or
                                 ("value" in p and p["value"] is None)
                                 for p in today_positions):
        return []

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
    dates = [(today - timedelta(days=offset)).isoformat()
             for offset in range(window_days, -1, -1)]
    for d_iso, last_txn_price in current_position_price_series(txns, dates):
        if have_cache and not any(d_iso in s for s in series_by_sym.values()):
            continue   # no symbol actually closed on this date
        total = 0.0
        had_any_price = False
        missing_price = False
        # Scoped to this date -- `today_positions` carries one row per
        # (account_group, symbol), so a symbol held in several accounts
        # would otherwise be looked up once per account, every day.
        px_on_date: dict[str, float | None] = {}
        for pos in today_positions:
            sym = pos.get("symbol", "")
            qty = float(pos.get("quantity", 0) or 0)
            if abs(qty) < QTY_EPSILON:
                continue
            # restate_qty=False: `qty` is TODAY's position (already
            # today-basis) and cached prices are today-basis, so the
            # product is correct as-is.  Restating is only for as-of-date
            # balances (see history.py) — here it would double-adjust
            # every date before a recent split.
            m = mark(sym, qty, d_iso, last_txn_price, restate_qty=False,
                     price_cache=px_on_date)
            if m.value is None:
                missing_price = True
                continue
            total += m.value
            had_any_price = True
        if not had_any_price or missing_price:
            # A newly priceable holding is coverage, not investment profit.
            # Require complete consecutive observations before emitting a bar.
            prev_total = None
            continue
        if prev_total is None:
            prev_total = total
            continue
        change = total - prev_total
        change_pct = (change / prev_total * 100) if (prev_total and prev_total > 0) else 0.0
        series.append({
            "date":       d_iso,
            "value":      round(total, 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 4),
        })
        prev_total = total

    return series
