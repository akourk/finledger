"""Income-tab analytics: dividends / interest / rewards / lending by year, month, source."""

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


def compute_income_analytics(txns: list[dict]) -> dict:
    """Aggregate income by year, month, and source."""
    by_year: dict[str, dict] = {}
    by_month: dict[str, float] = defaultdict(float)
    by_source: dict[str, dict] = {}
    total = 0.0

    for t in txns:
        kind = INCOME_ACTION_KINDS.get(t.get("action", ""))
        if not kind:
            continue
        amt = float(t.get("amount", 0) or 0)
        if amt <= 0:
            continue
        total += amt
        y = _year(t.get("date", ""))
        m = (t.get("date") or "")[:7]
        if y:
            row = by_year.setdefault(y, {
                "year": y, "dividends": 0.0, "interest": 0.0,
                "rewards": 0.0, "lending": 0.0, "total": 0.0,
            })
            row[kind] += amt
            row["total"] += amt
        if m:
            by_month[m] += amt
        src_sym = t.get("symbol")
        src = src_sym if (src_sym and src_sym != "USD") else (t.get("account_group") or "(unknown)")
        s = by_source.setdefault(src, {
            "source": src,
            "account": t.get("account_group") or "",
            "dividends": 0.0, "interest": 0.0,
            "rewards": 0.0, "lending": 0.0, "total": 0.0,
        })
        s[kind] += amt
        s["total"] += amt

    by_year_list = sorted(
        ({**r, **{k: round(v, 2) for k, v in r.items() if isinstance(v, float)}}
         for r in by_year.values()),
        key=lambda x: x["year"],
    )
    by_month_list = [
        {"date": m + "-01", "value": round(v, 2)}
        for m, v in sorted(by_month.items())
    ]
    by_source_list = sorted(
        ({**r, **{k: round(v, 2) for k, v in r.items() if isinstance(v, float)}}
         for r in by_source.values()),
        key=lambda x: -x["total"],
    )[:30]

    return {
        "by_year": by_year_list,
        "by_month": by_month_list,
        "by_source": by_source_list,
        "total": round(total, 2),
    }


# ---------------------------------------------------------------------------
# (SECTION_1256_UNDERLYINGS moved to tax.py where it's used.)
