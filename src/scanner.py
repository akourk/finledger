"""File detection and two-pass renaming for broker CSV exports."""

import csv
import tempfile
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

    # Legacy interrupted renames may leave these behind. Keep them intact
    # and let ingestion report an unrecognized input until manually recovered.
    if name.startswith(".tmp-"):
        return "unknown"

    # Exact matches
    if name == "manual-adjustments.csv":
        return "manual"
    # metadata.csv is the canonical name; retirement-data.csv is the
    # legacy name kept for backwards compat (see src/metadata.py).
    if name in ("metadata.csv", "retirement-data.csv", "account-mappings.csv"):
        return "skip"

    # Broker REFERENCE reports — Coinbase's tax-center "raw transactions"
    # / "gain/loss" downloads and Robinhood's consolidated 1099 CSV.
    # These are per-lot / per-form summaries of activity the regular
    # transaction CSVs already carry; ingesting them would double-count.
    # They're kept in data/ as broker ground truth for lot-level
    # reconciliation, never parsed as transactions.  (Must check BEFORE
    # the generic "coinbase" / "robinhood" filename matches.)
    if "rawtx" in name or "gainloss" in name or "1099" in name:
        return "skip"

    # Filename pattern matching
    if "apple-savings" in name or "apple_savings" in name:
        return "apple_savings"
    if "state-farm-fcu" in name or name.startswith("sfcu"):
        return "sfcu"
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

    # Robinhood Apex-era hand-entered transactions (must check before
    # the generic "robinhood" match — the conventional filename contains
    # both words, e.g. "robinhood apex transactions.csv").
    if "apex" in name:
        return "robinhood_apex"

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

        # State Farm FCU (and other credit unions on the same online-
        # banking platform): the export is named generically
        # ("ExportedTransactions.csv"), so the header IS the detection.
        # "posting date" + "posting status" is the distinctive pair —
        # "transaction type" alone is shared with Coinbase.
        if "posting date" in header and "posting status" in header:
            return "sfcu"

        # Robinhood: "Activity Date", "Trans Code", "Instrument", etc.
        if "activity date" in header and "trans code" in header:
            return "robinhood"

        # Robinhood Apex-era hand-entered CSV (transcribed from the old
        # 1099 PDFs — see parsers/robinhood_apex.py for the format).
        if "security description" in header and "transaction description" in header:
            return "robinhood_apex"

        # Coinbase: "Timestamp", "Transaction Type", "Asset", etc.
        if "timestamp" in header and "transaction type" in header and "asset" in header:
            return "coinbase"

        # Coinbase Pro/GDAX account ledger (the supported paired-leg format).
        if "amount/balance unit" in header and "trade id" in header and "time" in header:
            return "coinbase_pro"

        # Other Pro exports are identified so the parser can report their
        # unsupported columns explicitly instead of silently omitting them.
        if "trade id" in header and "product" in header and "side" in header:
            return "coinbase_pro"

        # Coinbase reference reports (see the filename check above) —
        # recognized by their distinctive columns in case the files get
        # renamed: the gain/loss report's per-lot "Tax lot ID", or the
        # raw-transactions report's acquired/disposed column pair.
        if "tax lot id" in header:
            return "skip"
        if "asset acquired" in header and "asset disposed" in header:
            return "skip"

        # Robinhood consolidated 1099 CSV — a multi-section file where
        # column 0 tags each row's form ("1099-DIV" / "1099-INT" /
        # "1099-B" / "1099-MISC") and each section carries its own
        # header row.  Detected header-first so a fresh (UUID-named)
        # yearly download is skipped without renaming.  The 1099-DIV
        # header sits on line 0; the 1099-B header (per-lot detail we'll
        # later consume for lot relief) is a few lines down.
        if "ordinary div" in header and "qualified div" in header:
            return "skip"
        if "date acquired" in header and "sale date" in header:
            return "skip"

    return "unknown"


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def check_pending_renames(data_dir: Path) -> None:
    """Do not ingest a partial ledger after an interrupted rename."""
    if any(data_dir.glob(".fin-rename-*")):
        raise ValueError("An incomplete CSV rename needs recovery. Restore the "
                         "original filenames from .fin-rename-* in the data "
                         "directory, then remove the empty recovery directory "
                         "before importing again.")


