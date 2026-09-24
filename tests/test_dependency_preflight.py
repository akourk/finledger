"""Dependency failures must stop early and recover without provider backoff.

Every input and cached mark below is independently fictional. CLI subprocesses
have isolated FIN paths and an unavailable provider; successful runs use stubs.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from .conftest import write_robinhood_csv


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ERROR = "yfinance not installed — pip install yfinance"
START = "2024-01-02"
AS_OF = "2024-06-20"
MISSING_PROVIDER_WORKER = """
import runpy
import sys
sys.modules['yfinance'] = None
sys.argv[0] = 'src.main'
runpy.run_module('src.main', run_name='__main__')
"""


def _files(root):
    return {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _write_csv(root):
    write_robinhood_csv(root / "data" / "download.csv", [{
        "Activity Date": "01/02/2024", "Instrument": "FAKEA",
        "Description": "Fictional purchase", "Trans Code": "Buy",
        "Quantity": "2", "Price": "$20", "Amount": "($40)",
    }])
    (root / "data" / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Account Group,,,Robinhood,Robinhood\n"
        "Account Type,,,Robinhood,Taxable\n", encoding="utf-8",
    )


def _missing_provider_cli(root, *arguments):
    environment = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1",
                       FIN_PROJECT_ROOT=str(root), FIN_AS_OF_DATE=AS_OF)
    for key, folder in (("FIN_DATA_DIR", "data"), ("FIN_CACHE_DIR", "cache"),
                        ("FIN_EXPORT_DIR", "exports")):
        environment[key] = str(root / folder)
    return subprocess.run(
        [sys.executable, "-c", MISSING_PROVIDER_WORKER, *arguments],
        cwd=root, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )


@pytest.mark.parametrize("arguments", [[], ["--refresh-prices"], ["--refresh-caches"]])
def test_missing_dependency_preserves_inputs_caches_and_outputs(isolated_workdir, arguments):
    root = isolated_workdir
    _write_csv(root)
    (root / "cache" / "price_cache_meta.json").write_text(json.dumps({
        "symbols": {"FAKEA": {"failure_count": 2, "last_error": LEGACY_ERROR,
                               "retry_after": "2024-07-01"}},
    }), encoding="utf-8")
    (root / "exports" / "transactions.json").write_text(
        '{"fictional_previous_export": true}', encoding="utf-8")
    (root / "exports" / "dashboard.html").write_text(
        "Fictional previous dashboard", encoding="utf-8")
    before = _files(root)

    result = _missing_provider_cli(root, *arguments)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "yfinance" in output
    assert "uv sync --locked --all-groups" in output
    assert "uv run python -m src.main" in output
    assert "Traceback" not in output
    assert _files(root) == before


@pytest.mark.parametrize("arguments, expected", [
    (["--dry-run", "--refresh-prices"], "Dry run"),
    (["--help"], "usage:"),
])
def test_help_and_dry_run_do_not_require_dependency_or_change_files(
    isolated_workdir, arguments, expected,
):
    root = isolated_workdir
    _write_csv(root)
    before = _files(root)

    result = _missing_provider_cli(root, *arguments)

    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert _files(root) == before


def test_snapshot_transport_operates_without_market_dependency(isolated_workdir):
    root = isolated_workdir
    _write_csv(root)
    # Snapshot transport stores CSV text and normalizes platform newlines.
    original = {path.name: path.read_text(encoding="utf-8")
                for path in (root / "data").iterdir()}

    exported = _missing_provider_cli(root, "--export-snapshot", "fictional.snapshot.json")
    assert exported.returncode == 0, exported.stderr
    assert json.loads((root / "fictional.snapshot.json").read_text(
        encoding="utf-8"))["file_count"] == len(original)
    for path in (root / "data").iterdir():
        path.unlink()
    imported = _missing_provider_cli(root, "--import-snapshot", "fictional.snapshot.json")

    assert imported.returncode == 0, imported.stderr
    assert {path.name: path.read_text(encoding="utf-8")
            for path in (root / "data").iterdir()} == original
    assert not _files(root / "cache")
    assert not _files(root / "exports")


def test_account_mapping_setup_operates_without_market_dependency(isolated_workdir):
    root = isolated_workdir
    _write_csv(root)

    result = _missing_provider_cli(root, "--init-account-mappings")

    assert result.returncode == 0, result.stderr
    assert (root / "data" / "account-mappings.csv").exists()
    assert not _files(root / "cache")
    assert not _files(root / "exports")


def _seed_failure(prices, symbol="FAKEA", *, failures=2, error=LEGACY_ERROR):
    prices._load_prices()[symbol] = {START: 20.0}
    prices._mark_prices_dirty(symbol)
    entry = {
        "covered_start": START, "covered_end": START, "settled_through": START,
        "failure_count": failures, "last_error": error,
        "last_fetch": AS_OF + "T00:00:00", "retry_after": "2024-07-20",
        "split_failure_count": 3, "split_retry_after": "2024-07-15",
    }
    if failures >= 5:
        entry["tombstone"] = True
    prices._load_meta()["symbols"][symbol] = entry
    prices._meta_dirty = True
    prices._load_splits()[symbol] = [["2023-01-03", 2.0]]
    prices._splits_dirty = True
    return entry


def test_recovery_requires_dependency_before_mutating_state(isolated_workdir, monkeypatch):
    from src import prices

    _seed_failure(prices)
    prices.save_caches()
    before_meta = deepcopy(prices._load_meta())
    before_files = _files(isolated_workdir)
    monkeypatch.setattr(prices, "_lazy_yf", lambda: None)

    with pytest.raises(prices.MarketDataDependencyError):
        prices.reset_dependency_failures()

    assert prices._load_meta() == before_meta
    prices.save_caches()
    assert _files(isolated_workdir) == before_files


@pytest.mark.parametrize("failures", [2, 5])
def test_recovery_clears_only_exact_dependency_failure(isolated_workdir, monkeypatch, failures):
    from src import prices

    entry = _seed_failure(prices, failures=failures)
    preserved = {key: value for key, value in entry.items()
                 if key not in {"failure_count", "last_error", "last_fetch",
                                "retry_after", "tombstone"}}
    provider_entry = deepcopy(_seed_failure(prices, "FAKEB", error="provider unavailable"))
    similar_entry = deepcopy(_seed_failure(prices, "FAKEC", error="yfinance not installed elsewhere"))
    before_prices = deepcopy(prices._load_prices())
    before_splits = deepcopy(prices._load_splits())
    prices.save_caches()
    prices.reset_caches()
    monkeypatch.setattr(prices, "_lazy_yf", lambda: object())

    assert prices.reset_dependency_failures() == 1
    prices.save_caches()
    prices.reset_caches()

    symbols = prices._load_meta()["symbols"]
    assert symbols["FAKEA"] == dict(preserved, failure_count=0)
    assert symbols["FAKEB"] == provider_entry
    assert symbols["FAKEC"] == similar_entry
    assert prices._load_prices() == before_prices
    assert prices._load_splits() == before_splits
    assert prices.reset_dependency_failures() == 0


def test_recovered_symbol_fetches_immediately_without_force(isolated_workdir, stub_prices, monkeypatch):
    from src import prices

    monkeypatch.setenv("FIN_AS_OF_DATE", AS_OF)
    monkeypatch.setattr(prices, "_lazy_yf", lambda: object())
    _seed_failure(prices, failures=5)
    history = Mock(return_value={AS_OF: 30.0})
    monkeypatch.setattr(prices, "_fetch_range", history)
    monkeypatch.setattr(prices, "_fetch_splits", lambda symbol: [["2023-01-03", 2.0]])
    prices.ensure_coverage(["FAKEA"], START, AS_OF, verbose=False)
    history.assert_not_called()

    assert prices.reset_dependency_failures() == 1
    prices.ensure_coverage(["FAKEA"], START, AS_OF, verbose=False)

    history.assert_called_once()
    assert prices.get_price("FAKEA", START) == 20.0
    assert prices.get_price("FAKEA", AS_OF) == 30.0
    assert prices._load_meta()["symbols"]["FAKEA"]["failure_count"] == 0


@pytest.mark.parametrize("refresh", [False, True])
def test_normal_pipeline_automatically_recovers_dependency_failures(
    isolated_workdir, stub_prices, monkeypatch, capsys, refresh,
):
    import pandas as pd

    from src import main, prices

    _write_csv(isolated_workdir)
    monkeypatch.setenv("FIN_AS_OF_DATE", AS_OF)
    symbols = ("FAKEA", "SPY", "BND", "VXUS")
    frame = pd.concat({symbol: pd.DataFrame(
        {"Close": [30.0]}, index=pd.to_datetime([AS_OF]),
    ) for symbol in symbols}, axis=1)
    provider = SimpleNamespace(download=Mock(return_value=frame))
    monkeypatch.setattr(prices, "_lazy_yf", lambda: provider)
    for symbol in symbols:
        stub_prices.set(symbol, {START: 20.0, AS_OF: 30.0})
    monkeypatch.setattr(sys, "argv", ["src.main", "--skip-rename"])
    if refresh:
        main.main()
        capsys.readouterr()
        monkeypatch.setattr(sys, "argv", ["src.main", "--refresh-prices"])
    _seed_failure(prices, failures=5)
    # Keep the retained split evidence consistent with the stubbed provider.
    prices._load_splits()["FAKEA"] = []
    prices.save_caches()

    main.main()

    output = capsys.readouterr().out
    assert "1" in output and "dependenc" in output.lower()
    prices.reset_caches()
    entry = prices._load_meta()["symbols"]["FAKEA"]
    assert entry["failure_count"] == 0
    assert "last_error" not in entry and "tombstone" not in entry
    assert prices.get_price("FAKEA", AS_OF) == 30.0
    assert (isolated_workdir / "exports" / "dashboard.html").exists()
    if refresh:
        provider.download.assert_called_once()
