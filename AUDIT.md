# fin — application audit

Sanitized, committable summary of the audit described in
`PLAN-audit.md`. **No portfolio figures appear here** — mechanism,
files, bug class, and severity only. The working ledger is
`audit/findings.md`, which is gitignored and never leaves the machine.

| | |
|---|---|
| **Started** | 2026-08-05 |
| **Segments complete** | 1, 2, 4, 6, Segment 5 item 1; Segment 3 substantially; Segment 7 begun |
| **Findings** | 6 open / 15 fixed |
| **Suite** | 478 → 686 tests, green · `src/` coverage 84.9% → **88.4%** · never-executed functions 23 → 13 |

Severity: **high** = a displayed number is wrong, or tax/basis is
affected. **medium** = wrong under conditions that haven't occurred
yet. **low** = latent, cosmetic, or a robustness gap.

---

## Findings

| ID | Sev | Claim |
|---|---|---|
| F-021 | medium | *(fixed)* The audit's own offline harness corrupted prices for split-carrying symbols, producing a false reconciliation break |
| F-020 | medium | *(fixed)* The JS tax-table fallback carried superseded 2025 standard deductions — OBBBA updated `tax.py` but not the JS literal |
| F-019 | low | The Performance tab's anchor cards invite an inference that doesn't hold — Total Return is not Realized + Unrealized |
| F-018 | medium | *(silence fixed)* `metadata.csv` silently degraded invalid input; three documented clamps reject-to-default instead of clamping |
| F-017 | **high** | *(tier 1 fixed)* A date-format change silently drops every row in every parser, with no output — 7 of 8 sample broker files went to zero rows in silence |
| F-016 | medium | *(fixed)* Two documented `prices.py` behaviours had no test — the failure-backoff cap and `covered_end` monotonicity |
| F-015 | medium | *(fixed)* The Split basis rule is implemented twice, verbatim, and neither copy was executed by any test |
| F-014 | low-med | *(fixed)* `build_holdings`' cost-basis source gate was unprotected while the same rule at two sibling sites was |
| F-012 | medium | *(fixed)* `txn_external_cash_flow`'s carve-outs are pinned only in the firing direction, contrary to the documented claim — all three `and` guards could be deleted with the suite green |
| F-013 | medium | *(fixed)* `_consume_lots_capped`'s descending-index lot removal was unprotected while its identical sibling in `_consume_lots` was |
| F-007 | high | *(fixed)* No test had ever tripped any of the 23 `data_health` checks — the suite's own safety net was entirely unverified |
| F-008 | medium | *(fixed)* The reconciliation panel's break-flagging path was never exercised — no test produced a balance row that wasn't "ok" |
| F-009 | medium | *(fixed)* `alerts.py` had no test file; five of seven alert sources had never been emitted |
| F-010 | low | The coverage-gap alert's lookback window silently halved when the history cadence went semimonthly |
| F-011 | low | A malformed `[expected ±N]` token is indistinguishable from no token, and its error branch is unreachable |
| F-001 | medium | *(fixed)* The shipped sample portfolio was not exercised by any test; five broker parsers had zero executed lines |
| F-002 | medium | Two basis-walker branches (`_remove_from_avg`, `_apply_split_to_lots`) are never executed |
| F-003 | low | `snapshot.py` has 0% coverage — export/import wholly untested, including the anti-clobber guard |
| F-004 | low | `normalize._amount_sign` is never executed (sign-split family — taxonomy 4) |
| F-005 | low | Never-executed functions in `analytics/tax.py` (`_pick_by_year`), `main.py`, `metadata.py`, `actions.py`, `parsers/_helpers.py` |
| F-006 | low | *(fixed)* `tools/mutate.py` round-tripped source through text-mode I/O, with a restore check that could not detect the corruption |

F-001 through F-006 come from Segment 1, which is infrastructure and
did no bug hunting; each is a **coverage** finding — code unprotected by
construction — not a confirmed defect. F-007 comes from the
pulled-forward verification-machinery audit.

**F-007 is the most consequential finding so far.** Not one of the 23
`data_health` checks had ever produced an issue in any test. They ran
on every pipeline test and returned clean every time, so a change that
disabled a detector would ship green — and the *next* real defect in
that area would then ship green too. Compounding blindness rather than
a single missed bug.

Worth stating precisely what was and wasn't wrong: all ten
high-severity checks were probed with a violating input and a clean
near-miss, and **all ten fire correctly**. They were untested
detectors, not broken ones. "These guards cannot fire" would have been
the wrong claim, and the difference is exactly what the plan means by
not reporting what you haven't reproduced.

