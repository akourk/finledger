"""The dashboard must not scroll sideways on a phone.

Measured before the fix, at a 375px viewport: `scrollWidth` 635 against
`clientWidth` 375. The whole page shifted horizontally on every tab.

**These are static assertions on the CSS, and that is a real limitation
worth stating.** The actual verification was done in a browser at 320px,
375px, 768px and 1400px — the numbers here came from there, not from
these tests. What a stylesheet grep can do is stop a specific rule being
deleted or reverted; what it cannot do is notice a NEW overflow from
unrelated markup. Treat a green run here as "the known causes are still
handled", not "the dashboard is responsive".

**A methodology note that cost a round of false confidence.** The first
sweep drove tabs with `location.hash`, which does NOT trigger the lazy
per-tab renderers — most panels were still empty, so a clean result
meant almost nothing. Clicking the actual tab buttons is what made the
sweep real, and it immediately surfaced overflows the hash-driven pass
had reported as clean. When a UI check passes suspiciously early, ask
whether the UI was actually there.

The causes, all found by measuring rather than reading:

1. **`.panel` inside `.overview-split`** — a grid item defaults to
   `min-width: auto` and so refuses to shrink below its content. One
   wide table stretched the item, the track, the grid, and the page:
   625px of panel inside a 355px column. This was the big one.
2. **`.mini-scroll`** declared `overflow-y: auto` and no `overflow-x`,
   so a table wider than the panel had nowhere to scroll and pushed
   instead. (Note that `overflow-y: auto` alone makes *computed*
   `overflow-x` become `auto` too — but only once the element is a
   scroll container in that axis, which is why the explicit declaration
   still changed behaviour here.)
3. **`.tabnav`** bleeds `-20px` to cancel the desktop body padding.
   Mobile drops that padding to 10px, so the nav hung 10px off each
   edge.
4. **`.top-bar-title`** is `flex: 0 0 auto`. `min-width: 0` alone did
   nothing — `flex-shrink: 0` is what actually refuses. Both are needed,
   which is the kind of thing only measuring tells you.
5. **Hidden tooltips still occupy layout.** 30 `opacity: 0` tooltips,
   each ~170px wide and centred on a ~23px bar, put a scrollbar on the
   page at TABLET width — the most commonly-hit of these.
6. **`.mini-table` in a bare `.panel`** — nowrap cells, no scroll
   container anywhere in the chain.
7. **Nowrap flex rows** (`.date-range-group`, `.bracket-summary`,
   `.if-totals`) holding content wider than their panel.
8. **An inline `grid-template-columns`** on the FIRE stat row pinned
   five columns; an inline style beats the media query, so the mobile
   2-column rule never applied.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = (Path(__file__).resolve().parent.parent
            / "src" / "dashboard" / "styles.css")
MOBILE_BREAKPOINT = "@media (max-width: 720px)"


@pytest.fixture(scope="module")
def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def mobile_block(css) -> str:
    """The body of the ≤720px media query."""
    i = css.index(MOBILE_BREAKPOINT)
    depth, j = 0, css.index("{", i)
    for k in range(j, len(css)):
        if css[k] == "{":
            depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0:
                return css[j:k]
    raise AssertionError("unterminated mobile media query")


def _rule(block: str, selector: str) -> str:
    """The declaration body of `selector` within `block`."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", block)
    assert m, f"rule {selector!r} not found"
    return m.group(1)


class TestGridItemsCanShrink:
    """Cause 1 — the largest by far."""

    GRIDS = [".overview-split > *", ".overview-split-2 > *",
             ".mc-stats > *", ".drawdown-stats > *",
             ".concentration-grid > *"]

    def test_grid_children_may_shrink(self, css):
        """One `min-width: 0` rule covers every card/panel grid.

        Each entry is a grid whose children were, or could be, held wider
        than their track by their own content.
        """
        i = css.index(".overview-split > *")
        block = css[i:css.index("}", i) + 1]
        for sel in self.GRIDS:
            assert sel in block, (
                f"{sel} dropped out of the min-width:0 rule — its cards can "
                "hold the grid wider than the viewport again"
            )
        assert re.search(r"min-width:\s*0", block)


class TestWideContentScrollsInsteadOfPushing:
    """Cause 2."""

    def test_mini_scroll_scrolls_horizontally(self, css):
        body = _rule(css, ".mini-scroll")
        assert re.search(r"overflow-x:\s*auto", body), (
            ".mini-scroll no longer scrolls horizontally — a table wider "
            "than its panel will push the layout instead"
        )

    def test_it_still_scrolls_vertically(self, css):
        """Near-miss: the vertical scroll and its sticky header are the
        reason this container exists."""
        body = _rule(css, ".mini-scroll")
        assert re.search(r"overflow-y:\s*auto", body)
        assert ".mini-scroll thead th" in css


class TestFullBleedMatchesTheMobilePadding:
    """Cause 3 — a constant duplicated across a breakpoint."""

    def test_body_padding_and_tabnav_bleed_agree(self, css, mobile_block):
        pad = _rule(mobile_block, "body")
        m = re.search(r"padding:\s*(\d+)px", pad)
        assert m, "mobile body padding is no longer a simple px value"
        padding = int(m.group(1))

        nav = _rule(mobile_block, ".tabnav")
        ml = re.search(r"margin-left:\s*-(\d+)px", nav)
        assert ml, "the mobile tabnav no longer corrects its bleed margin"
        assert int(ml.group(1)) == padding, (
            f"tabnav bleeds -{ml.group(1)}px against {padding}px of body "
            "padding — it will hang off the edge by the difference"
        )

    def test_the_desktop_bleed_is_unchanged(self, css):
        """The base rule is correct for the desktop padding and must not
        be 'fixed' along with the mobile one."""
        base = _rule(css, "  .tabnav")
        assert "-20px" in base


