"""Open-lot inventory — per-lot detail for every current position.

Single source of per-lot truth for the dashboard: the Holdings tab's
expandable lot tables read ``analytics.lots``, and the Tax tab's
"Approaching Long-Term Status" section (``tax.lt_horizon``) is a
filtered reshape of the same rows (see ``tax._compute_lt_horizon``) —
compute once, consume twice.

Returned shape (``compute_open_lots``)::

    {
      "as_of": "YYYY-MM-DD",
      "positions": [
        {
          "account_group": str, "symbol": str,
          "quantity": float,          # incl. micro lots
          "cost_basis": float,        # incl. micro lots
          "lt_relevant": bool,        # Taxable + non-option — show ST/LT
          "lots": [
            {"date": iso|"", "qty", "basis_per_share", "cost_basis",
             "price"|None, "value"|None, "unrealized_gain"|None,
             "unrealized_pct"|None, "days_held"|None,
             "lt_eligible_date"|None, "days_to_lt"|None,
             "is_long_term"|None},
            ...],                     # sorted by acquired date, undated last
          "micro": {"count", "qty", "cost_basis", "value"|None} | None,
        },
        ...],                         # sorted (account_group, symbol)
      "total_lots": int,
    }

Coverage: every non-cash position in ``holdings_by_account`` (so the
export always anchors to a visible Holdings row) — retirement accounts
and option symbols included.  Option lots are valued with the ×100
contract multiplier (``config.contract_multiplier``); their per-share
figures stay per-share, matching how brokers quote.  Cash is excluded:
USD is never lot-tracked (Savings cost basis is cash principal).

**Dust folding**: real pools accumulate hundreds of micro reward lots
(sub-$1).  Lots with |value| < $1 (or qty < 1e-6 when unpriced) fold
into one ``micro`` aggregate per position so the UI shows the lots that
matter.  Position totals ALWAYS include the folded lots — the
``lots_holdings_basis_parity`` data-health check pins
Σ(lots + micro cost_basis) to the Holdings row's cost basis.

Never emits NaN/Infinity: every division guards its denominator and
falls back to ``None``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..config import CASH_SYMBOLS, contract_multiplier


def _lt_eligible_date(open_d):
    """First calendar date on which selling this lot qualifies as
    long-term (held > 1 year, per IRS Topic 409).  Uses calendar-month
    math so Feb 29 (leap day) maps to Feb 28 of the next year, then +1
    day to land on Mar 1 — the standard convention.
    """
    try:
        anniversary = open_d.replace(year=open_d.year + 1)
    except ValueError:  # Feb 29 in a leap year
        anniversary = open_d.replace(year=open_d.year + 1, month=2, day=28)
    return anniversary + timedelta(days=1)


def _parse_date(iso: str):
    try:
        return datetime.strptime((iso or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def price_lookup(holding_rows: list[dict]) -> dict:
    """``{(account_group, symbol): price}`` + ``{symbol: price}``
    fallback from a holdings table (either the per-account or the
    per-symbol rollup — rows without ``account_group`` only feed the
    symbol-level fallback)."""
    out: dict = {}
    for h in holding_rows or []:
        price = h.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        sym = h.get("symbol", "")
        acct = h.get("account_group", "")
        if acct:
            out[(acct, sym)] = float(price)
        out.setdefault(sym, float(price))
    return out


def open_lot_rows(fifo_state: dict | None, prices: dict,
                  today=None) -> list[dict]:
    """One flat row per open lot across every (account_group, symbol)
    pool in the walker's final state.  Cash pools are skipped; undated
    lots (carried through an avg path or missing a date) are emitted
    with the holding-period fields ``None`` so their basis still counts.
    """
    if not fifo_state or "lots" not in fifo_state:
        return []
    if today is None:
        today = datetime.now().date()

    rows: list[dict] = []
    for (acct, sym), lots in fifo_state["lots"].items():
        if not sym or sym in CASH_SYMBOLS:
            continue
        if not isinstance(lots, list):
            continue                      # avg-method tuple state
        price = prices.get((acct, sym))
        if price is None:
            price = prices.get(sym)
        mult = contract_multiplier(sym)
        for lot in lots:
            qty = float(lot.get("qty", 0) or 0)
            if qty <= 1e-9:
                continue
            basis_per = float(lot.get("basis_per_share", 0) or 0)
            cost_basis = qty * basis_per
            value = (qty * price * mult) if price else None
            unrealized = (value - cost_basis) if value is not None else None
            unrealized_pct = (round(unrealized / cost_basis * 100, 2)
                              if (unrealized is not None and cost_basis > 0)
                              else None)
            open_iso = (lot.get("date") or "")[:10]
            open_d = _parse_date(open_iso)
            if open_d:
                lt_date = _lt_eligible_date(open_d)
                days_held = (today - open_d).days
                days_to_lt = (lt_date - today).days
                lt_iso = lt_date.isoformat()
                is_lt = days_to_lt <= 0
            else:
                open_iso = ""
                days_held = days_to_lt = lt_iso = is_lt = None
            rows.append({
                "account_group": acct,
                "symbol": sym,
                "date": open_iso,
                "qty": round(qty, 8),
                "basis_per_share": round(basis_per, 4),
                "cost_basis": round(cost_basis, 2),
                "price": round(price, 4) if price else None,
                "value": round(value, 2) if value is not None else None,
                "unrealized_gain": (round(unrealized, 2)
                                    if unrealized is not None else None),
                "unrealized_pct": unrealized_pct,
                "days_held": days_held,
                "lt_eligible_date": lt_iso,
                "days_to_lt": days_to_lt,
                "is_long_term": is_lt,
            })
    return rows


def _is_micro(row: dict) -> bool:
    """Dust-folding rule: sub-$1 value when priced, sub-1e-6 qty when
    not — mirrors the spirit of main.py's ``_is_dust`` at lot scale."""
    if row["value"] is not None:
        return abs(row["value"]) < 1.0
    return row["qty"] < 1e-6


