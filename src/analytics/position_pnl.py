"""Per-position P&L over trailing windows — the Holdings board's columns.

The Holdings tab's board mode shows, per position, what it is worth and
what it has made you over 1D / 1W / 1M / 3M / YTD / 1Y.  ``Open P&L`` is
the all-time column and already exists on the holdings row
(``unrealized_gain``); everything shorter is computed here.

**The definition, and why it is per-LOT.**  "Open P&L over a window" is
only unambiguous while the share count is constant.  The moment a
position is traded inside the window, two readings diverge:

* *constant-share market move* — ``qty_now x (P_now - P_start)``, which
  books price movement for shares you did not own; and
* *flow-adjusted* — what the shares you hold today actually made you
  over the window.

fin takes the second, because that is what a broker's 1D column already
is: a stock bought this morning shows ``mark - cost``, not ``mark -
yesterday's close``.  Generalizing that to any window falls out of the
open-lot inventory for free — each lot answers for itself::

    lot acquired AFTER the boundary  ->  value_now - cost_basis
    lot acquired BEFORE the boundary ->  qty x (P_now - P_start)

Summed over a position's open lots, that degenerates to exactly the
right thing in every case: an untouched position reduces to the
constant-share move; a position opened inside the window reports its
whole open P&L; a partially-sold position counts only the shares still
open (realized gain belongs to the Performance tab, not to a column
labelled *open*); and a position younger than the window needs no
historical price at all.

No ledger re-walk is involved — :mod:`analytics.lots` has already built
the open-lot inventory for the Holdings tab's expandable detail, so this
is one historical price per (symbol x window) on top of it.

**Split safety.**  Lot quantities are today-basis (the basis walker
rescales lots on a split) and cached closes are today-basis too
(yfinance's ``Close`` is always split-adjusted), so the product needs no
``split_factor_since`` — the same reasoning as ``header``'s
``restate_qty=False``.  A lot's *total* cost basis is split-invariant,
which is why the in-window branch uses it rather than a per-share
figure.

**Unpriceable positions report ``None``, never 0.**  The start mark is
taken with no transaction-price fallback, so it resolves from the price
cache (or, for an option contract, from the underlying's cached close
via the intrinsic floor) or not at all.  A flat last-traded price
carried backwards would silently print a $0 move for a fund whose real
move is unknown — the same rule ``priced_pct`` follows elsewhere.

A genuine $0.00 is still possible and is not the same thing: a mutual
fund whose NAV has not struck yet resolves to the SAME cached close at
both ends of a 1D window, so its move really is nil *in fin's data*.
That is deliberately not re-labelled "unknown" here, because
``header``'s portfolio 1-day change treats it the same way — it sums
values and books the fund as contributing nothing.  Splitting the two
would put the board and the top bar in disagreement about the same
position on the same day.  The freshness caveat those figures share is
the ``prices_as_of`` / ``provisional`` stamp, not a per-position dash.

Returned shape::

    {
      "as_of": "YYYY-MM-DD",
      "windows": [{"key": "1d", "label": "1D", "start_date": "..."}, ...],
      "by_account": [{account_group, symbol, quantity, price, value,
                      cost_basis, open_pnl, open_pnl_pct,
                      windows: {"1d": {pnl, pct, start_value} | None, ...},
                      traded_in: ["1d", ...]}, ...],
      "by_symbol":  [ ...same, keyed by symbol across accounts... ],
    }

``traded_in`` lists the windows in which the position had ANY
transaction.  It drives the board's activity badge: those rows are the
ones where the flow-adjusted figure and a naive constant-share one
disagree, and where realized gain sits outside the column.
"""

from __future__ import annotations

from .. import clock
from datetime import date, datetime, timedelta

from ..config import CASH_SYMBOLS, contract_multiplier
from ..valuation import mark
from .lots import open_lot_rows, price_lookup

# Ordered — the board renders columns in this order.
WINDOWS: tuple[tuple[str, str], ...] = (
    ("1d",  "1D"),
    ("1w",  "1W"),
    ("1m",  "1M"),
    ("3m",  "3M"),
    ("ytd", "YTD"),
    ("1y",  "1Y"),
)


def _months_back(d: date, months: int) -> date:
    """``d`` minus ``months`` calendar months, clamping the day.

    Jan 31 minus 1 month is Dec 31; Mar 31 minus 1 month is Feb 28/29.
    Clamping down is the convention every "1 month ago" control uses,
    and the price lookup's walk-back makes the exact landing day
    immaterial anyway.
    """
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = d.day
    while day > 1:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1
    return date(y, m, 1)


