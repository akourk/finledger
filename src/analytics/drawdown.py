"""Drawdown analytics — peak-to-trough decline series and stats.

Drawdown at time t = (current_value − running_max) / running_max.  Always
≤ 0.  The running max only moves up; whenever the portfolio sets a new
high-water mark, drawdown resets to 0.

Returned figures:

- ``series``:        ``[{date, drawdown_pct, running_peak, value}, ...]``
- ``max_drawdown``:  largest peak-to-trough drop in the window
- ``max_drawdown_window``:  ``{peak_date, trough_date, recovery_date, days, magnitude_pct}``
- ``current_drawdown_pct``:  drawdown right now (0 if at all-time high)
- ``windowed``:      per-trailing-window max drawdown (1y/3y/5y/lifetime)

Useful because returns alone hide risk: a 12% annualized return that
went through a 40% drawdown is psychologically different from a
steady 12% with no drawdown >5%.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


_WINDOW_MONTHS = {
    "lifetime": None,
    "5y":       60,
    "3y":       36,
    "2y":       24,
    "1y":       12,
    "6mo":       6,
    "3mo":       3,
    "30day":     1,
    "ytd":       "ytd",
}


def _max_dd_in_window(snapshots: list[dict], val_fn) -> dict:
    """Compute the deepest peak-to-trough decline that runs entirely
    within ``snapshots`` (a slice of the history).  Resets the
    running peak at the start of the slice so a high pre-window peak
    doesn't make every snapshot look like a drawdown.
    """
    out = {"magnitude_pct": 0.0, "peak_date": None,
           "trough_date": None, "n_snapshots": len(snapshots)}
    if not snapshots:
        return out
    running_peak = 0.0
    peak_date = None
    worst = 0.0
    worst_peak_date = None
    worst_trough_date = None
    for h in snapshots:
        v = val_fn(h)
        d = h.get("date") or ""
        if v >= running_peak:
            running_peak = v
            peak_date = d
            continue
        if running_peak <= 0:
            continue
        dd = (v - running_peak) / running_peak
        if dd < worst:
            worst = dd
            worst_peak_date = peak_date
            worst_trough_date = d
    if worst < 0:
        out["magnitude_pct"] = round(worst * 100, 2)
        out["peak_date"]     = worst_peak_date
        out["trough_date"]   = worst_trough_date
    return out


def compute_drawdown(history: list[dict],
                     bridges: list[dict] | None = None) -> dict:
    """Compute drawdown series and headline stats from the history
    snapshots.  Returns an empty result if history has < 2 points.

    ``bridges`` (rollover bridges from ``detect_rollover_bridges``) are
    added to each snapshot's total so a custodial rollover's in-flight
    window doesn't fabricate a portfolio-scale "drawdown" — the money
    never left, it was in transit between custodians.

    Max-drawdown computation filters out periods where the running
    peak is below 5% of all-time peak — drawdowns that ran on a
    portfolio under that size are dominated by early-portfolio
    volatility (often crypto roller-coasters on $X0k of capital)
    and aren't comparable to the current portfolio's risk profile.
    A $19k → $1.7k early crash is mathematically -91% but treating
    that as the "max drawdown" of a $400k+ portfolio is misleading
    — Calmar built on it makes the portfolio look uninvestable when
    today's drawdown risk is more like -10% to -20%.  The full
    series still flows to the chart so the shape stays visible.
    """
    if len(history) < 2:
        return {"series": [], "max_drawdown": 0.0,
                "max_drawdown_window": None,
                "current_drawdown_pct": 0.0}

    from ._shared import bridge_adjustment

    def _val(h: dict) -> float:
        return (float(h.get("total") or 0)
                + bridge_adjustment(h.get("date", ""), None, bridges or []))

    # All-time peak — 5% denominator filter excludes pre-growth
    # volatility from the headline max-drawdown figure.
    atl_peak = max(_val(h) for h in history) or 1.0
    significant_threshold = atl_peak * 0.05

    series = []
    running_peak = 0.0
    peak_date = history[0].get("date", "")
    max_dd = 0.0
    max_dd_peak_date = peak_date
    max_dd_trough_date = peak_date
    max_dd_recovery_date: str | None = None
    in_max_dd_window = False
    n_filtered = 0   # count of small-base periods skipped from max-dd

    # Track the peak that started each drawdown so we can identify
    # recovery (next time we touch the peak again).
    current_dd_peak = 0.0
    current_dd_peak_date = peak_date
    current_dd_trough = 0.0
    current_dd_trough_date = peak_date

    for h in history:
        v = _val(h)
        d = h.get("date", "")
        if v >= running_peak:
            running_peak = v
            peak_date = d
            current_dd_peak = v
            current_dd_peak_date = d
            current_dd_trough = v
            current_dd_trough_date = d
            dd_pct = 0.0
        else:
            dd_pct = (v - running_peak) / running_peak if running_peak > 0 else 0.0
            if v < current_dd_trough:
                current_dd_trough = v
                current_dd_trough_date = d

        # Capture the largest drawdown observed — but only if the
        # window peak is above the small-base threshold.  Otherwise
        # an early-portfolio crash from $19k → $1.7k dominates as
        # "max drawdown" forever despite the user having since grown
        # the portfolio 25x.
        if dd_pct < max_dd and current_dd_peak >= significant_threshold:
            max_dd = dd_pct
            max_dd_peak_date = current_dd_peak_date
            max_dd_trough_date = current_dd_trough_date
            in_max_dd_window = True
            max_dd_recovery_date = None
        elif dd_pct < max_dd:
            n_filtered += 1

        # Recovery: value back to the peak that started the max-DD window
        if in_max_dd_window and v >= current_dd_peak and d > max_dd_trough_date:
            max_dd_recovery_date = d
            in_max_dd_window = False

        series.append({
            "date":         d,
            "drawdown_pct": round(dd_pct, 6),
            "running_peak": round(running_peak, 2),
            "value":        round(v, 2),
        })

    # Days from peak to trough for the worst window
    from datetime import datetime
    def _days(a: str, b: str) -> int | None:
        try:
            return (datetime.strptime(b, "%Y-%m-%d").date()
                    - datetime.strptime(a, "%Y-%m-%d").date()).days
        except (ValueError, TypeError):
            return None

    max_dd_window = None
    if max_dd < 0:
        max_dd_window = {
            "peak_date":      max_dd_peak_date,
            "trough_date":    max_dd_trough_date,
            "recovery_date":  max_dd_recovery_date,
            "days_to_trough": _days(max_dd_peak_date, max_dd_trough_date),
            "days_to_recover": _days(max_dd_trough_date, max_dd_recovery_date)
                              if max_dd_recovery_date else None,
            "magnitude_pct":  round(max_dd * 100, 2),
        }

    # Per-window max drawdown.  Trailing-window slices of the history
    # so the "max drawdown in last 1 year" doesn't keep showing the
    # 2022 episode three years after recovery.  Lifetime mirrors the
    # headline above (small-base filter applies); shorter windows skip
    # the filter — the slice itself rules out early-portfolio noise.
    today = datetime.now().date()
    latest_iso = history[-1].get("date") or "" if history else ""
    latest_year = latest_iso[:4] if latest_iso else ""
    windowed: dict[str, dict] = {}
    for label, spec in _WINDOW_MONTHS.items():
        if spec is None:
            windowed[label] = {
                "magnitude_pct": round(max_dd * 100, 2) if max_dd < 0 else 0.0,
                "peak_date":     max_dd_window["peak_date"]   if max_dd_window else None,
                "trough_date":   max_dd_window["trough_date"] if max_dd_window else None,
                "n_snapshots":   len(history),
            }
            continue
        if spec == "ytd":
            cutoff = f"{latest_year}-01-01" if latest_year else ""
        else:
            cutoff = (today - timedelta(days=int(int(spec) * 30.5))).isoformat()
        slice_ = [h for h in history if (h.get("date") or "") >= cutoff]
        windowed[label] = _max_dd_in_window(slice_, _val)

    return {
        "series":               series,
        "max_drawdown":         round(max_dd, 6),
        "max_drawdown_window":  max_dd_window,
        "current_drawdown_pct": round(series[-1]["drawdown_pct"], 6) if series else 0.0,
        "small_base_threshold": round(significant_threshold, 2),
        "n_periods_below_threshold": n_filtered,
        "windowed":             windowed,
    }
