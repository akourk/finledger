"""Reconciliation — fin's computed figures vs broker-reported ground truth.

The user supplies ``Reconcile *`` rows in ``data/metadata.csv`` (broker
statement balances, 1099-B realized gains / §1256, 1099-DIV+INT income).
This module pairs each against fin's own computation and reports the delta,
so the dashboard can show a trust-at-a-glance panel and ``alerts.py`` can
flag material drift.

Row kinds (Type / Symbol=account_group / Date / Amount):
  - ``balance``       value of an account_group on a date (vs nearest
                      history snapshot)
  - ``realized``      net realized gain for an account/year, EXCLUDING
                      §1256 (matches a 1099-B equity grand-total)
  - ``section_1256``  net §1256 gain for an account/year
  - ``income``        dividends + interest for an account/year (matches
                      1099-DIV box 1a + 1099-INT)

Deltas are expected to be non-zero in a few legitimate cases — wash-sale
loss deferral (fin computes economic gain), broker non-FIFO lot relief,
and cash-sweep interest that brokers report on the 1099 but omit from the
activity CSV.  The panel surfaces them rather than hiding them.
"""

from __future__ import annotations

from datetime import date as _date

from ._shared import INCOME_ACTION_KINDS
from .tax import SECTION_1256_UNDERLYINGS, _parse_option_symbol


def _parse_iso(s):
    try:
        return _date.fromisoformat((s or "")[:10])
    except (ValueError, TypeError):
        return None


def _is_1256(symbol) -> bool:
    parsed = _parse_option_symbol(symbol or "")
    return bool(parsed and parsed["underlying"] in SECTION_1256_UNDERLYINGS)


def _status(kind: str, reported: float, delta: float) -> str:
    """ok / warn / off for color-coding.

    Percentage-based with an absolute floor so tiny-dollar items don't
    flash.  **Balance** checks get looser bands + a bigger floor because
    they're inherently noisy: the statement date rarely lines up with a
    fin snapshot to the day, intraday-vs-close prices differ, fin omits
    broker cash-sweep balances, and un-tickerable funds (e.g. a 401k CIT)
    are valued via a proxy — a 1–2% drift there is expected, not a bug.
    **Form figures** (realized / income / §1256 — exact dollar amounts off
    a 1099) stay tight.
    """
    a = abs(delta)
    pct = a / (abs(reported) or 1.0)
    if kind == "balance":
        if a < 25.0 or pct < 0.015:
            return "ok"
        return "warn" if pct < 0.04 else "off"
    if a < 5.0 or pct < 0.005:
        return "ok"
    return "warn" if pct < 0.02 else "off"


def _computed_balance(history, account_group, target):
    """Account-group value from the history snapshot nearest ``target``.
    Returns (value, gap_days) or (None, None)."""
    if not history or target is None:
        return None, None
    best = None
    for s in history:
        d = _parse_iso(s.get("date"))
        if not d:
            continue
        gap = abs((d - target).days)
        if best is None or gap < best[0]:
            best = (gap, s)
    if best is None:
        return None, None
    gap, snap = best
    val = float((snap.get("by_account_group") or {}).get(account_group, 0.0))
    return val, gap


def _computed_realized(txns, account_group, year, *, s1256: bool) -> float:
    total = 0.0
    for t in txns:
        if t.get("account_group") != account_group:
            continue
        if (t.get("date") or "")[:4] != year:
            continue
        rg = t.get("realized_gain")
        if not isinstance(rg, (int, float)):
            continue
        if _is_1256(t.get("symbol")) == s1256:
            total += rg
    return total


def _computed_income(txns, account_group, year, buckets) -> float:
    """Sum income for an account/year across the given income buckets.
    ``buckets`` is e.g. ``("dividends", "interest")`` to match a
    1099-DIV+INT, or ``("rewards", "lending")`` to match a crypto
    1099-MISC "Other Income"."""
    total = 0.0
    for t in txns:
        if t.get("account_group") != account_group:
            continue
        if (t.get("date") or "")[:4] != year:
            continue
        if INCOME_ACTION_KINDS.get(t.get("action")) in buckets:
            total += float(t.get("amount", 0) or 0)
    return total


def compute_reconciliation(txns, history, reconcile_meta):
    """Pair each user ``Reconcile *`` row against fin's computed figure.

    Returns ``{"rows": [...], "summary": {...}}`` or ``None`` when no
    reconcile rows are defined (so the dashboard hides the panel).
    """
    if not reconcile_meta:
        return None

    rows = []
    for r in reconcile_meta:
        kind = r.get("kind")
        ag = r.get("account_group")
        reported = float(r.get("amount", 0) or 0)
        date_s = r.get("date") or ""
        yr = date_s[:4]
        note = r.get("note") or ""
        detail = ""

        if kind == "balance":
            computed, gap = _computed_balance(history, ag, _parse_iso(date_s))
            if computed is not None and gap and gap > 7:
                detail = f"nearest snapshot {gap}d away"
            label = f"Balance @ {date_s}"
        elif kind == "realized":
            computed = _computed_realized(txns, ag, yr, s1256=False)
            label = f"Realized gains {yr}"
        elif kind == "section_1256":
            computed = _computed_realized(txns, ag, yr, s1256=True)
            label = f"§1256 gains {yr}"
        elif kind == "income":
            # div + int + lending: brokers commonly report stock-lending
            # income as "substitute interest" bundled INTO the 1099-INT
            # (verified for Robinhood: fin Interest + Lending == 1099-INT
            # box 1 to the cent every year), so the lending bucket must be
            # included for the income check to reconcile.
            computed = _computed_income(
                txns, ag, yr, ("dividends", "interest", "lending"))
            label = f"Income {yr}"
        elif kind == "other_income":
            computed = _computed_income(txns, ag, yr, ("rewards", "lending"))
            label = f"Other income {yr}"
        else:
            continue

        if computed is None:
            rows.append({
                "kind": kind, "account_group": ag, "label": label,
                "reported": round(reported, 2), "computed": None,
                "delta": None, "status": "nodata", "note": note,
                "detail": "no fin figure for this date/account",
            })
            continue

        delta = computed - reported
        rows.append({
            "kind": kind, "account_group": ag, "label": label,
            "reported": round(reported, 2), "computed": round(computed, 2),
            "delta": round(delta, 2), "status": _status(kind, reported, delta),
            "note": note, "detail": detail,
        })

    rows.sort(key=lambda x: (x["account_group"], x["kind"], x["label"]))
    summary = {
        "total": len(rows),
        "ok":   sum(1 for r in rows if r["status"] == "ok"),
        "warn": sum(1 for r in rows if r["status"] == "warn"),
        "off":  sum(1 for r in rows if r["status"] == "off"),
    }
    return {"rows": rows, "summary": summary}
