# finledger

**A local financial ledger that reconciles broker exports into one auditable portfolio.**

[![finledger dashboard built from a fictional sample portfolio](docs/img/dashboard.png)](https://akourk.github.io/finledger/)

**[Explore the live demo →](https://akourk.github.io/finledger/)** ·
[Engineering case study](docs/ENGINEERING_CASE_STUDY.md) ·
[Architecture](docs/ARCHITECTURE.md)

[![tests](https://github.com/akourk/finledger/actions/workflows/tests.yml/badge.svg)](https://github.com/akourk/finledger/actions/workflows/tests.yml)
![tests: 1400+](https://img.shields.io/badge/tests-1400%2B-brightgreen)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Brokerages describe the same financial events in different ways. Moving assets
between them can lose acquisition history; importing overlapping CSVs can count
the same transaction twice. finledger normalizes those exports, reconstructs
holdings and cost basis, and checks its results against declared broker figures.

I built the ingestion pipeline, financial calculations, and dashboard as a
personal project, then made a fictional demonstration available for anyone to
explore. The output is one HTML file with its data and assets embedded. It can be
opened locally without a server, database, account, or frontend runtime.

## Engineering highlights

- **Deduplication that preserves legitimate repeats.** Overlapping exports are
  merged while retaining repeated transactions occurring within a single file.
- **Cost basis across account transfers.** Lot tracking carries acquisition
  history through custodial moves; reconciliation exposes missing or conflicting
  source information.
- **Checks across the whole pipeline.** Synthetic fixtures, financial invariants,
  Python/JavaScript parity tests, keyboard/browser tests, and accessibility scans
  validate ingestion through the final dashboard.

**Stack:** Python, yfinance for optional market-data enrichment, vanilla
JavaScript, CSS and SVG. GitHub Actions validates the same fictional artifact
that GitHub Pages publishes. The browser has no CDN or network dependencies.

## Try the fictional demo locally

With Python 3.10+, uv, and Node.js 22.12+ installed:

```bash
uv sync --locked --all-groups
uv run python -m tools.build_demo --output _site
```

Open `_site/index.html`. This build uses a fixed sample date, independently
constructed fictional transactions and illustrative prices, and isolated
working directories. It never reads your personal `data/` or market-data cache.
See [how the demo is built](docs/DEMO.md).

## Use your own exports

```bash
sh tools/install-hooks.sh
# Put broker CSV exports in data/.
uv run python -m src.main --init-account-mappings
# Review data/account-mappings.csv and copy its rows into data/metadata.csv.
uv run python -m src.main
```

Every account needs an explicit classification. The starter file suggests known
account types and marks ambiguous ones `REVIEW_REQUIRED`; resolve those before
running the pipeline. Open the generated `exports/dashboard.html` locally.

Supported adapters cover Robinhood, Coinbase, Coinbase Pro/GDAX, Schwab,
Vanguard and Voya retirement exports, USAA Victory Capital, Apple Savings,
and State Farm FCU. See the [format and metadata reference](docs/USAGE.md) for
supported variants and limitations.

## Explore the dashboard

| Holdings | Performance | Tax |
|:---:|:---:|:---:|
| [![Holdings with per-lot detail](docs/img/holdings.png)](docs/img/holdings.png) | [![Performance and benchmark comparisons](docs/img/performance.png)](docs/img/performance.png) | [![Tax planning from fictional inputs](docs/img/tax.png)](docs/img/tax.png) |

Ten views cover holdings, transactions, options, retirement, planning, income,
tax, crypto, performance, and a reconciled overview. Historical selections use
the selected date consistently; estimates and intentional sample reconciliation
differences carry explanations. All public screenshots use fictional data.

## Develop and validate

```bash
uv sync --locked --all-groups
npm ci
sh tools/install-hooks.sh
uv run python -m pytest tests/ -q -rs
uv run python -m tools.build_demo --output _site
npm test
uv run python -m tools.privacy_guard scan --scope worktree
uv run python -m tools.privacy_guard scan --artifact _site
```

[CONTRIBUTING](CONTRIBUTING.md) covers isolated test runs and the exact
pre-commit/pre-push checklist. [The improvement plan](docs/IMPLEMENTATION_PLAN.md)
tracks the portfolio review work; earlier audit records are historical snapshots.

## Privacy and limitations

Broker files, personal metadata, and generated dashboards stay local. Normal
market-data enrichment requests symbol prices and sectors through yfinance.
The public demo uses fictional inputs; it does not upload or display your ledger.

Privacy hooks reject protected files, inspect staged content and commit metadata,
and scan every outgoing commit. Use your GitHub no-reply commit email. Add private identifiers and distinctive values
to the ignored local denylist. Automated checks complement manual provenance
review; they cannot identify every personal figure or read text inside an image.
See [the privacy workflow](docs/PRIVACY.md).

**Not tax, financial, or investment advice.** Results depend on the completeness
of broker exports and declared metadata. Missing acquisition history, lot-relief
differences, and wash-sale treatment can cause disagreements. Reconcile against
broker statements and tax documents before relying on the output.

MIT — see [LICENSE](LICENSE).
