# Contributing

Use fictional inputs and read [Privacy](docs/PRIVACY.md) before any commit or
push. The privacy hooks must be enabled in each clone. Shared agent instructions
live in [AGENTS.md](AGENTS.md); [engineering invariants](docs/INVARIANTS.md)
explain financial failure boundaries and parallel consumers. They are a reference,
not a substitute for tests.

## Set up

Python 3.10+ and uv are needed for the Python environment. Node.js 22.12+ is needed
for browser checks. No Node runtime is required to open the generated dashboard.

```bash
uv sync --locked --all-groups
npm ci
sh tools/install-hooks.sh
```

Use your GitHub no-reply email for public commits; the hooks also inspect author
and committer metadata. Review the local denylist privately before adding data.

Python dependencies are declared in `pyproject.toml` and resolved in `uv.lock`.
Browser dependencies are resolved in `package-lock.json`. Update manifests and
locks together, and validate dependency upgrades before publishing them.

`npm ci` installs Puppeteer's matching browser. If using an existing local Chrome
installation, set `PUPPETEER_EXECUTABLE_PATH` to its executable for browser checks.

## Development loop

1. Reproduce a problem with a small, independently constructed fictional input.
2. Add a behavioral regression where it protects a real boundary: input parsing,
   accounting/date semantics, failure recovery, or user interaction.
3. Run the relevant tests, then the complete suite for application changes.
4. For changes affecting dashboard output, build the fictional demo, exercise
   affected views, and run browser checks. Documentation-only edits need link,
   command, and skill-reference checks; they do not need a new pipeline run.
5. Follow the privacy checklist before every commit and every push.

```bash
uv run python -m pytest tests/ -q -rs
uv run python -m tools.build_demo --output _site
npm test
uv run python -m tools.privacy_guard scan --scope worktree
uv run python -m tools.privacy_guard scan --artifact _site
```

Tests isolate the pipeline with `FIN_PROJECT_ROOT`, `FIN_DATA_DIR`,
`FIN_CACHE_DIR`, and `FIN_EXPORT_DIR`. Use all four when running a custom scratch
pipeline; paths are resolved at import time. Never run an investigative pipeline
against root `data/` or let it mutate the checked-in cache. The demo builder does
this isolation automatically and refuses network access.

The test suite uses Node for Python/JavaScript parity. Do not accept a green run
that skipped required JavaScript checks because Node was missing. The local
metadata-dependent savings test may skip in a clean checkout; report that skip.

## Find the right surface

Start with the source and regression boundary below. The skills are plain
Markdown workflows shared across agents; open the linked file directly if your
client does not discover `.claude/skills/`. Keep one maintained copy of each
workflow instead of duplicating it for each model or client.