class TestTheTitleCanShrink:
    """Cause 4 — and the one that needed two declarations."""

    def test_mobile_title_may_shrink(self, mobile_block):
        body = _rule(mobile_block, ".top-bar-title")
        assert re.search(r"flex-shrink:\s*1", body), (
            "flex-shrink is what actually refuses to let the header "
            "narrow; min-width alone does nothing here"
        )

    def test_mobile_title_also_clears_the_automatic_minimum(self,
                                                            mobile_block):
        body = _rule(mobile_block, ".top-bar-title")
        assert re.search(r"min-width:\s*0", body), (
            "without this the automatic minimum re-imposes the same floor "
            "that flex-shrink just released"
        )


class TestTheStatusSummaryWraps:
    def test_dh_summary_wraps(self, css):
        body = _rule(css, ".dh-summary")
        assert re.search(r"flex-wrap:\s*wrap", body), (
            "the data-health chips are back on one nowrap line and will "
            "overflow a narrow panel"
        )


class TestNoWideMinimumsOutsideAScrollContainer:
    """A lint for the class of bug rather than its instances.

    A `min-width` wider than a phone inside the MOBILE block is exactly
    what causes this, and it is legitimate in one place: a table that
    scrolls inside `.table-wrap`. Anything else is almost certainly the
    bug coming back under a new selector.
    """

    ALLOWED = {".table-wrap table"}   # scrolls inside its own container

    def test_mobile_block_declares_no_unscrollable_wide_minimum(
            self, mobile_block):
        offenders = []
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", mobile_block):
            selector = " ".join(m.group(1).split())
            for w in re.finditer(r"min-width:\s*(\d+)px", m.group(2)):
                if int(w.group(1)) > 375 and selector not in self.ALLOWED:
                    offenders.append((selector, w.group(1)))
        assert not offenders, (
            f"selector(s) declare a min-width wider than a phone inside the "
            f"mobile breakpoint: {offenders}. If the element genuinely needs "
            "the width, put it inside a container with overflow-x: auto and "
            "add it to ALLOWED."
        )

    def test_the_lint_can_actually_fire(self, mobile_block):
        """Not-vacuous guard: the allowlisted rule must really be present,
        or the lint is scanning nothing."""
        assert re.search(r"min-width:\s*560px", mobile_block), (
            "the known wide-table rule is gone, so this lint no longer "
            "proves it is scanning the right block"
        )


class TestHiddenTooltipsLeaveTheLayout:
    """An `opacity: 0` tooltip still occupies space and still counts
    toward `scrollWidth`. Thirty of them, each ~170px wide and centred on
    a ~23px bar, put a horizontal scrollbar on the page at tablet width
    with nothing visible to scroll to."""

    def test_the_hidden_state_is_display_none(self, css):
        body = _rule(css, ".daily-pnl-bar .tip")
        # Comments explain the old approach by name, so strip them before
        # asserting the old approach is gone.
        decls = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        assert re.search(r"display:\s*none", decls), (
            "the hidden tooltip is back in the layout and will widen the "
            "page again"
        )
        assert not re.search(r"opacity:\s*0", decls), (
            "opacity:0 does not remove an element from layout — that is "
            "exactly the bug this replaced"
        )

    def test_hover_still_reveals_it(self, css):
        body = _rule(css, ".daily-pnl-bar:hover .tip")
        assert re.search(r"display:\s*block", body)


class TestWideTablesScrollThemselves:
    """`.mini-table` cells are `white-space: nowrap`, and most of these
    tables sit directly inside a `.panel` with no scroll container."""

    def test_mini_tables_scroll_at_mobile_width(self, mobile_block):
        body = _rule(mobile_block, ".mini-table")
        assert re.search(r"display:\s*block", body)
        assert re.search(r"overflow-x:\s*auto", body)

    def test_tables_already_in_a_scroll_container_are_exempt(self, mobile_block):
        """Near-miss: turning one of THESE into a block box would break
        its container's sticky header and its own horizontal scroll."""
        assert ".mini-scroll .mini-table" in mobile_block
        i = mobile_block.index(".mini-scroll .mini-table")
        body = mobile_block[i:mobile_block.index("}", i)]
        assert re.search(r"display:\s*table", body)


class TestNowrapRowsWrap:
    """Flex rows default to `nowrap`; several held content wider than a
    narrow panel and pushed the page."""

    @pytest.mark.parametrize("selector", [
        ".dh-summary", ".date-range-group", ".bracket-summary",
        ".income-forecast .if-totals",
    ])
    def test_row_wraps(self, css, selector):
        body = _rule(css, selector)
        assert re.search(r"flex-wrap:\s*wrap", body), (
            f"{selector} is nowrap again and will overflow a narrow panel"
        )


class TestInlineGridsDoNotPinAColumnCount:
    """An inline `grid-template-columns` beats the stylesheet's mobile
    rule, so a fixed `repeat(N,1fr)` survives all the way to phone width.
    That is how the FIRE row kept five columns at 375px and pushed its
    last card off the screen."""

    def test_fire_stats_use_auto_fit(self):
        js = (Path(__file__).resolve().parent.parent / "src" / "dashboard"
              / "app" / "50-retirement.js").read_text(encoding="utf-8")
        assert "repeat(auto-fit" in js, (
            "the FIRE stat row pins its column count inline again; the "
            "media query cannot override it and the last card leaves the "
            "screen"
        )
        assert "cards.length},1fr)" not in js, (
            "a card-count-derived column template is back"
        )