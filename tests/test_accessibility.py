"""Accessibility guards for the generated dashboard.

WHY THIS FILE EXISTS
--------------------
The accessibility work is spread across three assets (``template.html``,
``styles.css``, ``app/*.js``) and most of it is a single attribute on a
tag inside a JavaScript template literal — the easiest kind of thing to
drop while editing the markup around it.  Nothing else in the suite would
notice: the figures would still be right, the bundle would still splice,
and the page would still render.

So this file pins the attributes.  It deliberately checks the BUNDLE —
the real ``generate_dashboard`` output — rather than the source files, so
a change that moves a table from the template into a renderer (or the
reverse) is still covered.

It is the static half of the guarantee.  The other half is
``tools/a11y_check.js``, which runs axe-core against the rendered page in
CI and catches everything that is only true at runtime: computed colour
pairings, accessible names that come from a label association rather than
an attribute, and whether ``activateTab`` keeps ``aria-selected`` in step
with the class it sets.  Neither half subsumes the other — this one needs
no browser and runs in milliseconds; that one sees what a user sees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

TABS = [
    "overview", "holdings", "transactions", "options", "retirement",
    "planning", "income", "tax", "crypto", "performance",
]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> str:
    """The real dashboard HTML, built from a minimal payload.

    Minimal on purpose: the payload must not itself contain ``<th`` or
    ``<table`` or the markup scans below would be measuring the fixture.
    """
    from src.dashboard import generate_dashboard

    tmp = tmp_path_factory.mktemp("a11y")
    json_path = tmp / "txns.json"
    out_path = tmp / "dashboard.html"
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
        "basis_totals": {},
        "cash_summary": {"net_contributed": 0, "income": 0},
        "retirement_meta": {},
        "analytics": {},
        "sector_of": {},
        "display_of": {},
        "action_catalog": {"actions": []},
        "transactions": [],
    }))
    generate_dashboard(json_path, out_path)
    return out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------
# Page-level
# ---------------------------------------------------------------------

def test_document_title_names_the_project(bundle):
    """A generic title is what a browser tab, a bookmark, and a screen
    reader's first announcement all show."""
    m = re.search(r"<title>(.*?)</title>", bundle, re.S)
    assert m, "no <title>"
    title = m.group(1).strip()
    assert "finledger" in title, title
    assert title != "Portfolio Dashboard"


def test_skip_link_is_the_first_focusable_element(bundle):
    """SC 2.4.1.  Being present is not enough — it has to come before the
    ~60 controls it exists to skip, or it skips nothing."""
    body = bundle[bundle.index("<body>"):]
    skip = body.index('href="#main-content"')
    for tag in ("<button", "<input", "<select", "<nav"):
        assert skip < body.index(tag), f"skip link comes after the first {tag}"
    assert 'id="main-content"' in body, "skip link target is missing"


def test_main_landmark_wraps_the_panels(bundle):
    main_open = bundle.index('<main id="main-content"')
    main_close = bundle.index("</main>")
    for tab in TABS:
        panel = bundle.index(f'id="tab-{tab}"')
        assert main_open < panel < main_close, f"tab-{tab} is outside <main>"


# ---------------------------------------------------------------------
# Tabs — WAI-ARIA authoring practices, tabs pattern
# ---------------------------------------------------------------------

def test_tablist_and_tabs_are_wired_to_their_panels(bundle):
    assert 'role="tablist"' in bundle
    assert re.search(r'role="tablist"[^>]*aria-label="', bundle), \
        "the tablist needs a name — there is more than one nav-like region"

    for tab in TABS:
        btn = re.search(
            r'<button[^>]*data-tab="%s"[^>]*>' % tab, bundle)
        assert btn, f"no tab button for {tab}"
        b = btn.group(0)
        assert 'role="tab"' in b, b
        assert f'aria-controls="tab-{tab}"' in b, b
        assert "aria-selected=" in b, b
        assert "tabindex=" in b, b
        assert f'id="tabbtn-{tab}"' in b, b

        panel = re.search(r'<div[^>]*id="tab-%s"[^>]*>' % tab, bundle)
        assert panel, f"no panel for {tab}"
        p = panel.group(0)
        assert 'role="tabpanel"' in p, p
        assert f'aria-labelledby="tabbtn-{tab}"' in p, p


def test_exactly_one_tab_starts_selected(bundle):
    assert bundle.count('aria-selected="true"') == 1
    assert bundle.count('aria-selected="false"') == len(TABS) - 1


