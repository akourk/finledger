"""Golden-output + perturbation harness (docs/PLAN-audit.md Segment 1, items 4).

Runs the full pipeline against `samples/portfolio.snapshot.json` in a
throwaway workdir and diffs the exported JSON.  Two jobs:

**Golden diff** -- prove a change moved only what it should:

    python -m tools.golden --record          # before your change
    python -m tools.golden --check           # after; prints changed paths

**Perturbation** (technique 3) -- inject ONE synthetic transaction and
ask of every figure that moved: *should* it have?

    python -m tools.golden --inject '{"date":"2024-06-20","account":"Robinhood",
        "symbol":"VTI","action":"Buy","quantity":1,"price":250,"amount":250}'

The perturbation mode runs the pipeline TWICE in one invocation --
unperturbed then perturbed -- and diffs those two against each other.
That is deliberate: the export embeds today's date in a dozen places
(snapshot cadence, projections, YTD tax), so a golden recorded on a
different day produces drift that has nothing to do with the change
under test.  An in-invocation A/B cancels it exactly.

### Isolation

Every run gets its own workdir with its own `data/`, `cache/`,
`exports/`.  The repo's checked-in `cache/` is COPIED in so pricing
works offline; the real one is never written to (`docs/PLAN-audit.md`: keep
cache churn out of audit commits).  Network fetches are stubbed dead in
the child process, so a run is offline and deterministic.

`cache/last_run.json` is deliberately NOT copied: `analytics.changes`
diffs against it, so a shared one would make run A's output an input to
run B.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "portfolio.snapshot.json"
GOLDEN = ROOT / "audit" / "golden" / "export.json"

# Fields that legitimately differ between two runs of identical data.
# Kept deliberately SHORT -- every entry here is a figure the harness
# can no longer see move, so anything speculative belongs out.
VOLATILE_KEYS = {"generated", "generated_at", "exported_at", "last_fetch",
                 "last_deep_refresh"}


# ---------------------------------------------------------------------------
# Child-process pipeline run (offline)
# ---------------------------------------------------------------------------

def _exec_pipeline() -> int:
    """Run main() with every network path stubbed. Child process only."""
    sys.path.insert(0, str(ROOT))
    from src import prices as P
    from src import sectors as S

    P._fetch_range = lambda symbol, start, end: {}
    P._fetch_dividends = lambda symbol: []
    P._batch_fetch_ranges = lambda symbols, start, end: {}

    # Splits must echo what's already CACHED, not [].
    #
    # Returning [] makes `_invalidate_prices_for_split_change` see the
    # symbol's real split history disappear, which is a legitimate
    # invalidation signal — it drops that symbol's cached prices and
    # coverage.  Offline, nothing can refetch them, so every symbol with
    # split history silently falls back to its last transaction price and
    # freezes there.
    #
    # Found the hard way: an audit run reported three Schwab balance rows
    # breaking by thousands, which was entirely this stub.  A/B diffs
    # survived it (both sides degrade identically) but any ABSOLUTE
    # figure taken from an offline run was wrong.
    P._fetch_splits = lambda symbol: list(P._load_splits().get(symbol, []))

    def _offline_sector(symbol: str) -> str:
        quick = S._classify_no_fetch(symbol)
        if quick is not None:
            return quick
        return S._load_cache().get(symbol, "Other")

    S.get_sector = _offline_sector
    S._fetch_from_yfinance = lambda symbol: "Other"

    from src import main as M

    sys.argv = ["fin", "--skip-rename"]
    M.main()
    return 0


# ---------------------------------------------------------------------------
# Workdir preparation
# ---------------------------------------------------------------------------

def _prepare(workdir: Path, inject: dict | None) -> None:
    for sub in ("data", "cache", "exports"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    from src.snapshot import import_snapshot

    import_snapshot(SAMPLE, workdir / "data", overwrite=True)

    # Copy the checked-in cache so pricing works offline. Skip last_run.json
    # (see module docstring) and skip nothing else -- the shards are what
    # make an offline run produce real numbers.
    src_cache = ROOT / "cache"
    for item in src_cache.iterdir():
        if item.name == "last_run.json":
            continue
        dest = workdir / "cache" / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    if inject:
        _inject_txn(workdir / "data" / "manual-adjustments.csv", inject)


def _inject_txn(path: Path, txn: dict) -> None:
    """Append one row to manual-adjustments.csv.

    Injecting through a real CSV (rather than into the parsed list) means
    the perturbation traverses the ENTIRE pipeline -- parse, dedupe,
    normalize, basis, history, analytics -- which is the point.
    """
    headers = ["Account", "Date", "Type", "Symbol", "Quantity", "Price",
               "Amount", "Description"]
    row = {
        "Account": txn.get("account", ""),
        "Date": txn.get("date", ""),
        "Type": txn.get("action", ""),
        "Symbol": txn.get("symbol", ""),
        "Quantity": txn.get("quantity", ""),
        "Price": txn.get("price", ""),
        "Amount": txn.get("amount", ""),
        "Description": txn.get("description", "audit perturbation"),
    }
    exists = path.exists()
    existing = path.read_text(encoding="utf-8") if exists else ""
    needs_header = not exists or not existing.strip()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        if needs_header:
            w.writeheader()
        w.writerow(row)


def run_pipeline(inject: dict | None = None, keep: bool = False) -> dict:
    """Run the pipeline in an isolated workdir; return the exported JSON."""
    workdir = Path(tempfile.mkdtemp(prefix="fin-golden-"))
    try:
        _prepare(workdir, inject)
        env = dict(os.environ)
        env.update({
            "FIN_PROJECT_ROOT": str(workdir),
            "FIN_DATA_DIR": str(workdir / "data"),
            "FIN_CACHE_DIR": str(workdir / "cache"),
            "FIN_EXPORT_DIR": str(workdir / "exports"),
            "PYTHONPATH": str(ROOT),
        })
        proc = subprocess.run(
            [sys.executable, "-m", "tools.golden", "--_exec"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
        )
        export = workdir / "exports" / "transactions.json"
        if not export.exists():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
            raise RuntimeError(f"pipeline produced no export:\n{tail}")
        return json.loads(export.read_text(encoding="utf-8"))
    finally:
        if keep:
            print(f"  (workdir kept: {workdir})")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

def _walk(obj, prefix=""):
    """Yield (dotted_path, scalar) for every leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in VOLATILE_KEYS:
                continue
            yield from _walk(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def diff(a: dict, b: dict, tolerance: float = 0.0) -> list[tuple[str, object, object]]:
    da, db = dict(_walk(a)), dict(_walk(b))
    out = []
    for path in sorted(set(da) | set(db)):
        va, vb = da.get(path, "<absent>"), db.get(path, "<absent>")
        if va == vb:
            continue
        if (tolerance and isinstance(va, (int, float)) and isinstance(vb, (int, float))
                and not isinstance(va, bool) and not isinstance(vb, bool)):
            if abs(va - vb) <= tolerance:
                continue
        out.append((path, va, vb))
    return out


def _report(changes, label_a: str, label_b: str, limit: int,
            path_filter: str | None = None) -> None:
    if path_filter:
        changes = [c for c in changes if path_filter in c[0]]
        print(f"(filtered to paths containing {path_filter!r})")
    if not changes:
        print(f"IDENTICAL - no differences between {label_a} and {label_b}.")
        return
    # Group by top-level section so the output is a conclusion, not a dump.
    sections: dict[str, int] = {}
    for path, _va, _vb in changes:
        sections[path.split(".")[0].split("[")[0]] = sections.get(
            path.split(".")[0].split("[")[0], 0) + 1
    print(f"{len(changes)} changed leaf value(s) — {label_a} -> {label_b}\n")
    print("By top-level section:")
    for sec, n in sorted(sections.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6}  {sec}")
    print(f"\nFirst {min(limit, len(changes))}:")
    for path, va, vb in changes[:limit]:
        print(f"  {path}\n      {va!r}  ->  {vb!r}")
    if len(changes) > limit:
        print(f"  ... {len(changes) - limit} more (raise --limit to see)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--_exec", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--record", action="store_true", help="write the golden file")
    ap.add_argument("--check", action="store_true", help="diff a fresh run vs the golden")
    ap.add_argument("--inject", help="JSON transaction to inject, then A/B diff")
    ap.add_argument("--vs-golden", action="store_true",
                    help="with --inject, diff against the stored golden instead "
                         "of a fresh baseline (date-drift prone; see docstring)")
    ap.add_argument("--tolerance", type=float, default=0.0)
    ap.add_argument("--filter", help="only report paths containing this substring "
                                     "(e.g. --filter reconciliation)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--keep", action="store_true", help="keep workdirs for inspection")
    args = ap.parse_args()

    if args._exec:
        return _exec_pipeline()

    if args.record:
        data = run_pipeline(keep=args.keep)
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        n = len(data.get("transactions", []))
        print(f"recorded {GOLDEN.relative_to(ROOT).as_posix()} "
              f"({n} txns, {len(dict(_walk(data))):,} leaf values)")
        return 0

    if args.check:
        if not GOLDEN.exists():
            print("no golden recorded — run --record first")
            return 2
        old = json.loads(GOLDEN.read_text(encoding="utf-8"))
        new = run_pipeline(keep=args.keep)
        changes = diff(old, new, args.tolerance)
        _report(changes, "golden", "current", args.limit, args.filter)
        return 1 if changes else 0

    if args.inject:
        txn = json.loads(args.inject)
        if args.vs_golden:
            if not GOLDEN.exists():
                print("no golden recorded — run --record first")
                return 2
            base = json.loads(GOLDEN.read_text(encoding="utf-8"))
        else:
            print("running unperturbed baseline...")
            base = run_pipeline(keep=args.keep)
        print(f"running perturbed (injected: {txn})...")
        pert = run_pipeline(inject=txn, keep=args.keep)
        changes = diff(base, pert, args.tolerance)
        _report(changes, "baseline", "perturbed", args.limit, args.filter)
        print("\nAsk of every figure above: SHOULD the injection have moved it?")
        print("A figure keyed to a date BEFORE the injection must not move.")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
