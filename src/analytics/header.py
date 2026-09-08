"""Header-bar summary: current value, 1-day change, total return."""

from __future__ import annotations

import re
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
from ..prices import any_provisional, oldest_last_fetch
from ..valuation import mark
from .price_fallbacks import current_position_price_series, current_position_quantities


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
    from ..return_flows import scope_snapshot_value
    today_total = scope_snapshot_value(history[-1])
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
    today_positions = current_position_quantities(history[-1])
    _, last_txn_price = next(current_position_price_series(txns, [yest]))

    yest_total = 0.0
    unpriced = 0.0
    px_yest: dict[str, float | None] = {}
    for pos in today_positions:
        if pos.get("valuation_issue"):
            continue  # Unsupported transit units are already a coverage warning.
        sym = pos.get("symbol", "")
        qty = float(pos.get("quantity", 0) or 0)
        # restate_qty=False: `qty` comes from TODAY's snapshot positions
        # (already today-basis) and cached prices are today-basis too, so
        # the product is already correct.  Restating (as history.py does
        # for *as-of-date* balances) would double-adjust across a fresh
        # split.
        m = mark(sym, qty, yest, last_txn_price, restate_qty=False,
                 price_cache=px_yest)
        if "value" in pos and pos["value"] is None:
            # Today's missing mark is not a total loss. Exclude this position
            # from the comparison and expose its known prior value as coverage.
            unpriced += float(m.value or 0)
            continue
        if m.value is not None:
            yest_total += m.value
        else:
            # Couldn't price yesterday — fall back to today's value so
            # this position contributes 0 to the 1d delta rather than
            # distorting the denominator.
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

    # When the marks behind this snapshot were actually pulled.  `as_of`
    # is only a DATE, which reads as "current" all day even when the
    # prices behind it were captured at 7am — that ambiguity is exactly
    # what makes a mid-session reconciliation against a broker
    # confusing.  Positions the cache never prices (fund display names,
    # unfetchable stubs) contribute nothing; their mark comes from
    # transaction history and no fetch timestamp describes it.
    held_symbols = {pos.get("symbol", "") for pos in today_positions
                    if pos.get("symbol") and pos.get("symbol") not in CASH_SYMBOLS}
    prices_as_of = oldest_last_fetch(held_symbols)
    # ...and whether any of those marks is still moving.  A mid-session
    # figure is a legitimate thing to show; silently presenting it as a
    # close is not, which is how a reconciliation against a broker
    # disagreed at 11am and then differently at the close.
    prices_provisional = any_provisional(held_symbols)

    return {
        "as_of": today,
        "prev_day": yest,
        "prices_as_of": prices_as_of,
        "prices_provisional": prices_provisional,
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
