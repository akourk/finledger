"""Observed prices affect valuations, never the fictional transaction ledger."""

import json
import socket
import sys

import pytest


@pytest.fixture
def broker_pipeline(isolated_workdir, stub_prices, monkeypatch):
    from src import main

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-20")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("network forbidden"))
    monkeypatch.setattr(main, "fetch_latest_close_batch", lambda symbols: 0)
    root = isolated_workdir
    (root / "data" / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Account Group,,,Example IRA,Example IRA\n"
        "Account Type,,,Example IRA,Retirement\n", encoding="utf-8")
    (root / "data" / "manual-adjustments.csv").write_text(
        "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n"
        "Example IRA,2024-06-01,Buy,Example Retirement Fund,4,25,100,Fictional fund\n"
        "Example IRA,2024-06-01,Option Buy,FAKEA 7/19/2024 Call $100.00,1,2,200,Fictional option\n",
        encoding="utf-8")

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["fin", *args])
        main.main()
        return json.loads((root / "exports" / "transactions.json").read_text(encoding="utf-8"))

    return root, run


def _marks(payload):
    return {row["symbol"]: row for row in payload["holdings_by_account"]}


def test_broker_observations_preserve_ledger_and_basis_across_both_paths(broker_pipeline):
    root, run = broker_pipeline
    original = run("--skip-rename")
    path = root / "data" / "broker-prices.csv"
    path.write_text("Date,Symbol,Price\n"
                    "2024-06-20,Example Retirement Fund,32\n"
                    "2024-06-20,FAKEA 7/19/2024 Call $100.00,0\n", encoding="utf-8")
    refresh = run("--refresh-prices")
    full = run("--skip-rename")
    for payload in (refresh, full):
        assert payload["transactions"] == original["transactions"]
        assert payload["count"] == 2
        marks = _marks(payload)
        fund = marks["Example Retirement Fund"]
        option = marks["FAKEA 7/19/2024 Call $100.00"]
        assert (fund["quantity"], fund["value"], fund["cost_basis"]) == (4, 128, 100)
        assert (option["quantity"], option["price"], option["value"], option["cost_basis"]) == (1, 0, 0, 200)
        assert all(row["pricing"]["source"] == "broker" for row in marks.values())
        assert payload["history"][-1]["total"] == 128
        assert payload["history"][-1]["total_cost_basis"] == 300
        assert payload["analytics"]["pricing_quality"]["total"]["unavailable"] == 0
        assert payload["analytics"]["header_summary"]["prices_freshness_unknown"] is False
        for method in payload["basis_methods"].values():
            option_row = next(r for r in method["holdings"] if r["symbol"] == option["symbol"])
            assert option_row["value"] == 0
            assert option_row["unrealized_gain"] == -200
        earlier = [h for h in payload["history"] if h["date"] < "2024-06-20"]
        original_earlier = [h for h in original["history"] if h["date"] < "2024-06-20"]
        assert earlier == original_earlier
    assert full["holdings_by_account"] == refresh["holdings_by_account"]
    assert full["history"] == refresh["history"]


def test_each_run_reloads_observations_and_removed_marks_restore_fallback(broker_pipeline):
    root, run = broker_pipeline
    original = run("--skip-rename")
    path = root / "data" / "broker-prices.csv"
    path.write_text("Date,Symbol,Price\n2024-06-20,Example Retirement Fund,32\n", encoding="utf-8")
    assert _marks(run("--refresh-prices"))["Example Retirement Fund"]["value"] == 128
    path.write_text("Date,Symbol,Price\n2024-06-20,Example Retirement Fund,33\n", encoding="utf-8")
    assert _marks(run("--refresh-prices"))["Example Retirement Fund"]["value"] == 132
    path.unlink()
    assert _marks(run("--refresh-prices")) == _marks(original)


@pytest.mark.parametrize("mode", ["--skip-rename", "--refresh-prices"])
def test_malformed_observation_preserves_outputs_and_cached_market_data(broker_pipeline, mode):
    root, run = broker_pipeline
    run("--skip-rename")
    before = {str(p.relative_to(root)): p.read_bytes()
              for directory in (root / "exports", root / "cache")
              for p in directory.rglob("*") if p.is_file()}
    (root / "data" / "broker-prices.csv").write_text(
        "Date,Symbol,Price\n2024-06-20,Example Retirement Fund,invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="broker-prices.csv.*Price"):
        run(mode)
    after = {str(p.relative_to(root)): p.read_bytes()
             for directory in (root / "exports", root / "cache")
             for p in directory.rglob("*") if p.is_file()}
    assert after == before


def test_snapshot_round_trip_carries_marks_without_turning_them_into_transactions(broker_pipeline):
    from src.scanner import detect_broker
    from src.snapshot import export_snapshot, import_snapshot

    root, run = broker_pipeline
    path = root / "data" / "broker-prices.csv"
    contents = "Date,Symbol,Price\n2024-06-20,Example Retirement Fund,32\n"
    path.write_text(contents, encoding="utf-8")
    assert detect_broker(path) == "skip"
    bundle = root / "fictional.snapshot.json"
    export_snapshot(root / "data", bundle)
    path.unlink()
    import_snapshot(bundle, root / "data")
    assert path.read_text(encoding="utf-8") == contents
    assert run("--skip-rename")["count"] == 2
