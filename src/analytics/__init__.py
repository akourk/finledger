"""Derived analytics package — single source of truth for dashboard figures.

Submodules (one per dashboard tab):

- :mod:`._shared`    — constants, classifiers, TWR core helpers
- :mod:`.options`    — options tab
- :mod:`.crypto`     — crypto tab
- :mod:`.income`     — income tab
- :mod:`.tax`        — tax tab
- :mod:`.positions`  — performance tab position rollups
- :mod:`.header`     — persistent top-bar summary

:func:`build_analytics` is the orchestrator — it runs each per-tab
computation and assembles the final payload embedded in the JSON
export.
"""

from __future__ import annotations

# Re-export everything from _shared so existing imports still work
# (e.g. `from src.analytics import classify_retirement_contribution`).
from .. import clock
from ._shared import (
    # The frozensets are DEFAULTS kept for back-compat; the functions are
    # the live classification (user `Account Type` metadata unioned with
    # them).  New code should call the functions.
    RETIREMENT_GROUPS, SAVINGS_GROUPS,
    retirement_groups, savings_groups,
    CASH_ADD_ACTIONS, CASH_SUB_ACTIONS, INCOME_ACTION_KINDS,
    classify_retirement_contribution,
    detect_rollover_bridges,
    bridge_adjustment, net_cash_flow,
    contributions_by_year,
    compute_annual_returns, compute_twr_summary, compute_twr_daily_summary,
    compute_money_weighted_return,
)
from .alerts import compute_alerts
from .changes import compute_changes
from .concentration import compute_concentration
from .crypto import compute_crypto_analytics
from .data_health import compute_data_health
from .daily_pnl import compute_daily_pnl
from .budget import compute_budget
from .drawdown import compute_drawdown
from .header import compute_header_summary
from .paycheck import compute_paycheck
from .income import compute_income_analytics
from .income_calendar import compute_income_calendar
from .lots import compute_open_lots
from .monthly_pnl import compute_monthly_pnl
from .monte_carlo import compute_monte_carlo, trailing_annual_contribution
from .options import compute_options_analytics
from .position_pnl import compute_position_pnl
from .positions import compute_position_returns
from .rebalancing import compute_rebalancing
from .reconcile import compute_reconciliation
from .tax import compute_tax_analytics
from .trading_heatmap import compute_trading_heatmap
from ._shared import _account_filter_sets


def _daily_totals(txns: list[dict], *, transit_issues=None) -> list[tuple[str, float]]:
    """Daily total-value series for the drawdown stats' resolution —
    see ``history.compute_daily_totals``.  Late import: analytics
    modules are also imported standalone in tests, and the history
    module pulls in the whole basis/broker_lots machinery."""
    from ..history import compute_daily_totals
    return compute_daily_totals(txns, transit_issues=transit_issues)