def window_start_dates(as_of: date) -> dict[str, str]:
    """Boundary date per window key.

    The boundary is the date whose CLOSE the window measures from, so a
    lot acquired strictly after it is "inside" the window.  For ``1d``
    that makes the boundary yesterday and any lot acquired today
    in-window, which is what makes a stock bought this morning show
    ``mark - cost`` rather than a full day of movement it missed.

    YTD measures from the prior year's final close for the same reason:
    everything bought this year is in-window.
    """
    return {
        "1d":  (as_of - timedelta(days=1)).isoformat(),
        "1w":  (as_of - timedelta(days=7)).isoformat(),
        "1m":  _months_back(as_of, 1).isoformat(),
        "3m":  _months_back(as_of, 3).isoformat(),
        "ytd": date(as_of.year - 1, 12, 31).isoformat(),
        "1y":  _months_back(as_of, 12).isoformat(),
    }


def _parse_date(iso: str) -> date | None:
    try:
        return datetime.strptime((iso or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _start_price(symbol: str, on_date: str,
                 memo: dict[tuple[str, str], float | None]) -> float | None:
    """The symbol's mark at a window boundary, or ``None``.

    Deliberately passes NO transaction-price fallback: ``mark`` then
    resolves from the price cache, or from the underlying's cached close
    for an option contract (the intrinsic floor), or reports nothing.
    The flat last-traded price ``mark``'s other callers supply describes
    today, and carrying it backwards would turn "we cannot price this
    date" into a confident $0 move.
    """
    key = (symbol, on_date)
    if key not in memo:
        memo[key] = mark(symbol, 1.0, on_date, None, restate_qty=False).price
    return memo[key]


def _window_pnl(lots: list[dict], boundary: str, price_now: float | None,
                mult: float) -> dict | None:
    """Flow-adjusted open P&L for one position over one window.

    ``None`` when the figure cannot be established honestly: no current
    mark, or a lot held across the boundary whose boundary price the
    cache cannot supply.  A position whose lots were ALL acquired inside
    the window needs no historical price and is always computable.
    """
    if price_now is None or price_now <= 0 or not lots:
        return None

    pnl = 0.0
    start_value = 0.0
    for lot in lots:
        qty = lot["qty"]
        acquired = lot["acquired"]
        # Undated lots (carried through an average-cost path) are
        # treated as held across the boundary: they are old by
        # construction, and the alternative — calling them new — would
        # report their entire lifetime gain as this window's move.
        if acquired is not None and acquired > boundary:
            pnl += qty * price_now * mult - lot["cost_basis"]
            start_value += lot["cost_basis"]
        else:
            p0 = lot["_start_price"]
            if p0 is None:
                return None
            pnl += qty * (price_now - p0) * mult
            start_value += qty * p0 * mult

    return {
        "pnl": round(pnl, 2),
        "pct": (round(pnl / start_value * 100, 4)
                if start_value > 0 else None),
        "start_value": round(start_value, 2),
    }


def _traded_windows(txns: list[dict],
                    boundaries: dict[str, str]) -> dict[tuple[str, str], list[str]]:
    """``{(account_group, symbol): [window keys]}`` for positions with any
    transaction inside each window.

    Sourced from the ledger rather than from lot dates because a SELL
    leaves no lot behind — and a partially-sold position is exactly the
    case where an *open* P&L column omits realized gain and the reader
    deserves to know.
    """
    latest: dict[tuple[str, str], str] = {}
    for t in txns or []:
        sym = t.get("symbol") or ""
        if not sym or sym in CASH_SYMBOLS:
            continue
        key = (t.get("account_group") or "", sym)
        d = (t.get("date") or "")[:10]
        if d > latest.get(key, ""):
            latest[key] = d
    return {
        key: [w for w, b in boundaries.items() if d > b]
        for key, d in latest.items()
    }


def compute_position_pnl(fifo_state: dict | None,
                         holdings_by_account: list[dict],
                         txns: list[dict] | None = None,
                         as_of: str | None = None) -> dict | None:
    """Per-position window P&L for the Holdings board.

    Anchored to ``holdings_by_account`` — the same dust-filtered rows the
    Holdings table renders — so every board row has a table row and the
    two can never disagree about which positions exist.
    """
    if not holdings_by_account:
        return None

    as_of_d = _parse_date(as_of or "") or clock.now(fallback=datetime.now).date()
    as_of_iso = as_of_d.isoformat()
    boundaries = window_start_dates(as_of_d)

    prices = price_lookup(holdings_by_account)
    lot_rows = open_lot_rows(fifo_state, prices, today=as_of_d)
    lots_by_key: dict[tuple[str, str], list[dict]] = {}
    for r in lot_rows:
        lots_by_key.setdefault(
            (r["account_group"], r["symbol"]), []).append({
                "qty": r["_qty_exact"],
                "cost_basis": r["_cost_basis_exact"],
                "acquired": r["date"] or None,
            })

    traded = _traded_windows(txns or [], boundaries)
    px_memo: dict[tuple[str, str], float | None] = {}

    by_account: list[dict] = []
    for h in holdings_by_account:
        acct = h.get("account_group", "")
        sym = h.get("symbol", "")
        if not sym:
            continue
        price_now = h.get("price")
        price_now = (float(price_now)
                     if isinstance(price_now, (int, float)) else None)
        value = h.get("value")
        cost_basis = h.get("cost_basis")
        open_pnl = h.get("unrealized_gain")
        mult = contract_multiplier(sym)
        lots = lots_by_key.get((acct, sym), [])

        windows: dict[str, dict | None] = {}
        if sym in CASH_SYMBOLS:
            # Cash has no lots and no mark.  Its "gain" is accrued
            # interest against principal, which arrives as dated
            # transactions rather than as price movement — there is no
            # window figure to compute, and inventing 0 would read as a
            # flat position rather than an inapplicable column.
            windows = {w: None for w, _ in WINDOWS}
        else:
            for w, _label in WINDOWS:
                boundary = boundaries[w]
                for lot in lots:
                    lot["_start_price"] = _start_price(sym, boundary, px_memo)
                windows[w] = _window_pnl(lots, boundary, price_now, mult)

        by_account.append({
            "account_group": acct,
            "account_type": h.get("account_type", ""),
            "symbol": sym,
            "quantity": h.get("quantity"),
            "price": price_now,
            "value": value,
            "cost_basis": cost_basis,
            "open_pnl": open_pnl,
            "open_pnl_pct": (round(open_pnl / cost_basis * 100, 4)
                             if (isinstance(open_pnl, (int, float))
                                 and isinstance(cost_basis, (int, float))
                                 and cost_basis > 0) else None),
            "windows": windows,
            "traded_in": traded.get((acct, sym), []),
        })

    return {
        "as_of": as_of_iso,
        "windows": [{"key": w, "label": label,
                     "start_date": boundaries[w]} for w, label in WINDOWS],
        "by_account": by_account,
        "by_symbol": _aggregate_by_symbol(by_account),
    }


def _aggregate_by_symbol(rows: list[dict]) -> list[dict]:
    """Roll the per-account rows up to one row per symbol.

    A window sums only when every contributing position resolved it; one
    unpriceable account leg makes the symbol's figure unknown rather
    than partial.  In practice the legs agree — they share a symbol and
    therefore a price — so this fires only for genuinely unpriceable
    symbols.
    """
    agg: dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        a = agg.setdefault(sym, {
            "symbol": sym, "quantity": 0.0, "price": r["price"],
            "_value": 0.0, "_basis": 0.0, "_open": 0.0,
            "_has_value": False, "_has_basis": False, "_has_open": False,
            "_win": {w: {"pnl": 0.0, "start": 0.0, "ok": True}
                     for w, _ in WINDOWS},
            "traded_in": set(),
            "accounts": [],
        })
        a["quantity"] += float(r["quantity"] or 0)
        a["accounts"].append(r["account_group"])
        a["traded_in"].update(r["traded_in"])
        for field, tot, flag in (("value", "_value", "_has_value"),
                                 ("cost_basis", "_basis", "_has_basis"),
                                 ("open_pnl", "_open", "_has_open")):
            v = r.get(field)
            if isinstance(v, (int, float)):
                a[tot] += v
                a[flag] = True
        for w, _ in WINDOWS:
            slot = a["_win"][w]
            win = (r["windows"] or {}).get(w)
            if win is None:
                slot["ok"] = False
            else:
                slot["pnl"] += win["pnl"]
                slot["start"] += win["start_value"]

    out: list[dict] = []
    for a in agg.values():
        value = round(a["_value"], 2) if a["_has_value"] else None
        basis = round(a["_basis"], 2) if a["_has_basis"] else None
        open_pnl = round(a["_open"], 2) if a["_has_open"] else None
        windows: dict[str, dict | None] = {}
        for w, _ in WINDOWS:
            slot = a["_win"][w]
            windows[w] = None if not slot["ok"] else {
                "pnl": round(slot["pnl"], 2),
                "pct": (round(slot["pnl"] / slot["start"] * 100, 4)
                        if slot["start"] > 0 else None),
                "start_value": round(slot["start"], 2),
            }
        out.append({
            "symbol": a["symbol"],
            "accounts": sorted(set(a["accounts"])),
            "quantity": round(a["quantity"], 8),
            "price": a["price"],
            "value": value,
            "cost_basis": basis,
            "open_pnl": open_pnl,
            "open_pnl_pct": (round(open_pnl / basis * 100, 4)
                             if (open_pnl is not None and basis
                                 and basis > 0) else None),
            "windows": windows,
            "traded_in": sorted(a["traded_in"]),
        })
    out.sort(key=lambda r: r["symbol"])
    return out
