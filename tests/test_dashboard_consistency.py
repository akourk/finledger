"""Relations that must hold between figures the dashboard DISPLAYS.

Every other test in this suite checks a figure against an expected value.
These check figures against *each other*, in the rendered output, because
that is where the last two escaped bug classes lived.

`analytics/` guarantees each number is computed once and correctly.  It
cannot guarantee that two correctly-computed numbers placed side by side
are comparable — the pairing is invented at the render site, out of
inputs the compute layer never sees together.  F-031 is the worked
example: `computeWindowedMetrics` was right, `compute_twr_summary` was
right, and the card between them subtracted a three-year return from a
nine-year one.  Nothing that stops at the compute layer can see it.

So these tests render the real bundle (see `tools/dashboard_probe.js` for
the DOM stub) across the whole (filter x window) matrix and assert
relations that are true by construction of what the labels claim:

* a cumulative and an annualized return over one window satisfy
  `(1 + ann) ** years == 1 + cum` — the tell that identified F-031, since
  a lower cumulative beside a higher annualized is impossible
* two figures a card joins with "vs" report the same measured span
* a filtered view never shows rows predating the filter's own start
* nothing renders as NaN / undefined / Infinity

They are deliberately expressed as invariants rather than golden values,
so they keep their teeth when the sample portfolio changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.build_staggered_portfolio import build as build_portfolio
from tests.fixtures.build_staggered_portfolio import dense_prices
from tools.dashboard_probe import ProbeUnavailable, probe

pytestmark = pytest.mark.usefixtures("isolated_workdir")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def rendered(isolated_workdir, stub_prices):
    """Run the staggered portfolio through the real pipeline, then render
    the real dashboard bundle and return what it displayed.

    The fixture's two load-bearing properties (staggered account starts,
    activity reaching the present) are asserted by
    `TestTheHarnessActuallyRan` rather than assumed — a fixture that
    quietly loses them would turn every relation below into a test that
    passes by having nothing to check.
    """
    from datetime import date, timedelta

    build_portfolio(isolated_workdir)

    for sym, series in dense_prices(
        {
            "AAPL": (140.0, 0.14),
            "SPY": (400.0, 0.11),
            "BND": (75.0, 0.02),
            "VXUS": (55.0, 0.07),
        },
        end=date.today() + timedelta(days=2),
    ).items():
        stub_prices.set(sym, series)
    stub_prices.set_sector("AAPL", "Technology")
    stub_prices.set_sector("SPY", "ETFs")

    import sys
    argv_save = sys.argv
    sys.argv = ["fin", "--skip-rename"]
    try:
        from src.main import main
        main()
    finally:
        sys.argv = argv_save

    json_path = isolated_workdir / "exports" / "transactions.json"
    assert json_path.exists(), "pipeline did not produce transactions.json"

    try:
        return probe(json_path)
    except ProbeUnavailable as exc:
        pytest.skip(f"dashboard probe needs node: {exc}")


# ---------------------------------------------------------------------------
# Parsing helpers — the probe hands back display strings on purpose
# ---------------------------------------------------------------------------

_PCT = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def pcts(text: str) -> list[float]:
    """Every percentage in a displayed value, as fractions."""
    return [float(m) / 100.0 for m in _PCT.findall(text or "")]


def span_of(card: dict) -> tuple[str, str] | None:
    """The measured window a card states in its tooltip, if it states one."""
    found = _DATE.findall(card.get("title", ""))
    return (found[0], found[1]) if len(found) >= 2 else None


def years_between(start: str, end: str) -> float:
    from datetime import date
    d0 = date(*map(int, start.split("-")))
    d1 = date(*map(int, end.split("-")))
    return (d1 - d0).days / 365.25


def find_cards(view: dict, pattern: str) -> list[dict]:
    rx = re.compile(pattern, re.I)
    return [c for c in view["cards"] if rx.search(c["label"])]


# ---------------------------------------------------------------------------
# The probe itself must not be vacuous
# ---------------------------------------------------------------------------

class TestTheHarnessActuallyRan:

    def test_missing_node_skips_rather_than_passes(self, monkeypatch):
        """Without node the probe must raise, so the fixture skips.  If it
        returned an empty result instead, every relation below would pass
        on a machine that never rendered anything."""
        import tools.dashboard_probe as dp
        monkeypatch.setattr(dp, "node_path", lambda: None)
        with pytest.raises(ProbeUnavailable):
            dp.probe(Path("does-not-matter.json"))

    def test_no_render_errors(self, rendered):
        assert rendered["console_errors"] == [], (
            "a renderer threw; every assertion below is measuring nothing"
        )

    def test_matrix_is_populated(self, rendered):
        views = rendered["performance"]
        assert len(views) >= 8, f"matrix collapsed to {len(views)} views"
        assert any(v["cards"] for v in views), "no stat cards rendered at all"

    def test_every_captured_tab_produced_something(self, rendered):
        """A tab that renders nothing passes every check below by having
        nothing to check.  Overview did exactly that while this harness
        was being written: its stat cards come from a global that runs at
        load, so driving only the tab router captured an empty page and
        reported clean."""
        empty = [name for name, v in rendered["tabs"].items()
                 if not v["cards"] and not v["tables"]]
        assert not empty, f"tabs captured with no content: {empty}"

    def test_expected_tabs_were_reached(self, rendered):
        missing = {"overview", "performance", "holdings"} - set(rendered["tabs"])
        assert not missing, f"probe never rendered: {sorted(missing)}"

    def test_fixture_has_staggered_account_starts(self, rendered):
        """The relation F-031 broke is invisible unless some filter's
        natural window is shorter than the portfolio's."""
        spans = set()
        for v in rendered["performance"]:
            if v["window"] != "lifetime":
                continue
            for c in find_cards(v, r"Your Return"):
                s = span_of(c)
                if s:
                    spans.add(s[0])
        assert len(spans) >= 2, (
            "every account's lifetime window starts on the same date — this "
            f"fixture cannot exercise the pairing bug (starts: {sorted(spans)})"
        )


