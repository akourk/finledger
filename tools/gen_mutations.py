"""Propose targeted mutations for a module, as a `tools/mutate.py` spec.

Generates candidates from the AST rather than by hand, so the coverage
is systematic, then leaves curation to a human/agent: `--print` shows
what it would emit so obviously-equivalent mutants can be dropped before
burning suite runs on them.

Only mutates lines the test suite actually EXECUTES (read from the
`.coverage` data file).  A mutant on an unexecuted line survives
trivially and tells you nothing you didn't already know from
`audit/coverage.md` -- filtering them out is what keeps every survivor
meaningful.

    python -m coverage run --source=src -m pytest tests/ -q
    python -m tools.gen_mutations src/basis.py --tests tests/ --print
    python -m tools.gen_mutations src/basis.py --tests tests/ -o audit/mutations/basis.json
    python -m tools.mutate --spec audit/mutations/basis.json --skip-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import coverage

ROOT = Path(__file__).resolve().parent.parent

# (needle, replacement, label) -- applied to the source TEXT of one line.
# Ordered longest-first within each family so ">=" is tried before ">".
TEXT_MUTATIONS = [
    (" >= ", " > ",  "boundary: >= -> >"),
    (" <= ", " < ",  "boundary: <= -> <"),
    (" != ", " == ", "negate: != -> =="),
    (" > ", " >= ",  "boundary: > -> >="),
    (" < ", " <= ",  "boundary: < -> <="),
    (" and ", " or ", "logic: and -> or"),
    ("min(", "max(", "swap: min -> max"),
    ("max(", "min(", "swap: max -> min"),
    ("reverse=True", "reverse=False", "order: reverse flip"),
    ("reverse=False", "reverse=True", "order: reverse flip"),
    (".pop(0)", ".pop()", "order: FIFO -> LIFO"),
]

SKIP_LINE_MARKERS = ("import ", "from ", '"""', "print(", "#")


def _covered_lines(path: Path) -> set[int]:
    cov = coverage.Coverage(data_file=str(ROOT / ".coverage"))
    cov.load()
    _f, stmts, _e, missing, _m = cov.analysis2(str(path))
    return set(stmts) - set(missing)


def generate(path: Path, tests: str) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    covered = _covered_lines(path)
    rel = path.relative_to(ROOT).as_posix()

    # A line's text may repeat; mutate.py needs an occurrence index.
    seen: dict[str, int] = {}
    out: list[dict] = []

    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or any(stripped.startswith(m) for m in SKIP_LINE_MARKERS):
            continue
        occ = seen.get(stripped, 0) + 1
        seen[stripped] = occ
        if i not in covered:
            continue

        for needle, repl, label in TEXT_MUTATIONS:
            if needle not in stripped:
                continue
            mutated = stripped.replace(needle, repl, 1)
            if mutated == stripped:
                continue
            out.append({
                "name": f"L{i} {label}",
                "file": rel,
                "match": stripped,
                "replace": mutated,
                "tests": tests,
                "occurrence": occ,
                "_line": i,
                "_src": stripped[:90],
            })
            break  # one mutation per line keeps the batch interpretable

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--tests", default="tests/")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    path = (ROOT / args.module).resolve()
    muts = generate(path, args.tests)

    if args.show or not args.out:
        for m in muts:
            print(f"  L{m['_line']:>4}  {m['name'][8:]:26}  {m['_src']}")
        print(f"\n{len(muts)} candidate mutation(s) on covered lines of {args.module}")

    if args.out:
        clean = [{k: v for k, v in m.items() if not k.startswith("_")} for m in muts]
        p = ROOT / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(clean, indent=1), encoding="utf-8")
        print(f"wrote {args.out} ({len(clean)} mutations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
