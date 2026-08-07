# fin — application audit

Sanitized, committable summary of the audit described in
`PLAN-audit.md`. **No portfolio figures appear here** — mechanism,
files, bug class, and severity only. The working ledger is
`audit/findings.md`, which is gitignored and never leaves the machine.

| | |
|---|---|
| **Started** | 2026-08-05 |
| **Segments complete** | **all nine** (8's sweeps partly absorbed elsewhere) |
| **Findings** | 9 open / 19 fixed |
| **Suite** | 478 → 841 tests, green · `src/` coverage 84.9% → **88.8%** · never-executed functions 23 → 13 |

Severity: **high** = a displayed number is wrong, or tax/basis is
affected. **medium** = wrong under conditions that haven't occurred
yet. **low** = latent, cosmetic, or a robustness gap.

---

## Executive summary

**fin's logic is in good shape. Its risk was concentrated almost
entirely in undefended correctness.**

Across all nine segments, 27 findings, and roughly 300 targeted
mutations, **three live defects** were found in the application:

- **F-017** (high) — a broker changing its date format silently drops
  every row of that file. 7 of 8 sample broker files went to zero rows
  with no exception, no warning, no stderr. The account would simply
  disappear from the portfolio.
- **F-020** (medium) — the dashboard's emergency tax-table fallback
  carried the **pre-OBBBA** 2025 standard deduction for all four filing
  statuses. `tax.py` was updated when the law changed retroactively; the
  JS literal was not.
- **F-026** (medium) — a malformed price-cache entry (a quoted number,
  `Infinity`, a bool) was returned as a *value* rather than skipped,
  flowing straight into `qty × price`. The shards are documented as
  hand-editable, so the trigger is a plausible slip rather than an
  exotic one.

Everything else was code that was **already correct but could be broken
silently**. That distinction is the audit's main result: of ~300
mutations applied to basis, prices, history, tax, parsers and analytics,
the survivors were overwhelmingly missing *tests*, not wrong *rules*.
Every tax rule, every basis rule, every sign split, and every headline
figure computed the right answer when checked against hand-derived
values or broker ground truth.

**Broker reconciliation passes outright**: 31 `Reconcile` rows across
five account groups and nine tax years — balances, 1099-B realized,
§1256, 1099-DIV/INT income, crypto 1099-MISC — all `ok` or `explained`,
with zero unexplained breaks.

| | before | after |
|---|---|---|
| Tests | 478 | **841** |
| `src/` coverage | 84.9% | **88.8%** |
| Never-executed functions | 23 | **13** |
| Source changes | — | 7 in `src/`, 1 in `tools/` |

Every source change was verified figure-neutral against a same-day
golden, or had its scope measured explicitly.

### The three patterns worth carrying forward

**1. Guards pinned only in the firing direction.** The dominant shape by
a wide margin. A test proves a rule *fires when it should* and never
proves it *stays off when it shouldn't*, so the condition guarding the
"off" case can be deleted with the suite green. F-007, F-008, F-009,
F-012, F-013, F-014 and most of Segment 7 are all this. The sharpest
instance is F-012: CLAUDE.md *names a test* that pins
`txn_external_cash_flow`, and that claim is true and insufficient at the
same time — the test covers marker + right account and non-marker +
right account, and never marker + **wrong** account, which is exactly
what the guard rejects.

**Practical consequence:** read every "pinned by <test>" claim in
CLAUDE.md as "pinned in the firing direction" until checked.

**2. Sibling-site asymmetry.** The same rule implemented at several call
sites, with only some protected. `_consume_lots` vs
`_consume_lots_capped` (F-013); the Savings/cash gate at three sites in
`pipeline_stages` (F-014); the Split rule in two walkers (F-015). When
you find a rule at N sites, check all N.

**3. Documented parity claims that nobody tested.** CLAUDE.md asserts
several times that two implementations agree. Three were checked; **two
were false on at least one path** — `_value_at_date` vs `history`
(F-025) and the JS tax fallback vs `tax.py` (F-020). fin now has *three*
valuation implementations (`basis._walk`, the snapshot walker,
`compute_daily_totals`) plus `_value_at_date`, and parity between them
is now tested rather than asserted.

### The audit's own tooling reproduced the bug class three times

Recorded deliberately, because it is the strongest available evidence
for the plan's prime directive:

- `tools/mutate.py`'s restore check compared strings round-tripped
  through the same lossy newline translation it was meant to detect
  (F-006).
- `tools/golden.py`'s offline stub returned `[]` for splits, which reads
  as "the split history changed", invalidating prices for every
  split-carrying symbol. It produced a **large false reconciliation
  break** in a retirement account before being caught (F-021).
- The split-parity test's first draft asserted quantities and totals
  that are *invariant under a split by construction*, so it passed
  whether or not the rule ran.

All three were caught only by *running* a mutation, never by reading the
code. Three further first readings were also wrong — an artifact
mistaken for a break, an equivalent mutant mistaken for a gap, and a
coincidence mistaken for causation. **A surviving mutant is a question,
not a verdict**, and so is a clean result.

---

## Findings

| ID | Sev | Claim |
|---|---|---|
| F-028 | medium | *(fixed)* The refresh path's per-account lot-method plumbing was unprotected — the parity fixture carried the `Lot Method` row but never gave it two candidate lots to choose between |
| F-027 | low | *(fixed)* Corrupt sidecar caches raised a bare `JSONDecodeError` naming neither the file nor the cache |
| F-026 | medium | *(fixed)* A malformed price-cache entry (a quoted number, `Infinity`, a bool) was returned as a value instead of skipped |
| F-025 | medium | *(fixed)* `_value_at_date`'s txn-price fallback was filter-scoped while `history`'s is global — a documented parity that was false |
| F-024 | low | Drawdown values are fractions despite a `_pct` field name — a 100× trap for any future consumer |
| F-023 | low | `_solve_xirr([])` returns −99.99% instead of None (unreachable from the real caller, which guards it) |
| F-022 | low | Two documented limitations in wash-sale detection: same-day repurchases excluded, and no substantially-identical judgement |
| F-021 | medium | *(fixed)* The audit's own offline harness corrupted prices for split-carrying symbols, producing a false reconciliation break |
| F-020 | medium | *(fixed)* The JS tax-table fallback carried superseded 2025 standard deductions — OBBBA updated `tax.py` but not the JS literal |
| F-019 | low | The Performance tab's anchor cards invite an inference that doesn't hold — Total Return is not Realized + Unrealized |
| F-018 | medium | *(silence fixed)* `metadata.csv` silently degraded invalid input; three documented clamps reject-to-default instead of clamping |
| F-017 | **high** | *(tiers 1–2 fixed)* A date-format change silently drops every row in every parser, with no output — 7 of 8 sample broker files went to zero rows in silence |
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

### Segment 2 — Mutation: the invariant core (2026-08-05)

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

### Segment 3 — Prices, history, and walker parity (2026-08-05/06)

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

### Segment 4 — The ingest layer (2026-08-05/06)

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

### Segment 7 — Tax and reconciliation (2026-08-06)

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
first run reported three Schwab balance rows breaking badly,
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

**Item 3 — AGI/MAGI carve-outs: all correct, three were unprotected.**
AGI feeds Roth eligibility, the marginal-rate display and the estimated
capital-gains tax. Existing tests drive `_tax_rate_estimate` mostly with
an *empty* transaction list, which exercises the salary/paycheck side
and leaves the txn-driven rules unprotected. Mutation showed the
retirement-income exclusion and the employer-match filter were caught;
three were not:

- the 401(k) deduction's `account_group` scope. The sharpest of the
  three: without it a **Roth** contribution is deducted from AGI. Roth
  money is post-tax, and understating AGI there corrupts the very Roth
  eligibility figure the contribution belongs to.
- realized gains restricted to Taxable accounts — without it, gains
  realized inside an IRA enter the tax estimate.
- the pretax-deduction clamp — without it a fat-fingered `Paycheck
  Deduction` row drives wages negative and picks a nonsense bracket.

Every fixture supplies both sides of its rule, so a test can only pass
if the condition is doing work. 5/5 now caught; no source changes.

**Item 5 — wash sales: correct, and two limitations now documented.**
Only one of five rules was protected. The ±30-day boundaries are now
pinned from both sides (day 30 inside, day 31 outside, each direction),
along with the loss-only guard, the retirement-loss exclusion, and the
fact that a repurchase *inside an IRA* still triggers a taxable-account
loss — the IRS applies the rule across accounts. `Reinvest` and
`Contribution` count as acquiring transactions, which is what makes a
reinvested dividend the classic accidental wash sale.

Two deliberate limitations are pinned as **behaviour** rather than
asserted as correct (F-022), so changing either is a decision: a
repurchase on the sale's own date is excluded, and fin makes no
substantially-identical judgement (exact symbol only). Both
under-report on a panel titled "Potential Wash Sales", against which the
user still reconciles the broker's 1099-B.

**Segment 7 is complete** apart from verifying the tax tables against
the IRS source documents themselves — the remainder of item 2, which
needs external sources rather than code analysis.

### Segment 5 item 2 — Headline figures, hand-computed (2026-08-06)

Every other part of this audit tests **rules** — does this condition
fire, does that carve-out apply. This item tests **arithmetic**, against
answers derived by hand in the test comments rather than copied from a
run. A test that records whatever the code currently produces pins a
wrong answer just as firmly as a right one, and these are the figures
where a wrong answer is invisible: a TWR off by a factor looks
plausible, where a wrong holdings row does not.

**Modified Dietz** — `r = (EV − SV − net) / (SV + net/2)`. The
mid-period flow weighting is pinned by a case where the naive
alternative (dividing by SV alone) gives 20% and the correct answer is
13.33%, so the two can never be confused. Also pinned: a withdrawal must
not read as a loss, and a flow that dwarfs the balance returns None
rather than a number — a fabricated period return there compounds into
the chain-linked lifetime figure.

**XIRR** — 10% over a year, doubling, a loss, and the compounding check:
+10% in half a year annualizes to 21%, not 20%.

**Drawdown** — −40% peak-to-trough, and that the running peak *resets*
on a new high (100 → 200 → 150 is −25%, not −50%).

All correct. 6/6 mutations caught, no source changes.

Two conventions surfaced that are worth knowing before touching this
code, both now recorded in the tests: `_solve_xirr` takes
**years from t0**, not dates or ordinals; and drawdown values are
**fractions** despite one field carrying a `_pct` suffix (F-024) — the
dashboard multiplies by 100 at render.

Two low findings fell out, both pinned rather than fixed: F-023
(`_solve_xirr([])` returns −99.99% rather than None, unreachable because
the caller guards it — and that guard is now tested) and F-024.

### Segment 5 item 3 — Degenerate inputs and the NaN sweep (2026-08-06)

Individual analytics modules already had degenerate tests. What was
missing was a check on the **composed payload** — 27 blocks built from
each other's output, on inputs where many of them divide by something
that can be zero.

The central assertion is one line: `json.dumps(payload,
allow_nan=False)`. CLAUDE.md explains why that is not stylistic — a
single `NaN` reaches the dashboard as a `NaN` *total*, not a parse
error, because Python's `json.dump` writes a bare `NaN` literal that is
valid JavaScript. That one line covers all 27 blocks, and catches
`Infinity` too, which matters because the top tax bracket's `room_left`
must be `None` and never `inf`.

Eight degenerate ledgers — completely empty, one transaction, a single
snapshot, all values zero, every transaction on one day, a fully
withdrawn account, a zero-cost-basis position, history with no holdings.
**All clean.**

Verified live rather than assumed: injecting a `NaN` into
`header_summary.change_1d` and an `Infinity` into `header_summary.value`
are both caught.

**A correction worth recording.** A third mutation — removing
`concentration.py`'s `total <= 0` early return — survived, and my first
reading of it was that this exposed a test gap. It did not. The
accumulation loop already skips any holding whose value is not positive,
so the early return is redundant and its removal is a genuine
*equivalent* mutant. I had written a docstring asserting the opposite
before checking; investigating it turned a plausible-sounding false
claim into an accurate note about which of three overlapping guards
actually does the work. Same lesson as the F-021 near-miss: **a
surviving mutant is a question, not a verdict.**

### Segment 5 item 4 — `_shared.py` (2026-08-06)

1,067 lines used by everything, so a defect here is portfolio-wide. 79
curated mutations: 32 caught, 47 survived. Most survivors are
zero-boundary flips (`> 0` → `>= 0`) that are near-equivalent, but three
regions had real weight — the USD-skipping rule, the account-filter
scoping, and the option-intrinsic floor comparison, all inside
`_value_at_date`.

**F-025 — a documented parity that was false.** CLAUDE.md says
`_value_at_date` "applies the same valuation rules as
`history.compute_history` … and was verified against every snapshot date
to agree." Nothing tested that, and on one path it did not hold.

Both walkers fall back to the most recent transaction price for symbols
the cache cannot resolve. `history` builds that map **globally**;
`_value_at_date` recorded it *after* the account filter, so under a
filter it never saw other accounts' transactions and could use a staler
price for the same symbol. A security's market price is a property of
the symbol, not of whichever account holds it — so `history` was right.
Fixed by recording the price before the filter.

**Scope measured, not assumed.** The golden export is identical, and the
user's real reconciliation is byte-identical with and without the fix
(31 rows, 22 ok / 9 explained / 0 warn / 0 off) — every symbol in the
reconciled accounts is cache-priced, so the fallback never engages
there today. This is a **latent** fix, not a correction to a displayed
number.

**An attribution I nearly got wrong.** The Robinhood balance row
swung from a large negative break to a small positive one during
this session, and it was
tempting to credit this fix, which landed near it. Reverting the fix and
re-running showed the figures byte-identical: the improvement was
entirely the **F-021 harness fix**. Two changes landing close together
is exactly when a causal claim needs testing rather than asserting.

### Segment 8 — Error paths (2026-08-06)

The one cross-cutting sweep not absorbed by another segment. For each
damaged input, the question was which of three things happens: **raises**
(loud, fine), **degrades** (reduced but honest), or **silent** (a
plausible wrong value — the bad case).

Mostly good news. Truncated and empty price shards already degrade with
a named `"unreadable price shard AAA.json — skipped"` note. `NaN`
literals in a shard were already filtered, confirming the documented
behaviour. A shard whose in-file symbol disagrees with its filename
correctly yields nothing for the filename. A missing `yfinance` raises a
clear `RuntimeError` naming the fix.

**F-026** was the silent one. `get_price`'s guard was
`isinstance(val, float) and isnan(val)` — float-only — so a shard entry
holding `"110.0"` returned the **string**, and `Infinity`, `true`, a
list and a dict all passed through into `val *= _tr_factor_after(...)`
and every downstream `qty * price`. The trigger is realistic precisely
because CLAUDE.md documents these shards as hand-editable and invites
deleting them freely; quoting a number is the easiest slip there is.

Fixed so anything that is not a finite real number is a **gap**, letting
the lookback walk on to an earlier real close — strictly better than
returning None at the bad entry, which would blind the surrounding days.
Two near-misses are pinned: `0.0` is still a real price (a written-off
position genuinely closes at zero), and `bool` is rejected explicitly
because it is an `int` subclass.

**F-027** is the contrast, now fixed. The four sidecar caches —
`price_cache_meta`, `splits_cache`, `sector_cache`, `ticker_renames` —
all die on corrupt JSON with a bare `JSONDecodeError` naming neither the
file nor the cache. Crashing is the safe failure and far better than
silent corruption, but these are the same files the docs invite editing
by hand, and the price shards already show the better pattern.

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

## Post-audit follow-up

### Sample coverage: wrap/unwrap and splits (2026-08-06)

The first two items of the deferred extension list in
`audit/sample-coverage.md`, taken because they were the paths most
likely to break and the least testable.

**Wrap/unwrap (ETH → CBETH → sell)** — basis-carrying, the rule CLAUDE.md
says has broken most often. **A broker stock split** — reaches
`basis._apply_split_to_lots`, which was on the never-executed list and is
duplicated verbatim in `history.py`, with *neither* copy ever run
(F-002 / F-015). That function is now covered; coverage 88.6% → 88.8%.

**The lesson repeated itself, and I walked into it.** Both fixtures need
a trailing **sale** to prove anything: a wrap preserves total basis by
construction, and a split's shares are added by the balance walker
regardless, so quantity and total basis are identical whether or not the
lot-level work ran. I wrote that reasoning into the wrap fixture's
comment and then failed to apply it to the split — mutation showed
`ratio = 1.0` surviving my split assertions. Adding a post-split partial
sale makes the rescale observable (rescaled, 5 shares relieve cost/4;
unrescaled, cost/2) and the mutation is now caught.

That is the same invariance trap that made the first draft of
`test_split_walker_parity.py` vacuous, met again in a different guise —
which suggests it is worth stating as a rule rather than an anecdote:
**when a rule redistributes something without changing its total, no
assertion on the total can see it. Consume the thing.**

### Sample coverage: Lot Method and Cost Basis (2026-08-06)

Item 3 of the deferred list. Both metadata rows change realized gain —
tax — and neither was reachable from sample data.

**Lot Method** (`Coinbase = HIFO`, which is what Coinbase really uses),
made observable by two ADA lots priced 4× apart with the *later* one
expensive, then a sale of one lot's worth: FIFO relieves the cheap lot
for a +550 gain, HIFO the dear one for a −200 loss. Only Coinbase is
overridden, so the effect is attributable to the row rather than to a
changed default.

**Cost Basis**, on an off-platform MATIC `Receive` — which also brings
the external-boundary transfer path into the sample for the first time.
The override is deliberately different from the FMV so the test can tell
which was used.

**And it turned up F-028.** Mutating the `account_methods` plumbing
inside `_refresh_prices_only` **survives the entire suite**, while the
same mutation inside `main()` is caught by the new sample test. So the
refresh path could silently stop honouring per-account lot methods —
which change realized gain, holding period, MAGI and Roth eligibility,
while leaving balances untouched, so nothing else would flag it.

What makes it worth a finding rather than a shrug is that
`test_pipeline_path_parity.py` exists precisely to pin the two paths
together, and its fixture is *not* naive: it carries a
`Lot Method,,,Crypto,HIFO` row **and** two CBETH lots priced 3× apart,
exactly the structure needed to tell HIFO from FIFO. It still misses
this. `PLAN-audit.md` called the shot — *"its fixture had to be enlarged
this session before one of its tests stopped being vacuous. Assume the
others may be too."*

**Diagnosed and fixed.** The cause was measured rather than guessed:
disabling `Lot Method` parsing altogether left every figure in the
parity fixture byte-identical, so the row was **inert**. Every
consumption there has exactly one candidate lot in the pool at the time
— the unwrap sorts before the same-day pricey buy — and with one
candidate FIFO and HIFO pick identically. The fixture tests *ordering*,
not *method*, while its comment claimed the opposite. Adding two lots
that are simultaneously in the pool, priced 5× apart, makes the row
load-bearing and both refresh-path sites are now caught.

One mutation still survives on purpose: disabling `Lot Method` globally
leaves both paths FIFO, so they agree and parity rightly passes. **A
parity test pins agreement, not correctness** — correctness is pinned by
the sample test, which catches it. The two divide the work properly.

The generalisable form is worth keeping next to the "consume the thing"
rule: **a fixture can carry the CONFIG for a behaviour and still not
exercise it.** `Lot Method` needs two candidate lots the way a split
needs a later sale — necessary, not sufficient.

### Follow-up sweep: is the rest of the parity harness vacuous too? (2026-08-06)

`PLAN-audit.md` warned that if one test in
`test_pipeline_path_parity.py` was vacuous, others might be. F-028
proved one was, so the prediction was worth testing rather than
repeating.

Method: mutate each behaviour the refresh path mirrors from `main()` and
see which divergences the parity harness actually catches.

| refresh-path behaviour | verdict |
|---|---|
| per-account `Lot Method` | **was the gap** — now caught (F-028) |
| `ACCOUNT_TYPES` override | caught, and the test carries its own not-vacuous guard |
| cash principal | caught |
| `ACCOUNT_GROUPS` override | survives — but **equivalent**, see below |

**No further vacuity found.** The one survivor has a principled
explanation rather than being a hole: `_refresh_prices_only` loads the
previously-exported `transactions.json`, so every transaction already
carries its `account_group` from `main()`'s run and re-applying the
mapping cannot change the grouping. `ACCOUNT_TYPES` differs because
`account_type` is *recomputed* at holdings-build time
(`ACCOUNT_TYPES.get(acct, "Taxable")`) rather than read off the
transaction — which is exactly why the documented "refresh labels every
account Taxable" bug was possible and this one isn't.

So the harness is in better shape than F-028 alone suggested. It had one
inert fixture row, now load-bearing; the rest of what it claims to pin,
it pins.

### Sample coverage: corporate actions (2026-08-06)

Item 8 of the deferred list, and the last one with a whole module behind
it. `src/reorgs.py` exists specifically to hold corp-action pooling and
classification, and nothing reached it through a real run — its only
exercise was its own unit tests. That split matters here more than
usual, because the pooling is a **parser** behaviour whose consequences
land in the **basis walker**, and the two had never been checked
together.

Three shapes added to the sample, chosen as the ones that fail most
quietly:

- **A cash merger** — `MRGS` with an `S`-suffixed quantity (shares
  surrendered, no money) plus a paired `MRGC` (the cash), pooled on
  (date, symbol) into one Sell at the MRGC price. Both failure modes are
  invisible: misread the suffix and the surrender becomes a *receipt*,
  doubling the position while the cash arrives unattached; miss the
  MRGC and the Sell books zero proceeds, turning a gain into a total
  loss of basis.
- **A stock-for-stock merger** — the target surrenders with no MRGC
  (Sell at $0), the acquirer's shares arrive as an unsuffixed `MRGS`
  (Buy at $0).
- **Cash in lieu** — the disposal quantity exists *only* inside the
  description string.

`reorgs.py` 93% → 95%; overall coverage 88.8% → **89.5%**, never-executed
functions 16 → 13.

**"Two of each" — a third form of the same trap.** The first fixture
carried only the cash merger, and mutation found that
`is_surrender = True` (assume *every* MRGS is a surrender) **survived
every assertion**. With no receipt anywhere in the sample, reading the
suffix correctly and ignoring it entirely produce identical output. The
stock-for-stock merger exists to supply the second case, and adding it
takes the run to 5/5 caught.

That now completes a trio of the same underlying shape, and the three
are worth stating together because each looked like a different problem
at the time:

| trap | needs |
|---|---|
| a rule that redistributes without changing a total (split, wrap) | a later **sale** |
| a config row with only one candidate (`Lot Method`) | two **lots** |
| a flag with only one input value (`S` suffix) | two **rows** |

All three are the same question — *does the fixture contain the thing
the code chooses between?* A fixture that contains only one side of a
branch cannot tell a correct branch from a missing one.

**The documented trade-off is now pinned rather than merely described.**
The parser notes that the stock-for-stock path is balance-accurate but
not tax-accurate: the surrender realizes the full basis as a loss and
the $0-basis receipt carries it forward as unrealized gain. The sample
sells the acquirer's shares afterwards so the test can assert the
property that shortcut relies on — across both symbols, total realized
equals proceeds less the original cost. If someone later "fixes" the
surrender to carry basis, that assertion moves, which is the point.

### The reverse split closes somewhere else entirely (2026-08-06)

The last deferred item was "a reverse split", carried as a sub-note of
the split fixture. Chasing it changed what the item *was*.

**Two questions were hiding under one name.** Reverse splits reach fin
by two independent routes, and only one of them is about transactions:

1. **The transaction side.** Checking real broker data shows Robinhood
   reports a reverse split as `SPR` rows using the same S-suffix
   convention as `MRGS` — surrender the old shares, receive the new
   ones. That is the *same parser branch* the stock-for-stock merger
   added an hour earlier, differing only in a description string. A
   sample row would have added a label, not a path.
2. **The price side.** `split_factor_since` converts an as-of-date share
   count into today's basis so it can be multiplied against yfinance's
   always-split-adjusted `Close`. **The sample structurally cannot reach
   it**: split history comes from yfinance, never from the CSVs, so no
   snapshot bundle can carry one.

CLAUDE.md is unambiguous about which one carries the risk — *"never
multiply a historical balance by a historical cache price without
applying `split_factor_since` first ... you'll get a wildly wrong
number"* — and it names reverse-split penny stocks as the case. Every
snapshot, daily-total walk and TWR period boundary runs through that
function.

**Coverage said it ran; mutation said nothing checked it.** The loop
executes on ordinary runs, so line coverage looked fine. Two mutants
survived all 855 tests:

| mutation | consequence |
|---|---|
| `if d > target` -> `if d >= target` | a split dated the same day as the balance gets applied, when that day's close is already post-split — exactly one snapshot off by the full ratio |
| drop the direct-proxy forward | every proxied holding silently un-splits |

The first is the interesting one, and not only because the docstring
specifies "strictly after". A *systematic* error is visible; this one
misprices a single sample date, which reads as a one-day spike in the
history chart rather than as a bug.

`tests/test_split_factor.py` now pins the function directly: the
boundary from both sides, splits multiplying only from the correct side
of the date (with *different* ratios, so the wrong subset cannot land on
the right product), reverse ratios shrinking rather than growing, direct
proxies forwarding and scaled proxies deliberately not. 6/6 mutations
caught by the new file alone, including a reciprocal that a forward-only
fixture would have missed. `split_adjust_qty` came off the
never-executed list; 13 -> 12.

**The generalisable point:** the deferred list was written in terms of
*fixtures the sample lacks*, which quietly assumes every gap is
reachable from sample data. This one was not — and the sample-shaped
framing was steering toward a fixture that would have exercised an
already-covered branch while leaving the real gap open. Worth asking of
the remaining backlog: is this a missing input, or a missing test?

### F-002's remainder: the dead average-cost helper (2026-08-06)

`basis._remove_from_avg` was the last non-network entry on the
never-executed list. It is not merely uncovered — it is **never called**,
and `git log -S` shows it was born that way in the repo's initial
import. The live average-cost path is inlined in `_consume_from_key`.

That is bug-class #1 in its most durable form: a second implementation
nobody runs, sitting beside the one everybody does. Nothing was wrong
*today*; the hazard is that the next person to fix average-cost relief
has a 50/50 chance of fixing the copy that does not execute, and the
tests would agree with them.

**The dead copy was also the better one.** It carried a full-liquidation
snap the live path lacks. Subtracting `take * (total_basis / total_qty)`
from `total_basis` is not bit-identical to zero, so exiting a position
completely leaves basis behind on a position with no shares — measured
at **~10% of full exits, worst case ~6e-11** over 200k randomized fills.

Nowhere near a displayed figure, so this is a hygiene fix rather than a
defect. It is worth doing anyway because it is a *standing* wrong state
rather than a transient rounding artifact: a re-entry into the same
position averages the residual into the new basis, and "zero shares,
zero basis" is a cleaner invariant than "zero shares, approximately
zero basis" — the latter cannot be asserted exactly, so nothing can pin
it.

Dead copy deleted, snap moved into the live path, invariant now tested
exactly rather than approximately. 4/4 mutations caught.

**One survivor was worth chasing rather than waving off as equivalent.**
Replacing `t_state[0] = 0.0` with `t_state[0] = total_qty - take` looks
equivalent, because on a full exit `take == total_qty`. But the branch
fires on `take >= total_qty - 1e-12`, so `take` can be *within*
tolerance without equalling — which is the realistic case, since a pool
assembled from many fills rarely sums to a round number. Inside that
window the subtraction leaves a fractional share on a fully-exited
position. Reachable, so it got a test rather than a shrug.

Never-executed functions 12 → 11; the remainder are network I/O
(`_fetch_splits`, `_fetch_dividends`, `_batch_fetch_ranges`,
`_fetch_from_yfinance`) plus two trivial accessors.

### Improvement #1 done: the shared valuation kernel (2026-08-06)

The structural fix this audit kept pointing at. Five sites answered
"what is this position worth on this date" independently —
`history`'s snapshot walker, `history.compute_daily_totals`,
`_shared._value_at_date`, `analytics/daily_pnl`, `analytics/header` —
and the response to that had been an invariant in CLAUDE.md plus a
`fin-lot-walker-sync` skill to enforce it by hand. A process fix for a
structural problem. `src/valuation.py` now owns the ladder: **cash →
cache price → transaction price floored at option intrinsic →
unpriceable**, plus `is_dust` (moved from `pipeline_stages`) and the
near-zero cutoff.

**Reading them side by side is what found the drift.** None of it was
visible from any single file:

| divergence | status |
|---|---|
| three sites dropped the ×100 option multiplier on the cache-priced branch while applying it on the transaction branch | **latent** — `_classify_no_fetch` skips multi-word symbols, so the cache branch cannot currently fire for a contract |
| `history`'s inline dust rule documented as mirroring `is_dust`; it kept the small negative fractional positions `is_dust` drops | **latent** — measured 0 affected rows across 8,635 position rows in the current real export |
| `_value_at_date` had no dust filter at all, so a position history discards still moved a TWR period boundary | latent, same measurement |
| near-zero cutoff was `1e-12` / `1e-9` / absent depending on the site | immaterial |

**Every one is latent, and that is the finding.** Not one of these was
producing a wrong number today. Three of them were one plausible change
away from producing a badly wrong one — the multiplier gap is a 100×
error the moment an option symbol acquires a cache price, and nothing in
`valuation`'s callers guarantees `_classify_no_fetch` stays shut. The
value of the consolidation is not the bugs it fixed but the bugs it
makes unreachable.

The refactor is behaviour-preserving by measurement, not by assertion:
golden identical at every one of the five rewiring steps, 909 tests
green, and the dust change checked against the real export rather than
only the sample (which structurally lacks the shape). Net −61 lines
across the five sites.

**The consolidation exposed a gap it did not create.** Mutating the
kernel is caught 9/9. Mutating the `restate_qty` flag at three separate
call sites **survived all 909 tests** — flipping a site between
as-of-date and today-basis quantities changed nothing any test noticed,
and that flag is the difference between a correct historical value and
one off by the entire split ratio.

The reason is the one this audit had just finished writing down: split
history comes from yfinance's cache, never from transaction data, so no
CSV fixture and no snapshot bundle can carry one. Every test ran with
`split_factor_since` returning 1.0 for every symbol, which makes both
bases produce identical numbers. The sample even has a `Stock Split`
transaction row — it reaches the *lot* rescale and says nothing about
this.

That is the fourth instance of one shape, and the table from the
corporate-actions entry extends cleanly:

| trap | needs |
|---|---|
| a rule that redistributes without changing a total | a later **sale** |
| a config row with only one candidate | two **lots** |
| a flag with only one input value | two **rows** |
| a quantity-basis flag with no splits in the fixture | a **split** |

`tests/test_restate_qty_wiring.py` seeds the splits cache directly and
drives all five sites; 5/5 mutations now caught. Worth noting the
refactor did not *cause* this — the flag's predecessors (inline
`split_factor_since` calls at three sites, absent at two) were equally
unpinned. Consolidating just made it a single named parameter that a
mutation could aim at.

