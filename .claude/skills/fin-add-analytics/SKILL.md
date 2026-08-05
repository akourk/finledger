---
name: fin-add-analytics
description: Add or change a derived figure / dashboard view in the `fin` portfolio tracker — anything that computes a number in src/analytics/ and surfaces it in src/dashboard/app.js. Use when adding a stat card, chart, table, or whole tab; when a dashboard value is wrong or should be sliced differently; or when you're tempted to compute something in JavaScript. Covers the compute-once contract, the build_analytics → JSON → registerTabRenderer wiring, and the Python↔JS sync footguns.
---

# Adding / changing a dashboard figure in `fin`

**The contract: compute once in Python, consume in JS.** Every derived figure
the dashboard shows is computed in `src/analytics/`, embedded in
`exports/transactions.json` under the `analytics` key, and *read* by `app.js`.
Do **not** recompute it in JavaScript — that pattern has repeatedly produced
figures that subtly disagree with `main.py` / `basis.py` / `history.py`, and the
data-health checks can't see a JS-only recomputation. If the dashboard needs a
number that more than one view consumes, it belongs in `analytics/`.

First decide which case you're in:
- **New figure on an existing tab** (the common case) — steps 1, 2, 4.
- **Whole new tab** (rare) — add step 5 (a `template.html` panel + button).
- **Pure presentation tweak** (relabel, recolor, reorder an *already-exported*
  value) — skip to step 4; no Python change.

## 1. Compute it — `src/analytics/<area>.py`

Either extend an existing per-section module or add a new one. A module is a pure
function `txns`/`history`/`holdings` in, plain-JSON-serializable `dict` out:

```python
# src/analytics/myfigure.py
"""One-line what + why.  Document the returned shape — the JS reads it."""
from __future__ import annotations

def compute_myfigure(txns: list[dict], history: list[dict]) -> dict:
    return {
        "series": [...],          # [{date, value}, ...]
        "summary": {...},
    }
```

Conventions to match (see `drawdown.py` / `concentration.py` for clean
examples): module-level docstring describing the returned keys, helpers prefixed
`_`, the same input objects the orchestrator already has (`txns`, `history`,
`holdings`, `holdings_by_account`, `retirement_meta`, `cash_summary`,
`basis_methods`, `fifo_state`).

**Cash-flow / income / basis classification must route through the shared
sources of truth**, not bespoke per-module sets:
- external cash in/out → `basis.txn_external_cash_flow(t)`
- balance / basis / cash-flow action membership → the catalog sets in
  `src/actions.py` (`SUBTRACT_ACTIONS`, `BASIS_EFFECTS`, `CASH_ADD_ACTIONS`, …)
- income actions → `analytics._shared.INCOME_ACTION_KINDS` (see `fin-add-action`
  for the three-places income gotcha)
- TWR / period-return math → the helpers in `analytics/_shared.py`
  (`_period_return`, `_chain_link_return`, `_value_at_date`)

**Never emit `NaN` / `Infinity`.** Python's `json.dump` writes a bare `NaN`
literal, which is valid JavaScript when embedded — so it silently reaches the
dashboard as `NaN` (e.g. the Total Return bug). Guard every division by a
possibly-zero denominator and return `None` (→ JS `null`, which renders as `—`)
instead of a non-finite float. See CLAUDE.md "Invariants the price cache relies
on" for the same class of bug on the price side.

## 2. Wire it into the orchestrator — `src/analytics/__init__.py`

Import your function and add one entry to the `out` dict in `build_analytics`
(the key is what the JS reads as `ANALYTICS.<key>`):

```python
from .myfigure import compute_myfigure
...
out = {
    ...
    "myfigure": compute_myfigure(txns, history),
}
```

If your figure depends on other analytics (like `alerts` / `data_health` do),
compute it after its inputs and pass them in — `data_health` runs last because
it consults the assembled `out` dict.

## 3. Embedding — automatic, no change

`export.export_json` already writes the whole `build_analytics` result under
`DATA.analytics` (plus `action_catalog`, `sector_of`, `display_of`). You don't
touch `export.py` for a new figure. Both `main()` and `_refresh_prices_only()`
call `build_analytics`, so the refresh path stays in sync for free.

## 4. Consume it — `src/dashboard/app.js`

`app.js` exposes the export as globals near the top: `DATA`, `txns`,
`holdingsByAccount`, `history`, `cashSummary`, and
`const ANALYTICS = DATA.analytics`. Read your key and render it inside the
relevant tab's renderer:

