---
name: fin-add-analytics
description: Add or correct a derived metric, dashboard card, chart, or tab in finledger. Covers Python analytics export, JavaScript rendering and interactive-window parity, exact-date valuation, and behavioral validation. Pure styling needs only the rendering workflow.
---

# Add or change a dashboard figure

All paths below are repository-relative. Shared figures are computed once in
`src/analytics/`, embedded under `DATA.analytics`, and read by dashboard modules
in `src/dashboard/app/`. Pure presentation changes need no Python change.
For subsystem rationale, read the relevant
[engineering invariants](../../../docs/INVARIANTS.md).

## Compute and export

Extend the existing analytics module or add a pure computation module with a
documented JSON shape. Take explicit inputs already supplied by
`src/analytics/__init__.py::build_analytics` and add its result to that
orchestrator. Compute dependent sections after their inputs; data health
consults the assembled output and runs last. `export.export_json` exports the
analytics payload automatically, and both the full and refresh pipeline paths
must supply equivalent inputs.

Reuse the shared financial sources:

| Figure or rule | Source |
| --- | --- |
| Action effects and income membership | `src/actions.py`; income consumers derive from its `income` field. |
| External cash flow | `basis.txn_external_cash_flow(t)`; JS sums exported transaction `cash_flow`. |
| Flow for selected accounts | `return_flows.txn_cash_flow_for_groups`; JS `_txnCashFlowForGroups` reads `account_transfer` supplements prepared by `build_analytics`. |
| Position value at a date | `valuation.mark` and `valuation.is_dust`. |
| Historical scope value/basis | `return_flows.scope_snapshot_value` / `scope_snapshot_basis`; JS `_snapshotValueForGroups` / `_snapshotBasisForGroups` include eligible `in_transit` components. |
| Period/chain-linked returns | `analytics/_shared.py::_period_return` and `_chain_link_return`. |
| Portfolio basis/realized totals | `DATA.basis_totals`, built from annotated walker state. |
| Method comparisons | `basis_methods`, for the what-if table only. |
| Whole-portfolio total return | `analytics.header_summary`. |
| Account filters and paired benchmarks | `analytics.performance_by_filter`, including each filter's own window. |

Never emit `NaN` or `Infinity`: return `None` for an undefined result, preserve
real zeroes, and test finite JSON with `allow_nan=False`. Do not assume every
consumer will distinguish an unavailable figure from an invented zero.

## Render the exported figure

Edit the relevant `src/dashboard/app/<number>-<concern>.js` module. The bundler
concatenates these in filename order; there is no `src/dashboard/app.js` file.
`00-core.js` defines `DATA` and `ANALYTICS`; `10-holdings.js` owns registration
and routing. Read your key in the existing tab renderer and provide an honest
empty state. Shared globals must be initialized before top-level use.

For a new tab, add a matching nav button (`data-tab="mytab"`), panel
(`id="tab-mytab"`), and `registerTabRenderer('mytab', renderMyTab)`. Copy the
current template's button/panel accessibility attributes and check keyboard
focus. Tabs render lazily; Overview initializes on load and redraws on
activation. Renderer exceptions are caught and logged, so inspect console
errors as well as visible output. `window.history` is the browser API;
`history` inside this bundle holds portfolio data.

Treat imported labels, account names, descriptions, and symbols as data in
both JSON-in-HTML and DOM rendering. Use the existing escaping helpers for the
actual output context. Keep keyboard controls as real buttons and preserve
focus during rerenders.

## Interactive calculations and dates

The compute-once rule has an intentional boundary: user-selected date windows
and scenario inputs can require JavaScript calculations that Python could not
precompute. Reuse the established JS helpers; when their financial semantics
change, update the Python counterpart and parity tests together. In particular,
`90-performance.js::_twrWalk` mirrors the Python chain-link and period-return
rules. A full-range custom window must agree with lifetime, and each displayed
return must use a benchmark measured over the same dates and accounts.

For account-flow changes, cover TWR, XIRR, annual returns, monthly ratios,
contribution/P&L cards, and filtered benchmark purchases together. Test one
endpoint, both endpoints, and neither endpoint of a paired in-kind transfer.
Keep portfolio contributions and retirement tax contributions distinct from
flows crossing a selected account boundary. See `tests/test_account_transfer_flows.py`
and the [account-transfer contract](../../../docs/INVARIANTS.md#account-transfer-return-flows).

For "value on date D", use `_shared._value_at_date` with the appropriate
transactions, group filter, rollover bridges, and cash series. It walks to D
and shares the valuation kernel with history: USD handling, split restatement,
option intrinsic floor, and reconstructed broker cash. `restate_qty=False`
applies only to quantities already expressed on today's share basis.

Never use the nearest snapshot by absolute date distance for an as-of answer:
it can include future transactions. If a historical-snapshot approximation is
part of the feature contract, use the latest snapshot at or before D and expose
its actual date. Reconciliation must walk to the exact statement date. Test
that later prices/activity cannot move a D-keyed posted result. Matched arrivals
can confirm reconstructed transit ownership; follow the explicit
[transit contract](../../../docs/INVARIANTS.md#assets-in-transit) rather than
attributing in-flight assets to either custodian. Combine precision components
before rounding. Monthly ratios must be invariant to inserting a midmonth snapshot.

Tax tables, filing statuses, and Section 1256 reference data come from
`src/analytics/tax.py::tax_tables_to_json` under `DATA.tax_tables`. Top bracket
caps serialize as `null`, which JS decodes as an open bound. New years belong
in Python; use [fin-tax-year-update](../fin-tax-year-update/SKILL.md).

## Verify

Add behavioral tests with independently fictional inputs: ordinary math,
empty/zero-denominator cases, date boundaries, and affected cross-view parity.
For shared figures, test their actual rendered relationship where appropriate
(`tests/test_dashboard_consistency.py` and `tools/dashboard_probe.js`). Test the
new behavior rather than only the spelling of an exported field.

```bash
uv run python -m pytest tests/test_pipeline_snapshot.py tests/test_dashboard_bundling.py tests/test_dashboard_consistency.py -q -rs
uv run python -m pytest tests/ -q -rs
uv run python -m tools.build_demo --output _site
npm test
```

Include focused tests for the changed module. Exercise the affected filter,
date, empty state, or input in `_site/index.html`. See
[fin-dev-loop](../fin-dev-loop/SKILL.md) for isolated iteration and
[PRIVACY.md](../../../docs/PRIVACY.md) before publication or screenshots.
