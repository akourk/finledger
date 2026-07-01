"""Monthly P&L grid — year × month matrix of investment returns.

Gives more granularity than the annual table and surfaces seasonality
("how often is October a red month?") + worst/best month at a glance.

The figure shown is **return on investment**, not raw dollar P&L:

    monthly_return = (end_value - start_value - net_cash_flow) / start_value

This isolates investment performance from contributions/withdrawals
within the month, matching the same framing as compute_history's
benchmark series.

Output::

    {
      "rows":  [{"year": 2024, "months": {1: 0.0234, 2: -0.0118, ...}}, ...],
      "min_return": -0.18,
      "max_return":  0.14,
      "best_month":  {"year": 2024, "month": 11, "return": 0.142},
      "worst_month": {"year": 2022, "month":  9, "return": -0.182},
    }
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, date


# Trailing-window definitions used for the Performance tab's
# window selector.  Lifetime stays as the headline; the others let
# the user see "what is this portfolio doing recently?" without
# having the early-portfolio history dominate.
#
# Values:
#   None            → lifetime (no slice)
#   int             → trailing N months
#   "ytd"           → calendar year-to-date (months in the latest snapshot's year)
_WINDOW_MONTHS = {
    "lifetime": None,
    "5y":       60,
    "3y":       36,
    "2y":       24,
    "1y":       12,
    "6mo":       6,
    "3mo":       3,
    "30day":     1,   # ~1 month at monthly snapshot granularity
    "ytd":       "ytd",
}


def _ratios_from_returns(all_monthly_returns: list[float],
                          significant_returns: list[float],
                          rf_monthly: float = 0.04 / 12) -> dict:
    """Sharpe / Sortino / cumulative+annualized return from monthly returns.

    Two inputs:
    - ``all_monthly_returns``: every month in the window — used for
      cumulative chain-link and annualization.  Doesn't filter; the
      total return path includes whatever happened.
    - ``significant_returns``: the subset where start_value was
      above the small-base threshold — used for Sharpe/Sortino so
      the stdev isn't dominated by tiny-denominator percentage
      swings.  Same filter as the legacy headline ratio.

    Returns ``{sharpe, sortino, n_months, n_months_in_ratio,
    cumulative, annualized}``; any field is None when undefined.

    Cumulative return is the geometric chain-link of (1 + r);
    annualized scales it to per-year using the actual month count
    (so a 5-month window doesn't claim a year of return).
    """
    out = {
        "sharpe": None, "sortino": None,
        "n_months": len(all_monthly_returns),
        "n_months_in_ratio": len(significant_returns),
        "cumulative": None, "annualized": None,
    }
    if not all_monthly_returns:
        return out

    cum = 1.0
    for r in all_monthly_returns:
        cum *= (1.0 + r)
    out["cumulative"] = round(cum - 1.0, 6)
    years = len(all_monthly_returns) / 12.0
    # When the cumulative return multiplier drops to zero or below
    # (catastrophic drawdown — possible in synthetic samples or
    # heavily-bearish windows), a fractional exponent yields a
    # complex number that round() can't handle.  Clamp to -100%.
    if years > 0:
        if cum > 0:
            out["annualized"] = round(cum ** (1.0 / years) - 1.0, 6)
        else:
            out["annualized"] = -1.0

    if len(significant_returns) >= 6:
        excess = [r - rf_monthly for r in significant_returns]
        mean_excess = sum(excess) / len(excess)
        var = sum((e - mean_excess) ** 2 for e in excess) / max(1, len(excess) - 1)
        sd = var ** 0.5
        if sd > 0:
            out["sharpe"] = round(mean_excess / sd * (12 ** 0.5), 3)
        downside = [e for e in excess if e < 0]
        if len(downside) >= 2:
            ds_var = sum(e * e for e in downside) / len(downside)
            ds_sd = ds_var ** 0.5
            if ds_sd > 0:
                out["sortino"] = round(mean_excess / ds_sd * (12 ** 0.5), 3)
    return out


def compute_monthly_pnl(history: list[dict], txns: list[dict],
                        bridges: list[dict] | None = None) -> dict:
    if len(history) < 2:
        return {"rows": [], "min_return": 0.0, "max_return": 0.0,
                "best_month": None, "worst_month": None}

    # Rollover bridges: add in-flight custodial-transfer cash back to
    # each snapshot's total.  Without this, the transfer-out month reads
    # as a huge phantom loss and the arrival month as a huge phantom
    # gain (net_contributed excludes rollovers by design, so the flow
    # term doesn't absorb it) — corrupting the heatmap, best/worst
    # month, and (when under the magnitude cap) Sharpe/Sortino.
    from ._shared import bridge_adjustment

    def _val(h: dict) -> float:
        return (float(h.get("total") or 0)
                + bridge_adjustment(h.get("date", ""), None, bridges or []))

    # Index history by (year, month) — pick the latest snapshot per month.
    by_ym: dict[tuple[int, int], dict] = {}
    for h in history:
        d = h.get("date") or ""
        try:
            dt = date.fromisoformat(d)
        except ValueError:
            continue
        key = (dt.year, dt.month)
        # Latest snapshot wins (history is roughly monthly-sampled but
        # the final entry is "today"; keep it).
        if key not in by_ym or d > by_ym[key].get("date", ""):
            by_ym[key] = h

    if len(by_ym) < 2:
        return {"rows": [], "min_return": 0.0, "max_return": 0.0,
                "best_month": None, "worst_month": None}

    # All-time peak portfolio value — used as the denominator for the
    # "is this month's start_value too small to be meaningful?" filter
    # below.  Self-scaling: works the same for a $10k portfolio and a
    # $10M portfolio.
    peak_value = max(_val(h) for h in history) or 1.0
    # Months where start_value < this threshold are kept in the heatmap
    # (the math is correct, just on a tiny base) but excluded from the
    # Sharpe/Sortino calculation, where extreme percentage swings on a
    # near-zero denominator would dominate the std dev and corrupt the
    # ratio.  1% of peak is judgment but defensible.
    SIGNIFICANT_THRESHOLD = peak_value * 0.01
    # Sanity cap on monthly return magnitude.  Any value beyond ±50%
    # over a diversified portfolio in a single month is almost
    # certainly a data artifact (e.g. parser-synthesized cash legs that
    # double-count, transfer pairs miscategorized as contributions, a
    # corp action that moves the basis without moving the value).
    # Excluded from Sharpe/Sortino but kept in the heatmap (so the
    # user can see and investigate).
    MAGNITUDE_CAP = 0.50

    # Walk months in order, compute month-over-month return
    sorted_keys = sorted(by_ym.keys())
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    best = {"year": None, "month": None, "return": float("-inf")}
    worst = {"year": None, "month": None, "return": float("inf")}
    min_r = 0.0
    max_r = 0.0
    significant_returns: list[float] = []   # only months above the threshold

    for i, key in enumerate(sorted_keys):
        if i == 0:
            continue
        prev_key = sorted_keys[i - 1]
        prev = by_ym[prev_key]
        curr = by_ym[key]
        start_value = _val(prev)
        end_value   = _val(curr)
        if start_value <= 0:
            continue
        # Net external cash flow for the month = change in cumulative
        # net_contributed between the two snapshots.  Using the
        # snapshot field rather than re-summing per-txn cash actions
        # avoids double-counting Coinbase-style synthetic "Deposit USD"
        # rows that mirror crypto sells (the sell + auto-deposit pair
        # is intra-account, not external money).
        flow = (float(curr.get("net_contributed") or 0)
                - float(prev.get("net_contributed") or 0))
        ret = (end_value - start_value - flow) / start_value
        # Round to 6 decimals so JSON serialization stays readable
        r = round(ret, 6)
        by_year[key[0]][key[1]] = r
        # Both filters must pass: significant denominator AND plausible
        # magnitude.  This protects the ratio calc from data artifacts
        # while leaving the heatmap untouched.
        if start_value >= SIGNIFICANT_THRESHOLD and abs(r) <= MAGNITUDE_CAP:
            significant_returns.append(r)
            if r > best["return"]:
                best = {"year": key[0], "month": key[1], "return": r}
            if r < worst["return"]:
                worst = {"year": key[0], "month": key[1], "return": r}
            if r < min_r: min_r = r
            if r > max_r: max_r = r

    rows = [
        {"year": y, "months": dict(sorted(by_year[y].items()))}
        for y in sorted(by_year.keys())
    ]

    # Sharpe / Sortino — annualized risk-adjusted return, computed from
    # the significant_returns list (months where start_value ≥ 1% of
    # all-time peak).  Tiny-denominator months produce 50%+ swings that
    # would dominate stdev and make the ratios meaningless.
    # Sharpe  = (mean_excess_return) / stdev(excess) × sqrt(12)
    # Sortino = (mean_excess_return) / stdev(downside_only) × sqrt(12)
    # Risk-free benchmark: 4% annual ≈ 0.327% per month.  Ballpark T-bill
    # yield in 2024–2026; could be made configurable later.
    total_months = sum(len(y) for y in by_year.values())
    n_filtered_out = total_months - len(significant_returns)
    sharpe, sortino = None, None
    rf_monthly = 0.04 / 12
    if len(significant_returns) >= 6:
        excess = [r - rf_monthly for r in significant_returns]
        mean_excess = sum(excess) / len(excess)
        # Sample stdev (unbiased)
        var = sum((e - mean_excess) ** 2 for e in excess) / max(1, len(excess) - 1)
        sd = var ** 0.5
        if sd > 0:
            sharpe = round(mean_excess / sd * (12 ** 0.5), 3)
        # Downside-only stdev (Sortino): only deviations below 0 count
        downside = [e for e in excess if e < 0]
        if len(downside) >= 2:
            ds_var = sum(e * e for e in downside) / len(downside)
            ds_sd = ds_var ** 0.5
            if ds_sd > 0:
                sortino = round(mean_excess / ds_sd * (12 ** 0.5), 3)

    # Per-window metrics: trailing-N-months Sharpe/Sortino/cumulative/
    # annualized.  The lifetime window mirrors the headline numbers
    # above; shorter windows let the dashboard show "what is the
    # portfolio doing recently" without the early-portfolio history
    # dominating.
    #
    # We keep two parallel lists keyed on (year, month) so window
    # slicing is straightforward:
    #   - all_keyed: every month with a computable return (used for
    #     cumulative + annualized)
    #   - sig_keyed: subset above the small-base threshold + magnitude
    #     cap (used for Sharpe/Sortino so noisy early months don't
    #     corrupt the stdev)
    all_keyed: list[tuple[tuple[int, int], float]] = []
    sig_keyed: list[tuple[tuple[int, int], float]] = []
    for i, key in enumerate(sorted_keys):
        if i == 0:
            continue
        y, m = key
        r = by_year.get(y, {}).get(m)
        if r is None:
            continue
        all_keyed.append((key, r))
        prev = by_ym[sorted_keys[i - 1]]
        start_value = _val(prev)
        if start_value >= SIGNIFICANT_THRESHOLD and abs(r) <= MAGNITUDE_CAP:
            sig_keyed.append((key, r))

    windowed: dict[str, dict] = {}
    latest_year = all_keyed[-1][0][0] if all_keyed else None
    for label, spec in _WINDOW_MONTHS.items():
        if spec is None:
            all_slice = [r for _, r in all_keyed]
            sig_slice = [r for _, r in sig_keyed]
        elif spec == "ytd":
            # Year-to-date: every month in the latest snapshot's year.
            all_slice = [r for k, r in all_keyed if latest_year is not None and k[0] == latest_year]
            sig_slice = [r for k, r in sig_keyed if latest_year is not None and k[0] == latest_year]
        else:
            n_months = int(spec)
            all_slice = [r for _, r in all_keyed[-n_months:]]
            cutoff = all_keyed[-n_months][0] if len(all_keyed) >= n_months else (0, 0)
            sig_slice = [r for k, r in sig_keyed if k >= cutoff]
        windowed[label] = _ratios_from_returns(all_slice, sig_slice, rf_monthly)

    return {
        "rows":        rows,
        "min_return":  round(min_r, 6),
        "max_return":  round(max_r, 6),
        "best_month":  best if best["year"] is not None else None,
        "worst_month": worst if worst["year"] is not None else None,
        "sharpe":      sharpe,
        "sortino":     sortino,
        "n_months_total":      total_months,
        "n_months_in_ratio":   len(significant_returns),
        "n_months_filtered":   n_filtered_out,
        "significant_threshold_pct": 1.0,
        "significant_threshold_value": round(SIGNIFICANT_THRESHOLD, 2),
        "peak_value":  round(peak_value, 2),
        "risk_free_annual": 0.04,
        "windowed":    windowed,
    }