def test_roving_tabindex_leaves_one_tab_stop(bundle):
    """A tablist is a single tab stop; the rest are reached with arrows."""
    tabs = re.findall(r'<button[^>]*role="tab"[^>]*>', bundle)
    assert len(tabs) == len(TABS)
    assert sum('tabindex="0"' in t for t in tabs) == 1
    assert sum('tabindex="-1"' in t for t in tabs) == len(TABS) - 1


def test_tablist_handles_arrow_home_and_end(bundle):
    """The selection state above is inert without a key handler."""
    assert "function initTabA11y" in bundle
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in bundle, f"tablist does not handle {key}"


def test_activate_tab_updates_aria_state(bundle):
    """`aria-selected` set once in the template and never again is worse
    than absent: it would name the wrong tab from the first click on."""
    fn = bundle[bundle.index("function activateTab"):]
    fn = fn[:fn.index("\n}")]
    assert "aria-selected" in fn
    assert "tabIndex" in fn


# ---------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------

def _strip_json_payload(bundle: str) -> str:
    """Markup scans should not see the embedded data blob."""
    start = bundle.index("const DATA = ")
    end = bundle.index("\n", start)
    return bundle[:start] + bundle[end:]


def test_every_th_declares_a_scope(bundle):
    """Header association is otherwise left to the browser's guess, which
    is only reliable for the simplest tables — and several of these are a
    year-by-year pivot with two header rows and column groups."""
    src = _strip_json_payload(bundle)
    bad = [m.group(0) for m in re.finditer(r"<th(?![A-Za-z])[^>]*>", src)
           if "scope=" not in m.group(0)]
    assert not bad, f"{len(bad)} <th> without scope, e.g. {bad[:3]}"

    scopes = set(re.findall(r'<th[^>]*scope="([a-z]+)"', src))
    assert scopes <= {"col", "row", "colgroup", "rowgroup"}, scopes


def test_every_table_has_a_caption(bundle):
    """A caption is a table's accessible name.  Most of these tables sit
    under a visible heading, so the caption is visually hidden — but a
    screen-reader user listing the tables on a tab gets nothing without
    it."""
    src = _strip_json_payload(bundle)
    opens = [m for m in re.finditer(r"<table(?![A-Za-z])[^>]*>", src)]
    assert len(opens) >= 30, f"only found {len(opens)} tables — scan broke"
    missing = []
    for m in opens:
        after = src[m.end():m.end() + 400]
        if "<caption" not in after.split("<tr")[0]:
            missing.append(src[max(0, m.start() - 60):m.end()])
    assert not missing, f"{len(missing)} table(s) without a caption: {missing[:2]}"


def test_sortable_headers_are_keyboard_operable(bundle):
    """They are <th> with a click handler; without a tab stop and a key
    handler the sort is mouse-only (SC 2.1.1)."""
    src = _strip_json_payload(bundle)
    assert 'aria-sort=' in src, "no sort state is exposed"
    clickable = re.findall(r'<th[^>]*(?:data-hcol|data-bcol|data-col=|board-th|_setLtSort)[^>]*>', src)
    assert clickable, "sortable header scan found nothing"
    for th in clickable:
        assert 'tabindex="0"' in th, th[:120]
    # And the handler that turns that tab stop into an activation.
    assert re.search(r"th\[tabindex=.0.\]", src), "no key handler for header sort"


# ---------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------

_LABEL_FOR = None


def test_every_form_control_has_an_accessible_name(bundle):
    """A control with only a placeholder is announced as an unlabelled
    edit field, and `title` alone is a fallback most tooling flags."""
    src = _strip_json_payload(bundle)
    labelled_ids = set(re.findall(r'<label[^>]*for="([^"]+)"', src))
    problems = []
    for m in re.finditer(r"<(input|select|textarea)(?![A-Za-z])[^>]*>", src):
        tag = m.group(0)
        if 'type="hidden"' in tag:
            continue
        if "aria-label" in tag or "aria-labelledby" in tag:
            continue
        cid = re.search(r'id="([^"]+)"', tag)
        if cid and cid.group(1) in labelled_ids:
            continue
        # A control nested inside <label>…</label> takes its name from
        # the wrapping element's text.
        before = src[max(0, m.start() - 600):m.start()]
        if "<label" in before and "</label>" not in before[before.rindex("<label"):]:
            continue
        problems.append(tag[:110])
    assert not problems, f"{len(problems)} unnamed control(s): {problems}"


