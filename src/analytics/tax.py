"""Tax-tab analytics: realized gains split ST/LT/1256, harvest candidates, wash-sale detection, marginal-rate estimates."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from ._shared import (
    # Constants
    RETIREMENT_GROUPS, SAVINGS_GROUPS,
    CASH_ADD_ACTIONS, CASH_SUB_ACTIONS, INCOME_ACTION_KINDS,
    # Helpers
    _parse_iso, _year,
    classify_retirement_contribution,
    bridge_adjustment, net_cash_flow,
    _filter_value_fn, _account_filter_sets,
    _balance_sort_key, _value_at_date, _cash_flow_events,
    _period_return, _chain_link_return,
)
from ..actions import BASIS_EFFECTS  # noqa: F401
from ..basis import _basis_dollars  # noqa: F401
from ..config import ACCOUNT_TYPES, CASH_SYMBOLS
from ..prices import get_price, split_factor_since

# Option-symbol helpers are defined in the options module; tax classification
# needs them to detect 1256 contracts.  Importing here rather than via
# _shared avoids _shared growing every time a helper turns out to be
# cross-module.
from .options import _parse_option_symbol, _is_option_symbol, _hold_days

# Cash-settled broad-based index options: 60% long-term / 40% short-term
# regardless of holding period.  ETF options (SPY, QQQ) are NOT 1256.
SECTION_1256_UNDERLYINGS = frozenset({
    "SPX", "SPXW", "NDX", "NDXP", "XSP", "RUT", "RUTW", "DJX", "VIX",
})


def _classify_realized(txn: dict) -> dict:
    """Split a realized-gain txn into ST / LT / §1256 components."""
    gain = float(txn.get("realized_gain", 0) or 0)
    parsed = _parse_option_symbol(txn.get("symbol", ""))
    if parsed and parsed["underlying"] in SECTION_1256_UNDERLYINGS:
        return {"st": gain * 0.4, "lt": gain * 0.6, "s1256": gain, "kind": "1256"}
    days = txn.get("holding_days")
    is_lt = days is not None and days > 365
    return {
        "st": 0.0 if is_lt else gain,
        "lt": gain if is_lt else 0.0,
        "s1256": 0.0,
        "kind": "normal",
    }


# Federal ordinary-income tax brackets, LTCG brackets, and standard
# deduction, keyed by (year, filing_status).  Filing status comes
# from data/metadata.csv ("Filing Status" row); defaults to "Single"
# when absent.  Tables are verbatim from IRS Rev. Proc. publications.
#
# When a year isn't listed, _pick_by_year falls back to the most
# recent table at or before that year (or the newest available).
_BRACKETS_BY_YEAR_STATUS: dict[tuple[int, str], list[tuple[float, float]]] = {
    # ---- 2024 ----
    (2024, "Single"): [
        (11600, 0.10), (47150, 0.12), (100525, 0.22),
        (191950, 0.24), (243725, 0.32), (609350, 0.35), (math.inf, 0.37)],
    (2024, "Married Filing Jointly"): [
        (23200, 0.10), (94300, 0.12), (201050, 0.22),
        (383900, 0.24), (487450, 0.32), (731200, 0.35), (math.inf, 0.37)],
    (2024, "Married Filing Separately"): [
        (11600, 0.10), (47150, 0.12), (100525, 0.22),
        (191950, 0.24), (243725, 0.32), (365600, 0.35), (math.inf, 0.37)],
    (2024, "Head of Household"): [
        (16550, 0.10), (63100, 0.12), (100500, 0.22),
        (191950, 0.24), (243700, 0.32), (609350, 0.35), (math.inf, 0.37)],
    # ---- 2025 ----
    (2025, "Single"): [
        (11925, 0.10), (48475, 0.12), (103350, 0.22),
        (197300, 0.24), (250525, 0.32), (626350, 0.35), (math.inf, 0.37)],
    (2025, "Married Filing Jointly"): [
        (23850, 0.10), (96950, 0.12), (206700, 0.22),
        (394600, 0.24), (501050, 0.32), (751600, 0.35), (math.inf, 0.37)],
    (2025, "Married Filing Separately"): [
        (11925, 0.10), (48475, 0.12), (103350, 0.22),
        (197300, 0.24), (250525, 0.32), (375800, 0.35), (math.inf, 0.37)],
    (2025, "Head of Household"): [
        (17000, 0.10), (64850, 0.12), (103350, 0.22),
        (197300, 0.24), (250500, 0.32), (626350, 0.35), (math.inf, 0.37)],
}

_LTCG_BY_YEAR_STATUS: dict[tuple[int, str], list[tuple[float, float]]] = {
    # 2024
    (2024, "Single"):                    [(47025, 0.00), (518900, 0.15), (math.inf, 0.20)],
    (2024, "Married Filing Jointly"):    [(94050, 0.00), (583750, 0.15), (math.inf, 0.20)],
    (2024, "Married Filing Separately"): [(47025, 0.00), (291850, 0.15), (math.inf, 0.20)],
    (2024, "Head of Household"):         [(63000, 0.00), (551350, 0.15), (math.inf, 0.20)],
    # 2025
    (2025, "Single"):                    [(48350, 0.00), (533400, 0.15), (math.inf, 0.20)],
    (2025, "Married Filing Jointly"):    [(96700, 0.00), (600050, 0.15), (math.inf, 0.20)],
    (2025, "Married Filing Separately"): [(48350, 0.00), (300000, 0.15), (math.inf, 0.20)],
    (2025, "Head of Household"):         [(64750, 0.00), (566700, 0.15), (math.inf, 0.20)],
}

_STD_DED_BY_YEAR_STATUS: dict[tuple[int, str], float] = {
    (2024, "Single"):                    14600,
    (2024, "Married Filing Jointly"):    29200,
    (2024, "Married Filing Separately"): 14600,
    (2024, "Head of Household"):         21900,
    (2025, "Single"):                    15000,
    (2025, "Married Filing Jointly"):    30000,
    (2025, "Married Filing Separately"): 15000,
    (2025, "Head of Household"):         22500,
}

# Backwards-compat aliases — old code (and tests) read these by name.
# Frozen at the Single-filer view that was the original assumption.
_BRACKETS_BY_YEAR: dict[int, list[tuple[float, float]]] = {
    yr: _BRACKETS_BY_YEAR_STATUS[(yr, "Single")]
    for yr in {k[0] for k in _BRACKETS_BY_YEAR_STATUS}
}
_LTCG_BY_YEAR: dict[int, list[tuple[float, float]]] = {
    yr: _LTCG_BY_YEAR_STATUS[(yr, "Single")]
    for yr in {k[0] for k in _LTCG_BY_YEAR_STATUS}
}
_STD_DED_BY_YEAR: dict[int, float] = {
    yr: _STD_DED_BY_YEAR_STATUS[(yr, "Single")]
    for yr in {k[0] for k in _STD_DED_BY_YEAR_STATUS}
}
# IRS 401(k) elective deferral limits — used to cap end-of-year projections
# of contributions extrapolated from YTD pace.  Keep in sync with the
# dashboard's K401_LIMIT_BY_YEAR table in src/dashboard/app.js.
_K401_LIMIT_BY_YEAR: dict[int, float] = {
    2018: 18500, 2019: 19000, 2020: 19500, 2021: 19500, 2022: 20500,
    2023: 22500, 2024: 23000, 2025: 23500, 2026: 24500,
}


def _pick_by_year(yr: int, table: dict):
    if yr in table:
        return table[yr]
    if not table:
        return None
    # Most recent year ≤ yr, fall back to newest
    keys = sorted(table.keys())
    for k in reversed(keys):
        if k <= yr:
            return table[k]
    return table[keys[-1]]


def _pick_by_year_status(yr: int, status: str, table: dict):
    """Look up a (year, filing_status) entry, falling back to the
    closest year that has data for this status.  Falls back to Single
    if the requested status isn't tabulated for any year."""
    if not table:
        return None
    if (yr, status) in table:
        return table[(yr, status)]
    years_for_status = sorted({y for (y, s) in table if s == status})
    if years_for_status:
        for y in reversed(years_for_status):
            if y <= yr:
                return table[(y, status)]
        return table[(years_for_status[-1], status)]
    # Status unknown — fall back to Single.
    years_single = sorted({y for (y, s) in table if s == "Single"})
    for y in reversed(years_single):
        if y <= yr:
            return table[(y, "Single")]
    return table[(years_single[-1], "Single")] if years_single else None


