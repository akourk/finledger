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


class _WindowsDefaultsPath(type(Path())):
    """Exercise Windows path ordering and default newlines on any host."""

    def __lt__(self, other):
        return str(self).casefold() < str(other).casefold()

    def write_text(self, data, encoding=None, errors=None, newline=None):
        return super().write_text(data, encoding=encoding, errors=errors,
                                  newline="\r\n" if newline is None else newline)


@pytest.mark.parametrize("path_type", [Path, _WindowsDefaultsPath])
def test_sample_filename_order_and_lf_are_platform_independent(tmp_path, monkeypatch, path_type):
    from tools import build_sample_snapshot as builder

    def small_fixture(directory):
        directory.mkdir()
        for name in ("alpha.csv", "Zeta.csv"):
            (directory / name).write_bytes(b"Description\r\nFictional fixture\r\n")

    monkeypatch.setattr(builder, "Path", path_type)
    monkeypatch.setattr(builder, "_build", small_fixture)
    output = path_type(tmp_path / "snapshot.json")
    builder.build_snapshot(output)
    raw = output.read_bytes()
    bundle = json.loads(raw.decode("utf-8"))
    assert list(bundle["files"]) == ["Zeta.csv", "alpha.csv"]
    assert bundle["files"]["alpha.csv"] == "Description\nFictional fixture\n"
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_demo_banner_cli_writes_utf8_lf_with_windows_defaults(tmp_path, monkeypatch):
    from tools import demo_banner

    source, output = tmp_path / "source.html", tmp_path / "index.html"
    source.write_bytes(b'<html><head><title>Example</title></head>\r\n'
                       b'<body><a href="#main-content">Skip to dashboard content</a>\r\n'
                       b'</body></html>\r\n')
    monkeypatch.setattr(demo_banner, "Path", _WindowsDefaultsPath)
    assert demo_banner.main(["demo_banner", str(source), str(output)]) == 0
    raw = output.read_bytes()
    assert b"\r" not in raw
    assert "finledger — Interactive financial ledger demo" in raw.decode("utf-8")


def test_sample_exactly_matches_its_generator(tmp_path):
    from tools.build_sample_snapshot import build_snapshot, AS_OF_DATE
    output = tmp_path / "snapshot.json"
    build_snapshot(output)
    assert output.read_bytes() == (ROOT / "samples/portfolio.snapshot.json").read_bytes()
    assert json.loads(output.read_text())["exported_at"].startswith(AS_OF_DATE.isoformat())


def test_demo_bytes_match_under_windows_and_posix_path_ordering(tmp_path):
    """Mixed-case fixture filenames must not reorder transactions or analytics."""
    worker = """
import pathlib
import sys
sort_key = str if sys.argv[2] == "case-sensitive" else lambda p: str(p).casefold()
type(pathlib.Path()).__lt__ = lambda self, other: sort_key(self) < sort_key(other)
from tools.build_demo import _worker
_worker(pathlib.Path(sys.argv[1]))
"""
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "TZ": "UTC"})
    outputs = [tmp_path / order for order in ("case-sensitive", "case-insensitive")]
    for output in outputs:
        output.mkdir()
        subprocess.run([sys.executable, "-c", worker, str(output), output.name],
                       cwd=ROOT, env=env, check=True, capture_output=True, timeout=60)
    for name in ("index.html", "provenance.json"):
        assert (outputs[0] / "site" / name).read_bytes() == (outputs[1] / "site" / name).read_bytes()


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
        raw = (outputs[0] / name).read_bytes()
        assert raw == (outputs[1] / name).read_bytes()
        assert b"\r" not in raw
    html = (outputs[0] / "index.html").read_text(encoding="utf-8")
    assert "PRIVATE-SENTINEL" not in html
    manifest = json.loads((outputs[0] / "provenance.json").read_text(encoding="utf-8"))
    assert manifest["files"]["index.html"] == hashlib.sha256((outputs[0] / "index.html").read_bytes()).hexdigest()
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