**What replaced the static guard.**
`test_option_floor_parity.py` asserted that any module mentioning
`last_txn_price` also mentions `option_intrinsic` — a source-text check,
which is what you reach for when a rule *cannot* live in one place. It
can now, so the guard accepts routing through `valuation` as the
discharge. That delegation needed its own guard: if the floor were ever
removed from the kernel, the static check would go quiet for every site
at once, which is the exact inverse of its purpose. So there is now a
companion test that the kernel really does floor. **A test that can be
satisfied by delegation needs a test that the delegate still does the
work.**

Remaining under this heading: `basis._walk` and history's inline *lot*
walker are still two implementations. That is a different problem — lot
state, not valuation — and the `history_holdings_basis_parity` check
plus the `fin-lot-walker-sync` skill still carry it.

### F-017 tier 3: the warning reaches the dashboard (2026-08-06)

Tiers 1 and 2 detect rows a parser dropped and print to the console.
That is the one place a daily run's output is least likely to be read,
carrying the highest-severity failure this audit found: a broker changes
its date format, every row fails to parse, and the account simply
vanishes from the portfolio. Nothing downstream reliably catches it —
What's-Changed reports it only after the fact, and the empty-run guard
fires only when EVERY file is empty, so one missing broker sails
through.

**Why it needed out-of-band capture rather than a downstream check.**
A file that parses to zero leaves no transactions behind. There is
nothing in `txns`, `holdings` or `history` to notice its absence
against, so no consumer of those can reconstruct it. `parsers` now keeps
a per-file report (rebuilt each `parse_all_files` call, so it always
describes the run that produced the transactions in hand), and
`compute_data_health` reads it.