def compute_open_lots(fifo_state: dict | None,
                      holdings_by_account: list[dict]) -> dict | None:
    """Group the flat lot rows per Holdings position and fold micro
    lots.  Only positions present in ``holdings_by_account`` are
    emitted (the dust-filtered table the UI anchors to); ``None`` when
    there's no lot state at all."""
    if not fifo_state or "lots" not in fifo_state:
        return None

    prices = price_lookup(holdings_by_account)
    rows = open_lot_rows(fifo_state, prices)
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_key.setdefault((r["account_group"], r["symbol"]), []).append(r)

    from .options import _is_option_symbol

    positions: list[dict] = []
    total_lots = 0
    for h in sorted(holdings_by_account,
                    key=lambda h: (h.get("account_group", ""),
                                   h.get("symbol", ""))):
        acct = h.get("account_group", "")
        sym = h.get("symbol", "")
        if not sym or sym in CASH_SYMBOLS:
            continue
        lots = by_key.get((acct, sym), [])
        visible = [r for r in lots if not _is_micro(r)]
        folded = [r for r in lots if _is_micro(r)]
        visible.sort(key=lambda r: r["date"] or "9999-99-99")

        micro = None
        if folded:
            m_val = sum(r["value"] for r in folded
                        if r["value"] is not None)
            any_val = any(r["value"] is not None for r in folded)
            micro = {
                "count": len(folded),
                "qty": round(sum(r["qty"] for r in folded), 8),
                "cost_basis": round(sum(r["cost_basis"] for r in folded), 2),
                "value": round(m_val, 2) if any_val else None,
            }

        # Per-lot rows drop the grouping keys (redundant inside a
        # position) to keep the export lean.
        for r in visible:
            r.pop("account_group", None)
            r.pop("symbol", None)

        total_lots += len(visible)
        positions.append({
            "account_group": acct,
            "symbol": sym,
            "quantity": round(sum(r["qty"] for r in lots), 8),
            "cost_basis": round(sum(r["cost_basis"] for r in lots), 2),
            "lt_relevant": (h.get("account_type") == "Taxable"
                            and not _is_option_symbol(sym)),
            "lots": visible,
            "micro": micro,
        })

    return {
        "as_of": datetime.now().date().isoformat(),
        "positions": positions,
        "total_lots": total_lots,
    }
