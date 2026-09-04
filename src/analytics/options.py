"""Options-tab analytics: closed trades, open contracts, per-underlying stats, cumulative P&L series, annual summary."""

from __future__ import annotations


from collections import defaultdict
from datetime import datetime, timedelta

from ._shared import (
    # Constants
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


# Option-symbol parsing moved to src/prices.py (the pricing layer and
# both pipeline paths need it for the intrinsic-value floor); this alias
# keeps the analytics-side imports (tax.py, reconcile.py) working.
from ..prices import parse_option_symbol as _parse_option_symbol  # noqa: E402


def _is_option_symbol(sym: str) -> bool:
    return bool(sym) and (" Call " in sym or " Put " in sym or sym.endswith(" OPTION"))


def _hold_days(start_iso: str, end_iso: str) -> int | None:
    sd, ed = _parse_iso(start_iso), _parse_iso(end_iso)
    if not sd or not ed:
        return None
    return max(0, (ed - sd).days)


def compute_options_analytics(txns: list[dict]) -> dict:
    """Everything the Options tab displays, pre-computed.

    Returns ``{open_contracts, closed_trades, by_underlying, annual_summary,
    cumulative_pnl, stats}``.  All realized / basis / proceeds values come
    straight from the FIFO-annotated transaction rows — no re-walking.
    """
    # Walk txns FIFO-style to attach open_date + hold_days to closes
    open_lots: dict[str, list[dict]] = defaultdict(list)
    closed: list[dict] = []

    def _sort_key(t):
        eff = t.get("basis_effect", "")
        phase = 1 if eff in ("remove", "transfer_out") else 0
        return (t.get("date", ""), phase)

    for t in sorted(txns, key=_sort_key):
        sym = t.get("symbol", "")
        if not _is_option_symbol(sym):
            continue
        action = t.get("action", "")
        qty = float(t.get("quantity", 0) or 0)
        if action == "Option Buy" and qty > 0:
            open_lots[sym].append({"date": t.get("date", ""), "qty": qty})
        elif qty > 0 and action in ("Option Sell", "Option Expire", "Option Exercise"):
            remaining = qty
            earliest_open = None
            while remaining > 1e-9 and open_lots[sym]:
                take = min(open_lots[sym][0]["qty"], remaining)
                if earliest_open is None:
                    earliest_open = open_lots[sym][0]["date"]
                open_lots[sym][0]["qty"] -= take
                remaining -= take
                if open_lots[sym][0]["qty"] <= 1e-9:
                    open_lots[sym].pop(0)
            parsed = _parse_option_symbol(sym) or {}
            closed.append({
                "symbol": sym,
                "account_group": t.get("account_group", ""),
                "action": action,
                "close_date": t.get("date", ""),
                "open_date": earliest_open,
                "qty": qty,
                "proceeds": round(float(t.get("amount", 0) or 0), 2),
                "basis": round(float(t.get("cost_basis", 0) or 0), 2),
                "realized": round(float(t.get("realized_gain", 0) or 0), 2),
                "hold_days": _hold_days(earliest_open, t.get("date", "")),
                "underlying": parsed.get("underlying"),
                "expiry": parsed.get("expiry"),
                "option_type": parsed.get("type"),
                "strike": parsed.get("strike"),
            })

    # Open contracts — per-symbol running balance, matching main.py's rules
    _SUB = {"Sell", "Withdrawal", "Transfer Out", "Distribution", "Fee",
            "Tax", "Option Sell", "Option Expire", "Option Exercise"}
    _NEU = {"Neutral"}
    balances: dict[str, float] = defaultdict(float)
    first_date: dict[str, str] = {}
    entry_price: dict[str, float] = {}
    contract_account: dict[str, str] = {}
    for t in txns:
        sym = t.get("symbol", "")
        if not _is_option_symbol(sym):
            continue
        a = t.get("action", "")
        if a in _NEU:
            continue
        q = float(t.get("quantity", 0) or 0)
        balances[sym] += -q if a in _SUB else q
        if a == "Option Buy" and q > 0:
            d = t.get("date", "")
            if d and (sym not in first_date or d < first_date[sym]):
                first_date[sym] = d
            p = float(t.get("price", 0) or 0)
            if p > 0:
                entry_price[sym] = p
            # Capture the account_group from the opening leg so the
            # dashboard can filter open positions by broker.  Options
            # are account-specific in practice, so the first opener
            # is a reliable account label for the contract.
            contract_account[sym] = t.get("account_group", "")

    today_str = datetime.now().date().isoformat()
    today_d = _parse_iso(today_str)

    open_contracts: list[dict] = []
    for sym, bal in balances.items():
        if bal <= 1e-9:
            continue
        parsed = _parse_option_symbol(sym) or {}
        dte = None
        if parsed.get("expiry") and today_d:
            ed = _parse_iso(parsed["expiry"])
            if ed:
                dte = (ed - today_d).days
        open_contracts.append({
            "symbol": sym,
            "account_group": contract_account.get(sym, ""),
            "underlying": parsed.get("underlying"),
            "expiry": parsed.get("expiry"),
            "option_type": parsed.get("type"),
            "strike": parsed.get("strike"),
            "qty": bal,
            "dte": dte,
            "open_date": first_date.get(sym, ""),
            "entry_price": entry_price.get(sym),
        })
    # Soonest-to-expire first, nulls last
    open_contracts.sort(key=lambda o: (o["dte"] if o["dte"] is not None else 10**9))

    # P&L by underlying
    by_under: dict[str, dict] = {}
    for c in closed:
        u = c.get("underlying") or "(unknown)"
        if u not in by_under:
            by_under[u] = {"underlying": u, "trades": 0, "wins": 0, "realized": 0.0}
        by_under[u]["trades"] += 1
        if c["realized"] > 0:
            by_under[u]["wins"] += 1
        by_under[u]["realized"] += c["realized"]
    by_underlying = [
        {**r, "realized": round(r["realized"], 2),
         "win_rate": (r["wins"] / r["trades"]) if r["trades"] else None}
        for r in sorted(by_under.values(), key=lambda x: -x["realized"])
    ]

    # Annual summary (newest first)
    by_yr: dict[str, dict] = {}
    for c in closed:
        y = _year(c["close_date"])
        if not y:
            continue
        if y not in by_yr:
            by_yr[y] = {"year": y, "trades": 0, "wins": 0, "realized": 0.0}
        by_yr[y]["trades"] += 1
        if c["realized"] > 0:
            by_yr[y]["wins"] += 1
        by_yr[y]["realized"] += c["realized"]
    annual_summary = [
        {**r, "realized": round(r["realized"], 2),
         "win_rate": (r["wins"] / r["trades"]) if r["trades"] else None}
        for r in sorted(by_yr.values(), key=lambda x: x["year"], reverse=True)
    ]

    # Cumulative realized P&L over time (for the chart)
    cum_points: list[dict] = []
    run = 0.0
    for c in sorted(closed, key=lambda x: x["close_date"]):
        run += c["realized"]
        cum_points.append({"date": c["close_date"], "value": round(run, 2)})

    # Lifetime stats
    total = round(sum(c["realized"] for c in closed), 2)
    wins = sum(1 for c in closed if c["realized"] > 0)
    losses = sum(1 for c in closed if c["realized"] < 0)
    breakevens = sum(1 for c in closed if c["realized"] == 0)
    decided = wins + losses
    win_rate = wins / decided if decided else None
    holds = [c["hold_days"] for c in closed if c["hold_days"] is not None]
    avg_hold = sum(holds) / len(holds) if holds else None
    by_realized = sorted(closed, key=lambda x: -x["realized"])
    biggest_winner = by_realized[0]["realized"] if by_realized and by_realized[0]["realized"] > 0 else None
    biggest_loser = by_realized[-1]["realized"] if by_realized and by_realized[-1]["realized"] < 0 else None

    # Per-type (Call / Put) breakdown — same shape as the lifetime
    # stats but scoped to one option type.  Helps answer "am I a
    # better call buyer or put buyer?" at a glance.
    def _type_stats(label: str) -> dict:
        subset = [c for c in closed if c.get("option_type") == label]
        sub_wins = sum(1 for c in subset if c["realized"] > 0)
        sub_losses = sum(1 for c in subset if c["realized"] < 0)
        sub_decided = sub_wins + sub_losses
        sub_pnl = round(sum(c["realized"] for c in subset), 2)
        sub_holds = [c["hold_days"] for c in subset if c["hold_days"] is not None]
        return {
            "trades": len(subset),
            "wins": sub_wins,
            "losses": sub_losses,
            "breakevens": sum(1 for c in subset if c["realized"] == 0),
            "win_rate": (sub_wins / sub_decided) if sub_decided else None,
            "total_pnl": sub_pnl,
            "avg_hold_days": (sum(sub_holds) / len(sub_holds)) if sub_holds else None,
            "avg_winner": round(
                sum(c["realized"] for c in subset if c["realized"] > 0) / sub_wins, 2
            ) if sub_wins else None,
            "avg_loser": round(
                sum(c["realized"] for c in subset if c["realized"] < 0) / sub_losses, 2
            ) if sub_losses else None,
        }

    call_stats = _type_stats("Call")
    put_stats  = _type_stats("Put")

    # Profit factor = gross wins / |gross losses|.  > 1 means winners
    # outweigh losers in $ terms; widely used metric in options /
    # trading communities.  Expectancy = avg P&L per trade — combines
    # win rate and average size into a single per-trade dollar figure.
    gross_wins = sum(c["realized"] for c in closed if c["realized"] > 0)
    gross_loss = sum(c["realized"] for c in closed if c["realized"] < 0)
    profit_factor = (gross_wins / abs(gross_loss)) if gross_loss else None
    expectancy = (total / len(closed)) if closed else None
    avg_winner = round(gross_wins / wins, 2) if wins else None
    avg_loser  = round(gross_loss / losses, 2) if losses else None

    return {
        "open_contracts": open_contracts,
        "closed_trades": closed,
        "by_underlying": by_underlying,
        "annual_summary": annual_summary,
        "cumulative_pnl": cum_points,
        "stats": {
            "total_pnl": total,
            "trades": len(closed),
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": win_rate,
            "avg_hold_days": avg_hold,
            "biggest_winner": biggest_winner,
            "biggest_loser": biggest_loser,
            "open_count": len(open_contracts),
            # Trader-grade ratios.
            "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
            "expectancy":    round(expectancy, 2) if expectancy is not None else None,
            "avg_winner":    avg_winner,
            "avg_loser":     avg_loser,
            # Call / Put split — each carries its own win_rate, P&L,
            # trade count, avg hold, and avg winner/loser figures.
            "call":          call_stats,
            "put":           put_stats,
        },
    }


# ---------------------------------------------------------------------------
# Crypto (tab-level aggregations)
# ---------------------------------------------------------------------------
