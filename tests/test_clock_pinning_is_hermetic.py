"""A test that pins the clock must pin the calendar too.

`prices._today()` is `_now_utc().astimezone().date()` — it converts the
instant to the **runner's local zone**. That is correct in production
(`covered_end` must never name a date in the future *for the user*), and
it means a test that pins only `_now_utc` is not hermetic: the same UTC
instant is a different calendar day in different places.

**This shipped and broke CI while passing on every developer machine
west of Greenwich.** Pinning `2026-08-06T00:00Z`:

* at UTC-7 → local date 2026-08-05. `covered_end` equals today, so the
  bar is unsettled and a refetch range comes back.
* at UTC+0 (GitHub Actions) → local date 2026-08-06. `covered_end` is
  yesterday, so the bar is settled and nothing comes back.

The assertion expected the first and CI got the second. Nothing about
the failure pointed at a timezone; it read as a plain logic error.

The fix is for the pinning helpers to fix the zone as well as the
instant. This module guards that they keep doing so — a static check,
because the failure only reproduces on a machine in a different zone
from the one you are sitting at.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TESTS_DIR = Path(__file__).resolve().parent


class TestTheProductionRuleIsStillZoneDependent:
    """The premise. If `_today` ever stops consulting the ambient zone,
    this whole module is unnecessary and should go."""

    def test_today_derives_from_the_ambient_zone(self, isolated_workdir):
        from src import prices as P

        src = inspect.getsource(P._today)
        assert "astimezone()" in src, (
            "`_today` no longer converts through the ambient local zone. "
            "If that is deliberate, the pinning helpers and this module "
            "can be simplified."
        )

    def test_today_follows_the_pinned_instant(self, isolated_workdir,
                                              monkeypatch):
        from src import prices as P

        monkeypatch.setattr(
            P, "_now_utc",
            lambda: datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc))
        # Whatever the runner's zone, this is one of two adjacent days —
        # which is precisely the ambiguity the helpers must remove.
        assert P._today() in (date(2026, 8, 5), date(2026, 8, 6))


class TestPinnedHelpersAreZoneIndependent:

    def test_settle_gating_pin_yields_the_eastern_date(self, isolated_workdir,
                                                       monkeypatch):
        """The exact case CI got wrong.

        The helper's comments are written in ET, so the pinned calendar
        day must be the ET one — 2026-08-06T00:00Z is 20:00 ET on the
        5th — on every machine.
        """
        from tests.test_prices import TestSettledThroughGating
        from src import prices as P

        TestSettledThroughGating._pin(monkeypatch, "2026-08-06T00:00:00")
        assert P._today() == date(2026, 8, 5), (
            "the pinned 'today' followed the runner's zone again; this is "
            "the assertion that passed locally and failed on CI"
        )

    def test_the_pin_is_a_pure_function_of_the_instant(self, isolated_workdir,
                                                        monkeypatch):
        """No ambient input: the same instant must give the same date
        however the machine is configured."""
        from tests.test_prices import TestSettledThroughGating
        from src import prices as P

        TestSettledThroughGating._pin(monkeypatch, "2026-08-06T00:00:00")
        expected = (datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
                    .astimezone(ZoneInfo("America/New_York")).date())
        assert P._today() == expected

    def test_the_pin_covers_both_seams(self, isolated_workdir, monkeypatch):
        """`_now_utc` alone is not enough — that was the bug."""
        from tests.test_prices import TestSettledThroughGating
        from src import prices as P

        TestSettledThroughGating._pin(monkeypatch, "2026-08-05T15:00:00")
        assert P._now_utc() == datetime(2026, 8, 5, 15, 0,
                                        tzinfo=timezone.utc)
        assert P._today() == date(2026, 8, 5)


def _patches(node: ast.AST, attr: str) -> bool:
    """Whether this node contains a `setattr(..., "<attr>", ...)` CALL.

    Deliberately not a substring search over `ast.dump`: the first
    version of this guard did that, and `_pin`'s docstring discusses
    `_today` at length — so deleting the actual pin left the docstring
    behind and the guard stayed green. Mutation caught it. A guard that
    matches prose instead of code cannot fire.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "setattr":
            continue
        for a in sub.args:
            if isinstance(a, ast.Constant) and a.value == attr:
                return True
    return False


def _functions_patching(module_src: str, attr: str) -> list[str]:
    """Names of functions that monkeypatch `attr`."""
    return [n.name for n in ast.walk(ast.parse(module_src))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _patches(n, attr)]


class TestNoTestPinsTheInstantAlone:
    """The static guard.

    Any test helper that pins `_now_utc` must also pin `_today`, or it
    reintroduces exactly this bug in a new place — and the failure will
    again appear only on a machine in another zone.
    """

    def test_every_now_utc_pin_also_pins_today(self):
        offenders = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            # This module is the guard: it names both seams as strings to
            # scan for them, and deliberately pins ONLY the instant in one
            # place to demonstrate the ambiguity (that test asserts the
            # result is either adjacent day, so it stays hermetic).
            if path.name == Path(__file__).name:
                continue
            src = path.read_text(encoding="utf-8")
            if "_now_utc" not in src:
                continue
            pins_today = set(_functions_patching(src, "_today"))
            for fn in _functions_patching(src, "_now_utc"):
                if fn not in pins_today:
                    offenders.append(f"{path.name}::{fn}")
        assert not offenders, (
            "these pin `_now_utc` without pinning `_today`, so the calendar "
            f"day they test depends on the runner's timezone: {offenders}. "
            "Pin both (see TestSettledThroughGating._pin)."
        )

    def test_the_guard_can_actually_fire(self):
        """Not-vacuous: the detector must find the real pinning helpers,
        or it is scanning nothing."""
        src = (TESTS_DIR / "test_prices.py").read_text(encoding="utf-8")
        found = _functions_patching(src, "_now_utc")
        assert found, "the AST scan found no `_now_utc` pins at all"