def test_every_chart_svg_has_a_label(bundle):
    """`<title>` elements inside an SVG name individual marks; the chart
    itself is announced as 'graphic' with no name without this."""
    src = _strip_json_payload(bundle)
    svgs = re.findall(r"<svg(?![A-Za-z])[^>]*>", src)
    assert len(svgs) >= 8, f"only found {len(svgs)} svg elements"
    unnamed = [s[:110] for s in svgs if "aria-label" not in s]
    assert not unnamed, f"{len(unnamed)} unnamed chart(s): {unnamed}"
    assert all('role="img"' in s for s in svgs), "chart svgs need role=img"


# ---------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------

def _relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
           for c in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _palette(bundle: str) -> dict:
    root = bundle[bundle.index(":root {"):]
    root = root[:root.index("}")]
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{3,6});", root))


def test_palette_tokens_meet_4_5_to_1_on_every_surface_they_are_used_on(bundle):
    """SC 1.4.3.  This is the check that actually holds the line: the
    dashboard has one dark palette, so a single token appears at hundreds
    of sites and one careless value fails all of them at once.  `#777` did
    exactly that — it was the colour of every label, hint and sub-figure
    on the page, at 3.9:1."""
    p = _palette(bundle)
    surfaces = [p["bg"], p["surface"], p["border"]]
    for token in ("text", "text-dim", "green", "red", "yellow", "accent"):
        for surface in surfaces:
            ratio = _contrast(p[token], surface)
            assert ratio >= 4.5, (
                f"--{token} ({p[token]}) on {surface} is {ratio:.2f}:1")


def test_text_on_the_accent_fill_is_readable(bundle):
    """Active pills invert: accent becomes the background.  White text on
    it reads at 2.7:1."""
    p = _palette(bundle)
    ratio = _contrast(p["on-accent"], p["accent"])
    assert ratio >= 4.5, f"--on-accent on --accent is {ratio:.2f}:1"


def test_loss_is_not_signalled_by_colour_alone(bundle):
    """SC 1.4.1.  Numeric figures carry the sign, so the colour is
    reinforcement — except in the daily P&L strip, where every bar grows
    upward from a shared baseline and the fill was the only channel."""
    assert "fmtSigned" in bundle
    assert re.search(r"\.daily-pnl-bar\.neg \.bar\s*\{[^}]*repeating-linear-gradient",
                     bundle, re.S), "negative P&L bars need a non-colour cue"
    # And each bar carries its signed value as an accessible name.
    assert re.search(r'class="daily-pnl-bar[^"]*"[^>]*\n?[^>]*aria-label=', bundle)


# ---------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------

def test_focus_is_visible_and_drawn_outside_the_control(bundle):
    """SC 2.4.7 + 1.4.11.  `outline-offset` is the load-bearing half: an
    accent-coloured ring drawn ON an active accent-filled pill would be
    invisible, so the ring is white and sits on the page background."""
    assert ":focus-visible" in bundle
    focus_block = re.search(r":focus-visible\s*\{([^}]*)\}", bundle)
    assert focus_block, "no :focus-visible rule"
    css = focus_block.group(1)
    assert "outline" in css and "none" not in css
    assert "outline-offset" in css
    assert re.search(r"outline:\s*2px solid #ffffff", css)


def test_visually_hidden_utility_is_clipped_not_hidden(bundle):
    """`display:none` would take the captions out of the accessibility
    tree too, which is the opposite of the point."""
    rule = re.search(r"\.sr-only\s*\{([^}]*)\}", bundle)
    assert rule, "no .sr-only utility"
    css = rule.group(1)
    assert "display: none" not in css
    assert "clip" in css


def test_scrollable_regions_become_focusable(bundle):
    """SC 2.1.1.  Whether a wrapper scrolls depends on the viewport, so
    this is measured at runtime rather than declared — the guard is that
    the measurement exists and runs after each tab renders."""
    assert "function applyScrollRegionFocus" in bundle
    assert "scrollWidth" in bundle and "scrollHeight" in bundle
    activate = bundle[bundle.index("function activateTab"):]
    assert "applyScrollRegionFocus" in activate[:activate.index("\n}")]


# ---------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------

def test_ci_runs_the_axe_gate():
    """Half of what this module promises is only checkable in a browser.
    If the axe job stops running, these tests would keep passing while
    the runtime guarantees rot."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "tools" / "a11y_check.js").exists()
    wf = (root / ".github" / "workflows" / "accessibility.yml").read_text(encoding="utf-8")
    assert "tools/a11y_check.js" in wf
    assert "axe-core" in wf
    assert "samples/portfolio.snapshot.json" in wf, \
        "the gate must build from the synthetic sample, never from data/"
