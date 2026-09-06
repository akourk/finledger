"""Fail-closed import/export regressions using exclusively fictional inputs."""

import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANUAL = "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n"
GOOD = "Example,2024-01-01,Buy,AAA,2,50,100,Fictional purchase\n"


def _environment(root):
    environment = dict(os.environ)
    for key, folder in (("FIN_DATA_DIR", "data"), ("FIN_CACHE_DIR", "cache"),
                        ("FIN_EXPORT_DIR", "exports")):
        (root / folder).mkdir(exist_ok=True)
        environment[key] = str(root / folder)
    environment["FIN_PROJECT_ROOT"] = str(root)
    environment.pop("FIN_ASSERT_INVARIANTS", None)
    return environment


def _cli(root, *arguments):
    return subprocess.run([sys.executable, "-m", "src.main", *arguments],
                          cwd=ROOT, env=_environment(root),
                          capture_output=True, text=True)


@pytest.mark.parametrize("value", ["typo", "NaN", "Infinity", "-inf", "1e999", "1,2"])
def test_numeric_rejection_has_location_but_no_cell_contents(tmp_path, value):
    from src.parsers import parse_manual
    path = tmp_path / "manual-adjustments.csv"
    path.write_text(MANUAL + GOOD.replace(",100,", ',"' + value + '",'))
    with pytest.raises(ValueError) as error:
        parse_manual(path)
    assert "manual-adjustments.csv: row 2, column Amount" in str(error.value)
    assert value not in str(error.value)


def test_valid_numeric_formats_and_optional_blanks():
    from src.parsers import _num
    assert [_num(value) for value in ["", "($1,250.50)", ".5", "1e2"]] == [0, -1250.5, .5, 100]


@pytest.mark.parametrize("arguments", [[], ["--skip-rename"], ["--refresh-prices"],
                                       ["--export-snapshot", "unused.json"],
                                       ["--import-snapshot", "unused.json", "--force"],
                                       ["--init-account-mappings"]])
def test_dry_run_has_no_side_effects_even_with_other_modes(tmp_path, arguments):
    _environment(tmp_path)
    (tmp_path / "data/manual-adjustments.csv").write_text(MANUAL + GOOD)
    (tmp_path / "exports/transactions.json").write_text("previous JSON")
    (tmp_path / "exports/dashboard.html").write_text("previous dashboard")
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    result = _cli(tmp_path, "--dry-run", *arguments)
    assert result.returncode == 0, result.stderr
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes()
                      for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("bad_csv", [
    "ChangedDate,Trans Code,Instrument,Quantity,Price,Amount\n01/01/2024,Buy,AAA,2,50,100\n",
    "Activity Date,Trans Code,Instrument,Quantity,Price,Amount\n01/01/2024,Buy,AAA,2,50,100\nwrong,Buy,AAA,2,50,100\n",
    "Activity Date,Trans Code,Instrument,Quantity,Price,Amount\n",
])
def test_bad_or_empty_import_preserves_both_exports(tmp_path, bad_csv):
    _environment(tmp_path)
    (tmp_path / "data/robinhood.csv").write_text(bad_csv)
    (tmp_path / "exports/transactions.json").write_text("previous JSON")
    (tmp_path / "exports/dashboard.html").write_text("previous dashboard")
    result = _cli(tmp_path, "--skip-rename")
    assert result.returncode != 0
    assert (tmp_path / "exports/transactions.json").read_text() == "previous JSON"
    assert (tmp_path / "exports/dashboard.html").read_text() == "previous dashboard"
    assert not list((tmp_path / "cache").iterdir())


@pytest.mark.parametrize("data", [{}, {"transactions": []}, {"transactions": [dict(date="wrong")]},
    {"analytics": {"data_health": [{"kind": "parser_dropped_rows"}]}}])
def test_refresh_rejects_invalid_or_previously_partial_export(tmp_path, data):
    _environment(tmp_path)
    original = json.dumps(data)
    (tmp_path / "exports/transactions.json").write_text(original)
    (tmp_path / "exports/dashboard.html").write_text("previous dashboard")
    result = _cli(tmp_path, "--refresh-prices")
    assert result.returncode != 0
    assert (tmp_path / "exports/transactions.json").read_text() == original
    assert (tmp_path / "exports/dashboard.html").read_text() == "previous dashboard"


def test_dashboard_build_failure_preserves_pair(tmp_path, monkeypatch):
    import src.main as main
    (tmp_path / "transactions.json").write_text("previous JSON")
    (tmp_path / "dashboard.html").write_text("previous dashboard")
    def failed(*args):
        raise RuntimeError("synthetic template failure")
    monkeypatch.setattr(main, "generate_dashboard", failed)
    with pytest.raises(RuntimeError, match="synthetic"):
        main._publish_dashboard([], tmp_path / "transactions.json")
    assert (tmp_path / "transactions.json").read_text() == "previous JSON"
    assert (tmp_path / "dashboard.html").read_text() == "previous dashboard"


def test_nonfinite_derived_data_cannot_replace_export(tmp_path):
    from src.export import export_json
    path = tmp_path / "transactions.json"
    path.write_text("previous JSON")
    with pytest.raises(ValueError):
        export_json([], path, holdings=[{"value": float("nan")}])
    assert path.read_text() == "previous JSON"


