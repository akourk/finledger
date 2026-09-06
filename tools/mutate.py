"""Targeted mutation harness — does the suite actually notice this change?

`docs/PLAN-audit.md` technique (1).  Deliberately NOT a full mutmut sweep: a
blanket run over 11k lines produces mostly noise (unreachable branches,
equivalent mutants, log strings).  Here you name a specific, meaningful
mutation and a scoped set of tests, and get one of three answers:

    CAUGHT     the suite failed -> that behaviour is genuinely protected
    SURVIVED   the suite passed -> a FINDING: missing assertion or dead code
    ERROR      the mutation didn't apply, or the baseline was already red

That third outcome is the whole reason this file is longer than the
twenty lines it looks like it should be.  A harness that reports CAUGHT
because the scoped tests were failing *before* the mutation is exactly
the vacuous-safety-net shape this audit exists to hunt (see the prime
directive).  So every run verifies the baseline is green first, and
verifies the mutation actually changed the file.

The source file is ALWAYS restored -- try/finally plus a verified
write-back.  This runs against the user's real working tree.

Single mutation:

    python -m tools.mutate --file src/prices.py \\
        --match "return min(value, _today" \\
        --replace "return max(value, _today" \\
        --tests tests/test_prices.py::TestTodayFreshness

Batch (what Segments 2/3 actually use) -- a JSON list of the same fields:

    python -m tools.mutate --spec audit/mutations/basis.json

    [{"name": "fifo->lifo consume order",
      "file": "src/basis.py",
      "match": "lots.pop(0)",
      "replace": "lots.pop()",
      "tests": "tests/test_basis.py"}]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CAUGHT = "CAUGHT"
SURVIVED = "SURVIVED"
ERROR = "ERROR"


@dataclass
class Mutation:
    name: str
    file: str
    match: str
    replace: str
    tests: str = "tests/"
    occurrence: int | None = None  # 1-based; None = require exactly one
    regex: bool = False


def _run_pytest(selector: str, timeout: int) -> tuple[bool, str]:
    """Return (passed, short_reason)."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", selector,
            "-q", "-x", "--no-header", "-p", "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    reason = tail[-1] if tail else f"exit {proc.returncode}"
    # pytest exit 5 = "no tests collected": a bad selector, not a pass.
    if proc.returncode == 5:
        return False, "NO TESTS COLLECTED (bad selector)"
    return proc.returncode == 0, reason


def _match_file_newlines(pattern: str, text: str) -> str:
    """Rewrite a pattern's newlines to whatever the file actually uses.

    `_read_exact` deliberately keeps CRLF verbatim (see its docstring), so
    a multi-line spec written with plain `\\n` matches nothing in a CRLF
    file.  The failure is loud -- "substring never matched" -- but it
    costs a round trip every time, and the spec author has no reason to
    care which line ending a given source file happens to carry.

    Detect the file's dominant ending and translate the pattern to it.
    The FILE is never touched, so byte-exact round-tripping still holds.
    """
    if "\n" not in pattern or "\r\n" in pattern:
        return pattern
    if text.count("\r\n") > text.count("\n") - text.count("\r\n"):
        return pattern.replace("\n", "\r\n")
    return pattern


def _apply(text: str, m: Mutation) -> tuple[str, int]:
    """Return (mutated_text, n_matches). Raises ValueError on ambiguity."""
    if not m.regex:
        m = replace(m, match=_match_file_newlines(m.match, text),
                    replace=_match_file_newlines(m.replace, text))
    if m.regex:
        matches = list(re.finditer(m.match, text))
        n = len(matches)
        if n == 0:
            raise ValueError(f"pattern never matched: {m.match!r}")
        if m.occurrence is None and n > 1:
            raise ValueError(
                f"pattern matched {n} times; pass occurrence to disambiguate"
            )
        idx = (m.occurrence or 1) - 1
        if idx >= n:
            raise ValueError(f"occurrence {m.occurrence} > {n} matches")
        span = matches[idx].span()
        return text[: span[0]] + re.sub(m.match, m.replace, text[span[0]: span[1]]) + text[span[1]:], n

    n = text.count(m.match)
    if n == 0:
        raise ValueError(f"substring never matched: {m.match!r}")
    if m.occurrence is None and n > 1:
        raise ValueError(
            f"substring matched {n} times; pass occurrence to disambiguate"
        )
    idx = (m.occurrence or 1) - 1
    # Replace only the chosen occurrence.
    start = -1
    for _ in range(idx + 1):
        start = text.find(m.match, start + 1)
    return text[:start] + m.replace + text[start + len(m.match):], n


