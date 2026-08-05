# Plan: full application audit

Status: **not started**. Written 2026-08-05, immediately after the
price-freshness work, by the session that did it.

Read this whole file before starting any segment. It carries a bug-class
taxonomy derived from *this repo's actual failure history* — that is the
part you cannot re-derive by reading code, and it is what makes the
difference between an audit that finds things and one that produces a
tour of the codebase.

---

## What this audit is, and is not

**Is:** a systematic hunt for defects and for places where a future
defect is likely, ranked by expected yield.

**Is not:** a refactor, a rewrite, a style pass, or a tour. The failure
mode for this kind of work is Segment 2 deciding `basis.py` would be
nicer with a class hierarchy and consuming the whole budget. **Findings
first. Fixes only when small, proven, and covered by a test.**

### The prime directive

> Every finding must come with a way to reproduce it, and every fix must
> come with a test that FAILS when the fix is reverted.

That second clause is not ceremony. This project has repeatedly shipped
tests that passed with the fix removed — three were caught in the
price-freshness session alone, including one guarding an unreachable
branch. A test that passes both ways is worse than no test, because it
tells you an invariant is protected when it isn't.

---

## The bug-class taxonomy (read this twice)

Ranked by how often this repo has actually produced them. Hunt these
shapes, not "bugs" in the abstract.

### 1. Duplicated logic that drifts — the #1 source, by a wide margin

The same rule implemented in two places; one gets updated. Known
instances, all of which shipped:

- `basis.py::_walk` vs `history.py`'s inline snapshot walker — basis
  rules landed in one and not the other **twice** (wrap basis-carrying,
  FMV transfer-ins), silently desyncing the Overview from Holdings.
- `main.main()` vs `main._refresh_prices_only()` — the refresh path
  re-implemented the USD-non-Savings skip, the dust filter, the cash
  fold, metadata load order, ingest ordering, and (found this session)
  the basis source. Every single one diverged.
- Python vs dashboard JS — the JS recomputing something `analytics/`
  already computed, subtly differently. `analytics/` exists as a
  compute-once layer *because* of this.
- Option intrinsic floor — needed at **six** call sites; three were
  missing it, printing a phantom daily move for every open contract.

**Hunt:** enumerate every pair of places implementing one rule. For each,
either find the parity test or write it.

### 2. Two-path divergence

A special case of (1) but worth its own entry because the trigger is
structural: any figure computed by both `main()` and
`_refresh_prices_only()` is suspect until proven equal.
`tests/test_pipeline_path_parity.py` is the harness — **and note its
fixture had to be enlarged this session before one of its tests stopped
being vacuous.** Assume the others may be too.

### 3. Coverage / freshness lies

State that claims to describe data but describes an intention instead.
`covered_end` recorded what was *requested*, not what had *settled*.
A benchmark froze at $0 with `failure_count: 0` because it was never
requested — it didn't fail.

**Hunt:** every cached or memoised value. What invalidates it? What
happens when the thing it describes changes underneath?

### 4. Sign and direction errors

`cash_bridge.py`'s docstring says it plainly: a sign error here invents
portfolio value. Parsers `abs()` everything and encode direction in the
action, so any action whose direction is ambiguous *before* the `abs()`
is a latent bug (Robinhood `ACH`, Coinbase `Convert`/`Wrap`/`Transfer`,
Voya `TRANSFER`, Coinbase Pro `match`).

### 5. Classification gaps

An action, symbol, or account that falls through every rule and lands on
a default. Title-cased unknown actions, `account_group` defaulting to
the raw name, `account_type` defaulting to `Taxable`, sector `Other`.
Defaults are silent; that's what makes them dangerous.

### 6. Accumulated rounding

Found this session: summing thousands of cent-rounded annotations drifts
from the full-precision total. **Hunt:** every place a total is built by
summing already-rounded values instead of rounding once at the end.

### 7. Ordering and tie-breaks

Same-day rows, stable sorts, and "whatever order the caller passed".
`seq` exists because this moved realized gains by thousands of dollars.

### 8. Calendar and timezone

LT classification at the anniversary + 1 day (across Feb 29), DST, UTC
vs local day boundaries, market holidays, half-days.

### 9. Float / NaN propagation

A single `NaN` reaches the dashboard as a `NaN` total, not a parse error,
because `json.dump` writes a bare `NaN` literal that is valid JS.

