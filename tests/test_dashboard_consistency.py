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
* an Income stat card equals the column it was summed from
* a Tax year's cards, its table row, and proceeds - basis all agree
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

    def test_basis_totals_is_the_annotated_walk_not_the_comparison_table(
            self, rendered):
        """`basis_totals` exists so a published figure never has to pick
        between four what-if walks.  Its realized figure must be the
        annotated one — which, on a fixture with a `Lot Method` override,
        is a visibly different number from pure FIFO."""
        d = rendered["_export"]
        bt = d.get("basis_totals") or {}
        assert bt, "export carries no basis_totals"
        assert bt.get("method") == "annotated"
        annotated = sum(t.get("realized_gain") or 0 for t in d["transactions"])
        pure_fifo = d["basis_methods"]["fifo"]["totals"]["realized_gain"]
        # Matches the annotated walk (to the cent-rounding of the
        # annotations it is deliberately NOT re-summing) ...
        assert bt["realized_gain"] == pytest.approx(annotated, abs=0.05)
        # ... and is NOT the comparison table's pure-FIFO column.
        assert abs(bt["realized_gain"] - pure_fifo) > 1.0, (
            f"basis_totals realized ({bt['realized_gain']:,.2f}) equals pure "
            f"FIFO ({pure_fifo:,.2f}) — this fixture cannot tell the "
            f"annotated walk from the comparison table"
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

    # Every dollar card in the windowed row has a twin in the
    # always-lifetime anchor row above the toggles.  At filter=Total /
    # window=lifetime each pair is one quantity, so the two must agree
    # to the displayed cent.
    ANCHOR_TWINS = ["Total Return", "Realized", "Unrealized",
                    "Net Contributed"]

    @pytest.mark.parametrize("metric", ANCHOR_TWINS)
    def test_lifetime_card_matches_its_anchor_card(self, rendered, metric):
        """The same quantity by two routes, side by side on one screen.

        The tolerance here used to be 2 cents, and that slack was load-
        bearing: the windowed row re-summed the per-txn `realized_gain`
        / `cash_flow` annotations — each rounded to cents for display —
        while the anchor read the walker's own accumulator.  Realized
        and Net Contributed rendered a cent apart, Total Return two.
        CLAUDE.md forbids exactly that ("a published basis figure never
        comes from re-summing the annotations"), and F-033 is these two
        cards disagreeing for the same reason at ~19%.

        A cent is not the point — the drift scales with fill count, and
        an approximate assertion cannot tell "rounding" from "wrong".
        The all-time case now READS the anchor's field, so this is exact.
        """
        v = next((x for x in rendered["performance"]
                  if x["filter"] == "Total" and x["window"] == "lifetime"), None)
        assert v is not None, "no Total/lifetime view rendered"
        # Select by label shape, not position: the metric can appear a
        # third time further down the tab (Net Contributed also labels a
        # bar in the benchmark comparison), and an index would silently
        # start comparing the wrong pair.  The anchor's label is exactly
        # the metric; the windowed one carries a suffix (the window, or
        # for Unrealized the as-of date).
        cards = find_cards(v, rf"^{metric}(?:\s|$)")
        anchor_c = next((c for c in cards if c["label"].strip() == metric), None)
        windowed_c = next((c for c in cards if c["label"].strip() != metric), None)
        assert anchor_c and windowed_c, (
            f"expected an anchor and a windowed {metric} card, got "
            f"{[c['label'] for c in cards]}"
        )
        anchor, windowed = self._money(anchor_c["value"]), self._money(windowed_c["value"])
        assert anchor is not None and windowed is not None, (
            f"could not parse {metric}: {[c['value'] for c in cards]}")
        assert windowed == pytest.approx(anchor, abs=0.005), (
            f"anchor {metric} {anchor:+,.2f} vs windowed {windowed:+,.2f} "
            f"— the same figure by two routes has drifted."
        )

    def test_the_unrealized_card_is_labelled_by_date_not_window(self, rendered):
        """Unrealized is the one LEVEL in a row of flows.

        Every trailing window ends on the same day, so the figure is
        identical across lifetime / 5y / 3mo — which read as a bug when
        the card was labelled `3mo`, and was the user report that found
        F-035.  Labelling it with the as-of date instead is what makes
        the unchanging value legible rather than suspicious.
        """
        views = [x for x in rendered["performance"] if x["filter"] == "Total"]
        assert views, "no Total views rendered"
        seen = set()
        for v in views:
            windowed = find_cards(v, r"^Unrealized")[-1]
            assert "as of" in windowed["label"], (
                f"[{v['window']}] windowed Unrealized is labelled "
                f"{windowed['label']!r} — a level must not carry a window "
                f"label it does not react to"
            )
            seen.add(self._money(windowed["value"]))
        assert len(seen) == 1, (
            f"Unrealized differs across trailing windows: {sorted(seen)}.  "
            f"Every preset window ends at the latest snapshot, so this is "
            f"one figure — if it moved, the two sources have diverged again."
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
# Relation 4b — Income: the stat cards are summed from the table below
# ---------------------------------------------------------------------------

class TestIncomeCardsSumTheirTable:
    """Income's all-time stat cards are produced IN JAVASCRIPT by summing
    the annual table's columns (`allTimeDiv` and friends in
    `70-income.js`).  A card and the rows it was summed from are the same
    arithmetic rendered twice, which makes the discrepancy invisible to
    anything that stops at `analytics/`.
    """

    CATEGORIES = ["Dividends", "Interest", "Rewards", "Lending"]

    def _annual_table(self, rendered):
        for t in rendered["tabs"]["income"]["tables"]:
            if t["headers"][:2] == ["Year", "Dividends"]:
                return t
        return None

    def test_each_category_card_equals_its_column(self, rendered):
        tbl = self._annual_table(rendered)
        assert tbl is not None, "Income's annual table is no longer rendered"
        assert tbl["rows"], "Income's annual table has no rows"
        cards = rendered["tabs"]["income"]["cards"]
        checked = []
        for idx, name in enumerate(self.CATEGORIES, start=1):
            column = sum((money(r[idx]) or 0) for r in tbl["rows"] if len(r) > idx)
            card = find_cards({"cards": cards}, r"^" + name + r" \(all-time\)$")
            if not card:
                continue
            shown = money(card[0]["value"])
            if abs(column) < 0.01 and (shown is None or abs(shown) < 0.01):
                continue  # structurally absent for this portfolio
            assert shown == pytest.approx(column, abs=0.02), (
                f"{name}: the all-time card shows {shown:,.2f} but its "
                f"column in the annual table sums to {column:,.2f}."
            )
            checked.append(name)
        assert len(checked) >= 2, (
            f"only {checked} had non-zero amounts — this fixture exercises "
            f"too few income categories for the relation to mean much"
        )

    def test_each_row_totals_its_own_categories(self, rendered):
        tbl = self._annual_table(rendered)
        assert tbl is not None
        total_idx = len(self.CATEGORIES) + 1
        for row in tbl["rows"]:
            if len(row) <= total_idx:
                continue
            parts = sum((money(row[i]) or 0) for i in range(1, total_idx))
            total = money(row[total_idx])
            if total is None:
                continue
            assert total == pytest.approx(parts, abs=0.02), (
                f"income row {row[0]}: categories sum to {parts:,.2f} but "
                f"the Total column shows {total:,.2f}"
            )


# ---------------------------------------------------------------------------
# Relation 4c — Tax: the year's cards, its table row, and its arithmetic
# ---------------------------------------------------------------------------

class TestTaxYearFiguresAgree:
    """The Tax tab renders each year's realizations twice — as headline
    cards and as a row in Realized Gains by Year — and the row carries
    proceeds and basis, whose difference IS the gain.

    Both checks need a year that actually realized something in BOTH
    directions; the tab defaults to the current year, which usually has
    neither, so the probe sweeps every year with activity.
    """

    def _year_row(self, view, year):
        for t in view["tables"]:
            if t["headers"] and t["headers"][0].startswith("Year"):
                for row in t["rows"]:
                    if row and row[0] == year:
                        return row
        return None

    def test_cards_match_the_by_year_row(self, rendered):
        years = rendered.get("tax_years", {})
        assert years, "probe captured no tax years"
        checked = 0
        for year, view in years.items():
            if year == "all":
                continue
            st = find_cards(view, r"^Short-term Gain$")
            lt = find_cards(view, r"^Long-term Gain$")
            row = self._year_row(view, year)
            if not (st and lt and row and len(row) >= 6):
                continue
            cst, clt = money(st[0]["value"]), money(lt[0]["value"])
            rst, rlt = money(row[4]), money(row[5])
            if cst is None or clt is None or rst is None or rlt is None:
                continue
            assert cst == pytest.approx(rst, abs=0.02), (
                f"{year}: Short-term card {cst:+,.2f} vs table {rst:+,.2f}")
            assert clt == pytest.approx(rlt, abs=0.02), (
                f"{year}: Long-term card {clt:+,.2f} vs table {rlt:+,.2f}")
            checked += 1
        assert checked >= 1, "no tax year rendered both cards and its row"

    def test_proceeds_minus_basis_is_the_gain(self, rendered):
        checked = 0
        for year, view in rendered.get("tax_years", {}).items():
            if year == "all":
                continue
            row = self._year_row(view, year)
            if not row or len(row) < 6:
                continue
            proceeds, basis = money(row[2]), money(row[3])
            st, lt = money(row[4]), money(row[5])
            if None in (proceeds, basis, st, lt):
                continue
            require_nontrivial(proceeds=proceeds, basis=basis)
            assert (proceeds - basis) == pytest.approx(st + lt, abs=0.05), (
                f"{year}: proceeds {proceeds:,.2f} - basis {basis:,.2f} = "
                f"{proceeds - basis:+,.2f}, but short-term {st:+,.2f} + "
                f"long-term {lt:+,.2f} = {st + lt:+,.2f}"
            )
            checked += 1
        assert checked >= 1, "no tax year carried proceeds and basis"

    def test_fixture_realizes_in_both_terms(self, rendered):
        """An ST/LT relation cannot catch a misclassification while one
        side is structurally zero."""
        d = rendered["_export"]
        by_year = (d["analytics"].get("tax") or {}).get("realized_by_year") or []
        st = sum(r.get("st") or 0 for r in by_year)
        lt = sum(r.get("lt") or 0 for r in by_year)
        assert abs(st) > 0.01 and abs(lt) > 0.01, (
            f"fixture realizes short-term {st:,.2f} and long-term {lt:,.2f} "
            f"— it needs both to exercise the split"
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


# ---------------------------------------------------------------------------
# Structural guard — the relation tests above cannot catch this alone
# ---------------------------------------------------------------------------

class TestTheWindowedRowDoesNotRederiveTheAnchorFigures:
    """A source-level guard, because the render relation goes vacuous.

    `test_lifetime_card_matches_its_anchor_card` states the right law,
    and on a real portfolio it fails against the pre-fix code by a cent
    or two.  On the synthetic fixture it does NOT: every quantity here
    is round, so the cent-rounded per-txn annotations re-sum to exactly
    the walker's accumulator and both routes agree by arithmetic
    accident.  Verified by reverting the fix — 28 of 29 still passed.

    That is the fixture-quality trap this file's own docstring warns
    about, in its other direction: a relation that PASSES on synthetic
    data is not thereby guarded.  Tuning the fixture to manufacture
    rounding noise would be tuning a test to fail, so the regression is
    pinned structurally instead — this cannot go quiet on any data.
    """

    @staticmethod
    def _perf_src() -> str:
        return (Path(__file__).resolve().parents[1]
                / "src" / "dashboard" / "app" / "90-performance.js").read_text(
                    encoding="utf-8")

    def test_the_all_time_case_reads_the_anchor_fields(self):
        src = self._perf_src()
        assert "_isWholeLifetime" in src, (
            "the windowed row no longer distinguishes the all-time case — "
            "it is re-deriving figures the anchor row already publishes"
        )
        for expr, what in (
            ("_isWholeLifetime ? _whole_realized", "Realized"),
            ("_isWholeLifetime ? _whole_netContrib", "Net Contributed"),
            ("_isWholeLifetime\n    ? _whole_totalReturn", "Total Return"),
            ("if (_isWholeLifetime) {\n    totalUnrealized = _whole_unrealized;",
             "Unrealized / Value"),
        ):
            assert expr in src, (
                f"the windowed {what} card no longer reads the anchor's "
                f"field at lifetime/no-filter — the two cards will drift "
                f"by the annotations' cent rounding again"
            )

    def test_the_unrealized_card_carries_no_window_label(self):
        """Unrealized is a level; every trailing window ends today."""
        src = self._perf_src()
        assert 'label: `Unrealized <span class="sub">${_winLabel}</span>`' not in src, (
            "the Unrealized card is labelled with the window again — it "
            "cannot react to one, which is what made it read as a bug"
        )
        assert "as of ${_winUpperIso}" in src, (
            "the Unrealized card no longer states the date it is measured at"
        )


# ---------------------------------------------------------------------------
# Relation — a custom window ends where it says it ends
# ---------------------------------------------------------------------------

class TestACustomWindowEndsWhereItSaysItDoes:
    """History is semimonthly; the "To" picker accepts any day.

    The end bound used to be an exact `history.find(...)` with
    `|| history[history.length - 1]` behind it, so any date that wasn't
    the 15th or a month end silently resolved to TODAY — while the flow
    filters honoured the date the user actually typed.  A window ending
    in April therefore reported today's value minus April's
    contributions: every market move and every deposit in between
    mis-attributed.  Unrealized was the visible symptom (it never moved
    off today's figure, which is what surfaced it); Total Return was
    wrong in the same way and looked plausible.

    None of the preset windows can catch this — they all end at the
    latest snapshot, where the broken lookup and the correct one agree.
    That is why the probe renders a custom window explicitly.
    """

    @pytest.fixture
    def custom(self, rendered):
        c = rendered.get("performance_custom")
        if not c:
            pytest.skip("probe could not build a between-snapshots date")
        return c

    def test_the_requested_date_was_not_a_snapshot(self, custom):
        """Guards the guard: if the probe happened to pick a real
        snapshot date, every assertion below would hold trivially."""
        assert custom["requested_end"] != custom["expected_end"], (
            "probe picked a date that IS a snapshot — this case cannot "
            "distinguish correct resolution from the old fallback"
        )
        assert custom["requested_end"] != custom["latest_snapshot"]

    def test_the_card_reports_the_resolved_date_not_the_requested_one(self, custom):
        """Resolution is backward — the last snapshot at or before the
        request — and the card says which date it actually used."""
        card = find_cards(custom, r"^Unrealized")[-1]
        assert custom["expected_end"] in card["label"], (
            f"Unrealized is labelled {card['label']!r}; expected it to "
            f"name the resolved snapshot {custom['expected_end']}"
        )
        assert custom["latest_snapshot"] not in card["label"], (
            "the card resolved to the LATEST snapshot — the silent "
            "fallback to today is back"
        )

    def test_the_figures_moved_off_the_latest_snapshot(self, custom, rendered):
        """The real defect, not the label: a custom window ending in the
        past must not report today's value."""
        life = next((x for x in rendered["performance"]
                     if x["filter"] == "Total" and x["window"] == "lifetime"), None)
        assert life is not None
        for metric in ("Unrealized", "Total Return"):
            cur = money(find_cards(custom, rf"^{metric}(?:\s|$)")[-1]["value"])
            latest = money(find_cards(life, rf"^{metric}(?:\s|$)")[-1]["value"])
            assert cur is not None and latest is not None
            assert cur != pytest.approx(latest, abs=0.01), (
                f"custom {metric} equals the latest-snapshot figure "
                f"({cur:,.2f}) — the window end silently fell through to today"
            )

    def test_the_stated_span_matches_the_card(self, custom):
        """The row's own "Measured X to Y" note and the Unrealized
        card's as-of date are the same date, because every figure in the
        row is measured to the resolved bound."""
        spans = [c["title"] for c in custom["cards"] if "Measured " in (c["title"] or "")]
        assert spans, "no card states its measured span"
        for t in spans:
            assert f"to {custom['expected_end']}" in t, (
                f"a card claims a span ending elsewhere: {t!r}")


# ---------------------------------------------------------------------------
# Relation — the two TWR engines agree over the same span
# ---------------------------------------------------------------------------

class TestTheTwoReturnEnginesAgree:
    """`lifetime` renders Python's precomputed summary; every other
    window — presets and custom alike — runs the JS chain-link walk.
    Two implementations of one rule, and the tab puts them one chip-click
    apart, so any divergence reads as "clicking a button changed my
    return".

    They diverged, badly and in the flattering direction: the JS walk
    re-derived external cash flow instead of reading the `cash_flow`
    annotation the pipeline already stamps on every txn, and its copy of
    the classifier was missing two carve-outs (Coinbase bank-funded
    Buys; transfers crossing fin's measurement boundary).  Under-counting
    money IN is not neutral — the value it bought has to be attributed
    to something, and Modified Dietz with no flow to net out books it as
    market return.
    """

    def _twr(self, view):
        card = find_cards(view, r"^Your Return \(TWR\)")
        assert card, "no TWR card rendered"
        m = re.search(r"([-+]?[\d.]+)%", card[0]["value"])
        return float(m.group(1)) if m else None

    def test_a_full_range_custom_window_equals_lifetime(self, rendered):
        full = rendered.get("performance_full_custom")
        if not full:
            pytest.skip("probe could not render a full-range custom window")
        life = next((x for x in rendered["performance"]
                     if x["filter"] == "Total" and x["window"] == "lifetime"), None)
        assert life is not None
        a, b = self._twr(life), self._twr(full)
        assert a is not None and b is not None
        assert b == pytest.approx(a, abs=0.05), (
            f"lifetime TWR {a:+.2f}% but a custom window over the SAME span "
            f"{b:+.2f}% — the Python summary and the JS walk have diverged"
        )


class TestTheJsWalkDoesNotReclassifyCashFlow:
    """Structural, because the relation above can go vacuous.

    Whether the fixture exercises the divergence depends on it holding
    the exact transaction shapes the JS copy mishandled — Coinbase
    bank-funded Buys, boundary-crossing transfers.  A fixture without
    them makes both routes agree and the relation prove nothing.  This
    guard holds on any data.
    """

    @staticmethod
    def _perf_src() -> str:
        return (Path(__file__).resolve().parents[1]
                / "src" / "dashboard" / "app" / "90-performance.js").read_text(
                    encoding="utf-8")

    def test_net_flow_reads_the_exported_annotation(self):
        src = self._perf_src()
        body = src[src.index("function _netFlowBetween"):]
        body = body[:body.index("\n}")]
        assert "_txnCashFlowForGroups" in body, (
            "_netFlowBetween no longer reads the shared per-txn cash-flow "
            "annotation — external cash flow is classified once, in "
            "basis.txn_external_cash_flow, and re-deriving it here is how "
            "the two return engines drifted apart"
        )
        core = (Path(__file__).resolve().parents[1]
                / "src" / "dashboard" / "app" / "00-core.js").read_text(
                    encoding="utf-8")
        helper = core[core.index("function _txnCashFlowForGroups"):]
        helper = helper[:helper.index("\n}")]
        assert "t.cash_flow" in helper
        body += helper
        for token in ("_TWR_ADD_ACTIONS", "_TWR_SUB_ACTIONS",
                      "retirementContribInfo", "cash_flow', 'in"):
            assert token not in body, (
                f"_netFlowBetween is re-deriving the classification again "
                f"({token}) instead of reading the annotation"
            )

    def test_the_chain_link_walk_still_carries_unabsorbed_flow(self):
        """Mirrors analytics/_shared.py::_chain_link_return."""
        src = self._perf_src()
        body = src[src.index("function _twrWalk"):]
        body = body[:body.index("\n}\n")]
        assert "pendingFlow" in body, (
            "the JS walk no longer carries unabsorbed flow across skipped "
            "periods — Python does, and dropping it books a skipped "
            "deposit's value as market gain"
        )
        assert "peakSoFar" in body, (
            "the JS walk lost the trailing-peak small-base filter"
        )
