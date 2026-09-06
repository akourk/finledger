"""Deduplication and JSON export."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from .clock import now
from .io_safe import replace_files


def _txn_hash(txn: dict) -> str:
    """Compute a hash for deduplication.

    Based on the core identity fields — two rows from overlapping CSV exports
    with the same (date, account, symbol, action, quantity, price, amount)
    are considered duplicates.
    """
    key = "|".join([
        txn.get("date", ""),
        txn.get("account", ""),
        txn.get("symbol", ""),
        txn.get("action", ""),
        f"{txn.get('quantity', 0):.8f}",
        f"{txn.get('price', 0):.8f}",
        f"{txn.get('amount', 0):.2f}",
    ])
    return hashlib.md5(key.encode()).hexdigest()


def deduplicate(txns: list[dict]) -> list[dict]:
    """Remove cross-file duplicates while preserving intra-file duplicates.

    Identical rows within the same source file are legitimate (e.g. 3 identical
    CBETH sells in one Coinbase export). Identical rows across different source
    files are overlapping exports. For each identity hash, we keep the max count
    from any single source file.

    Returns (deduped_list, num_removed).
    """
    from collections import Counter, defaultdict

    # Count occurrences of each hash per source file
    hash_source_counts: dict[str, Counter] = defaultdict(Counter)
    hash_txns: dict[str, list[dict]] = defaultdict(list)

    for txn in txns:
        h = _txn_hash(txn)
        source = txn.get("source", "")
        hash_source_counts[h][source] += 1
        hash_txns[h].append(txn)

    # For each hash, keep the max count from any single source file
    result = []
    for h, source_counts in hash_source_counts.items():
        keep_count = max(source_counts.values())
        # Take from the source with the most (they're all identical anyway,
        # but this preserves the correct source attribution)
        best_source = source_counts.most_common(1)[0][0]
        kept = 0
        for txn in hash_txns[h]:
            if txn.get("source", "") == best_source and kept < keep_count:
                result.append(txn)
                kept += 1

    return result, len(txns) - len(result)


def export_json(txns: list[dict], output_path: Path, *,
                holdings: list[dict] | None = None,
                holdings_by_account: list[dict] | None = None,
                history: list[dict] | None = None,
                basis_methods: dict | None = None,
                basis_totals: dict | None = None,
                cash_summary: dict | None = None,
                retirement_meta: dict | None = None,
                analytics: dict | None = None,
                sector_of: dict[str, str] | None = None,
                display_of: dict[str, str] | None = None):
    """Write transactions (and optional holdings/history/basis/analytics) to a JSON file."""
    txns_sorted = sorted(txns, key=lambda t: (t.get("date", ""), t.get("account", "")))

    # Enforce a consistent field order so dashboard columns are logical.
    # Any fields not listed here appear at the end in their natural order.
    _FIELD_ORDER = [
        "date", "account_group", "account_type", "account", "symbol",
        "action", "raw_action", "quantity", "price", "fees", "amount",
        "balance", "value", "cost_basis", "realized_gain", "cash_flow",
        "holding_days", "basis_effect", "description", "source", "seq",
    ]

    # Internal analytics intermediates that live on the txn dict in memory
    # but should not bloat the exported JSON.  ``lot_breakdown`` is the
    # per-lot FIFO detail consumed by analytics/tax.py (form_8949, ST/LT
    # split); the dashboard reads the precomputed results, not the raw lots.
    #
    # NOTE: ``seq`` is deliberately NOT in here.  ``--refresh-prices``
    # reloads this file instead of re-parsing the CSVs, and `seq` is the
    # only record of the order the full pipeline's lot walkers ran in —
    # strip it and the refresh path silently relieves different lots.
    # See pipeline_stages.assign_ingest_seq.
    _INTERNAL_FIELDS = {"lot_breakdown", "basis_override"}

    def _ordered(txn: dict) -> dict:
        ordered = {}
        for key in _FIELD_ORDER:
            if key in txn:
                ordered[key] = txn[key]
        for key in txn:
            if key not in ordered and key not in _INTERNAL_FIELDS:
                ordered[key] = txn[key]
        return ordered

    txns_ordered = [_ordered(t) for t in txns_sorted]

    from .actions import to_json_dict as _action_catalog
    from .analytics.tax import tax_tables_to_json as _tax_tables

    data = {
        "generated": now(fallback=datetime.now).isoformat(timespec="seconds"),
        "count": len(txns_ordered),
        "holdings": holdings or [],
        "holdings_by_account": holdings_by_account or [],
        "history": history or [],
        # The lot-method COMPARISON table (four pure what-if walks).
        "basis_methods": basis_methods or {},
        # The portfolio's REAL totals, from the annotated walk.  Read
        # this, not basis_methods, for any published figure — see
        # pipeline_stages.build_annotated_basis_totals and docs/AUDIT.md F-033.
        "basis_totals": basis_totals or {},
        "cash_summary": cash_summary or {},
        "retirement_meta": retirement_meta or {},
        "analytics": analytics or {},
        "sector_of": sector_of or {},
        "display_of": display_of or {},
        "action_catalog": _action_catalog(),
        # Canonical federal tax tables (brackets / LTCG / std deduction /
        # 401k limit / Roth-MAGI / §1256) — single source of truth read by
        # the dashboard JS instead of hardcoded copies.  See
        # analytics.tax.tax_tables_to_json.
        "tax_tables": _tax_tables(),
        "transactions": txns_ordered,
    }

    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    replace_files({Path(output_path): payload.encode("utf-8")})
