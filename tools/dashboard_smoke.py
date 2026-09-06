"""Build a dashboard from a deliberately degenerate portfolio.

Segment 6 of `docs/PLAN-audit.md` needed behavioural coverage of the
dashboard's 8.7k lines of JavaScript, which has no test harness. It
turns out none is required: generate the real dashboard from a
degenerate input, serve it, and drive it in a browser. That found the
empty-state paths to be clean — but only because the inputs were nasty
enough to reach them.

Two scenarios, both chosen because they null out analytics blocks that
the normal sample never does:

``minimal``
    The sample portfolio with every OPTIONAL metadata row stripped —
    the state a brand-new user is actually in. Nulls `rebalancing`,
    `reconciliation`, `budget` and `paycheck`.

``tiny``
    A two-transaction ledger: one deposit, one buy. No sells (so
    realized is 0 and pct_return has a zero denominator), no options,
    no crypto, no dividends. Additionally nulls `monte_carlo`.

Usage:

    python -m tools.dashboard_smoke minimal --out .
    python -m tools.dashboard_smoke tiny --out .

Then serve the directory and open the file, e.g.::

    python -m http.server 8765 --bind 127.0.0.1

and drive it. The check that matters, run after activating EVERY tab
(renderers are lazy, so a null only bites on first activation):

    - zero console errors
    - no /\\bNaN\\b/, /\\bundefined\\b/, /\\bInfinity\\b/ in any panel's
      innerText
    - sections backed by a null analytics block are ABSENT, not empty
    - `hideEmptyTabs` sets Options / Crypto to display:none

**Validate the scanner before believing a clean result.** Inject
``$NaN undefined Infinity`` into the active panel and confirm the scan
flips from clean to detecting all three, then back on removal. This
audit has repeatedly found checks that could not fire; a browser scan is
no more trustworthy by default than a test.

Runs fully offline — network fetches are stubbed via `tools.golden`'s
child-process entry point, and the repo cache is copied rather than
written to.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "portfolio.snapshot.json"

# Every metadata Type that is OPTIONAL — each one absent nulls or empties
# something downstream, which is exactly what we want to exercise.
OPTIONAL_TYPES = {
    "Budget", "Paycheck Deduction", "Target Allocation", "Annual Expenses",
    "Reconcile Balance", "Reconcile Realized", "Reconcile Income",
    "Reconcile Section 1256", "Reconcile Other Income", "Target",
    "Tax Return", "Bonus History", "Pay Frequency", "State", "State Tax Rate",
    "Retirement Age", "Savings APR", "Lot Method", "Cost Basis",
    "Balance Anchor",
}

NL = "\n"

TINY_BROKER_CSV = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount" + NL
    + "6/02/2026,,,,ACH Deposit,ACH,,,$1000.00" + NL
    + "6/03/2026,,,VOO,Vanguard S&P 500 ETF,Buy,1,$500.00,($500.00)" + NL
)

TINY_METADATA = (
    "Type,Date,Amount,Symbol,Note" + NL
    + "Personal Info,,,,Birthday=1990-06-15" + NL
    + "Account Group,,,Robinhood,Robinhood" + NL
    + "Account Type,,,Robinhood,Taxable" + NL
)


def _seed_cache(work: Path) -> None:
    """Copy the checked-in cache so pricing works offline.

    `last_run.json` is skipped: `analytics.changes` diffs against it, so
    sharing one would make a previous run an input to this one.
    """
    for item in (ROOT / "cache").iterdir():
        if item.name == "last_run.json":
            continue
        dest = work / "cache" / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


def _build_minimal(data: Path) -> str:
    sys.path.insert(0, str(ROOT))
    from src.snapshot import import_snapshot

    with contextlib.redirect_stdout(io.StringIO()):
        import_snapshot(SAMPLE, data, overwrite=True)

    meta = data / "metadata.csv"
    lines = meta.read_text(encoding="utf-8").splitlines()
    kept = [lines[0]] + [l for l in lines[1:]
                         if l.split(",")[0].strip() not in OPTIONAL_TYPES]
    meta.write_text(NL.join(kept) + NL, encoding="utf-8")
    return f"metadata rows {len(lines) - 1} -> {len(kept) - 1}"


def _build_tiny(data: Path) -> str:
    (data / "robinhood-1.csv").write_text(TINY_BROKER_CSV, encoding="utf-8")
    (data / "metadata.csv").write_text(TINY_METADATA, encoding="utf-8")
    return "2 transactions, 1 account, no sells/options/crypto"


SCENARIOS = {"minimal": _build_minimal, "tiny": _build_tiny}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--out", default=".", help="directory for the .html")
    ap.add_argument("--keep", action="store_true", help="keep the workdir")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix=f"fin-smoke-{args.scenario}-"))
    for sub in ("data", "cache", "exports"):
        (work / sub).mkdir(parents=True)

    note = SCENARIOS[args.scenario](work / "data")
    _seed_cache(work)
    print(f"scenario '{args.scenario}': {note}")

    env = dict(os.environ)
    env.update({
        "FIN_PROJECT_ROOT": str(work), "FIN_DATA_DIR": str(work / "data"),
        "FIN_CACHE_DIR": str(work / "cache"),
        "FIN_EXPORT_DIR": str(work / "exports"), "PYTHONPATH": str(ROOT),
    })
    proc = subprocess.run([sys.executable, "-m", "tools.golden", "--_exec"],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=900)

    dash = work / "exports" / "dashboard.html"
    if not dash.exists():
        print("PIPELINE FAILED on a degenerate input — that is itself a "
              "finding:\n" + (proc.stdout + proc.stderr)[-2000:])
        return 1

    data = json.loads((work / "exports" / "transactions.json")
                      .read_text(encoding="utf-8"))
    analytics = data.get("analytics", {})
    nulls = sorted(k for k, v in analytics.items() if v is None)
    print(f"  txns={len(data.get('transactions', []))} "
          f"holdings={len(data.get('holdings', []))} "
          f"snapshots={len(data.get('history', []))}")
    print(f"  null analytics blocks ({len(nulls)}): {', '.join(nulls) or 'none'}")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"dashboard-{args.scenario}.html"
    shutil.copy2(dash, target)
    print(f"  wrote {target} ({target.stat().st_size:,} bytes)")

    if args.keep:
        print(f"  workdir kept: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
