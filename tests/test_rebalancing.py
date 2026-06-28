"""Tests for Target Allocation parsing + rebalancing-drift analytics."""

from __future__ import annotations


def test_metadata_parses_target_allocation(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Target Allocation,,70,Technology,core\n"
        "Target Allocation,,0.30,Cash,dry powder\n",   # accepts decimal too
        encoding="utf-8",
    )
    meta = parse_metadata(tmp_path)
    ta = {t["bucket"]: t["pct"] for t in meta["target_allocation"]}
    assert ta == {"Technology": 70.0, "Cash": 30.0}


def test_rebalancing_drift_and_actions():
    from src.analytics.rebalancing import compute_rebalancing
    holdings = [
        {"symbol": "AAPL", "sector": "Technology", "value": 6000},
        {"symbol": "BTC-USD", "sector": "Cryptocurrency", "value": 2000},
        {"symbol": "USD", "sector": "Cash", "value": 2000},
    ]
    targets = [
        {"bucket": "Technology", "pct": 50},
        {"bucket": "Cryptocurrency", "pct": 30},
        {"bucket": "Cash", "pct": 20},
    ]
    rb = compute_rebalancing(holdings, targets)
    rows = {r["bucket"]: r for r in rb["rows"]}
    assert rows["Technology"]["current_pct"] == 60.0
    assert rows["Technology"]["drift_pct"] == 10.0       # over target
    assert rows["Technology"]["action_value"] == -1000.0  # sell $1000
    assert rows["Cryptocurrency"]["action_value"] == 1000.0  # buy $1000
    assert rb["total_target_pct"] == 100.0
    assert rb["max_abs_drift"] == 10.0


def test_rebalancing_none_without_targets():
    from src.analytics.rebalancing import compute_rebalancing
    holdings = [{"symbol": "AAPL", "sector": "Technology", "value": 100}]
    assert compute_rebalancing(holdings, None) is None
    assert compute_rebalancing(holdings, []) is None


def test_rebalancing_untargeted_bucket_reported():
    from src.analytics.rebalancing import compute_rebalancing
    holdings = [
        {"symbol": "AAPL", "sector": "Technology", "value": 5000},
        {"symbol": "XOM", "sector": "Energy", "value": 5000},  # no target
    ]
    rb = compute_rebalancing(holdings, [{"bucket": "Technology", "pct": 100}])
    assert rb["untargeted_value"] == 5000.0
    assert rb["untargeted_pct"] == 50.0
