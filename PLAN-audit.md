# Plan: full application audit

Status: **not started**. Written 2026-08-05, immediately after the
price-freshness work, by the session that did it.

Updated 2026-08-05, later the same day: a user-reported bug in the
reconciliation panel turned out to be a bug class the taxonomy was
missing entirely, and the way it hid exposed two failure shapes in the
safety nets themselves. See taxonomy (11), the vacuous-safety-net
subsection, and the perturbation technique.

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

### The three shapes of vacuous safety net

The prime directive covers only the first. All three have shipped here,
and the 2026-08-05 reconciliation bug was hidden by two of them at once.

1. **A test that passes with the fix reverted.** Three found in the
   price-freshness session alone.
2. **A guard that cannot fire.** `reconcile` annotated a balance row with
   "nearest snapshot Nd away" only when the gap exceeded 7 days — but the
   semimonthly history cadence caps that gap near 7, and a **1-day** gap
   was already enough to leak a transaction into the comparison. The
   diagnostic was coarser than the thing it diagnosed, so it never fired
   on the case it existed for. Ask of every warning, status band, and
   tolerance floor: what input trips this, and can that input occur?
3. **A fixture that supplies the value the code is supposed to derive.**
   `test_reconcile.py`'s balance tests passed a hand-written `history`
   with *no transactions behind it*, so the snapping logic resolved to
   the very snapshot the test author had placed there. Green, and blind
   to the defect by construction. Ask of every fixture: does it hand the
   code under test the intermediate result it was supposed to compute?

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

### 11. As-of / date-alignment errors — new class, large surface

A figure keyed to date D is answered with data from a *different* date.
Found 2026-08-05 in `analytics/reconcile.py`: `_computed_balance`
resolved a broker statement date to the history snapshot at minimum
**absolute** distance, so a statement dated the 4th matched *today's*
snapshot on the 5th — and a deposit posted the day after the statement
date printed as a reconciliation break of exactly its own size.

Ranked last only because it was identified last; its surface is one of
the largest in the codebase. Two properties make it dangerous:

- **Symmetric matching can resolve forward in time**, letting activity
  that happened *after* the requested date leak into the answer. Treat
  any `abs(...)` over two dates as suspect on sight.
- **History is sampled, not continuous.** Snapshots are semimonthly, so
  "the snapshot for date D" usually does not exist and every consumer is
  silently reading a neighbour. The sampling is a chart-resolution
  decision; nothing makes it safe to answer date-keyed questions with.

**Hunt:** every site that resolves a requested date to an available one.
Known surface: `reconcile._computed_balance` (fixed),
`prices.get_price`'s 7-day backward walk (deliberate, documented),
`cash_bridge.balance_at`, `balance_anchor`, `_shared._value_at_date`,
the TWR window snapping in `app/90-performance.js` and the year-ago
lookup in `app/20-history.js`, latest-in-month selection in
`monthly_pnl` and annual returns, and every TTM window. For each ask:
is the direction correct, is the distance bounded, and **does the caller
find out it got a different date than it asked for?**

Note the trap in the obvious fix: snapping *backward* is causally sound
but was also wrong here, because CLAUDE.md documents `Balance Anchor`
rows as pairing with `Reconcile Balance` at the same date — a backward
snap would have excluded the anchor's own true-up and reported a delta
equal to the drift the anchor had just corrected. Walking the ledger to
the exact date was the only correct answer. Expect this shape elsewhere:
the plausible fix that no test catches and that looks *more* right.

---

## Technique ranking (highest yield per hour first)

**1. Mutation testing the existing suite.** 478 tests exist; their real
coverage is unknown. Every surviving mutant is a concrete, actionable
gap, and the technique is mechanisable. This is the single highest-yield
activity available and it directly measures the thing that lets bugs
through.

**2. Duplicated-logic parity audit.** Each finding prevents a *class* of
future bug, not one instance. See taxonomy (1).

