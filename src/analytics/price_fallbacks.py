"""Dated transaction quotes for repricing today's fixed share quantities."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
import math

from ..basis import _sort_key
from ..valuation import split_factor_since


def current_position_quantities(snapshot: dict) -> list[dict]:
    """Copy raw snapshot quantities into the cache's current share units.

    A historical snapshot can predate a subsequently recorded split. Posted
    quantities use the snapshot date; transit quantities retain departure units.
    Neither the stored custody rows nor their values/basis are rewritten.
    """
    positions = [*(snapshot.get("positions") or []),
                 *(snapshot.get("in_transit") or [])]
    return [{**p, "quantity": float(p.get("quantity", 0) or 0)
             * split_factor_since(p.get("symbol", ""),
                                  p.get("start_date") or snapshot["date"])}
            for p in positions]


def current_position_price_series(txns: list[dict], dates: Iterable[str]
                                  ) -> Iterator[tuple[str, dict[str, float]]]:
    """Yield fallback maps at ascending dates, using canonical day-close order.

    Only dated positive finite quotes available by each date qualify. Convert
    their original trade share units to today's units, matching the current
    quantities and split-adjusted cache prices used with ``restate_qty=False``.
    This prepares quotes; ``valuation.mark`` still resolves price sources and
    applies the option intrinsic floor and contract multiplier.

    Sort once and advance through the ledger once for the whole query window.
    Each result owns its map so later observations cannot mutate earlier dates.
    """
    rows = sorted((t for t in txns if t.get("date")), key=_sort_key)
    cursor = 0
    prices: dict[str, float] = {}
    for day in sorted(set(dates)):
        while cursor < len(rows) and rows[cursor]["date"] <= day:
            row = rows[cursor]
            cursor += 1
            symbol = row.get("symbol", "")
            price = float(row.get("price", 0) or 0)
            if not symbol or not math.isfinite(price) or price <= 0:
                continue
            factor = split_factor_since(symbol, row["date"])
            if math.isfinite(factor) and factor > 0:
                adjusted = price / factor
                if math.isfinite(adjusted) and adjusted > 0:
                    prices[symbol] = adjusted
        yield day, dict(prices)
