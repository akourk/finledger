"""Add public-demo context and sharing metadata to a generated dashboard.

Usage: python -m tools.demo_banner exports/dashboard.html _site/index.html
Only the isolated demo builder/deployment uses this transformation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from tools.build_sample_snapshot import AS_OF_DATE

REPO = "https://github.com/akourk/finledger"
PREVIEW_IMAGE = "https://raw.githubusercontent.com/akourk/finledger/main/docs/img/dashboard.png"
DESCRIPTION = "A local-first financial ledger that turns broker exports into reconciled holdings, cost basis, and portfolio analytics. Explore a fictional portfolio."

BANNER = f"""<header id="demo-intro" class="demo-intro" aria-label="About this demo">
  <div class="demo-heading"><strong>finledger</strong> &mdash; live demo
    <span class="demo-badge">Fictional portfolio · {AS_OF_DATE.strftime('%b %d, %Y')}</span></div>
  <p>From scattered broker exports to one reconciled financial ledger.</p>
  <div class="demo-links"><a href="#about-project" onclick="document.getElementById('about-project').open=true">About this project</a>
    <a href="{REPO}">Source on GitHub &rarr;</a>
    <a href="{REPO}/blob/main/docs/ENGINEERING_CASE_STUDY.md">Engineering case study</a></div>
  <details id="about-project">
    <summary>How it works and what to explore</summary>
    <p>Python normalizes broker CSVs, reconciles transfers, tracks tax lots, and
    computes portfolio history. A self-contained JavaScript dashboard makes the
    results explorable. Data stays local; this public example uses fictional
    transactions and illustrative synthetic prices, with a fixed snapshot date.</p>
    <p>Try a historical date in Holdings, compare lot methods in Tax, or follow
    the rollover in Performance. The reconciliation panel includes one explicitly
    explained statement timing difference to demonstrate an auditable exception.</p>
    <p>Planning and tax projections illustrate the software and are estimates.
    Rebuilding this demo reproduces the same inputs and dates; reloading does
    not fetch market prices.</p>
  </details>
</header>
"""
STYLE = """<style id="demo-styles">
.demo-intro{font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#141429;color:#dddde9;border-bottom:1px solid #373753;padding:18px 24px}
.demo-intro p{margin:7px 0;max-width:1000px}.demo-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:#eeeef8;font-size:16px}.demo-heading strong{color:#c4b5fd}.demo-badge{font-size:12px;color:#dddde9;border:1px solid #595975;border-radius:20px;padding:2px 9px}.demo-links{display:flex;flex-wrap:wrap;gap:8px 22px}.demo-intro a{color:#c4b5fd;text-underline-offset:3px}.demo-intro summary{cursor:pointer;color:#dddde9;font-weight:600;padding:8px 0}.demo-intro details{margin-top:5px;max-width:1100px}.demo-intro :focus-visible{outline:2px solid #c4b5fd;outline-offset:4px}
@media(max-width:600px){.demo-intro{padding:14px 16px}.demo-heading{font-size:15px}}
</style>"""
FAVICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%237c3aed'/%3E%3Cpath d='M18 46V18h30M18 31h23' fill='none' stroke='white' stroke-width='7'/%3E%3C/svg%3E"
HEAD = f"""<meta name="description" content="{DESCRIPTION}">
<meta property="og:title" content="finledger — Interactive financial ledger demo">
<meta property="og:description" content="{DESCRIPTION}">
<meta property="og:type" content="website">
<meta property="og:url" content="https://akourk.github.io/finledger/">
<meta property="og:image" content="{PREVIEW_IMAGE}">
<meta property="og:image:width" content="1440">
<meta property="og:image:height" content="1100">
<meta property="og:image:alt" content="finledger dashboard showing a clearly labeled fictional portfolio as of June 30, 2026">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{PREVIEW_IMAGE}">
<meta name="twitter:image:alt" content="finledger dashboard showing a clearly labeled fictional sample portfolio">
<link rel="icon" href="{FAVICON}">
{STYLE}
"""
_SKIP_LINK_END = 'href="#main-content">Skip to dashboard content</a>'


def inject(html: str) -> str:
    if 'id="demo-intro"' in html:
        return html
    if _SKIP_LINK_END not in html:
        raise ValueError("demo dashboard is missing its keyboard skip link")
    if "</head>" not in html:
        raise ValueError("demo dashboard is missing its document head")
    html = html.replace(_SKIP_LINK_END, _SKIP_LINK_END + "\n" + BANNER, 1)
    html = html.replace("</head>", HEAD + "</head>", 1)
    html = html.replace(
        '<div class="subtitle">Generated: <span id="generated"></span><span id="pricesAsOf"></span></div>',
        f'<div class="subtitle">Fixed sample: {AS_OF_DATE.strftime("%B %d, %Y")} · illustrative prices'
        '<span id="generated" hidden></span><span id="pricesAsOf" hidden></span></div>', 1)
    html = re.sub(r"<title>.*?</title>", "<title>finledger — Interactive financial ledger demo</title>", html, count=1)
    # A static public snapshot has no data refresh service behind it.
    html = re.sub(r'<button\b[^>]*\bid="topBarRefresh"[^>]*>.*?</button>',
                  '<button class="top-bar-refresh" id="topBarRefresh" title="Reload this fixed fictional snapshot; market prices are not fetched." onclick="window.location.reload()">↻ Reload demo</button>',
                  html, count=1, flags=re.S)
    return html


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    src, dst = Path(argv[1]), Path(argv[2])
    out = inject(src.read_text(encoding="utf-8"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8", newline="\n")
    print(f"demo_banner: wrote {len(out):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
