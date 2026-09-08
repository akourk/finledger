"""Exercise the CLI migration workflow with independently invented data.

Snapshots preserve CSV text; that alone does not establish that a fresh
checkout can parse the restored files and produce the same portfolio.
Every command below runs in a fresh process with isolated FIN directories,
fictional prices, a fixed date, and no permitted network fallback.
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2024-01-31"
HEADERS = [
    "Activity Date", "Process Date", "Settle Date", "Instrument",
    "Description", "Trans Code", "Quantity", "Price", "Amount",
]
DESCRIPTION = 'Fictional purchase, first line\nSecond line with "quoted" text'
NOTICE = "The data provided is for informational purposes only. Fictional test notice."

# Run the actual __main__ entry point so argument parsing, exit status and
# error formatting are covered too. A deny-only yfinance module also blocks
# native curl traffic, which Python's socket audit hook cannot intercept.
CLI_WORKER = """
import runpy
import sys
import types

attempts = []
def deny_network(event, args):
    if event.startswith('socket.') and event not in {'socket.__new__', 'socket.gethostname'}:
        attempts.append(event)
        raise RuntimeError('network access is forbidden in snapshot workflow tests')

def deny_yfinance(name):
    attempts.append('yfinance.' + name)
    raise RuntimeError('fictional price fixture is incomplete')

sys.addaudithook(deny_network)
yf = types.ModuleType('yfinance')
yf.__getattr__ = deny_yfinance
sys.modules['yfinance'] = yf
sys.argv[0] = 'src.main'
try:
    runpy.run_module('src.main', run_name='__main__')
finally:
    if attempts:
        raise RuntimeError('snapshot workflow attempted a network fallback')