**3. Perturbation testing.** Inject ONE synthetic transaction into the
sample portfolio at a known date and amount, re-run the pipeline, and
diff the export against the unperturbed golden. Then ask of every
changed figure: *should* this have moved? The 2026-08-05 reconciliation
bug is exactly what this finds — a balance keyed to a past date moved
when activity was added after it. Cheap, mechanisable, and it targets
taxonomy (11) and (7) head-on. Perturbations worth running: a txn dated
after an as-of boundary (nothing keyed before it may move), a txn dated
on a boundary, a zero-quantity row, a same-day pair in both orders, a
txn in an account with no other activity. Segment 1's golden harness is
90% of the rig; build the last 10% there.

**4. The JavaScript layer.** 8,773 lines with zero behavioural tests,
rendering every number the user actually reads. Enormous surface, no
coverage. Highest yield here does NOT require a JS test runner — see
Segment 6.

**5. Property / differential testing.** Generate weird-but-legal
ledgers; assert the 26 existing `data_health` invariants hold. Finds
edge cases nobody thinks to write by hand.

**6. Ground-truth reconciliation.** Bounded by how many `Reconcile *`
rows exist, but the highest *consequence* per finding, because it's tax.
Caveat learned the hard way: this technique runs *through* the
reconciliation panel, so audit the panel itself first (Segment 5, item 1)
or you are measuring with an uncalibrated instrument.

**7. Reading high-risk modules.** Lowest yield per hour. Reserve it for
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

**Consequence of that split, and it matters:** the working ledger is
gitignored, so it is *never committed* and does not survive a fresh
clone, a machine change, or an errant `git clean`. It is the artifact
most likely to be lost and the one carrying all the detail. So: write
each finding into `AUDIT.md` in sanitized form **in the same working
session that discovers it**, not in Segment 9. Segment 9 consolidates
and re-ranks what is already committed; it must not be the first time a
finding reaches a tracked file. If a segment ends with findings that
exist only in `audit/findings.md`, that segment's output is one
directory deletion from zero.

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

## Working within the budget

The binding constraint is **not wall-clock time — it is the usage
budget, and that is consumed by context, not by hours.** A segment that
reads twenty source files into context is most of the way through its
budget before it has tested anything. This is the single most common way
a segment will under-deliver, and it is invisible until it is abrupt.

Tactics, in rough order of impact:

- **Grep before you Read.** Pull the function you need, not the file it
  lives in. `src/analytics/_shared.py` alone is 1,067 lines.
- **Make scripts print conclusions, not data.** A probe that prints a
  12-row comparison table costs almost nothing; one that dumps the
  export costs a large fraction of a segment. Never read
  `exports/transactions.json` directly — query it with a script.
- **Never re-read a file you already read or just edited.**
- **Prefer one broad script run over many narrow tool calls** when
  checking a repeated property across many sites.
- **Write findings down as they land.** A finding still in your head
  when the budget ends never existed.

Budget the *end* of a segment, not just the start: stop hunting and
write up while you still have room to write up well. The plan's "~3–4
productive hours" is a rough calibration, not a measurement — trust the
usage indicator over the clock.

---

## Segments

Each is one session. Each is **independently valuable** — if the audit
stops after any segment, that segment still delivered. Each ends green
(`python -m pytest tests/ -q`), with findings written into the
**committed** `AUDIT.md` — not only into the gitignored working ledger.

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

   **Make it accept an optional injected transaction** — date, account,
   symbol, action, quantity, price, amount — and diff the perturbed run
   against the unperturbed golden. That single extra parameter turns the
   harness into the perturbation rig in technique (3), which is what
   Segments 5 and 8 use to hunt as-of errors. It is a few lines now and
   is not reconstructable cheaply later.