def build_analytics(txns: list[dict], history: list[dict],
                    holdings: list[dict],
                    holdings_by_account: list[dict],
                    retirement_meta: dict | None = None,
                    cash_summary: dict | None = None,
                    basis_methods: dict | None = None,
                    fifo_state: dict | None = None,
                    basis_totals: dict | None = None) -> dict:
    """Compute the full analytics payload embedded in the export JSON.

    This is the single source of truth for derived figures the
    dashboard displays.  The dashboard JS should prefer reading from
    this rather than recomputing.
    """
    from pathlib import Path
    from ..config import CACHE_DIR
    from ..return_flows import annotate_account_transfers, scope_snapshot_value

    # Recompute price-dependent account-boundary verdicts on both full and
    # refresh paths before any return consumer reads them.
    annotate_account_transfers(txns)
    bridges = detect_rollover_bridges(txns)
    contribs_yr = contributions_by_year(txns)

    filters = _account_filter_sets(holdings_by_account)
    performance_by_filter: dict = {}
    for name, filt in filters.items():
        entry = {
            "annual":  compute_annual_returns(txns, history, bridges, filt),
            "summary": compute_twr_summary(txns, history, bridges, filt),
            # Money-weighted (XIRR) alongside TWR: what the user's
            # dollars earned, contribution timing included.
            "money_weighted": compute_money_weighted_return(
                txns, history, bridges, filt),
            "filter_groups": sorted(filt) if filt else None,
        }
        # True-Daily-TWR for retirement filters only.  See
        # compute_twr_daily_summary for why we skip taxable accounts.
        is_retirement_filter = (
            name == "Retirement"
            or (filt is not None and set(filt) <= retirement_groups())
        )
        if is_retirement_filter:
            daily = compute_twr_daily_summary(txns, history, bridges, filt)
            if daily is not None:
                entry["summary_daily"] = daily
        performance_by_filter[name] = entry

    tax = compute_tax_analytics(txns, holdings, retirement_meta or {},
                                  fifo_state=fifo_state)
    concentration = compute_concentration(holdings_by_account)

    # Monte Carlo projections — two scenarios:
    #   Retirement-only:  401K + IRAs, contribution = avg attributed
    #                     retirement contributions, no cash bucket
    #                     (these accounts are 100% equity by assumption).
    #   All-accounts:     full portfolio, contribution = trailing-3yr
    #                     average net cash in (across all accounts),
    #                     cash bucket = Savings + USD positions.
    # FIRE info attaches to the All-accounts scenario only — that's the
    # money actually available to spend.  4% rule × annual_expenses
    # gives the FI threshold; first-crossing year per percentile band.
    monte_carlo = None
    rm = retirement_meta or {}
    bd = (rm.get("birthday") or "").strip() if isinstance(rm.get("birthday"), str) else ""
    if bd:
        from datetime import datetime as _dt
        try:
            birth = _dt.strptime(bd, "%Y-%m-%d").date()
            today = clock.now(fallback=_dt.now).date()
            # Calendar age — days//365 drifts by the accumulated leap
            # days (reports the birthday a week early by your 30s),
            # which shifts the Monte Carlo horizon a year at the edges.
            age = (today.year - birth.year
                   - ((today.month, today.day) < (birth.month, birth.day)))
            # Monte Carlo horizon = years until the user's target
            # retirement age (data/metadata.csv `Retirement Age`,
            # default 67).  Naming the local `years_to_60` is
            # vestigial from when this was hardcoded to age 60.
            target_age = int(rm.get("retirement_age") or 67)
            years_to_60 = max(0, target_age - age)
            if years_to_60 > 0:
                # Annual retirement contribution = average of last 3
                # years of attributed contributions, falling back to the
                # 2024 IRS limit if no history.
                contribs = contribs_yr or {}
                yrs = sorted(contribs.keys())[-3:]
                if yrs:
                    avg_ret_contrib = sum(contribs[y].get("total", 0) for y in yrs) / len(yrs)
                else:
                    avg_ret_contrib = 23000   # 2024 401K limit

                # Current retirement balance from latest snapshot
                last = history[-1] if history else {}
                ret_value = scope_snapshot_value(last, retirement_groups())

                # All-accounts metrics
                total_value = scope_snapshot_value(last)
                # Cash bucket = the Cash sector, which already covers every
                # USD position portfolio-wide: Savings-account balances
                # (sector_of["USD"] = "Cash" hard rule), the Coinbase USD
                # bridge, and any other cash-like holding.  Do NOT also add
                # the Savings account groups — their value IS their USD
                # position, so summing both double-counted the HYSA and
                # inverted the cash/equity split in the simulation.
                cash_value = float((last.get("by_sector") or {}).get("Cash", 0))
                # Defensive cap so a rounding artifact can't push equity
                # negative.
                cash_value = min(cash_value, total_value)
                equity_value = max(0.0, total_value - cash_value)

                # Trailing-3-year average annual net contribution (all
                # accounts) — pulled from history snapshots'
                # ``net_contributed`` cumulative field.
                avg_total_contrib = trailing_annual_contribution(
                    history, avg_ret_contrib)

                # FIRE threshold: most-recent annual_expenses entry × 25
                # (the classic 4% safe-withdrawal-rate rule).
                fi_threshold = None
                mc_annual_expenses = None
                ann_exp_list = rm.get("annual_expenses") or []
                if ann_exp_list:
                    latest = max(ann_exp_list, key=lambda x: x.get("date", ""))
                    if latest.get("amount"):
                        mc_annual_expenses = float(latest["amount"])
                        fi_threshold = mc_annual_expenses * 25

                mc_retirement = compute_monte_carlo(
                    current_balance=ret_value,
                    annual_contribution=avg_ret_contrib,
                    years_to_retirement=years_to_60,
                )
                mc_all = compute_monte_carlo(
                    current_balance=equity_value,
                    annual_contribution=avg_total_contrib,
                    years_to_retirement=years_to_60,
                    cash_balance=cash_value,
                    fi_threshold=fi_threshold,
                )
                monte_carlo = {
                    "retirement":   mc_retirement,
                    "all_accounts": mc_all,
                    "fi_threshold": fi_threshold,
                    # Same latest-by-date row the fi_threshold uses —
                    # ann_exp_list[-1] (file order) disagreed with it
                    # whenever the metadata rows weren't date-sorted.
                    "annual_expenses": mc_annual_expenses,
                }
        except (ValueError, TypeError):
            monte_carlo = None

    # Prepare the next activity baseline without writing it. Only successful
    # dashboard publication advances it, using the same totals as the export.
    if basis_totals is None:
        from ..pipeline_stages import build_annotated_basis_totals
        basis_totals = build_annotated_basis_totals(
            holdings or holdings_by_account, fifo_state)
        if fifo_state is None:
            basis_totals["realized_gain"] = None
    changes = compute_changes(txns, holdings_by_account, basis_totals, CACHE_DIR)

    # CUSIP collisions surface in main.py as console output.  For the
    # alerts panel we recompute (cheap) so it lands in the JSON too.
    from ..cusips import detect_collisions
    coll = detect_collisions(txns)
    rename_for_alerts = []
    rules = []
    try:
        from ..parsers import _load_ticker_renames as _ldr
        rules = (_ldr() or {}).get("Robinhood", []) or []
    except (ImportError, OSError, ValueError):
        # ImportError: shim missing in some test environments.
        # OSError: ticker_renames.json missing or unreadable.
        # ValueError: parents JSONDecodeError when the file is malformed.
        pass
    covered = {(r.get("from"), r.get("to")) for r in rules
               if isinstance(r, dict) and r.get("from") and r.get("to")}
    for r in coll.get("rename_candidates", []):
        for stale in r["stale"]:
            if (stale, r["canonical"]) not in covered:
                rename_for_alerts.append({
                    "stale": stale, "canon": r["canonical"], "cusip": r["cusip"],
                })

    # Load price meta for the alerts module's failing-tickers signal
    price_meta = {}
    try:
        import json as _json
        meta_path = CACHE_DIR / "price_cache_meta.json"
        if meta_path.exists():
            price_meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # OSError: file vanished between exists() check and read_text().
        # ValueError: parent of JSONDecodeError when the file is malformed.
        pass

    alerts = compute_alerts(
        txns=txns,
        holdings_by_account=holdings_by_account,
        history=history,
        tax_analytics=tax,
        concentration=concentration,
        price_meta=price_meta,
        cusip_collisions=rename_for_alerts,
    )

    # Current-year tax estimate — the paycheck panel reads its projected
    # employee 401(k) deferral (and projection flag) from here.
    from datetime import date as _date_today
    _current_rate_est = (tax.get("rate_estimates_by_year") or {}).get(
        str(clock.today(fallback=_date_today.today).year))

    # Latest annual-expenses figure (drives FIRE + income expense
    # coverage).  Most-recent entry wins, matching the FI-threshold rule.
    _ann_exp_list = rm.get("annual_expenses") or []
    latest_annual_expenses = None
    if _ann_exp_list:
        _latest_exp = max(_ann_exp_list, key=lambda x: x.get("date", ""))
        if _latest_exp.get("amount"):
            latest_annual_expenses = float(_latest_exp["amount"])

    # "vs SPY" as a single dollar delta — the benchmark chart's headline.
    benchmark_delta = None
    if history:
        _last_h = history[-1]
        _pv = scope_snapshot_value(_last_h)
        _bv = float(_last_h.get("benchmark_spy") or 0)
        if _bv > 0:
            benchmark_delta = {
                "portfolio": round(_pv, 2),
                "spy": round(_bv, 2),
                "delta": round(_pv - _bv, 2),
                "as_of": _last_h.get("date", ""),
            }

    from .savings import compute_fees, compute_savings_by_year

    # Retain coverage failures from the existing daily walk: a transfer can
    # begin and end entirely between the exported semimonthly snapshots.
    transit_issues: list[dict] = []
    daily_totals = _daily_totals(txns, transit_issues=transit_issues)
    out = {
        "rollover_bridges": bridges,
        "retirement_contributions_by_year": contribs_yr,
        "savings_by_year":  compute_savings_by_year(txns, retirement_meta),
        "fees":             compute_fees(txns),
        "benchmark_delta":  benchmark_delta,
        "performance_by_filter": performance_by_filter,
        "options": compute_options_analytics(txns),
        "crypto": compute_crypto_analytics(txns, holdings),
        "income": compute_income_analytics(txns),
        "tax": tax,
        # Per-lot inventory for the Holdings tab's expandable lot view.
        # tax.lt_horizon is a filtered view of the same rows.
        "lots": compute_open_lots(fifo_state, holdings_by_account),
        "positions": compute_position_returns(txns, holdings),
        # Per-position trailing-window P&L for the Holdings board.
        # Reads the same open-lot inventory as `lots` above, so the
        # board and the expandable lot detail cannot disagree.
        "position_pnl": compute_position_pnl(
            fifo_state, holdings_by_account, txns,
            as_of=(history[-1].get("date") if history else None)),
        "header_summary": compute_header_summary(txns, history, bridges,
                                                 cash_summary=cash_summary),
        # New (this pass)
        "concentration":   concentration,
        "rebalancing":     compute_rebalancing(
            holdings_by_account, (retirement_meta or {}).get("target_allocation")),
        "reconciliation":  compute_reconciliation(
            txns, history, (retirement_meta or {}).get("reconcile")),
        # drawdown + monthly_pnl take the rollover bridges so an
        # in-flight custodial transfer (e.g. Voya→Schwab, weeks of $0
        # account value) doesn't read as a real crash / red month.
        # daily_totals upgrades the drawdown HEADLINE stats to daily
        # resolution (sparse snapshots understate peak-to-trough depth);
        # the exported series stays at snapshot cadence.
        "drawdown":        compute_drawdown(history, bridges,
                                            daily_totals=daily_totals),
        "daily_pnl":       compute_daily_pnl(history, txns),
        "trading_heatmap": compute_trading_heatmap(txns),
        "income_calendar": (_income_cal := compute_income_calendar(
            txns, holdings_by_account,
            annual_expenses=latest_annual_expenses,
            savings_apr=rm.get("savings_apr"))),
        # Budget — recurring living expenses from `Budget` metadata rows.
        # Reuses the income calendar's trailing-12mo income for the
        # "passive income covers X% of budget" figure (compute-once).
        "budget": compute_budget(
            rm.get("budget"),
            annual_expenses=latest_annual_expenses,
            ttm_income=(_income_cal or {}).get("ttm_actual")),
        # Paycheck — gross → deductions → estimated take-home, from
        # `Paycheck Deduction` metadata rows.  Reuses the current-year
        # tax estimate for the projected employee 401(k) deferral.
        "paycheck": compute_paycheck(retirement_meta, _current_rate_est),
        "monthly_pnl":     compute_monthly_pnl(history, txns, bridges),
        "monte_carlo":     monte_carlo,
        "changes":         changes,
        "alerts":          alerts,
    }
    # Data-health checks run last — they consult the rest of the
    # analytics dict (e.g. options.open_contracts, tax.realized_by_year).
    out["data_health"] = compute_data_health(
        txns, holdings_by_account, history, out, CACHE_DIR,
        cash_summary=cash_summary or {},
        transit_issues=transit_issues,
    )
    return out
