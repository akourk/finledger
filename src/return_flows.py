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
from . import valuation
from .valuation import mark


def eligible_account_transfer_pairs(txns: list[dict], *, pairings=None) -> list[tuple[dict, dict]]:
    """Canonical, portfolio-neutral in-kind pairs, shared by flows and transit.

    A caller already holding the basis walk's pairings can reuse that result.
    The arrival confirms a historical internal move; it never supplies an
    earlier valuation. Unmatched departures are not assumed to remain owned.
    """
    ordered = sorted(txns, key=_sort_key) if pairings is None else txns
    paired = (pairings if pairings is not None else _pair_transfers(ordered))["tin_to_tout"]
    result = []
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
        result.append((tout, tin))
    return result


def annotate_account_transfers(txns: list[dict]) -> None:
    """Replace ``account_transfer: {counterparty_group, flow}`` on eligible legs.

    Each signed dollar mark uses its own quantity and closing date, including
    split restatement and option multipliers. Prices use the complete day's
    rows in canonical ingest order, never a later day. Raw transfer amounts
    may be asset units; carried basis is not market value. Neither supplies a
    missing price. Null flow triggers data health, rather than inventing zero.
    Rebuild after refresh: exported metadata is only a price-dependent snapshot.
    """
    for txn in txns:
        txn.pop("account_transfer", None)
    ordered = sorted(txns, key=_sort_key)
    legs: dict[int, tuple[str, int]] = {}
    for tout, tin in eligible_account_transfer_pairs(ordered):
        legs[id(tout)] = (tin["account_group"], -1)
        legs[id(tin)] = (tout["account_group"], 1)
    if not legs:
        return

    last_prices: dict[str, float] = {}
    quote_dates: dict[str, str] = {}
    for day, rows in groupby(ordered, key=lambda t: t.get("date", "")):
        rows = list(rows)
        for txn in rows:
            price = float(txn.get("price", 0) or 0)
            symbol = txn.get("symbol", "")
            if symbol and math.isfinite(price) and price > 0:
                last_prices[symbol] = price
                quote_dates[symbol] = day
        # Transfer legs may straddle a split. A carried transaction quote
        # must use this day's share units before it can price an arrival.
        leg_symbols = {txn["symbol"] for txn in rows if id(txn) in legs}
        if not leg_symbols:
            continue
        prices_on_date = valuation.rebase_transaction_prices(
            {symbol: last_prices[symbol] for symbol in leg_symbols if symbol in last_prices},
            quote_dates, day)
        price_cache: dict[str, float | None] = {}
        for txn in rows:
            leg = legs.get(id(txn))
            if leg is None:
                continue
            counterparty, sign = leg
            value = mark(txn["symbol"], float(txn["quantity"]), day,
                         prices_on_date, price_cache=price_cache).value
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


def transit_in_scope(position: dict, filter_groups: set | None) -> bool:
    """In-transit custody belongs to neither endpoint separately."""
    return filter_groups is None or (
        position["source_group"] in filter_groups
        and position["destination_group"] in filter_groups)


def transit_positions(pairs: list[tuple[dict, dict]], target: str,
                      last_prices: dict[str, float], *,
                      carried_basis: dict[int, float] | None = None,
                      price_cache: dict | None = None) -> list[dict]:
    """Mark eligible departed assets until arrival, with separate custody/basis.

    The matcher has reconciled both endpoints using cached split evidence.
    Keep exported quantities in departure units; convert only the temporary
    valuation quantity to the target date. After a split, undated transaction
    fallbacks cannot establish price units, so require a cached market quote.
    """
    positions = []
    for tout, tin in pairs:
        start, end = tout["date"], tin["date"]
        if not start <= target < end:
            continue
        symbol, quantity = tout["symbol"], float(tout["quantity"])
        issue = None
        factors = [valuation.split_factor_since(symbol, d) for d in (start, target)]
        if not all(math.isfinite(f) and f > 0 for f in factors):
            value, issue = None, "split_during_transfer"
        else:
            ratio = factors[0] / factors[1]
            target_quantity = quantity * ratio
            m = mark(symbol, target_quantity, target,
                     last_prices if ratio == 1 else {}, price_cache=price_cache)
            value = m.value
            if ratio != 1 and m.source != "cache":
                value = None
            if value is None or not math.isfinite(value) or value < 0:
                value, issue = None, "unpriced"
            elif valuation.mark_is_dust(m, target_quantity):
                continue
        position = {
            "source_group": tout["account_group"],
            "destination_group": tin["account_group"],
            "start_date": start, "end_date": end,
            "symbol": symbol, "quantity": quantity,
            "price": value / quantity if value is not None else None,
            "value": value,
            "cost_basis": (carried_basis.get(id(tout), 0.0)
                           if carried_basis is not None else None),
        }
        if issue:
            position["valuation_issue"] = issue
        positions.append(position)
    return positions


def scope_snapshot_value(snapshot: dict, filter_groups: set | None = None) -> float:
    """Posted custody plus eligible transit; rollover cash remains separate."""
    positions = [p for p in snapshot.get("in_transit", ()) if transit_in_scope(p, filter_groups)]
    source = (snapshot.get("valuation_precision") or snapshot) if positions else snapshot
    value = (float(source.get("total", 0) or 0) if filter_groups is None else
             sum(float((source.get("by_account_group") or {}).get(g, 0) or 0)
                 for g in filter_groups))
    return round(value + sum(float(p.get("value") or 0) for p in positions), 2) if positions else value


def scope_snapshot_basis(snapshot: dict, filter_groups: set | None = None) -> float:
    """Posted basis plus carried basis for transit inside the selected scope."""
    positions = [p for p in snapshot.get("in_transit", ()) if transit_in_scope(p, filter_groups)]
    source = (snapshot.get("valuation_precision") or snapshot) if positions else snapshot
    basis = (float(source.get("total_cost_basis", 0) or 0) if filter_groups is None else
             sum(float((source.get("cost_basis_by_group") or {}).get(g, 0) or 0)
                 for g in filter_groups))
    return round(basis + sum(float(p.get("cost_basis") or 0) for p in positions), 2) if positions else basis