```js
function renderMyTab() {
  const mf = ANALYTICS.myfigure || {};
  // build HTML / draw chart from mf; fall back gracefully if empty
}
registerTabRenderer('mytab', renderMyTab);
```

- **Tabs render lazily** on first activation (`activateTab` → `registerTabRenderer`
  callback, run once). Overview is the exception — it renders on load and
  re-runs its chart on every activation.
- Renderer exceptions are **caught and logged**, not thrown — a broken tab
  leaves the rest of the page working, so check the browser console (not a blank
  page) when a tab is empty.
- Don't reference the bare identifier `history` expecting the browser History
  API — `app.js` shadows it with `const history = DATA.history`. Use
  `window.history` for the routing API.

## 5. New tab only — `src/dashboard/template.html`

Adding a brand-new tab (not just a figure on an existing one) also needs, in
`template.html`: a nav button `<button class="tab-btn" data-tab="mytab">My
Tab</button>` and a panel `<div id="tab-mytab" class="tab-panel"></div>`. The
`data-tab` / `id="tab-<name>"` / `registerTabRenderer('<name>')` strings must all
match. Hash routing (`#mytab`) and lazy render then work automatically.

## Tax reference data: single-sourced in Python

The federal tax tables (brackets / LTCG / standard deduction / Roth-MAGI / §1256
underlyings) used to be duplicated in Python and JS. They're now **emitted from
Python** via `analytics/tax.py::tax_tables_to_json()` into `DATA.tax_tables`, and
`app.js` reads them from there (the `FEDERAL_BRACKETS` etc. literals in `app.js`
are emergency fallbacks only). **Add a new tax year or filing status in
`analytics/tax.py` only** — the JS picks it up automatically.

One emit detail to copy if you add similar reference data: the open-ended top
bracket's threshold is emitted as `null` (→ `Infinity` in JS via
`_loadBracketTable`), never a non-finite float — the same no-`NaN`/`Infinity`
rule from step 1.

## Date-keyed figures: compute AT the date, never snap to a snapshot

If your figure answers "what was X on date D" — a statement check, an as-of
holdings view, a window boundary — **walk to D**. Use
`analytics/_shared.py::_value_at_date(txns_sorted, target, filter_groups,
bridges, cash_series)`; it applies history's valuation rules (USD-skipping,
split adjustment, option-intrinsic floor, reconstructed broker cash) and
agrees with `history`'s `by_account_group` within float noise.

**Do not pick the nearest snapshot.** History is sampled semimonthly (15th /
EOM / today), so a snapshot on an arbitrary D usually does not exist — and
nearest-by-*absolute*-distance can resolve **forward in time**, letting
activity that happened after D leak into a D-keyed answer. That shipped:
`reconcile`'s balance check matched a statement dated the 4th to today's
snapshot on the 5th, so a deposit made the day *after* the statement printed
as a reconciliation break of exactly its own size. Treat `abs(...)` over two
dates as a bug on sight.

If a full walk is genuinely too expensive (it is O(txns) per date), take the
latest snapshot **at or before** D — never the nearest — and surface the gap
in the output so the consumer knows it got a different date.

Test it with one assertion: add a txn dated *after* your figure's date and
assert the figure does not move. That is what catches this whole class, and a
fixture that hands the code a pre-built `history` will not.

## Test & verify

- **Unit-test the module** like the others (`tests/test_*`): feed synthetic
  `txns`/`history`, assert the returned shape and a couple of known values.
  Include a divide-by-zero / empty-input case so it can't regress to `NaN`.
- `python -m pytest tests/ -q` — green, especially `test_pipeline_snapshot.py`
  (the e2e run asserts the whole analytics payload composes) and
  `test_dashboard_bundling.py`.
- **See it render.** Re-bundle against an existing sample
  `exports/transactions.json` rather than re-running the pipeline, then open the
  HTML — the dashboard is multi-MB, so confirm it actually renders rather than
  trusting a clean diff. Exact re-bundle command + the scratch-dir sample run are
  in the **fin-dev-loop** skill. (`node --check src/dashboard/app.js` catches JS
  syntax errors fast before bundling.)

## Done when

- The figure is computed in `analytics/`, keyed into `build_analytics`'s `out`,
  and read from `ANALYTICS.<key>` in `app.js` — no recompute in JS.
- No `NaN`/`Infinity` can reach the JSON (zero-denominator guarded).
- Any date-keyed figure is computed AT that date (or documented as snapping
  backward), with a test that it doesn't move when activity is added after it.
- If you touched tax brackets or §1256, both Python and JS copies agree.
- `python -m pytest tests/ -q` is green and the tab renders in a re-bundled
  dashboard.
