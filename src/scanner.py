"""File detection and two-pass renaming for broker CSV exports."""

import csv
from collections import defaultdict
from pathlib import Path

from .config import CANONICAL_PREFIXES, SKIP_RENAME


# ---------------------------------------------------------------------------
# Broker detection
# ---------------------------------------------------------------------------

def detect_broker(filepath: Path) -> str:
    """Identify which broker a CSV came from.

    Checks filename patterns first, then falls back to CSV header inspection.
    Returns a key into CANONICAL_PREFIXES, or "manual"/"skip"/"unknown".
    """
    name = filepath.name.lower()

    # Exact matches
    if name == "manual-adjustments.csv":
        return "manual"
    # metadata.csv is the canonical name; retirement-data.csv is the
    # legacy name kept for backwards compat (see src/metadata.py).
    if name in ("metadata.csv", "retirement-data.csv"):
        return "skip"

    # Filename pattern matching
    if "apple-savings" in name or "apple_savings" in name:
        return "apple_savings"
    if "vanguard401k" in name or name.startswith("vanguard-401k"):
        return "vanguard_401k"
    if "voya401k" in name or name.startswith("voya-401k"):
        return "voya_401k"
    if "usaavictorycapital" in name or name.startswith("usaa-roth-ira"):
        return "usaa"

    # Schwab new format: original filenames or already-renamed
    if "_transactions_" in name:
        if "rollover_ira" in name:
            return "schwab_rollover"
        if "roth_contributory_ira" in name:
            return "schwab_roth"
    if name.startswith("schwab-rollover-ira"):
        return "schwab_rollover"
    if name.startswith("schwab-roth-ira"):
        return "schwab_roth"
    # Old schwab format
    if name.startswith("schwabrolloverira"):
        return "schwab_rollover"
    if name.startswith("schwabrothcontributoryira"):
        return "schwab_roth"

    # Coinbase Pro / GDAX (must check before generic coinbase)
    if "gdax" in name or "coinbase-pro" in name or "coinbase_pro" in name:
        return "coinbase_pro"

    # Coinbase (not pro)
    if "coinbase" in name:
        return "coinbase"

    if "robinhood" in name:
        return "robinhood"

    # Fallback: inspect CSV headers
    return _detect_by_headers(filepath)


def _detect_by_headers(filepath: Path) -> str:
    """Detect broker from CSV column headers.

    Scans the first several lines rather than only the first, because some
    exports (notably Coinbase) prefix the real header row with metadata lines
    ("Transactions", "User,...") — the actual "ID,Timestamp,Transaction Type,
    ..." header lands on line 3+.
    """
    try:
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            lines = [f.readline().strip().lower() for _ in range(10)]
    except Exception:
        return "unknown"

    for header in lines:
        if not header:
            continue

        # Robinhood: "Activity Date", "Trans Code", "Instrument", etc.
        if "activity date" in header and "trans code" in header:
            return "robinhood"

        # Coinbase: "Timestamp", "Transaction Type", "Asset", etc.
        if "timestamp" in header and "transaction type" in header and "asset" in header:
            return "coinbase"

        # Coinbase Pro: "portfolio", "trade id", "product", "side", "size", "price", "fee"
        if "trade id" in header and "product" in header and "side" in header:
            return "coinbase_pro"

    return "unknown"


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def scan_data_files(data_dir: Path) -> dict[str, str]:
    """Scan all CSVs in data_dir and return {filename: broker_key}.

    Broker key is one of the CANONICAL_PREFIXES keys, "manual", "skip", or "unknown".
    """
    results = {}
    for csv_file in sorted(data_dir.glob("*.csv")):
        results[csv_file.name] = detect_broker(csv_file)
    return results


# ---------------------------------------------------------------------------
# Two-pass renaming
# ---------------------------------------------------------------------------

def rename_data_files(data_dir: Path, *, dry_run: bool = False) -> dict[str, str]:
    """Rename data files to consistent format: {prefix}.csv or {prefix}-{n}.csv.

    Uses a two-pass approach (temp names first) to avoid collisions.
    Returns {old_name: new_name} for files that were renamed.
    """
    # Group files by their canonical prefix
    prefix_files: dict[str, list[Path]] = defaultdict(list)
    skipped: list[str] = []

    for csv_file in sorted(data_dir.glob("*.csv")):
        broker = detect_broker(csv_file)

        if broker in ("skip", "unknown"):
            skipped.append(csv_file.name)
            continue
        if broker == "manual":
            continue  # never rename manual-adjustments.csv
        if csv_file.name in SKIP_RENAME:
            continue

        prefix = CANONICAL_PREFIXES.get(broker)
        if not prefix:
            continue

        prefix_files[prefix].append(csv_file)

    # Build rename plan: (current_path, final_name)
    rename_plan: list[tuple[Path, str]] = []

    for prefix, files in sorted(prefix_files.items()):
        files.sort(key=lambda f: f.name)

        if len(files) == 1:
            final_name = f"{prefix}.csv"
            if files[0].name != final_name:
                rename_plan.append((files[0], final_name))
        else:
            for i, f in enumerate(files, 1):
                final_name = f"{prefix}-{i}.csv"
                if f.name != final_name:
                    rename_plan.append((f, final_name))

    if not rename_plan:
        return {}

    if dry_run:
        renames = {}
        for orig_path, final_name in rename_plan:
            renames[orig_path.name] = final_name
            print(f"  {orig_path.name} -> {final_name}")
        return renames

    # Pass 1: rename to temp names to avoid collisions
    temp_paths: list[tuple[Path, str]] = []
    for orig_path, final_name in rename_plan:
        tmp_path = orig_path.parent / f".tmp-{orig_path.name}"
        orig_path.rename(tmp_path)
        temp_paths.append((tmp_path, final_name))

    # Pass 2: rename from temp to final names
    renames = {}
    for tmp_path, final_name in temp_paths:
        final_path = tmp_path.parent / final_name
        tmp_path.rename(final_path)
        orig_name = tmp_path.name.removeprefix(".tmp-")
        renames[orig_name] = final_name
        print(f"  {orig_name} -> {final_name}")

    return renames