"""


def _new_workdir(root: Path) -> Path:
    for name in ("data", "cache", "exports"):
        (root / name).mkdir(parents=True)
    # These round, constant prices are invented here, never copied from
    # market caches. Full coverage avoids any hidden dependency on a quote
    # provider, including the benchmark and sector enrichment paths.
    first = date(2024, 1, 1)
    days = [(first + timedelta(days=n)).isoformat() for n in range(31)]
    marks = {"ALFA": 20, "BETA": 10, "SPY": 100, "BND": 50, "VXUS": 25}
    documents = {
        "price_cache.json": {symbol: dict.fromkeys(days, mark)
                             for symbol, mark in marks.items()},
        "price_cache_meta.json": {
            "version": 1, "auto_adjusted": False, "migrated_tr_close_v2": True,
            "last_deep_refresh": AS_OF,
            "symbols": {symbol: {
                "covered_start": days[0], "covered_end": AS_OF,
                "settled_through": AS_OF, "last_fetch": AS_OF + "T00:00:00",
                "failure_count": 0,
            } for symbol in marks},
        },
        "sector_cache.json": dict.fromkeys(marks, "Other"),
        "splits_cache.json": {symbol: [] for symbol in marks},
        "dividends_cache.json": {symbol: [] for symbol in marks},
        "symbol_proxy_map.json": {},
        "ticker_renames.json": {},
    }
    for name, value in documents.items():
        (root / "cache" / name).write_text(json.dumps(value), encoding="utf-8")
    return root


def _run_cli(root: Path, *args: str, success: bool = True,
             as_of: str = AS_OF) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "FIN_PROJECT_ROOT": str(root), "FIN_DATA_DIR": str(root / "data"),
        "FIN_CACHE_DIR": str(root / "cache"), "FIN_EXPORT_DIR": str(root / "exports"),
        "FIN_AS_OF_DATE": as_of, "FIN_ASSERT_INVARIANTS": "1",
        "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1", "PYTHONHASHSEED": "0",
        "TZ": "UTC",
    })
    result = subprocess.run(
        [sys.executable, "-c", CLI_WORKER, *args], cwd=root, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, "invalid restored CSV unexpectedly succeeded"
    assert "network fallback" not in result.stderr
    return result


def _write_portfolio(root: Path, *, invalid_column=None, invalid_value=None) -> None:
    rows = [
        ["01/02/2024", "", "", "ALFA", DESCRIPTION, "Buy", "3", "$20", "($60)"],
        ["01/15/2024", "", "", "ALFA", "Fictional share surrender", "SXCH", "3S", "", ""],
        ["01/15/2024", "", "", "BETA", "Fictional share receipt", "SXCH", "6", "", ""],
        [""] * 9 + [NOTICE],
    ]
    if invalid_column is not None:
        # Corporate-action rows deliberately infer their prices; put the
        # malformed Price on the Buy, where that input is authoritative.
        index = 0 if invalid_column == "Price" else 1
        rows[index][HEADERS.index(invalid_column)] = invalid_value
    with (root / "data" / "robinhood.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(HEADERS)
        writer.writerows(rows)
    (root / "data" / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Account Group,,,Robinhood,Robinhood\n"
        "Account Type,,,Robinhood,Taxable\n",
        encoding="utf-8", newline="",
    )


def _output(root: Path) -> dict:
    return json.loads((root / "exports" / "transactions.json").read_text(encoding="utf-8"))


def _transfer_snapshot(source: Path, restored: Path, *, force: bool = False,
                       as_of: str = AS_OF) -> None:
    _run_cli(source, "--export-snapshot", "snap.json", as_of=as_of)
    shutil.copyfile(source / "snap.json", restored / "snap.json")
    args = ["--import-snapshot", "snap.json"]
    if force:
        args.append("--force")
    _run_cli(restored, *args, as_of=as_of)


def test_cli_snapshot_restore_runs_the_pipeline_and_preserves_share_exchange(tmp_path):
    source = _new_workdir(tmp_path / "source")
    restored = _new_workdir(tmp_path / "restored")
    _write_portfolio(source)

    # Exercise the three-command CLI workflow, copying the snapshot to a
    # different FIN root and using a new Python process for every command.
    _transfer_snapshot(source, restored)
    assert not (restored / "exports" / "transactions.json").exists()
    for name in ("robinhood.csv", "metadata.csv"):
        assert (restored / "data" / name).read_bytes() == (source / "data" / name).read_bytes()
    _run_cli(restored)
    _run_cli(source)

    actual, direct = _output(restored), _output(source)
    for field in ("transactions", "holdings", "holdings_by_account", "history", "basis_totals"):
        assert actual[field] == direct[field], field
    broker_txns = [t for t in actual["transactions"] if t["source"] == "robinhood.csv"]
    assert len(broker_txns) == 3  # Footer is not a transaction.
    assert broker_txns[0]["description"] == DESCRIPTION
    surrender, receipt = broker_txns[1:]
    assert (surrender["symbol"], surrender["action"], surrender["quantity"], surrender["balance"]) == (
        "ALFA", "Sell", 3, 0,
    )
    assert (receipt["symbol"], receipt["action"], receipt["quantity"], receipt["balance"]) == (
        "BETA", "Buy", 6, 6,
    )
    assert surrender["amount"] == receipt["amount"] == 0
    holdings = {h["symbol"]: h for h in actual["holdings"]}
    assert "ALFA" not in holdings
    assert holdings["BETA"]["quantity"] == 6
    assert (restored / "exports" / "dashboard.html").is_file()


def test_cli_snapshot_round_trip_preserves_the_complete_fictional_sample(tmp_path):
    from tools.build_demo import seed_prices
    from tools.build_sample_snapshot import AS_OF_DATE, build_snapshot

    sample = tmp_path / "fixture.snapshot.json"
    fixture = build_snapshot(sample)
    assert sample.read_bytes() == (ROOT / "samples" / "portfolio.snapshot.json").read_bytes()
    source, restored = tmp_path / "source", tmp_path / "restored"
    for root in (source, restored):
        for name in ("data", "cache", "exports"):
            (root / name).mkdir(parents=True)
        seed_prices(root / "cache")
    for name, content in fixture["files"].items():
        (source / "data" / name).write_text(content, encoding="utf-8", newline="")

    as_of = AS_OF_DATE.isoformat()
    _transfer_snapshot(source, restored, as_of=as_of)
    exported = json.loads((source / "snap.json").read_text(encoding="utf-8"))
    assert exported["file_count"] == fixture["file_count"]
    assert exported["files"] == fixture["files"]
    assert {p.name for p in (restored / "data").glob("*.csv")} == set(fixture["files"])
    for name in fixture["files"]:
        assert (restored / "data" / name).read_bytes() == (source / "data" / name).read_bytes()
    assert not (restored / "exports" / "transactions.json").exists()

    # Default renaming is part of normal ingestion on a fresh checkout.
    _run_cli(restored, as_of=as_of)
    _run_cli(source, as_of=as_of)
    actual, direct = _output(restored), _output(source)
    for field in ("count", "transactions", "holdings", "holdings_by_account", "history", "basis_totals"):
        assert actual[field] == direct[field], field
    restored_files = {p.name for p in (restored / "data").glob("*.csv")}
    assert restored_files == {p.name for p in (source / "data").glob("*.csv")}
    assert len(restored_files) == fixture["file_count"]
    by_source = Counter(t["source"] for t in actual["transactions"])
    assert by_source == Counter(t["source"] for t in direct["transactions"])
    # Metadata is configuration and this sample's manual file is deliberately
    # empty. Every populated broker CSV must contribute transactions.
    broker_files = restored_files - {"metadata.csv", "manual-adjustments.csv"}
    assert broker_files <= by_source.keys()
    assert (restored / "exports" / "dashboard.html").is_file()


@pytest.mark.parametrize(("column", "invalid"), [
    ("Quantity", "3SS"),
    ("Price", "invented-invalid-price"),
    ("Amount", "invented-invalid-amount"),
])
def test_invalid_restored_csv_reports_location_and_preserves_previous_exports(tmp_path, column, invalid):
    source = _new_workdir(tmp_path / "source")
    restored = _new_workdir(tmp_path / "restored")
    _write_portfolio(restored)
    _run_cli(restored)
    before = {p.name: p.read_bytes() for p in (restored / "exports").iterdir() if p.is_file()}
    assert {"transactions.json", "dashboard.html"} <= before.keys()

    _write_portfolio(source, invalid_column=column, invalid_value=invalid)
    _transfer_snapshot(source, restored, force=True)
    # Successful transport must not be confused with successful ingestion.
    result = _run_cli(restored, success=False)
    error = result.stdout + result.stderr
    row = 3 if column == "Price" else 4
    assert f"robinhood.csv: row {row}, column {column}: invalid number" in error
    assert invalid not in error
    after = {p.name: p.read_bytes() for p in (restored / "exports").iterdir() if p.is_file()}
    assert after == before
