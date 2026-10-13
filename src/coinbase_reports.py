"""Conservative report-recency evidence for Coinbase fee-only overlaps.

Coverage is a fallback heuristic, not proof of when a report was generated.
No filesystem times, filename ordering, or import clock participates.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import hashlib
import json
import re


_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:Z| UTC|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
_GENERATION_LABELS = {"generated at", "report generated at"}
_TRADE_ACTIONS = {"Buy", "Sell", "Advanced Trade Buy", "Advanced Trade Sell"}
_EXACT_NUMBERS = {
    "Quantity Transacted", "Price at Transaction", "Subtotal",
    "Total (inclusive of fees and/or spread)",
}


def report_timestamp(value: str) -> str | None:
    """Return a comparable UTC instant only for a complete, zoned timestamp."""
    value = value.strip()
    if not _TIMESTAMP.fullmatch(value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace(" UTC", "+00:00")
                                       .replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def generation_timestamp(preamble: list[list[str]]) -> str | None:
    """Recognize explicit generation labels, never arbitrary preamble dates.

    Reports without these labels use the activity fallback. Unknown, ambiguous,
    date-only, or timezone-free metadata cannot establish generation order.
    """
    found = []
    for cells in preamble:
        cells = [cell.strip() for cell in cells]
        if not cells:
            continue
        label = cells[0].rstrip(":").casefold()
        value = cells[1] if len(cells) == 2 else ""
        if len(cells) == 1 and ":" in cells[0]:
            label, value = cells[0].split(":", 1)
            label = label.strip().casefold()
        if label in _GENERATION_LABELS:
            found.append(report_timestamp(value))
    return found[0] if found and None not in found and len(set(found)) == 1 else None


def exact_trade_identity(row: dict) -> str | None:
    """Fingerprint every non-fee raw field, with lossless numeric comparison.

    Positive broker-ID evidence and full timestamps avoid treating same-day
    lookalikes as a verified fee-only conflict. Blank economic cells are not
    equivalent to explicit zero. Extra broker columns must also agree.
    """
    if row.get("Transaction Type", "").strip() not in _TRADE_ACTIONS:
        return None
    if any(not row.get(key, "").strip()
           for key in ("ID", "Price Currency", "Fees and/or Spread")):
        return None
    timestamp = report_timestamp(row.get("Timestamp", ""))
    if timestamp is None or any(not row.get(key, "").strip() for key in _EXACT_NUMBERS):
        return None
    fields = {key: str(value).strip() for key, value in row.items()
              if key != "Fees and/or Spread"}
    fields["Timestamp"] = timestamp
    # Use the normal strict grammar first, but never use its float result for
    # evidence: cents/eight-digit hashing is intentionally too loose here.
    from .parsers._helpers import _num
    for key in _EXACT_NUMBERS:
        value = fields[key]
        try:
            _num(row[key])
        except ValueError:
            return None
        if value.startswith("(") and value.endswith(")"):
            value = "-" + value[1:-1]
        value = re.sub(r"^([+-]?)\$", r"\1", value).replace(",", "")
        try:
            number = Decimal(value)
        except DecimalException:
            return None
        # A compact coefficient/exponent avoids allocating an enormous string
        # for a finite-underflow input such as 1e-1000000000. Decimal.normalize
        # would instead round long coefficients under the active context.
        sign, digits, exponent = number.as_tuple()
        coefficient = "".join(map(str, digits))
        stripped = coefficient.rstrip("0")
        fields[key] = ([sign, stripped, exponent + len(coefficient) - len(stripped)]
                       if stripped else [0, "0", 0])
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def resolve_fee_conflict(rows: list[dict]) -> list[dict] | None:
    """Select one complete source only for proven Coinbase fee-only conflicts.

    Return None when the narrow exception is inapplicable; the caller retains
    the general fee-conflict failure. Do not mutate input rows or fee evidence.
    """
    by_source = defaultdict(list)
    for row in rows:
        evidence = row.get("_coinbase_import") or {}
        if evidence.get("format") != "coinbase_transaction_history" or not evidence.get("identity"):
            return None
        by_source[row["source"]].append(row)
    identities = [Counter(row["_coinbase_import"]["identity"] for row in group)
                  for group in by_source.values()]
    if not identities or any(identity != identities[0] for identity in identities[1:]):
        return None

    reports = []
    for source, group in sorted(by_source.items()):
        dates = {(row["_coinbase_import"].get("generated_at"),
                  row["_coinbase_import"].get("latest_transaction_at")) for row in group}
        if len(dates) != 1:
            return None
        generated, latest = dates.pop()
        reports.append({"source": source, "generated_at": generated,
                        "latest_transaction_at": latest,
                        "fees": sorted(row.get("fees", 0) for row in group)})
    # A common criterion for the whole group avoids non-transitive pairwise
    # comparisons when just some of the reports have generation metadata.
    criterion = ("generated_at" if all(report["generated_at"] for report in reports)
                 else "latest_transaction_at")
    if not all(report[criterion] for report in reports):
        raise ValueError("Conflicting fee evidence across overlapping exports; "
                         "Coinbase report recency is unavailable. Review source files.")
    newest = max(report[criterion] for report in reports)
    winners = [report["source"] for report in reports if report[criterion] == newest]
    distributions = [Counter((row["_coinbase_import"]["identity"], row.get("fees", 0))
                             for row in by_source[source]) for source in winners]
    if any(distribution != distributions[0] for distribution in distributions[1:]):
        raise ValueError("Conflicting fee evidence across overlapping exports; "
                         "Coinbase report recency is tied. Review source files.")
    # Tied sources may be interchangeable only after their full per-identity
    # fee evidence agrees. Lexical ordering then selects provenance, not fees.
    selected = winners[0]
    resolution = {"criterion": criterion, "selected_source": selected, "reports": reports}
    return [dict(row, fee_resolution=resolution) for row in by_source[selected]]