| Change | Source and focused checks | Workflow |
| --- | --- | --- |
| Pipeline orchestration or refresh | `src/main.py`, `pipeline_stages.py`; `test_pipeline_snapshot.py`, `test_pipeline_path_parity.py`, `test_import_safety.py` | [Development loop](.claude/skills/fin-dev-loop/SKILL.md) |
| Broker format or snapshot import | `src/scanner.py`, `parsers/`, `snapshot.py`, `accounts.py`; parser-specific tests, `test_snapshot_workflow.py`, `test_parser_silent_drop.py` | [Broker parser](.claude/skills/fin-add-broker/SKILL.md) |
| New financial action | `src/actions.py`, `normalize.py`; `test_actions_catalog.py` and affected financial consumers | [Action catalog](.claude/skills/fin-add-action/SKILL.md) |
| Lots, cash principal, or cost basis | `src/basis.py`, `history.py`, `pipeline_stages.py`; `test_lot_walker_parity.py`, `test_cash_basis_parity.py`, `test_pipeline_path_parity.py` | [Lot parity](.claude/skills/fin-lot-walker-sync/SKILL.md) |
| Valuation or price cache | `src/valuation.py`, `prices.py`; `test_valuation_kernel.py`, `test_value_at_date_parity.py`, `test_prices.py`, `test_price_cache_corruption.py`, `test_prices_backoff.py` | [Price invariants](docs/INVARIANTS.md#invariants-the-price-cache-relies-on) |
| Cache persistence or interrupted-save recovery | `src/io_safe.py`, `prices.py`, `sectors.py`; `test_io_safe.py`, `test_cache_persistence.py` | [Recovery procedure](docs/USAGE.md#cache-save-recovery) and [persistence invariants](docs/INVARIANTS.md#invariants-the-price-cache-relies-on) |
| Analytics or dashboard behavior | `src/analytics/`, `src/dashboard/app/*.js`; module tests, `test_dashboard_consistency.py`, `test_dashboard_bundling.py`, then demo browser checks | [Analytics and rendering](.claude/skills/fin-add-analytics/SKILL.md) |
| Annual tax reference update | `src/analytics/tax.py`; `test_tax_tables_vs_irs.py`, `test_tax_tables.py`, `test_tax_table_js_parity.py` | [Tax tables](.claude/skills/fin-tax-year-update/SKILL.md) |
| Privacy or publication | `tools/privacy_guard.py`, `tools/build_demo.py`, `githooks/`; `test_privacy_guard.py`, `test_privacy_hooks.py`, `test_demo_build.py` | [Privacy](docs/PRIVACY.md) |

Test filenames in the table are under `tests/`. Use `uv run python -m pytest
tests/<module>.py -q -rs` for a focused run. Read the relevant
[invariants](docs/INVARIANTS.md) before interpreting a surprising financial
result as a bug; verify the claimed behavior against source and a fictional
regression. The historical audit explains prior failures, but its old commands
and observations are not current operating instructions.

For performance work, measure the suspected path with isolated fictional data
and a fixed date before changing it. Report workload and before/after timings
together with output parity; reducing a walk's runtime is not an improvement if
it loses split, cash, date, or lot semantics. Distinguish local measurements from
general speed claims. In parallel reviews, assign file ownership and share
qualitative findings or independently fictional reproducers, never private
inputs or local audit output. Review the combined diff before final validation.

Use the reproducible transfer/basis/history benchmark to compare code changes:

```bash
uv run python -m tools.benchmark_transfers --cycles 250 500 1000 --symbols 1 8
```

It constructs fictional transactions and prices in temporary directories with
network access blocked. Each cycle contributes four rows. The output reports
median pairing and combined-walk times plus a semantic digest; matching digests
check output agreement for that workload. This does not measure ingestion,
network fetching, analytics, or browser rendering. Matcher changes also require
`tests/test_transfer_pairing_index.py` and the existing lot/history parity tests.

For cache persistence changes, inject failures with fictional files at
serialization, replacement, deletion, rollback, and marker removal. Assert
byte-for-byte preservation or intact recovery evidence, retained dirty state,
and rejection of subsequent cold loads when recovery is pending. Use a child
process to test abrupt termination. The helper assumes a single writer and
does not make multiple file replacements globally atomic or power-loss durable;
do not turn those assumptions into stronger documentation claims.

### CSV parser investigations

For snapshot restore errors, first compare the restored CSV text with its
snapshot entry locally. Snapshot transport does not validate transactions.
Inspect structural facts such as header width, row width, blank-cell positions,
and physical line numbers without printing private cell contents. Reproduce
the shape with independently fictional CSVs before changing the parser.

For Robinhood ingestion changes, start with:

```bash
uv run python -m pytest tests/test_snapshot_workflow.py tests/test_robinhood_footer.py tests/test_import_safety.py tests/test_parser_silent_drop.py tests/test_robinhood_corp_actions.py tests/test_sample_snapshot.py -q -rs
```

Then run the complete suite. Include snapshot round trips, multiline quoted
fields, and malformed rows near any recognized informational footer. Keep CSV
record parsing intact so error locations retain their physical line numbers.
Snapshot tests must run the restored files through the normal CLI pipeline,
not stop at successful export/import. Assert resulting share balances and
preservation of previous outputs on failure. Use a fixed date, fictional price
coverage, and disabled network access so a missing format case cannot hide
behind environment differences or live quotes.

### Windows validation

Set `PYTHONUTF8=1` when running the suite from PowerShell so subprocesses and
tests that use the default text encoding read generated UTF-8 consistently.
Write synthetic CSV fixtures with `newline=""` to preserve intentional embedded
line endings. `.gitattributes` keeps public text in LF form so reviewed working
source and staged source have identical bytes for provenance checks.
Git hook tests need `sh` on `PATH` (provided by Git for Windows). If a managed
Python runtime resets `PATH` at startup, configure it inside the test process
before launching pytest. The filesystem symlink test reports a skip when Windows
denies that privilege; Git-index symlink checks still run.

If the demo reports a stale sample, compare both parsed JSON and serialized
bytes before regenerating anything. Sorting `Path` objects can produce a
different filename order on Windows; the generators, scanner, and parser sort
explicit filename strings. Identical JSON values do not satisfy a byte-for-byte
provenance check. Report the failed check and investigate its cause; do not
disable the check or regenerate artifacts from personal inputs.

When a full-suite failure appears unrelated, reproduce the affected test on
unchanged source in a separate temporary checkout. Keep its inputs isolated
and logs local, and report both results rather than treating the failure as a
pass. Check runtime availability before repeatedly attempting dependency setup;
use the configured project environment when it is available.

## Extending the ledger

- A new broker needs detection, a parser returning the common transaction shape,
  registration, normalization rules, and fictional format examples. See
  [Usage](docs/USAGE.md) and the existing parsers.
- Every account group needs an explicit `Taxable`, `Retirement`, or `Savings`
  classification. `--init-account-mappings` creates a reviewable starter file;
  it cannot decide ambiguous accounts for the user.
- A new action must be classified in the shared action catalog and checked in
  balances, cash flow, cost basis, and historical valuation. Check existing
  invariants before adding a special case to only one consumer.
- Monthly ratios must use monthly observations. Inserting a midmonth snapshot
  must not alter a monthly calculation. Historical views must not include future
  transactions or silently mix current metrics into historical totals.
- Treat imported strings as data in both JSON-in-HTML and DOM rendering contexts.
  Preserve focus and use real buttons for keyboard-operated controls.

## Deployment

GitHub Actions validates Python tests, privacy rules, the isolated sample build,
its final browser interactions and accessibility, and artifact provenance. Pages
publishes only the artifact from that successful validation. Review the workflow
and resulting artifact when changing any build or publication step.

The public site is a fictional demonstration. It must never accept a personal
local pipeline output as a deployment input. See [Demo](docs/DEMO.md) for the
fixed-date sample design and intentional reconciliation example.