def _marginal_for(taxable_income: float, brackets: list[tuple[float, float]]) -> float:
    rate = brackets[0][1]
    for cap, r in brackets:
        rate = r
        if taxable_income <= cap:
            break
    return rate


def _tax_rate_estimate(year_str: str, retirement_meta: dict,
                       txns: list[dict]) -> dict:
    """Estimate the user's marginal ordinary-income and LTCG rates for
    a given year based on salary history + bonuses + portfolio income +
    pre-tax 401K contributions.
    """
    try:
        yr = int(year_str)
    except (ValueError, TypeError):
        return {}
    status = (retirement_meta or {}).get("filing_status") or "Single"
    brackets = _pick_by_year_status(yr, status, _BRACKETS_BY_YEAR_STATUS)
    ltcg     = _pick_by_year_status(yr, status, _LTCG_BY_YEAR_STATUS)
    std      = _pick_by_year_status(yr, status, _STD_DED_BY_YEAR_STATUS) or 0
    if not brackets or not ltcg:
        return {}

    # Salary: most recent salary-history entry dated on or before Dec 31
    salaries = (retirement_meta or {}).get("salary_history") or []
    cutoff = f"{yr}-12-31"
    salary = 0.0
    for s in salaries:
        if s.get("date") and s["date"] <= cutoff:
            salary = float(s.get("amount", 0) or 0)

    bonuses = sum(
        float(b.get("amount", 0) or 0)
        for b in (retirement_meta or {}).get("bonus_history") or []
        if _year(b.get("date", "")) == year_str
    )

    portfolio_income_ytd = 0.0
    k401_ytd = 0.0
    # Realized gains (taxable accounts only — Retirement-account sells are
    # tax-deferred or tax-free).  Short-term stacks with ordinary income at
    # your marginal rate; long-term taxed separately at LTCG rates but still
    # counts toward AGI / MAGI.  Not projected — sells are lumpy and a
    # linear extrapolation from YTD pace would mislead.
    realized_st_ytd = 0.0
    realized_lt_ytd = 0.0
    for t in txns:
        if _year(t.get("date", "")) != year_str:
            continue
        if t.get("action") in INCOME_ACTION_KINDS:
            portfolio_income_ytd += float(t.get("amount", 0) or 0)
        info = classify_retirement_contribution(t)
        if info["is_contrib"] and t.get("account_group") in ("401K", "Rollover IRA"):
            k401_ytd += float(t.get("amount", 0) or 0)
        if (t.get("realized_gain") not in (None, 0)
                and t.get("account_type") == "Taxable"):
            c = _classify_realized(t)
            realized_st_ytd += c["st"]
            realized_lt_ytd += c["lt"]

    # End-of-year projection for the current year only.  Past years use
    # their actuals as-is.  Year fraction = days elapsed / total days
    # (handles leap years).  Portfolio income projects linearly; 401(k)
    # projects linearly but caps at the IRS limit (front-loaded
    # contributors hit the cap mid-year and stop, so unbounded
    # extrapolation overstates the deduction).
    from datetime import date as _date
    today = _date.today()
    is_current_year = (yr == today.year)
    year_fraction = 1.0
    if is_current_year:
        year_start = _date(yr, 1, 1)
        year_end = _date(yr, 12, 31)
        days_total = (year_end - year_start).days + 1
        days_elapsed = max(1, (today - year_start).days + 1)
        year_fraction = min(1.0, days_elapsed / days_total)

    if is_current_year and year_fraction < 1.0:
        portfolio_income = portfolio_income_ytd / year_fraction
        k401_uncapped = k401_ytd / year_fraction
        k401_cap = _K401_LIMIT_BY_YEAR.get(yr, k401_uncapped)
        k401 = min(k401_uncapped, k401_cap)
    else:
        portfolio_income = portfolio_income_ytd
        k401 = k401_ytd

    # Realized gains use YTD figures unchanged — sells aren't pace-based.
    realized_st = realized_st_ytd
    realized_lt = realized_lt_ytd

    # Ordinary income = salary + bonuses + dividends/interest + ST capital
    # gains.  ST gains stack with the ordinary bracket schedule, so they
    # belong in the bracket-fill display.  LT gains feed AGI separately.
    ordinary_income = salary + bonuses + portfolio_income + realized_st
    gross_income = ordinary_income + realized_lt
    agi = max(0.0, gross_income - k401)
    taxable_ordinary = max(0.0, ordinary_income - k401 - std)
    # Total taxable income (drives LTCG bracket selection)
    taxable_income = max(0.0, agi - std)
    # Bracket projection: lay out each ordinary-income bracket and how
    # much of the user's income falls into each.  Helpful for tax-
    # planning decisions ("if I realize $10k more in short-term gains,
    # how much pushes through into the next bracket?").
    bracket_fill = []
    prev_cap = 0.0
    # Ordinary bracket fill is driven by ordinary taxable income only —
    # LT gains slot into the separate LTCG schedule and don't push the
    # ordinary brackets.
    remaining = taxable_ordinary
    for cap, rate in brackets:
        # Width of THIS bracket (cap is cumulative)
        width = cap - prev_cap
        in_this = min(remaining, width) if remaining > 0 else 0.0
        room = max(0.0, width - in_this)
        bracket_fill.append({
            "rate":  rate,
            "lower": round(prev_cap, 2),
            "upper": round(cap, 2) if cap != float("inf") else None,
            "in_bracket":  round(in_this, 2),
            "room_left":   round(room, 2),
            "tax_paid":    round(in_this * rate, 2),
        })
        remaining = max(0.0, remaining - in_this)
        prev_cap = cap
        if cap == float("inf"):
            break

    # Headroom = $ until the next bracket bites.  Useful as a single
    # number on the dashboard.
    headroom_to_next = 0.0
    next_rate = None
    for b in bracket_fill:
        if b["room_left"] > 0:
            headroom_to_next = b["room_left"]
            # Next bracket = the one immediately after this row
            idx = bracket_fill.index(b)
            if idx + 1 < len(bracket_fill):
                next_rate = bracket_fill[idx + 1]["rate"]
            break

    return {
        "year": yr,
        "filing_status": status,
        "salary": round(salary, 2),
        "bonuses": round(bonuses, 2),
        "portfolio_income": round(portfolio_income, 2),
        "portfolio_income_ytd": round(portfolio_income_ytd, 2),
        "realized_st": round(realized_st, 2),
        "realized_lt": round(realized_lt, 2),
        "k401": round(k401, 2),
        "k401_ytd": round(k401_ytd, 2),
        "k401_limit": _K401_LIMIT_BY_YEAR.get(yr),
        "gross_income": round(gross_income, 2),
        "agi": round(agi, 2),
        "taxable_income": round(taxable_income, 2),
        "taxable_ordinary": round(taxable_ordinary, 2),
        "std_deduction": round(std, 2),
        # Marginal ordinary rate is driven by taxable_ordinary (salary +
        # bonuses + dividends + ST gains − k401 − std).  LTCG bracket is
        # driven by total taxable_income (since LTCG slots above ordinary).
        "marginal_short": round(_marginal_for(taxable_ordinary, brackets), 4),
        "marginal_long": round(_marginal_for(taxable_income, ltcg), 4),
        "bracket_fill": bracket_fill,
        "headroom_to_next_bracket": round(headroom_to_next, 2),
        "next_bracket_rate": next_rate,
        "is_projection": bool(is_current_year and year_fraction < 1.0),
        "year_fraction_observed": round(year_fraction, 4),
    }


