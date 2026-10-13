"""Fictional CLI coverage for dated Coinbase fee corrections."""

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
HEADERS = [
    "ID", "Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
    "Price Currency", "Price at Transaction", "Subtotal",
    "Total (inclusive of fees and/or spread)", "Fees and/or Spread", "Notes",
]

# Reuse the real CLI entry point and its error handling. A refresh retains the
# seeded fictional marks; all other provider accesses fail, while offline_python
# independently rejects Python socket and native curl transport attempts.
CLI_WORKER = """
import runpy
import sys
import types

attempts = []
def deny_provider(name):
    attempts.append(name)
    raise RuntimeError('Coinbase fee regression forbids provider access')

provider = types.ModuleType('yfinance')
provider.__getattr__ = deny_provider
sys.modules['yfinance'] = provider
if '--refresh-prices' in sys.argv:
    import src.prices
    src.prices.fetch_latest_close_batch = lambda symbols: 0
sys.argv[0] = 'src.main'
try:
    runpy.run_module('src.main', run_name='__main__')
finally:
    if attempts:
        raise RuntimeError('Coinbase fee regression attempted a provider fallback')
"""


def _environment(root):
    for folder in ("data", "cache", "exports"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "FIN_PROJECT_ROOT": str(root), "FIN_DATA_DIR": str(root / "data"),
        "FIN_CACHE_DIR": str(root / "cache"), "FIN_EXPORT_DIR": str(root / "exports"),
        "FIN_AS_OF_DATE": AS_OF, "FIN_ASSERT_INVARIANTS": "1",
        "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", "TZ": "UTC",
    })
    return environment


def _prepare(root):
    _environment(root)
    (root / "data" / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Account Group,,,Coinbase,Coinbase\n"
        "Account Type,,,Coinbase,Taxable\n", encoding="utf-8", newline="",
    )
    days = [(date(2024, 1, 1) + timedelta(days=offset)).isoformat()
            for offset in range(31)]
    marks = {"FEECOIN-USD": 30, "SPY": 100, "BND": 50, "VXUS": 25}
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
        "sector_cache.json": {symbol: "Cryptocurrency" if symbol == "FEECOIN-USD"
                              else "Other" for symbol in marks},
        "splits_cache.json": {symbol: [] for symbol in marks},
        "dividends_cache.json": {symbol: [] for symbol in marks},
        "symbol_proxy_map.json": {}, "ticker_renames.json": {},
    }
    for name, document in documents.items():
        (root / "cache" / name).write_text(json.dumps(document), encoding="utf-8")


def _rows(*, buy_fee, sell_fee, multiplicity=1, later=False):
    rows = [
        ["fictional-funding", "2024-01-01T08:00:00Z", "Deposit", "USD",
         "1000", "USD", "1", "1000", "1000", "0", "Fictional funding"],
        ["fictional-buy", "2024-01-02T09:15:27Z", "Buy", "FEECOIN",
         "10", "USD", "20", "200", "205", buy_fee,
         "Fictional purchase using USD wallet"],
    ]
    rows.extend([
        ["fictional-sell", "2024-01-10T11:12:13Z", "Sell", "FEECOIN",
         "-2", "USD", "30", "60", "57", sell_fee, "Fictional sale"]
        for _ in range(multiplicity)
    ])
    rows.append([
        "fictional-earlier-funding", "2024-01-20T09:00:00Z", "Deposit", "USD",
        "3", "USD", "1", "3", "3", "0", "Fictional earlier deposit",
    ])
    if later:
        # Both reports end on the same calendar date. Recency needs the full
        # timestamp of this unrelated row, not the conflicted trade's date.
        rows.append([
            "fictional-later-funding", "2024-01-20T15:00:00Z", "Deposit", "USD",
            "7", "USD", "1", "7", "7", "0", "Fictional later deposit",
        ])
    return rows


def _write_report(root, name, rows):
    with (root / "data" / name).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(HEADERS)
        writer.writerows(rows)


