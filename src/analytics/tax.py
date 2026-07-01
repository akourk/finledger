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
from .options import _parse_option_symbol, _is_option_symbol

# Cash-settled broad-based index options: 60% long-term / 40% short-term
# regardless of holding period.  ETF options (SPY, QQQ) are NOT 1256.
SECTION_1256_UNDERLYINGS = frozenset({
    "SPX", "SPXW", "NDX", "NDXP", "XSP", "RUT", "RUTW", "DJX", "VIX",
})


def _realized_override_delta(txns, retirement_meta, year_str):
    """Per-year (st_delta, lt_delta, accounts) so that any account with a
    ``Reconcile Realized`` row for that year reports the *broker-authoritative*
    realized gain instead of fin's reconstruction.

    fin cannot reconstruct off-platform cost basis (e.g. crypto acquired
    before/outside the broker), so its computed realized can diverge from the
    figure the broker reported to the IRS on a 1099-B / 1099-DA.  For
    tax-decision purposes (AGI / MAGI / Roth eligibility / cap-gains tax)
    the broker figure is what's on record, so we trust it.  The override
    replaces the account's whole-year realized, preserving fin's ST/LT
    character ratio (defaulting to short-term when fin had none — typical
    for crypto)."""
    rm = retirement_meta or {}
    overrides = {r.get("account_group"): float(r.get("amount", 0) or 0)
                 for r in rm.get("reconcile", [])
                 if r.get("kind") == "realized"
                 and (r.get("date") or "")[:4] == year_str}
    if not overrides:
        return 0.0, 0.0, []
    acct = defaultdict(lambda: {"st": 0.0, "lt": 0.0})
    for t in txns:
        if _year(t.get("date", "")) != year_str:
            continue
        ag = t.get("account_group", "")
        if ag not in overrides:
            continue
        if t.get("realized_gain") in (None, 0) or t.get("account_type") != "Taxable":
            continue
        c = _classify_realized(t)
        acct[ag]["st"] += c["st"]
        acct[ag]["lt"] += c["lt"]
    st_delta = lt_delta = 0.0
    for ag, ov in overrides.items():
        cur = acct.get(ag, {"st": 0.0, "lt": 0.0})
        cur_total = cur["st"] + cur["lt"]
        frac = cur["st"] / cur_total if cur_total > 0 else 1.0
        frac = min(1.0, max(0.0, frac))
        st_delta += ov * frac - cur["st"]
        lt_delta += ov * (1.0 - frac) - cur["lt"]
    return st_delta, lt_delta, sorted(overrides)


def _is_long_term(acquired_iso, sold_iso, days_fallback) -> bool:
    """Held MORE than one year (IRS Topic 409)?

    Calendar-correct when both dates are known: long-term iff the sale
    falls on/after the LT-eligible date (anniversary + 1 day, see
    ``_lt_eligible_date``).  A sale exactly on the one-year anniversary
    is short-term — the naive ``days > 365`` test misclassifies that
    case whenever the holding window spans a Feb 29.  Falls back to the
    day-count test when either date is missing/unparseable (e.g. a
    ``VARIOUS`` acquired date)."""
    a = _parse_iso(acquired_iso) if acquired_iso else None
    s = _parse_iso(sold_iso) if sold_iso else None
    if a and s:
        return s >= _lt_eligible_date(a)
    return isinstance(days_fallback, (int, float)) and days_fallback > 365


