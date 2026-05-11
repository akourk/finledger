"""Per-day trading activity counts and dollar volume, for the
calendar heatmap visualization on the Performance tab.

A "trade" here = Buy / Sell / Option Buy / Option Sell / Reinvest /
crypto Convert.  Other actions (Contribution, Dividend, Lending,
Transfer, etc.) are NOT trades — they're flow / income / bookkeeping.
"""

from __future__ import annotations

from collections import defaultdict


_TRADE_ACTIONS = frozenset({
    "Buy", "Sell", "Reinvest",
    "Option Buy", "Option Sell",
    "Convert In", "Convert Out",
    "Wrap Asset In", "Wrap Asset Out",
    "Unwrap In", "Unwrap Out",
})


def compute_trading_heatmap(txns: list[dict]) -> dict:
    """Return per-day trade counts + dollar volume.

    Output shape::

        {
          "by_date":    [{"date": "YYYY-MM-DD", "count": int, "volume": float, "buy": int, "sell": int}, ...],
          "max_count":  int,    # for scaling the heatmap intensity
          "max_volume": float,
          "first_date": "YYYY-MM-DD",
          "last_date":  "YYYY-MM-DD",
          "total_trades": int,
        }
    """
    by_date: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "volume": 0.0, "buy": 0, "sell": 0}
    )
    for t in txns:
        a = t.get("action", "")
        if a not in _TRADE_ACTIONS:
            continue
        d = t.get("date", "")
        if not d:
            continue
        amt = abs(float(t.get("amount", 0) or 0))
        rec = by_date[d]
        rec["count"] += 1
        rec["volume"] += amt
        if a in ("Buy", "Reinvest", "Option Buy", "Convert In",
                 "Wrap Asset In", "Unwrap In"):
            rec["buy"] += 1
        elif a in ("Sell", "Option Sell", "Convert Out",
                   "Wrap Asset Out", "Unwrap Out"):
            rec["sell"] += 1

    if not by_date:
        return {"by_date": [], "max_count": 0, "max_volume": 0,
                "first_date": "", "last_date": "", "total_trades": 0}

    rows = sorted(
        ({"date": d, **v} for d, v in by_date.items()),
        key=lambda r: r["date"],
    )
    max_count = max(r["count"] for r in rows)
    max_vol = max(r["volume"] for r in rows)
    return {
        "by_date":      [{"date": r["date"], "count": r["count"],
                          "volume": round(r["volume"], 2),
                          "buy": r["buy"], "sell": r["sell"]}
                         for r in rows],
        "max_count":    max_count,
        "max_volume":   round(max_vol, 2),
        "first_date":   rows[0]["date"],
        "last_date":    rows[-1]["date"],
        "total_trades": sum(r["count"] for r in rows),
    }