Severity splits on what the evidence supports, which is the part worth
getting right: a **zero-row file is `high`** (an entire account is
missing; there is no benign reading) and **dropped rows alone are
`warn`** (one malformed row in an export is ordinary). Collapsing them
to one severity would have made the loud one unreadable.

No dashboard change was needed — the panel groups by free-text
`category` and renders any issue shape.

**Three things caught on the way in**, worth recording because two are
the audit's own machinery working and the third is this entry's own
lesson applied one level up:

1. `test_data_health_guards.py`'s meta-guard failed the moment the new
   `high` check landed, demanding a fire/near-miss pair. That test was
   written in Segment 2 precisely to stop check #11 shipping
   unprotected, and it did.
2. Grouping by free-text category means a synonym silently creates a
   second heading for one concept — I introduced `"Data integrity"`
   next to the established `"Integrity"` and only noticed when reading
   the renderer. Now normalised, with a test pinning the vocabulary
   closed. A string that becomes a UI grouping key is an enum wearing a
   disguise.
3. **Mutation found the check was not pinned as WIRED.** Deleting the
   `issues.extend(...)` line from `compute_data_health` survived all 931
   tests. Every test written for this feature proved the detector
   detects and the report records; none proved the two ever meet — so
   the whole feature could have been disconnected and stayed green.
   That is the failure tier 3 exists to prevent, recurring one level up:
   perfect detection reaching nobody.

   It is also the same shape as the option-floor delegation guard from
   the valuation kernel. **"The component works" and "the component is
   connected" are different claims, and a suite full of the first reads
   like coverage of the second.** Both the explicit-argument path and
   the default-lookup path are now pinned — the production caller passes
   no report, so a broken default would lose the check on every real run
   while the explicit test stayed green.

