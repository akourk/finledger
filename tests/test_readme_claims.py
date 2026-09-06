"""Guards for the claims the README makes about the project.

A README is the one file nobody re-reads, so its factual claims rot
quietly: a badge that overstates the suite, a project name that only got
renamed in two of the three places it appears.  Both are cheap to pin.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_test_count_badge_is_not_an_overclaim():
    """The badge says "N+"; the suite has to actually clear N."""
    m = re.search(r"badge/tests-(\d+)%2B", _readme())
    assert m, "no test-count badge in the README"
    claimed = int(m.group(1))

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    m = re.search(r"(\d+) tests collected", proc.stdout)
    if not m:
        pytest.skip(f"could not read a collection count: {proc.stdout[-300:]}")
    actual = int(m.group(1))
    assert actual >= claimed, (
        f"README badge claims {claimed}+ tests; collection found {actual}")


def test_the_project_has_one_name():
    """It is called `finledger` in the README, in the published demo's
    banner, and in the dashboard's own <title>.  It used to be called
    `fin` in the first two and nothing in the third."""
    readme = _readme()
    assert readme.splitlines()[0].strip() == "# finledger"

    banner = (ROOT / "tools" / "demo_banner.py").read_text(encoding="utf-8")
    assert "finledger</strong> &mdash; live demo" in banner
    assert "akourk/finledger" in banner

    template = (ROOT / "src" / "dashboard" / "template.html").read_text(encoding="utf-8")
    assert re.search(r"<title>finledger", template)


def test_readme_screenshots_exist():
    """A broken image in the first screenful is worse than none."""
    readme = _readme()
    for path in re.findall(r"\((docs/img/[^)]+\.png)\)", readme):
        assert (ROOT / path).exists(), f"README references missing {path}"
