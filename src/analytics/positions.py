"""Per-position total-return rollup + top winners / losers."""

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


def compute_position_returns(txns: list[dict], holdings: list[dict]) -> dict:
    """Per-symbol aggregate: realized + unrealized, returned as a sorted
    list with the top-10 winners and top-10 losers broken out.

    "Total gain" = realized + unrealized for positions we still hold;
    just realized for positions that are closed.  "% Return" = total_gain
    / current_basis (None if basis is 0, e.g. closed positions).
    """
    per_sym: dict[str, dict] = {}
    for t in txns:
        sym = t.get("symbol")
        if not sym or sym == "USD":
            continue
        r = per_sym.setdefault(sym, {"symbol": sym, "realized": 0.0})
        rg = t.get("realized_gain")
        if isinstance(rg, (int, float)):
            r["realized"] += rg
    for h in holdings:
        sym = h["symbol"]
        r = per_sym.setdefault(sym, {"symbol": sym, "realized": 0.0})
        r["value"] = h.get("value")
        r["basis"] = h.get("cost_basis")
        r["unrealized"] = h.get("unrealized_gain")
        r["sector"] = h.get("sector")

    rows: list[dict] = []
    for r in per_sym.values():
        total_gain = (r.get("realized") or 0) + (r.get("unrealized") or 0)
        basis = r.get("basis") or 0
        pct = (total_gain / basis) * 100 if basis > 0 else None
        rows.append({
            **r,
            "realized": round(r.get("realized", 0), 2),
            "total_gain": round(total_gain, 2),
            "pct_return": round(pct, 2) if pct is not None else None,
        })
    rows.sort(key=lambda x: -x["total_gain"])
    return {
        "positions": rows,
        "winners": rows[:10],
        "losers": list(reversed(rows))[:10],
    }


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------
