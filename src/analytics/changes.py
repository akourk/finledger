"""Run-over-run diff: what changed since the user last ran the pipeline.

Reads a tiny snapshot in ``cache/last_run.json`` (just enough to diff
against, not the full export).  On the next run we compute deltas:
new txns by date, value change, basis change, gain change, and a few
per-position movers.

Intentionally lightweight — the dashboard surfaces this as a small
"Recent activity" card on the Overview tab.  The data is precomputed
once per run so the JS just reads it. The returned ``current`` snapshot is
pending publication; only the publisher may advance the stored baseline.
"""

from __future__ import annotations

from .. import clock
import json
import math
from pathlib import Path
from typing import Optional


SNAPSHOT_FILE = "last_run.json"


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_snapshot(payload) -> bool:
    """Reject malformed comparison inputs without changing their stored bytes."""
    if not isinstance(payload, dict):
        return False
    version = payload.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        return False
    if not isinstance(payload.get("generated"), str) or not payload["generated"]:
        return False
    count = payload.get("txn_count")
    if type(count) is not int or count < 0 or not _finite_number(payload.get("value")):
        return False
    values, dates = payload.get("value_by_symbol"), payload.get("txn_dates")
    if not isinstance(values, dict) or any(
            not isinstance(symbol, str) or not symbol or not _finite_number(value)
            for symbol, value in values.items()):
        return False
    if not isinstance(dates, list) or len(dates) > count or any(
            not isinstance(day, str) or not day for day in dates):
        return False
    for field in ("cost_basis", "realized_gain"):
        value = payload.get(field)
        if not _finite_number(value) and not (version == 2 and value is None):
            return False
    if version == 2 and payload.get("basis_method") is not None and not isinstance(
            payload["basis_method"], str):
        return False
    return True


def _reject_nonfinite(token):
    raise ValueError("Non-finite snapshot number")


def _load_prev(cache_dir: Path) -> Optional[dict]:
    p = cache_dir / SNAPSHOT_FILE
    try:
        payload = json.loads(p.read_text(encoding="utf-8"),
                             parse_constant=_reject_nonfinite)
    except (OSError, ValueError, UnicodeError):
        return None
    return payload if _valid_snapshot(payload) else None