def _run_cli(root, offline_python, *, refresh=False):
    result = subprocess.run(
        offline_python([sys.executable, "-c", CLI_WORKER,
                        "--refresh-prices" if refresh else "--skip-rename"]),
        cwd=root, env=_environment(root), capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert "provider fallback" not in result.stderr
    return result


def _output(root):
    return json.loads((root / "exports" / "transactions.json").read_text(encoding="utf-8"))


def _accounting_rows(output):
    # Source selection and ingest numbering may differ from the direct report;
    # every economic field and every calculated annotation must still agree.
    return [{key: value for key, value in row.items()
             if key not in {"source", "seq", "fee_resolution"}}
            for row in output["transactions"]]


@pytest.mark.parametrize("reverse_sources", [False, True])
@pytest.mark.parametrize("multiplicity", [1, 2])
def test_newest_coinbase_report_preserves_accounting_and_refresh_provenance(
        tmp_path, offline_python, reverse_sources, multiplicity):
    overlap = tmp_path / "overlap"
    direct = tmp_path / "direct"
    for root in (overlap, direct):
        _prepare(root)
    older, newer = "coinbase-1.csv", "coinbase-2.csv"
    if reverse_sources:
        older, newer = newer, older
    _write_report(overlap, older, _rows(buy_fee="1", sell_fee="0.5",
                                      multiplicity=multiplicity))
    selected_rows = _rows(buy_fee="5", sell_fee="3", multiplicity=multiplicity,
                          later=True)
    _write_report(overlap, newer, selected_rows)
    _write_report(direct, "coinbase-direct.csv", selected_rows)

    for root in (overlap, direct):
        result = _run_cli(root, offline_python)
        assert result.returncode == 0, result.stdout + result.stderr
    selected = _output(overlap)
    expected = _output(direct)
    assert selected["count"] == expected["count"] == 4 + multiplicity
    assert _accounting_rows(selected) == _accounting_rows(expected)
    for key in ("holdings", "holdings_by_account", "history", "basis_methods",
                "basis_totals", "cash_summary"):
        assert selected[key] == expected[key], key
    assert selected["analytics"]["fees"] == expected["analytics"]["fees"]
    assert selected["analytics"]["fees"]["total"] == 5 + 3 * multiplicity

    trades = [row for row in selected["transactions"] if row["action"] in {"Buy", "Sell"}]
    assert len(trades) == 1 + multiplicity
    assert all(row["source"] == newer for row in trades)
    buy = next(row for row in trades if row["action"] == "Buy")
    sells = [row for row in trades if row["action"] == "Sell"]
    # Net totals already include fees; subtracting them again changes these.
    assert buy["amount"] == buy["cost_basis"] == 205
    assert all(row["amount"] == 57 and row["cost_basis"] == 41
               and row["realized_gain"] == 16 for row in sells)
    assert sells[-1]["balance"] == 10 - 2 * multiplicity
    for row in trades:
        resolution = row["fee_resolution"]
        assert resolution["criterion"] == "latest_transaction_at"
        assert resolution["selected_source"] == newer
        count = multiplicity if row["action"] == "Sell" else 1
        old_fee = 0.5 if row["action"] == "Sell" else 1
        new_fee = 3 if row["action"] == "Sell" else 5
        assert resolution["reports"] == sorted([
            {"source": older, "generated_at": None,
             "latest_transaction_at": "2024-01-20T09:00:00.000000Z",
             "fees": [old_fee] * count},
            {"source": newer, "generated_at": None,
             "latest_transaction_at": "2024-01-20T15:00:00.000000Z",
             "fees": [new_fee] * count},
        ], key=lambda report: report["source"])
    assert all("fee_resolution" not in row for row in selected["transactions"]
               if row["action"] not in {"Buy", "Sell"})
    assert "_coinbase_import" not in json.dumps(selected)

    # Refresh must load this evidence from the local transaction payload.
    before = [row["fee_resolution"] for row in trades]
    result = _run_cli(overlap, offline_python, refresh=True)
    assert result.returncode == 0, result.stdout + result.stderr
    refreshed = _output(overlap)
    refreshed_trades = [row for row in refreshed["transactions"]
                        if row["action"] in {"Buy", "Sell"}]
    assert [row["fee_resolution"] for row in refreshed_trades] == before
    assert all(row["source"] == newer for row in refreshed_trades)
    assert _accounting_rows(refreshed) == _accounting_rows(selected)
    assert "_coinbase_import" not in json.dumps(refreshed)


@pytest.mark.parametrize("reverse_sources", [False, True])
def test_tied_coinbase_fee_evidence_preserves_published_outputs_and_cache(
        tmp_path, offline_python, reverse_sources):
    _prepare(tmp_path)
    first, second = "coinbase-1.csv", "coinbase-2.csv"
    if reverse_sources:
        first, second = second, first
    _write_report(tmp_path, first, _rows(buy_fee="1", sell_fee="0.5"))
    baseline = _run_cli(tmp_path, offline_python)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    preserved = [tmp_path / "exports" / "transactions.json",
                 tmp_path / "exports" / "dashboard.html",
                 tmp_path / "cache" / "last_run.json"]
    before = {path: path.read_bytes() for path in preserved}
    cache_before = {path.relative_to(tmp_path / "cache"): path.read_bytes()
                    for path in (tmp_path / "cache").rglob("*") if path.is_file()}

    _write_report(tmp_path, second, _rows(buy_fee="5", sell_fee="3"))
    result = _run_cli(tmp_path, offline_python)
    assert result.returncode != 0
    assert "Import/export failed: Conflicting fee evidence" in result.stderr
    assert "tied" in result.stderr.lower()
    for private_field in ("FEECOIN", "fictional-buy", "fictional-sell",
                          "2024-01-02", "2024-01-10", "2024-01-20", "205", "57",
                          "Fictional purchase", "Fictional sale", first, second):
        assert private_field not in result.stderr
    assert {path: path.read_bytes() for path in preserved} == before
    assert {path.relative_to(tmp_path / "cache"): path.read_bytes()
            for path in (tmp_path / "cache").rglob("*") if path.is_file()} == cache_before