### The warn/info data-health checks get their guards (2026-08-06)

The last of the deferred test work. Segment 2 gave every **high**-severity
check a fire/near-miss pair, because those raise `InvariantViolation` and
the whole suite leans on them. The twelve **warn/info** checks were left,
and the reason they were left is the reason they mattered: nothing else
in the suite ever exercises their violating branch, because they never
raise. A `high` check that stops detecting takes tests down with it. A
`warn` check that stops detecting just goes quiet — and **an empty panel
reads as "all clear"**, so the failure presents as good news.

Twelve checks now have both halves, plus a thirteenth
(`_check_held_symbol_price_health`) in its own class, since it takes a
`cache_dir` and reads cache sidecars rather than plain dicts. 9/9
representative mutations caught; 972 tests.

The near-miss half does more work here than it does for the `high`
checks. Every one of these is a *threshold* — 95% priced coverage, a
100× basis-to-price ratio, ±500% TWR, $1k of unclassified sector, $50
of Coinbase bridge endpoint. A threshold set too loose is invisible; a
threshold set too tight lights the panel permanently, and a permanently
lit warning is indistinguishable from no warning while also burying the
`high` items next to it.

**Three meta-guards now stand behind the set**, and each closes a way a
future check could arrive unprotected:

1. every `high` check appears in `GUARD_CASES` (Segment 2);
2. every `warn`/`info` check appears in `SOFT_GUARD_CASES` — or in the
   explicit `_COVERED_BY_A_FIXTURE_CLASS` map, whose named classes are
   themselves asserted to exist, so the exemption cannot quietly become
   a hole;
