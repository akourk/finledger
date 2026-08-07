"""F-019: the Performance anchor cards must not read as addends.

The five lifetime cards — Total Return, Realized, Unrealized, Net
Contributed, Fees Paid — used to sit in one flat row of equal peers.
The obvious reading is `Realized + Unrealized = Total Return`, and it is
wrong, by a lot.

**There is no clean sum that reaches Total Return**, which is why the fix
is presentational rather than arithmetic. Total Return is
`value − net_contributed`. Realized and Unrealized are lot-level views.
They diverge because sale proceeds get *redeployed*: sell at a gain, buy
something else, and that gain now sits inside the cost basis of a
position you still hold — counted in neither Realized (the lot is
closed and its gain already booked) nor Unrealized (which measures only
the movement since the new purchase). Income arrives as cash without
being a gain on any lot, and cash outside Savings accounts is not in the
holdings value at all.

Measured on the shipped sample, Realized + Unrealized falls short of
Total Return by roughly 16% of the total. That is not a rounding gap a
reader would shrug off — it is large enough to look like a bug in the
dashboard, which is exactly the problem.

So: the headline sits on its own row carrying the one identity that DOES
hold exactly, and the components sit below a caption saying they do not
sum. These tests pin that structure, because it is the kind of thing a
later layout tidy-up would quietly flatten back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "src" / "dashboard" / "app"
PERF = APP / "90-performance.js"
CORE = APP / "30-overview.js"
CSS = Path(__file__).resolve().parent.parent / "src" / "dashboard" / "styles.css"


@pytest.fixture(scope="module")
def perf_src():
    return PERF.read_text(encoding="utf-8")


class TestTheHeadlineIsSeparated:

    def test_total_return_is_its_own_card_row(self, perf_src):
        assert "anchorHeadline" in perf_src, (
            "Total Return is no longer rendered separately from the "
            "component cards — a flat row invites Realized + Unrealized"
        )
        assert re.search(r"_renderCardRow\(anchorHeadline\)", perf_src)

    def test_the_components_no_longer_include_total_return(self, perf_src):
        """The component list must not carry the headline back into the
        same row."""
        block = perf_src[perf_src.index("const anchorCards = ["):]
        block = block[:block.index("];")]
        assert "'Total Return'" not in block

    def test_a_caption_sits_between_the_two_rows(self, perf_src):
        i_head = perf_src.index("_renderCardRow(anchorHeadline)")
        i_cap = perf_src.index("stats-caption", i_head)
        i_cards = perf_src.index("_renderCardRow(anchorCards)", i_head)
        assert i_head < i_cap < i_cards, (
            "the caption is not between the headline and the components, "
            "so it does not read as applying to them"
        )

    def test_the_caption_says_they_do_not_sum(self, perf_src):
        i = perf_src.index("stats-caption")
        caption = perf_src[i:i + 400]
        assert "not</strong> add up" in caption or "not add up" in caption, (
            "the caption no longer states the thing it exists to state"
        )


class TestTheIdentityThatHoldsIsVisible:
    """The tooltip already explained this. A tooltip is invisible until
    you hover the exact card that confused you — which you only do if you
    already suspect something is wrong."""

    def test_total_return_carries_a_visible_note(self, perf_src):
        block = perf_src[perf_src.index("const anchorHeadline = ["):]
        block = block[:block.index("];")]
        assert "note:" in block, (
            "Total Return lost its visible definition line; the "
            "explanation is hover-only again"
        )

    def test_the_note_shows_value_minus_contributed(self, perf_src):
        block = perf_src[perf_src.index("const anchorHeadline = ["):]
        block = block[:block.index("];")]
        assert "_whole_value" in block and "_whole_netContrib" in block, (
            "the note no longer shows the two operands, so it asserts the "
            "identity without letting the reader check it"
        )

    def test_the_tooltip_states_it_is_not_realized_plus_unrealized(self,
                                                                   perf_src):
        i = perf_src.index("_totalReturnTitle")
        tip = perf_src[i:i + 1200]
        assert "NOT Realized + Unrealized" in tip


class TestTheNoteMechanismIsGeneral:
    """`note` was added to the shared card renderer, so it must be
    optional — every other tab passes cards without one."""

    def test_the_renderer_supports_an_optional_note(self):
        src = CORE.read_text(encoding="utf-8")
        i = src.index("function _renderStatCards")
        body = src[i:i + 900]
        assert "c.note" in body
        assert "stat-note" in body

    def test_a_card_without_a_note_renders_nothing_extra(self):
        """Guard: the note must be conditional. An unconditional empty
        div would add vertical space to every stat card on every tab."""
        src = CORE.read_text(encoding="utf-8")
        i = src.index("function _renderStatCards")
        body = src[i:i + 900]
        assert re.search(r"c\.note\s*\?", body), (
            "the note is not conditional on being supplied"
        )

    def test_the_styles_exist(self):
        css = CSS.read_text(encoding="utf-8")
        assert ".stat-card .stat-note" in css, (
            "the note has no styling, so it renders at full value size"
        )
        assert ".stats-caption" in css


class TestTheGapIsRealAndLarge:
    """The premise. If Realized + Unrealized happened to land close to
    Total Return, all of the above would be ceremony around a rounding
    difference — and a future reader would be right to delete it."""

    def test_the_sample_gap_is_material(self, isolated_workdir, stub_prices):
        import contextlib
        import io
        import json
        import sys
        from pathlib import Path as _P

        from src.snapshot import import_snapshot

        root = _P(__file__).resolve().parent.parent
        with contextlib.redirect_stdout(io.StringIO()):
            import_snapshot(root / "samples" / "portfolio.snapshot.json",
                            isolated_workdir / "data", overwrite=True)
        argv = sys.argv
        sys.argv = ["fin", "--skip-rename"]
        try:
            from src.main import main
            with contextlib.redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = argv

        d = json.loads((isolated_workdir / "exports" / "transactions.json")
                       .read_text(encoding="utf-8"))
        tr = d["analytics"]["header_summary"]["total_return"]
        realized = sum((t.get("realized_gain") or 0) for t in d["transactions"])
        unreal = sum((h.get("unrealized_gain") or 0)
                     for h in d["holdings_by_account"])

        gap = abs(tr - (realized + unreal))
        assert tr > 0, "sample portfolio has no return; premise gone"
        assert gap > abs(tr) * 0.02, (
            f"Realized + Unrealized now lands within 2% of Total Return "
            f"(gap {gap:.2f} on {tr:.2f}) — if that is genuinely always "
            "true, the separation and caption are unnecessary and this "
            "test should go with them"
        )
