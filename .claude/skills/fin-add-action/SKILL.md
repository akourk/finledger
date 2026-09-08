---
name: fin-add-action
description: Add a canonical financial action to finledger when no existing action has the required semantics. Covers action and income classification, normalization, consumers, and behavioral parity checks; map ordinary broker aliases to existing actions instead.
---

# Add a canonical action

All paths below are repository-relative. First inspect `src/actions.py` and
`src/normalize.py`. An unmatched raw action passes through title-cased and
triggers `action_not_in_catalog`; that usually needs a normalization rule,
not a new canonical action. Use [fin-add-broker](../fin-add-broker/SKILL.md)
for broker format changes and the
[ledger invariants](../../../docs/INVARIANTS.md#invariants-the-code-relies-on)
for financial semantics.

## Catalog and normalization

Add an `Action(...)` row to `_ACTIONS` in `src/actions.py`, with an explanation
of its financial effects. The dataclass fields are `name`, `balance`, `basis`,
`cash_flow`, `color`, `description`, and optional `income`.

| Field | Decision |
| --- | --- |
| `balance` | `add`, `subtract`, or `neutral`: direction of share movement. |
| `basis` | Reuse an existing `BasisEffect` handled by both walkers; inspect the literal and dispatch branches for the complete vocabulary. |
| `cash_flow` | `in`/`out` for external money; `neutral`/`ignore` for internal activity. Neither of the latter contributes to net external flow. |
| `income` | `dividends`, `interest`, `rewards`, or `lending` when it is income; otherwise `None`. |

Income membership is **derived from the catalog**, including
`basis._INCOME_ACTIONS`, `analytics._shared.INCOME_ACTION_KINDS`, and
`analytics.income_calendar._INCOME_ACTIONS`. Set the catalog's `income` field;
never recreate separate hardcoded lists. The exported catalog also drives
JavaScript membership and action colors.

Add a first-match normalization rule in `src/normalize.py`. Scope broker-specific
vocabulary by `account_group`, and verify that the configured group matches
that scope. Matchers include raw `action`, `symbol`, `amount_sign`, and
`description`. Resolve directional ambiguity in the parser **before** taking
absolute values; normalization cannot recover a sign that has been discarded.

## Financial boundaries

- `basis.basis_effect_for` ignores USD or empty symbols regardless of the
  catalog's basis effect. Cash does not create security lots.
- For non-USD positions, the lot queue's quantity must agree with the running
  balance. A new basis effect requires the
  [lot-walker sync workflow](../fin-lot-walker-sync/SKILL.md), including history
  and annotation reconstruction; adding a catalog row cannot implement a new
  branch in those consumers.
- `zero_basis` is a historical effect name: reward receipts can use FMV basis
  when available. Check the shared helper instead of assuming every such lot
  has zero cost.
- Return of capital uses `roc`: it reduces basis, realizes excess at each lot's
  holding period, and is neither income nor contributed capital. Reversals are
  paired by the shared ROC helpers. Wrap effects carry basis without realizing
  a sale. Reuse those mechanisms instead of disguising a different event as a
  Buy or Sell.
- Cash-flow exceptions belong in `basis.txn_external_cash_flow(t)`, which
  combines catalog classification with broker/context rules. JavaScript reads
  the exported per-transaction `cash_flow` verdict; it must not reproduce those
  rules from the catalog alone.
- Inspect `cash_bridge.py` for broker-cash effects and
  `cost_basis_overrides.py` for lot-creation matching when the new action can
  reach either. Catalog membership does not update broker-specific dispatch.

## Verify the event, not just its name

Use independently fictional transactions. In `tests/test_actions_catalog.py`,
cover normalization, effect classification, and income membership. Add a
behavioral case for the event's resulting balances, basis, realized gain,
external cash flow, and income where applicable. Include reversal or paired
legs when the event has them; internal consistency alone cannot show whether
the chosen classification is economically correct.

```bash
uv run python -m pytest tests/test_actions_catalog.py tests/test_lot_walker_parity.py tests/test_pipeline_snapshot.py -q -rs
uv run python -m pytest tests/ -q -rs
```

Use [fin-dev-loop](../fin-dev-loop/SKILL.md) for isolation and demo validation.
Finish when the raw action normalizes correctly, relevant consumers agree,
and the regression demonstrates the intended financial effect.
