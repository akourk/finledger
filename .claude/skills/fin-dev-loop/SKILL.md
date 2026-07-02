---
name: fin-dev-loop
description: How to develop and test the `fin` portfolio tracker safely. Use when running the pipeline, iterating on parsers/analytics/dashboard, previewing the dashboard, or any change where you'd otherwise be tempted to run `python -m src.main` against real data. Covers the sample-data workflow, the fast feedback loops, and the privacy rules.
---

# Developing `fin` safely

`fin` ingests **real, private financial data**. `data/` holds the user's actual
broker CSV exports and `exports/` holds their actual generated dashboard. Before
you run anything, internalize the rules below — the default `python -m src.main`
regenerates real-money artifacts.

## Privacy rules (non-negotiable)

- **Never read, print, paste, or summarize the contents of `data/` or `exports/`**
  into your responses, commits, issue bodies, test fixtures, or any external
  service. They are gitignored for a reason. Filenames are fine to mention;
  row contents are not.
- **Develop against the sample portfolio, not `data/`.** The synthetic, fully
  fictional "Sam Sample" snapshot at `samples/portfolio.snapshot.json` exercises
  every parser and is safe to inspect, print, and reason about freely.
- If you genuinely must reproduce a bug that only shows up on real data, work
  from a **minimal synthetic fixture** that mimics the broker's CSV shape (see
  `tests/conftest.py` writers and `tests/fixtures/`) — do not copy real rows.

## The fast feedback loops (use the smallest one that proves your change)

Pick the tightest loop that exercises what you changed. Most changes are proven
by tests alone; only re-run the full pipeline when you need a rendered dashboard.

### 1. Tests — first resort for almost everything

```bash
python -m pytest tests/ -q                       # full suite (~220 tests, fast)
python -m pytest tests/test_basis_walker.py -q   # one module
python -m pytest tests/test_pipeline_snapshot.py -q   # end-to-end safety net
```

- Tests run **fully offline** (yfinance is stubbed in `conftest.py`) and in an
  **isolated tmp dir** via `FIN_*_DIR` env vars — they never touch real `data/`,
  `cache/`, or `exports/`. Run them freely.
- Tests set `FIN_ASSERT_INVARIANTS=1`, which promotes data-health checks
  (`analytics/data_health.py`) to hard errors. If a refactor breaks a rollup or
  the lot-queue parity, a test will fail at the point of introduction.
- `test_pipeline_snapshot.py` runs a synthetic multi-broker portfolio through
  the *entire* `main()` and asserts known-good figures. **This is the safety net
  for any structural change** — if it stays green, the pipeline still composes.

### 2. Full pipeline against sample data — when you need a real dashboard

Do this in a **scratch directory**, never in the repo working tree, so you don't
overwrite the user's real `data/`, `cache/`, or `exports/`:

```bash
# Point fin at a throwaway dir, seeded with the fictional snapshot.
SCRATCH="$TEMP/fin-scratch"   # or any tmp path
mkdir -p "$SCRATCH/data" "$SCRATCH/cache" "$SCRATCH/exports"
FIN_PROJECT_ROOT="$SCRATCH" FIN_DATA_DIR="$SCRATCH/data" \
FIN_CACHE_DIR="$SCRATCH/cache" FIN_EXPORT_DIR="$SCRATCH/exports" \
  python -m src.main --import-snapshot samples/portfolio.snapshot.json

FIN_PROJECT_ROOT="$SCRATCH" FIN_DATA_DIR="$SCRATCH/data" \
FIN_CACHE_DIR="$SCRATCH/cache" FIN_EXPORT_DIR="$SCRATCH/exports" \
  python -m src.main
# → $SCRATCH/exports/dashboard.html  (open in a browser to eyeball it)
```

The `FIN_PROJECT_ROOT` / `FIN_DATA_DIR` / `FIN_CACHE_DIR` / `FIN_EXPORT_DIR` env
vars (resolved in `src/config.py`) are the supported way to redirect the whole
pipeline — the same mechanism the test suite uses.

> Running `python -m src.main` with **no** env overrides operates on the user's
> real data. Only do that if the user explicitly asks for a fresh real-data run.

### 3. Dashboard-only changes — re-bundle, don't re-pipeline

The dashboard is `src/dashboard/{template.html,styles.css,app/*.js}` spliced
into one HTML file by `src/dashboard/__init__.py` (`generate_dashboard`). The
JS lives in `app/` as one module per tab/concern (`00-core.js`,
`10-holdings.js`, …, `95-transactions.js`), concatenated **in filename order**
at bundle time — a top-level definition must precede its use, so edit the
relevant module (or slot a new numbered file between existing ones).

