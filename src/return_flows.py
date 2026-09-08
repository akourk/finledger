"""Cash movements across a return measurement's account boundary.

Portfolio contributions remain classified by ``txn_external_cash_flow``.
Verified in-kind transfers are additionally valued once for account returns;
the browser consumes the exported verdict instead of pairing or pricing rows.
"""

from __future__ import annotations

from itertools import groupby
import math

from .basis import (
    _EXTERNAL_TRANSFER_MARKERS, _pair_transfers, _sort_key, txn_external_cash_flow,
)
from .valuation import mark


def annotate_account_transfers(txns: list[dict]) -> None:
    """Replace derived ``account_transfer`` metadata on matched transfer legs.

    Shape: ``{counterparty_group: str, flow: signed dollars | None}``.
    Each leg uses its own quantity and closing-date mark, including split
    restatement and option multipliers. Raw ``amount`` may be asset units;
    carried cost basis is not a market value. Neither supplies a price.

    Only cross-group pairs with two portfolio-neutral legs qualify. Existing
    pairing excludes USD; unmatched movements, external-marker transfers,
    income, and same-group moves retain their existing classification. A null
    flow means no finite positive valuation was available; data health reports
    that coverage gap. It is not an invented zero-dollar transfer.

    Pair order matches the basis/history walkers, including ingest ``seq``.
    Prices use the complete day's rows but never a later day's transaction.
    Rebuild after price refresh, since exported metadata is only a snapshot.
    This does not add in-transit holdings to portfolio history.
    """
    for txn in txns:
        txn.pop("account_transfer", None)
    ordered = sorted(txns, key=_sort_key)
    paired = _pair_transfers(ordered)["tin_to_tout"]
    legs: dict[int, tuple[str, int]] = {}
    for tin in ordered:
        tout = paired.get(id(tin))
        if tout is None:
            continue
        # The lot matcher also accepts non-cash Deposit rows. That is not
        # evidence of an internal account move, even if its amount is absent.
        # A Receive marked as an airdrop is income despite its neutral
        # portfolio contribution verdict; a coincidental match must not erase it.
        if (tin.get("action") != "Transfer In"
                or tout.get("action") not in ("Transfer Out", "Synthetic Transfer Out")
                or any("airdrop" in (t.get("description") or "").lower()
                       for t in (tin, tout))):
            continue
        # Explicit off-platform evidence takes precedence even when missing
        # amount made the portfolio classifier return zero before its carve-out.
        if any(t.get("raw_action", "") in markers
               for t in (tin, tout)
               for markers in _EXTERNAL_TRANSFER_MARKERS.get(
                   t.get("account_group", ""), ())):
            continue
        source = tout.get("account_group")
        destination = tin.get("account_group")
        if not source or not destination or source == destination:
            continue
        if txn_external_cash_flow(tin) or txn_external_cash_flow(tout):
            continue
        legs[id(tout)] = (destination, -1)
        legs[id(tin)] = (source, 1)
    if not legs:
        return

    last_prices: dict[str, float] = {}
    for day, rows in groupby(ordered, key=lambda t: t.get("date", "")):
        rows = list(rows)
        for txn in rows:
            price = float(txn.get("price", 0) or 0)
            symbol = txn.get("symbol", "")
            if symbol and math.isfinite(price) and price > 0:
                last_prices[symbol] = price
        price_cache: dict[str, float | None] = {}
        for txn in rows:
            leg = legs.get(id(txn))
            if leg is None:
                continue
            counterparty, sign = leg
            value = mark(txn["symbol"], float(txn["quantity"]), day,
                         last_prices, price_cache=price_cache).value
            flow = (sign * value if value is not None
                    and math.isfinite(value) and value > 0 else None)
            txn["account_transfer"] = {
                "counterparty_group": counterparty, "flow": flow,
            }


def txn_cash_flow_for_groups(txn: dict, filter_groups: set | None) -> float:
    """Signed flow entering the selected accounts; None selects the portfolio.

    A verified transfer crosses a selected scope only when exactly one of its
    account groups is included. Summing every leg blindly would introduce
    phantom portfolio flows between the departure and arrival dates.
    """
    if filter_groups is not None and txn.get("account_group") not in filter_groups:
        return 0.0
    flow = txn_external_cash_flow(txn)
    transfer = txn.get("account_transfer")
    if (not flow and filter_groups is not None and transfer
            and transfer["counterparty_group"] not in filter_groups):
        value = transfer.get("flow")
        if isinstance(value, (int, float)) and math.isfinite(value):
            flow += value
    return flow