5. **Audit what the sample portfolio actually exercises — before
   trusting anything built on it.** The golden harness, the perturbation
   rig, and every "run it on sample data" instruction in this plan are
   only as good as the sample's path coverage, and the sample is a
   *fixture*: exactly the thing that hid the 2026-08-05 bug by not
   exercising the risky path.

   Produce `audit/sample-coverage.md`: for each high-risk code path,
   whether `samples/portfolio.snapshot.json` reaches it. Start from this
   list — every one is a documented-as-subtle path in CLAUDE.md:

   - option exercise pairing (OEXCS + OCC), STC, the ×100 multiplier,
     and the intrinsic floor
   - `REC` rewards and the same-day-companion FMV resolution
   - wrap / unwrap basis carrying (ETH ↔ CBETH)
   - Coinbase Pro `match` pairing and `Trade Settle In/Out`
   - Coinbase regular ↔ Pro intra-transfer re-tagging
   - external-boundary transfers (`Receive` / `Send`)
   - corporate actions (CIL / MRGS / MRGC / LIQ / SOFF / CONV / SPR)
   - report-directed lot relief (a Coinbase RAWTX / gain-loss file and a
     Robinhood 1099 CSV in `data/`)
   - rollover bridges, balance anchors, cost-basis overrides
   - §1256 contracts, per-account `Lot Method` overrides
   - splits (forward and reverse) spanning a snapshot date
   - the cash bridges (`BRIDGED_GROUPS`)

   A cheap first cut: grep `tools/build_sample_snapshot.py` for the
   broker action codes it emits. The Robinhood section, for instance,
   writes only ACH / Buy / Sell / CDIV / BTO / OEXP — so option
   *exercise*, STC and `REC` appear unreached by it.

   **Then extend `tools/build_sample_snapshot.py` to cover the gaps**,
   highest-risk first, and regenerate the snapshot. This is the one
   place in the audit where building something beats finding something:
   an uncovered path is not merely untested, it is untestable by every
   later segment, and each addition is permanent leverage. Cap it — if
   the list is long, cover what Segments 2, 3 and 5 need and log the
   rest as findings.
6. Produce `audit/risk-map.md`: every `src/` module scored on size ×
   blast radius × coverage gap. This orders Segment 8.

**Deliverable:** the artifacts above — `audit/coverage.md`,
`tools/mutate.py`, the golden + perturbation harness,
`audit/sample-coverage.md` (plus any sample extensions), and
`audit/risk-map.md`. No bug hunting yet.

This is the largest segment in the plan and the one everything else
rests on. If the budget runs short, item 5's *inventory* matters more
than its *extensions* — knowing where the sample is blind still lets
later segments compensate; silently trusting it does not.

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

1. **Audit the verification machinery FIRST**: `data_health.py`,
   `alerts.py`, `reconcile.py`, `changes.py`, and `priced_pct`. A defect
   here is worse than a defect elsewhere because it *disables
   detection* — and 2026-08-05 produced exactly that: a reconciliation
   panel reporting a break that did not exist, while its own staleness
   annotation was incapable of firing. Everything else in this audit,
   and Segment 7 entirely, is measured with these instruments. For each
   check: construct an input that should trip it and confirm it does,
   then a near-miss and confirm it doesn't. Run the perturbation rig
   against the panel — inject a transaction dated after a
   `Reconcile Balance` row's date and confirm that row does not move.
2. **Trace the headline figures end to end**: Total Return, TWR,
   XIRR, Sharpe/Sortino, max drawdown, FI date. For each, hand-compute
   the expected value on a tiny fixture and compare. These are the
   numbers the user makes decisions on and the ones hardest to eyeball.
3. **Edge cases for every module**: empty portfolio, single snapshot,
   all-zero values, a fully-withdrawn account, a negative balance, one
   transaction, transactions all on one day. Many analytics modules
   divide by something that can be zero.
4. Check `analytics/_shared.py` (1,067 lines, used by everything) with
   particular care — a defect there is portfolio-wide. `_value_at_date`
   in particular is now load-bearing for reconciliation as well as TWR.

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

**Resolve this before you start fixing anything here:** the prime
directive requires every fix to carry a test that fails when the fix is
reverted, and with no JS runner that is unsatisfiable. Do not quietly
drop the requirement — that is how an unverified fix ships. Choose per
finding:

- If the defect is a **recomputation in JS of something `analytics/`
  already computes** (the expected case, per item 1), the fix is to
  delete the JS computation and read the Python figure — and the test is
  a *Python* one asserting the analytics field exists and is correct.
  The prime directive is satisfiable in full. Prefer this framing.
- If the defect is genuinely JS-only (null rendering, empty state), and
  no harness exists, **log it as a finding with an exact manual repro
  and leave it unfixed.** An unverifiable fix to the layer that renders
  every number the user reads is a bad trade.
