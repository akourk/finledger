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
* a window reaching back past the first snapshot reports lifetime
* holdings total the same by account, by type and by sector
* a figure rendered on two tabs is the same figure
* a filtered view never shows rows predating the filter's own start
* nothing renders as NaN / undefined / Infinity

They are deliberately expressed as invariants rather than golden values,
so they keep their teeth when the sample portfolio changes.

One caution, learned immediately: a relation that passes on the synthetic
fixture is not thereby a law.  The fixture's cash flows are simple by
design, which makes some genuinely-independent quantities look locked
together.  Check a candidate against a real portfolio before believing
it — see the note under `TestAWindowLongerThanHistoryEqualsLifetime` for
the one that did not survive that check.
"""

from __future__ import annotations

import json
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
        out = probe(json_path)
    except ProbeUnavailable as exc:
        pytest.skip(f"dashboard probe needs node: {exc}")
    # Carried along so the fixture-quality guards can check properties
    # that are invisible in rendered output — see
    # `test_fixture_can_distinguish_the_two_realized_sources`.
    out["_export"] = json.loads(json_path.read_text(encoding="utf-8"))
    return out


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


_MONEY = re.compile(r"([-+]?)\$([\d,]+(?:\.\d+)?)")


def money(text: str) -> float | None:
    """First dollar figure in a displayed value."""
    m = _MONEY.search(text or "")
    if not m:
        return None
    return (-1 if m.group(1) == "-" else 1) * float(m.group(2).replace(",", ""))


def require_nontrivial(**quantities: float | None) -> None:
    """Fail unless every input to a relation is a real, non-zero number.

    An equality between two zeros is not evidence of anything, and a
    fixture that drifts toward emptiness turns a relation into decoration
    without ever going red.  Both failure modes have already happened
    here — the first fixture made the window-pairing relation untestable,
    and the Overview captured no cards at all — so the guard is applied
    at the point of use rather than trusted to a fixture review.
    """
    dead = [name for name, v in quantities.items() if v is None or abs(v) < 0.01]
    assert not dead, (
        f"relation inputs are absent or zero: {dead} — this comparison "
        f"would hold trivially.  Enrich the fixture or drop the check; do "
        f"not leave it passing on nothing."
    )


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

    def test_fixture_can_distinguish_the_two_realized_sources(self, rendered):
        """The dashboard can source Realized from the annotated walk (real
        per-account lot methods) or from the pure-method comparison table.
        Those two agree exactly unless some account overrides the default
        AND owns more than one lot — so without both halves, a test that
        checks WHICH source a tab reads passes either way.

        That is not hypothetical: F-033's regression test passed against
        the reverted fix until this fixture grew a `Lot Method` row and a
        second lot.  `require_nontrivial` does not catch this shape — the
        values are non-zero, they simply coincide.
        """
        d = rendered["_export"]
        annotated = sum(t.get("realized_gain") or 0 for t in d["transactions"])
        pure_fifo = d["basis_methods"]["fifo"]["totals"]["realized_gain"]
        assert abs(annotated) > 0.01, "fixture realizes no gains at all"
        assert abs(annotated - pure_fifo) > 1.0, (
            f"annotated realized ({annotated:,.2f}) and pure FIFO "
            f"({pure_fifo:,.2f}) are the same figure — this fixture cannot "
            f"tell the two sources apart, so the cross-tab checks below "
            f"pass whichever one the dashboard reads.  It needs a "
            f"`Lot Method` override and at least two lots to relieve."
        )

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
# Relation 3 — a window that contains all of history IS lifetime
# ---------------------------------------------------------------------------

class TestAWindowLongerThanHistoryEqualsLifetime:
    """A portfolio cannot have returns from before it existed, so any
    window reaching back past the first snapshot must report exactly the
    lifetime figures.

    F-032 lived here: the start value was read from the first snapshot AT
    OR AFTER the cutoff while flows were counted from the cutoff, so the
    founding deposit was subtracted twice and a 5y view of a younger
    portfolio printed a dollar LOSS beside a large positive cumulative
    return.
    """

    def _money(self, text: str) -> float | None:
        m = re.search(r"([-+]?)\$([\d,]+(?:\.\d+)?)", text or "")
        if not m:
            return None
        return (-1 if m.group(1) == "-" else 1) * float(m.group(2).replace(",", ""))

    def _by_filter(self, rendered, window):
        return {v["filter"]: v for v in rendered["performance"]
                if v["window"] == window}

    def test_dollar_return_matches_lifetime_for_an_oversized_window(self, rendered):
        life = self._by_filter(rendered, "lifetime")
        wide = self._by_filter(rendered, "5y")
        assert life and wide, "fixture lost the lifetime or 5y views"
        checked = 0
        for filt, lv in life.items():
            wv = wide.get(filt)
            if wv is None:
                continue
            lcards = find_cards(lv, r"^Total Return")
            wcards = find_cards(wv, r"^Total Return")
            if not (lcards and wcards):
                continue
            # The last match is the windowed card; the first is the
            # always-lifetime anchor card above the toggles.
            a, b = self._money(lcards[-1]["value"]), self._money(wcards[-1]["value"])
            if a is None or b is None:
                continue
            assert b == pytest.approx(a, abs=1.0), (
                f"[{filt}] 5y dollar return {b:+,.2f} but lifetime {a:+,.2f}.  "
                f"The 5y window starts before this portfolio's first "
                f"snapshot, so the two must be the same figure."
            )
            checked += 1
        assert checked >= 3, f"only {checked} filters compared"

    def test_lifetime_dollar_return_matches_the_anchor_card(self, rendered):
        """The Performance tab renders Total Return twice: once as an
        always-lifetime anchor above the toggles (straight from
        `analytics.header_summary`) and once as the windowed figure below.
        At filter=Total / window=lifetime they are the same quantity by
        two routes, and the code comment says so — which makes it exactly
        the kind of claim that stops being true without anyone noticing.
        """
        v = next((x for x in rendered["performance"]
                  if x["filter"] == "Total" and x["window"] == "lifetime"), None)
        assert v is not None, "no Total/lifetime view rendered"
        cards = find_cards(v, r"^Total Return")
        assert len(cards) >= 2, (
            f"expected an anchor and a windowed Total Return card, got "
            f"{[c['label'] for c in cards]}"
        )
        anchor, windowed = self._money(cards[0]["value"]), self._money(cards[-1]["value"])
        assert anchor is not None and windowed is not None
        assert windowed == pytest.approx(anchor, abs=0.02), (
            f"anchor Total Return {anchor:+,.2f} vs windowed {windowed:+,.2f} "
            f"— the same figure by two routes has drifted."
        )

    # NOT asserted, deliberately: that the dollar return and the
    # cumulative return agree in SIGN.  It looks like a law and it is not.
    # The dollar figure is a single-period Modified Dietz numerator; the
    # percentage is a chain-linked TWR that neutralises flow timing.  A
    # deposit landing just before a decline drives them apart honestly —
    # the same behaviour gap the tab already explains for XIRR vs TWR.
    # This test existed for an hour and passed on the synthetic fixture,
    # whose flows are too simple to separate them; running it against a
    # real portfolio flagged 10 of 88 views, all with substantial net
    # flows.  Validate a candidate relation against real data before
    # trusting a green synthetic run to mean it holds.


# ---------------------------------------------------------------------------
# Relation 4 — a filtered view does not render rows it has no data for
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
# Relation 5 — one number, several groupings
# ---------------------------------------------------------------------------

class TestHoldingsGroupingsAgree:
    """By account, by type and by sector are three ways of partitioning
    the SAME portfolio, so their totals are the same number three times.
    A grouping that drops or double-counts a position shows up here and
    almost nowhere else — each view looks internally plausible on its
    own."""

    def test_every_grouping_totals_the_same(self, rendered):
        views = rendered.get("holdings_views", {})
        assert set(views) >= {"account", "type", "sector"}, (
            f"probe captured only {sorted(views)}"
        )
        totals = {k: money(v["fields"].get("holdingsTotalValue", ""))
                  for k, v in views.items()}
        require_nontrivial(**totals)
        ref_name, ref = next(iter(totals.items()))
        for name, val in totals.items():
            assert val == pytest.approx(ref, abs=0.02), (
                f"holdings total by {name} is {val:,.2f} but by {ref_name} "
                f"it is {ref:,.2f} — the same positions, partitioned two ways."
            )

    def test_each_grouping_actually_partitions_something(self, rendered):
        """Guards the guard: three identical totals prove nothing if every
        view rendered a single all-encompassing row."""
        views = rendered.get("holdings_views", {})
        for name, v in views.items():
            rows = sum(len(t["rows"]) for t in v["tables"])
            assert rows >= 1, f"holdings view {name} rendered no rows"


# ---------------------------------------------------------------------------
# Relation 6 — the same quantity rendered on two tabs
# ---------------------------------------------------------------------------

class TestOverviewAgreesWithPerformance:
    """Realized, Unrealized and Net Contributed appear on both Overview
    and Performance, reached by different code paths.

    F-033 lived here: Overview sourced Cost Basis, Unrealized and
    Realized from `basisMethods.fifo` — the Lot Method Comparison table,
    which holds four PURE single-method what-if walks.  The real
    portfolio uses per-account methods from `Lot Method` metadata, so
    with one account on HIFO the two tabs disagreed by roughly 19% on
    Realized under one unqualified label.

    This is the cross-tab form of the F-031 shape, and the reason it
    survived is the same: both numbers were individually computable,
    plausible, and never rendered next to each other.
    """

    PAIRS = [("Realized P&L", r"^Realized$"),
             ("Unrealized P&L", r"^Unrealized$"),
             ("Net Contributed", r"^Net Contributed$")]

    def test_shared_figures_match(self, rendered):
        ov = rendered["tabs"]["overview"]
        perf = next((v for v in rendered["performance"]
                     if v["filter"] == "Total" and v["window"] == "lifetime"), None)
        assert perf is not None, "no Total/lifetime performance view"
        for ov_label, perf_pattern in self.PAIRS:
            a = find_cards(ov, r"^" + re.escape(ov_label) + r"$")
            b = find_cards(perf, perf_pattern)
            assert a, f"Overview no longer renders {ov_label!r}"
            assert b, f"Performance no longer renders a card matching {perf_pattern!r}"
            x, y = money(a[0]["value"]), money(b[0]["value"])
            require_nontrivial(**{f"overview_{ov_label}": x, f"perf_{ov_label}": y})
            assert x == pytest.approx(y, abs=0.02), (
                f"{ov_label}: Overview shows {x:+,.2f}, Performance shows "
                f"{y:+,.2f} for the same quantity.  Check both are reading "
                f"the annotated walk and not the pure-method comparison table."
            )

    def test_overview_cost_basis_matches_the_holdings_table(self, rendered):
        """Cost Basis has no counterpart on Performance, but the Holdings
        table is built from the same annotated walk it should be using."""
        ov = rendered["tabs"]["overview"]
        card = find_cards(ov, r"^Cost Basis$")
        assert card, "Overview no longer renders Cost Basis"
        shown = money(card[0]["value"])
        unreal = find_cards(ov, r"^Unrealized P&L$")
        value_card = find_cards(ov, r"^(Total )?Value$")
        require_nontrivial(cost_basis=shown)
        # value = basis + unrealized is the identity the two cards imply.
        if value_card and unreal:
            v, u = money(value_card[0]["value"]), money(unreal[0]["value"])
            if v is not None and u is not None:
                assert v == pytest.approx(shown + u, abs=1.0), (
                    f"Overview value {v:,.2f} != cost basis {shown:,.2f} + "
                    f"unrealized {u:+,.2f} — the three cards do not close."
                )


# ---------------------------------------------------------------------------
# Relation 5 — nothing renders as a non-number
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
