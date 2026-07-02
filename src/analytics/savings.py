"""Savings-rate and fee rollups.

Savings rate = external net contributions ÷ gross income (salary +
bonuses from metadata.csv).  Arguably the most controllable driver of a
FIRE plan — more so than returns — so it gets a per-year figure in the
Year-by-Year table.

Fees: brokers report per-txn fees/spread that fin parses but never
aggregated anywhere.  One rollup (lifetime / by year / by account)
surfaces the drag.
"""

from __future__ import annotations

from collections import defaultdict

from ._shared import _year
from ..basis import txn_external_cash_flow


def compute_savings_by_year(txns: list[dict],
                            retirement_meta: dict | None) -> dict:
    """``{year: {gross_income, net_contributed, savings_rate_pct}}`` for
    every year that has BOTH salary data and transactions.

    Gross income uses the same effective-salary rule as the tax
    estimates (latest Salary History entry dated on or before Dec 31 of
    the year) plus that year's bonuses.  Net contributed sums
    ``txn_external_cash_flow`` across all accounts — the same external-
    money rule every other metric uses.  Years without salary data are
    omitted (a rate against $0 income is meaningless).
    """
    rm = retirement_meta or {}
    salaries = rm.get("salary_history") or []
    bonuses = rm.get("bonus_history") or []
    if not salaries:
        return {}

    contrib_by_year: dict[str, float] = defaultdict(float)
    for t in txns:
        y = _year(t.get("date", ""))
        if not y:
            continue
        contrib_by_year[y] += txn_external_cash_flow(t)

    out: dict[str, dict] = {}
    for y in sorted(contrib_by_year):
        cutoff = f"{y}-12-31"
        salary = 0.0
        for s in salaries:                     # sorted by date in metadata.py
            if s.get("date") and s["date"] <= cutoff:
                salary = float(s.get("amount", 0) or 0)
        bonus = sum(float(b.get("amount", 0) or 0) for b in bonuses
                    if _year(b.get("date", "")) == y)
        gross = salary + bonus
        if gross <= 0:
            continue
        net = contrib_by_year[y]
        out[y] = {
            "gross_income": round(gross, 2),
            "net_contributed": round(net, 2),
            "savings_rate_pct": round(net / gross * 100, 2),
        }
    return out


def compute_fees(txns: list[dict]) -> dict:
    """``{total, by_year, by_account}`` of broker-reported fees/spread.

    Every parsed txn carries an abs() ``fees`` field (commissions,
    Coinbase spread, fund maintenance fees, ADR fees).  Purely
    informational — fees are already baked into basis/proceeds where
    the broker reports them that way.
    """
    total = 0.0
    by_year: dict[str, float] = defaultdict(float)
    by_account: dict[str, float] = defaultdict(float)
    for t in txns:
        f = float(t.get("fees", 0) or 0)
        if f <= 0:
            continue
        total += f
        y = _year(t.get("date", ""))
        if y:
            by_year[y] += f
        by_account[t.get("account_group", "") or "(unknown)"] += f
    return {
        "total": round(total, 2),
        "by_year": {y: round(v, 2) for y, v in sorted(by_year.items())},
        "by_account": {a: round(v, 2)
                       for a, v in sorted(by_account.items(),
                                          key=lambda kv: -kv[1])},
    }
