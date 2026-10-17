"""Options executions, completed position episodes, and matched-contract outcomes.

Financial amounts are consumed from accounting annotations. The quantity walk
only identifies account/contract episodes; it does not select purchase lots or
calculate a second basis. Date-only activity follows the ledger's ordering
(adds before removals, then ingest sequence), so same-day close/reopen activity
cannot be distinguished from scaling an existing position.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from .. import clock
from ..actions import ADD_ACTIONS, SUBTRACT_ACTIONS
from ..basis import _basis_dollars, _sort_key, basis_effect_for
from ..config import contract_multiplier
from ._shared import _parse_iso, _year

# Other analytics modules import these aliases.
from ..prices import parse_option_symbol as _parse_option_symbol

_EPS = 1e-9
_OPEN_ACTIONS = {"Option Buy", "Buy"}
_CLOSE_ACTIONS = {"Option Sell", "Option Expire", "Option Exercise", "Sell"}
_BASIS_ADD = {"add", "zero_basis", "transfer_in", "transfer_in_unpaired",
              "wrap_in", "wrap_in_unpaired", "rebase_in", "rebase_neutral"}
_BASIS_SUB = {"remove", "transfer_out", "transfer_out_eager_consumed",
              "wrap_out", "wrap_out_unpaired", "rebase_out", "roc"}
_NOOP = {"intra_group_noop", "wrap_carry_noop", "roc_noop"}


def _is_option_symbol(sym: str) -> bool:
    return bool(sym) and (" Call " in sym or " Put " in sym or sym.endswith(" OPTION"))


def _hold_days(start_iso: str, end_iso: str) -> int | None:
    sd, ed = _parse_iso(start_iso), _parse_iso(end_iso)
    return max(0, (ed - sd).days) if sd and ed else None


def _finite(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _money_sum(rows: list[dict], field: str) -> float | None:
    values = [_finite(row.get(field)) for row in rows]
    return round(sum(values), 2) if all(v is not None for v in values) else None


def _position_stats(positions: list[dict]) -> dict:
    """Position outcomes, shared by lifetime, type, and grouped summaries."""
    winners = [p["realized"] for p in positions if p["realized"] > 0]
    losers = [p["realized"] for p in positions if p["realized"] < 0]
    wins, losses = len(winners), len(losers)
    holds = [p["hold_days"] for p in positions if p.get("hold_days") is not None]
    total = round(sum(p["realized"] for p in positions), 2)
    gross_wins, gross_losses = sum(winners), abs(sum(losers))
    return {
        "trades": len(positions), "wins": wins, "losses": losses,
        "breakevens": len(positions) - wins - losses,
        "win_rate": wins / (wins + losses) if wins + losses else None,
        "total_pnl": total, "completed_pnl": total,
        "avg_hold_days": sum(holds) / len(holds) if holds else None,
        "biggest_winner": max(winners) if winners else None,
        "biggest_loser": min(losers) if losers else None,
        "profit_factor": gross_wins / gross_losses if gross_losses else None,
        "expectancy": total / len(positions) if positions else None,
        "avg_winner": gross_wins / wins if wins else None,
        "avg_loser": -gross_losses / losses if losses else None,
    }


def _lot_outcomes(txn: dict, qty: float, basis: float | None,
                  proceeds: float | None, realized: float | None) -> tuple[list[dict], bool]:
    """Require the whole matched-lot annotation to reconcile to its close.

    A partial lot vector is not usable: the ledger apportions all proceeds over
    consumed quantity, which can overstate proceeds on an unmatched close. Never
    fill missing contract outcomes with the net execution's sign.
    """
    unknown = ([{"qty": qty, "realized": None, "outcome": "unknown"}], False)
    lots = txn.get("lot_breakdown")
    if not isinstance(lots, list) or not lots or None in (basis, proceeds, realized):
        return unknown
    clean = []
    for lot in lots:
        if not isinstance(lot, dict):
            return unknown
        q, b, p = (_finite(lot.get(k)) for k in ("qty", "cost_basis", "proceeds"))
        if q is None or q <= 0 or b is None or b < 0 or p is None or p < 0:
            return unknown
        clean.append((q, b, p))
    if (abs(sum(q for q, _, _ in clean) - qty) > _EPS
            or abs(sum(b for _, b, _ in clean) - basis) > 0.011
            or abs(sum(p for _, _, p in clean) - proceeds) > 0.011
            or abs(sum(p - b for _, b, p in clean) - realized) > 0.011):
        return unknown
    return ([{
        "qty": q, "realized": round(p - b, 8),
        "outcome": "win" if p - b > _EPS else "loss" if p - b < -_EPS else "breakeven",
    } for q, b, p in clean], True)


def _contract_stats(closed: list[dict]) -> dict:
    result = {"wins": 0.0, "losses": 0.0, "breakevens": 0.0, "unknown": 0.0}
    fields = {"win": "wins", "loss": "losses", "breakeven": "breakevens"}
    for close in closed:
        for lot in close["lot_outcomes"]:
            result[fields.get(lot["outcome"], "unknown")] += lot["qty"]
    decided = result["wins"] + result["losses"]
    return {**result, "contracts": sum(result.values()),
            "win_rate": result["wins"] / decided if decided else None,
            "coverage_complete": result["unknown"] <= _EPS}


def compute_options_analytics(txns: list[dict]) -> dict:
    """Export Options statistics without accessing caches or fetching prices.

    ``closed_trades`` preserves execution-date realized P&L, including partial
    exits. ``completed_positions`` counts one supported long episode for each
    account and exact contract from flat to flat. Transferred, unmatched, or
    inconsistent episodes remain in execution P&L but not position statistics.
    ``contract_stats`` weights matched-lot outcomes by quantity; unknown coverage
    is explicit. All win rates exclude breakevens and unavailable outcomes.
    """
    today = clock.now(fallback=datetime.now).date()
    eligible = [t for t in txns if _is_option_symbol(t.get("symbol", ""))
                and _parse_iso(t.get("date", "")) is not None
                and _parse_iso(t["date"]) <= today]
    states: dict[tuple[str, str], dict] = {}
    closed: list[dict] = []
    completed: list[dict] = []

    for txn in sorted(eligible, key=_sort_key):
        sym, account = txn["symbol"], txn.get("account_group", "")
        key = (account, sym)
        state = states.setdefault(key, {"qty": 0.0, "episode": None, "serial": 0})
        action = txn.get("action", "")
        effect = txn.get("basis_effect") or basis_effect_for(sym, action)
        if effect in _NOOP:
            continue
        qty = _finite(txn.get("quantity"))
        if qty is None or qty < 0:
            if state["episode"]:
                state["episode"]["valid"] = False
                state["episode"]["basis_known"] = False
            continue
        delta = qty if action in ADD_ACTIONS else -qty if action in SUBTRACT_ACTIONS else 0.0
        before = state["qty"]
        if abs(before) <= _EPS and abs(delta) > _EPS:
            state["serial"] += 1
            state["episode"] = {
                "position_id": f"{account}|{sym}|{state['serial']}",
                "open_date": txn["date"] if delta > 0 else None,
                "valid": delta > 0 and action in _OPEN_ACTIONS,
                "remaining_basis": 0.0, "basis_known": True,
                "entries": 0, "closes": [],
            }
        episode = state["episode"]
        cb = _finite(txn.get("cost_basis"))
        if episode:
            if action in _OPEN_ACTIONS and delta > 0:
                episode["entries"] += 1
            elif action not in _CLOSE_ACTIONS and (abs(delta) > _EPS or effect in _BASIS_ADD | _BASIS_SUB):
                episode["valid"] = False
            if effect in _BASIS_ADD | _BASIS_SUB:
                if cb is None or cb < 0 or effect in ("transfer_in_unpaired", "wrap_in_unpaired"):
                    episode["basis_known"] = False
                else:
                    episode["remaining_basis"] += cb if effect in _BASIS_ADD else -cb
            elif abs(delta) > _EPS:
                episode["basis_known"] = False

        if qty > _EPS and action in _CLOSE_ACTIONS:
            realized = _finite(txn.get("realized_gain"))
            # The annotated pair preserves basis overrides and fee semantics.
            proceeds = cb + realized if cb is not None and realized is not None else _finite(_basis_dollars(txn))
            lots, coverage = _lot_outcomes(txn, qty, cb, proceeds, realized)
            raw_lots = txn.get("lot_breakdown")
            raw_lots = raw_lots if isinstance(raw_lots, list) else []
            acquired = [lot.get("date_acquired") for lot in raw_lots
                        if isinstance(lot, dict) and _parse_iso(lot.get("date_acquired"))]
            first_open = min(acquired) if acquired else episode["open_date"] if episode else None
            # Execution holding time follows the lots actually relieved, not
            # the first entry in an episode. Unknown dates must remain unknown.
            days = [_finite(lot.get("days")) for lot in raw_lots if isinstance(lot, dict)]
            hold = (sum(lot["qty"] * day for lot, day in zip(raw_lots, days)) / qty
                    if coverage and days and all(day is not None and day >= 0 for day in days)
                    else None)
            parsed = _parse_option_symbol(sym) or {}
            row = {
                "symbol": sym, "account_group": account, "action": action,
                "close_date": txn["date"], "open_date": first_open,
                "qty": qty, "proceeds": round(proceeds, 2) if proceeds is not None else None,
                "basis": round(cb, 2) if cb is not None else None,
                "realized": round(realized, 2) if realized is not None else None,
                "hold_days": hold,
                "underlying": parsed.get("underlying"), "expiry": parsed.get("expiry"),
                "option_type": parsed.get("type"), "strike": parsed.get("strike"),
                "lot_outcomes": lots, "lot_coverage_complete": coverage,
                "position_id": episode["position_id"] if episode else None,
                "completed_position": False,
            }
            closed.append(row)
            if episode:
                episode["closes"].append(row)
                if (before < qty - _EPS or None in (cb, realized, proceeds)
                        or (txn.get("lot_breakdown") and not coverage)):
                    episode["valid"] = False
                if before < qty - _EPS:
                    episode["basis_known"] = False

        state["qty"] += delta
        if state["qty"] < -_EPS and episode:
            episode["valid"] = False
            episode["basis_known"] = False
        if abs(state["qty"]) <= _EPS:
            state["qty"] = 0.0
            if episode and episode["closes"]:
                # Individual annotations are rounded; permit only their maximum
                # cumulative cent-rounding error when checking a flat episode.
                tolerance = max(0.011, 0.005 * (episode["entries"] + len(episode["closes"])) + _EPS)
                if (episode["valid"] and episode["basis_known"]
                        and abs(episode["remaining_basis"]) <= tolerance):
                    rows = episode["closes"]
                    completed.append({
                        **{k: rows[-1][k] for k in ("symbol", "account_group", "underlying", "expiry", "option_type", "strike")},
                        "position_id": episode["position_id"], "open_date": episode["open_date"],
                        "close_date": txn["date"], "hold_days": _hold_days(episode["open_date"], txn["date"]),
                        "qty": sum(r["qty"] for r in rows), "close_count": len(rows),
                        **{field: _money_sum(rows, field) for field in ("basis", "proceeds", "realized")},
                    })
                    for row in rows:
                        row["completed_position"] = True
            state["episode"] = None

    open_contracts = []
    for (account, sym), state in states.items():
        if state["qty"] <= _EPS:
            continue
        episode = state["episode"]
        parsed = _parse_option_symbol(sym) or {}
        expiry = _parse_iso(parsed.get("expiry"))
        basis = episode["remaining_basis"] if episode and episode["basis_known"] else None
        if basis is not None and basis < -0.011:
            basis = None
        basis = max(0.0, basis) if basis is not None else None
        open_contracts.append({
            "symbol": sym, "account_group": account, "underlying": parsed.get("underlying"),
            "expiry": parsed.get("expiry"), "option_type": parsed.get("type"), "strike": parsed.get("strike"),
            "qty": state["qty"], "dte": (expiry - today).days if expiry else None,
            "open_date": episode["open_date"] if episode else None,
            "entry_price": basis / (state["qty"] * contract_multiplier(sym)) if basis is not None else None,
            "basis": round(basis, 2) if basis is not None else None,
        })
    open_contracts.sort(key=lambda p: (p["dte"] if p["dte"] is not None else 10**9, p["account_group"], p["symbol"]))

    def group_summary(field: str, key_fn) -> list[dict]:
        keys = {key_fn(r) for r in completed + closed}
        result = []
        for key in keys:
            positions = [r for r in completed if key_fn(r) == key]
            executions = [r for r in closed if key_fn(r) == key]
            stats = _position_stats(positions)
            result.append({field: key, **{k: stats[k] for k in (
                "trades", "wins", "losses", "breakevens", "win_rate", "completed_pnl")},
                "realized": _money_sum(executions, "realized")})
        return result

    by_underlying = group_summary("underlying", lambda r: r.get("underlying") or "(unknown)")
    by_underlying.sort(key=lambda r: (r["realized"] is None, -(r["realized"] or 0), r["underlying"]))
    annual_summary = group_summary("year", lambda r: _year(r["close_date"]))
    annual_summary.sort(key=lambda r: r["year"], reverse=True)
    by_date = defaultdict(list)
    for row in closed:
        by_date[row["close_date"]].append(row)
    cumulative, running = [], 0.0
    for date in sorted(by_date):
        daily = _money_sum(by_date[date], "realized")
        running = running + daily if running is not None and daily is not None else None
        cumulative.append({"date": date, "value": round(running, 2) if running is not None else None})
    stats = {
        **_position_stats(completed), "realized_pnl": _money_sum(closed, "realized"),
        "closed_count": len(closed), "closed_contracts": sum(r["qty"] for r in closed),
        "open_count": len(open_contracts), "open_contract_count": sum(r["qty"] for r in open_contracts),
    }
    for label in ("Call", "Put"):
        rows = [r for r in closed if r["option_type"] == label]
        stats[label.lower()] = {
            **_position_stats([r for r in completed if r["option_type"] == label]),
            "realized_pnl": _money_sum(rows, "realized"),
        }
    return {"open_contracts": open_contracts, "closed_trades": closed,
            "completed_positions": completed, "by_underlying": by_underlying,
            "annual_summary": annual_summary, "cumulative_pnl": cumulative,
            "stats": stats, "contract_stats": _contract_stats(closed)}