def compute_tax_analytics(txns: list[dict], holdings: list[dict],
                         retirement_meta: dict) -> dict:
    """Realized gains split by year / asset / underlying, harvest
    candidates, wash sales, and per-year tax rate estimates."""
    realized = [t for t in txns if isinstance(t.get("realized_gain"), (int, float))]

    # By year
    by_year: dict[str, dict] = {}
    for t in realized:
        y = _year(t.get("date", ""))
        if not y:
            continue
        c = _classify_realized(t)
        r = by_year.setdefault(y, {
            "year": y, "count": 0, "proceeds": 0.0, "basis": 0.0,
            "st": 0.0, "lt": 0.0, "s1256": 0.0,
        })
        r["count"] += 1
        r["proceeds"] += float(t.get("amount", 0) or 0)
        r["basis"] += float(t.get("cost_basis", 0) or 0)
        r["st"] += c["st"]
        r["lt"] += c["lt"]
        r["s1256"] += c["s1256"]
    realized_by_year = [
        {**r, **{k: round(v, 2) for k, v in r.items() if isinstance(v, float)}}
        for r in sorted(by_year.values(), key=lambda x: x["year"])
    ]

    # By symbol (every realized-gain txn contributes)
    by_sym: dict[str, dict] = {}
    for t in realized:
        sym = t.get("symbol") or "(unknown)"
        c = _classify_realized(t)
        parsed = _parse_option_symbol(sym)
        entry = by_sym.setdefault(sym, {
            "symbol": sym,
            "underlying": parsed["underlying"] if parsed else sym,
            "is_option": bool(parsed),
            "trades": 0, "proceeds": 0.0, "basis": 0.0,
            "st": 0.0, "lt": 0.0, "s1256": 0.0,
            "total_gain": 0.0, "year": _year(t.get("date", "")),
        })
        entry["trades"] += 1
        entry["proceeds"] += float(t.get("amount", 0) or 0)
        entry["basis"] += float(t.get("cost_basis", 0) or 0)
        entry["st"] += c["st"]
        entry["lt"] += c["lt"]
        entry["s1256"] += c["s1256"]
        entry["total_gain"] += float(t.get("realized_gain", 0) or 0)
    realized_by_symbol = sorted(
        ({**r, **{k: round(v, 2) for k, v in r.items() if isinstance(v, float)}}
         for r in by_sym.values()),
        key=lambda x: -abs(x["total_gain"]),
    )

    # Options by underlying (flagged as 1256 or not)
    under: dict[str, dict] = {}
    for t in realized:
        sym = t.get("symbol") or ""
        if not _is_option_symbol(sym):
            continue
        parsed = _parse_option_symbol(sym)
        u = parsed["underlying"] if parsed else "(unknown)"
        c = _classify_realized(t)
        entry = under.setdefault(u, {
            "underlying": u, "trades": 0,
            "st": 0.0, "lt": 0.0, "s1256": 0.0, "total": 0.0,
            "is_1256": parsed is not None and parsed["underlying"] in SECTION_1256_UNDERLYINGS,
        })
        entry["trades"] += 1
        entry["st"] += c["st"]
        entry["lt"] += c["lt"]
        entry["s1256"] += c["s1256"]
        entry["total"] += float(t.get("realized_gain", 0) or 0)
    realized_by_underlying = sorted(
        ({**r, **{k: round(v, 2) for k, v in r.items() if isinstance(v, float)}}
         for r in under.values()),
        key=lambda x: -abs(x["total"]),
    )

    # Harvest candidates: current positions with unrealized loss > $10
    harvest = [
        h for h in holdings
        if isinstance(h.get("unrealized_gain"), (int, float))
        and h["unrealized_gain"] < -10
    ]
    harvest.sort(key=lambda h: h["unrealized_gain"])   # most-negative first
    harvest = harvest[:25]

    # Wash sales: sells at loss with buy-of-same-symbol within 30 days
    buys_by_sym: dict[str, list[str]] = defaultdict(list)
    for t in txns:
        if t.get("action") not in ("Buy", "Reinvest", "Contribution"):
            continue
        sym = t.get("symbol")
        if sym and t.get("date"):
            buys_by_sym[sym].append(t["date"])
    wash_sales = []
    for t in realized:
        if (t.get("realized_gain") or 0) >= 0:
            continue
        if t.get("action") != "Sell":
            continue
        sym = t.get("symbol", "")
        close_d = _parse_iso(t.get("date", ""))
        if not close_d:
            continue
        mn = close_d - timedelta(days=30)
        mx = close_d + timedelta(days=30)
        for bd in buys_by_sym.get(sym, []):
            bd_d = _parse_iso(bd)
            if bd_d and mn <= bd_d <= mx and bd != t.get("date"):
                wash_sales.append({
                    "date": t["date"],
                    "symbol": sym,
                    "loss": round(t["realized_gain"], 2),
                    "offending_buy_date": bd,
                })
                break

    # Tax rate estimates per year
    years_seen = sorted({_year(t.get("date", "")) for t in txns
                         if _year(t.get("date", ""))})
    rate_estimates = {
        y: _tax_rate_estimate(y, retirement_meta or {}, txns)
        for y in years_seen
    }
    rate_estimates = {y: r for y, r in rate_estimates.items() if r}

    return {
        "realized_by_year": realized_by_year,
        "realized_by_symbol": realized_by_symbol,
        "realized_by_underlying": realized_by_underlying,
        "harvest_candidates": harvest,
        "wash_sales": wash_sales,
        "rate_estimates_by_year": rate_estimates,
        "section_1256_underlyings": sorted(SECTION_1256_UNDERLYINGS),
    }


# ---------------------------------------------------------------------------
# Performance tab extras — position returns, winners / losers
# ---------------------------------------------------------------------------
