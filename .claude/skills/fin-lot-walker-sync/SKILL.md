---
name: fin-lot-walker-sync
description: Change cost-basis or cash-principal semantics in finledger. Use for lot creation/consumption, relief methods, transfers, wraps, overrides, or basis effects that must agree across annotated and historical walkers.
---

# Changing basis / lot semantics in `fin`

All paths below are repository-relative. Read the relevant
[cost-basis invariants](../../../docs/INVARIANTS.md#invariants-the-cost-basis-walker-relies-on)
and use [fin-dev-loop](../fin-dev-loop/SKILL.md) for isolated fictional inputs.

**The bug class this skill exists for**: basis-rule changes landing in
`basis.py` but not its parallel implementations.  It has happened twice
in this repo's history — wrap basis-carrying (05398e7) and FMV
transfer-in basis (9cdefb9) both shipped in `basis.py` only, silently
desyncing the Overview chart's Cost Basis line and the as-of-date
holdings view from the Holdings table for months.

## The walkers that must agree

A basis rule lives in up to FOUR places.  Change all that apply:

1. **`basis._walk`** (`src/basis.py`) — the authoritative annotated
   walker.  FIFO default, per-account overrides via `account_methods`
   (`_method_for`), `basis_override` honoured via `_ov` on lot-creating
   branches (add / unpaired transfer-in / unpaired wrap-in).

2. **`history.compute_history`'s inline lot walker** (`src/history.py`)
   — needs lot state at every sample date, so it re-implements the walk.
   It reuses `basis._consume_lots` / `_pair_wraps` / `_rescale_lots`
   and takes the same `account_methods`; keep every branch (add /
   zero_basis / remove / transfer_out / transfer_in / wrap_out+wrap_in /
   split) mirroring `basis._walk`.  If you add a branch to one, add it
   to the other — the docstring carries the same INVARIANT note.

3. **`basis.derive_basis_by_key_from_txns`** — reconstructs running
   basis from per-txn `basis_effect` + `cost_basis` annotations for the
   lot-queue parity check. Published basis figures in both pipeline paths
   come from walker state, never this rounded reconstruction. A new
   `basis_effect` value must be added to its add-set or subtract-set
   (or explicitly documented as a no-op) or annotation parity will drift.

4. **`src/actions.py`** — if the change introduces a new action or a new
   `BasisEffect` literal, the catalog is the single source of truth
   (see the `fin-add-action` skill).

## Cash: the basis rule with no lot queue

A Savings cash position (`USD` in an account typed `Savings`) never
enters a lot queue — `basis_effect_for` returns `"ignore"` for every
cash row — so neither walker can read its basis off lot state.  Both
walk it separately, and both must call
**`pipeline_stages.cash_principal_effect`**:

- `compute_cash_principal` → `build_holdings` (the Holdings table)
- `history.compute_history`, incrementally beside the balance walk, so
  each snapshot carries principal as of ITS date

The convention is **principal, not face value**: basis moves only with
external cash flow (the helper delegates to
`basis.txn_external_cash_flow`), so interest earned surfaces as the
account's unrealized gain.  Both directions of getting this wrong are
real regressions, so pin whichever you touch:

- Making the two walkers *disagree* is the bug that shipped — one date,
  one position, two answers, visible on the Performance tab as an
  Unrealized card that changed when only the time window changed
  (docs/AUDIT.md F-035).
- Making them *agree on face value* would also make the parity check
  pass, and would silently delete the HYSA's entire reported return.

Derive from the cash-flow classifier, never a local action list.  The
rule was a literal `Deposit`/`Withdrawal` check until `balance_anchor.py`
began synthesizing `Cash Back` rows (`cash_flow="in"`), at which point
the same dollars were contributed capital to `net_contributed` and
investment return to the Holdings table.

## Adjacent things that often need the same change

- **`cost_basis_overrides.py`** — its `_ADD_ACTIONS` set must
  include any new lot-creating action or user `Cost Basis` rows won't
  match it.
- **Balance side**: `main.py` / `pipeline_stages.walk_balances` derive
  sign from the catalog automatically, but check the **balance↔basis
  parity** rule: for every non-USD `(account_group, symbol)`, the final
  lot-queue quantity must equal the running balance.
- **`analytics/tax.py`** — if realized-gain annotation shape changes
  (`lot_breakdown`, `holding_days`), `_classify_realized` and
  `_build_form_8949` consume it.

## The parity checks that catch drift (run them!)

All enforced as high-severity in tests via `FIN_ASSERT_INVARIANTS=1`:

- `lot_queue_parity_drift` — txn-annotation reconstruction vs holdings
  table (catches #3 drift).
- `history_holdings_basis_parity` — latest snapshot per-position basis
  vs holdings table (catches #2 drift).  Added after the second missed-
  consumer bug; do not weaken it.  **It used to skip `symbol == "USD"`,
  which made it pass by declining to look at the only positions where
  the two cash conventions could differ.**  If a check needs an
  exemption to go green, the exemption is the finding — verify the
  exempted class agrees instead of carving it out.
- `negative_cost_basis`, `zero_qty_with_basis`, `negative_holding_days`.

**Plus a STRUCTURAL check that needs no fixture:**
`tests/test_lot_walker_parity.py` asserts both walkers dispatch on the
same set of basis effects, that every effect they branch on is one the
catalog actually produces, and that every effect the catalog produces is
handled. The numeric checks above only compare the LATEST snapshot for
symbols some fixture reaches — a branch present in one walker and absent
from the other stays invisible to them until data arrives at it. That is
precisely how the two documented misses happened.

**Prefer extraction over lockstep.** The strongest version of this
checklist is not following it: if the rule can live at module level in
`basis.py` and be called from both walkers, put it there. Already
shared: `_consume_lots`, `_consume_lots_directed`,
`_consume_lots_reserving`, `_consume_for_rebase`, `_pair_transfers`,
`_pair_wraps`, `_rescale_lots`, `_rebase_is_move`,
`_apply_split_to_lots`, `basis_override_or`, `fmv_basis`, and — since
2026-08-25 — `basis_effect_for` (the symbol-aware effect classifier),
`reserved_for` (future-demand lot reservation), `wrap_kind`,
`zero_basis_origin`, and `wrap_carry_lots` (the whole basis-carrying
half of a wrap: which source lots go and how they rescale).  Since
2026-09-04, `pipeline_stages.cash_principal_effect` too — the one
shared rule that lives outside `basis.py`, because cash basis is not
lot state.
`test_lot_walker_parity.py` pins ALL of these as shared — each has a
test that fails if a walker re-inlines it, several asserting the inlined
form is absent rather than just that the call is present.

```bash
uv run python -m pytest tests/test_basis_walker.py tests/test_history.py \
  tests/test_lot_walker_parity.py tests/test_cash_basis_parity.py \
  tests/test_data_health.py tests/test_pipeline_path_parity.py tests/test_pipeline_snapshot.py -q -rs
uv run python -m pytest tests/ -q -rs        # full suite before calling it done
```

`test_pipeline_snapshot.py` (end-to-end synthetic portfolio) is the
safety net — if the numbers move, either your change is wrong or the
snapshot's known-good figures need a *justified* update.

## Test additions expected with any rule change

- A `basis.py`-level test in `tests/test_basis_walker.py` proving the
  new rule's realized/basis math.
- A `history.py` mirror test in `tests/test_history.py` asserting the
  latest snapshot's per-position `cost_basis` matches
  `state_to_holdings(compute_basis_default(txns), "fifo")` — see
  `test_history_wrap_carries_basis_to_destination` for the pattern.
- For a cash-basis change, `tests/test_cash_basis_parity.py` — it runs
  both walkers over one savings ledger and asserts they report the same
  figure, that the figure is principal rather than face value, and that
  the data-health check still flags a seeded disagreement.

**Verify any new parity test is non-vacuous**: run it against the prior
behavior in an isolated checkout or otherwise demonstrate the failure it catches.
Do not revert shared working-tree changes while other agents are editing them.
A parity test that passes against the broken code is the failure mode this
whole skill is about.
