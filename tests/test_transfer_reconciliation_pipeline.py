"""Split evidence must agree across real full/refresh pipeline consumers."""
from __future__ import annotations

import json
import socket
import sys

import pytest


SYMBOL = "LUME"
SOURCE = "Transfer Source"
DESTINATION = "Transfer Destination"
SPLITS = [["2024-06-12", 2.0]]


@pytest.fixture
def transfer_pipeline(isolated_workdir, stub_prices, monkeypatch):
    from src import main, prices

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-30")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail(
        "Fictional pipeline must not contact the network"))
    monkeypatch.setattr(main, "fetch_latest_close_batch", lambda symbols: 0)
    data = isolated_workdir / "data"
    (data / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        f"Account Group,,,{SOURCE},{SOURCE}\n"
        f"Account Type,,,{SOURCE},Taxable\n"
        f"Account Group,,,{DESTINATION},{DESTINATION}\n"
        f"Account Type,,,{DESTINATION},Taxable\n",
        encoding="utf-8")
    (data / "manual-adjustments.csv").write_text(
        "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n"
        f"{SOURCE},2024-06-01,Buy,{SYMBOL},10,10,100,fictional acquisition\n"
        f"{SOURCE},2024-06-10,Transfer Out,{SYMBOL},5,10,0,departure\n"
        f"{SOURCE},2024-06-12,Split,{SYMBOL},5,0,0,remaining custody split\n"
        f"{DESTINATION},2024-06-14,Transfer In,{SYMBOL},10,8,0,arrival\n"
        f"{DESTINATION},2024-06-20,Sell,{SYMBOL},2,8,16,partial sale\n",
        encoding="utf-8")
    stub_prices.set(SYMBOL, {
        "2024-06-01": 5.0, "2024-06-10": 5.0,
        "2024-06-12": 8.0, "2024-06-14": 8.0,
        "2024-06-20": 8.0, "2024-06-30": 8.0,
    })
    stub_prices.set_sector(SYMBOL, "Technology")
    evidence = {"splits": SPLITS}
    monkeypatch.setattr(prices, "_fetch_splits", lambda symbol:
                        evidence["splits"] if symbol == SYMBOL else [])

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["fin", *args])
        main.main()
        return json.loads((isolated_workdir / "exports" /
                           "transactions.json").read_text(encoding="utf-8"))

    return run, evidence


def _assert_reconciled(payload, *, source_basis=100.0):
    source = next(h for h in payload["holdings_by_account"]
                  if h["account_group"] == SOURCE)
    destination = next(h for h in payload["holdings_by_account"]
                       if h["account_group"] == DESTINATION)
    assert source["quantity"] == 10.0
    assert destination["quantity"] == 8.0
    assert source["cost_basis"] == pytest.approx(source_basis / 2)
    assert destination["cost_basis"] == pytest.approx(source_basis * .4)
    assert payload["basis_totals"]["cost_basis"] == pytest.approx(source_basis * .9)
    assert payload["basis_totals"]["realized_gain"] == pytest.approx(16 - source_basis * .1)
    assert payload["history"][-1]["total_cost_basis"] == pytest.approx(source_basis * .9)
    for method in payload["basis_methods"].values():
        assert method["totals"]["cost_basis"] == pytest.approx(source_basis * .9)
        assert method["totals"]["realized_gain"] == pytest.approx(16 - source_basis * .1)
    arrival = next(t for t in payload["transactions"] if t["action"] == "Transfer In")
    assert arrival["cost_basis"] == pytest.approx(source_basis / 2)
    assert arrival["account_transfer"]["flow"] == pytest.approx(80.0)
    assert not any(i["kind"] in {"history_holdings_basis_parity",
                                "unreconciled_transfer_quantity",
                                "unsupported_transfer_share_units"}
                   for i in payload["analytics"]["data_health"])


@pytest.mark.parametrize("discovery", ["deep_refresh", "coverage_fetch"])
def test_full_pipeline_waits_for_cold_split_evidence(transfer_pipeline,
                                                     monkeypatch, discovery):
    from src import main, prices

    run, _ = transfer_pipeline
    assert SYMBOL not in prices._load_splits()
    if discovery == "coverage_fetch":
        # A throttled deep refresh leaves ensure_coverage responsible for
        # discovering the split alongside the first historical quote fetch.
        monkeypatch.setattr(main, "revalidate_stale_caches", lambda *a, **kw: None)
    _assert_reconciled(run("--skip-rename"))


