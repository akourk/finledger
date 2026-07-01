"""Single source of truth for derived analytics.

Everything here used to be computed in-browser on the dashboard side,
which meant the same logic (e.g. "what counts as a contribution?")
existed in two or three places and drifted out of sync whenever we
fixed a bug in one of them.  Centralising these computations in Python
means:

- Bug fixes happen once.  USAA "Buy with CURRENT YEAR CONTRIBUTION" is
  recognised as a contribution by every consumer because they all pull
  from the same precomputed structure.
- The dashboard becomes mostly presentation code (tab routing,
  filtering, rendering).  Business logic lives here.
- Each derived value is computed from a well-defined pipeline input.
  The JSON export is a self-describing frozen snapshot.

The output of :func:`build_analytics` is added to the JSON export under
a top-level ``analytics`` key.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from ..basis import _basis_dollars, BASIS_EFFECTS  # noqa: F401
from ..config import ACCOUNT_TYPES, CASH_SYMBOLS
from ..prices import get_price, split_factor_since

# ---------------------------------------------------------------------------
# Shared constants / classifiers
# ---------------------------------------------------------------------------

RETIREMENT_GROUPS = frozenset({"401K", "Roth IRA", "Rollover IRA"})
# Accounts whose purpose is liquidity / emergency savings, not growth
# capital.  Excluded from the "Investments" combined filter so TWR
# comparisons vs. equity benchmarks don't get diluted by HYSA yield.
SAVINGS_GROUPS    = frozenset({"Apple Savings"})

# Actions that count as inflows/outflows of user cash for the account,
# and the income classification — all sourced from src/actions.py's
# catalog (single source of truth; see the `income` field on Action).
from ..actions import (
    CASH_ADD_ACTIONS as _CASH_ADD_ACTIONS,
    CASH_SUB_ACTIONS as _CASH_SUB_ACTIONS,
    INCOME_ACTION_KINDS as _INCOME_ACTION_KINDS,
)
CASH_ADD_ACTIONS = _CASH_ADD_ACTIONS
CASH_SUB_ACTIONS = _CASH_SUB_ACTIONS

# Actions treated as income (dividends, interest, rewards, lending rebates).
# Used by the cash summary and the Income tab aggregation.
INCOME_ACTION_KINDS = _INCOME_ACTION_KINDS


def _parse_iso(d: str):
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _year(d: str) -> str:
    return (d or "")[:4]


# ---------------------------------------------------------------------------
# Contribution classification
# ---------------------------------------------------------------------------

def classify_retirement_contribution(txn: dict) -> dict:
    """Classify a txn as a retirement contribution or not.

    Returns ``{"is_contrib": bool, "year": str, "amount": float}``.

    - Handles Schwab/Vanguard-style explicit ``Contribution`` or
      ``Deposit`` actions.
    - Handles USAA Victory Capital style where contributions come in as
      ``Buy`` rows with ``"CURRENT YEAR CONTRIBUTION"`` /
      ``"PRIOR YEAR CONTRIBUTION"`` in the Description field.  For
      "PRIOR YEAR", the reported year is shifted back one â€” useful for
      tracking against IRS annual limits by tax year.
    - Skips ``Transfer In`` for retirement accounts (those are custodian
      moves, not user contributions).
    """
    g = txn.get("account_group", "")
    if g not in RETIREMENT_GROUPS:
        return {"is_contrib": False}
    amount = float(txn.get("amount", 0) or 0)
    if amount <= 0:
        return {"is_contrib": False}

    desc = (txn.get("description") or "").lower()
    is_prior = "prior year contribution" in desc
    is_current = "current year contribution" in desc
    year = _year(txn.get("date", ""))

    action = txn.get("action", "")
    if action in ("Contribution", "Deposit"):
        return {"is_contrib": True, "year": year, "amount": amount, "source": "explicit"}
    # Plan-admin error reversal: treat as NEGATIVE contribution so the
    # year's total offsets the original.  Amount is stored abs() in the
    # ledger; we flip the sign here for annual-total math.
    if action == "Contribution Reversal":
        return {"is_contrib": True, "year": year, "amount": -amount, "source": "reversal"}
    # Transfer In on retirement accounts = custodian move â€” skip.
    if action == "Transfer In":
        return {"is_contrib": False}

    # USAA-style Buy with contribution marker
    if (is_prior or is_current) and g == "Roth IRA":
        attr_year = str(int(year) - 1) if (is_prior and year.isdigit()) else year
        return {
            "is_contrib": True,
            "year": attr_year,
            "amount": amount,
            "source": "usaa_marker",
            "cash_flow_year": year,   # actual calendar year the cash moved
        }

    return {"is_contrib": False}


# ---------------------------------------------------------------------------
# Rollover bridge detection
# ---------------------------------------------------------------------------

def detect_rollover_bridges(txns: list[dict]) -> list[dict]:
    """Find custodian rollovers where one brokerage ``Distribution``
    event is matched by a ``Transfer In`` on the same account_group
    within 90 days.  During this window, the "in-flight" cash is
    invisible to our balance tracking (main.py skips USD balance for
    non-Savings accounts), so snapshots look like a fake drop followed
    by a fake recovery.

    Returns ``[{group, start_date, end_date, amount}, ...]``.  Bridge
    consumers add ``amount`` to a snapshot's effective balance while
    ``start_date <= snap.date < end_date``.
    """
    # Sum Distribution amounts by (group, date)
    dist_by_key: dict[tuple[str, str], dict] = {}
    for t in txns:
        if t.get("action") != "Distribution":
            continue
        g = t.get("account_group", "")
        if g not in ("Rollover IRA", "Roth IRA"):
            continue
        date = t.get("date", "")
        if not date:
            continue
        key = (g, date)
        if key not in dist_by_key:
            dist_by_key[key] = {"group": g, "date": date, "amount": 0.0}
        dist_by_key[key]["amount"] += float(t.get("amount", 0) or 0)

    bridges: list[dict] = []
    used_tin: set[int] = set()
    for _, d in dist_by_key.items():
        if d["amount"] <= 0:
            continue
        d_date = _parse_iso(d["date"])
        if d_date is None:
            continue
        best = None   # (idx, rel_diff, days_diff, txn)
        for i, t in enumerate(txns):
            if i in used_tin:
                continue
            if t.get("action") != "Transfer In":
                continue
            if t.get("account_group") != d["group"]:
                continue
            t_date = _parse_iso(t.get("date", ""))
            if t_date is None or t_date <= d_date:
                continue
            delta_days = (t_date - d_date).days
            if delta_days > 90:
                continue
            amt = float(t.get("amount", 0) or 0)
            if amt <= 0:
                continue
            denom = max(amt, d["amount"])
            rel = abs(amt - d["amount"]) / denom
            if rel > 0.05:   # 5% tolerance for dividends/fees between sell and wire
                continue
            cand = (i, rel, delta_days, t)
            if (best is None
                    or cand[1] < best[1]
                    or (cand[1] == best[1] and cand[2] < best[2])):
                best = cand
        if best is not None:
            used_tin.add(best[0])
            bridges.append({
                "group": d["group"],
                "start_date": d["date"],
                "end_date": best[3]["date"],
                "amount": round(d["amount"], 2),
            })
    return bridges


def bridge_adjustment(snapshot_date: str, filter_groups: set | None,
                      bridges: list[dict]) -> float:
    """Dollars-in-transit on `snapshot_date` for accounts in `filter_groups`.

    Add to a snapshot's effective balance during the bridge window so
    TWR and annual returns don't see a phantom drop-and-recovery.
    """
    if not bridges or not snapshot_date:
        return 0.0
    total = 0.0
    for b in bridges:
        if filter_groups is not None and b["group"] not in filter_groups:
            continue
        if b["start_date"] <= snapshot_date < b["end_date"]:
            total += b["amount"]
    return total


# ---------------------------------------------------------------------------
# Cash flows per period â€” the one function every return calc uses
# ---------------------------------------------------------------------------

def net_cash_flow(txns: list[dict], start_excl: str, end_incl: str,
                 filter_groups: set | None) -> float:
    """Net cash flow in (start_excl, end_incl] for the given account filter.

    Delegates the per-txn classification to ``basis.txn_external_cash_flow``
    so the rule (incl. Contribution Reversal handling, Roth/Rollover-IRA
    Distribution carve-out, and USAA contribution-marker Buys) stays in
    one place.  This function only adds period filtering and account-
    group filtering on top.
    """
    from ..basis import txn_external_cash_flow
    net = 0.0
    for t in txns:
        date = t.get("date", "")
        if not date or date <= start_excl or date > end_incl:
            continue
        if filter_groups is not None and t.get("account_group") not in filter_groups:
            continue
        net += txn_external_cash_flow(t)
    return net


# ---------------------------------------------------------------------------
# Contributions by year + by group (consumed by Retirement & Performance tabs)
# ---------------------------------------------------------------------------

def contributions_by_year(txns: list[dict]) -> dict:
    """Retirement contributions keyed on tax-attribution year.

    For USAA "PRIOR YEAR CONTRIBUTION" rows, attribute to the prior
    calendar year (so IRS limit tracking aligns with tax year).  Rolls
    ``Rollover IRA`` contributions into the ``401K`` bucket since
    the user's Rollover IRA is legacy 401K money.

    Shape::

        {
          "2019": {"401K": 150.0, "Roth IRA": 6000.0, "total": 6150.0},
          ...
        }
    """
    out: dict[str, dict] = {}
    for t in txns:
        info = classify_retirement_contribution(t)
        if not info["is_contrib"]:
            continue
        y = info["year"]
        if not y:
            continue
        amt = info["amount"]
        if y not in out:
            out[y] = {"401K": 0.0, "Roth IRA": 0.0, "total": 0.0}
        if t.get("account_group") == "Roth IRA":
            out[y]["Roth IRA"] += amt
        else:
            # 401K + Rollover IRA (legacy 401K) â†’ one column for display
            out[y]["401K"] += amt
        out[y]["total"] += amt
    # Round for a clean JSON
    for y in out:
        for k in out[y]:
            out[y][k] = round(out[y][k], 2)
    return out


# ---------------------------------------------------------------------------
# Annual returns + TWR per filter
# ---------------------------------------------------------------------------

def _filter_value_fn(filter_groups: set | None):
    """Return a function mapping history snapshot -> value for this filter."""
    if filter_groups is None:
        return lambda h: float(h.get("total", 0) or 0)

    def v(h):
        by_group = h.get("by_account_group") or {}
        return sum(float(by_group.get(g, 0) or 0) for g in filter_groups)
    return v


def compute_annual_returns(txns: list[dict], history: list[dict],
                           bridges: list[dict], filter_groups: set | None) -> list[dict]:
    """Per-year start/end/net/$return/TWR%/SPY% for the filter.

    Year boundary uses the previous year's final snapshot as "start"
    (so Jan 1 â†’ Dec 31 coverage is captured; otherwise the first ~30
    days of a year are missed because monthly snapshots skip Jan 1â€“31).
    SPY % is raw market return (price change over the same window).
    TWR % is Modified-Dietz chain-linked per sub-period within the year.
    """
    if not history:
        return []

    # Group snapshots by year
    by_year: dict[str, list[dict]] = defaultdict(list)
    for h in history:
        y = _year(h.get("date", ""))
        if y:
            by_year[y].append(h)
    years = sorted(by_year.keys())

    base_val = _filter_value_fn(filter_groups)

    def val(h):
        return base_val(h) + bridge_adjustment(h.get("date", ""), filter_groups, bridges)

    rows: list[dict] = []
    for idx, y in enumerate(years):
        curr_snaps = by_year[y]
        # Use last snapshot of prior year as "start" (true Jan 1 proxy)
        start_snap = (by_year[years[idx - 1]][-1] if idx > 0 else curr_snaps[0])
        end_snap = curr_snaps[-1]

        start_value = val(start_snap)
        end_value = val(end_snap)
        net = net_cash_flow(txns, start_snap.get("date", ""), end_snap.get("date", ""), filter_groups)
        dollar_return = end_value - start_value - net
        denom = start_value + net / 2
        pct = (dollar_return / denom) * 100 if denom > 0 else None

        # Per-year TWR: chain Modified-Dietz sub-period returns across
        # the year's snapshot gaps.  Skips periods dominated by cash flow
        # noise (see _period_return for why).
        period_snaps = [start_snap] + curr_snaps if idx > 0 else curr_snaps
        twr_ret = _chain_link_return(period_snaps, val, txns, filter_groups)

        # SPY market return for the year (raw price change)
        spy0 = start_snap.get("benchmark_spy_price")
        spy1 = end_snap.get("benchmark_spy_price")
        spy_pct = ((spy1 / spy0) - 1) * 100 if (spy0 and spy1 and spy0 > 0) else None

        rows.append({
            "year": y,
            "start": round(start_value, 2),
            "end": round(end_value, 2),
            "net_contrib": round(net, 2),
            "dollar_return": round(dollar_return, 2),
            "pct": round(pct, 4) if pct is not None else None,
            "twr_pct": round(twr_ret * 100, 4) if twr_ret is not None else None,
            "spy_pct": round(spy_pct, 4) if spy_pct is not None else None,
        })
    return rows


def _period_return(prev: dict, curr: dict, val_fn, txns: list[dict],
                   filter_groups: set | None) -> float | None:
    """Modified-Dietz period return, with bootstrap-noise guards.

    Returns the period return as a fraction (e.g. 0.015 = 1.5%), or None
    if the period is too noisy to estimate (cash flow dwarfs share
    balance â€” typically early in a brokerage account before much of the
    cash has been invested).
    """
    sv = val_fn(prev)
    ev = val_fn(curr)
    net = net_cash_flow(txns, prev.get("date", ""), curr.get("date", ""), filter_groups)
    denom = sv + net / 2
    if denom <= 0:
        return None
    # Cash flow dwarfs share value on both sides â†’ bootstrap noise.
    max_bal = max(sv, ev)
    if max_bal > 0 and abs(net) > max_bal * 0.8:
        return None
    if denom < 100:
        return None
    r = (ev - sv - net) / denom
    if r <= -1:
        return None
    return r


def _chain_link_return(snaps: list[dict], val_fn, txns: list[dict],
                       filter_groups: set | None) -> float | None:
    """Chain-link per-sub-period returns into a cumulative return for
    the span covered by `snaps`.  Returns None if no valid period.

    **Small-base filter**: skip periods whose starting value is below 1%
    of the **trailing** peak (the highest value seen *so far*, not the
    whole window's all-time peak).  Without any filter, a small-base
    portfolio with high volatility produces asymmetric chain-linked
    returns (-56% then +65% = ×0.72, NOT ×1) that drag cumulative TWR
    negative even though the portfolio gained value.  Using the *trailing*
    peak — rather than the window's global max — is critical: an all-time
    peak makes a full-history chain skip every early month (when the
    portfolio was small but represented the user's *entire* capital at the
    time), which silently drops the early-years returns and makes the
    lifetime TWR disagree with the per-year table.  The trailing peak only
    skips a period once the portfolio has genuinely crashed to <1% of a
    level it actually reached.
    """
    if len(snaps) < 2:
        return None
    cumulative = 1.0
    any_valid = False
    peak_so_far = 0.0
    for i in range(1, len(snaps)):
        prev = snaps[i - 1]
        sv = val_fn(prev)
        if sv > peak_so_far:
            peak_so_far = sv
        if sv < peak_so_far * 0.01:
            continue   # crashed to <1% of a prior high — skip from chain
        r = _period_return(prev, snaps[i], val_fn, txns, filter_groups)
        if r is None:
            continue
        cumulative *= (1 + r)
        any_valid = True
    return cumulative - 1 if any_valid else None


def compute_twr_summary(txns: list[dict], history: list[dict],
                       bridges: list[dict], filter_groups: set | None) -> dict | None:
    """Cumulative + annualized TWR over the full history window for the
    given filter.  Paired with SPY market return over the same window.
    """
    if len(history) < 2:
        return None
    base_val = _filter_value_fn(filter_groups)

    def val(h):
        return base_val(h) + bridge_adjustment(h.get("date", ""), filter_groups, bridges)

    # Start from first snapshot where account has any meaningful value
    # or has received a contribution
    start_idx = 0
    while start_idx < len(history):
        h = history[start_idx]
        if val(h) > 0:
            break
        flow = net_cash_flow(txns, "", h.get("date", ""), filter_groups)
        if flow > 0:
            break
        start_idx += 1
    if start_idx >= len(history) - 1:
        return None

    snaps = history[start_idx:]
    cum = _chain_link_return(snaps, val, txns, filter_groups)
    if cum is None:
        return None
    start_d = _parse_iso(snaps[0].get("date", ""))
    end_d = _parse_iso(snaps[-1].get("date", ""))
    if not start_d or not end_d:
        return None
    years = (end_d - start_d).days / 365.25
    ann = None
    if years > 0 and (1 + cum) > 0:
        ann = (1 + cum) ** (1 / years) - 1

    # SPY market return over the same window
    spy0 = snaps[0].get("benchmark_spy_price")
    spy1 = snaps[-1].get("benchmark_spy_price")
    spy_cum = None
    spy_ann = None
    if spy0 and spy1 and spy0 > 0:
        spy_cum = (spy1 / spy0) - 1
        if years > 0 and (1 + spy_cum) > 0:
            spy_ann = (1 + spy_cum) ** (1 / years) - 1

    return {
        "cumulative": round(cum, 6),
        "annualized": round(ann, 6) if ann is not None else None,
        "years": round(years, 2),
        "start_date": snaps[0].get("date", ""),
        "end_date": snaps[-1].get("date", ""),
        "spy_cumulative": round(spy_cum, 6) if spy_cum is not None else None,
        "spy_annualized": round(spy_ann, 6) if spy_ann is not None else None,
    }


# ---------------------------------------------------------------------------
# True-Daily-TWR (Schwab-style) â€” used for retirement filters
# ---------------------------------------------------------------------------
#
# Modified Dietz (compute_twr_summary above) chains monthly-snapshot
# returns with a midpoint-weighted denominator.  It's robust and fast
# but approximates mid-period flows, so it's ~1-2 pp off a brokerage's
# daily-close valuation per year.
#
# True-Daily-TWR (what Schwab publishes as "Rate of return") breaks
# sub-periods at every cash flow event, prices the portfolio at
# close-of-day on each boundary, and uses ``(EV - NC) / SV - 1`` per
# sub-period.  With boundaries placed AT flow dates, NC is just the
# flow amount that landed at the sub-period's closing boundary â€” so
# the formula backs it out of EV before measuring return.
#
# We only run this for retirement filters.  For taxable accounts, fin
# deliberately skips USD balance tracking (broker CSVs underreport sell
# proceeds â€” see CLAUDE.md), so value_at_date is unreliable there.
# Retirement accounts are mostly mutual funds with auto-reinvest and
# don't accumulate uninvested cash in the same way, so the walk is
# clean enough to validate against Schwab's statement numbers.

# Sourced from src/actions.py â€” single source of truth (see Phase 2).
from ..actions import SUBTRACT_ACTIONS as _SUBTRACT_ACTIONS
from ..actions import NEUTRAL_ACTIONS as _NEUTRAL_ACTIONS


def _balance_sort_key(t: dict) -> tuple:
    """Match main.py's balance sort: date, then adds-before-subtracts.

    Required so same-day Transfer In posts before the matching Transfer
    Out (otherwise balances would dip negative intra-day and the
    value_at_date walk would undercount).
    """
    d = t.get("date", "")
    sub = 1 if t.get("action") in _SUBTRACT_ACTIONS else 0
    return (d, sub, t.get("account_group", ""), t.get("symbol", ""))


def _value_at_date(txns_sorted: list[dict], target: str,
                   filter_groups: frozenset | None,
                   bridges: list[dict]) -> float:
    """Portfolio value at close-of-day `target` for the given filter.

    Walks a pre-sorted txn list to build running balances per
    ``(account_group, symbol)``, prices each non-zero position from the
    daily price cache (with fallback to the most recent txn price for
    symbols the cache can't resolve â€” multi-word fund names, delisted
    tickers), and sums.  Applies the same USD-skipping and split
    adjustment policies as ``history.compute_history``.
    """
    balances: dict[tuple[str, str], float] = defaultdict(float)
    last_txn_price: dict[str, float] = {}

    for t in txns_sorted:
        d = t.get("date", "")
        if d > target:
            break
        acct = t.get("account_group", "")
        if filter_groups is not None and acct not in filter_groups:
            continue
        sym = t.get("symbol", "")
        action = t.get("action", "")
        qty = float(t.get("quantity", 0) or 0)
        price = float(t.get("price", 0) or 0)
        if sym and price > 0:
            last_txn_price[sym] = price
        # Skip USD balance tracking for non-Savings accounts (matches main.py).
        if sym in CASH_SYMBOLS and ACCOUNT_TYPES.get(acct) != "Savings":
            continue
        if action in _NEUTRAL_ACTIONS:
            continue
        if action in _SUBTRACT_ACTIONS:
            balances[(acct, sym)] -= qty
        else:
            balances[(acct, sym)] += qty

    total = 0.0
    for (acct, sym), qty in balances.items():
        if abs(qty) < 1e-9:
            continue
        if sym in CASH_SYMBOLS:
            total += qty
            continue
        px = get_price(sym, target)
        if px is not None:
            total += qty * split_factor_since(sym, target) * px
        else:
            fb = last_txn_price.get(sym)
            if fb is not None:
                total += qty * fb
    total += bridge_adjustment(target, filter_groups, bridges)
    return total


def _cash_flow_events(txns: list[dict], start_excl: str, end_incl: str,
                     filter_groups: frozenset | None) -> list[tuple[str, float]]:
    """List ``(date, net_flow)`` pairs (one entry per date with activity) in
    ``(start_excl, end_incl]``.  Flows on the same date are aggregated.

    Delegates per-txn classification to ``basis.txn_external_cash_flow``
    — the single source of truth ``net_cash_flow`` above also uses — so
    the daily-TWR boundaries see the exact same carve-outs
    (Contribution Reversal, Roth/Rollover Distribution skip, USAA
    marker Buys, Coinbase bank-funded-Buy detection).  A previous
    hand-rolled copy of the rule here silently missed the Coinbase
    carve-outs; harmless while daily TWR is retirement-only, but a
    drift trap the moment the filter set widens.
    """
    from ..basis import txn_external_cash_flow
    by_date: dict[str, float] = defaultdict(float)
    for t in txns:
        d = t.get("date", "")
        if not d or d <= start_excl or d > end_incl:
            continue
        if filter_groups is not None and t.get("account_group") not in filter_groups:
            continue
        flow = txn_external_cash_flow(t)
        if flow:
            by_date[d] += flow
    return sorted(by_date.items())


def compute_twr_daily_summary(txns: list[dict], history: list[dict],
                             bridges: list[dict],
                             filter_groups: frozenset | None) -> dict | None:
    """Schwab-style True-Daily-TWR over the natural window.

    Sub-periods are bounded by cash-flow events.  Within each
    sub-period, ``r = (EV - NC) / SV - 1`` where NC is the flow that
    landed at the closing boundary.  Chain-link, annualize, pair with
    SPY over the same window.
    """
    if len(history) < 2:
        return None

    base_val = _filter_value_fn(filter_groups)

    # Find the natural start â€” same criterion as compute_twr_summary
    start_idx = 0
    while start_idx < len(history):
        h = history[start_idx]
        if base_val(h) + bridge_adjustment(h.get("date", ""), filter_groups, bridges) > 0:
            break
        flow = net_cash_flow(txns, "", h.get("date", ""), filter_groups)
        if flow > 0:
            break
        start_idx += 1
    if start_idx >= len(history) - 1:
        return None

    start_date = history[start_idx].get("date", "")
    end_date = history[-1].get("date", "")

    txns_sorted = sorted(txns, key=_balance_sort_key)
    events = _cash_flow_events(txns, start_date, end_date, filter_groups)

    # Sub-period boundaries: start + every flow date + end (deduped).
    seen = set()
    boundaries: list[str] = []
    for d in [start_date, *[e[0] for e in events], end_date]:
        if d not in seen:
            seen.add(d)
            boundaries.append(d)
    flow_at = dict(events)

    values = [_value_at_date(txns_sorted, d, filter_groups, bridges) for d in boundaries]

    cumulative = 1.0
    any_valid = False
    for i in range(1, len(boundaries)):
        sv = values[i - 1]
        ev = values[i]
        nc = flow_at.get(boundaries[i], 0.0)
        if sv <= 0:
            continue
        # Schwab's formula: (EV - NC) / SV - 1.  NC is the flow at the
        # closing boundary, which is baked into EV (because value_at_date
        # on that day includes any same-day Buy that invested the flow).
        r = (ev - nc) / sv - 1
        if r <= -1:
            continue
        cumulative *= (1 + r)
        any_valid = True

    if not any_valid:
        return None
    cum = cumulative - 1
    sd, ed = _parse_iso(start_date), _parse_iso(end_date)
    years = (ed - sd).days / 365.25 if sd and ed else 0
    ann = None
    if years > 0 and (1 + cum) > 0:
        ann = (1 + cum) ** (1 / years) - 1

    # SPY over the same window (uses snapshot prices â€” close-of-day SPY
    # is already cached under benchmark_spy_price)
    spy0 = history[start_idx].get("benchmark_spy_price")
    spy1 = history[-1].get("benchmark_spy_price")
    spy_cum = None
    spy_ann = None
    if spy0 and spy1 and spy0 > 0:
        spy_cum = (spy1 / spy0) - 1
        if years > 0 and (1 + spy_cum) > 0:
            spy_ann = (1 + spy_cum) ** (1 / years) - 1

    return {
        "cumulative": round(cum, 6),
        "annualized": round(ann, 6) if ann is not None else None,
        "years": round(years, 2),
        "start_date": start_date,
        "end_date": end_date,
        "spy_cumulative": round(spy_cum, 6) if spy_cum is not None else None,
        "spy_annualized": round(spy_ann, 6) if spy_ann is not None else None,
        "sub_periods": len(boundaries) - 1,
        "method": "daily",
    }


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def _account_filter_sets(holdings_by_account: list[dict]) -> dict:
    """Enumerate the account filters we want to pre-compute analytics for.

    Returns ``{filter_name: frozenset | None}``.  `None` means "Total" (no filter).
    """
    accounts = sorted({h.get("account_group", "")
                       for h in holdings_by_account
                       if h.get("account_group")})
    filters: dict[str, frozenset | None] = {"Total": None}
    # Combined view for everything except liquidity/savings â€” the default
    # "portfolio performance" view you'd compare against an equity
    # benchmark.  Apple Savings yields dilute SPY comparisons because
    # they're measuring different things (liquidity vs growth).
    investments = set(accounts) - SAVINGS_GROUPS
    if investments and investments != set(accounts):
        filters["Investments"] = frozenset(investments)
    # Combined view for all retirement accounts
    retirement = RETIREMENT_GROUPS & set(accounts)
    if len(retirement) > 1:
        filters["Retirement"] = frozenset(retirement)
    # Combined view for after-tax investment accounts (Robinhood,
    # Coinbase, etc.).  Symmetric with Retirement.  Useful because
    # money moves freely between these with no tax / regulatory
    # friction (unlike retirement, which has contribution limits +
    # withdrawal penalties).  Only emitted when there are 2+ taxable
    # accounts — otherwise the single per-account row is enough.
    taxable = set(accounts) - RETIREMENT_GROUPS - SAVINGS_GROUPS
    if len(taxable) > 1:
        filters["Taxable"] = frozenset(taxable)
    # Individual accounts
    for a in accounts:
        filters[a] = frozenset([a])
    return filters


# ---------------------------------------------------------------------------
# Options (tab-level aggregations)
# ---------------------------------------------------------------------------
