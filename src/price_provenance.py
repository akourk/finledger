"""Describe valuation evidence without changing the selected price or value.

Dates describe the actual cached bar or transaction quote. A successful fetch
time describes retrieval, not the bar's date. Historical callers supply the
valuation kernel's source and the transaction dates known at that snapshot.
"""

from __future__ import annotations

from datetime import date, timedelta
import math

from .config import CASH_SYMBOLS
from .prices import (get_price_observation, price_source_symbol, any_provisional,
                     _settle_horizon, _settle_class, _last_trading_day, _today)


def transaction_quotes(txns, target):
    """Return rebased quotes and their original dates, in canonical order."""
    from .basis import _sort_key
    from .valuation import rebase_transaction_prices

    values, dates = {}, {}
    for txn in sorted(txns, key=_sort_key):
        day = txn.get("date", "")
        price = float(txn.get("price", 0) or 0)
        symbol = txn.get("symbol", "")
        if symbol and day and day <= target and math.isfinite(price) and price > 0:
            values[symbol] = price
            dates[symbol] = day
    return rebase_transaction_prices(values, dates, target), dates


def _old_market_quote(symbol, observed, target):
    # Require a completed session. Allow one weekday of slack for a market
    # holiday or delayed NAV; the exact quote date remains visible throughout.
    expected = min(date.fromisoformat(target), _settle_horizon(symbol))
    if _settle_class(symbol) != "crypto":
        expected = _last_trading_day(expected)
        expected = _last_trading_day(expected - timedelta(days=1))
    return date.fromisoformat(observed) < expected


def describe_pricing(symbol, target, source, transaction_date=None):
    """Metadata for an already-resolved ``valuation.Mark.source``.

    No price is invented when a mocked/legacy valuation lacks its evidence.
    Scaled proxies and intrinsic floors stay visibly estimated even when their
    underlying market quote is fresh. Cash has no market quote date.
    """
    result = {"source": "unknown", "date": None, "quote_symbol": None,
              "stale": False, "provisional": False}
    if symbol in CASH_SYMBOLS or source == "cash":
        return {**result, "source": "cash"}
    if source == "none":
        return {**result, "source": "unavailable"}
    if source == "txn":
        observed = transaction_date if transaction_date and transaction_date <= target else None
        return {**result, "source": "transaction", "date": observed,
                "stale": bool(observed and
                              (date.fromisoformat(target) - date.fromisoformat(observed)).days > 7)}
    target_symbol = price_source_symbol(symbol) if source == "intrinsic" else symbol
    observation = get_price_observation(target_symbol, target) if target_symbol else None
    if observation is None:
        return {**result, "source": "intrinsic" if source == "intrinsic" else "unknown"}
    observed = observation["date"]
    kind = ("intrinsic" if source == "intrinsic" else
            "proxy" if observation["estimated"] else observation.get("source", "market"))
    quote_symbol = observation["quote_symbol"]
    provisional = kind != "broker" and observed > _settle_horizon(quote_symbol).isoformat()
    if kind != "broker" and target >= _today().isoformat() and observed == target:
        provisional = provisional or any_provisional([quote_symbol])
    return {"source": kind, "date": observed, "quote_symbol": quote_symbol,
            "stale": _old_market_quote(quote_symbol, observed, target),
            "provisional": provisional}


def annotate_holdings(holdings, holdings_by_account, txns, target):
    """Attach the same per-symbol evidence to both current holdings views."""
    from .valuation import mark

    values, dates = transaction_quotes(txns, target)
    details = {}
    for row in holdings_by_account:
        symbol = row.get("symbol", "")
        if symbol not in details:
            source = mark(symbol, row.get("quantity", 0), target, values,
                          restate_qty=False).source
            details[symbol] = describe_pricing(symbol, target, source, dates.get(symbol))
        row["pricing"] = dict(details[symbol])
    for row in holdings:
        row["pricing"] = dict(details.get(row.get("symbol"), {
            "source": "unknown", "date": None, "quote_symbol": None,
            "stale": False, "provisional": False,
        }))


def summarize_pricing(rows, *, as_of=None):
    """Count evidence categories once in Python; do not estimate missing value."""
    def empty():
        return {"positions": 0, "estimated": 0, "stale": 0,
                "unavailable": 0, "unknown": 0}

    total, accounts = empty(), {}
    for row in rows:
        group = row.get("account_group", "")
        counts = accounts.setdefault(group, empty())
        pricing = row.get("pricing") or {}
        source = pricing.get("source", "cash" if row.get("symbol") in CASH_SYMBOLS else "unknown")
        unavailable = row.get("value") is None or source == "unavailable"
        unknown = not unavailable and (source == "unknown" or
                    (not pricing.get("date") and source != "cash"))
        for bucket in (total, counts):
            bucket["positions"] += 1
            bucket["estimated"] += int(source in {"transaction", "proxy", "intrinsic"})
            bucket["stale"] += int(bool(pricing.get("stale")))
            bucket["unavailable"] += int(unavailable)
            bucket["unknown"] += int(unknown)
    return {"as_of": as_of, "total": total,
            "by_account": [{"account_group": group, **accounts[group]}
                           for group in sorted(accounts)]}