3. **every check is actually called by `compute_data_health`.**

The third came directly from the F-017 mutation survivor and generalises
it. All 24 checks are wired today; the point is that nothing said so,
and a detector nobody calls is indistinguishable from a detector that
finds nothing.

### F-029: the tax tables, checked against the IRS documents (2026-08-06)

The last deferred item, and it found a real error.

Everything about the tax tables was already verified *except the one
thing that mattered*: they were internally consistent, Python and JS
agreed, and `tax_tables_to_json` was the single source for both. All
true of figures that are simply wrong. Nothing had ever compared a
number to what the IRS published.

**F-029 — 2026 Head-of-Household 24% bracket ceiling was $201,775; the
IRS sets it at $201,750.** Confirmed against Rev. Proc. 2025-32 §4.01
two ways: the printed ceiling, and the Rev. Proc.'s own cumulative-tax
figure, which reproduces as exactly $39,207 at 201,750 and not at
201,775.

The interesting part is *how* it got there. In **2024 and 2025 the HoH
and Single 24% ceilings were the same figure**; the IRS split them by
$25 for 2026, and the table carried the old pattern forward with
Single's value. Not a typo — an inherited assumption that stopped being
true. That is the failure mode a source check catches and no amount of
internal consistency ever will, because the wrong value is perfectly
self-consistent.

