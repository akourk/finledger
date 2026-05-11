"""Income calendar — historical monthly income series + a simple
forward forecast for currently-held dividend-paying positions.

Forecast methodology: for each currently-held position, average its
last 4 quarters of received dividends and project that out for the
next 12 months.  Doesn't try to match exact ex-div dates (we don't
fetch yfinance's calendar API — adds complexity and a network call
that might fail) — just produces a "expected income next 12 months"
number per position so the user has something to plan around.

Output:
- ``forecast_12mo``: list of ``{symbol, last_4_quarters, projected_annual}``
- ``forecast_total``: sum across all positions
- ``ttm_actual``: trailing-12-months actual income for comparison
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta


_INCOME_ACTIONS = frozenset({"Dividend", "Interest", "Lending", "Reward"})


def compute_income_calendar(txns: list[dict],
                            holdings_by_account: list[dict]) -> dict:
    # Held symbols that aren't cash, plus per-symbol cost basis
    # rolled up across accounts.  Yield-on-cost = trailing-12mo income
    # / aggregate cost basis — measures whether contribution-weighted
    # yield is rising over time, regardless of price changes.
    held = set()
    basis_by_sym: dict[str, float] = defaultdict(float)
    value_by_sym: dict[str, float] = defaultdict(float)
    for h in holdings_by_account:
        sym = h.get("symbol", "")
        qty = h.get("quantity", 0)
        if sym and isinstance(qty, (int, float)) and qty > 0 and sym != "USD":
            held.add(sym)
            cb = h.get("cost_basis")
            if isinstance(cb, (int, float)):
                basis_by_sym[sym] += cb
            v = h.get("value")
            if isinstance(v, (int, float)):
                value_by_sym[sym] += v

    # Per-symbol income history (last 4 quarters)
    today = datetime.now().date()
    one_year_ago = (today - timedelta(days=365)).isoformat()
    today_iso = today.isoformat()

    by_sym_recent = defaultdict(float)   # sym -> sum income last 365 days
    ttm_total = 0.0
    for t in txns:
        a = t.get("action", "")
        if a not in _INCOME_ACTIONS:
            continue
        d = t.get("date", "")
        if not d or d < one_year_ago or d > today_iso:
            continue
        amt = float(t.get("amount", 0) or 0)
        if amt <= 0:
            continue
        sym = t.get("symbol", "") or "USD"
        # USD dividends (cash dividends paid to taxable accounts) come in
        # under symbol=USD via the USAA reclassification etc.  Map them
        # to whichever underlying is hinted by the description if we can,
        # otherwise group as "Cash income".  Keep it simple: the
        # dashboard already groups USD income under the originating
        # account so we don't try to re-attribute.
        by_sym_recent[sym] += amt
        ttm_total += amt

    # Project: held positions whose ticker had income last year get
    # an annualized projection.  Ignores symbols paid only one tiny
    # dividend (likely one-off lending payment, not recurring).
    forecast = []
    forecast_total = 0.0
    for sym in sorted(held):
        last_4q = by_sym_recent.get(sym, 0.0)
        if last_4q < 1.00:   # under $1 in trailing year → ignore
            continue
        cb = basis_by_sym.get(sym, 0.0)
        cv = value_by_sym.get(sym, 0.0)
        # Yield-on-cost: TTM income / cost basis.  Rises over time as
        # the position grows the dividend.  Distinct from "current
        # yield" (TTM income / current price), which falls when the
        # stock appreciates.  Both are useful — YoC tells you what
        # *your* contribution earns, current yield tells you what
        # *new money* would earn.
        yoc       = round((last_4q / cb) * 100, 3) if cb > 0.01 else None
        cur_yield = round((last_4q / cv) * 100, 3) if cv > 0.01 else None
        forecast.append({
            "symbol":           sym,
            "last_12mo_income": round(last_4q, 2),
            "cost_basis":       round(cb, 2) if cb > 0 else None,
            "yield_on_cost":    yoc,
            "current_yield":    cur_yield,
            # Simple flat extrapolation — assumes next year mirrors
            # last year.  Doesn't account for share-count changes,
            # dividend hikes, etc.  Good enough for ballpark.
            "projected_annual": round(last_4q, 2),
        })
        forecast_total += last_4q

    forecast.sort(key=lambda r: r["projected_annual"], reverse=True)

    # Monthly bars for the past 12 months for the chart
    monthly: dict[str, float] = defaultdict(float)
    for t in txns:
        a = t.get("action", "")
        if a not in _INCOME_ACTIONS:
            continue
        d = t.get("date", "")
        if not d or d < one_year_ago:
            continue
        amt = float(t.get("amount", 0) or 0)
        if amt <= 0:
            continue
        ym = d[:7]   # "YYYY-MM"
        monthly[ym] += amt
    monthly_series = [
        {"month": m, "amount": round(monthly[m], 2)}
        for m in sorted(monthly)
    ]

    return {
        "forecast_12mo":       forecast,
        "forecast_total":      round(forecast_total, 2),
        "ttm_actual":          round(ttm_total, 2),
        "monthly_last_12mo":   monthly_series,
    }