- Building the harness is itself a legitimate deliverable for this
  segment. If it lands, the second bullet's findings become fixable.

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
- **As-of alignment** (taxonomy 11): every site resolving a requested
  date to an available one. Grep for `abs(` near date subtraction and
  for "nearest" in comments — symmetric matching is the signature. For
  each, confirm direction, bound, and whether the caller is told.
- **Vacuous guards** (see the prime-directive subsection): every runtime
  warning, status band, tolerance floor, and `data_health` check. For
  each, construct the input that trips it. One that cannot be tripped is
  a finding, and a guard whose threshold is coarser than the granularity
  of what it guards is the same finding in disguise.
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
> only. Do not hunt for bugs yet — build the artifacts it specifies.
> Ask me before installing any dependency. It is the largest segment;
> if the budget gets tight, prioritise the sample-coverage inventory
> over extending the sample. Stop and write up while you still have
> room to write up well.

> **Segment N** (2–9). Read `PLAN-audit.md` in full, then read
> `audit/findings.md`, `audit/risk-map.md`, and
> `audit/sample-coverage.md` for what earlier segments found — the last
> one tells you which paths the sample data cannot exercise, so you know
> where a green suite proves nothing. Execute Segment N only. Log
> findings as you go rather than saving them for the end, and write each
> one into the committed `AUDIT.md`, not only the gitignored ledger.
> Every fix needs a test that fails when the fix is reverted; verify
> that by actually reverting it.

---

## Discipline rules (all segments)

- **Log as you go.** Budget can end abruptly. An unlogged finding is a
  lost finding.
- **Verify every new test by breaking the fix.** Non-negotiable — see
  the prime directive.
- **Prove the fix addresses the symptom that was actually reported.**
  Cheap technique: `git show HEAD:src/foo.py > <scratch>/old_foo.py`,
  import it, run the repro through it, and confirm it reproduces the
  reported shape *exactly*. Stronger than revert-and-rerun, because it
  also proves you fixed the reported bug rather than an adjacent one.
- **Don't fix what you haven't reproduced.** A plausible-looking bug
  that doesn't reproduce is a finding about your understanding, not
  about the code.
- **Check CLAUDE.md before reporting *and before choosing a fix*.**
  Several apparent bugs are documented, deliberate, proven limits. And
  several correct-looking fixes break a documented cross-feature
  contract — the `Balance Anchor` / `Reconcile Balance` same-date
  pairing killed the obvious fix for the 2026-08-05 bug, and no test
  would have caught it.
- **Stay in scope.** Note refactor ideas in the improvement section;
  don't do them.
- **No portfolio figures** in commits, `AUDIT.md`, test fixtures, or any
  tracked file. Synthetic round numbers in tests, always. `githooks/pre-commit`
  is the mechanical backstop (enabled via `git config core.hooksPath
  githooks`) — **confirm it is active at the start of every segment**, and
  add newly-surfaced real figures to the gitignored `.pii-denylist.txt`
  as the audit turns them up. Never `--no-verify`.
- **Run the pipeline on sample data**, not the user's, whenever the task
  allows it (see the `fin-dev-loop` skill).
- **Keep cache churn out of audit commits.** `cache/` is checked in, so
  any real-data pipeline run dirties hundreds of price shards plus
  `price_cache_meta.json`. Stage audit changes by explicit path; never
  `git add -A` in this repo during a segment. A cache refresh is its own
  commit, or none.
- **End green, twice.** `python -m pytest tests/ -q` passes before every
  commit — and before ending a segment that touched pipeline code, run
  the real pipeline once and confirm the dashboard still builds and the
  reconciliation panel has not moved unexpectedly. The suite runs on
  synthetic data; the user's ledger is the only thing that exercises the
  paths the sample does not reach.

## Expected effort

Nine segments, ~3–4 productive hours each. Segments 1–3 are where the
mechanisable yield is; 6 is the largest unexplored surface; 7 has the
highest consequence per finding. If the whole thing can't be run,
**1 → 2 → 3 → 6** is the highest-value subsequence — but pull
**Segment 5's item 1** (audit the verification machinery) forward into
Segment 1 regardless. It is a couple of hours, it is what every other
segment's conclusions rest on, and 2026-08-05 demonstrated that those
instruments are not themselves trustworthy.