**F-001 is the one with leverage.** The sample snapshot contains all
ten broker CSVs and runs end to end offline, but no test consumes it —
the end-to-end test builds its own inline portfolio instead. A single
test running the pipeline on the sample would cover five parsers
immediately.

**F-006 is worth reading even though it caused no harm**, because the
failure shape is the audit's own subject: the verification compared
decoded strings that had round-tripped through the same lossy
translation it was meant to detect. That is the plan's third vacuous-
safety-net shape — a check that agrees with itself by construction —
occurring in the tooling built to hunt it.

---

## Segment log

### Segment 1 — Infrastructure and the risk map (2026-08-05)

Built the measurement apparatus the rest of the audit runs on. Suite
green throughout (478 passed).

**Tooling added** (all dev-only; the runtime still has no dependency
beyond `yfinance`):

- `coverage` installed as a dev dependency. `mutmut` and `hypothesis`
  deliberately **not** installed — see "Decisions" below.
- **`tools/audit_coverage.py`** → `audit/coverage.md`. Per-module line
  coverage plus, more usefully, an AST-derived list of **functions no
  test ever enters**. Overall `src/` coverage is **84.9%**
  (6,397/7,538 statements) with **23 never-executed functions**.
- **`tools/mutate.py`** — targeted mutation harness. Name a specific
  mutation and a scoped test selector; get `CAUGHT` / `SURVIVED` /
  `ERROR`. Verified in both directions: the plan's nominated case
  (`min`→`max` in `prices._cap_covered_end`) is CAUGHT by
  `TestTodayFreshness`, and a mutation to a never-executed function
  correctly reports SURVIVED. It refuses to report a verdict when the
  scoped tests were already red, and restores the source byte-exactly.
- **`tools/golden.py`** — golden-output **and** perturbation harness.
  Runs the full pipeline against the sample snapshot in a throwaway
  workdir, network stubbed dead, and diffs the exported JSON down to
  dotted leaf paths. Verified deterministic: two runs differ only in
  timestamps. `--inject` adds one synthetic transaction and A/Bs the
  perturbed run against a fresh baseline in the same invocation, which
  cancels the date-drift a stored golden would otherwise accumulate.
  `--filter` scopes the diff to a subtree.

**Reports produced:** `audit/coverage.md`, `audit/sample-coverage.md`,
`audit/risk-map.md` (all gitignored — they carry path detail, not
figures, but the directory is ignored wholesale by design).

**Risk map** — modules scored on size × blast radius × coverage gap,
blast radius weighted highest. Top of the list: `prices.py`,
`basis.py`, `analytics/_shared.py`, `config.py`. This corroborates the
plan's independently-chosen Segment 2/3 targets rather than redirecting
them.

**Sample-coverage inventory** — the most consequential deliverable.
The sample reaches each broker's happy path and almost none of the
documented-as-subtle ones. Not reachable from sample data: wrap/unwrap
basis carrying, option exercise pairing and the intrinsic floor,
`REC` rewards, external-boundary transfers, corporate actions,
report-directed lot relief, rollover bridges, balance anchors,
`Cost Basis` and `Lot Method` overrides, and splits spanning a
snapshot. Metadata is the best-covered area (19 of 25 `Type` values).

The practical consequence, recorded for later segments: **a surviving
mutant in those regions means the sample lacks the input, not that the
suite lacks the assertion** — and the two must not be logged as the
same finding.

**Deferred:** extending `tools/build_sample_snapshot.py` to close those
gaps. The plan caps this item and ranks the inventory above the
extensions; the prioritized extension list is in
`audit/sample-coverage.md`, headed by wiring the sample into a test.

**Regression check, incidentally.** The perturbation rig was proved out
on the exact shape of the 2026-08-05 reconciliation bug: injecting a
transaction dated *after* a `Reconcile Balance` row's as-of date leaves
every reconciliation figure identical. The fix holds, and it is now
mechanically re-checkable.

**Decisions:**

- *`coverage` yes* — it is what makes the mutation segments efficient.
  A mutant on a line no test executes survives trivially and teaches
  nothing; coverage lets Segments 2–3 spend their budget on covered
  lines, where a survivor is genuinely informative.
- *`mutmut` no* — a blanket run over 11k lines against a 12-second
  suite is hours of wall time for mostly noise. The targeted harness
  makes every mutant meaningful by construction.
