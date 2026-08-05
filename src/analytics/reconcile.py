"""Reconciliation — fin's computed figures vs broker-reported ground truth.

The user supplies ``Reconcile *`` rows in ``data/metadata.csv`` (broker
statement balances, 1099-B realized gains / §1256, 1099-DIV+INT income).
This module pairs each against fin's own computation and reports the delta,
so the dashboard can show a trust-at-a-glance panel and ``alerts.py`` can
flag material drift.

Row kinds (Type / Symbol=account_group / Date / Amount):
  - ``balance``       value of an account_group on a date (the ledger is
                      walked to that EXACT date — see ``_computed_balance``)
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

import re
from datetime import date as _date

from ._shared import (
    INCOME_ACTION_KINDS,
    _balance_sort_key,
    _value_at_date,
    cash_bridge_series,
)
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


def _computed_balance(txns_sorted, cash_series, account_group, target):
    """Account-group value at close-of-day ``target``, walked exactly.

    This used to snap to the history snapshot NEAREST ``target``, which
    was a real bug.  History is sampled semimonthly (15th / EOM / today),
    so a statement dated the 4th of a month resolved to *today's*
    snapshot when today was the 5th — and every transaction in between
    leaked into the comparison.  A deposit made the day AFTER the
    statement date printed as a reconciliation break of exactly that
    deposit's size.

    Snapping BACKWARD instead would be causally sound but still wrong
    for the case this panel is built to serve: a ``Balance Anchor`` row
    is documented to pair with a ``Reconcile Balance`` at the SAME date,
    so a backward snap would exclude the anchor's own true-up and report
    a delta exactly equal to the drift the anchor just corrected.

    ``_value_at_date`` applies the same valuation rules as
    ``history.compute_history`` (USD-skipping, split adjustment,
    option-intrinsic floor, reconstructed broker cash), and was verified
    against every snapshot date to agree within float-summation noise —
    so this is the figure the snapshot would carry, just without
    requiring a snapshot to exist on that date.

    ``bridges`` is deliberately empty: a rollover bridge exists to stop
    TWR seeing a phantom drop while money is in transit between
    custodians, but the broker's statement shows the real, depressed
    balance.  Reconciliation compares against what the statement
    literally says.
    """
    if _parse_iso(target) is None:      # `_value_at_date` compares ISO strings
        return None
    return _value_at_date(
        txns_sorted, target[:10], frozenset({account_group}), [], cash_series)


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


_EXPECTED_RE = re.compile(r"\[expected\s+([+-]?\d+(?:\.\d+)?)\s*\]")


def _expected_delta(note: str) -> float | None:
    """Parse an ``[expected ±N.NN]`` token from a Reconcile row's Note.

    The token declares a KNOWN, documented delta (a K-1 entity the form
    can't see, a broker's per-program reporting threshold, structural
    lot-relief residuals…).  Status is then computed on the RESIDUAL
    (delta − expected): a clean residual renders as "explained" instead
    of warn/off, while a residual outside the band re-flags the row —
    an explanation can never mask NEW drift.  No thousands separators
    in the amount (the Note is a CSV field).
    """
    m = _EXPECTED_RE.search(note or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def compute_reconciliation(txns, history, reconcile_meta):
    """Pair each user ``Reconcile *`` row against fin's computed figure.

    Returns ``{"rows": [...], "summary": {...}}`` or ``None`` when no
    reconcile rows are defined (so the dashboard hides the panel).
    """
    if not reconcile_meta:
        return None

    # Balance rows walk the whole ledger to their statement date, so
    # build the shared inputs once rather than per row.  `last_snapshot`
    # bounds what fin can speak to: a statement dated past the final
    # snapshot (i.e. the future) gets "nodata" instead of silently being
    # answered with today's positions.
    txns_sorted = cash_series = None
    last_snapshot = ""
    if any(r.get("kind") == "balance" for r in reconcile_meta):
        txns_sorted = sorted(txns, key=_balance_sort_key)
        cash_series = cash_bridge_series(txns)
        for s in (history or []):
            d = (s.get("date") or "")[:10]
            if d > last_snapshot:
                last_snapshot = d

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
            if last_snapshot and date_s[:10] > last_snapshot:
                computed = None
            else:
                computed = _computed_balance(
                    txns_sorted, cash_series, ag, date_s)
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
                "date": date_s,
                "reported": round(reported, 2), "computed": None,
                "delta": None, "status": "nodata", "note": note,
                "detail": "no fin figure for this date/account",
            })
            continue

        delta = computed - reported
        expected = _expected_delta(note)
        if expected is not None:
            residual = round(delta - expected, 2)
            if residual == 0:
                residual = 0.0   # normalize -0.00 from float epsilon
            # Band the residual, not the raw delta: a documented delta
            # that still holds renders "explained"; one that drifted
            # away re-flags at the residual's severity.
            status = _status(kind, reported, residual)
            if status == "ok":
                status = "explained"
            detail = (f"expected {expected:+.2f}; "
                      f"residual {residual:+.2f}"
                      + (f" — {detail}" if detail else ""))
        rows.append({
            # `date` (year for form kinds, ISO date for balance) lets the
            # dashboard's drill-down re-derive the composing txn set
            # without parsing the display label.
            "kind": kind, "account_group": ag, "label": label,
            "date": date_s,
            "reported": round(reported, 2), "computed": round(computed, 2),
            "delta": round(delta, 2),
            "status": (status if expected is not None
                       else _status(kind, reported, delta)),
            "expected": (round(expected, 2) if expected is not None else None),
            # The unexplained remainder — what the panel's Δ column
            # shows for rows carrying an expectation (raw delta stays
            # available on hover).
            "residual": (residual if expected is not None else None),
            "note": note, "detail": detail,
        })

    rows.sort(key=lambda x: (x["account_group"], x["kind"], x["label"]))
    summary = {
        "total": len(rows),
        "ok":        sum(1 for r in rows if r["status"] == "ok"),
        "explained": sum(1 for r in rows if r["status"] == "explained"),
        "warn":      sum(1 for r in rows if r["status"] == "warn"),
        "off":       sum(1 for r in rows if r["status"] == "off"),
    }
    return {"rows": rows, "summary": summary}
