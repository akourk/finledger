"""The JS tax-table fallbacks must agree with the Python tables.

`analytics/tax.py` is the single source of truth: `tax_tables_to_json()`
serializes the tables into the export and the dashboard reads
`DATA.tax_tables`. The literals in `app/80-tax.js` are an emergency
fallback for when that key is missing.

An emergency fallback is exactly the kind of thing that rots unnoticed,
and this one had: when OBBBA (July 2025) retroactively raised the 2025
standard deduction, `tax.py` was updated and the JS literal was not. The
fallback carried superseded figures for all four filing statuses —
wrong by $750–$1,500 each, which feeds the marginal-rate estimate and
the whole Tax tab. Nothing noticed, because the fallback only runs when
something else has already gone wrong.

This module compares the two for every year they BOTH define. The JS is
allowed to lag by whole years on purpose — CLAUDE.md and the
`fin-tax-year-update` skill both say a new tax year is a one-file change
in `analytics/tax.py` — so years present only in Python are skipped.
What is not allowed is disagreeing about a year they both claim to know.

Adding a year to `tax.py` therefore needs no change here. Editing a year
the JS also carries does.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

JS_PATH = Path(__file__).resolve().parent.parent / "src" / "dashboard" / "app" / "80-tax.js"


def _js_source() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def _js_bracket_fallback(varname: str) -> dict:
    """Parse the `{year: {status: [[threshold, rate], ...]}}` literal."""
    js = _js_source()
    start = js.index(f"const {varname}")
    seg = js[start:js.index("\n};", start)]
    out: dict[int, dict[str, list]] = {}
    year = None
    for line in seg.splitlines():
        m = re.match(r"\s*(\d{4}):\s*\{", line)
        if m:
            year = int(m.group(1))
            out[year] = {}
            continue
        m = re.match(r"\s*'([^']+)':\s*\[(.*)\],\s*$", line)
        if m and year is not None:
            pairs = re.findall(r"\[([\d.]+|Infinity),\s*([\d.]+)\]", m.group(2))
            out[year][m.group(1)] = [
                (math.inf if a == "Infinity" else float(a), float(b))
                for a, b in pairs
            ]
    return out


def _js_std_deduction_fallback() -> dict:
    js = _js_source()
    start = js.index("const STD_DEDUCTION")
    seg = js[start:js.index("\n};", start)]
    out: dict[int, dict[str, float]] = {}
    for line in seg.splitlines():
        m = re.match(r"\s*(\d{4}):\s*\{(.*)\},?\s*$", line)
        if m:
            out[int(m.group(1))] = {
                k: float(v)
                for k, v in re.findall(r"'([^']+)':\s*([\d.]+)", m.group(2))
            }
    return out


@pytest.fixture
def py_tables(isolated_workdir):
    from src.analytics.tax import (_BRACKETS_BY_YEAR_STATUS,
                                   _LTCG_BY_YEAR_STATUS,
                                   _STD_DED_BY_YEAR_STATUS)
    return (_BRACKETS_BY_YEAR_STATUS, _LTCG_BY_YEAR_STATUS,
            _STD_DED_BY_YEAR_STATUS)


class TestParserSanity:
    """If the regexes stop matching, every comparison below passes
    vacuously. Assert they found something first."""

    def test_bracket_fallbacks_parse(self):
        for var in ("FEDERAL_BRACKETS", "LTCG_BRACKETS"):
            tbl = _js_bracket_fallback(var)
            assert tbl, f"parsed no fallback years from {var}"
            for year, statuses in tbl.items():
                assert statuses, f"{var} {year} parsed no statuses"
                for status, rows in statuses.items():
                    assert rows, f"{var} {year} {status} parsed no brackets"

    def test_std_deduction_fallback_parses(self):
        tbl = _js_std_deduction_fallback()
        assert tbl
        assert all(v for v in tbl.values())


class TestPythonJsAgreement:

    def test_federal_brackets_match_for_shared_years(self, py_tables):
        py, _ltcg, _std = py_tables
        js = _js_bracket_fallback("FEDERAL_BRACKETS")
        mismatches = []
        for (year, status), rows in sorted(py.items()):
            if year not in js or status not in js[year]:
                continue          # JS lags by whole years on purpose
            if [tuple(r) for r in rows] != js[year][status]:
                mismatches.append(f"{year} {status}")
        assert not mismatches, (
            f"JS federal-bracket fallback disagrees with tax.py: {mismatches}"
        )

    def test_ltcg_brackets_match_for_shared_years(self, py_tables):
        _fed, py, _std = py_tables
        js = _js_bracket_fallback("LTCG_BRACKETS")
        mismatches = []
        for (year, status), rows in sorted(py.items()):
            if year not in js or status not in js[year]:
                continue
            if [tuple(r) for r in rows] != js[year][status]:
                mismatches.append(f"{year} {status}")
        assert not mismatches, (
            f"JS LTCG fallback disagrees with tax.py: {mismatches}"
        )

    def test_standard_deduction_matches_for_shared_years(self, py_tables):
        """The one that had actually drifted (OBBBA 2025)."""
        _fed, _ltcg, py = py_tables
        js = _js_std_deduction_fallback()
        mismatches = []
        for (year, status), amount in sorted(py.items()):
            if year not in js or status not in js[year]:
                continue
            if js[year][status] != amount:
                mismatches.append(
                    f"{year} {status}: js={js[year][status]:,.0f} "
                    f"py={amount:,.0f}")
        assert not mismatches, (
            "JS standard-deduction fallback disagrees with tax.py: "
            + "; ".join(mismatches)
        )

    def test_the_comparison_is_not_vacuous(self, py_tables):
        """At least one year must actually be compared — otherwise the
        three tests above pass by skipping everything."""
        fed, ltcg, std = py_tables
        for label, pytbl, jstbl in (
            ("federal", fed, _js_bracket_fallback("FEDERAL_BRACKETS")),
            ("ltcg", ltcg, _js_bracket_fallback("LTCG_BRACKETS")),
            ("std_deduction", std, _js_std_deduction_fallback()),
        ):
            shared = {y for (y, _s) in pytbl} & set(jstbl)
            assert shared, (
                f"{label}: no shared years between tax.py and the JS "
                "fallback, so the parity check compares nothing"
            )


class TestSection1256Parity:
    """CLAUDE.md singles this out: the underlyings are single-sourced in
    Python and the JS literal is an emergency fallback."""

    def test_underlyings_match(self, isolated_workdir):
        from src.analytics.tax import SECTION_1256_UNDERLYINGS

        js = _js_source()
        start = js.index("const SECTION_1256_UNDERLYINGS")
        seg = js[start:js.index(";", start)]
        js_syms = set(re.findall(r"'([A-Z0-9]+)'", seg))
        assert js_syms, "parsed no §1256 symbols from the JS fallback"
        assert js_syms == set(SECTION_1256_UNDERLYINGS), (
            f"§1256 underlyings drifted — js-only={sorted(js_syms - set(SECTION_1256_UNDERLYINGS))}, "
            f"py-only={sorted(set(SECTION_1256_UNDERLYINGS) - js_syms)}"
        )