- *`hypothesis` deferred* — the bottleneck for property-testing this
  domain is writing a generator that emits *legal* ledgers (paired
  wraps, paired transfers, same-day ordering), not the library. The
  perturbation rig covers much of the same ground from a known-legal
  starting point. Revisit at Segment 5 if generative testing is
  actually wanted.
- *Golden file gitignored rather than committed.* The plan asked for a
  committable golden; the privacy reason for that (sample data is
  synthetic, so it carries no real figures) still holds, but the export
  embeds today's date in many places, so a committed golden would show
  spurious diffs daily and train the reader to ignore it. It
  regenerates in seconds via `--record`.

### Segment 5, item 1 — the verification machinery (2026-08-05, pulled forward)

The plan recommends auditing the instruments before trusting anything
measured with them. Done for `data_health.py`; `reconcile.py`,
`alerts.py` and `changes.py` remain.

**Method that did the work:** rather than reading 983 lines, ask
coverage a sharper question — is the line that *constructs* each
violation ever executed? A check function can run on every test and
always return clean, so function-level coverage says nothing; the
violation branch says everything. All 23 came back never-executed.

Then, before claiming a defect, the necessary second step: feed each
high-severity check an input that should trip it, and a near-miss that
shouldn't. All ten behaved correctly. The finding is a test gap, not a
broken detector — see F-007.

**Fixed:** `tests/test_data_health_guards.py` (+21 tests, suite now
499). Mutation-verified in both directions.

**Then the other three instruments.** Same method, three different
outcomes — which is itself the useful result, because it shows the
question discriminates:

- **`reconcile.py`** (F-008) — 92% covered, but the *one* uncovered
  decision was the balance status band, so the panel's break-flagging
  path had never run. Worse than an ordinary gap: the plan's
  ground-truth technique measures Segment 7 *through* this panel.
- **`alerts.py`** (F-009) — no test file at all; five of seven alert
  sources had never been emitted.
- **`changes.py`** — needs nothing. `first_run` and
  `skipped_empty_run` are both already pinned, matching its 95%. Worth
  recording that a module passed.

Two smaller findings fell out of reading `reconcile.py` closely: the
coverage-gap alert's lookback is coupled to snapshot cadence and
silently halved when history went semimonthly (F-010), and a malformed
`[expected ±N]` token parses as no token at all (F-011). F-011 fails in
the safe direction — it re-flags rather than masks — but silently. Both
logged, neither fixed: they are behaviour changes, and the scope rule
says log those rather than take them.

**Fixed:** `tests/test_data_health_guards.py`,
`tests/test_reconcile_guards.py`, `tests/test_alerts_guards.py`.
Suite 478 → 545, green. Every addition mutation-verified: 26 mutations
across the three modules, all CAUGHT.

**Still open:** the 13 `warn` / `info` `data_health` checks.

### Segment 2 — Mutation: the invariant core (2026-08-05, in progress)

**Method note, learned the expensive way.** `tools/gen_mutations.py`
proposes mutations from the AST, filtered to lines the suite actually
executes. The first `basis.py` run emitted 67 candidates and most were
`<= 1e-12` → `< 1e-12` boundary flips — **equivalent mutants**, which
can only behave differently if a float is exactly 1e-12. They survive,
they mean nothing, and they bury the real signal. This is precisely the
noise that makes a blanket `mutmut` sweep unhelpful on this codebase,
reproduced in miniature by an unfiltered generator.

Curating epsilon comparisons out left 55 semantically meaningful
mutations: `min`/`max` swaps that change how much of a lot is consumed,
`reverse=` flips that change consume order, `and`→`or` on the documented
`txn_external_cash_flow` carve-outs, and `!=`→`==` on the lot-method
gates.

`actions.py` generates **zero** candidates and correctly so — it is a
declarative catalog with no branching, so its correctness is structural
(what the table says) rather than conditional. `test_actions_catalog.py`
is the right instrument for it; mutation is not.

**Result: 55 mutations, 15 caught, 40 survived.** Most survivors are
zero-boundary flips (`<= 0` → `< 0`) whose observable difference needs a
quantity of exactly zero; those are logged but not individually chased.
Two survivors had real weight, and both became findings:

- **F-012** — the `and` guards in `txn_external_cash_flow`'s carve-outs
  all survived. This one is worth dwelling on: CLAUDE.md states the
  helper is pinned by a named test, and it *is* — but only in the
  direction where the rule fires. The existing test covers marker +
  right account and non-marker + right account, and never marker +
  **wrong** account, which is exactly what the `account_group` half of
  each condition rejects. So a documented safety claim was true and
  insufficient at the same time.
