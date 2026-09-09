"""Run-over-run diff (analytics/changes.py) + test-isolation guard.

The "What's Changed" panel diffs each run against cache/last_run.json.
Two failure modes are pinned here:

1. An empty run (no txns, no value) must never overwrite a previous
   snapshot — it would make the next real run diff against zeros and
   report the entire portfolio value as "new".
2. The test suite itself must never resolve fin's real repo dirs —
   that is exactly how the user's real cache/last_run.json got
   clobbered (tests calling build_analytics() without the
   isolated_workdir fixture).  conftest.py's import-time env guard
   pins every FIN_*_DIR at a session tmp dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from copy import deepcopy

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _mk_holdings(value: float) -> list[dict]:
    return [{"account_group": "Robinhood", "symbol": "VOO",
             "quantity": 1.0, "value": value}]


def _txn(date: str) -> dict:
    return {"date": date, "symbol": "VOO", "action": "Buy"}


def _totals(basis=0.0, realized=0.0):
    return {"method": "annotated", "cost_basis": basis, "realized_gain": realized}


def _publish_current(directory, changes):
    """Simulate a successful publisher in tests; computation cannot write."""
    from src.analytics.changes import SNAPSHOT_FILE
    (directory / SNAPSHOT_FILE).write_text(
        json.dumps(changes["current"], allow_nan=False), encoding="utf-8")


def test_env_guard_never_points_at_repo_dirs():
    """No test — fixture-using or not — may resolve the real repo dirs."""
    for var, sub in (("FIN_DATA_DIR", "data"), ("FIN_CACHE_DIR", "cache"),
                     ("FIN_EXPORT_DIR", "exports")):
        val = os.environ.get(var)
        assert val, f"{var} not set — conftest guard missing"
        assert Path(val).resolve() != (ROOT / sub).resolve(), (
            f"{var} points at the real repo {sub}/ dir — tests would "
            f"read/write the user's actual files"
        )


def test_first_run_prepares_snapshot_without_creating_directory(tmp_path):
    from src.analytics.changes import compute_changes
    directory = tmp_path / "not-created"
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(100.0),
                          _totals(), directory)
    assert out["first_run"] is True
    assert out["current"]["value"] == 100.0
    assert out["current"]["txn_count"] == 1
    assert out["current"]["schema_version"] == 2
    assert out["current"]["basis_method"] == "annotated"
    assert not directory.exists()


def test_second_run_diffs_against_first(tmp_path):
    from src.analytics.changes import compute_changes
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")],
                     _mk_holdings(100.0), _totals(), tmp_path))
    out = compute_changes([_txn("2026-01-02"), _txn("2026-01-03")],
                          _mk_holdings(150.0), _totals(), tmp_path)
    assert out["first_run"] is False
    assert out["value_delta"] == 50.0
    assert out["txn_count_delta"] == 1


def test_empty_run_does_not_clobber_snapshot(tmp_path):
    """An all-empty call must leave the previous snapshot intact."""
    from src.analytics.changes import compute_changes
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")],
                     _mk_holdings(100.0), _totals(), tmp_path))
    before = (tmp_path / "last_run.json").read_text()

    out = compute_changes([], [], _totals(), tmp_path)
    assert out.get("skipped_empty_run") is True
    assert out["first_run"] is True  # hides the panel — nothing to say
    assert "current" not in out

    assert (tmp_path / "last_run.json").read_text() == before

    # The NEXT real run still diffs against the good snapshot.
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(120.0),
                          _totals(), tmp_path)
    assert out["first_run"] is False
    assert out["value_delta"] == 20.0


def test_build_analytics_with_bare_inputs_leaves_no_snapshot(isolated_workdir,
                                                             stub_prices):
    """The original leak: build_analytics([], history, [], []) wrote a
    zeroed last_run.json into whatever CACHE_DIR resolved to."""
    from src.analytics import build_analytics

    history = [{"date": "2026-06-30", "total": 0.0, "by_account_group": {},
                "by_account_type": {}, "by_sector": {}, "net_contributed": 0.0,
                "priced_pct": 1.0, "positions": [], "total_cost_basis": 0.0}]
    out = build_analytics([], history, [], [], retirement_meta={})
    assert out["changes"].get("skipped_empty_run") is True
    assert not (isolated_workdir / "cache" / "last_run.json").exists()


def test_new_txn_on_already_seen_date_is_counted(tmp_path):
    """A new txn landing on a date that already had activity must show
    up in new_txns_by_date — the old set-membership diff hid it."""
    from src.analytics.changes import compute_changes
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")],
                     _mk_holdings(100.0), _totals(), tmp_path))
    out = compute_changes(
        [_txn("2026-01-02"), _txn("2026-01-02"), _txn("2026-01-03")],
        _mk_holdings(120.0), _totals(), tmp_path)
    by_date = {r["date"]: r["count"] for r in out["new_txns_by_date"]}
    assert by_date == {"2026-01-02": 1, "2026-01-03": 1}
    assert out["txn_count_delta"] == 2


def test_nonempty_computation_leaves_baseline_and_inputs_unchanged(tmp_path,
                                                                monkeypatch):
    from src.analytics.changes import compute_changes

    monkeypatch.setenv("FIN_AS_OF_DATE", "2026-01-03")
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")],
                     _mk_holdings(100), _totals(80, 10), tmp_path))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    inputs = ([_txn("2026-01-02"), _txn("2026-01-03")],
              _mk_holdings(125), _totals(90, 15))
    original = deepcopy(inputs)
    first = compute_changes(*inputs, tmp_path)
    second = compute_changes(*inputs, tmp_path)
    assert first == second
    assert inputs == original
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert first["current"]["value"] == 125
    assert first["basis_comparison_available"] is True
    assert first["basis_delta"] == 10
    assert first["realized_delta"] == 5


def _trade(day, action, qty, price):
    return {"date": day, "account": "Example Broker",
            "account_group": "Example Broker", "account_type": "Taxable",
            "symbol": "FICT", "action": action, "quantity": qty,
            "price": price, "amount": qty * price, "fees": 0}


def _annotated_inputs(txns, *, method="hifo", price=40):
    from src.basis import compute_basis_default, state_to_holdings
    from src.config import ACCOUNT_TYPES
    from src.pipeline_stages import build_annotated_basis_totals

    ACCOUNT_TYPES["Example Broker"] = "Taxable"
    rows = deepcopy(txns)
    state = compute_basis_default(rows, account_methods={"Example Broker": method})
    holdings = state_to_holdings(state, "fifo")
    for holding in holdings:
        holding["value"] = holding["quantity"] * price
        holding["unrealized_gain"] = holding["value"] - holding["cost_basis"]
    return rows, holdings, build_annotated_basis_totals(holdings, state)


def test_changes_follow_actual_hifo_instead_of_fifo(isolated_workdir):
    from src.analytics.changes import compute_changes

    cache = isolated_workdir / "cache"
    purchases = [_trade("2024-01-02", "Buy", 1, 10),
                 _trade("2024-02-02", "Buy", 1, 30)]
    _publish_current(cache, compute_changes(*_annotated_inputs(purchases), cache))
    after_sale = [*purchases, _trade("2024-03-02", "Sell", 1, 40)]
    actual = _annotated_inputs(after_sale)
    fifo = _annotated_inputs(after_sale, method="fifo")
    assert actual[2]["realized_gain"] == 10
    assert fifo[2]["realized_gain"] == 30  # The fixture exercises the method choice.
    changes = compute_changes(*actual, cache)
    assert changes["basis_delta"] == -30
    assert changes["realized_delta"] == 10
    assert changes["current"]["cost_basis"] == 10


def test_realized_delta_uses_accumulator_not_rounded_annotations(isolated_workdir):
    from src.analytics.changes import compute_changes

    cache = isolated_workdir / "cache"
    purchases = [_trade("2024-01-02", "Buy", 10, 1)]
    _publish_current(cache, compute_changes(*_annotated_inputs(purchases), cache))
    sales = [_trade(f"2024-02-{day:02}", "Sell", 1, 1.003)
             for day in range(1, 11)]
    actual = _annotated_inputs([*purchases, *sales])
    assert sum(t.get("realized_gain", 0) for t in actual[0]) == 0
    assert actual[2]["realized_gain"] == .03
    assert compute_changes(*actual, cache)["realized_delta"] == .03


def test_savings_interest_does_not_increase_principal_basis(isolated_workdir):
    from src.analytics.changes import compute_changes
    from src.config import ACCOUNT_TYPES
    from src.pipeline_stages import (build_annotated_basis_totals, build_holdings,
                                     compute_cash_principal, walk_balances)

    cache = isolated_workdir / "cache"
    group = "Example Savings"
    ACCOUNT_TYPES[group] = "Savings"
    deposit = {"date": "2024-01-02", "account": group, "account_group": group,
               "account_type": "Savings", "symbol": "USD", "action": "Deposit",
               "quantity": 100, "price": 1, "amount": 100}
    interest = dict(deposit, date="2024-02-02", action="Interest", quantity=5, amount=5)

    def inputs(rows):
        _, balances = walk_balances(rows)
        holdings, by_account = build_holdings(
            balances, {"USD": 1}, {}, compute_cash_principal(rows))
        return rows, by_account, build_annotated_basis_totals(
            holdings, {"realized_total": 0})

    _publish_current(cache, compute_changes(*inputs([deposit]), cache))
    out = compute_changes(*inputs([deposit, interest]), cache)
    assert out["value_delta"] == 5
    assert out["basis_delta"] == 0
    assert out["current"]["cost_basis"] == 100


def test_legacy_baseline_retains_activity_but_suppresses_basis_delta(tmp_path):
    from src.analytics.changes import compute_changes

    legacy = compute_changes([_txn("2026-01-02")], _mk_holdings(100),
                             _totals(90, 25), tmp_path)["current"]
    legacy.pop("schema_version")
    legacy.pop("basis_method")
    (tmp_path / "last_run.json").write_text(json.dumps(legacy), encoding="utf-8")
    out = compute_changes([_txn("2026-01-02"), _txn("2026-01-03")],
                          _mk_holdings(120), _totals(70, 15), tmp_path)
    assert out["first_run"] is False
    assert out["basis_comparison_available"] is False
    assert out["value_delta"] == 20
    assert out["txn_count_delta"] == 1
    assert out["top_gainers"][0]["delta"] == 20
    assert out["basis_delta"] is out["realized_delta"] is None
    assert out["current"]["cost_basis"] == 70
    # After successful publication, the next run compares annotated totals.
    _publish_current(tmp_path, out)
    next_run = compute_changes([_txn("2026-01-02"), _txn("2026-01-03")],
                               _mk_holdings(125), _totals(75, 17), tmp_path)
    assert next_run["basis_comparison_available"] is True
    assert next_run["basis_delta"] == 5
    assert next_run["realized_delta"] == 2


@pytest.mark.parametrize("missing", ["previous", "current"])
def test_missing_realized_total_never_becomes_zero(tmp_path, missing):
    from src.analytics.changes import compute_changes

    previous = _totals(80, None if missing == "previous" else 10)
    current = _totals(90, None if missing == "current" else 15)
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")],
                     _mk_holdings(100), previous, tmp_path))
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(110), current, tmp_path)
    assert out["first_run"] is False
    assert out["value_delta"] == 10
    assert out["basis_comparison_available"] is False
    assert out["basis_delta"] is out["realized_delta"] is None
    assert out["current"]["realized_gain"] == current["realized_gain"]


@pytest.mark.parametrize("bad", [
    None, [], 4, "text", {},
    {"txn_count": True}, {"txn_count": -1}, {"txn_count": 1.5},
    {"value": float("nan")}, {"value": float("inf")}, {"value": True},
    {"value": 10 ** 400}, {"value_by_symbol": []},
    {"value_by_symbol": {"VOO": "100"}},
    {"value_by_symbol": {"VOO": float("inf")}},
    {"txn_dates": "2026-01-02"}, {"txn_dates": [{}]},
    {"txn_dates": ["2026-01-02", "2026-01-03"]},
    {"generated": []}, {"generated": ""},
    {"cost_basis": "90"}, {"realized_gain": float("nan")},
    {"basis_method": []}, {"schema_version": 3}, {"schema_version": True},
], ids=[
    "null", "array", "number", "string", "empty-object",
    "boolean-count", "negative-count", "fractional-count",
    "nan-value", "infinite-value", "boolean-value", "overflow-value",
    "array-values", "string-position-value", "infinite-position-value",
    "string-dates", "object-date", "excess-dates", "array-generated", "empty-generated",
    "string-basis", "nan-realized", "array-method", "future-version", "boolean-version",
])
def test_malformed_baselines_are_unavailable_without_writes(tmp_path, bad):
    from src.analytics.changes import compute_changes

    valid = compute_changes([_txn("2026-01-02")], _mk_holdings(100),
                            _totals(90, 5), tmp_path)["current"]
    invalid = {**valid, **bad} if isinstance(bad, dict) and bad else bad
    path = tmp_path / "last_run.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    before = path.read_bytes()
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(120),
                          _totals(80, 15), tmp_path)
    assert out["first_run"] is True
    assert out["basis_comparison_available"] is False
    assert out["current"]["value"] == 120
    assert path.read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("raw", [b'{"incomplete":', b'\xff'])
def test_unreadable_baseline_text_is_preserved(tmp_path, raw):
    from src.analytics.changes import compute_changes

    path = tmp_path / "last_run.json"
    path.write_bytes(raw)
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(120), _totals(), tmp_path)
    assert out["first_run"] is True
    assert path.read_bytes() == raw


def test_tied_movers_have_deterministic_symbol_order(tmp_path):
    from src.analytics.changes import compute_changes

    old = [{"symbol": symbol, "value": 100} for symbol in ["B", "D", "A", "C"]]
    new = [{"symbol": symbol, "value": value}
           for symbol, value in [("B", 110), ("D", 90), ("A", 110), ("C", 90)]]
    _publish_current(tmp_path, compute_changes([_txn("2026-01-02")], old,
                                              _totals(), tmp_path))
    out = compute_changes([_txn("2026-01-02")], new, _totals(), tmp_path)
    assert [r["symbol"] for r in out["top_gainers"]] == ["A", "B"]
    assert [r["symbol"] for r in out["top_losers"]] == ["C", "D"]
