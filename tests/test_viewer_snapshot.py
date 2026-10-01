"""Viewer export boundaries; all records and labels are independently fictional."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return {
        "generated": "2024-04-02T00:00:00+00:00", "count": 1,
        "transactions": [{"date": "2024-01-03", "account": "Example Account",
                          "symbol": "EXAMPLE", "action": "Buy", "quantity": 0.123456789,
                          "price": 0.0000123456789, "amount": 1.23456789, "fees": 0.001,
                          "description": "Fictional O'Brien & Unicode café \"label\""}],
        "holdings": [{"symbol": "EXAMPLE", "quantity": 0.123456789, "price": None,
                      "value": None, "cost_basis": None, "unrealized_gain": None}],
        "holdings_by_account": [],
        "history": [{"date": "2024-03-31", "total": 1.23456789012345,
                     "positions": [{"symbol": "EXAMPLE", "value": None, "cost_basis": 1.23}],
                     "by_account_group": {"Example Account": 1.23456789012345}}],
        "analytics": {"fictional": {"value": 0.000000123456789}},
        "basis_totals": {}, "basis_methods": {}, "cash_summary": {}, "retirement_meta": {},
        "sector_of": {"EXAMPLE": "Other"}, "display_of": {"EXAMPLE": "Fictional Asset"},
        "action_catalog": {"actions": [{"name": "Buy", "color": "#abcdef"}]}, "tax_tables": {},
    }


@pytest.fixture
def module(isolated_workdir):
    from src import viewer_snapshot
    return viewer_snapshot


def _paths(root, data=None):
    source = root / "exports" / "transactions.json"
    source.write_text(json.dumps(_payload() if data is None else data), encoding="utf-8")
    destination = root / "exports" / "viewer.json"
    destination.write_bytes(b"previous fictional viewer")
    return source, destination


def test_export_preserves_complete_numeric_payload_and_source(module, isolated_workdir):
    source, destination = _paths(isolated_workdir)
    before = source.read_bytes()
    data = json.loads(before)
    result = module.export_viewer_snapshot(source, destination)
    assert result == json.loads(destination.read_bytes())
    assert result["format"] == "finledger-viewer" and result["version"] == 1
    assert result["as_of"] == result["data"]["as_of"] == "2024-03-31"
    assert {key: value for key, value in result["data"].items() if key != "as_of"} == data
    assert source.read_bytes() == before
    assert not list((isolated_workdir / "data").iterdir())
    assert not list((isolated_workdir / "cache").iterdir())


@pytest.mark.parametrize("headers,history,expected", [
    ({"as_of": "2024-02-29", "snapshot_date": "2024-03-30"}, True, "2024-02-29"),
    ({"snapshot_date": "2024-03-30"}, True, "2024-03-30"),
    ({}, True, "2024-03-31"), ({}, False, "2024-04-02"),
])
def test_cutoff_matches_frontend_precedence_without_changing_input(module, headers, history, expected):
    data = _payload()
    data.update(headers)
    if not history:
        data["history"] = []
    before = copy.deepcopy(data)
    snapshot = module.build_viewer_snapshot(data)
    assert snapshot["as_of"] == snapshot["data"]["as_of"] == expected
    assert data == before


@pytest.mark.parametrize("field", ["transactions", "holdings", "holdings_by_account", "history",
                                  "analytics", "basis_totals", "basis_methods", "cash_summary",
                                  "retirement_meta", "sector_of", "display_of", "action_catalog", "tax_tables"])
def test_required_fields_fail_closed(module, isolated_workdir, field):
    data = _payload()
    del data[field]
    source, destination = _paths(isolated_workdir, data)
    with pytest.raises(ValueError, match="Invalid viewer snapshot"):
        module.export_viewer_snapshot(source, destination)
    assert destination.read_bytes() == b"previous fictional viewer"


@pytest.mark.parametrize("field", ["transactions", "holdings", "holdings_by_account", "history"])
@pytest.mark.parametrize("record", [None, 3, "fictional", []])
def test_records_must_be_objects(module, field, record):
    data = _payload()
    data[field] = [record]
    with pytest.raises(ValueError, match="arrays of objects"):
        module.build_viewer_snapshot(data)


@pytest.mark.parametrize("field,value", [
    ("as_of", "2024-02-30"), ("snapshot_date", "2024-1-01"),
    ("generated", "fictional-secret-date"), ("as_of", None), ("as_of", "0000-01-01"),
])
def test_header_dates_reject_invalid_values_without_echoing(module, field, value):
    data = _payload()
    data[field] = value
    with pytest.raises(ValueError) as error:
        module.build_viewer_snapshot(data)
    assert str(value) not in str(error.value)


@pytest.mark.parametrize("field", ["transactions", "history"])
@pytest.mark.parametrize("value", [None, "2023-02-29", "2024-01-01\" autofocus", "20240101"])
def test_all_record_dates_are_validated(module, field, value):
    data = _payload()
    data[field][0]["date"] = value
    with pytest.raises(ValueError):
        module.build_viewer_snapshot(data)


def test_missing_all_date_sources_is_rejected(module):
    data = _payload()
    del data["generated"]
    data["history"] = []
    with pytest.raises(ValueError, match="calendar date"):
        module.build_viewer_snapshot(data)


@pytest.mark.parametrize("value", ["fictional-secret-number", True, {}, []])
@pytest.mark.parametrize("field", ["quantity", "price", "value", "cost_basis", "realized_gain", "cash_flow"])
def test_known_financial_fields_are_typed(module, field, value):
    data = _payload()
    data["transactions"][0][field] = value
    with pytest.raises(ValueError, match="financial fields must be numbers") as error:
        module.build_viewer_snapshot(data)
    assert "fictional-secret-number" not in str(error.value)


@pytest.mark.parametrize("change", [
    {"positions": [3]}, {"in_transit": [None]}, {"valuation_precision": []},
    {"by_sector": {"Other": "fictional-secret-number"}},
    {"positions": [{"price": "fictional-secret-number"}]},
])
def test_nested_history_financial_shapes(module, change):
    data = _payload()
    data["history"][0].update(change)
    with pytest.raises(ValueError):
        module.build_viewer_snapshot(data)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_preserves_previous_file(module, isolated_workdir, literal):
    source, destination = _paths(isolated_workdir)
    source.write_text(json.dumps(_payload()).replace("0.001", literal))
    with pytest.raises(ValueError):
        module.export_viewer_snapshot(source, destination)
    assert destination.read_bytes() == b"previous fictional viewer"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_unknown_nested_fields_also_rejected(module, value):
    data = _payload()
    data["analytics"]["unknown"] = {"values": [value]}
    with pytest.raises(ValueError, match="finite"):
        module.build_viewer_snapshot(data)


@pytest.mark.parametrize("contents", [b"not fictional JSON", b"\xff", b"[1,2]",
    b'{"version":1,"files":[{"content":"fictional raw CSV"}]}',
    b'{"format":"other-viewer","version":1,"data":{}}'])
def test_malformed_and_wrong_source_formats_preserve_previous_file(module, isolated_workdir, contents):
    source, destination = _paths(isolated_workdir)
    source.write_bytes(contents)
    with pytest.raises(ValueError):
        module.export_viewer_snapshot(source, destination)
    assert destination.read_bytes() == b"previous fictional viewer"


@pytest.mark.parametrize("change", [{"format": "other-viewer"}, {"version": 2},
                                   {"version": True}, {"as_of": "2024-04-01"}])
def test_envelope_format_version_and_dates(module, change):
    snapshot = module.build_viewer_snapshot(_payload())
    snapshot.update(change)
    with pytest.raises(ValueError):
        module.validate_viewer_snapshot(snapshot)


def test_integer_valued_json_numbers_are_compatible(module):
    data = _payload()
    data["count"] = 1.0
    snapshot = module.build_viewer_snapshot(data)
    snapshot["version"] = 1.0
    module.validate_viewer_snapshot(snapshot)


@pytest.mark.parametrize("count", [True, "1", None, 1.5, -1, 2])
def test_count_must_match_transactions_as_a_number(module, count):
    data = _payload()
    data["count"] = count
    with pytest.raises(ValueError, match="count"):
        module.build_viewer_snapshot(data)


def test_integer_beyond_browser_finite_range_is_rejected(module):
    data = _payload()
    data["analytics"]["fictional"]["value"] = 10 ** 400
    with pytest.raises(ValueError, match="finite"):
        module.build_viewer_snapshot(data)


def test_limits_include_envelope_and_utf8_bytes(module, isolated_workdir, monkeypatch):
    source, destination = _paths(isolated_workdir)
    assert module.MAX_SNAPSHOT_BYTES == 25 * 1024 * 1024
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", len(source.read_bytes()) - 1)
    with pytest.raises(ValueError, match="size limit"):
        module.export_viewer_snapshot(source, destination)
    # Compact source fits, but the envelope itself must fit the same limit.
    source.write_text(json.dumps(_payload(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", len(source.read_bytes()))
    with pytest.raises(ValueError, match="size limit"):
        module.export_viewer_snapshot(source, destination)
    assert destination.read_bytes() == b"previous fictional viewer"


def test_depth_boundary_counts_envelope_root_as_zero(module):
    snapshot = module.build_viewer_snapshot(_payload())
    child = snapshot
    # Existing data depth is smaller; extend one independent branch to max.
    for _ in range(module.MAX_JSON_DEPTH):
        child["nested"] = {}
        child = child["nested"]
    module.validate_viewer_snapshot(snapshot)
    child["nested"] = None
    with pytest.raises(ValueError, match="nesting"):
        module.validate_viewer_snapshot(snapshot)


@pytest.mark.parametrize("alias", ["same", "relative", "symlink", "hardlink"])
def test_source_aliases_cannot_be_overwritten(module, isolated_workdir, alias):
    source, _ = _paths(isolated_workdir)
    before = source.read_bytes()
    if alias == "same":
        destination = source
    elif alias == "relative":
        destination = Path("exports/../exports/transactions.json")
    else:
        destination = source.with_name("alias.json")
        if alias == "symlink":
            destination.symlink_to(source)
        else:
            os.link(source, destination)
    with pytest.raises(ValueError, match="must differ"):
        module.export_viewer_snapshot(source, destination)
    assert source.read_bytes() == before


@pytest.mark.parametrize("folder", ["data", "cache"])
def test_destination_cannot_overwrite_inputs_or_cache(module, isolated_workdir, folder):
    source, _ = _paths(isolated_workdir)
    protected = isolated_workdir / folder / "fictional.json"
    protected.write_bytes(b"original fictional input")
    with pytest.raises(ValueError, match="outside inputs and caches"):
        module.export_viewer_snapshot(source, protected)
    assert protected.read_bytes() == b"original fictional input"
    alias = isolated_workdir / (folder + "-alias")
    alias.symlink_to(protected.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="outside inputs and caches"):
        module.export_viewer_snapshot(source, alias / "new.json")
    assert not (protected.parent / "new.json").exists()


@pytest.mark.parametrize("boundary", ["read", "stage", "replace"])
def test_io_failures_preserve_destination_and_source(module, isolated_workdir, monkeypatch, boundary):
    import src.io_safe as io_safe
    source, destination = _paths(isolated_workdir)
    before = source.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("fictional I/O failure")
    if boundary == "read":
        original = Path.open
        def read_failure(path, *args, **kwargs):
            return fail() if path == source else original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", read_failure)
    elif boundary == "stage":
        monkeypatch.setattr(io_safe, "_prepare", fail)
    else:
        monkeypatch.setattr(io_safe.os, "replace", fail)
    with pytest.raises(OSError, match="fictional"):
        module.export_viewer_snapshot(source, destination)
    if boundary == "read":
        monkeypatch.setattr(Path, "open", original)
    assert source.read_bytes() == before
    assert destination.read_bytes() == b"previous fictional viewer"
    assert not list(destination.parent.glob(".fin-*"))


@pytest.mark.parametrize("change", [
    {"description": "<svg onload=fictional>"}, {"description": "greater > than"},
    {"__proto__": {}}, {"constructor": {}}, {"prototype": {}}, {"<bad-key>": "fictional"},
])
def test_markup_and_unsafe_keys_are_rejected(module, change):
    data = _payload()
    data["transactions"][0].update(change)
    with pytest.raises(ValueError) as error:
        module.build_viewer_snapshot(data)
    assert "fictional>" not in str(error.value)


@pytest.mark.parametrize("color", ["red", "#123\" onmouseover=\"fictional", None, 3, "#xyzxyz",
                                   "#abc", "#abcde", "#abcdefg", "#abcdef12"])
def test_action_colors_cannot_inject_attributes(module, color):
    data = _payload()
    data["action_catalog"]["actions"][0]["color"] = color
    with pytest.raises(ValueError, match="hexadecimal"):
        module.build_viewer_snapshot(data)


@pytest.mark.parametrize("name", [None, 3, {}, []])
def test_action_catalog_names_are_strings(module, name):
    data = _payload()
    data["action_catalog"]["actions"][0]["name"] = name
    with pytest.raises(ValueError, match="action names"):
        module.build_viewer_snapshot(data)


def _cli(root, offline_python, *arguments):
    environment = dict(os.environ, FIN_PROJECT_ROOT=str(root), FIN_DATA_DIR=str(root / "data"),
                       FIN_CACHE_DIR=str(root / "cache"), FIN_EXPORT_DIR=str(root / "exports"),
                       FIN_AS_OF_DATE="2024-04-02", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    # Missing provider makes accidental market preflight fail even before the
    # offline transport guard; the export must also avoid importer/cache calls.
    script = ("import runpy,sys; sys.modules['yfinance']=None; "
              "sys.argv=['src.main']+sys.argv[1:]; runpy.run_module('src.main',run_name='__main__')")
    return subprocess.run(offline_python([sys.executable, "-c", script, *arguments]), cwd=ROOT,
                          env=environment, capture_output=True, text=True)


@pytest.mark.parametrize("custom_source", [False, True])
def test_cli_uses_existing_json_and_leaves_all_other_state_unchanged(isolated_workdir, offline_python, custom_source):
    root = isolated_workdir
    source, destination = _paths(root)
    if custom_source:
        source = source.rename(root / "exports" / "fictional-custom.json")
    (root / "data" / "fictional.csv").write_text("invalid fictional CSV\n")
    (root / "exports" / "dashboard.html").write_bytes(b"fictional previous dashboard")
    (root / "cache" / "last_run.json").write_bytes(b"fictional previous baseline")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    arguments = ["--export-viewer-snapshot", str(destination), "--refresh-prices",
                 "--import-snapshot", str(root / "missing-backup.json"), "--init-account-mappings"]
    if custom_source:
        arguments.extend(["-o", str(source)])
    result = _cli(root, offline_python, *arguments)
    assert result.returncode == 0, result.stderr
    assert json.loads(destination.read_bytes())["data"]["transactions"] == _payload()["transactions"]
    assert {path: path.read_bytes() for path in before if path != destination} == {
        path: contents for path, contents in before.items() if path != destination}
    assert {path for path in root.rglob("*") if path.is_file()} == set(before)


def test_cli_dry_run_does_not_even_read_source_or_touch_files(module, isolated_workdir, monkeypatch):
    import src.main as main
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run attempted an operation")
    monkeypatch.setattr(module, "export_viewer_snapshot", forbidden)
    monkeypatch.setattr(main, "require_market_data", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sys, "argv", ["fin", "--dry-run", "--export-viewer-snapshot", "unused.json",
                                      "-o", "missing.json", "--import-snapshot", "missing-backup.json"])
    main.main()


def test_cli_malformed_source_preserves_existing_artifacts(isolated_workdir, offline_python):
    source, destination = _paths(isolated_workdir)
    source.write_text('{"fictional-secret":NaN}')
    before = {path: path.read_bytes() for path in isolated_workdir.rglob("*") if path.is_file()}
    result = _cli(isolated_workdir, offline_python, "--export-viewer-snapshot", str(destination))
    assert result.returncode == 1
    assert "fictional-secret" not in result.stderr
    assert before == {path: path.read_bytes() for path in isolated_workdir.rglob("*") if path.is_file()}


def test_current_exporter_payload_is_compatible(module, isolated_workdir, monkeypatch):
    from src.export import export_json
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-04-02")
    data = _payload()
    source, destination = _paths(isolated_workdir)
    export_json(data["transactions"], source, holdings=data["holdings"],
                history=data["history"], analytics=data["analytics"])
    snapshot = module.export_viewer_snapshot(source, destination)
    assert snapshot["as_of"] == "2024-03-31"
    assert snapshot["data"]["action_catalog"]["actions"]
