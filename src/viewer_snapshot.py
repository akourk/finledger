"""Versioned, read-only viewer packages from already computed dashboard data.

This module does not import the financial engine or market-data providers.
Validation checks transport/rendering boundaries, not accounting correctness.
"""

import json
import math
import os
import re
from datetime import date
from pathlib import Path

from .io_safe import replace_files

VIEWER_FORMAT = "finledger-viewer"
VIEWER_VERSION = 1
MAX_SNAPSHOT_BYTES = 25 * 1024 * 1024
MAX_JSON_DEPTH = 64

REQUIRED_ARRAYS = ("transactions", "holdings", "holdings_by_account", "history")
REQUIRED_OBJECTS = (
    "analytics", "basis_totals", "basis_methods", "cash_summary",
    "retirement_meta", "sector_of", "display_of", "action_catalog", "tax_tables",
)
_RECORD_NUMBERS = (
    "quantity", "price", "fees", "amount", "balance", "value", "cost_basis",
    "realized_gain", "cash_flow", "total", "total_cost_basis", "priced_pct",
    "net_contributed", "unrealized_gain", "benchmark_spy", "benchmark_spy_price",
)
_BREAKDOWNS = (
    "by_account_group", "by_account_type", "by_sector", "cost_basis_by_group",
    "cost_basis_by_type",
)
_RECORD_STRINGS = (
    "symbol", "account", "account_group", "account_type", "action", "raw_action",
    "description", "source", "sector", "basis_effect",
)


def _invalid(reason):
    # Static diagnostics deliberately omit input values, labels and filenames.
    raise ValueError("Invalid viewer snapshot: " + reason)


def _calendar_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        _invalid("expected a calendar date")
    try:
        date.fromisoformat(value)
    except ValueError:
        _invalid("expected a calendar date")
    return value


def _validate_json(value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            _invalid("JSON nesting exceeds the limit")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                _invalid("object keys must be strings")
            if any(key in {"__proto__", "prototype", "constructor"} for key in item):
                _invalid("unsafe object key")
            if any("<" in key or ">" in key for key in item):
                _invalid("markup is not supported")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            if not finite:
                _invalid("numbers must be finite")
        elif isinstance(item, str):
            if "<" in item or ">" in item:
                _invalid("markup is not supported")
        elif item is not None and not isinstance(item, (int, bool)):
            _invalid("unsupported JSON value")


def _number(value, *, nullable=False):
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid("financial fields must be numbers")


def _records(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        _invalid("record fields must be arrays of objects")
    return value


def _financial_record(row):
    for field in _RECORD_STRINGS:
        if field in row and not isinstance(row[field], str):
            _invalid("record labels must be strings")
    for field in _RECORD_NUMBERS:
        if field in row:
            _number(row[field], nullable=field in {
                "price", "value", "balance", "cost_basis", "realized_gain", "unrealized_gain",
                "benchmark_spy", "benchmark_spy_price",
            })
    for field in _BREAKDOWNS:
        if field in row:
            if not isinstance(row[field], dict):
                _invalid("financial breakdowns must be objects")
            for value in row[field].values():
                _number(value)
    for field in ("positions", "in_transit"):
        if field in row:
            for position in _records(row[field]):
                _financial_record(position)
    if "valuation_precision" in row:
        if not isinstance(row["valuation_precision"], dict):
            _invalid("valuation precision must be an object")
        _financial_record(row["valuation_precision"])


def _validate_data(data):
    if not isinstance(data, dict) or "format" in data or "files" in data:
        _invalid("expected computed dashboard data")
    for field in REQUIRED_ARRAYS:
        for row in _records(data.get(field)):
            _financial_record(row)
            if field in {"transactions", "history"}:
                _calendar_date(row.get("date"))
    for field in REQUIRED_OBJECTS:
        if not isinstance(data.get(field), dict):
            _invalid("required dashboard objects are missing or invalid")
    if "actions" in data["action_catalog"]:
        for action in _records(data["action_catalog"]["actions"]):
            if not isinstance(action.get("name"), str):
                _invalid("action names must be strings")
            color = action.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                _invalid("action colors must be hexadecimal")
    if "count" in data:
        if (isinstance(data["count"], bool) or not isinstance(data["count"], (int, float))
                or data["count"] != len(data["transactions"])):
            _invalid("transaction count does not match")
    for field in ("as_of", "snapshot_date"):
        if field in data:
            _calendar_date(data[field])
    if "generated" in data:
        if not isinstance(data["generated"], str):
            _invalid("expected a generation date")
        _calendar_date(data["generated"][:10])
    # Exactly the existing dashboard precedence; no wall-clock fallback.
    selected = (data.get("as_of") or data.get("snapshot_date")
                or (data["history"][-1]["date"] if data["history"] else None)
                or (data.get("generated", "")[:10]))
    return _calendar_date(selected)


def validate_viewer_snapshot(snapshot):
    """Reject unsupported packages and malformed rendering inputs."""
    _validate_json(snapshot)
    if not isinstance(snapshot, dict) or snapshot.get("format") != VIEWER_FORMAT:
        _invalid("unsupported format")
    version = snapshot.get("version")
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version != VIEWER_VERSION:
        _invalid("unsupported version")
    cutoff = _calendar_date(snapshot.get("as_of"))
    data = snapshot.get("data")
    if _validate_data(data) != cutoff or data.get("as_of") != cutoff:
        _invalid("snapshot dates do not agree")


def build_viewer_snapshot(data):
    """Wrap a complete payload without recalculating or rounding any values."""
    _validate_json(data)
    cutoff = _validate_data(data)
    canonical = dict(data, as_of=cutoff)
    snapshot = {"format": VIEWER_FORMAT, "version": VIEWER_VERSION,
                "as_of": cutoff, "data": canonical}
    validate_viewer_snapshot(snapshot)
    return snapshot


def _reject_constant(_value):
    _invalid("numbers must be finite")


def export_viewer_snapshot(source_path, destination):
    """Read computed JSON and atomically publish a separate viewer package.

    Inputs and cache state are never valid destinations. Like ``replace_files``,
    this assumes a single writer and does not promise power-loss durability.
    """
    from .config import CACHE_DIR, DATA_DIR

    source, target = Path(source_path), Path(destination)
    resolved_target = target.resolve()
    if (source.resolve() == resolved_target
            or (target.exists() and source.exists() and os.path.samefile(source, target))):
        _invalid("source and destination must differ")
    if any(resolved_target.is_relative_to(root.resolve()) for root in (DATA_DIR, CACHE_DIR)):
        _invalid("destination must stay outside inputs and caches")
    if target.is_symlink():
        _invalid("destination must not be a symbolic link")
    with source.open("rb") as handle:
        contents = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(contents) > MAX_SNAPSHOT_BYTES:
        _invalid("file exceeds the size limit")
    try:
        data = json.loads(contents.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        _invalid("source is not valid UTF-8 JSON")
    snapshot = build_viewer_snapshot(data)
    contents = json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    if len(contents) > MAX_SNAPSHOT_BYTES:
        _invalid("file exceeds the size limit")
    replace_files({target: contents})
    return snapshot
