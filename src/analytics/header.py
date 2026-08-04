"""Header-bar summary: current value, 1-day change, total return."""

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
from ..prices import get_price, option_intrinsic, split_factor_since


def compute_header_summary(txns: list[dict], history: list[dict],
                          bridges: list[dict],
                          cash_summary: dict | None = None) -> dict | None:
    """Summary for the persistent header bar: current value, 1-day
    change, and the total-return figure.

    1-day change is computed by repricing today's balance at
    yesterday's close (keeps share count constant so same-day cash
    flows don't contaminate the "market moved X today" number).
    Yesterday = most recent trading day at or before today-1; the
    price cache's 7-day lookback handles weekends / holidays.

    ``total_return`` (value − net contributed) is computed HERE, once —
    the top bar and the Performance tab's all-time anchor card both
    read it from this dict.  They used to derive it independently
    (header value vs a JS sum over holdings rows), which produced
    cent-level disagreements from rounding.
    """
    if not history:
        return None
    today = history[-1].get("date", "")
    today_total = float(history[-1].get("total", 0) or 0)
    if not today:
        return None

    from datetime import timedelta as _td
    t_date = _parse_iso(today)
    if not t_date:
        return None
    yest = (t_date - _td(days=1)).isoformat()

    # Reprice today's positions at yesterday's close.  Uses the same
    # balance walk as _value_at_date, but constrained to today's
    # snapshot positions (no ledger re-walk needed).  Falls back to
    # the most recent txn price when the cache can't resolve a symbol.
    today_positions = history[-1].get("positions", []) or []
    last_txn_price: dict[str, float] = {}
    for t in txns:
        p = float(t.get("price", 0) or 0)
        sym = t.get("symbol", "")
        if sym and p > 0:
            last_txn_price[sym] = p

    yest_total = 0.0
    unpriced = 0.0
    for pos in today_positions:
        sym = pos.get("symbol", "")
        qty = float(pos.get("quantity", 0) or 0)
        if sym in CASH_SYMBOLS:
            yest_total += qty
            continue
        px = get_price(sym, yest)
        if px is not None:
            # No split_factor_since here: `qty` comes from TODAY's
            # snapshot positions (already today-basis) and cached prices
            # are today-basis too, so qty × px is already correct.
            # Applying the factor (as history.py does for *as-of-date*
            # balances) would double-adjust across a fresh split.
            yest_total += qty * px
        else:
            fb = last_txn_price.get(sym)
            # Apply the same option intrinsic floor history.py uses for
            # TODAY's value.  Without it the two sides of the 1-day
            # delta are priced by different rules: today's snapshot
            # marks a deep-ITM contract at intrinsic while this branch
            # pins yesterday at the purchase premium, so the whole
            # intrinsic-over-cost gain reprints as a phantom "today's
            # move" every single day the contract is open.
            iv = option_intrinsic(sym, yest)
            if iv is not None and iv > (fb or 0):
                fb = iv
            if fb is not None:
                # Contracts × per-share premium need the ×100 multiplier
                # (matches the snapshot valuation in history.py).
                from ..config import contract_multiplier
                yest_total += qty * fb * contract_multiplier(sym)
            else:
                # Couldn't price yesterday — fall back to today's value
                # so this position contributes 0 to the 1d delta rather
                # than distorting the denominator.
                unpriced += float(pos.get("value", 0) or 0)
                yest_total += float(pos.get("value", 0) or 0)

    change_1d = today_total - yest_total
    pct_1d = (change_1d / yest_total) if yest_total > 0 else None

    # Total return = what you have minus what you put in (net of
    # withdrawals).  Realized gains / dividends are already reflected
    # in current value, so nothing is double-counted.
    net_contributed = float((cash_summary or {}).get("net_contributed", 0) or 0)
    total_return = today_total - net_contributed
    total_return_pct = ((total_return / net_contributed)
                        if net_contributed > 0 else None)

    return {
        "as_of": today,
        "prev_day": yest,
        "value": round(today_total, 2),
        "prev_day_value": round(yest_total, 2),
        "change_1d": round(change_1d, 2),
        "change_1d_pct": round(pct_1d, 6) if pct_1d is not None else None,
        "unpriced_1d": round(unpriced, 2),   # coverage canary
        "net_contributed": round(net_contributed, 2),
        "total_return": round(total_return, 2),
        "total_return_pct": (round(total_return_pct, 6)
                             if total_return_pct is not None else None),
    }