- **F-013** — `_consume_lots_capped` deletes emptied lots by index,
  correct only descending. Its identical sibling in `_consume_lots` is
  protected; this one wasn't, because no test consumed two or more lots
  to exhaustion in a single *reserving* call.

Both rules were already **correct** — a probe confirmed the behaviour
before any test was written. Nothing was wrong in the output; the guards
were simply deletable with the suite green. No source changed.

**A method note that generalises.** My first reading dismissed two of
the three carve-out survivors as equivalent mutants, reasoning that the
inner description check made the outer account check redundant. Probing
the actual function showed the opposite — a non-Roth account carrying
the same marker returns 0, so the account check is load-bearing. The
lesson is the plan's own rule in miniature: **don't classify a survivor
by reading it, run it.**

`pipeline_stages.py` and `cost_basis_overrides.py` completed the
segment: 16 curated mutations, 6 caught, 10 survived. Nine survivors are
equal-value boundary flips (`>` → `>=` on a running maximum, `< 0.01`
→ `<= 0.01` on the dust threshold) — observable only when two floats are
exactly equal, and logged rather than chased. The tenth became **F-014**,
the same asymmetry as F-013 for a third time: a Savings/cash rule
protected at two sites in the module and unprotected at the third.

**Segment 2's shape, stated plainly:** across `basis.py`,
`pipeline_stages.py` and `cost_basis_overrides.py`, every meaningful
survivor was a *missing near-miss test*, and **not one was a defect**.
Every rule the mutations probed was already correct. What the segment
bought is that they can no longer be deleted silently.

### F-001 closed — the sample portfolio is now exercised

`tests/test_sample_snapshot.py` pins the shipped sample as working input
and, in doing so, gives the five orphaned parsers their first coverage.
Split by cost: fast parse-level tests do the covering and assert the
post-parse invariants CLAUDE.md declares; one end-to-end test proves the
sample still drives a full run.

| | before | after |
|---|---|---|
| `src/` coverage | 84.9% | **88.0%** |
| never-executed functions | 23 | **14** |
| `parsers/usaa.py` | 22% | 85% |
| `parsers/schwab.py` | 26% | 81% |
| `parsers/vanguard.py` | 30% | 85% |
| `parsers/apple_savings.py` | 32% | 84% |
| `parsers/coinbase.py` | 46% | 90% |
| `snapshot.py` | 0% | 58% |

Assertions are deliberately structural — counts, signs, membership —
never hard-coded totals, because the sample is regenerated by
`tools/build_sample_snapshot.py` and pinning its arithmetic would break
on every legitimate regeneration.

**One trap worth recording**, because it produced a convincing false
positive. The first draft asserted that every parsed action normalizes
to a catalog entry, run against raw parser output. It failed loudly with
seven "classification gaps" — `BTO`, `CDIV`, `OEXP`, `ACH Deposit` and
others apparently falling through to title-case. None was real:
`normalize.RULES` are scoped by `account_group`, which `main.py` attaches
*after* parsing, so no scoped rule could match. The assertion was testing
a pipeline state that does not exist. It now runs on the export, where
`account_group` is populated. This is the plan's third vacuous-safety-net
shape inverted — a fixture that *withholds* the intermediate the code
needs, manufacturing failures instead of hiding them.

### Segment 3 — Prices, history, and walker parity (2026-08-05, in progress)

**Walker parity, done properly.** Segment 1 established that the two lot
walkers dispatch the same eight effects in the same order. That is a
branch-structure check and it is not enough — the plan asks for a
rule-by-rule comparison. Method: extract, per effect branch, the set of
helper functions each walker calls (walking only the branch *body*, not
the trailing `elif` chain — the first attempt walked the whole `If` node
and every branch showed the union, which reads as universal disagreement
and means nothing).

Seven of eight effects reduce to the same helper set once two naming
differences are normalised: `_push_txn_lots` vs `_push_txn` for the push
helper, and basis's `_consume_from_key` / `_method_for` wrappers vs
history's `_consume` closure over the same consume functions. The
`basis_override` handling also differs only in placement — basis applies
`_ov(...)` per branch, history applies it inside its push helper.

**One effect did not reduce: `split`.** See F-015. basis calls the shared
`_apply_split_to_lots`; history re-implements the same arithmetic inline.
Comparing them line by line, they **agree** — same ratio, same scaling,
same guard inverted — so this is not a live defect. But it is the one
rule with two copies, and *neither copy was executed by any test*:
`_apply_split_to_lots` was on the never-executed list, and
`audit/sample-coverage.md` records that no sample symbol splits after
the sample's start date. `history_holdings_basis_parity` pins the two
walkers together only over ledgers the tests actually run, and none
contained a `Split`.

