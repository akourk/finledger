"""Anomaly / alert aggregation for the Overview tab.

Pulls signals from across the analytics modules and surfaces them as
a unified list of cards, each with a severity ("info" / "warn" /
"high"), a message, a kind tag, and optional metadata.  The dashboard
renders the list as a small "Attention" panel — gives the user one
place to look for issues that would otherwise require digging through
multiple tabs.

Sources we pull from (each runs as a small classifier here):

- Concentration risk flags (already structured in concentration.py)
- Stale tickers (failure_count > 0 in the price cache meta)
- CUSIP collision suggestions (rename candidates not in ticker_renames.json)
- Tax-loss harvest candidates (top 3 by absolute loss)
- Stale data (last txn > 30 days ago — user may have forgotten to drop new CSVs)
- Coverage gaps (recent snapshots with priced_pct < 1)
- Significant unrealized gains crossing the long-term threshold soon
"""

from __future__ import annotations

from datetime import datetime, timedelta


def compute_alerts(txns: list[dict],
                   holdings_by_account: list[dict],
                   history: list[dict],
                   tax_analytics: dict,
                   concentration: dict,
                   price_meta: dict,
                   cusip_collisions: list[dict] | None = None) -> list[dict]:
    alerts: list[dict] = []

    # 1. Concentration risk
    for f in concentration.get("flags", []) or []:
        alerts.append({
            "kind":     f"concentration_{f.get('kind', '')}",
            "severity": f.get("severity", "warn"),
            "message":  f.get("message", ""),
        })

    # 2. Top 3 tax-loss harvest candidates (most-negative unrealized)
    for c in (tax_analytics.get("harvest_candidates") or [])[:3]:
        alerts.append({
            "kind":     "harvest",
            "severity": "info",
            "message":  (f"{c.get('symbol')} has an unrealized loss of "
                         f"${abs(c.get('unrealized_gain', 0)):.0f} — "
                         f"potential tax-loss harvest candidate."),
        })

    # 3. Stale data: latest txn vs today
    if txns:
        latest = max((t.get("date", "") for t in txns if t.get("date")),
                     default="")
        if latest:
            try:
                latest_d = datetime.strptime(latest, "%Y-%m-%d").date()
                age = (datetime.now().date() - latest_d).days
                if age > 30:
                    alerts.append({
                        "kind":     "stale_data",
                        "severity": "info" if age < 90 else "warn",
                        "message":  (f"Latest transaction is {age} days old — "
                                     f"have you exported fresh broker CSVs?"),
                    })
            except ValueError:
                pass

    # 4. Coverage gaps in recent snapshots
    recent = [s for s in (history[-12:] if history else [])
              if s.get("priced_pct", 1) < 0.999]
    if recent:
        worst = min(s.get("priced_pct", 1) for s in recent)
        alerts.append({
            "kind":     "coverage_gap",
            "severity": "info",
            "message":  (f"{len(recent)} of last 12 snapshots have unpriced "
                         f"positions (worst: {worst*100:.1f}%) — some history "
                         f"values may be approximations."),
        })

    # 5. Failing tickers (failure_count > 0)
    failing = [(s, e.get("failure_count", 0))
               for s, e in (price_meta.get("symbols") or {}).items()
               if e.get("failure_count", 0) > 0]
    if failing:
        alerts.append({
            "kind":     "fetch_failing",
            "severity": "info",
            "message":  (f"{len(failing)} symbol(s) failing price fetches — "
                         f"may be delisted or renamed."),
            "details":  [s for s, _ in failing[:5]],
        })

    # 6. CUSIP rename suggestions not yet curated
    for r in (cusip_collisions or []):
        alerts.append({
            "kind":     "cusip_rename",
            "severity": "info",
            "message":  (f"Potential ticker rename: {r['stale']} → {r['canon']} "
                         f"(CUSIP {r['cusip']}).  Add to "
                         f"cache/ticker_renames.json if confirmed."),
        })

    # 7. Long-term threshold crossings: LOTS within 60 days of long-term
    #    eligibility with a meaningful unrealized gain — selling now =
    #    ST tax, waiting = LT.  Reads tax.lt_horizon (the per-lot
    #    inventory, calendar-correct anniversary rule) instead of the
    #    old first-buy txn scan, which used naive 365-day math and
    #    assumed the earliest buy was still the open lot (wrong after
    #    any FIFO sale).
    soon: dict[str, dict] = {}
    for r in (tax_analytics or {}).get("lt_horizon") or []:
        if r.get("is_long_term"):
            continue
        days = r.get("days_to_lt")
        gain = r.get("unrealized_gain")
        if not isinstance(days, (int, float)) or days > 60:
            continue
        if not isinstance(gain, (int, float)) or gain < 100:
            continue
        sym = r.get("symbol", "")
        # One alert per symbol — soonest lot wins, gains accumulate.
        e = soon.setdefault(sym, {"sym": sym, "days": days, "gain": 0.0})
        e["days"] = min(e["days"], days)
        e["gain"] += gain
    for s in sorted(soon.values(), key=lambda r: r["days"])[:3]:
        alerts.append({
            "kind":     "long_term_soon",
            "severity": "info",
            "message":  (f"{s['sym']}: {s['days']:.0f} day(s) until "
                         f"long-term capital gains treatment "
                         f"(unrealized ${s['gain']:.0f} across lots)."),
        })

    return alerts