**Impact: none for this user, and small in general.** Filing status is
Single, so the HoH table is never consulted; the golden confirms the fix
moves exactly ONE leaf value — the constant itself — with no computed
figure downstream. For an actual HoH filer the error is worth about $2,
inside a $25 band of taxable income.

Everything else verifies clean against source: all four 2026 ordinary
schedules (the other three), all four 2026 capital-gains breakpoints,
2026 and OBBBA-amended-2025 standard deductions, the 401(k) elective
deferral limits, and the Roth MAGI phase-out ranges including the
deliberately non-indexed MFS band.

`tests/test_tax_tables_vs_irs.py` transcribes each figure with its
citation, so next year's update has something to diff against rather
than a second copy of what the code already says. It also pins the
HoH-vs-Single divergence by name, since re-copying that column is the
natural mistake.

One thing noted, not fixed: the Roth phase-out table keys its years by
`int` while every other table in the export uses `str`. Harmless through
`json.dumps`, which stringifies either way, but a Python consumer of
`tax_tables_to_json()` has to know which table it holds. The test
asserts the contract that actually matters — string-addressable after a
JSON round trip, which is how the dashboard reads it.

### Segment 8's error-path sweep: the yfinance boundary (2026-08-06)

The last named segment item, and it turned out to be the same item as
F-005. The six functions with **zero executed lines** —
`prices._fetch_splits`, `prices._fetch_dividends`,
`prices._batch_fetch_ranges`, `sectors._lazy_yf`,
`sectors._fetch_from_yfinance`, `sectors.get_sector` — are precisely the
code that talks to the outside world. They are the only inputs the
project does not control, and none of their failure handling had ever
run.