`tests/test_split_walker_parity.py` closes it: unit tests for the shared
helper (basis preserved, acquired dates untouched, degenerate inputs a
no-op) plus one ledger with a Split run through **both** walkers,
asserting they agree on quantity and basis.

The improvement — have history call the shared helper instead of
duplicating it — is logged for Segment 9 rather than taken, per the
scope rule.

**Mutation run: 152 curated mutations across `prices.py`, `history.py`,
`cash_bridge.py` and `broker_lots.py` — 53 caught, 99 survived.** The
survivor rate is much higher than Segment 2's and the reason is
structural rather than alarming: a large share of these modules is the
network fetch path, which the test suite stubs dead by design, so those
mutants survive for lack of *input*. `audit/sample-coverage.md` warned
this would happen and it is the distinction that keeps the count
honest — they are not logged as missing assertions.

Of the genuinely reachable survivors, the two worth acting on became
**F-016**: the failure-backoff cap and `covered_end` monotonicity, both
behaviours CLAUDE.md documents in detail and neither with a test. The
backoff one has real teeth — inverting the 30-day cap into a floor means
a symbol's first transient failure waits a month, during which the
dashboard prices it from stale marks and only an `info`-level alert
mentions it.

**A pattern that recurred three times and is worth naming.** Twice in
this segment (and once in Segment 1's tooling) a test I wrote
*re-implemented* the logic it was meant to protect, and passed for that
reason:

- `tools/mutate.py`'s restore check compared strings that had
  round-tripped through the same lossy translation it was detecting
  (F-006).
- The split-parity test's first draft asserted `quantity == 20` and
  `basis == 1000` — both invariant under a split by construction, since
  total basis is preserved and quantity comes from the *balance* walker.
  Neutering history's split branch survived it. Only adding a later
  SELL, where relieved basis depends on the rescaled per-share figure,
  made it discriminate.
- The `covered_end` test's first draft computed `max(ce, end_s)` itself
  instead of calling `_record_success`.

All three were caught only by running the mutation, never by reading the
test. That is the concrete argument for the prime directive: **a test
that has not been seen to fail is not yet evidence of anything.**

### Segment 4 — The ingest layer (2026-08-05, begun)

**Item 1 — action coverage on real data: clean.** All 14,063 real
transactions across 48 broker files, 92 distinct
`(account_group, raw action)` pairs, and **every one normalizes to a
catalog entry**. Nothing falls through to title-case pass-through.

Worth recording *how* that conclusion was nearly wrong. The first probe
reported 16 actions falling through — `Reinvest Shares`,
`trade_settle_out`, `Stock Split` and others — which would have been a
substantial false finding. `normalize.RULES` are scoped by
`account_group`, and the probe had left that field as the raw broker
account name (`Schwab Roth IRA` rather than `Roth IRA`), so no scoped
rule could match. The cause was a wrong assumption about
`parse_metadata`: it *returns* the account-group mapping, and `main.py`
applies it (`ACCOUNT_GROUPS.update(...)`). CLAUDE.md says
`parse_metadata` applies the overrides itself — minor doc drift, but it
is what sent the probe wrong. **Same trap as the sample-snapshot test
earlier in this audit: normalization checked without the state it
depends on.** Twice in one session from two different directions.

**Item 4 — malformed input: F-017, the first high-severity finding that
is a live robustness gap rather than a missing test.** Every parser
drops a row whose date won't parse, silently, via `except ...: continue`.
Simulating a broker date-format change on the synthetic sample takes 7
of 8 broker files to **zero rows with no exception, no warning and no
stderr** — 35 rows gone in silence. Brokers do change export formats;
when one does, that account simply disappears from the portfolio. The
only signal is an informational per-file count line. Partial drops are
worse: no count anomaly at all.

**Tier 1 fixed** (`e2c0e13`) — the audit's **first and only source
change**. `parse_all_files` now calls out a recognised broker file that
has data rows but yields zero transactions, and states the consequence.
Golden export byte-identical, so no figure moved; four mutations CAUGHT,
including a straight revert of the guard.

The quiet half was designed as carefully as the loud half. The threshold
sits above the longest known preamble (Voya writes six lines before its
header) so the warning **cannot** fire on an empty-but-valid file — an
empty `manual-adjustments.csv` is the common case. The cost is that
losing a handful of rows stays silent. That trade is the right way
round: a warning that fires every run trains the reader to skip the
line, which is precisely the failure this audit found in the alerts
panel. Tests cover both halves.

Tiers 2 and 3 stay open: a skipped-row count per parser (catches
**partial** loss, which tier 1 structurally cannot see) and surfacing it
as a `data_health` check so it reaches the dashboard rather than only
the console.

**Validated on the real corpus, not just fixtures.** Parsing the user's
48 real broker files (read-only — no network, no cache writes) emits
**zero** warnings. Exactly one real file parses to zero rows, and it is
header-only — genuinely empty, so the silence is correct rather than a
miss. That is the property worth having evidence for: synthetic tests
show the warning *can* fire, but only the real corpus shows it does not
fire spuriously on the data the user actually runs every day.

**CLAUDE.md corrected** for the drift that sent the item-1 probe wrong:
`parse_metadata` returns the account-group mapping and `main()` applies
it. The note now spells out the consequence, because the failure is
non-obvious and looks like a real bug — normalizing without that update
makes every scoped rule miss and every action fall through to
title-case.

**F-017 tier 2 also fixed** — the partial-loss case tier 1 structurally
cannot see. A file that loses *some* rows still parses to a non-zero
count, so nothing notices and those transactions are simply absent.
Counted without touching a single parser: each calls exactly one
`_date_*` helper, once per row, inside the try/except that drops it, and
none tries several formats speculatively — so a raise from those helpers
*is* a dropped row, exactly. The helpers tally and re-raise unchanged;
`parse_all_files` reads and resets per file. Blank dates are deliberately
not counted (spacer rows are structural, and counting them would put a
permanent false warning on files that contain them). Real corpus: zero
warnings, so no rows are being lost today either.

**Item 3 — sign splits: clean, now pinned.** Every direction-ambiguous
raw action must be split *before* the `abs()`, because once abs() has run
the direction is unrecoverable. Robinhood `ACH` and Voya `TRANSFER` are
both correct and are now pinned in both directions, with magnitudes
asserted equal so the split can't later be "fixed" by leaking a negative
through instead. Also pinned: the two canonical actions must differ *and*
land on opposite balance effects — a split that normalization collapses
back into one action would be silently useless. Removing or inverting
either split is caught.

**Item 5 — metadata: F-018.** Three documented clamps do not clamp.
`Retirement Age = 20` gives 67 rather than 30, `Pay Frequency = 13`
gives 26 rather than 12, `State Tax Rate = 25` gives 0.0 rather than
0.20 — each REJECTS an out-of-range value to its default. That is a
defensible choice, just a different one than documented, so CLAUDE.md
was corrected for all three rather than the behaviour changed: switching
reject-to-default into real clamping would move planning figures for
anyone relying on today's result, and that is the user's call.

The sharper half is that every rejection and coercion is **silent**. A
non-numeric `Annual Expenses` becomes `0.0`, and the Planning tab
multiplies it by 25 for the FI number — so a fat-fingered row reports a
$0 FI target rather than refusing to guess. A malformed date is stored
raw and consumers slice `date[:4]` for a tax year. 26 tests now pin the
real behaviour; the recommended fix is a warning on rejection, the same
additive shape as F-017 tier 1, which removes the silence without moving
a figure.

Item 2 (post-parse invariant property tests) is covered by
`tests/test_sample_snapshot.py` and `tests/test_parser_sign_splits.py`,
which assert non-negative quantity/price/fees/amount across all nine
parsers. **Segment 4 is complete.**

**F-018's silence fixed** (`791c0ca`). `parse_metadata` now reports every
rejected or coerced value, naming the row *and* the value actually used
— in a 60-row file, "something was rejected" is not actionable. The
reject-vs-clamp semantics are left alone; only the silence changed.

A note on how that was verified, because it nearly went wrong. A plain
`golden --check` showed **3,414 differences** — alarming for a change
that should have been stdout-only. They were entirely **date drift**:
the golden had been recorded the previous day, the date rolled over, and
`get_price(sym, today)` began returning a newer bar. The tell was
`"Latest transaction is 46 days old" → "47 days old"`. Proving
figure-neutrality meant re-recording the baseline at HEAD *today* and
comparing on top of that — which came back IDENTICAL.

This is exactly the drift `golden.py`'s docstring warns about, and the
reason its perturbation mode does an in-invocation A/B rather than
trusting a stored golden. **Treat a stored golden as valid only for the
day it was recorded**; across a date boundary, re-record at HEAD before
concluding anything.

### Segment 6 — The Python↔JS boundary (2026-08-05)

The largest unaudited surface: 8,774 lines across twelve `app/*.js`
modules, zero behavioural tests.

**Item 1 — recomputation inventory.** The JS makes **47** `ANALYTICS.*`
reads against **28** traversals of raw `txns` / `holdings` / `history`.
Triaged, most traversals are legitimate rendering rather than
recomputation: enumerating unique values for filter pills, windowing a
series to a user-selected range, feeding a table. Four in
`90-performance.js` genuinely recompute figures Python also produces.

Rather than flag those on principle, the useful question is whether the
two would **agree** — which is testable without a JS runtime by
evaluating the JS expressions against the golden export in Python:

| figure | JS | Python | delta |
|---|---|---|---|
| lifetime realized | `sum(txn.realized_gain)` | `sum(analytics.positions.realized)` | **0.0000** |
| lifetime unrealized | `sum(holding.unrealized_gain)` | `sum(analytics.positions.unrealized)` | **0.0000** |

Exact agreement. The remaining two recomputations are windowed figures
driven by a runtime-selected range that Python does not precompute for
every window, so they are unavoidable without precomputing all of them.

That comparison did surface **F-019**: `total_return` is not
`realized + unrealized`, differing by `cost_basis − net_contributed +
realized`. Both are correct — reinvested income creates basis with no
external contribution — but they sit as adjacent cards a reader will try
to add. Cosmetic, logged.

**Item 3 — empty-state rendering, tested by actually running it.** No JS
test harness was needed. The pipeline was run twice against
deliberately degenerate inputs, and each dashboard loaded in a browser
with every tab activated (renderers are lazy, so a null only bites on
first activation):

1. **All optional metadata stripped** — the state of a brand-new user.
   39 of 59 metadata rows removed, leaving `rebalancing`,
   `reconciliation`, `budget` and `paycheck` null exactly as CLAUDE.md
   documents.
2. **A two-transaction portfolio** — one deposit, one buy. No sells (so
   realized is 0 and pct_return has a zero denominator), no options, no
   crypto, no dividends. `monte_carlo` nulls as well.

**Result: clean in both.** All ten tabs render with zero console errors
and no `NaN`, `undefined`, `Infinity` or `null` anywhere in the rendered
text. The four null-block sections **hide** rather than render empty,
and `hideEmptyTabs` correctly sets Options and Crypto to `display: none`
when there is no such activity.

**The detector was validated before that result was trusted** —
injecting `$NaN`, `undefined` and `Infinity` into a live panel flips the
scan from `clean` to detecting all three, and back to `clean` on
removal. A check that finds nothing is worth nothing until it has been
seen to find something; that is the same discipline the prime directive
applies to tests.

**Technique worth reusing.** The plan suggested a node + DOM shim "only
if time remains". Serving the generated dashboard over localhost and
driving it with the browser tool gives real behavioural coverage of the
JS layer for the cost of one script, with no new dependency and nothing
added to the repo. The two degenerate-input builders live in the
session scratchpad; promoting them to `tools/` would make this
repeatable.

### Segment 7 — Tax and reconciliation (2026-08-06, begun)

**Item 4 — ST/LT classification: correct, now pinned.** `_is_long_term`
decides whether a realized gain is taxed at ordinary income rates or
long-term capital gains rates, and it had **no test** — `test_lots.py`
pinned the `_lt_eligible_date` helper for the leap day, but nothing
exercised the function consuming it, and nothing exercised its
day-count fallback at all.

The rule matches IRS Topic 409: counting begins the day *after*
acquisition, so one year completes on the anniversary and "more than one
year" starts the day after. A sale exactly on the anniversary is
short-term.

The test's centrepiece is the disagreement the calendar path exists to
fix: acquired 2024-02-28, sold 2025-02-28 spans **366** days because
2024 is a leap year, so a naive `days > 365` test calls it long-term —
but it lands exactly on the anniversary and is short-term. Getting that
backwards taxes a gain at LTCG rates when the IRS says ordinary. All
three claims (the 366-day span, what the naive test would say, what fin
says) are asserted so they cannot drift apart. Bypassing the calendar
path entirely is caught, which proves the case discriminates rather than
agreeing with both methods.

**Item 2 — tax tables: F-020, a real drift.** The Python tables are
complete and internally consistent (2024–2026, all four filing statuses,
no gaps). But comparing them against the *JS fallback literals* found
the 2025 standard deduction wrong for all four statuses: OBBBA (July
2025) retroactively raised it, `tax.py` was updated with a source
comment, and the JS literal was not.

The fallback only runs when `DATA.tax_tables` is missing — which is
exactly when you are relying on it. An emergency backup silently holding
superseded law is not a backup. Federal brackets, LTCG brackets and the
§1256 underlyings were compared too and all agreed.

Fixed, and `tests/test_tax_table_js_parity.py` now compares the two for
every year they both define. The JS is still allowed to lag by whole
years on purpose — a new tax year is a one-file change in `tax.py` per
the `fin-tax-year-update` skill — so only disagreement about a *shared*
year fails. The test carries a not-vacuous guard: if the parsing regexes
stop matching, or no year is shared, the comparison would pass by
comparing nothing, and both are asserted.

**Item 1 — reconciliation against broker ground truth: fin passes.**
Every `Reconcile *` row in the real ledger — balances, 1099-B realized,
§1256, 1099-DIV/INT income, crypto 1099-MISC other income, across five
account groups and nine tax years — comes out `ok` or `explained`. The
one row carrying a declared cash-sweep expectation lands on exactly that
expectation. This is the technique with the highest consequence per
finding in the whole plan, and it found nothing wrong.

**It very nearly found something wrong that wasn't there — F-021.** The
first run reported three Schwab balance rows breaking by thousands,
including a retirement account off by a large amount. That was entirely an
artifact of the audit's own offline harness: `golden.py` stubbed
`_fetch_splits` to `[]`, which makes
`prices._invalidate_prices_for_split_change` see a symbol's real split
history vanish, drop its cached prices, and — with no way to refetch
offline — freeze it at its last transaction price.

Two things made that catchable rather than reportable. `get_price`
returned correct moving values for the frozen symbols while a
*neighbouring* position in the same account matched `get_price` exactly,
which is not a shape any real pricing bug takes. And the obvious
hypothesis was wrong on its first test: FSELX also carries cached
splits and priced correctly, so "has splits" was not the discriminator
and the theory had to be checked by running `compute_history` directly
with no pipeline fetch step. It priced everything correctly, locating
the fault in the harness.

Blast radius was assessed rather than assumed: A/B golden diffs are
unaffected (both sides degrade identically, so every figure-neutrality
conclusion here still holds) and the Segment 6 smoke runs are unaffected
(they assert on NaN and console errors, not absolute values). Only
absolute figures from an offline run were wrong, and that reached just
this one probe.

The lesson generalises past this bug: **a stub is a claim about the
world, and a wrong one degrades silently.** Returning `[]` looked like
"no data" and was read as "the data changed".

Verifying tables against the IRS source documents themselves (the rest
of item 2) still wants doing, as do items 3 and 5.

### Verified clean (no finding)

Recorded deliberately — a checked-and-clean result is worth as much to
the next segment as a defect, and re-deriving it costs the same as
finding it did.

- **Option intrinsic-floor call sites** (Segment 3's explicit
  sub-task). CLAUDE.md requires the floor at every txn-price fallback,
  and the existing static guard in `test_option_floor_parity.py`
  catches *modules*, not *call sites*. Enumerated all seven documented
  sites — both pipeline paths' `last_prices`, both history walkers,
  `analytics/header.py`, `analytics/daily_pnl.py`,
  `analytics/_shared.py::_value_at_date` — and all seven apply it. The
  two other modules referencing `last_prices` are consumers of an
  already-floored dict, not independent fallbacks:
  `pipeline_stages.build_holdings` is called *after*
  `apply_option_intrinsic_floor` in both paths, and `cash_bridge` only
  sets `USD = 1.0`.
- **Lot-walker branch parity** (Segment 3's main job, partial). The two
  walkers dispatch the same eight basis effects in the same order
  (`add`, `zero_basis`, `remove`, `transfer_out`, `transfer_in`,
  `wrap_out`/`wrap_in`, `split`, and the `ignore`-with-quantity case),
  and `history.py` now imports `_consume_lots`, `_pair_wraps`,
  `_rescale_lots`, `_consume_for_rebase` and `_rebase_is_move` directly
  from `basis.py` rather than reimplementing them. The structural drift
  CLAUDE.md warns about has largely been closed by that sharing. Note
  this is a *branch-structure* check, not a semantic one — Segment 3
  should still compare the bodies.
- **`changes.py`** — see above; already pinned.

---

## Improvement opportunities

Architectural observations surfaced by the audit, kept deliberately
separate from defects. Populated in Segment 9.

- *(Segment 1)* The end-to-end test and the shipped sample portfolio
  are two independent synthetic portfolios maintained in parallel.
  Collapsing them would cover five parsers and remove a duplicate
  fixture — see F-001.
