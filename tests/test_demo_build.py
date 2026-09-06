"""Public demo provenance, determinism and calendar isolation regressions."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_sample_exactly_matches_its_generator(tmp_path):
    from tools.build_sample_snapshot import build_snapshot, AS_OF_DATE
    output = tmp_path / "snapshot.json"
    build_snapshot(output)
    assert output.read_bytes() == (ROOT / "samples/portfolio.snapshot.json").read_bytes()
    assert json.loads(output.read_text())["exported_at"].startswith(AS_OF_DATE.isoformat())


def test_clock_uses_explicit_calendar_or_existing_test_seam(monkeypatch):
    from src import clock
    monkeypatch.delenv("FIN_AS_OF_DATE", raising=False)
    sentinel = datetime(2031, 2, 3, 4, 5)
    assert clock.now(fallback=lambda: sentinel) == sentinel
    monkeypatch.setenv("FIN_AS_OF_DATE", "2026-06-30")
    assert clock.today() == date(2026, 6, 30)
    assert clock.now(timezone.utc) == datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert clock.now(fallback=lambda: sentinel) == datetime(2026, 6, 30)
    monkeypatch.setenv("FIN_AS_OF_DATE", "2026-02-30")
    with pytest.raises(ValueError, match="FIN_AS_OF_DATE"):
        clock.today()


def test_public_build_is_isolated_repeatable_and_reconciled(tmp_path):
    """Hostile ambient FIN_* settings must never become public build inputs."""
    private = tmp_path / "private"
    private.mkdir()
    sentinel = private / "private.csv"
    sentinel.write_text("PRIVATE-SENTINEL-NEVER-PUBLISH\n")
    env = os.environ.copy()
    for name in ("FIN_PROJECT_ROOT", "FIN_DATA_DIR", "FIN_CACHE_DIR", "FIN_EXPORT_DIR"):
        env[name] = str(private)
    env["FIN_AS_OF_DATE"] = "2099-01-01"
    env["TZ"] = "Pacific/Honolulu"
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        subprocess.run([sys.executable, "-m", "tools.build_demo", "--output", str(output)],
                       cwd=ROOT, env=env, check=True, capture_output=True, timeout=60)
    assert sentinel.read_text() == "PRIVATE-SENTINEL-NEVER-PUBLISH\n"
    assert list(private.iterdir()) == [sentinel]
    for name in ("index.html", "provenance.json"):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()
    html = (outputs[0] / "index.html").read_text()
    assert "PRIVATE-SENTINEL" not in html
    manifest = json.loads((outputs[0] / "provenance.json").read_text())
    assert manifest["files"]["index.html"] == hashlib.sha256(html.encode()).hexdigest()
    data = json.loads(re.search(r"const DATA = (.*?);\n", html, re.S).group(1))
    assert data["demo"]["synthetic"] is True
    assert data["snapshot_date"] == "2026-06-30"
    assert not data["analytics"]["data_health"]
    reconciliation = data["analytics"]["reconciliation"]["summary"]
    assert reconciliation == {"explained": 1, "off": 0, "ok": 5, "total": 6, "warn": 0}
    assert 'id="demo-intro"' in html
    assert "Reload demo" in html
    assert "og:description" in html
    assert 'property="og:image" content="https://raw.githubusercontent.com/akourk/finledger/main/docs/img/dashboard.png"' in html
    assert 'property="og:image:width" content="1440"' in html
    assert 'property="og:image:height" content="1100"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html


def test_positive_broker_wallet_is_valid_and_negative_cash_is_reported():
    from src.analytics.data_health import _check_broker_cash_balance
    deposit = {"date": "2026-01-01", "account_group": "Coinbase", "symbol": "USD", "action": "Deposit", "amount": 500}
    withdrawal = {**deposit, "date": "2026-01-02", "action": "Withdrawal", "amount": 600}
    assert not _check_broker_cash_balance([deposit])
    issues = _check_broker_cash_balance([deposit, withdrawal])
    assert issues[0]["kind"] == "negative_broker_cash"
    assert issues[0]["count"] == 1