def scan_data_files(data_dir: Path) -> dict[str, str]:
    """Scan all CSVs in data_dir and return {filename: broker_key}.

    Broker key is one of the CANONICAL_PREFIXES keys, "manual", "skip", or "unknown".
    """
    check_pending_renames(data_dir)
    results = {}
    for csv_file in sorted(data_dir.glob("*.csv"), key=lambda p: p.name):
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
    from .broker_lots import _is_robinhood_1099_file, robinhood_1099_tax_year

    check_pending_renames(data_dir)

    # Group files by their canonical prefix
    prefix_files: dict[str, list[Path]] = defaultdict(list)
    skipped: list[str] = []
    # Consolidated 1099s are scanner-"skip" (reference reports, never
    # parsed as transactions) but still get the self-naming treatment:
    # a fresh UUID-named yearly download renames to
    # robinhood-1099-{TAX YEAR}.csv (year read from the file itself).
    plan_1099: list[tuple[Path, str]] = []
    claimed_1099: set[str] = {p.name for p in data_dir.glob("robinhood-1099-*.csv")}

    for csv_file in sorted(data_dir.glob("*.csv"), key=lambda p: p.name):
        broker = detect_broker(csv_file)

        if broker in ("skip", "unknown"):
            if broker == "skip" and _is_robinhood_1099_file(csv_file):
                year = robinhood_1099_tax_year(csv_file)
                final_1099 = f"robinhood-1099-{year}.csv" if year else None
                if final_1099 and csv_file.name != final_1099:
                    if final_1099 in claimed_1099:
                        # A file with this year's canonical name already
                        # exists (corrected-1099 re-download?) — never
                        # clobber it; leave the new file for the user.
                        print(f"  ! {csv_file.name}: {final_1099} already "
                              f"exists — leaving as-is")
                    else:
                        claimed_1099.add(final_1099)
                        plan_1099.append((csv_file, final_1099))
                        continue
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

    # Consolidated-1099 renames ride the same two-pass mechanism; their
    # target names can't collide with the canonical broker prefixes.
    rename_plan.extend(plan_1099)

    if not rename_plan:
        return {}

    # Validate the whole plan before moving any input. Only destinations
    # vacated by this plan may already exist (including filename swaps).
    originals = {path for path, _ in rename_plan}
    for original, final_name in rename_plan:
        if original.is_symlink() or not original.is_file():
            raise ValueError("CSV rename sources must be regular files")
        destination = data_dir / final_name
        if destination not in originals and (destination.exists() or destination.is_symlink()):
            raise FileExistsError(f"CSV rename destination already exists: {final_name}")

    if dry_run:
        renames = {}
        for orig_path, final_name in rename_plan:
            renames[orig_path.name] = final_name
            print(f"  {orig_path.name} -> {final_name}")
        return renames

    # A private sibling directory avoids clobbering remnants of an earlier
    # interrupted run. Never recursively clean it: if recovery itself fails,
    # its files are still the user's source data.
    staging = Path(tempfile.mkdtemp(prefix=".fin-rename-", dir=data_dir))
    staged: list[tuple[Path, Path, Path]] = []
    completed: list[tuple[Path, Path, Path]] = []
    recovery_incomplete = False

    def move(source, destination):
        # Path.rename replaces existing files on POSIX. Reject destinations
        # observed after planning; this pipeline still requires one writer.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("CSV rename destination became occupied")
        source.rename(destination)

    try:
        for original, final_name in rename_plan:
            temporary = staging / original.name
            entry = (original, temporary, data_dir / final_name)
            move(original, temporary)
            staged.append(entry)
        for original, temporary, destination in staged:
            move(temporary, destination)
            completed.append((original, temporary, destination))
    except OSError as error:
        recovery_errors = []
        # Re-stage every completed rename before restoring original names;
        # a final name can itself be another input's original name.
        for original, temporary, destination in reversed(completed):
            try:
                move(destination, temporary)
            except OSError as recovery_error:
                recovery_errors.append(recovery_error)
        for original, temporary, destination in reversed(staged):
            if temporary.exists():
                try:
                    move(temporary, original)
                except OSError as recovery_error:
                    recovery_errors.append(recovery_error)
        if recovery_errors:
            recovery_incomplete = True
            raise OSError("CSV rename recovery was incomplete; source files "
                          f"were preserved in the data directory and {staging.name}. "
                          "Restore original filenames before running again.") from error
        raise
    finally:
        if not recovery_incomplete and not any(staging.iterdir()):
            staging.rmdir()

    renames = {original.name: final_name for original, final_name in rename_plan}
    for original, final_name in renames.items():
        print(f"  {original} -> {final_name}")

    return renames