def compute_changes(txns: list[dict],
                    holdings_by_account: list[dict],
                    basis_totals: dict,
                    cache_dir: Path) -> dict:
    """Read the previous published baseline and prepare a diff without writing.

    ``basis_totals`` is the shared annotated-walker total, never a what-if
    method or a sum of rounded transaction annotations. A valid old snapshot
    still supports activity/value comparisons, but its FIFO basis figures
    cannot be compared with the actual portfolio. Those deltas remain null
    until both snapshots have compatible annotated totals.

    Diff includes:
      - ``prev_run_at`` / ``current_run_at``: published and current timestamps
      - ``txn_count_delta``: how many new txns since last run
      - ``new_txns_by_date``: count grouped by date
      - ``value_delta`` / ``basis_delta`` / ``realized_delta``
      - ``top_gainers`` / ``top_losers``: per-symbol value changes,
        positive and negative (top 5 each)
      - ``new_symbols``: symbols not in the previous holdings
      - ``closed_symbols``: symbols held last run but not now

    First run returns an empty diff with ``first_run: True``.
    """
    from datetime import datetime

    prev = _load_prev(cache_dir)

    # Pull the portfolio's authoritative totals, preserving unavailable data.
    cur_value     = sum(h.get("value") or 0 for h in holdings_by_account)
    cur_basis     = (basis_totals or {}).get("cost_basis")
    cur_realized  = (basis_totals or {}).get("realized_gain")
    cur_txn_count = len(txns)

    # Per-symbol value snapshot (used for top_movers)
    cur_by_sym: dict[str, float] = {}
    for h in holdings_by_account:
        if not h.get("symbol"):
            continue
        cur_by_sym[h["symbol"]] = cur_by_sym.get(h["symbol"], 0.0) + (h.get("value") or 0)

    payload = {
        "schema_version":  2,
        "basis_method":    (basis_totals or {}).get("method"),
        "generated":       clock.now(fallback=datetime.now).isoformat(timespec="seconds"),
        "txn_count":       cur_txn_count,
        "value":           round(cur_value, 2),
        "cost_basis":      round(cur_basis, 2) if _finite_number(cur_basis) else None,
        "realized_gain":   round(cur_realized, 2) if _finite_number(cur_realized) else None,
        "value_by_symbol": {k: round(v, 2) for k, v in sorted(cur_by_sym.items())},
        "txn_dates":       [t.get("date", "") for t in txns if t.get("date")],
    }

    # An empty run — no transactions AND no value — means the caller had
    # nothing (e.g. build_analytics invoked with bare inputs, or a
    # misconfigured data dir).  Never let it overwrite a real snapshot:
    # the next real run would diff against zeros and report the entire
    # portfolio value as "new".  Return first_run so the panel stays
    # hidden; there is nothing meaningful to diff.
    if cur_txn_count == 0 and abs(cur_value) < 0.01:
        return {"first_run": True, "skipped_empty_run": True}

    if prev is None:
        return {"first_run": True, "basis_comparison_available": False,
                "current": payload}

    comparable = (prev.get("schema_version") == 2
                  and prev.get("basis_method") == payload["basis_method"] == "annotated"
                  and all(_finite_number(snapshot.get(field))
                          for snapshot in (prev, payload)
                          for field in ("cost_basis", "realized_gain")))

    # Build diff — per-date COUNT delta, not set membership.  The
    # snapshot stores every txn's date, so a new txn landing on a date
    # that already had activity (same-day re-export refresh, a broker
    # backfilling a missed row) still shows up as +N on that date; the
    # old membership test silently hid those.
    from collections import Counter
    prev_date_counts = Counter(prev.get("txn_dates", []))
    new_dates: dict[str, int] = {}
    for d, c in Counter(payload["txn_dates"]).items():
        delta = c - prev_date_counts.get(d, 0)
        if delta > 0:
            new_dates[d] = delta

    prev_by_sym = prev.get("value_by_symbol", {})
    movers = []
    all_syms = set(cur_by_sym) | set(prev_by_sym)
    for sym in all_syms:
        delta = cur_by_sym.get(sym, 0) - prev_by_sym.get(sym, 0)
        if abs(delta) < 0.01:
            continue
        movers.append({
            "symbol": sym,
            "delta":  round(delta, 2),
            "value":  round(cur_by_sym.get(sym, 0), 2),
            "prev":   round(prev_by_sym.get(sym, 0), 2),
        })
    movers.sort(key=lambda m: (-m["delta"], m["symbol"]))
    top_gainers = [m for m in movers if m["delta"] > 0][:5]
    top_losers  = sorted([m for m in movers if m["delta"] < 0],
                         key=lambda m: (m["delta"], m["symbol"]))[:5]

    new_symbols    = sorted(set(cur_by_sym) - set(prev_by_sym))
    closed_symbols = sorted(set(prev_by_sym) - set(cur_by_sym))

    return {
        "first_run":         False,
        "current":           payload,
        "basis_comparison_available": comparable,
        "prev_run_at":       prev.get("generated"),
        "current_run_at":    payload["generated"],
        "txn_count_delta":   cur_txn_count - prev.get("txn_count", 0),
        "new_txns_by_date":  sorted(
            [{"date": d, "count": c} for d, c in new_dates.items()],
            key=lambda x: x["date"], reverse=True,
        ),
        "value_delta":       round(cur_value - prev.get("value", 0), 2),
        "basis_delta":       (round(payload["cost_basis"] - prev["cost_basis"], 2)
                              if comparable else None),
        "realized_delta":    (round(payload["realized_gain"] - prev["realized_gain"], 2)
                              if comparable else None),
        "top_gainers":       top_gainers,
        "top_losers":        top_losers,
        "new_symbols":       new_symbols,
        "closed_symbols":    closed_symbols,
    }