def _read_exact(path: Path) -> str:
    """Read preserving line endings verbatim.

    NOT `read_text()`: that opens in text mode with universal newlines, so
    CRLF collapses to LF in memory and the paired `write_text` re-expands
    it using os.linesep.  On Windows that silently rewrites every LF file
    to CRLF -- a whole-file diff dressed up as a one-line mutation.  The
    restore check can't catch it either, because it reads back through the
    same translation and sees agreement.  Round-trip byte-exactly instead.
    """
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_exact(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run_one(m: Mutation, timeout: int, skip_baseline: bool = False) -> tuple[str, str]:
    """Apply, test, restore. Returns (verdict, detail)."""
    path = ROOT / m.file
    if not path.exists():
        return ERROR, f"no such file: {m.file}"

    original = _read_exact(path)
    original_bytes = path.read_bytes()

    try:
        mutated, _ = _apply(original, m)
    except ValueError as exc:
        return ERROR, str(exc)
    if mutated == original:
        return ERROR, "mutation was a no-op (replace == match?)"

    # Baseline: the scoped tests MUST be green before we trust a failure
    # after mutation to mean anything.
    if not skip_baseline:
        ok, reason = _run_pytest(m.tests, timeout)
        if not ok:
            return ERROR, f"baseline already red: {reason}"

    try:
        _write_exact(path, mutated)
        passed, reason = _run_pytest(m.tests, timeout)
    finally:
        _write_exact(path, original)
        # Compare BYTES, not decoded text: a newline-translation bug is
        # invisible to a str comparison that decoded through the same
        # translation.  This is the harness's one real safety property --
        # it writes to the user's live working tree.
        if path.read_bytes() != original_bytes:
            raise RuntimeError(
                f"FAILED TO RESTORE {m.file} byte-for-byte -- "
                f"restore it with `git checkout -- {m.file}` before continuing"
            )

    return (SURVIVED if passed else CAUGHT), reason


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file")
    ap.add_argument("--match")
    ap.add_argument("--replace")
    ap.add_argument("--tests", default="tests/")
    ap.add_argument("--name", default="")
    ap.add_argument("--occurrence", type=int)
    ap.add_argument("--regex", action="store_true")
    ap.add_argument("--spec", help="JSON file with a list of mutations")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument(
        "--skip-baseline",
        action="store_true",
        help="skip the pre-flight green check (faster in a batch where you "
             "already know the scope is green -- use sparingly)",
    )
    args = ap.parse_args()

    if args.spec:
        spec = json.loads((ROOT / args.spec).read_text(encoding="utf-8"))
        muts = [
            Mutation(
                name=d.get("name") or d["match"][:40],
                file=d["file"], match=d["match"], replace=d["replace"],
                tests=d.get("tests", "tests/"),
                occurrence=d.get("occurrence"), regex=d.get("regex", False),
            )
            for d in spec
        ]
    else:
        if not (args.file and args.match and args.replace is not None):
            ap.error("need --file/--match/--replace, or --spec")
        muts = [
            Mutation(
                name=args.name or args.match[:40], file=args.file,
                match=args.match, replace=args.replace, tests=args.tests,
                occurrence=args.occurrence, regex=args.regex,
            )
        ]

    results = []
    for m in muts:
        verdict, detail = run_one(m, args.timeout, args.skip_baseline)
        results.append((verdict, m, detail))
        mark = {CAUGHT: "ok  ", SURVIVED: "MISS", ERROR: "ERR "}[verdict]
        print(f"[{mark}] {verdict:8} {m.file}: {m.name}")
        if verdict != CAUGHT:
            print(f"           {detail}")

    survived = [r for r in results if r[0] == SURVIVED]
    errored = [r for r in results if r[0] == ERROR]
    print(
        f"\n{len(results)} mutation(s): "
        f"{len(results) - len(survived) - len(errored)} caught, "
        f"{len(survived)} SURVIVED, {len(errored)} error"
    )
    if survived:
        print("\nSurvivors are findings -- log them:")
        for _, m, _d in survived:
            print(f"  - {m.file}: {m.name}  (tests: {m.tests})")
    return 1 if survived or errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