### 10. Module-level state

`ACCOUNT_GROUPS` / `ACCOUNT_TYPES` ship empty and are filled by metadata.
An in-process second run inherits the first run's maps and hides ordering
bugs. Any test running both paths must clear them.

---

## Technique ranking (highest yield per hour first)

**1. Mutation testing the existing suite.** 476 tests exist; their real
coverage is unknown. Every surviving mutant is a concrete, actionable
gap, and the technique is mechanisable. This is the single highest-yield
activity available and it directly measures the thing that lets bugs
through.

**2. Duplicated-logic parity audit.** Each finding prevents a *class* of
future bug, not one instance. See taxonomy (1).

**3. The JavaScript layer.** 8,773 lines with zero behavioural tests,
rendering every number the user actually reads. Enormous surface, no
coverage. Highest yield here does NOT require a JS test runner — see
Segment 6.

**4. Property / differential testing.** Generate weird-but-legal
ledgers; assert the 26 existing `data_health` invariants hold. Finds
edge cases nobody thinks to write by hand.

**5. Ground-truth reconciliation.** Bounded by how many `Reconcile *`
rows exist, but the highest *consequence* per finding, because it's tax.

**6. Reading high-risk modules.** Lowest yield per hour. Reserve it for
modules that rank worst on (size × blast radius × test coverage), and
do it *last*, when the earlier segments have told you where to look.

---

## Tooling decisions to make before Segment 1

None of `coverage`, `mutmut`, `hypothesis`, or `pytest-cov` is installed.
CLAUDE.md's "no external dependencies beyond yfinance" is about the
**runtime**; dev/test tooling is a separate call, and `pytest` is already
a dev dependency.

**Recommendation:** install `coverage` and `hypothesis` as dev
dependencies. Do NOT install `mutmut` — a full mutmut run over 11k lines
against a 7-second suite is many hours of wall time and produces mostly
noise. Instead hand-roll a **targeted** mutation harness (the pattern
used throughout the price-freshness session): apply a specific,
meaningful mutation to a specific line, run a scoped subset of the suite,
assert something fails. Roughly 60 lines of script, orders of magnitude
faster, and every mutant is meaningful by construction.

Ask the user before adding any dependency.

---

## Findings ledger

**This repo is public and the audit will surface real figures.** The
ledger must not leak them.

- Working ledger: `audit/findings.md` — **add `audit/` to `.gitignore`**.
  Real amounts allowed here; it never leaves the machine.
- Committed summary: `AUDIT.md` — qualitative only. Mechanism, files, bug
  class, severity. No dollar amounts, no account balances, no figures
  from the user's 1099/1040. "Coinbase realized reconciles closer under
  HIFO", never the number.

Ledger entry format:

```markdown
### F-012 · [severity] · <one-line claim>
- **Class:** <taxonomy number + name>
- **Where:** src/foo.py:123  (+ any other affected sites)
- **Repro:** exact command or test that shows it
- **Impact:** which displayed figures are wrong, and by how much
- **Status:** open / fixed in <commit> / wontfix (<reason>)
```

Severity: **high** = a displayed number is wrong, or tax/basis is
affected. **medium** = wrong under conditions that haven't occurred yet.
**low** = latent, cosmetic, or a robustness gap.

---

## Segments

Each is one ~5-hour session. Each is **independently valuable** — if the
audit stops after any segment, that segment still delivered. Each ends
green (`python -m pytest tests/ -q`) with the ledger committed.

Kickoff prompts are at the bottom, ready to paste into a fresh chat.

---

### Segment 1 — Infrastructure and the risk map

Everything downstream depends on this; do not skip it.

1. Create `audit/findings.md` + `AUDIT.md`; add `audit/` to `.gitignore`.
2. Install and run coverage. Produce `audit/coverage.md`: every
   `src/` file with its executed-line percentage, and **an explicit list
   of functions never executed by any test**. That list alone usually
   contains bugs.
3. Build `tools/mutate.py` — the targeted mutation harness described
   above. It takes a file, a line-matching pattern, a replacement, and a
   pytest selector; reports whether the suite caught it. Verify it
   against a known-good case: mutate `prices._cap_covered_end` and
   confirm `TestTodayFreshness` fails.
4. Build a **golden-output harness**: run the full pipeline on
   `samples/portfolio.snapshot.json`, store the exported JSON, and
   provide a diff command. Later segments use this to prove a fix
   changed only what it should. (Use the SAMPLE data, not the user's —
   the golden file must be committable.)
