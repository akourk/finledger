---
name: fin-dev-loop
description: Develop and validate finledger with isolated fictional inputs. Use for pipeline experiments, dashboard previews, and choosing focused regression checks; use the specialized broker, action, lot, analytics, or tax skill for those changes.
---

# Development loop

All source paths below are repository-relative. Read
[CONTRIBUTING.md](../../../CONTRIBUTING.md) for environment setup, Windows
validation, and the task-to-test map; [PRIVACY.md](../../../docs/PRIVACY.md)
controls publication. `data/`, `exports/`, local financial caches, and audit
notes are private. Create fixtures independently from fictional inputs; do not
send personal rows, filenames carrying identifiers, logs, or screenshots to
external services.

## Choose the smallest useful loop

- For a Python change, run its focused regression modules, then the complete
  suite for application changes. Reuse `isolated_workdir` and `stub_prices` from
  `tests/conftest.py`. A new test must explicitly use the fixtures it needs;
  session path isolation alone does not stub every possible network call.
- For rendered output, use the dedicated fictional builder:

  ```bash
  uv run python -m tools.build_demo --output _site
  npm test
  ```

  This fixes the date, seeds fictional prices, disables network access, and
  isolates all pipeline paths. Open `_site/index.html` to inspect the affected
  interaction. The browser smoke test drives the actual bundle; the separate
  accessibility check tests the final artifact.
- For documentation or skills only, check local links, referenced functions,
  commands, and changed instructions against source. A pipeline run does not
  establish that guidance is accurate.

```bash
uv run python -m pytest tests/test_pipeline_snapshot.py -q -rs
uv run python -m pytest tests/ -q -rs
```

Report failures and required skips accurately. Node is needed for financial
Python/JS parity checks. `FIN_ASSERT_INVARIANTS=1` turns severe data-health
findings into errors in isolated pipeline tests; do not remove an invariant or
exempt the failing account class merely to obtain a passing run.

## Custom pipeline experiments

Prefer an existing regression fixture or `tools.build_demo` over manually
assembling a second demo workflow. When a custom experiment is necessary, use
a new temporary directory and bind **all four** variables before importing
`src.config`: `FIN_PROJECT_ROOT`, `FIN_DATA_DIR`, `FIN_CACHE_DIR`, and
`FIN_EXPORT_DIR`. Supply fictional metadata with explicit account types,
controlled prices, a fixed date, and blocked network access. Clean up the
experiment's own temporary files afterward.

A default `python -m src.main` operates on personal paths. The user's request
to improve code does not authorize running it against personal inputs. A
sample snapshot alone is also insufficient for a deterministic public build:
its prices, effective date, network policy, and output provenance matter.

## Dashboard iteration

Edit `src/dashboard/{template.html,styles.css,app/*.js}`. The bundler in
`src/dashboard/__init__.py` concatenates JavaScript in **filename order**;
top-level initialization must not run before its dependency is initialized.
`00-core.js` owns `DATA` and `ANALYTICS`; tab registration and routing live in
`10-holdings.js`. Use `window.history` for the browser History API because the
bundle's `history` variable contains portfolio snapshots.

For a tight local presentation loop, `generate_dashboard(json_path, html_path)`
can rebundle JSON from an isolated fictional scratch run. Its arguments are
paths, not loaded dictionaries. Use the dedicated demo builder again for final
browser checks, publication, or screenshots; rebundling alone does not produce
validated public provenance.

A syntax-only check of one module cannot verify concatenation or execution.
`tests/test_dashboard_bundling.py` checks assembly; `npm test` checks the built
page. Exercise the changed state explicitly (filter, historical date, empty
input, keyboard focus) because lazy tabs catch and log renderer exceptions.
Tax-year filtering may legitimately show no rows for a year absent from the
fictional data. On `file://`, storage access can throw; preserve the guards.

## Shared rules and completion

Use [INVARIANTS.md](../../../docs/INVARIANTS.md) for the affected subsystem.
Action/income classification belongs to `src/actions.py`; external cash flow
to `basis.txn_external_cash_flow`; valuation to `valuation.mark`. Shared
figures are exported from Python. User-selected date windows retain a JS
return engine that must agree with Python on equivalent inputs.

Parser changes must validate the shipped snapshot via
`tests/test_sample_snapshot.py` and the isolated demo. Regenerate
`samples/portfolio.snapshot.json` with `tools.build_sample_snapshot` only when
fictional generator changes require it or a stale-sample failure is understood.
Record a behavioral regression for a new failure boundary, complete the
relevant validation, and follow the privacy process before any commit or push.
