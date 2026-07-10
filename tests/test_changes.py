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

ROOT = Path(__file__).resolve().parent.parent


def _mk_holdings(value: float) -> list[dict]:
    return [{"account_group": "Robinhood", "symbol": "VOO",
             "quantity": 1.0, "value": value}]


def _txn(date: str) -> dict:
    return {"date": date, "symbol": "VOO", "action": "Buy"}


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


def test_first_run_saves_snapshot(tmp_path):
    from src.analytics.changes import compute_changes
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(100.0), [],
                          {}, {}, tmp_path)
    assert out["first_run"] is True
    saved = json.loads((tmp_path / "last_run.json").read_text())
    assert saved["value"] == 100.0
    assert saved["txn_count"] == 1


def test_second_run_diffs_against_first(tmp_path):
    from src.analytics.changes import compute_changes
    compute_changes([_txn("2026-01-02")], _mk_holdings(100.0), [],
                    {}, {}, tmp_path)
    out = compute_changes([_txn("2026-01-02"), _txn("2026-01-03")],
                          _mk_holdings(150.0), [], {}, {}, tmp_path)
    assert out["first_run"] is False
    assert out["value_delta"] == 50.0
    assert out["txn_count_delta"] == 1


def test_empty_run_does_not_clobber_snapshot(tmp_path):
    """An all-empty call must leave the previous snapshot intact."""
    from src.analytics.changes import compute_changes
    compute_changes([_txn("2026-01-02")], _mk_holdings(100.0), [],
                    {}, {}, tmp_path)
    before = (tmp_path / "last_run.json").read_text()

    out = compute_changes([], [], [], {}, {}, tmp_path)
    assert out.get("skipped_empty_run") is True
    assert out["first_run"] is True  # hides the panel — nothing to say

    assert (tmp_path / "last_run.json").read_text() == before

    # The NEXT real run still diffs against the good snapshot.
    out = compute_changes([_txn("2026-01-02")], _mk_holdings(120.0), [],
                          {}, {}, tmp_path)
    assert out["first_run"] is False
    assert out["value_delta"] == 20.0


def test_build_analytics_with_bare_inputs_leaves_no_snapshot(tmp_path,
                                                             monkeypatch):
    """The original leak: build_analytics([], history, [], []) wrote a
    zeroed last_run.json into whatever CACHE_DIR resolved to."""
    monkeypatch.setenv("FIN_CACHE_DIR", str(tmp_path))
    # Re-import so config picks up the redirected cache dir.
    import sys
    for m in [m for m in list(sys.modules)
              if m == "src" or m.startswith("src.")]:
        del sys.modules[m]
    from src.analytics import build_analytics

    history = [{"date": "2026-06-30", "total": 0.0, "by_account_group": {},
                "by_account_type": {}, "by_sector": {}, "net_contributed": 0.0,
                "priced_pct": 1.0, "positions": [], "total_cost_basis": 0.0}]
    out = build_analytics([], history, [], [], retirement_meta={})
    assert out["changes"].get("skipped_empty_run") is True
    assert not (tmp_path / "last_run.json").exists()


def test_new_txn_on_already_seen_date_is_counted(tmp_path):
    """A new txn landing on a date that already had activity must show
    up in new_txns_by_date — the old set-membership diff hid it."""
    from src.analytics.changes import compute_changes
    compute_changes([_txn("2026-01-02")], _mk_holdings(100.0), [],
                    {}, {}, tmp_path)
    out = compute_changes(
        [_txn("2026-01-02"), _txn("2026-01-02"), _txn("2026-01-03")],
        _mk_holdings(120.0), [], {}, {}, tmp_path)
    by_date = {r["date"]: r["count"] for r in out["new_txns_by_date"]}
    assert by_date == {"2026-01-02": 1, "2026-01-03": 1}
    assert out["txn_count_delta"] == 2