def test_refresh_rebuilds_all_walks_when_split_evidence_changes(transfer_pipeline,
                                                               monkeypatch):
    from src import main

    run, evidence = transfer_pipeline
    evidence["splits"] = []
    old = run("--skip-rename")
    old_destination = next(h for h in old["holdings_by_account"]
                           if h["account_group"] == DESTINATION)
    assert old_destination["cost_basis"] == 64.0

    evidence["splits"] = SPLITS
    requests = []
    ensure = main.ensure_coverage

    def record(symbols, start, end, **kwargs):
        requests.append((symbols, start, end))
        return ensure(symbols, start, end, **kwargs)

    monkeypatch.setattr(main, "ensure_coverage", record)
    refreshed = run("--refresh-prices", "--refresh-caches")
    _assert_reconciled(refreshed)
    assert requests == [([SYMBOL], "2024-06-01", "2024-06-30")]
    # The JSON reload must reconstruct pairs; prior annotations and stored
    # comparison quantities/basis are not authoritative after new evidence.
    full = run("--skip-rename")
    for key in ("basis_totals", "basis_methods", "holdings_by_account", "history"):
        assert refreshed[key] == full[key]


def test_unchanged_refresh_preserves_throttle_and_skips_history_fetch(
        transfer_pipeline, monkeypatch):
    from src import main, prices

    run, _ = transfer_pipeline
    first = run("--skip-rename")
    monkeypatch.setattr(main, "ensure_coverage", lambda *a, **kw: pytest.fail(
        "Unchanged split evidence must not trigger historical backfill"))
    monkeypatch.setattr(prices, "_fetch_splits", lambda *a, **kw: pytest.fail(
        "The recent deep-refresh timestamp must retain its throttle"))
    refreshed = run("--refresh-prices")
    _assert_reconciled(refreshed)
    assert refreshed["basis_methods"] == first["basis_methods"]


def test_override_stamping_follows_finalized_splits_in_both_paths(
        transfer_pipeline, isolated_workdir, monkeypatch):
    from src import cost_basis_overrides, prices

    run, _ = transfer_pipeline
    metadata = isolated_workdir / "data" / "metadata.csv"
    with metadata.open("a", encoding="utf-8") as file:
        file.write(f"Cost Basis,2024-06-01,80,{SOURCE},10 {SYMBOL}\n")
    original = cost_basis_overrides.match_and_stamp
    observations = []

    def stamp(txns, overrides):
        observations.append(prices.split_factor_since(SYMBOL, "2024-06-10"))
        return original(txns, overrides)

    monkeypatch.setattr(cost_basis_overrides, "match_and_stamp", stamp)
    _assert_reconciled(run("--skip-rename"), source_basis=80)
    _assert_reconciled(run("--refresh-prices"), source_basis=80)
    assert observations == [2.0, 2.0]


def test_split_receipt_without_market_or_receipt_price_preserves_value(
        transfer_pipeline, isolated_workdir, stub_prices):
    run, _ = transfer_pipeline
    source = isolated_workdir / "data" / "manual-adjustments.csv"
    rows = source.read_text(encoding="utf-8").splitlines()
    rows = [row for row in rows if ",Sell," not in row]
    rows = [row.replace(f"Transfer In,{SYMBOL},10,8,0",
                        f"Transfer In,{SYMBOL},10,0,0") for row in rows]
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    # Today's marks are beyond get_price's seven-day backward window.
    # The remaining quote is ten dollars per PRE-split share; current
    # custody quantities are in post-split units and must use five dollars.
    stub_prices.set(SYMBOL, {"2024-06-01": 5.0, "2024-06-10": 5.0})
    for mode in ("--skip-rename", "--refresh-prices"):
        payload = run(mode)
        assert payload["basis_totals"]["value"] == 100.0
        assert payload["history"][-1]["total"] == 100.0
        for row in payload["holdings_by_account"]:
            assert row["quantity"] == 10.0
            assert row["price"] == 5.0
            assert row["value"] == 50.0
        for method in payload["basis_methods"].values():
            assert method["totals"]["value"] == 100.0
        arrival = next(t for t in payload["transactions"]
                       if t["action"] == "Transfer In")
        assert arrival["account_transfer"]["flow"] == 50.0
