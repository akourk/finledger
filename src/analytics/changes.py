"""Run-over-run diff: what changed since the user last ran the pipeline.

Stores a tiny snapshot in ``cache/last_run.json`` (just enough to diff
against, not the full export).  On the next run we compute deltas:
new txns by date, value change, basis change, gain change, and a few
per-position movers.

Intentionally lightweight — the dashboard surfaces this as a small
"Recent activity" card on the Overview tab.  The data is precomputed
once per run so the JS just reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


_SNAPSHOT_FILE = "last_run.json"


def _load_prev(cache_dir: Path) -> Optional[dict]:
    p = cache_dir / _SNAPSHOT_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_current(cache_dir: Path, payload: dict) -> None:
    p = cache_dir / _SNAPSHOT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: matches the pattern used by the dashboard / json
    # export so a concurrent reader never sees a half-written file.
    import os
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def compute_changes(txns: list[dict],
                    holdings_by_account: list[dict],
                    history: list[dict],
                    cash_summary: dict,
                    basis_methods: dict,
                    cache_dir: Path) -> dict:
    """Return a diff against the previous run's snapshot, then save
    a fresh snapshot for next time.

    Diff includes:
      - ``run_dates``: previous and current run timestamps
      - ``txn_count_delta``: how many new txns since last run
      - ``new_txns_by_date``: count grouped by date
      - ``value_delta`` / ``basis_delta`` / ``realized_delta``
      - ``top_movers``: per-symbol value change since last snapshot,
        positive and negative (top 5 each)
      - ``new_symbols``: symbols not in the previous holdings
      - ``closed_symbols``: symbols held last run but not now

    First run returns an empty diff with ``first_run: True``.
    """
    from datetime import datetime

    prev = _load_prev(cache_dir)

    # Pull headline figures
    fifo = (basis_methods or {}).get("fifo", {}).get("totals", {}) or {}
    cur_value     = sum(h.get("value") or 0 for h in holdings_by_account)
    cur_basis     = float(fifo.get("cost_basis") or 0)
    cur_realized  = float(fifo.get("realized_gain") or 0)
    cur_txn_count = len(txns)

    # Per-symbol value snapshot (used for top_movers)
    cur_by_sym: dict[str, float] = {}
    for h in holdings_by_account:
        if not h.get("symbol"):
            continue
        cur_by_sym[h["symbol"]] = cur_by_sym.get(h["symbol"], 0.0) + (h.get("value") or 0)

    payload = {
        "generated":       datetime.now().isoformat(timespec="seconds"),
        "txn_count":       cur_txn_count,
        "value":           round(cur_value, 2),
        "cost_basis":      round(cur_basis, 2),
        "realized_gain":   round(cur_realized, 2),
        "value_by_symbol": {k: round(v, 2) for k, v in cur_by_sym.items()},
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

    # Save current snapshot for next run BEFORE computing diff so even
    # a first run leaves something for the next one.
    _save_current(cache_dir, payload)

    if prev is None:
        return {"first_run": True, "current": payload}

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
    movers.sort(key=lambda m: m["delta"], reverse=True)
    top_gainers = [m for m in movers if m["delta"] > 0][:5]
    top_losers  = sorted([m for m in movers if m["delta"] < 0],
                         key=lambda m: m["delta"])[:5]

    new_symbols    = sorted(set(cur_by_sym) - set(prev_by_sym))
    closed_symbols = sorted(set(prev_by_sym) - set(cur_by_sym))

    return {
        "first_run":         False,
        "prev_run_at":       prev.get("generated"),
        "current_run_at":    payload["generated"],
        "txn_count_delta":   cur_txn_count - prev.get("txn_count", 0),
        "new_txns_by_date":  sorted(
            [{"date": d, "count": c} for d, c in new_dates.items()],
            key=lambda x: x["date"], reverse=True,
        ),
        "value_delta":       round(cur_value - prev.get("value", 0), 2),
        "basis_delta":       round(cur_basis - prev.get("cost_basis", 0), 2),
        "realized_delta":    round(cur_realized - prev.get("realized_gain", 0), 2),
        "top_gainers":       top_gainers,
        "top_losers":        top_losers,
        "new_symbols":       new_symbols,
        "closed_symbols":    closed_symbols,
    }
