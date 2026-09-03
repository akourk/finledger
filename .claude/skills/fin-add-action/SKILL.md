---
name: fin-add-action
description: Add a new canonical action to the `fin` portfolio tracker's action vocabulary. Use when a broker emits a raw action string that doesn't map to any existing canonical action (it shows up un-normalized / title-cased in the dashboard, or trips the "action_not_in_catalog" data-health flag), and none of the existing canonical actions fit. Covers the catalog entry, the three-dimension classification, the normalize rule, the income-set gotcha, and the test.
---

# Adding a new canonical action to `fin`

The action vocabulary is centralized in **`src/actions.py`** (single source of
truth). Every canonical action carries a `(balance, basis, cash_flow, color)`
classification; downstream modules (`main.py`, `basis.py`, `history.py`,
`analytics/`, `dashboard/`) import the pre-baked sets, so **adding the action in
one place threads it through every consumer**.

You usually need this when a broker's raw action string has **no existing
canonical action that fits**. If an existing one fits (most do — `Buy`, `Sell`,
`Dividend`, `Deposit`, `Withdrawal`, `Transfer In/Out`, `Reward`, `Reinvest`,
`Contribution`, `Neutral`, …), skip this skill and just add a `normalize.RULES`
entry mapping the raw string to that existing action (see `fin-add-broker` step
6). Only add a *new* canonical action when the semantics are genuinely new.

## How you know you need a new action

- A raw action passes through `normalize.normalize_action` un-matched, so it
  arrives title-cased (`"Futswp"`, `"Roc"`) and lands in neither the balance,
  basis, nor cash-flow sets — silently mis-classified.
- `analytics/data_health.py::_check_action_catalog_coverage` raises the
  `action_not_in_catalog` warning ("N action name(s) used by parsers but missing
  from src/actions.py — they fall back to default classification (likely
  wrong)"). That check is your safety net; this skill clears it.

## 1. Add the catalog entry — `src/actions.py`

Add one `Action(...)` row to the `_ACTIONS` tuple, in the section that fits its
meaning (trades / cash flows / income / account-internal / corporate / crypto /
options / other). Set all four fields plus a docstring explaining *why*:

```python
Action("Your Action", balance, basis, cash_flow, "#hexcolor",
       "One-paragraph why: what broker event this is, and the reasoning "
       "behind each classification choice."),
```

### Choosing the three effects

**`balance`** — how the running share-balance walker (`main.py`) treats `quantity`:
- `"add"` → `balance += qty` (acquisitions, inbound transfers, income-as-shares)
- `"subtract"` → `balance -= qty` (disposals, outbound transfers, fees-as-shares)
- `"neutral"` → no balance change (pure bookkeeping / cash-only events)

**`basis`** — how the FIFO/LIFO/HIFO/Avg cost-basis walker (`basis.py`) treats it:
- `"add"` → push a new lot at the txn's per-share basis
- `"remove"` → consume front lot(s), realize gain
- `"zero_basis"` → push a lot at $0 basis (gifted / spinoff / reward shares)
- `"transfer_out"` / `"transfer_in"` → release / receive lots via `_pair_transfers`
- `"split"` → rescale qty and basis-per-share by the split ratio
- `"ignore"` → not a share-level event (cash flows, USD-only rows)
- `"unknown"` → flag for manual review (avoid in real actions)

**`cash_flow`** — how the TWR / Net-Contributed walkers treat it:
- `"in"` → external money INTO the account (deposits, contributions)
- `"out"` → external money OUT (withdrawals, real distributions)
- `"neutral"` → internal activity already reflected in value (dividends, sells,
  trade-settlement cash legs)
- `"ignore"` → not user-attributable (fees, internal inter-entity transfers)

`"neutral"` and `"ignore"` behave the same for `net_contributed` (neither counts
as in/out). Pick `"neutral"` for investment-attributable cash and `"ignore"` for
true internal plumbing — it documents intent.

### Two classification gotchas

- **Symbol-aware basis override.** `basis._basis_effect` forces `"ignore"` for
  any txn whose symbol is `USD` or empty, *regardless* of the catalog `basis`
  value. So a cash action (symbol→USD) can safely declare e.g. `zero_basis`; the
  walker won't create a phantom USD lot. (This is why `Reward` works for a USD
  cash reward.)
