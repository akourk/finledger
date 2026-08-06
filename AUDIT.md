# fin — application audit

Sanitized, committable summary of the audit described in
`PLAN-audit.md`. **No portfolio figures appear here** — mechanism,
files, bug class, and severity only. The working ledger is
`audit/findings.md`, which is gitignored and never leaves the machine.

| | |
|---|---|
| **Started** | 2026-08-05 |
| **Segments complete** | 1 of 9, Segment 5 item 1 (pulled forward), Segment 2 partial |
| **Findings** | 7 open / 6 fixed |
| **Suite** | 478 → 554 tests, green |

Severity: **high** = a displayed number is wrong, or tax/basis is
affected. **medium** = wrong under conditions that haven't occurred
yet. **low** = latent, cosmetic, or a robustness gap.

---

## Findings

| ID | Sev | Claim |
|---|---|---|
| F-012 | medium | *(fixed)* `txn_external_cash_flow`'s carve-outs are pinned only in the firing direction, contrary to the documented claim — all three `and` guards could be deleted with the suite green |
| F-013 | medium | *(fixed)* `_consume_lots_capped`'s descending-index lot removal was unprotected while its identical sibling in `_consume_lots` was |
| F-007 | high | *(fixed)* No test had ever tripped any of the 23 `data_health` checks — the suite's own safety net was entirely unverified |
| F-008 | medium | *(fixed)* The reconciliation panel's break-flagging path was never exercised — no test produced a balance row that wasn't "ok" |
| F-009 | medium | *(fixed)* `alerts.py` had no test file; five of seven alert sources had never been emitted |
| F-010 | low | The coverage-gap alert's lookback window silently halved when the history cadence went semimonthly |
| F-011 | low | A malformed `[expected ±N]` token is indistinguishable from no token, and its error branch is unreachable |
| F-001 | medium | The shipped sample portfolio is not exercised by any test; five broker parsers have zero executed lines |
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
