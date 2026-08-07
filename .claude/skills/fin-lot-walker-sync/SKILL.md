---
name: fin-lot-walker-sync
description: Checklist for changing cost-basis semantics in the `fin` portfolio tracker — new basis effects, wrap/transfer/override rules, lot-relief methods, or anything touching how lots are created/consumed. Use whenever editing basis.py's walker rules, and ALWAYS when a change lands in basis.py that affects lot state. The same rule lives in multiple walkers; this skill lists every consumer that must change in lockstep and the parity checks that catch drift.
---

# Changing basis / lot semantics in `fin`

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
   basis from per-txn `basis_effect` + `cost_basis` annotations (used by
   `--refresh-prices` and the lot-queue parity check).  A new
   `basis_effect` value must be added to its add-set or subtract-set
   (or explicitly documented as a no-op) or the refresh path and parity
   check will drift.

4. **`src/actions.py`** — if the change introduces a new action or a new
   `BasisEffect` literal, the catalog is the single source of truth
   (see the `fin-add-action` skill).

## Adjacent things that often need the same change

- **`cost_basis_overrides.py`** — its `_LOT_CREATING_ACTIONS` set must
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
  consumer bug; do not weaken it.
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
`_apply_split_to_lots`, `basis_override_or`, `fmv_basis`.
`test_lot_walker_parity.py` pins the last three as shared, so re-inlining
one fails.

```bash
python -m pytest tests/test_basis_walker.py tests/test_history.py \
  tests/test_data_health.py tests/test_pipeline_snapshot.py -q
python -m pytest tests/ -q        # full suite before calling it done
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
