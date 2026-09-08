# Contributing

Use fictional inputs and read [Privacy](docs/PRIVACY.md) before any commit or
push. The privacy hooks must be enabled in each clone. `CLAUDE.md` documents
financial subsystem invariants; it is a reference, not a substitute for tests.

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
3. Run the relevant tests, then the complete suite before finishing a change.
4. Build the fictional demo, exercise affected views, and run browser checks.
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

### CSV parser investigations

For snapshot restore errors, first compare the restored CSV text with its
snapshot entry locally. Snapshot transport does not validate transactions.
Inspect structural facts such as header width, row width, blank-cell positions,
and physical line numbers without printing private cell contents. Reproduce
the shape with independently fictional CSVs before changing the parser.

For Robinhood ingestion changes, start with:

```bash
uv run python -m pytest tests/test_robinhood_footer.py tests/test_import_safety.py tests/test_parser_silent_drop.py tests/test_robinhood_corp_actions.py tests/test_sample_snapshot.py -q -rs
```

Then run the complete suite. Include snapshot round trips, multiline quoted
fields, and malformed rows near any recognized informational footer. Keep CSV
record parsing intact so error locations retain their physical line numbers.

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
