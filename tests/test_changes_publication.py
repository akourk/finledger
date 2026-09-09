"""The activity baseline advances only with a successfully published dashboard."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

import pytest


ACCOUNT = "Fictional Method Account"
SYMBOL = "PRISM"


@pytest.fixture
def activity_pipeline(isolated_workdir, stub_prices, monkeypatch):
    from src import main

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-30")
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail(
        "Activity publication fixtures must not contact the network"))
    monkeypatch.setattr(main, "fetch_latest_close_batch", lambda symbols: 0)
    data = isolated_workdir / "data"
    metadata = data / "metadata.csv"
    metadata.write_text(
        "Type,Date,Amount,Symbol,Note\n"
        f"Account Group,,,{ACCOUNT},{ACCOUNT}\n"
        f"Account Type,,,{ACCOUNT},Taxable\n"
        f"Lot Method,,,{ACCOUNT},HIFO\n", encoding="utf-8")
    ledger = data / "manual-adjustments.csv"
    header = "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n"
    ledger.write_text(header
        + f"{ACCOUNT},2024-01-01,Buy,{SYMBOL},1,10,10,fictional inexpensive lot\n"
        + f"{ACCOUNT},2024-02-01,Buy,{SYMBOL},1,30,30,fictional expensive lot\n",
        encoding="utf-8")
    stub_prices.set(SYMBOL, {"2024-01-01": 10, "2024-02-01": 30,
                             "2024-03-01": 40, "2024-06-30": 40})
    stub_prices.set_sector(SYMBOL, "Other")
    for benchmark in ("SPY", "BND", "VXUS"):
        stub_prices.set(benchmark, {"2024-01-01": 100, "2024-06-30": 100})

    class Pipeline:
        output = isolated_workdir / "exports" / "transactions.json"
        baseline = isolated_workdir / "cache" / "last_run.json"

        def paths(self):
            return (self.output, self.output.parent / "dashboard.html", self.baseline)

        def bytes(self):
            return {path: path.read_bytes() if path.exists() else None for path in self.paths()}

        def run(self, *, refresh=False):
            # Recreate process-start metadata state before either path.
            from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
            ACCOUNT_GROUPS.clear()
            ACCOUNT_TYPES.clear()
            monkeypatch.setattr(sys, "argv", ["fin", "--refresh-prices" if refresh else "--skip-rename",
                                              "--output", str(self.output)])
            main.main()
            return json.loads(self.output.read_text(encoding="utf-8")) if self.output.exists() else None

        def add_sale(self):
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(f"{ACCOUNT},2024-03-01,Sell,{SYMBOL},1,40,40,fictional partial sale\n")

        def set_fifo(self):
            metadata.write_text(metadata.read_text(encoding="utf-8").replace(",HIFO\n", ",FIFO\n"), encoding="utf-8")

        def empty(self):
            ledger.write_text(header, encoding="utf-8")

    return Pipeline()


def _assert_current_matches_export(data, baseline):
    current = data["analytics"]["changes"]["current"]
    assert current["schema_version"] == 2
    assert current["basis_method"] == "annotated"
    assert current["cost_basis"] == data["basis_totals"]["cost_basis"]
    assert current["realized_gain"] == data["basis_totals"]["realized_gain"]
    assert json.loads(baseline.read_text(encoding="utf-8")) == current


def _assert_sale_changes(data):
    changes = data["analytics"]["changes"]
    assert changes["first_run"] is False
    assert changes["txn_count_delta"] == 1
    assert changes["new_txns_by_date"] == [{"date": "2024-03-01", "count": 1}]
    assert changes["value_delta"] == -40
    assert changes["basis_delta"] == -30
    assert changes["realized_delta"] == 10
    # The comparison table deliberately remains hypothetical FIFO.
    assert data["basis_methods"]["fifo"]["totals"]["cost_basis"] == 30
    assert data["basis_methods"]["fifo"]["totals"]["realized_gain"] == 30


def _assert_no_changes(data):
    changes = data["analytics"]["changes"]
    assert changes["first_run"] is False
    assert all(changes[field] == 0 for field in (
        "txn_count_delta", "value_delta", "basis_delta", "realized_delta"))
    assert changes["new_txns_by_date"] == []


def _inject_failure(monkeypatch, boundary, pipeline):
    from src import analytics, io_safe, main
    from src.analytics import data_health

    calls = []

    def fail(*args, **kwargs):
        calls.append(boundary)
        raise OSError("Fictional publication failure")

    if boundary == "analytics":
        monkeypatch.setattr(analytics, "compute_alerts", fail)
        expected_error = OSError
    elif boundary == "invariants":
        def fail_invariant(*args, **kwargs):
            calls.append(boundary)
            raise data_health.InvariantViolation("Fictional invariant failure")

        monkeypatch.setattr(data_health, "check_invariants", fail_invariant)
        expected_error = data_health.InvariantViolation
    elif boundary == "serialization":
        original = main.export_json

        def fail_serialization(txns, output, **payload):
            calls.append(boundary)
            payload["analytics"] = {**payload["analytics"], "fictional_invalid_value": object()}
            return original(txns, output, **payload)

        monkeypatch.setattr(main, "export_json", fail_serialization)
        expected_error = TypeError
    elif boundary == "rendering":
        monkeypatch.setattr(main, "generate_dashboard", fail)
        expected_error = OSError
    else:
        target = dict(zip(("json", "html", "baseline"), pipeline.paths()))[boundary]
        original = io_safe.os.replace

        def fail_replacement(source, destination):
            # Fail once at the real published target; allow rollback to run.
            if Path(destination).absolute() == target.absolute() and not calls:
                calls.append(boundary)
                raise OSError("Fictional replacement failure")
            return original(source, destination)

        monkeypatch.setattr(io_safe.os, "replace", fail_replacement)
        expected_error = OSError
    return expected_error, calls


def test_full_and_refresh_activity_use_actual_hifo_totals(activity_pipeline):
    pipeline = activity_pipeline
    before = pipeline.run()
    assert before["analytics"]["changes"]["first_run"] is True
    assert before["basis_totals"]["cost_basis"] == 40
    _assert_current_matches_export(before, pipeline.baseline)
    pipeline.add_sale()
    after = pipeline.run()
    _assert_sale_changes(after)
    _assert_current_matches_export(after, pipeline.baseline)
    refreshed = pipeline.run(refresh=True)
    _assert_no_changes(refreshed)
    _assert_current_matches_export(refreshed, pipeline.baseline)


@pytest.mark.parametrize("boundary", ["analytics", "invariants", "serialization", "rendering",
                                      "json", "html", "baseline"])
def test_failed_run_preserves_outputs_and_activity_until_successful_retry(
        activity_pipeline, monkeypatch, boundary):
    pipeline = activity_pipeline
    pipeline.run()
    original = pipeline.bytes()
    pipeline.add_sale()
    with monkeypatch.context() as patch:
        error, calls = _inject_failure(patch, boundary, pipeline)
        with pytest.raises(error):
            pipeline.run()
        assert calls == [boundary], "Failure injection did not reach its intended boundary"
    assert pipeline.bytes() == original
    retry = pipeline.run()
    _assert_sale_changes(retry)
    _assert_current_matches_export(retry, pipeline.baseline)
    _assert_no_changes(pipeline.run())


def test_failed_refresh_preserves_method_change_for_retry(activity_pipeline, monkeypatch):
    pipeline = activity_pipeline
    pipeline.add_sale()
    before = pipeline.run()
    assert before["basis_totals"]["cost_basis"] == 10
    assert before["basis_totals"]["realized_gain"] == 10
    original = pipeline.bytes()
    pipeline.set_fifo()
    with monkeypatch.context() as patch:
        error, calls = _inject_failure(patch, "baseline", pipeline)
        with pytest.raises(error):
            pipeline.run(refresh=True)
        assert calls == ["baseline"]
    assert pipeline.bytes() == original
    retried = pipeline.run(refresh=True)
    changes = retried["analytics"]["changes"]
    assert changes["basis_delta"] == changes["realized_delta"] == 20
    assert changes["txn_count_delta"] == 0
    _assert_current_matches_export(retried, pipeline.baseline)
    _assert_no_changes(pipeline.run(refresh=True))


@pytest.mark.parametrize("boundary", ["rendering", "baseline"])
def test_first_failed_run_creates_neither_baseline_nor_partial_dashboard(
        activity_pipeline, monkeypatch, boundary):
    pipeline = activity_pipeline
    assert all(value is None for value in pipeline.bytes().values())
    with monkeypatch.context() as patch:
        error, calls = _inject_failure(patch, boundary, pipeline)
        with pytest.raises(error):
            pipeline.run()
        assert calls == [boundary]
    assert all(value is None for value in pipeline.bytes().values())
    result = pipeline.run()
    assert result["analytics"]["changes"]["first_run"] is True
    _assert_current_matches_export(result, pipeline.baseline)


def test_custom_outputs_publish_configured_cache_baseline_last(activity_pipeline, monkeypatch,
                                                               isolated_workdir):
    from src import io_safe

    pipeline = activity_pipeline
    pipeline.output = isolated_workdir / "custom-output" / "fictional-ledger.json"
    observed = []
    original = io_safe.os.replace
    targets = {path.absolute() for path in pipeline.paths()}

    def record(source, destination):
        target = Path(destination).absolute()
        if target in targets:
            observed.append(target)
        return original(source, destination)

    monkeypatch.setattr(io_safe.os, "replace", record)
    data = pipeline.run()
    assert observed == [path.absolute() for path in pipeline.paths()]
    _assert_current_matches_export(data, pipeline.baseline)
    assert not (pipeline.output.parent / "last_run.json").exists()


def test_empty_pipeline_run_preserves_existing_comparison_baseline(activity_pipeline):
    pipeline = activity_pipeline
    pipeline.run()
    original = pipeline.bytes()
    pipeline.empty()
    with pytest.raises(ValueError, match="No valid transactions"):
        pipeline.run()
    assert pipeline.bytes() == original


def test_custom_output_cannot_overwrite_activity_baseline(activity_pipeline):
    pipeline = activity_pipeline
    pipeline.run()
    original = pipeline.bytes()
    pipeline.add_sale()
    pipeline.output = pipeline.baseline
    with pytest.raises(ValueError, match="activity snapshot"):
        pipeline.run()
    assert all(path.read_bytes() == contents for path, contents in original.items())
    assert not (pipeline.baseline.parent / "dashboard.html").exists()


def test_read_only_analytics_does_not_advance_baseline(activity_pipeline):
    from src.analytics import build_analytics

    pipeline = activity_pipeline
    data = pipeline.run()
    original = pipeline.baseline.read_bytes()
    holdings = deepcopy(data["holdings_by_account"])
    assert len(holdings) == 1
    holdings[0]["value"] += 20
    result = build_analytics(data["transactions"], data["history"], data["holdings"], holdings,
                             data["retirement_meta"], cash_summary=data["cash_summary"],
                             basis_methods=data["basis_methods"], basis_totals=data["basis_totals"])
    assert result["changes"]["value_delta"] == 20
    assert result["changes"]["current"]["value"] == 100
    assert pipeline.baseline.read_bytes() == original
