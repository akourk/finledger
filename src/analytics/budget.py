"""Budget rollup — recurring living expenses from ``Budget`` metadata rows.

Turns the user's list of subscriptions / rent / insurance (see the
``Budget`` row type in ``src/metadata.py``) into the figures the Income
tab's Budget section renders.  Returns ``None`` when no budget rows are
defined so the section can hide.

Returned shape::

    {
      "rows": [                     # active rows, sorted monthly desc
        {"label", "category", "cadence", "amount",   # per-period cost
         "monthly",                                  # normalized $/month
         "effective"},                               # effective-from date
      ],
      "monthly_total":  float,
      "annual_total":   float,
      "by_category": [              # sorted monthly desc
        {"category", "monthly", "annual", "pct"},    # pct of monthly_total
      ],
      "count": int,                 # active rows
      "superseded_count": int,      # rows replaced by a later same-label row
      "ttm_income": float | None,       # trailing-12mo passive income
      "income_coverage_pct": float | None,  # ttm_income / annual_total
      "vs_annual_expenses": {           # bottom-up budget vs the declared
        "annual_expenses": float,       #   `Annual Expenses` lump, when set
        "delta": float,                 # annual_total − annual_expenses
        "delta_pct": float | None,
      } | None,
    }

Cadence → monthly normalization: monthly ×1, yearly ÷12, 6mo ÷6,
quarterly ÷3, weekly ×52÷12.  Rows sharing a label supersede each other by effective
date (latest wins; ties broken by file order), so a rent increase is a
new row and an ``Amount = 0`` row cancels a subscription.
"""

from __future__ import annotations

_MONTHLY_FACTOR = {
    "monthly":   1.0,
    "yearly":    1.0 / 12.0,
    "6mo":       1.0 / 6.0,     # every-6-months billing (car insurance…)
    "quarterly": 1.0 / 3.0,
    "weekly":    52.0 / 12.0,
}


def compute_budget(budget_rows: list[dict] | None,
                   annual_expenses: float | None = None,
                   ttm_income: float | None = None) -> dict | None:
    """Roll up ``Budget`` metadata rows for the Income tab.

    ``annual_expenses`` is the active `Annual Expenses` metadata figure
    (for the bottom-up vs declared comparison); ``ttm_income`` is the
    trailing-12-months passive income from the income calendar (for the
    coverage percentage).
    """
    if not budget_rows:
        return None

    # Latest row per label wins (case-insensitive), preserving file
    # order for same-date rows so a later line supersedes an earlier one.
    latest: dict[str, dict] = {}
    for i, r in enumerate(budget_rows):
        key = (r.get("label") or "").strip().lower()
        if not key:
            continue
        prev = latest.get(key)
        if prev is None or (r.get("date") or "") >= (prev.get("date") or ""):
            latest[key] = {**r, "_order": i}
    superseded = len([r for r in budget_rows if (r.get("label") or "").strip()]) \
        - len(latest)

    rows = []
    for r in latest.values():
        amt = float(r.get("amount") or 0)
        if amt <= 0:          # cancelled (Amount = 0) or nonsense
            continue
        cadence = r.get("cadence") or "monthly"
        monthly = amt * _MONTHLY_FACTOR.get(cadence, 1.0)
        rows.append({
            "label":     r.get("label", ""),
            "category":  r.get("category") or "Other",
            "cadence":   cadence,
            "amount":    round(amt, 2),
            "monthly":   round(monthly, 2),
            "effective": r.get("date") or None,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: -r["monthly"])

    monthly_total = sum(r["monthly"] for r in rows)
    annual_total = monthly_total * 12.0

    by_cat: dict[str, float] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0.0) + r["monthly"]
    by_category = [
        {
            "category": c,
            "monthly":  round(m, 2),
            "annual":   round(m * 12.0, 2),
            "pct":      round(m / monthly_total * 100, 2) if monthly_total > 0 else None,
        }
        for c, m in sorted(by_cat.items(), key=lambda kv: -kv[1])
    ]

    income_coverage_pct = None
    if ttm_income is not None and annual_total > 0:
        income_coverage_pct = round(ttm_income / annual_total * 100, 2)

    vs_annual_expenses = None
    if annual_expenses and annual_expenses > 0:
        delta = annual_total - annual_expenses
        vs_annual_expenses = {
            "annual_expenses": round(annual_expenses, 2),
            "delta":           round(delta, 2),
            "delta_pct":       round(delta / annual_expenses * 100, 2),
        }

    return {
        "rows":                rows,
        "monthly_total":       round(monthly_total, 2),
        "annual_total":        round(annual_total, 2),
        "by_category":         by_category,
        "count":               len(rows),
        "superseded_count":    superseded,
        "ttm_income":          round(ttm_income, 2) if ttm_income is not None else None,
        "income_coverage_pct": income_coverage_pct,
        "vs_annual_expenses":  vs_annual_expenses,
    }
