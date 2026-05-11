"""Dashboard asset bundler tests — guards the template/CSS/JS split.

Catches accidental regressions like a missing placeholder marker or
asset file that would silently produce a broken HTML output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_required_asset_files_exist():
    from src import dashboard
    pkg = Path(dashboard.__file__).parent
    for asset in ("template.html", "styles.css", "app.js"):
        assert (pkg / asset).exists(), f"missing asset: {asset}"


def test_template_has_required_markers():
    """Three placeholders must remain in the HTML template:
    @@STYLES@@, @@APP_JS@@, and __JSON_DATA__ (the last one lives in
    app.js, but template can't accidentally claim it).
    """
    from src import dashboard
    pkg = Path(dashboard.__file__).parent
    template = (pkg / "template.html").read_text(encoding="utf-8")
    app_js   = (pkg / "app.js").read_text(encoding="utf-8")
    assert "/* @@STYLES@@ */" in template
    assert "// @@APP_JS@@" in template
    assert "__JSON_DATA__" in app_js


def test_generate_dashboard_produces_runnable_html(tmp_path):
    """End-to-end: synthetic JSON in, full HTML out, with the JSON
    payload, CSS, and JS all inlined."""
    from src.dashboard import generate_dashboard

    json_path = tmp_path / "txns.json"
    out_path  = tmp_path / "dashboard.html"
    json_path.write_text(json.dumps({
        "generated": "2026-01-01T00:00:00",
        "count": 0,
        "holdings": [],
        "holdings_by_account": [],
        "history": [],
        "basis_methods": {"fifo": {"totals": {"value": 0, "cost_basis": 0,
                                              "realized_gain": 0,
                                              "unrealized_gain": 0},
                                   "totals_by_type": {}, "holdings": []}},
        "cash_summary": {"net_contributed": 0, "income": 0},
        "retirement_meta": {},
        "analytics": {},
        "sector_of": {},
        "display_of": {},
        "action_catalog": {"actions": []},
        "transactions": [],
    }))

    generate_dashboard(json_path, out_path)
    out = out_path.read_text(encoding="utf-8")

    # Markers must all be substituted
    assert "/* @@STYLES@@ */" not in out
    assert "// @@APP_JS@@" not in out
    assert "__JSON_DATA__" not in out

    # Standard HTML envelope is intact
    assert out.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in out
    assert "<style>" in out
    assert "<script>" in out


def test_missing_marker_raises(tmp_path, monkeypatch):
    """If someone deletes a placeholder from template.html, the
    bundler must fail loudly rather than producing a half-built
    HTML."""
    from src.dashboard import generate_dashboard
    from src import dashboard

    pkg = Path(dashboard.__file__).parent
    bad_template = (pkg / "template.html").read_text(encoding="utf-8") \
        .replace("/* @@STYLES@@ */", "/* (oops, deleted) */")

    # Patch _read_asset to return our broken template
    def fake_read(name):
        if name == "template.html":
            return bad_template
        return (pkg / name).read_text(encoding="utf-8")
    monkeypatch.setattr(dashboard, "_read_asset", fake_read)

    json_path = tmp_path / "txns.json"
    json_path.write_text("{}")
    with pytest.raises(RuntimeError, match="missing marker"):
        generate_dashboard(json_path, tmp_path / "out.html")
