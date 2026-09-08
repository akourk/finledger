"""Build the public, fictional dashboard without accessing portfolio data.

Usage: python -m tools.build_demo [--output _site]

The parent creates a fresh temporary directory. A clean Python subprocess
imports the generator and pipeline only after binding every FIN_* directory
there. All prices come from samples/prices.fixture.json; network access is
forbidden and attempted fallbacks fail the build even if caught downstream.
The shipped snapshot must exactly match the generator. Only the bannered HTML
and its verifiable provenance manifest are copied into the public site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "tools/build_sample_snapshot.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_prices(cache: Path) -> None:
    fixture = json.loads((ROOT / "samples/prices.fixture.json").read_text(encoding="utf-8"))
    start, end = date.fromisoformat(fixture["start"]), date.fromisoformat(fixture["as_of"])
    span = (end - start).days
    prices, sectors, symbols = {}, {}, {}
    for index, (symbol, (first, last, sector)) in enumerate(sorted(fixture["symbols"].items())):
        series = {}
        for day in range(span + 1):
            fraction = day / span
            # Smooth growth plus an explicitly invented cycle. Endpoints are
            # exact, deterministic, and the same on every platform/timezone.
            wave = 1 + 0.09 * math.sin(fraction * 12 * math.pi) * math.sin(fraction * math.pi)
            series[(start + timedelta(days=day)).isoformat()] = round(
                first * (last / first) ** fraction * wave, 6)
        prices[symbol] = series
        sectors[symbol] = sector
        symbols[symbol] = {"covered_start": start.isoformat(), "covered_end": end.isoformat(),
                           "settled_through": end.isoformat(), "last_fetch": end.isoformat() + "T00:00:00",
                           "failure_count": 0}
    cache.mkdir(parents=True, exist_ok=True)
    docs = {
        "price_cache.json": prices,
        "price_cache_meta.json": {"version": 1, "auto_adjusted": False, "migrated_tr_close_v2": True,
                                   "last_deep_refresh": end.isoformat(), "symbols": symbols},
        "sector_cache.json": sectors,
        "splits_cache.json": {s: fixture.get("splits", {}).get(s, []) for s in prices},
        "dividends_cache.json": {s: [] for s in prices},
        "symbol_proxy_map.json": {},
        "ticker_renames.json": {},
    }
    for name, doc in docs.items():
        (cache / name).write_text(json.dumps(doc, sort_keys=True), encoding="utf-8", newline="\n")


def _worker(work: Path) -> None:
    # The worker may only be entered in a fresh process. Never import src
    # above this point: its configured paths are captured at import time.
    if "src.config" in sys.modules:
        raise RuntimeError("demo worker requires a fresh Python process")
    if not work.is_dir() or any(work.iterdir()):
        raise RuntimeError("demo worker requires a new empty temporary directory")
    from tools.build_sample_snapshot import AS_OF_DATE, build_snapshot
    os.environ.update({"FIN_PROJECT_ROOT": str(work), "FIN_DATA_DIR": str(work / "data"),
                       "FIN_CACHE_DIR": str(work / "cache"), "FIN_EXPORT_DIR": str(work / "exports"),
                       "FIN_AS_OF_DATE": AS_OF_DATE.isoformat(), "FIN_ASSERT_INVARIANTS": "1"})
    os.chdir(work)
    snapshot = work / "sample.snapshot.json"
    build_snapshot(snapshot)
    if snapshot.read_bytes() != (ROOT / "samples/portfolio.snapshot.json").read_bytes():
        raise RuntimeError("sample snapshot is stale: run python -m tools.build_sample_snapshot")
    seed_prices(work / "cache")
    attempts = []

    def deny_network(event, args):
        if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
            attempts.append(event)
            raise RuntimeError("network access is forbidden in the demo build")

    sys.addaudithook(deny_network)
    # yfinance can use native curl sockets outside Python's audit events.
    # A deny-only module prevents importing its network implementation.
    import types
    yf = types.ModuleType("yfinance")

    def deny_yfinance(name):
        attempts.append("yfinance." + name)
        raise RuntimeError("sample price fixture is incomplete; network fallback is forbidden")

    yf.__getattr__ = deny_yfinance
    sys.modules["yfinance"] = yf
    from src.snapshot import import_snapshot
    import_snapshot(snapshot, work / "data")
    from src.main import main
    sys.argv = ["finledger", "--skip-rename"]
    main()
    if attempts:
        raise RuntimeError("offline demo attempted a price/network fallback: " + ", ".join(sorted(set(attempts))))
    exported = work / "exports/transactions.json"
    data = json.loads(exported.read_text(encoding="utf-8"))
    demo = {"synthetic": True, "as_of": AS_OF_DATE.isoformat(), "source": SOURCE,
            "source_sha256": digest(ROOT / SOURCE), "snapshot_sha256": digest(snapshot),
            "prices": "illustrative synthetic curves", "prices_sha256": digest(ROOT / "samples/prices.fixture.json")}
    data["demo"] = demo
    data["snapshot_date"] = AS_OF_DATE.isoformat()
    # Do not suppress or rewrite diagnostics. Failed coverage, reconciliation
    # and mathematical integrity must be corrected in the synthetic inputs.
    issues = data.get("analytics", {}).get("data_health", [])
    if any(i.get("severity") in {"high", "warn"} for i in issues):
        raise RuntimeError("demo contains unresolved data health warnings: " + repr(issues))
    reconciliation = data.get("analytics", {}).get("reconciliation", {}).get("summary", {})
    if reconciliation.get("off") or reconciliation.get("warn"):
        raise RuntimeError("demo contains unexplained reconciliation differences")
    exported.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False), encoding="utf-8", newline="\n")
    from src.dashboard import generate_dashboard
    generate_dashboard(exported, work / "dashboard.html")
    from tools.demo_banner import inject
    stage = work / "site"
    stage.mkdir()
    html = inject((work / "dashboard.html").read_text(encoding="utf-8"))
    (stage / "index.html").write_text(html, encoding="utf-8", newline="\n")
    manifest = {"schema": 1, **demo, "files": {"index.html": digest(stage / "index.html")},
                "builder_sha256": digest(ROOT / "tools/build_demo.py")}
    (stage / "provenance.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        _worker(args.worker.resolve())
        return
    destination = args.output.resolve()
    if destination.exists() and any(destination.iterdir()):
        existing = {p.name for p in destination.iterdir()}
        if not existing <= {"index.html", "provenance.json"}:
            raise SystemExit("refusing to overwrite a directory containing unrelated files")
    with tempfile.TemporaryDirectory(prefix="finledger-public-demo-") as td:
        work = Path(td)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONHASHSEED"] = "0"
        env["PYTHONUTF8"] = "1"
        env["TZ"] = "UTC"
        subprocess.run([sys.executable, "-m", "tools.build_demo", "--worker", str(work)],
                       cwd=work, env=env, check=True)
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "provenance.json"):
            shutil.copyfile(work / "site" / name, destination / name)
    print(f"Public fictional demo: {destination / 'index.html'}")


if __name__ == "__main__":
    main()