# ---------------------------------------------------------------------------
# Relation 1 — a return pair agrees on its own span
# ---------------------------------------------------------------------------

class TestCumulativeAndAnnualizedAgree:
    """`(1 + ann) ** years == 1 + cum`.  Violated exactly when the two
    halves of one card were measured over different windows."""

    def test_every_return_card_is_self_consistent(self, rendered):
        checked = 0
        for v in rendered["performance"]:
            for c in v["cards"]:
                if "ann." not in c["value"]:
                    continue
                span = span_of(c)
                if not span:
                    continue
                vals = pcts(c["value"])
                if len(vals) < 2:
                    continue
                cum, ann = vals[0], vals[1]
                years = years_between(*span)
                if years <= 0 or (1 + cum) <= 0 or (1 + ann) <= 0:
                    continue
                implied = (1 + ann) ** years - 1
                # Displayed figures are rounded to 2dp, which contributes
                # roughly 0.1% relative noise over a multi-year span.  A
                # tolerance an order of magnitude above that still catches
                # the one-snapshot window offset on the trailing presets.
                assert implied == pytest.approx(cum, rel=0.01, abs=0.002), (
                    f"[{v['filter']} / {v['window']}] {c['label']!r}: "
                    f"cumulative {cum:+.4f} and annualized {ann:+.4f} over "
                    f"{years:.2f}y ({span[0]}..{span[1]}) do not agree — "
                    f"annualizing implies {implied:+.4f}.  The two halves "
                    f"were measured over different windows."
                )
                checked += 1
        assert checked >= 4, f"only {checked} return pairs checked — too weak"

    def test_a_lower_cumulative_never_has_a_higher_annualized(self, rendered):
        """The cheap form of the same law, stated across a comparison
        pair rather than within one card.  This is the shape the user
        spotted by eye in F-031."""
        checked = 0
        for v in rendered["performance"]:
            yours = find_cards(v, r"^Your Return")
            spy = find_cards(v, r"^SPY Return")
            if not (yours and spy):
                continue
            a, b = pcts(yours[0]["value"]), pcts(spy[0]["value"])
            if len(a) < 2 or len(b) < 2:
                continue
            (a_cum, a_ann), (b_cum, b_ann) = a[:2], b[:2]
            assert (a_cum >= b_cum) == (a_ann >= b_ann), (
                f"[{v['filter']} / {v['window']}] impossible pair: yours "
                f"{a_cum:+.4f} cum / {a_ann:+.4f} ann vs SPY {b_cum:+.4f} "
                f"cum / {b_ann:+.4f} ann.  Two returns over one window "
                f"cannot order differently cumulatively and annualized."
            )
            checked += 1
        assert checked >= 4, f"only {checked} comparison pairs checked"


