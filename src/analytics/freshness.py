"""Per-account dates of imported transactions, separate from quote freshness.

The latest transaction date cannot establish when a CSV was downloaded or
whether broker activity is missing. Generated reconciliation and balance rows
are not imported activity; parser records carry a CSV filename as ``source``.
"""

from __future__ import annotations

from datetime import date, datetime

from .. import clock


def _day(value) -> date | None:
    """Accept canonical ledger dates without guessing at malformed values."""
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _group(row: dict) -> str | None:
    value = row.get("account_group") or row.get("account")
    return value if isinstance(value, str) and value.strip() else None


def compute_account_freshness(txns: list[dict], holdings_by_account: list[dict], *,
                              as_of: str | date | None = None) -> list[dict]:
    """Return sorted account groups with imported activity through ``as_of``.

    Each result contains ``account_group``, ``account_type``,
    ``latest_transaction_date``, ``days_since_latest``, and ``transaction_count``.
    Counts include valid dated CSV records only, including manually entered CSVs.
    Generated ``auto-*`` sources and old records without CSV provenance cannot
    establish an imported date. No source filename is exported in this summary.

    Closed groups with imported activity remain in the results. Current holdings
    establish a known account even with no eligible records, reported with a
    null date/age and zero count. Unknown or conflicting types remain null.
    """
    if as_of is None:
        cutoff = clock.now(fallback=datetime.now).date()
    elif isinstance(as_of, datetime):
        cutoff = as_of.date()
    elif isinstance(as_of, date):
        cutoff = as_of
    else:
        cutoff = _day(as_of)
        if cutoff is None:
            raise ValueError("Account freshness as_of must be an ISO date")

    groups: dict[str, dict] = {}

    def account(row):
        group = _group(row)
        if group is None:
            return None
        entry = groups.setdefault(group, {"types": set(), "latest": None, "count": 0})
        kind = row.get("account_type")
        if isinstance(kind, str) and kind.strip():
            entry["types"].add(kind)
        return entry

    for holding in holdings_by_account:
        account(holding)
    for txn in txns:
        source = txn.get("source")
        if not isinstance(source, str) or not source.lower().endswith(".csv"):
            continue
        day = _day(txn.get("date"))
        if day is None or day > cutoff:
            continue
        entry = account(txn)
        if entry is None:
            continue
        entry["count"] += 1
        if entry["latest"] is None or day > entry["latest"]:
            entry["latest"] = day

    results = []
    for group, entry in sorted(groups.items()):
        latest = entry["latest"]
        results.append({
            "account_group": group,
            "account_type": next(iter(entry["types"])) if len(entry["types"]) == 1 else None,
            "latest_transaction_date": latest.isoformat() if latest else None,
            "days_since_latest": (cutoff - latest).days if latest else None,
            "transaction_count": entry["count"],
        })
    return results