Tested by injecting a fake `yfinance` into the module-level `_yf` global
that `_lazy_yf` memoises: no network, no new dependency, and the fakes
misbehave the way an outage does — raising, returning nothing, returning
NaN, returning a partly-populated frame.

**The contract worth stating is the split between raising and
swallowing, which is deliberately different at each site:**

| site | on failure | why |
|---|---|---|
| `sectors.*` | degrade to `"Other"`, never raise | a sector is cosmetic; a lookup failure must not stop a run |
| `prices._fetch_splits` / `_fetch_dividends` | **raise** | `ensure_coverage` owns retry/backoff/tombstoning and can only do it if it hears about the failure. Returning `[]` is indistinguishable from "this symbol has never split" — and would be cached as fact |
| `prices._batch_fetch_ranges` | return `None` | `None` means one specific thing: fall back to the per-symbol serial path, which owns the bookkeeping. A raise would take the run down over an outage the serial path absorbs |

Getting those three backwards is invisible until the day yfinance is
down, which is exactly when you least want to discover it. The empty
frame is the subtle one: a weekend range legitimately returns no rows,
and that must be `{}` (zero symbols fetched), not `None` (go re-ask
serially and book a failure against every symbol).

10/10 mutations caught. Never-executed functions 11 → 5, coverage
89.7% → 91.1%, 1030 tests.

