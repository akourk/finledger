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
python -m pytest tests/ -q                       # full suite (~129 tests, fast)
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

The dashboard is `src/dashboard/{template.html,styles.css,app.js}` spliced into
one HTML file by `src/dashboard/__init__.py` (`generate_dashboard`). `app.js`
(~7.5K lines) is the place to edit behavior — it gets real editor support
instead of living in a Python string.

To re-bundle without re-running the pipeline, reuse an existing
`exports/transactions.json` (from a scratch sample run):

```bash
python -c "import json,pathlib; from src.dashboard import generate_dashboard; \
d=json.load(open('PATH/transactions.json')); \
generate_dashboard(d, pathlib.Path('PATH/dashboard.html'))"
```

`tests/test_dashboard_bundling.py` covers the splice mechanics. After editing
`app.js`, sanity-check it actually renders (the file is multi-MB) rather than
assuming a syntax-clean diff worked.

## Where things live (so you edit the right file)

- **Pipeline spec**: read `src/main.py` top to bottom — CLAUDE.md narrates all 16
  stages and the *why* behind each invariant. Read it before changing pipeline
  ordering or balance/sign logic.
- **Compute-once rule**: any derived figure the dashboard shows is computed in
  `src/analytics/` and embedded in the JSON; the JS consumes it. Do **not** add a
  recomputation in `app.js` — that pattern has repeatedly produced figures that
  subtly disagree with the Python. Add it to the right `analytics/` module and
  read it from the JSON in both places.
- **Action vocabulary**: `src/actions.py` is the single source of truth. Adding an
  action there threads its `(balance, basis, cash_flow, color)` classification
  through `main.py`, `basis.py`, `history.py`, `analytics`, and `dashboard` —
  don't hand-maintain those sets.
- **Two hardcoded-and-must-agree pairs** (Python + JS): Section 1256 underlyings
  (`analytics/tax.py` ↔ `dashboard/app.js`) and the tax bracket / LTCG / standard
  deduction / Roth-phaseout tables. Edit both copies together.

## Before you call a change done

1. `python -m pytest tests/ -q` is green (especially `test_pipeline_snapshot.py`).
2. If you touched the dashboard, you re-bundled and confirmed it renders.
3. If you touched a parser, you ran `tools/build_sample_snapshot.py` and a sample
   pipeline run to confirm the shipped snapshot still parses (see the
   `fin-add-broker` skill).
4. You did not leave real data in scratch dirs you printed, and you reported test
   results faithfully (paste failures; don't claim green you didn't see).
