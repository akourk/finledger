"""The dashboard must not scroll sideways on a phone.

Measured before the fix, at a 375px viewport: `scrollWidth` 635 against
`clientWidth` 375. The whole page shifted horizontally on every tab.

**These are static assertions on the CSS, and that is a real limitation
worth stating.** The actual verification was done in a browser at 375px,
768px and 1400px — the numbers in this docstring came from there, not
from these tests. What a stylesheet grep can do is stop a specific rule
being deleted or reverted; what it cannot do is notice a NEW overflow
from unrelated markup. Treat a green run here as "the known causes are
still handled", not "the dashboard is responsive".

The four causes, all found by measuring rather than reading:

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

    def test_overview_grid_children_have_min_width_zero(self, css):
        body = _rule(css, ".overview-split > *,\n  .overview-split-2 > *")
        assert re.search(r"min-width:\s*0", body), (
            "grid items can no longer shrink below their content, so one "
            "wide table will stretch the page again"
        )

    def test_the_rule_covers_both_overview_grids(self, css):
        assert ".overview-split > *" in css
        assert ".overview-split-2 > *" in css


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