- **Balance parity.** For every non-USD symbol, the basis walker's final
  lot-queue quantity must equal `main.py`'s running balance. If your action is
  on a real ticker, `balance` and `basis` must agree on direction (both
  add-ish, both subtract-ish, or both neutral/ignore) or the parity invariant
  breaks and `FIN_ASSERT_INVARIANTS=1` tests fail.

## 2. Add the normalize rule — `src/normalize.py`

Map the broker's **raw** action string to your new canonical name. Scope by
`account_group` so you don't collide with another broker's strings; first match
wins. Matchers (all optional): `account_group`, `action` (case-insensitive),
`symbol`, `amount_sign` (`"+"`/`"-"`/`"0"`), `description` (substring).

```python
{"account_group": "Robinhood", "action": "ROC", "normalized": "Return of Capital"},
```

If the raw action is **directional** (one string means both inflow and outflow),
do **not** handle it here — split it by sign in the *parser* before `abs()`
(see the `fin-add-broker` invariants), emitting two distinct raw strings that
each map to a separate canonical action.

## 3. Income? Update the THREE hardcoded income sets

If — and only if — your action represents income (counts toward the Income tab /
dividend-interest-reward-lending totals), it must be added to **all three**
hardcoded sets (these are NOT derived from the catalog):

- `src/basis.py` → `_INCOME_ACTIONS`
- `src/analytics/_shared.py` → `INCOME_ACTION_KINDS` (maps action → bucket name)
- `src/analytics/income_calendar.py` → `_INCOME_ACTIONS`

Miss one and income figures disagree between the cash summary, the Income tab,
and the 12-month forecast. Most new actions are **not** income (return-of-capital
and inter-entity transfers aren't) — leave them out of all three.

## 4. Cash-flow carve-outs go through one helper

Don't add bespoke cash-flow logic. `basis.txn_external_cash_flow(t)` is the
single source of truth for inflow/outflow classification (it reads
`CASH_ADD_ACTIONS` / `CASH_SUB_ACTIONS`, which derive from the catalog
`cash_flow` field, plus three documented carve-outs). If your action needs a
carve-out beyond a plain `cash_flow` value, add it there — not in each consumer.

## 5. What you DON'T touch

These are all derived from the catalog at import time — never hand-maintain them:
`main.py`'s `_SUBTRACT_ACTIONS` / `_NEUTRAL_ACTIONS`, `basis.py`'s
`BASIS_EFFECTS`, `dashboard.py`'s `ACTION_COLORS` / `_TWR_*` sets, and the
dashboard JS sets (the catalog is serialized into the export under
`action_catalog`, which `app.js` consumes — same source of truth on both sides).

## 6. Test it

- Add/extend a case in `tests/test_actions_catalog.py` pinning the new action's
  `(balance, basis, cash_flow)` and, if relevant, its (non-)membership in
  `CASH_ADD_ACTIONS` / `CASH_SUB_ACTIONS` / `INCOME_ACTION_KINDS`. Mirror
  `test_robinhood_roc_futswp_misc_classification`.
- `python -m pytest tests/ -q` — green, especially `test_pipeline_snapshot.py`
  (end-to-end) and `test_actions_catalog.py`.

## Worked examples (from this repo)

| Raw | Canonical | balance / basis / cash_flow | Why |
|-----|-----------|-----------------------------|-----|
| Robinhood `FUTSWP` | `Event Contract Transfer` | neutral / ignore / ignore | Inter-entity cash shuffle to the prediction-markets entity — the user's own money, no positions imported, so fully excluded from every metric. |
| Robinhood `ROC` | `Return of Capital` | neutral / **roc** / neutral | Non-dividend distribution; shares unchanged, not income, not a contribution — but it REDUCES cost basis (IRS Pub 550), with any excess over basis realized as gain. Was `ignore`, justified by the app not tracking taxable cash; `cash_bridge.py` later made that premise false and left the basis half unapplied (AUDIT.md F-034) — a reminder that a conditional exemption is only as durable as its condition, which lived in another file. |
| Robinhood `MISC` | `Reward` (existing) | add / zero_basis / neutral | "Cash reward" promo — reused an existing income action rather than adding one; USD symbol auto-routes basis to ignore. |

## Done when

- The raw broker string maps to a catalog action (`action_not_in_catalog` flag
  clears on the next run).
- `python -m pytest tests/ -q` is green.
- If income: all three income sets updated. If not: none of them touched.