def test_embedded_data_cannot_create_script_elements(tmp_path):
    from src.dashboard import generate_dashboard
    payload = "</ScRiPt><script>globalThis.syntheticInjected=true</script><!--&\u2028\u2029"
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"transactions": [{"description": payload}]}))
    output = tmp_path / "dashboard.html"
    generate_dashboard(source, output)
    class Scripts(HTMLParser):
        count = 0
        def handle_starttag(self, tag, attrs):
            self.count += tag == "script"
    parser = Scripts()
    parser.feed(output.read_text())
    assert parser.count == 1
    assert payload not in output.read_text()
    assert "\\u003c/ScRiPt\\u003e" in output.read_text()


def test_account_types_are_explicit_and_starter_is_reviewable(tmp_path):
    from src.accounts import configure_accounts, write_account_mapping_starter
    txns = [{"account": "Robinhood"}, {"account": "Apple Savings"}]
    with pytest.raises(ValueError, match="Missing Account Type"):
        configure_accounts(txns, {})
    path = tmp_path / "account-mappings.csv"
    write_account_mapping_starter(txns, {}, path)
    assert "REVIEW_REQUIRED" in path.read_text()
    assert "Apple Savings,Savings" in path.read_text()
    with pytest.raises(FileExistsError):
        write_account_mapping_starter(txns, {}, path)
    with pytest.raises(ValueError, match="Account Type must"):
        configure_accounts(txns, {"account_types": {"Robinhood": "Taxble"}})


def test_crypto_source_resolves_new_and_ambiguous_symbols():
    from src.config import normalize_symbol
    for symbol in ["SOL", "DOGE", "LINK", "AVAX", "DOT", "QTUM"]:
        assert normalize_symbol(symbol, "Coinbase") == symbol + "-USD"
        assert normalize_symbol(symbol, "Robinhood") == symbol
    assert normalize_symbol("ETH2", "Coinbase Pro") == "ETH-USD"
    assert normalize_symbol("USD", "Coinbase") == "USD"
    assert normalize_symbol("SOL-USD", "Example") == "SOL-USD"


def test_crypto_to_crypto_pro_trade_cannot_get_zero_basis(tmp_path):
    from src.parsers import parse_coinbase_pro
    path = tmp_path / "coinbase-pro.csv"
    path.write_text("portfolio,type,time,amount,balance,amount/balance unit,transfer id,trade id,order id\n"
                    "default,match,2024-01-01T12:00:00Z,-1,2,BTC,,123,456\n"
                    "default,match,2024-01-01T12:00:00Z,10,12,ETH,,123,456\n")
    with pytest.raises(ValueError, match="unsupported Coinbase Pro trade"):
        parse_coinbase_pro(path)


@pytest.mark.parametrize("bad_name,bad_content", [("bad.csv", None), ("../bad.csv", "text"),
                                                ("bad.txt", "text"), ("bad.csv", ["text"])])
def test_snapshot_validates_all_entries_before_overwriting(tmp_path, bad_name, bad_content):
    from src.snapshot import import_snapshot
    destination = tmp_path / "data"
    destination.mkdir()
    (destination / "good.csv").write_text("previous CSV")
    snapshot = tmp_path / "input.json"
    snapshot.write_text(json.dumps({"version": 1, "files": {"good.csv": "new CSV", bad_name: bad_content}}))
    with pytest.raises(ValueError):
        import_snapshot(snapshot, destination, overwrite=True)
    assert (destination / "good.csv").read_text() == "previous CSV"
    assert list(destination.iterdir()) == [destination / "good.csv"]


def test_snapshot_valid_restore_preserves_existing_by_default(tmp_path):
    from src.snapshot import export_snapshot, import_snapshot
    source = tmp_path / "source"
    source.mkdir()
    (source / "good.csv").write_text(MANUAL + GOOD)
    snapshot = tmp_path / "snapshot.json"
    export_snapshot(source, snapshot)
    destination = tmp_path / "destination"
    assert import_snapshot(snapshot, destination)["written"] == ["good.csv"]
    assert import_snapshot(snapshot, destination)["skipped"] == ["good.csv"]
    assert (destination / "good.csv").read_text() == MANUAL + GOOD


def test_small_unread_file_cannot_disappear_beside_good_account(tmp_path):
    from src.parsers import parse_all_files, validate_ingestion
    (tmp_path / "manual-adjustments.csv").write_text(MANUAL + GOOD)
    (tmp_path / "vanguard-401k.csv").write_text(
        "Date,Symbol,Action,Quantity,unitPrice,Subtotal\n"
        "2024-01-01,AAA,,2,50,100\n")
    with pytest.raises(ValueError, match="vanguard-401k.csv"):
        parse_all_files(tmp_path)


def test_replacement_io_failure_rolls_back_previously_replaced_file(tmp_path, monkeypatch):
    from src.io_safe import replace_files
    first, second = tmp_path / "one.csv", tmp_path / "two.csv"
    first.write_bytes(b"previous one")
    second.write_bytes(b"previous two")
    real_replace = os.replace
    def fail_second(source, destination):
        if destination == second:
            raise OSError("synthetic replacement failure")
        return real_replace(source, destination)
    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="synthetic"):
        replace_files({first: b"new one", second: b"new two"})
    assert first.read_bytes() == b"previous one"
    assert second.read_bytes() == b"previous two"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["one.csv", "two.csv"]