# ---------------------------------------------------------------------------
# Relation 2 — a comparison states one span for both sides
# ---------------------------------------------------------------------------

class TestComparisonsShareTheirWindow:

    def test_benchmark_cards_agree_on_the_measured_span(self, rendered):
        checked = 0
        for v in rendered["performance"]:
            group = find_cards(v, r"^(Your Return|SPY Return|Vs SPY)")
            spans = {span_of(c) for c in group if span_of(c)}
            if not spans:
                continue
            assert len(spans) == 1, (
                f"[{v['filter']} / {v['window']}] the benchmark cards claim "
                f"{len(spans)} different measured spans: {sorted(spans)}"
            )
            checked += 1
        assert checked >= 4, f"only {checked} views carried a stated span"

    def test_a_stated_span_is_not_the_whole_history_for_a_late_account(self, rendered):
        """Guards the specific regression: a filtered lifetime view must
        not silently widen to the portfolio's first snapshot."""
        starts = {}
        for v in rendered["performance"]:
            if v["window"] != "lifetime":
                continue
            for c in find_cards(v, r"^Your Return"):
                s = span_of(c)
                if s:
                    starts[v["filter"]] = s[0]
        assert starts, "no lifetime view stated its span"
        earliest = min(starts.values())
        later = {k: s for k, s in starts.items() if s > earliest}
        assert later, (
            "no filter starts later than the portfolio — cannot distinguish "
            "a correctly-narrow window from a wrongly-wide one"
        )


# ---------------------------------------------------------------------------
# Relation 3 — a filtered view does not render rows it has no data for
# ---------------------------------------------------------------------------

class TestFilteredViewsDoNotShowEmptyLeadingYears:

    def test_annual_table_starts_at_the_filters_first_real_year(self, rendered):
        for v in rendered["performance"]:
            if v["window"] != "lifetime":
                continue
            for tbl in v["tables"]:
                if not tbl["headers"] or "Year" not in tbl["headers"][0]:
                    continue
                if not tbl["rows"]:
                    continue
                first = tbl["rows"][0]
                money = [c for c in first[1:4] if re.search(r"\d", c)]
                allzero = money and all(
                    re.fullmatch(r"[-+$]*0\.00", c.replace(",", "")) for c in money
                )
                assert not allzero, (
                    f"[{v['filter']}] Annual Returns opens on {first[0]} with "
                    f"an all-zero row: {first!r}.  A year the filter did not "
                    f"exist for renders beside a real benchmark percentage."
                )


# ---------------------------------------------------------------------------
# Relation 4 — nothing renders as a non-number
# ---------------------------------------------------------------------------

class TestNoNonNumbersReachTheReader:

    def test_no_nan_undefined_or_infinity(self, rendered):
        bad = re.compile(r"\b(NaN|undefined|Infinity|null)\b")
        offenders = []
        for v in rendered["performance"]:
            for c in v["cards"]:
                if bad.search(c["value"]):
                    offenders.append(f"[{v['filter']}/{v['window']}] {c['label']}: {c['value']}")
        for name, v in rendered["tabs"].items():
            for c in v["cards"]:
                if bad.search(c["value"]):
                    offenders.append(f"[{name}] {c['label']}: {c['value']}")
            for tbl in v["tables"]:
                for row in tbl["rows"]:
                    if any(bad.search(cell) for cell in row):
                        offenders.append(f"[{name} table] {row!r}")
        assert not offenders, "non-numbers rendered:\n" + "\n".join(offenders[:20])