To re-bundle without re-running the pipeline, reuse an existing
`exports/transactions.json` (from a scratch sample run):

```bash
# generate_dashboard takes the JSON *path* (not a loaded dict).
python -c "import pathlib; from src.dashboard import generate_dashboard; \
generate_dashboard(pathlib.Path('PATH/transactions.json'), \
pathlib.Path('PATH/dashboard.html'))"
```

`tests/test_dashboard_bundling.py` covers the splice mechanics. After editing
the `app/*.js` modules, sanity-check the bundle actually renders (the file is
multi-MB) rather than assuming a syntax-clean diff worked.

## Where things live (so you edit the right file)

- **Pipeline spec**: read `src/main.py` top to bottom — CLAUDE.md narrates all 16
  stages and the *why* behind each invariant. Read it before changing pipeline
  ordering or balance/sign logic.
- **Compute-once rule**: any derived figure the dashboard shows is computed in
  `src/analytics/` and embedded in the JSON; the JS consumes it. Do **not** add a
  recomputation in the dashboard JS — that pattern has repeatedly produced figures that
  subtly disagree with the Python. Add it to the right `analytics/` module and
  read it from the JSON in both places.
- **Action vocabulary**: `src/actions.py` is the single source of truth. Adding an
  action there threads its `(balance, basis, cash_flow, color)` classification
  through `main.py`, `basis.py`, `history.py`, `analytics`, and `dashboard` —
  don't hand-maintain those sets.
- **Tax reference data is single-sourced in Python.** The bracket / LTCG /
  standard-deduction / Roth-MAGI / §1256 tables live in `analytics/tax.py`,
  are emitted into the export via `tax_tables_to_json()` (under
  `DATA.tax_tables`), and the JS reads them from there. The literals still in
  `app/80-tax.js` are emergency fallbacks only — **add a new tax year in
  `analytics/tax.py`, not in the JS.** (One genuine Python+JS pair remains: the
  `K401_LIMIT_BY_YEAR` and 401k logic, but that too now flows through
  `tax_tables`.)

## Browser smoke test — for template / tab-structure / renderer changes

The bundling test proves the splice; it does NOT prove the page runs.
Anything that moves template containers, renames element ids, or changes
tab renderers deserves a live check (a missing `getElementById` target
fails silently or halts the script).

1. **Syntax gate first** (fast, catches concat-order and paren errors):

   ```bash
   python -c "from pathlib import Path; from src.dashboard import _read_app_js; \
   Path(r'<TMP>/fin-appjs-check.js').write_text(_read_app_js(), encoding='utf-8')"
   node --check "<TMP>/fin-appjs-check.js"
   ```

2. **Serve the SCRATCH exports dir** (never the real `exports/`) and open
   `dashboard.html` in the preview browser:

   ```bash
   python -m http.server 8791 --directory "$SCRATCH/exports"
   ```

3. In the browser console (or preview-eval), check for errors, then
   drive every tab in one shot — `activateTab` is global:

   ```js
   ['performance','planning','holdings','income','tax','retirement',
    'options','crypto','transactions','overview'].forEach(t => activateTab(t));
   ```

   Zero console errors after that sweep is the bar.  Then assert the
   specific thing you changed exists (query the section header text or
   element id) rather than eyeballing a screenshot — screenshots are for
   layout only.

Gotchas learned the hard way:
- The tab router + `ACTION_COLORS` + `activateTab` live in
  `app/10-holdings.js`, not `00-core.js`; `__JSON_DATA__` and
  `ANALYTICS` live in `00-core.js`.  Concatenation is filename-order —
  a new top-level use must come after its definition file.
- String-built SVG charts that measure their container at render time
  (e.g. the drawdown chart's `queueMicrotask` +
  `getBoundingClientRect`) fall back to a fixed width when rendered
  inside a `display:none` wrapper — fine, they scale via viewBox.
- The Tax tab's default year filter is the current year; sample data
  may have no transactions in it, so year-dependent sections
  legitimately render empty until you `setTaxYearFilter('<year>')`.

## Before you call a change done

1. `python -m pytest tests/ -q` is green (especially `test_pipeline_snapshot.py`).
2. If you touched the dashboard, you re-bundled and confirmed it renders.
3. If you touched a parser, you ran `tools/build_sample_snapshot.py` and a sample
   pipeline run to confirm the shipped snapshot still parses (see the
   `fin-add-broker` skill).
4. You did not leave real data in scratch dirs you printed, and you reported test
   results faithfully (paste failures; don't claim green you didn't see).