5. Produce `audit/risk-map.md`: every `src/` module scored on size ×
   blast radius × coverage gap. This orders Segment 8.

**Deliverable:** the four artifacts above. No bug hunting yet.

---

### Segment 2 — Mutation: the invariant core

Targets: `src/basis.py`, `src/actions.py`, `src/pipeline_stages.py`,
`src/cost_basis_overrides.py`.

For each meaningful branch and constant, apply a mutation and check the
suite catches it. Mutation classes worth applying: flip a comparison
(`>` ↔ `>=`), negate a condition, change a rounding precision, remove a
branch, swap a `min` for `max`, drop a set member, change a tolerance
constant, reverse a sort key.

Every **surviving** mutant is a finding: either a missing test, or dead
code. Both are worth knowing. Prioritise survivors in the basis walker —
that code decides tax figures.

**Deliverable:** ledger entries for every survivor; new tests for the
high-severity ones (each mutation-verified).

---

### Segment 3 — Mutation: prices, history, and walker parity

Targets: `src/prices.py`, `src/history.py`, `src/cash_bridge.py`,
`src/broker_lots.py`.

Same method as Segment 2, plus one specific job: **prove the two lot
walkers agree.** `history.py` keeps its own inline walker mirroring
`basis.py::_walk`. `history_holdings_basis_parity` pins the totals, but
line up the two implementations rule by rule and confirm each basis
effect is handled identically. Any rule present in one and absent in the
other is a high-severity finding even if today's data doesn't expose it.

Also: enumerate every txn-price fallback site and confirm each applies
`prices.option_intrinsic` (the static guard in
`tests/test_option_floor_parity.py` catches modules, not call sites).

---

### Segment 4 — The ingest layer

Targets: `src/parsers/`, `src/scanner.py`, `src/normalize.py`,
`src/reorgs.py`, `src/metadata.py`.

1. For every broker, enumerate the raw action strings appearing in that
   broker's real CSVs and confirm each maps to a canonical action.
   Anything falling through to title-case pass-through is a finding.
2. Property-test the post-parse invariant: quantity, amount, and fees
   are non-negative for every row, from every parser, on both the real
   data and the sample snapshot.
3. For every ambiguous action (taxonomy 4), confirm the sign split
   happens *in the parser*, before `abs()`.
4. Malformed-input behaviour: truncated CSV, missing column, empty file,
   BOM, wrong encoding, a date the format doesn't match. Does it fail
   loudly or silently drop rows? **Silent row-dropping is high
   severity** — it under-reports a position with no visible symptom.
5. `metadata.csv`: every `Type` value, plus unknown types, malformed
   dates, and out-of-range amounts.

---

### Segment 5 — Analytics correctness

Targets: `src/analytics/` (excluding `tax.py`, which is Segment 7).

1. **Trace the headline figures end to end**: Total Return, TWR,
   XIRR, Sharpe/Sortino, max drawdown, FI date. For each, hand-compute
   the expected value on a tiny fixture and compare. These are the
   numbers the user makes decisions on and the ones hardest to eyeball.
2. **Edge cases for every module**: empty portfolio, single snapshot,
   all-zero values, a fully-withdrawn account, a negative balance, one
   transaction, transactions all on one day. Many analytics modules
   divide by something that can be zero.
3. Check `analytics/_shared.py` (1,067 lines, used by everything) with
   particular care — a defect there is portfolio-wide.

**Before reporting anything as a bug, check CLAUDE.md's documented
limits.** Several apparent discrepancies are known, proven data limits —
the Coinbase 2021 double-book and off-platform basis especially. Do not
spend hours rediscovering them; the file says explicitly not to.

---

### Segment 6 — The Python↔JS boundary

The biggest unaudited surface: 8,773 lines, zero behavioural tests.

The highest-yield work here needs **no JS test runner**:

1. **Inventory every figure the dashboard renders.** For each, determine
   whether it is read from `DATA.analytics` or recomputed in JS. **Every
   recomputation is a finding** — CLAUDE.md's compute-once rule exists
   because those drift. Rank by how visible the number is.
2. **Null/NaN handling.** For every field the JS reads, what happens when
   it's `null`, `undefined`, `0`, or `NaN`? Python emits `None` freely
   (`total_return_pct`, `change_1d_pct`, `room_left`, `expense_coverage_pct`,
   and any `analytics` block that returns `None` wholesale).
