"""Monte Carlo retirement projection.

The existing Retirement-tab projection assumes a constant rate of
return for each scenario (5% / 7% / 9%).  Reality has sequence-of-
returns risk: the path to a long-run average matters because early
losses compound differently than late losses.

This module runs N stochastic simulations using draws from a normal
distribution calibrated to historical S&P 500 stats (default
mean = 8%, stdev = 16%), accumulates results, and returns the
distribution of ending balances at the user's retirement age.

Output is suitable for a fan chart: P10 / P25 / P50 / P75 / P90
percentile bands of projected portfolio value over time.

Implementation notes:

- Pure stdlib (random, statistics, math) — no numpy dependency.
- Default 1000 simulations is plenty for stable percentiles and runs
  in well under a second.
- Results are deterministic given a fixed seed (configurable) so
  repeated pipeline runs produce identical output.
"""

from __future__ import annotations

from .. import clock
import math
import random
from datetime import datetime


_DEFAULT_RUNS = 1000
_DEFAULT_SEED = 42
# S&P 500 historical mean ~10%, stdev ~16% (1928-present, real terms
# ~7% / 16%).  We use a slightly conservative mean (8%) and the same
# stdev — gets you reasonable bands without being optimistic.
_DEFAULT_MEAN = 0.08
_DEFAULT_STDEV = 0.16


def compute_monte_carlo(
    *,
    current_balance: float,
    annual_contribution: float,
    years_to_retirement: int,
    runs: int = _DEFAULT_RUNS,
    mean: float = _DEFAULT_MEAN,
    stdev: float = _DEFAULT_STDEV,
    seed: int = _DEFAULT_SEED,
    cash_balance: float = 0.0,
    cash_yield: float = 0.04,
    fi_threshold: float | None = None,
) -> dict:
    """Run ``runs`` simulations of an annual-step random-walk.

    Each year:
      equity_bal  *= (1 + draw)              # market return on equity bucket
      cash_bal    *= (1 + cash_yield)        # deterministic yield on cash bucket
      total_bal   += annual_contribution      # added end of year (to equity)

    The ``cash_balance`` bucket models high-yield savings / money-market
    holdings that shouldn't be modeled as equity volatility.  Set to 0 to
    skip the cash bucket entirely (retirement-only mode).

    If ``fi_threshold`` is given, the result includes the year each
    percentile band first crosses the threshold (FIRE date estimate).

    Returns yearly P10/P25/P50/P75/P90 bands plus headline stats.
    """
    if years_to_retirement <= 0 or (current_balance + cash_balance) < 0:
        return {"runs": 0, "years": 0, "bands": [], "summary": {}}

    rng = random.Random(seed)
    n_years = int(years_to_retirement)
    # paths[year] = list of balance values at end of that year (across runs)
    paths: list[list[float]] = [[] for _ in range(n_years + 1)]

    for _ in range(runs):
        eq = current_balance
        ca = cash_balance
        paths[0].append(eq + ca)
        for y in range(1, n_years + 1):
            r = rng.gauss(mean, stdev)
            eq = eq * (1 + r) + annual_contribution
            if eq < 0:
                eq = 0   # absorbing barrier — can't go negative in real life
            ca = ca * (1 + cash_yield)
            paths[y].append(eq + ca)

    def _percentile(sorted_vals: list[float], p: float) -> float:
        """Percentile via linear interpolation (matches numpy default)."""
        if not sorted_vals:
            return 0.0
        idx = (len(sorted_vals) - 1) * p
        lo, hi = math.floor(idx), math.ceil(idx)
        if lo == hi:
            return sorted_vals[lo]
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    bands = []
    this_year = clock.now(fallback=datetime.now).year
    for y, vals in enumerate(paths):
        sv = sorted(vals)
        bands.append({
            "year_offset": y,
            "calendar_year": this_year + y,
            "p10":  round(_percentile(sv, 0.10), 2),
            "p25":  round(_percentile(sv, 0.25), 2),
            "p50":  round(_percentile(sv, 0.50), 2),
            "p75":  round(_percentile(sv, 0.75), 2),
            "p90":  round(_percentile(sv, 0.90), 2),
            "mean": round(sum(vals) / len(vals), 2) if vals else 0.0,
        })

    final = sorted(paths[-1])
    summary = {
        "runs":             runs,
        "years":            n_years,
        "mean_return":      mean,
        "stdev_return":     stdev,
        "annual_contribution": round(annual_contribution, 2),
        "starting_balance": round(current_balance + cash_balance, 2),
        "starting_equity":  round(current_balance, 2),
        "starting_cash":    round(cash_balance, 2),
        "cash_yield":       cash_yield,
        "p10_final":        round(_percentile(final, 0.10), 2),
        "p50_final":        round(_percentile(final, 0.50), 2),
        "p90_final":        round(_percentile(final, 0.90), 2),
        # Probability of ending below the simple "no growth" projection
        # = current_balance + annual_contribution * years.  Useful as a
        # sanity check that the ranges aren't pessimistic-only.
        "p_below_no_growth":
            sum(1 for v in final
                if v < (current_balance + cash_balance)
                       + annual_contribution * n_years) / runs,
    }

    # FIRE date: first year (per percentile band) at which the projected
    # balance crosses the FI threshold.  If never reached within the
    # window, returns None for that band.
    fire = None
    if fi_threshold is not None and fi_threshold > 0:
        def _first_crossing(percentile: float) -> int | None:
            for b in bands:
                # Each band has p10/p25/p50/p75/p90 — pick the one for
                # this percentile.  Year 0 is the starting balance.
                key = f"p{int(percentile * 100):02d}"
                if b[key] >= fi_threshold:
                    return b["year_offset"]
            return None
        fire = {
            "threshold":            round(fi_threshold, 2),
            "p10_first_year":       _first_crossing(0.10),
            "p25_first_year":       _first_crossing(0.25),
            "p50_first_year":       _first_crossing(0.50),
            "p75_first_year":       _first_crossing(0.75),
            "p90_first_year":       _first_crossing(0.90),
            "p_reaches_within_window":
                sum(1 for v in final if v >= fi_threshold) / runs,
        }
        summary["fire"] = fire

    return {
        "runs":    runs,
        "years":   n_years,
        "bands":   bands,
        "summary": summary,
        "fire":    fire,
    }
