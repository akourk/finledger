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
    for asset in ("template.html", "styles.css"):
        assert (pkg / asset).exists(), f"missing asset: {asset}"
    # The JS now lives as ordered modules under app/, concatenated by
    # _read_app_js().  At least the core module must be present.
    js_modules = sorted((pkg / "app").glob("*.js"))
    assert js_modules, "no app/*.js modules found"
    assert (pkg / "app" / "00-core.js").exists()


def test_template_has_required_markers():
    """Three placeholders must remain in the HTML template:
    @@STYLES@@, @@APP_JS@@, and __JSON_DATA__ (the last one lives in the
    concatenated app/ JS, but template can't accidentally claim it).
    """
    from src import dashboard
    pkg = Path(dashboard.__file__).parent
    template = (pkg / "template.html").read_text(encoding="utf-8")
    app_js   = dashboard._read_app_js()
    assert "/* @@STYLES@@ */" in template
    assert "// @@APP_JS@@" in template
    assert "__JSON_DATA__" in app_js


def test_app_modules_concatenate_in_order():
    """The split app/ modules must join into one complete program — spot
    check that key definitions from across the modules are all present and
    the JSON placeholder appears exactly once."""
    from src import dashboard
    app_js = dashboard._read_app_js()
    for needed in ("registerTabRenderer", "renderHoldings", "renderHistory",
                   "renderOverviewStatus", "renderPerformance", "renderTable"):
        assert needed in app_js, f"missing {needed} — a module dropped out"
    assert app_js.count("__JSON_DATA__") == 1


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


def test_board_module_is_bundled_and_reads_precomputed_figures():
    """The positions board must ship in the bundle AND source its
    numbers from `analytics.position_pnl`.

    The board's whole claim is that it re-arranges one dataset rather
    than deriving a second one; a renderer that recomputed a window
    would be the exact failure this codebase keeps re-learning (the JS
    quietly disagreeing with the Python).  This pins the wiring: the
    module is concatenated, it reads the precomputed key, and the
    template gives it both a container and a way in.
    """
    from src.dashboard import _read_app_js

    js = _read_app_js()
    assert "ANALYTICS.position_pnl" in js, (
        "the board must read the precomputed payload")
    assert "renderPositionsBoard" in js

    tpl = (Path("src/dashboard/template.html")).read_text(encoding="utf-8")
    assert 'id="boardBody"' in tpl, "board needs a render target"
    assert "setBoardLayout('board')" in tpl, "board needs a way in"


def test_holdings_table_reads_precomputed_returns():
    """The Holdings table's TWR / XIRR columns must come from
    `analytics.performance_by_filter`, matched by the SET of account
    groups a grouped row covers.

    Deriving a return in JS is the failure this codebase keeps
    re-learning, and a return is especially easy to get wrong: each
    filter's window is its own (a 401K opened years into the ledger
    spans nothing like a taxable account), so a locally-computed figure
    would silently measure the wrong period.  Matching on the exported
    `filter_groups` set also means a row no filter covers renders
    nothing rather than a number measured over some other group.
    """
    from src.dashboard import _read_app_js
    js = _read_app_js()
    assert "ANALYTICS_PERF" in js
    assert "PERF_BY_GROUPSET" in js, (
        "grouped rows must match a precomputed filter by account-group set")
    assert "filter_groups" in js, "the match key is the exported set"
    # The span travels with the figure — a return next to no window is
    # the thing AUDIT.md F-031 was about.
    assert "_perfSpan" in js


def test_board_windows_are_independent_toggles():
    """Window chips select any COMBINATION, each contributing its own
    sortable column pair — so there is no separate "all windows" mode
    whose columns cannot be sorted."""
    from src.dashboard import _read_app_js
    js = _read_app_js()
    assert "toggleBoardPaneWindow" in js
    assert "boardValidSort" in js, (
        "switching a window off must not leave a pane sorting by a column "
        "it no longer renders")
    assert "toggleBoardAllWindows" not in js, (
        "the all-windows mode was replaced by independent chips")


def test_js_account_classes_come_from_the_exported_filter_sets():
    """The Performance tab's combined-filter chips must resolve their
    account sets from `analytics.performance_by_filter[*].filter_groups`,
    not from JS literals.

    A literal here is a third copy of a classification the user declares
    in metadata.csv, and it had already drifted: the "Taxable" chip
    resolved to a metadata-derived set while the figure beneath it came
    from Python's differently-composed `Taxable` entry, and
    "Investments" subtracted a hardcoded savings set that missed any
    savings account not literally named "Apple Savings" — defeating the
    one thing that filter exists to do.  Same failure class as F-038.
    """
    from src.dashboard import _read_app_js
    js = _read_app_js()
    assert "_filterGroupSet" in js, (
        "combined-filter sets must be read from the export")
    # The specific literals that drifted.  A fallback derived from
    # ACCOUNT_TYPE_OF is fine; naming accounts in code is not.
    assert "new Set(['Apple Savings'])" not in js
    assert "new Set(['401K', 'Roth IRA', 'Rollover IRA'])" not in js
    assert "_groupsOfType" in js, "the fallback must derive from account types"


def test_python_account_classes_are_not_hardcoded_literals():
    """Membership is the user's `Account Type` metadata unioned with a
    fallback, read at CALL time — `config.ACCOUNT_TYPES` is empty until
    main() applies the parsed metadata, so a module-level snapshot would
    always be the bare fallback."""
    src = Path("src/analytics/_shared.py").read_text(encoding="utf-8")
    assert "def savings_groups()" in src
    assert "def retirement_groups()" in src
    # The filter builder is the site that actually decides the classes.
    assert "savings_groups()" in src and "retirement_groups()" in src
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("investments =") or stripped.startswith("taxable ="):
            assert "SAVINGS_GROUPS" not in stripped, (
                "the filter builder must use the live classification, "
                "not the frozen default")
