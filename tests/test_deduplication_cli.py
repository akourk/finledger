"""Fee conflicts fail the real CLI before publishing fictional outputs."""

import csv
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2024-01-31"
HEADERS = ["Date", "Action", "Symbol", "Description", "Quantity", "Price",
           "Fees & Comm", "Amount"]

# Provider denial also prevents native curl traffic from yfinance; socket
# auditing catches other Python network attempts, even if code swallows the
# denial. These guards run before importing the actual CLI entry point.
CLI_WORKER = """
import runpy
import sys
import types

attempts = []
def deny_network(event, args):
    if event.startswith('socket.') and event not in {'socket.__new__', 'socket.gethostname'}:
        attempts.append(event)
        raise RuntimeError('deduplication regression forbids network access')

def deny_provider(name):
    attempts.append('yfinance.' + name)
    raise RuntimeError('deduplication regression forbids provider access')

sys.addaudithook(deny_network)
provider = types.ModuleType('yfinance')
provider.__getattr__ = deny_provider
sys.modules['yfinance'] = provider
sys.argv[0] = 'src.main'
try:
    runpy.run_module('src.main', run_name='__main__')
finally:
    if attempts:
        raise RuntimeError('deduplication regression attempted a network fallback')
"""


def _environment(root):
    for folder in ("data", "cache", "exports"):
        (root / folder).mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "FIN_PROJECT_ROOT": str(root), "FIN_DATA_DIR": str(root / "data"),
        "FIN_CACHE_DIR": str(root / "cache"), "FIN_EXPORT_DIR": str(root / "exports"),
        "FIN_AS_OF_DATE": AS_OF, "FIN_ASSERT_INVARIANTS": "1",
        "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", "TZ": "UTC",
    })
    return environment


def _seed_fictional_prices(root):
    days = [(date(2024, 1, 1) + timedelta(days=offset)).isoformat()
            for offset in range(31)]
    marks = {"FICT": 17.53, "SPY": 100, "BND": 50, "VXUS": 25}
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
        "symbol_proxy_map.json": {}, "ticker_renames.json": {},
    }
    for name, document in documents.items():
        (root / "cache" / name).write_text(json.dumps(document), encoding="utf-8")


def _write_export(root, name, fees):
    with (root / "data" / name).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(HEADERS)
        for fee in fees:
            writer.writerow(["01/02/2024", "Buy", "FICT", "Fictional purchase",
                             "5", "$17.53", fee, "($87.65)"])


def _run_cli(root):
    result = subprocess.run(
        [sys.executable, "-c", CLI_WORKER, "--skip-rename"], cwd=root,
        env=_environment(root), capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert "network fallback" not in result.stderr
    return result


@pytest.mark.parametrize(("first_fees", "second_fees"), [
    (("0.003127",), ("0.003128",)),
    (("0", "2"), ("1", "1")),
    (("1", "1", "1"), ("1", "2")),
])
@pytest.mark.parametrize("reverse_sources", [False, True])
def test_conflicting_broker_exports_preserve_dashboard_and_baseline(
        tmp_path, first_fees, second_fees, reverse_sources):
    _environment(tmp_path)
    _seed_fictional_prices(tmp_path)
    (tmp_path / "data" / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Account Group,,,Schwab Roth IRA,Roth IRA\n"
        "Account Type,,,Roth IRA,Retirement\n", encoding="utf-8", newline="",
    )
    first = "schwab-roth-ira-1.csv"
    second = "schwab-roth-ira-2.csv"
    if reverse_sources:
        first, second = second, first
    _write_export(tmp_path, first, first_fees)
    baseline = _run_cli(tmp_path)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    output = json.loads((tmp_path / "exports" / "transactions.json").read_text())
    assert output["count"] == len(first_fees)
    assert [row["fees"] for row in output["transactions"]] == list(map(float, first_fees))

    preserved = [tmp_path / "exports" / "transactions.json",
                 tmp_path / "exports" / "dashboard.html",
                 tmp_path / "cache" / "last_run.json"]
    before = {path: path.read_bytes() for path in preserved}
    cache_before = {path.relative_to(tmp_path / "cache"): path.read_bytes()
                    for path in (tmp_path / "cache").rglob("*") if path.is_file()}
    _write_export(tmp_path, second, second_fees)
    result = _run_cli(tmp_path)
    assert result.returncode != 0
    assert "Import/export failed: Conflicting fee evidence" in result.stderr
    for private_field in ("FICT", "01/02/2024", "2024-01-02", "17.53", "87.65",
                          "Fictional purchase", first, second,
                          "0.003127", "0.003128"):
        assert private_field not in result.stderr
    assert {path: path.read_bytes() for path in preserved} == before
    assert {path.relative_to(tmp_path / "cache"): path.read_bytes()
            for path in (tmp_path / "cache").rglob("*") if path.is_file()} == cache_before
