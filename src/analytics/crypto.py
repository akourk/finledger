"""Crypto-tab analytics: per-coin rollups and conversion history."""

from __future__ import annotations

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


def _is_crypto_symbol(sym: str) -> bool:
    return bool(sym) and sym.endswith("-USD")


def compute_crypto_analytics(txns: list[dict], holdings: list[dict]) -> dict:
    """Per-coin holdings + realized + income, recent activity, and the
    conversion/wrap log."""
    crypto_holdings = [h for h in holdings if _is_crypto_symbol(h.get("symbol", ""))]
    crypto_txns = [t for t in txns if _is_crypto_symbol(t.get("symbol", ""))]

    per_coin: dict[str, dict] = {}
    for t in crypto_txns:
        sym = t["symbol"]
        c = per_coin.setdefault(sym, {
            "symbol": sym, "first_date": None, "last_date": None,
            "realized": 0.0, "income": 0.0, "txn_count": 0,
        })
        c["txn_count"] += 1
        d = t.get("date", "")
        if d:
            if not c["first_date"] or d < c["first_date"]:
                c["first_date"] = d
            if not c["last_date"] or d > c["last_date"]:
                c["last_date"] = d
        if isinstance(t.get("realized_gain"), (int, float)):
            c["realized"] += t["realized_gain"]
        # Lending included: crypto lending income lands on the 1099-MISC
        # "Other Income" alongside rewards (see reconcile's other_income
        # kind) — omitting it made the per-coin income column disagree
        # with what the reconciliation panel checks against.
        if t.get("action") in ("Reward", "Interest", "Lending"):
            amt = float(t.get("amount", 0) or 0)
            if amt > 0:
                c["income"] += amt

    # Attach current-holding fields
    for h in crypto_holdings:
        sym = h["symbol"]
        c = per_coin.setdefault(sym, {
            "symbol": sym, "first_date": None, "last_date": None,
            "realized": 0.0, "income": 0.0, "txn_count": 0,
        })
        c["quantity"] = h.get("quantity")
        c["price"] = h.get("price")
        c["value"] = h.get("value")
        c["basis"] = h.get("cost_basis")
        c["unrealized"] = h.get("unrealized_gain")

    per_coin_list = sorted(
        per_coin.values(),
        key=lambda x: -(x.get("value") or 0),
    )
    for c in per_coin_list:
        c["realized"] = round(c["realized"], 2)
        c["income"] = round(c["income"], 2)

    # Recent activity — 30 most recent
    recent = sorted(crypto_txns, key=lambda t: t.get("date", ""), reverse=True)[:30]

    # Conversions / wraps
    conv_actions = {"Convert In", "Convert Out", "Wrap Asset In",
                    "Wrap Asset Out", "Unwrap In", "Unwrap Out"}
    conversions = [
        t for t in crypto_txns
        if t.get("action") in conv_actions
        or t.get("raw_action") in conv_actions
        or any(w in (t.get("raw_action") or "") for w in ("Convert", "Wrap", "Unwrap"))
    ]
    conversions = sorted(conversions, key=lambda t: t.get("date", ""), reverse=True)[:30]

    # Totals
    total_value = sum((h.get("value") or 0) for h in crypto_holdings)
    total_basis = sum((h.get("cost_basis") or 0) for h in crypto_holdings)
    total_realized = sum((t.get("realized_gain") or 0) for t in crypto_txns)
    total_income = sum(float(t.get("amount", 0) or 0)
                       for t in crypto_txns
                       if t.get("action") in ("Reward", "Interest", "Lending"))
    return {
        "per_coin": per_coin_list,
        "recent_activity": recent,
        "conversions": conversions,
        "stats": {
            "total_value": round(total_value, 2),
            "total_basis": round(total_basis, 2),
            "total_unrealized": round(total_value - total_basis, 2),
            "total_realized": round(total_realized, 2),
            "total_income": round(total_income, 2),
            "txn_count": len(crypto_txns),
            "coins_held": len(crypto_holdings),
            "coins_ever": len(per_coin_list),
        },
    }


# ---------------------------------------------------------------------------
# Income (dividends, interest, rewards, lending)
# ---------------------------------------------------------------------------