### Zero never-executed functions (2026-08-06)

F-003, F-004 and F-005 closed together. Coverage's second table —
functions with zero executed lines — is the actionable one, because an
unexecuted line is unprotected *by construction*: no test enters it, so
no test can notice it breaking. It started this audit at **23** and is
now **empty**.

The last five were `snapshot.export_snapshot` (F-003 — half of the
move-between-machines feature; every test imported the shipped sample,
none produced one), `normalize._amount_sign` (F-004, the sign-split
family), `analytics.tax._pick_by_year`, `actions.names_with_basis_effect`
and `main._usaa_position_before_date`.

None of them was wrong. That is the expected outcome and not the point —
until now any of them could have been deleted or inverted with the suite
green. 8/8 mutations caught, including two whose correct behaviour is
easy to state backwards: `_pick_by_year` must fall **back** to an
earlier year (falling forward would apply figures that are not yet in
effect), and `_usaa_position_before_date`'s as-of comparison is `>`, so
same-day rows count.

Final state: **1067 tests, 91.7% line coverage, 0 never-executed
functions.**

## Improvement opportunities

Architectural observations surfaced by the audit, kept deliberately
separate from defects. **None of these is a bug**; each is a place where
the current shape makes a future bug more likely, ordered by leverage.

**1. Four valuation implementations, pairwise-asserted rather than
shared.** `basis._walk`, `history`'s snapshot walker,
`history.compute_daily_totals`, and `_shared._value_at_date` all decide
what a position is worth. CLAUDE.md documents the invariant ("if you
change a basis rule, change BOTH walkers") and a `fin-lot-walker-sync`
skill exists to enforce it by hand. Two of the three parity claims
checked turned out false (F-020, F-025), and the Split rule is
duplicated verbatim (F-015).

The parity is now *tested* rather than asserted, which is the cheap fix.
The structural fix is to extract the shared valuation kernel — the
balance walk, the USD-outside-Savings rule, the price lookup with
txn-price fallback and option-intrinsic floor — so the rule exists once.
`history.py` already imports `_consume_lots` / `_pair_wraps` /
`_rescale_lots` from `basis.py`, so the precedent and the appetite both
exist.

**DONE 2026-08-06** for the *valuation* half — `src/valuation.py` now
owns the price ladder, the dust filter and the near-zero cutoff, and all
five sites (the three named above plus `analytics/daily_pnl` and
`analytics/header`, which this entry missed) call it. Write-up below.
The *lot-walker* half — `basis._walk` versus history's inline copy —
is still two implementations and still carried by the
`history_holdings_basis_parity` check.

**2. The sample portfolio and the e2e fixture are two parallel synthetic
portfolios.** Collapsing them would remove a duplicate fixture and was
what closed F-001. `audit/sample-coverage.md` listed what the sample
could not reach — wrap/unwrap, option exercise, `Cost Basis` and
`Lot Method` overrides, rollover bridges, balance anchors, corporate
actions, splits spanning a snapshot. Every one was a rule a later
segment had to test in isolation because no end-to-end path reached it.
**Eight of nine are now in the sample**; a reverse split is the
remainder.

**3. `_pct` field names that hold fractions** (F-024). `max_drawdown`
and `current_drawdown_pct` carry the same units under different naming
conventions. Harmless today because both consumers agree; a 100× trap
for the next one.

**4. The JS emergency fallbacks are unmaintained by design.** F-020
found superseded tax law in one. They now have a parity test, but the
deeper question is whether fallbacks that only run when the export is
malformed earn their keep at all — a missing `DATA.tax_tables` is
arguably better surfaced as an error than silently papered over with
year-old constants.

**5. A schema version on the export.** The dashboard is generated with
its data, so version skew is not possible today — but `tools/golden.py`,
`audit/golden/export.json` and any future external consumer all parse
the export, and none can tell which shape it is.

**6. Metadata rejection semantics** (F-018). Three fields *reject* an
out-of-range value to their default rather than clamping to the bound.
That is defensible and now documented and announced — but it is a
choice, and the alternative (clamp, or refuse to run) is worth a
deliberate decision rather than inheritance.

### Deferred audit work

- ~~**F-017 tier 3**~~ — **DONE 2026-08-06**. Dropped rows and zero-row
  files now surface as `data_health` checks (`high` for a whole missing
  account, `warn` for dropped rows), so they land in the dashboard panel
  rather than only the console. Write-up above.
- ~~**Segment 8's remaining sweeps.**~~ — **DONE 2026-08-06**. Most were
  absorbed by other segments (NaN/Infinity by Segment 5 item 3, as-of
  alignment by Segment 5 item 1, vacuous guards by the whole audit,
  ordering by the sign-split and consume-order work). The **error-path
  sweep** was the genuine remainder and is now closed: corrupt caches by
  F-026/F-027, and the yfinance boundary by the write-up above.
- ~~**Tax tables against the IRS source documents.**~~ — **DONE
  2026-08-06**. Checked against Rev. Proc. 2025-32 and Notice 2025-67.
  Found F-029 (2026 Head-of-Household 24% ceiling off by $25); every
  other figure verifies clean. Write-up above.
- ~~**The 13 `warn`/`info` `data_health` checks.**~~ — **DONE
  2026-08-06**. All thirteen have fire/near-miss pairs, and three
  meta-guards now stand behind the whole set (high covered, soft
  covered, and every check actually wired into `compute_data_health`).
  Write-up above.