3. **Empty-state rendering.** Tabs auto-hide when empty; do the panels
   *within* tabs? A section rendering `$NaN` or `undefined` is a finding.
4. Consider a minimal JS test setup (node + a DOM shim) — but only if
   time remains, and ask before adding the dependency. The inventory in
   (1) is worth more than the harness.

---

### Segment 7 — Tax and reconciliation

Highest consequence of being wrong.

1. Verify every `Reconcile *` row against fin's computed figure. Any
   delta without an `[expected ±N.NN]` annotation is either a bug or an
   explanation that needs writing down.
2. Re-verify the tax tables in `analytics/tax.py` against the IRS source
   documents for every year present — brackets, LTCG, standard
   deduction, 401k limit, Roth MAGI phase-out, NIIT threshold. A wrong
   table is silent and affects every downstream figure.
3. Check the AGI/MAGI build-up rule by rule against the actual 1040:
   retirement dividends excluded, savings interest included, 401k
   deduction excluding employer match, Section 125 pre-tax reducing
   wages.
4. ST/LT classification: the anniversary + 1 day rule, across leap years
   and across a Feb 29 purchase.
5. Wash sales: the 30-day window, cross-account detection, and the
   substantially-identical judgement fin does or doesn't make.

---

### Segment 8 — Cross-cutting sweeps

Driven by Segment 1's risk map. One sweep per bug class, across the
whole codebase rather than per module:

- Float/NaN: every division, every `/ 0` risk, every place a `NaN` could
  enter the cache or the export.
- Calendar/timezone: every date arithmetic site.
- Ordering: every sort, every `groupby`, every "first match wins".
- Rounding: every place a total sums already-rounded values.
- Error paths: what happens when yfinance is down, the cache is corrupt
  JSON, a shard has a symbol mismatch, the export is from an older
  schema version.
- Module-level state: everything mutated at import or first use.

---

### Segment 9 — Synthesis

1. Consolidate the ledger; de-duplicate; re-rank by severity.
2. Fix everything high-severity that isn't already fixed, each with a
   mutation-verified test.
3. Write `AUDIT.md` — the sanitized, committable summary.
4. **Improvement opportunities**, kept separate from defects: the
   architectural observations the audit surfaced. Expect candidates
   like collapsing the two lot walkers into one, giving the JS layer a
   test harness, and a schema version on the export.

---

## Kickoff prompts

Paste one into a fresh chat. Each is self-contained.

> **Segment 1.** Read `PLAN-audit.md` in full, then execute Segment 1
> only. Do not hunt for bugs yet — build the four artifacts it
> specifies. Ask me before installing any dependency. Stop and write the
> ledger when you are ~30 minutes from your limit.

> **Segment N** (2–9). Read `PLAN-audit.md` in full, then read
> `audit/findings.md` and `audit/risk-map.md` for what earlier segments
> found. Execute Segment N only. Log findings as you go rather than
> saving them for the end — if you run out of budget mid-segment, the
> ledger is what survives. Every fix needs a test that fails when the
> fix is reverted; verify that by actually reverting it.

---

## Discipline rules (all segments)

- **Log as you go.** Budget can end abruptly. An unlogged finding is a
  lost finding.
- **Verify every new test by breaking the fix.** Non-negotiable — see
  the prime directive.
- **Don't fix what you haven't reproduced.** A plausible-looking bug
  that doesn't reproduce is a finding about your understanding, not
  about the code.
- **Check CLAUDE.md before reporting.** Several apparent bugs are
  documented, deliberate, proven limits.
- **Stay in scope.** Note refactor ideas in the improvement section;
  don't do them.
- **No portfolio figures** in commits, `AUDIT.md`, test fixtures, or any
  tracked file. Synthetic round numbers in tests, always.
- **Run the pipeline on sample data**, not the user's, whenever the task
  allows it (see the `fin-dev-loop` skill).
- **End green.** `python -m pytest tests/ -q` passes before every commit.

## Expected effort

Nine segments, ~3–4 productive hours each. Segments 1–3 are where the
mechanisable yield is; 6 is the largest unexplored surface; 7 has the
highest consequence per finding. If the whole thing can't be run,
**1 → 2 → 3 → 6** is the highest-value subsequence.