def _classify_realized(txn: dict) -> dict:
    """Split a realized-gain txn into ST / LT / §1256 components.

    Prefers the per-lot ``lot_breakdown`` (from the FIFO walker) so a
    sell that straddles the 1-year line is split lot-by-lot — the right
    answer and what the broker's 1099-B reports.  Falls back to the
    weighted-average ``holding_days`` (whole sell into one bucket) only
    when no breakdown is present.
    """
    gain = float(txn.get("realized_gain", 0) or 0)
    parsed = _parse_option_symbol(txn.get("symbol", ""))
    if parsed and parsed["underlying"] in SECTION_1256_UNDERLYINGS:
        return {"st": gain * 0.4, "lt": gain * 0.6, "s1256": gain, "kind": "1256"}
    breakdown = txn.get("lot_breakdown")
    if breakdown:
        st = lt = 0.0
        for lot in breakdown:
            lot_gain = (float(lot.get("proceeds", 0) or 0)
                        - float(lot.get("cost_basis", 0) or 0))
            if _is_long_term(lot.get("date_acquired"), txn.get("date"),
                             lot.get("days")):
                lt += lot_gain
            else:
                st += lot_gain
        return {"st": st, "lt": lt, "s1256": 0.0, "kind": "normal"}
    days = txn.get("holding_days")
    is_lt = _is_long_term(None, None, days)
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
    # ---- 2026 (Rev. Proc. 2025-32, reflects OBBBA's extra bump to the
    # 10%/12% brackets) ----
    (2026, "Single"): [
        (12400, 0.10), (50400, 0.12), (105700, 0.22),
        (201775, 0.24), (256225, 0.32), (640600, 0.35), (math.inf, 0.37)],
    (2026, "Married Filing Jointly"): [
        (24800, 0.10), (100800, 0.12), (211400, 0.22),
        (403550, 0.24), (512450, 0.32), (768700, 0.35), (math.inf, 0.37)],
    (2026, "Married Filing Separately"): [
        (12400, 0.10), (50400, 0.12), (105700, 0.22),
        (201775, 0.24), (256225, 0.32), (384350, 0.35), (math.inf, 0.37)],
    (2026, "Head of Household"): [
        (17700, 0.10), (67450, 0.12), (105700, 0.22),
        (201775, 0.24), (256200, 0.32), (640600, 0.35), (math.inf, 0.37)],
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
    # 2026 (Rev. Proc. 2025-32)
    (2026, "Single"):                    [(49450, 0.00), (545500, 0.15), (math.inf, 0.20)],
    (2026, "Married Filing Jointly"):    [(98900, 0.00), (613700, 0.15), (math.inf, 0.20)],
    (2026, "Married Filing Separately"): [(49450, 0.00), (306850, 0.15), (math.inf, 0.20)],
    (2026, "Head of Household"):         [(66200, 0.00), (579600, 0.15), (math.inf, 0.20)],
}

