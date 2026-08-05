# fin — application audit

Sanitized, committable summary of the audit described in
`PLAN-audit.md`. **No portfolio figures appear here** — mechanism,
files, bug class, and severity only. The working ledger is
`audit/findings.md`, which is gitignored and never leaves the machine.

| | |
|---|---|
| **Started** | 2026-08-05 |
| **Segments complete** | 1 of 9 |
| **Findings** | 5 open / 1 fixed |

Severity: **high** = a displayed number is wrong, or tax/basis is
affected. **medium** = wrong under conditions that haven't occurred
yet. **low** = latent, cosmetic, or a robustness gap.

---

## Findings

Segment 1 is infrastructure and did no bug hunting. Every entry below
is a **coverage** finding — code unprotected by construction — not a
confirmed defect. None claims a wrong number.

| ID | Sev | Claim |
|---|---|---|
| F-001 | medium | The shipped sample portfolio is not exercised by any test; five broker parsers have zero executed lines |
| F-002 | medium | Two basis-walker branches (`_remove_from_avg`, `_apply_split_to_lots`) are never executed |
| F-003 | low | `snapshot.py` has 0% coverage — export/import wholly untested, including the anti-clobber guard |
| F-004 | low | `normalize._amount_sign` is never executed (sign-split family — taxonomy 4) |
| F-005 | low | Never-executed functions in `analytics/tax.py` (`_pick_by_year`), `main.py`, `metadata.py`, `actions.py`, `parsers/_helpers.py` |
| F-006 | low | *(fixed)* `tools/mutate.py` round-tripped source through text-mode I/O, with a restore check that could not detect the corruption |

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

---

## Improvement opportunities

Architectural observations surfaced by the audit, kept deliberately
separate from defects. Populated in Segment 9.

- *(Segment 1)* The end-to-end test and the shipped sample portfolio
  are two independent synthetic portfolios maintained in parallel.
  Collapsing them would cover five parsers and remove a duplicate
  fixture — see F-001.
