"""Rebalancing drift — current allocation vs user-defined targets.

Target allocations come from ``data/metadata.csv`` ``Target Allocation``
rows (Symbol = sector bucket, Amount = target percent).  Here we compare
each target against the *current* sector weight (as a percent of TOTAL
portfolio value, **including cash** — unlike the concentration module's
By-Sector view, which is equity-only — because users typically target a
cash percentage too).

Returns ``None`` when no targets are defined so the dashboard can render
an empty-state prompt instead of a misleading all-zero table.

Output:
- ``rows``: per-target ``{bucket, target_pct, current_pct, current_value,
  drift_pct, action_value}`` where ``action_value`` is the dollar amount
  to buy (+) or sell (−) to hit the target.  Sorted by |drift|.
- ``total_value``, ``untargeted_value`` / ``untargeted_pct`` (sectors held
  but not given a target), ``total_target_pct`` (should sum ~100), and
  ``max_abs_drift``.
"""

from __future__ import annotations

from collections import defaultdict


def compute_rebalancing(holdings_by_account: list[dict],
                        target_allocation: list[dict] | None) -> dict | None:
    if not target_allocation:
        return None

    by_sec: dict[str, float] = defaultdict(float)
    total = 0.0
    for h in holdings_by_account:
        v = h.get("value")
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        total += v
        by_sec[h.get("sector") or "Other"] += v

    if total <= 0:
        return None

    # Collapse duplicate target buckets (last wins), preserve order.
    targets: dict[str, float] = {}
    for t in target_allocation:
        b = t.get("bucket")
        if b:
            targets[b] = float(t.get("pct", 0) or 0)

    rows = []
    for bucket, tgt in targets.items():
        cur = by_sec.get(bucket, 0.0)
        cur_pct = cur / total * 100
        target_value = total * tgt / 100
        rows.append({
            "bucket":        bucket,
            "target_pct":    round(tgt, 2),
            "current_pct":   round(cur_pct, 2),
            "current_value": round(cur, 2),
            "drift_pct":     round(cur_pct - tgt, 2),
            # + = under target (buy this much); − = over target (sell).
            "action_value":  round(target_value - cur, 2),
        })
    rows.sort(key=lambda r: abs(r["drift_pct"]), reverse=True)

    untargeted = sum(v for s, v in by_sec.items() if s not in targets)
    return {
        "rows":             rows,
        "total_value":      round(total, 2),
        "untargeted_value": round(untargeted, 2),
        "untargeted_pct":   round(untargeted / total * 100, 2),
        "total_target_pct": round(sum(targets.values()), 2),
        "max_abs_drift":    round(max((abs(r["drift_pct"]) for r in rows),
                                      default=0.0), 2),
    }