_STD_DED_BY_YEAR_STATUS: dict[tuple[int, str], float] = {
    (2024, "Single"):                    14600,
    (2024, "Married Filing Jointly"):    29200,
    (2024, "Married Filing Separately"): 14600,
    (2024, "Head of Household"):         21900,
    # 2025 figures are the OBBBA (July 2025) amounts, which retroactively
    # replaced the originally-announced inflation adjustments
    # (15000 / 30000 / 22500).
    (2025, "Single"):                    15750,
    (2025, "Married Filing Jointly"):    31500,
    (2025, "Married Filing Separately"): 15750,
    (2025, "Head of Household"):         23625,
    # 2026 (Rev. Proc. 2025-32)
    (2026, "Single"):                    16100,
    (2026, "Married Filing Jointly"):    32200,
    (2026, "Married Filing Separately"): 16100,
    (2026, "Head of Household"):         24150,
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
# of contributions extrapolated from YTD pace.
_K401_LIMIT_BY_YEAR: dict[int, float] = {
    2018: 18500, 2019: 19000, 2020: 19500, 2021: 19500, 2022: 20500,
    2023: 22500, 2024: 23000, 2025: 23500, 2026: 24500,
}

# Roth IRA MAGI phase-out windows by filing status.  `start` is the MAGI
# at which the contribution limit begins to phase out; full phase-out is
# `start + width`.  (MFS phases out over $0..$10k regardless of year.)
# Drives the Retirement tab's Roth Eligibility column.
_ROTH_MAGI_PHASEOUT_BY_STATUS: dict[str, dict] = {
    "Single": {
        "start": {2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
                  2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
                  2026: 153000},
        "width": 15000,
    },
    "Head of Household": {
        "start": {2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
                  2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
                  2026: 153000},
        "width": 15000,
    },
    "Married Filing Jointly": {
        "start": {2018: 189000, 2019: 193000, 2020: 196000, 2021: 198000,
                  2022: 204000, 2023: 218000, 2024: 230000, 2025: 236000,
                  2026: 242000},   # Notice 2025-67: $242k–$252k
        "width": 10000,
    },
    "Married Filing Separately": {
        "start": {y: 0 for y in range(2018, 2027)},
        "width": 10000,
    },
}


def _brackets_table_to_json(table: dict) -> dict:
    """Convert a {(year, status): [(threshold, rate), ...]} table to the
    JS-shaped {year_str: {status: [[threshold|null, rate], ...]}}.  The
    top bracket's `math.inf` threshold becomes JSON `null` (the dashboard
    converts it back to Infinity) so we never emit a non-finite literal."""
    out: dict = {}
    for (yr, status), rows in table.items():
        out.setdefault(str(yr), {})[status] = [
            [None if (isinstance(thr, float) and math.isinf(thr)) else thr, rate]
            for thr, rate in rows
        ]
    return out


def tax_tables_to_json() -> dict:
    """Serialize the canonical federal tax tables for the JSON export.

    This is the **single source of truth** for the bracket / LTCG /
    standard-deduction / 401(k)-limit / Roth-MAGI / §1256 reference data.
    The dashboard JS reads these from ``DATA.tax_tables`` instead of
    keeping its own hardcoded copies — so adding a new tax year touches
    only this file.  Embedded by ``export.export_json`` (mirrors the
    ``action_catalog`` pattern)."""
    std_deduction: dict = {}
    for (yr, status), amt in _STD_DED_BY_YEAR_STATUS.items():
        std_deduction.setdefault(str(yr), {})[status] = amt
    return {
        "federal_brackets": _brackets_table_to_json(_BRACKETS_BY_YEAR_STATUS),
        "ltcg_brackets":    _brackets_table_to_json(_LTCG_BY_YEAR_STATUS),
        "std_deduction":    std_deduction,
        "k401_limit": {str(y): v for y, v in _K401_LIMIT_BY_YEAR.items()},
        "roth_magi_phaseout": _ROTH_MAGI_PHASEOUT_BY_STATUS,
        "section_1256_underlyings": sorted(SECTION_1256_UNDERLYINGS),
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
        # Portfolio income for AGI: dividends / interest / rewards earned
        # OUTSIDE retirement wrappers only.  Dividends inside a 401K / IRA
        # are tax-deferred (or tax-free) and never hit AGI — counting them
        # overstated MAGI right at the Roth phase-out edge.  Savings-account
        # interest IS taxable (1099-INT) and stays in.
        if (t.get("action") in INCOME_ACTION_KINDS
                and t.get("account_type") != "Retirement"):
            portfolio_income_ytd += float(t.get("amount", 0) or 0)
        info = classify_retirement_contribution(t)
        if info["is_contrib"] and t.get("account_group") in ("401K", "Rollover IRA"):
            # Only the employee's elective deferral reduces W-2 wages.
            # Employer match / safe-harbor money (Voya tags the Money
            # Source in the description) is not an AGI deduction — skip it.
            desc = (t.get("description") or "").lower()
            if "employer" not in desc and "match" not in desc:
                # info["amount"] carries the sign: negative for
                # Contribution Reversal rows, which must net out.
                k401_ytd += float(info.get("amount", 0) or 0)
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

    # Broker-authoritative override: for any account with a Reconcile
    # Realized row this year, trust the broker's reported gain over fin's
    # reconstruction (fin can't see off-platform cost basis).  Drives AGI /
    # MAGI / Roth eligibility / cap-gains tax below.
    _st_d, _lt_d, _override_accts = _realized_override_delta(
        txns, retirement_meta, year_str)
    realized_st += _st_d
    realized_lt += _lt_d

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
        is_top = cap == float("inf")
        bracket_fill.append({
            "rate":  rate,
            "lower": round(prev_cap, 2),
            "upper": round(cap, 2) if not is_top else None,
            "in_bracket":  round(in_this, 2),
            # Top bracket has no ceiling → room is unbounded; emit None
            # (not inf, which would serialize as an invalid JSON literal
            # and leak Infinity into the dashboard — same class as the
            # NaN price-cache bug).
            "room_left":   None if is_top else round(room, 2),
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
        if b["room_left"] and b["room_left"] > 0:   # None (top bracket) is falsy
            headroom_to_next = b["room_left"]
            # Next bracket = the one immediately after this row
            idx = bracket_fill.index(b)
            if idx + 1 < len(bracket_fill):
                next_rate = bracket_fill[idx + 1]["rate"]
            break

    # --- Estimated tax on YTD realized capital gains ---------------------
    # The thing that drives surprise quarterly-estimated-tax obligations.
    # ST gains taxed at the marginal ordinary rate; LT (and the 60/40-split
    # §1256 portion, already folded into realized_st/lt by _classify_realized)
    # at the LTCG rate.  Plus a flat state rate (states generally tax gains
    # as ordinary income) and a simplified NIIT (3.8% on net investment
    # income above the MAGI threshold — AGI used as a MAGI proxy).
    marginal_short = _marginal_for(taxable_ordinary, brackets)
    marginal_long = _marginal_for(taxable_income, ltcg)
    state_rate = float((retirement_meta or {}).get("state_tax_rate", 0) or 0)
    est_fed = realized_st * marginal_short + realized_lt * marginal_long
    est_state = (realized_st + realized_lt) * state_rate
    # NIIT: 3.8% on the lesser of net investment income or (MAGI − threshold).
    niit_threshold = {
        "Single": 200000, "Head of Household": 200000,
        "Married Filing Jointly": 250000, "Married Filing Separately": 125000,
    }.get(status, 200000)
    net_investment_income = max(0.0, realized_st + realized_lt + portfolio_income)
    est_niit = 0.038 * min(net_investment_income, max(0.0, agi - niit_threshold))
    est_cap_gains_total = est_fed + est_state + est_niit

    return {
        "year": yr,
        "filing_status": status,
        "salary": round(salary, 2),
        "bonuses": round(bonuses, 2),
        "portfolio_income": round(portfolio_income, 2),
        "portfolio_income_ytd": round(portfolio_income_ytd, 2),
        "realized_st": round(realized_st, 2),
        "realized_lt": round(realized_lt, 2),
        # Accounts whose realized gain was overridden by a broker-reported
        # Reconcile Realized figure (off-platform basis fin can't see).
        "realized_override_accounts": _override_accts,
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
        "marginal_short": round(marginal_short, 4),
        "marginal_long": round(marginal_long, 4),
        "bracket_fill": bracket_fill,
        "headroom_to_next_bracket": round(headroom_to_next, 2),
        "next_bracket_rate": next_rate,
        # Estimated tax on YTD realized capital gains (federal + state +
        # NIIT) and a naive even-quarters suggested estimated payment.
        "est_cap_gains_tax_federal": round(est_fed, 2),
        "est_cap_gains_tax_state": round(est_state, 2),
        "est_niit": round(est_niit, 2),
        "est_cap_gains_tax_total": round(est_cap_gains_total, 2),
        "est_quarterly_payment": round(est_cap_gains_total / 4, 2),
        "state_tax_rate": round(state_rate, 4),
        "is_projection": bool(is_current_year and year_fraction < 1.0),
        "year_fraction_observed": round(year_fraction, 4),
    }


def compute_tax_analytics(txns: list[dict], holdings: list[dict],
                         retirement_meta: dict,
                         fifo_state: dict | None = None) -> dict:
    """Realized gains split by year / asset / underlying, harvest
    candidates, wash sales, per-year tax rate estimates, and the
    long-term-eligibility horizon for currently open taxable lots.

    ``fifo_state`` is the basis-walker state dict returned by
    ``basis.compute_basis_default`` — needed to enumerate open lots
    per (account_group, symbol) for the LT horizon table.
    """
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
        # Wash-sale deferral only matters for taxable-account losses —
        # a loss inside an IRA/401K is never deductible in the first
        # place, so flagging it is pure noise.  (Buys in ANY account,
        # including IRAs, still count as offending repurchases below —
        # the IRS applies the rule across accounts.)
        if t.get("account_type") not in (None, "Taxable"):
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

    lt_horizon = _compute_lt_horizon(fifo_state, holdings) if fifo_state else []

    return {
        "realized_by_year": realized_by_year,
        "realized_by_symbol": realized_by_symbol,
        "realized_by_underlying": realized_by_underlying,
        "harvest_candidates": harvest,
        "wash_sales": wash_sales,
        "rate_estimates_by_year": rate_estimates,
        "section_1256_underlyings": sorted(SECTION_1256_UNDERLYINGS),
        "lt_horizon": lt_horizon,
        "form_8949": _build_form_8949(realized),
    }


def _build_form_8949(realized: list[dict]) -> list[dict]:
    """Per-disposal rows for an IRS Form 8949-style realized-gains CSV.

    Only **taxable-account** disposals are reportable — retirement
    accounts aren't taxed on gains, so they're excluded.  Each row mirrors
    8949's columns: description, date acquired / sold, proceeds, cost
    basis, gain/loss, and term (short / long / §1256).

    When the FIFO ``lot_breakdown`` is present, a sell is emitted as **one
    row per consumed lot** with that lot's real acquired date and its own
    short/long term — matching how a broker's 1099-B itemizes a
    multi-lot disposal.  §1256 contracts stay a single row (60/40 rule).
    Without a breakdown we fall back to a single row whose acquired date
    is derived from the weighted-average ``holding_days`` (or ``VARIOUS``).
    """
    rows: list[dict] = []
    for t in realized:
        if t.get("account_type") != "Taxable":
            continue
        c = _classify_realized(t)
        sold = t.get("date", "")
        sym = t.get("symbol", "")
        account = t.get("account_group", "")
        breakdown = t.get("lot_breakdown")

        if c["kind"] != "1256" and breakdown:
            for lot in breakdown:
                proceeds = round(float(lot.get("proceeds", 0) or 0), 2)
                basis = round(float(lot.get("cost_basis", 0) or 0), 2)
                term = ("long" if _is_long_term(lot.get("date_acquired"),
                                                sold, lot.get("days"))
                        else "short")
                rows.append({
                    "description": f"{float(lot.get('qty', 0) or 0):g} {sym}".strip(),
                    "date_acquired": lot.get("date_acquired") or "VARIOUS",
                    "date_sold": sold,
                    "proceeds": proceeds,
                    "cost_basis": basis,
                    "gain": round(proceeds - basis, 2),
                    "term": term,
                    "account": account,
                })
            continue

        # §1256 or no per-lot detail: single row.
        days = t.get("holding_days")
        acquired = "VARIOUS"
        if isinstance(days, (int, float)) and days >= 0:
            sold_d = _parse_iso(sold)
            if sold_d:
                acquired = (sold_d - timedelta(days=int(days))).isoformat()
        if c["kind"] == "1256":
            term = "1256"
        else:
            term = "long" if _is_long_term(None, None, days) else "short"
        qty = float(t.get("quantity", 0) or 0)
        rows.append({
            "description": f"{qty:g} {sym}".strip(),
            "date_acquired": acquired,
            "date_sold": sold,
            "proceeds": round(float(t.get("amount", 0) or 0), 2),
            "cost_basis": round(float(t.get("cost_basis", 0) or 0), 2),
            "gain": round(float(t.get("realized_gain", 0) or 0), 2),
            "term": term,
            "account": account,
        })
    rows.sort(key=lambda r: (r["date_sold"], r["description"]))
    return rows


def _lt_eligible_date(open_d):
    """First calendar date on which selling this lot qualifies as
    long-term (held > 1 year, per IRS Topic 409).  Uses calendar-month
    math so Feb 29 (leap day) maps to Feb 28 of the next year, then +1
    day to land on Mar 1 — the standard convention.
    """
    try:
        anniversary = open_d.replace(year=open_d.year + 1)
    except ValueError:  # Feb 29 in a leap year
        anniversary = open_d.replace(year=open_d.year + 1, month=2, day=28)
    return anniversary + timedelta(days=1)


def _compute_lt_horizon(fifo_state: dict, holdings: list[dict]) -> list[dict]:
    """For every currently-open FIFO lot in a Taxable account, compute
    when it becomes long-term-eligible (held > 1 year).  Returns a
    list sorted by days_to_lt ascending — lots about to cross the
    threshold land at the top, lots already long-term at the bottom.

    Retirement-account lots are excluded (gains there aren't taxed
    short-term/long-term — they're either tax-deferred or tax-free
    inside the wrapper).  USD / cash lots are excluded.  §1256
    contracts (index options) are excluded since they get 60/40
    treatment regardless of holding period.

    Each row carries:
      - account_group, symbol, open_date, qty, cost_basis
      - lt_eligible_date, days_held, days_to_lt
      - is_long_term: bool
      - price (per-share) and value (qty × price) when available;
        unrealized = value − cost_basis
    """
    if not fifo_state or "lots" not in fifo_state:
        return []

    # Build a price lookup from the holdings table.  Holdings are
    # already keyed by (account_group, symbol) with a per-share `price`.
    price_by_key: dict[tuple[str, str], float | None] = {}
    for h in holdings or []:
        key = (h.get("account_group", ""), h.get("symbol", ""))
        # Holdings table is per-symbol (account_group not set there in
        # the top-level rollup) AND per-(account, symbol) — try both.
        price_by_key[key] = h.get("price")
        sym_only = ("", h.get("symbol", ""))
        if sym_only not in price_by_key or price_by_key[sym_only] is None:
            price_by_key[sym_only] = h.get("price")

    today = datetime.now().date()
    rows: list[dict] = []
    for (acct, sym), lots in fifo_state["lots"].items():
        # Skip retirement (no ST/LT distinction inside the wrapper).
        if ACCOUNT_TYPES.get(acct) == "Retirement":
            continue
        # Skip cash + options (1256 has its own rule; non-1256 options
        # are typically short-dated and the 1y horizon is irrelevant).
        if sym in CASH_SYMBOLS or not sym:
            continue
        if _is_option_symbol(sym):
            continue
        if not isinstance(lots, list):
            continue
        # Per-share price (try (acct, sym) first, then symbol-only).
        price = price_by_key.get((acct, sym))
        if price is None:
            price = price_by_key.get(("", sym))
        for lot in lots:
            qty = float(lot.get("qty", 0) or 0)
            if qty <= 1e-6:
                continue
            open_iso = lot.get("date", "")
            open_d = _parse_iso(open_iso) if open_iso else None
            if not open_d:
                continue
            lt_date = _lt_eligible_date(open_d)
            days_held = (today - open_d).days
            days_to_lt = (lt_date - today).days
            basis_per = float(lot.get("basis_per_share", 0) or 0)
            cost_basis = round(qty * basis_per, 2)
            value = round(qty * price, 2) if price else None
            unrealized = round(value - cost_basis, 2) if value is not None else None
            rows.append({
                "account_group": acct,
                "symbol": sym,
                "open_date": open_iso,
                "lt_eligible_date": lt_date.isoformat(),
                "days_held": days_held,
                "days_to_lt": days_to_lt,
                "is_long_term": days_to_lt <= 0,
                "qty": round(qty, 8),
                "cost_basis": cost_basis,
                "price": round(price, 4) if price else None,
                "value": value,
                "unrealized_gain": unrealized,
            })
    # ST lots first (smallest days_to_lt at top — most-imminent LT
    # crossings).  LT lots fall to the bottom of the list.
    rows.sort(key=lambda r: (r["is_long_term"], r["days_to_lt"]))
    return rows


# ---------------------------------------------------------------------------
# Performance tab extras — position returns, winners / losers
# ---------------------------------------------------------------------------
