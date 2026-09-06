# Reproducing the public demo

The [Pages demo](https://akourk.github.io/finledger/) is a fixed fictional
portfolio as of **June 30, 2026**. Every personal detail, transaction, balance,
and market price is invented. Public ticker names help explain the interface;
the price curves are illustrative, not observed history or investment results.

```bash
uv sync --frozen --all-groups
uv run --frozen python -m tools.build_demo
uv run --frozen python -m tools.privacy_guard scan --artifact _site
npm ci
npm test
```

Open `_site/index.html`. The page needs no server, account, API key, external
script, or live market connection. `npm test` runs ordinary keyboard/browser
interactions and axe accessibility scans against the final bannered page.

## Inputs and isolation

`tools/build_sample_snapshot.py` generates eleven broker and metadata CSVs
from literal fictional fixtures. Their portable bundle must match the
checked-in `samples/portfolio.snapshot.json` byte for byte. Regenerate it with
`uv run --frozen python -m tools.build_sample_snapshot` after editing fixtures.

`tools/build_demo.py` starts a fresh subprocess with a new empty temporary
root and overrides all `FIN_*` portfolio directories before importing any
application configuration. Ambient data, cache, export, and snapshot-date
settings cannot become inputs. The worker refuses a nonempty working directory.
The builder does not read the repository's mutable `cache/` or personal data.

`FIN_AS_OF_DATE=2026-06-30` pins the Python clock. The dashboard uses the exported
snapshot date for tax years, age calculations, and trailing activity windows,
so visiting next year will not silently reinterpret the same figures.

`samples/prices.fixture.json` describes fixed endpoints, sectors, and a
synthetic split. A deterministic formula produces daily illustrative prices
between those endpoints. Empty dividend fixtures mean the invented benchmark
paths already describe their illustrative return series. They are not Yahoo
prices and should never be described as historical market performance.

Python socket audit checks and a deny-only replacement for yfinance prohibit
network access, including native curl-backed yfinance requests. Any attempt
fails the build, even if a downstream fetch handler catches the exception.
Incomplete fixtures therefore cannot quietly fall back to live prices.

## Reconciliation and validation

The sample contains five matching statement checks and one explicitly
explained $142.60 difference caused by the fictional statement's earlier
close. The expected difference is visible and independently fixed in metadata.
It demonstrates the exception mechanism; all other differences must resolve.
Statement targets are never recalculated from the pipeline during a build.
Updating them is a fixture change that deserves the same review as a test
expectation. Never widen tolerance just to make a regression pass.

The sample also includes a fully funded Coinbase Pro transfer and an explicit
withdrawal of fictional USD proceeds. Keeping positive cash in an actual
broker wallet is valid; diagnostics check negative reconstructed balances
instead of assuming every wallet must end at zero.

The builder fails on unresolved data-health warnings and unexplained
reconciliation differences. CI additionally checks historical realized gains,
Python/JavaScript monthly risk parity, character-by-character Planning and Tax
input, keyboard lot disclosures, CSV download, deep links, every tab on a
375-pixel viewport, browser console errors, and accessibility. A second clean
build must reproduce both HTML and manifest exactly.

## Provenance and deployment

Only `index.html` and `provenance.json` are staged. The manifest records the
snapshot date, synthetic designation, generator path and SHA-256 digest,
snapshot digest, price-fixture digest, builder digest, and HTML digest. The same
provenance appears in the embedded dashboard data. The privacy guard verifies
these relationships and scans artifact contents. It also independently rebuilds
from a temporary copy of the current public source and requires identical
output bytes; a label and self-consistent manifest alone are insufficient.
Review of the literal inputs establishes where the fictional data came from.

The Pages workflow calls the reusable tests workflow and downloads its
`verified-demo` artifact. Deployment depends on the completed Python 3.10/3.12
matrix and final-artifact privacy, browser, accessibility, and reproducibility
checks. It publishes that checked artifact, without rebuilding from different
inputs or pulling live prices at deployment time.

Python packages are resolved in `uv.lock`; browser dependencies in
`package-lock.json`. Use `uv sync --frozen --all-groups` and `npm ci` for the
reviewed graph. Dependency updates should regenerate their lockfiles and pass
the same checks.

## Portfolio screenshots

Refresh the README images only from the verified demo:

```bash
node tools/capture_demo_screenshots.js _site/index.html
uv run --frozen python -m tools.privacy_guard scan --scope worktree
```

The capture tool checks the input HTML and current generator hashes and
requires its fictional designation before opening a browser. It writes four
1440 × 1100 screenshots plus `docs/img/provenance.json`, linking
image hashes to the exact dashboard and sample inputs. Public sharing metadata
uses the fixed repository URL for the reviewed Overview screenshot. Review all four rendered images, then set `visually_reviewed` to `true` in
the image manifest before committing. The capture tool resets it to `false`
on every regeneration. Each image visibly labels the fictional sample.
