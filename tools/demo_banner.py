"""Prepend a demo banner to a rendered dashboard, for GitHub Pages only.

The published dashboard is the first thing most people see, and on its own
it is unlabelled: nothing on the page says the figures are fictional or
where the source lives.  Someone who is sent the link has no way back to
the repo.

This runs in the Pages workflow, never in the pipeline, so a locally
generated dashboard is untouched — a user looking at their own money
should not be told it is a demo.

Usage::

    python -m tools.demo_banner exports/dashboard.html _site/index.html
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = "https://github.com/akourk/finledger"

# Inline styles only: the dashboard is one self-contained file and the
# banner must not depend on, or perturb, its stylesheet.
BANNER = f"""<div style="
  font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  background:#1a1a2e;color:#c9c9d4;border-bottom:1px solid #2f2f45;
  padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 18px;
  align-items:center;justify-content:center;text-align:center;">
  <span><strong style="color:#a78bfa;">fin</strong> &mdash; live demo</span>
  <span style="color:#8a8a9e;">Every figure below is a fictional sample
  portfolio. No real financial data.</span>
  <a href="{REPO}" style="color:#a78bfa;font-weight:600;">Source on GitHub &rarr;</a>
</div>
"""


def inject(html: str) -> str:
    """Insert the banner immediately after <body>.

    Anchored on the opening tag rather than appended at the end so the
    banner is visible without scrolling, and so it lands outside the
    dashboard's own top bar instead of inside a flex container that
    would stretch it.
    """
    marker = "<body>"
    if marker not in html:
        raise SystemExit("demo_banner: no <body> in the rendered dashboard")
    if REPO in html:
        return html                      # already injected; stay idempotent
    return html.replace(marker, marker + "\n" + BANNER, 1)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    src, dst = Path(argv[1]), Path(argv[2])
    out = inject(src.read_text(encoding="utf-8"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(f"demo_banner: {src} -> {dst} ({len(out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
