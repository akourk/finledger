# Usage reference

The [README](../README.md) provides a quick introduction. This reference covers
local ingestion, metadata, dashboard features, and supported formats. Broker
files and generated personal dashboards must stay local; see [Privacy](PRIVACY.md).

## Requirements

- Python 3.10+
- `uv` for installing the locked Python environment
- Node.js 22+ for browser development checks (not for using a generated dashboard)

```bash
uv sync --locked --all-groups
```

Use `uv run` before the Python commands below when using this environment.

## Usage

### Try it with the sample portfolio

A synthetic, fictional portfolio is bundled in `samples/portfolio.snapshot.json`.
For a deterministic, fully offline preview that never reads your `data/` or
`cache/`, run `uv run python -m tools.build_demo --output _site` and open
`_site/index.html`. To explore the normal ingestion pipeline in a separate
scratch checkout, import the snapshot and run:

```bash
python -m src.main --import-snapshot samples/portfolio.snapshot.json
python -m src.main
```

Open `exports/dashboard.html` to see the dashboard populated with the
sample data.  Regenerate the sample bundle from
`tools/build_sample_snapshot.py` if you want to tweak it.

### Use your own data

```bash
# Drop your broker CSV exports into data/ (any filenames are fine — they'll
# be auto-detected and renamed). First generate and review classifications.
python -m src.main --init-account-mappings
# Review data/account-mappings.csv. Resolve every REVIEW_REQUIRED account type,
# then copy its Account Group / Account Type rows into data/metadata.csv.
python -m src.main
```

Options:

```bash
python -m src.main --dry-run             # preview renames, do nothing else
python -m src.main --skip-rename         # parse without renaming
python -m src.main -o path.json          # custom JSON output path
python -m src.main --refresh-prices      # re-price the existing export
                                         #   without re-parsing CSVs
python -m src.main --refresh-caches      # force-refresh splits cache and
                                         #   retry tombstoned tickers
python -m src.main --export-snapshot snap.json   # bundle data/ → 1 JSON file
python -m src.main --import-snapshot snap.json   # restore data/ from a snapshot
                                                 #   (--force to overwrite)
```

Open `exports/dashboard.html` in any browser. It's a single file with the
full dataset inlined — no network calls at view time.

### Portable snapshots

`--export-snapshot` bundles every CSV in `data/` into a single JSON file
(plain-text, diffable).  `--import-snapshot` restores it on the other
side.  Use this to move your portfolio between machines without copying
30+ files individually.  Existing files are preserved unless you pass
`--force`.

Generated price history and its coverage metadata stay local and are not part
of the CSV snapshot or a fresh checkout. The next normal run fetches missing
prices. To preserve cached history when moving machines, privately copy
`cache/prices/` and `cache/price_cache_meta.json` together (and
`cache/price_cache.json` if using the legacy format). Keep these files out of
Git; historical prices for delisted instruments may be difficult to fetch again.
Before pulling the change that removes tracked price caches into an older
clone, back up those files privately and restore them after the update. Git
can remove unchanged tracked copies when applying that change.

Snapshots transport raw CSV text; export and import do not parse its transaction
rows. Import validates the snapshot bundle before restoring files, while a
subsequent `python -m src.main` validates transactions and builds the dashboard.
A successful restore can therefore be followed by a CSV validation error. The
`Import/export failed:` prefix is also used for normal pipeline errors; read the
file, row, and reason that follow it to identify the problem.

The Robinhood parser automatically excludes its recognized informational
disclaimer footer when all transaction cells are blank and the notice occupies
one extra column. It still rejects oversized transaction rows, unrecognized
extra cells, and other malformed transaction data. This footer needs no manual
CSV or snapshot edits; other validation errors require correcting the source
format while preserving transaction records.

## Supported brokers

| Broker               | Account type        | Detection                                           |
| -------------------- | ------------------- | --------------------------------------------------- |
| Robinhood            | Taxable             | filename contains `robinhood`, or Robinhood headers |
| Coinbase             | Taxable             | filename contains `coinbase`, or Coinbase headers   |
| Coinbase Pro / GDAX  | Taxable             | filename contains `gdax` or `coinbase-pro`          |
| Schwab Rollover IRA  | Retirement          | Schwab `_transactions_` + `rollover_ira`            |
| Schwab Roth IRA      | Retirement          | Schwab `_transactions_` + `roth_contributory_ira`   |
| Vanguard 401K        | Retirement          | filename contains `vanguard401k`                    |
| Voya 401K            | Retirement          | filename contains `voya401k`                        |
| USAA Victory Capital | Retirement (Roth)   | filename contains `usaavictorycapital`              |
| Apple Savings        | Savings             | filename contains `apple-savings`                   |
| State Farm FCU       | Savings             | `Posting Date` + `Posting Status` headers           |

You can also drop a `manual-adjustments.csv` for corrections that aren't in
any broker export (see `src/parsers/manual.py` for the column format).

### `metadata.csv` — personal info + project config

`metadata.csv` is a reserved filename for everything the broker exports
don't carry: birthday, salary, bonuses, expenses, year-end targets, and
required account classifications and optional grouping overrides. Skipped by the transaction
pipeline but read by the Overview / Retirement / Planning / Income /
Tax tabs.  Each row is `Type,Date,Amount,Symbol,Note` where `Type`
is one of:

| Type              | Used by                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| `Personal Info`   | Birthday (Note=`Birthday`) → Monte Carlo horizon, age math, Roth eligibility                     |
| `Salary History`  | Effective-as-of dates feed AGI / MAGI / Roth eligibility / cash-flow forecast / Year-by-Year     |
| `Bonus History`   | Per-event lump sums; the cash-flow forecast uses last full year as a planning estimate           |
| `Annual Expenses` | FIRE section: most recent × 25 = FI number (4% rule)                                             |
| `Target`          | Year-by-Year breakdown's Target column (Date = year, Amount = goal).  Suggested fallback uses Fidelity age × salary |
| `Account Group`   | Override `ACCOUNT_GROUPS` — Symbol = broker's raw account name, Note = simplified group key      |
| `Account Type`    | Override `ACCOUNT_TYPES` — Symbol = group key, Note = `Taxable` / `Retirement` / `Savings`       |
| `Filing Status`   | Note = `Single` / `Married Filing Jointly` / `Married Filing Separately` / `Head of Household` (default `Single`).  Drives federal + LTCG brackets, standard deduction, Roth MAGI phaseout window. |
| `State`           | Note = 2-letter state code (`WA`, `CA`, …).  Display label; pair with `State Tax Rate`.            |
| `State Tax Rate`  | Amount = marginal state income-tax rate as a decimal (`0.093`).  Added to the federal marginal for the Tax tab's combined rate + estimated capital-gains tax. |
| `Target Allocation` | Symbol = sector bucket (`ETFs`, `Cash`, …), Amount = target %.  Drives the Holdings tab's Target vs Actual rebalancing-drift view. |
| `Retirement Age`  | Amount = integer age (default `67`).  Drives Monte Carlo horizon + Planning tab projection-age default. |
| `Budget`          | A recurring living expense (rent, subscription, insurance…).  Symbol = category (`Housing`, `Subscriptions`, …), Amount = cost per period, Note = label with optional cadence suffix `@monthly` (default) / `@yearly` / `@quarterly` / `@6mo` / `@weekly`.  Date = effective-from; rows sharing a label supersede each other by date (rent increase = new row; `Amount = 0` cancels).  Drives the Income tab's Budget section + the cash-flow forecast's projected-savings line. |
| `Paycheck Deduction` | A recurring per-paycheck payroll line.  Symbol = kind (`Pre-Tax` / `Tax` / `Post-Tax` / `Withholding`), Amount = $ per paycheck (negative = a credit, e.g. a wellness refund), Note = label.  Pre-Tax rows reduce W-2 wages for AGI / MAGI / Roth eligibility; `Withholding` = voluntary extra federal withholding (reduces take-home, credited against the estimated year-end tax on realized gains); all rows feed the Income tab's Paycheck panel (gross → deductions → estimated take-home).  401(k) deferrals don't belong here — they're derived from the contribution transactions. |
| `Pay Frequency`   | Amount = pay periods per year (`12` / `24` / `26` / `52`, default `26` = biweekly).  Annualizes the paycheck rows. |
| `Tax Return`      | A figure from a filed 1040.  Date = tax year, Symbol = field (`Total Tax` = line 24, `AGI` = line 11, `Withholding` = line 25d, `Wages`, `Capital Gains`), Amount = $.  The prior year's Total Tax + AGI drive the Tax tab's **withholding safe-harbor check** (IRS Form 2210: 90%-of-current vs 100%/110%-of-prior-year, whichever is less). |
| `Lot Method`      | Symbol = account group, Note = `FIFO` / `LIFO` / `HIFO` — the lot-relief method that account's broker actually uses (e.g. Coinbase defaults to HIFO).  Affects realized gains / holding period, never balances. |
| `Cost Basis`      | True cost basis for an off-platform crypto receive finledger can't reconstruct.  Symbol = account group, Date = acquired date, Amount = total basis, Note = `"<qty> <asset>"` (optional `#N` to disambiguate). |
| `Reconcile Balance` / `Reconcile Realized` / `Reconcile Income` / `Reconcile Section 1256` / `Reconcile Other Income` | Broker-reported ground truth (statement balance, 1099-B, 1099-DIV/INT, 1099-MISC) to check finledger against — drives the Overview's Reconciliation panel.  Symbol = account group, Date = as-of date (balance) or year, Amount = the broker figure.  `Reconcile Realized` also overrides finledger's realized figure in AGI / MAGI / Roth-eligibility math. |

`Account Group` / `Account Type` rows let you onboard accounts without editing
`src/config.py`. Every account group must have an explicit type: `Taxable`,
`Retirement`, or `Savings`. Use `--init-account-mappings` to write suggestions
to `data/account-mappings.csv`, review them, then copy the rows into
`metadata.csv`. Generic broker names may require a decision; the pipeline
refuses to publish until every group is classified.

Older setups using `retirement-data.csv` keep working — the parser
falls back to it when `metadata.csv` is absent.

## Dashboard

The dashboard is a tabbed single-page app (URL hash routing, so
`dashboard.html#planning` deep-links). Tabs:

- **Overview** — persistent top bar (Portfolio Value, Total Return,
  1-Day Change), alerts / "what changed since last run" /
  **reconciliation** (finledger vs broker-reported figures) feedback panels,
  stat cards (Cost Basis, Unrealized/Realized P&L, Net Contributed,
  Income, Current Drawdown), history chart with overlay toggles (Cost
  Basis, Unrealized Gain, SPY Benchmark, Net Contributed,
  Year-over-Year), top 10 holdings, recent transactions, allocation
  donut.
- **Holdings** — by Asset / Account / Type / Sector, with cost basis and
  unrealized gain columns. Plus a **Target vs Actual** rebalancing-drift
  view (when `Target Allocation` is set), a concentration grid
  (positions / sectors / accounts with HHI + top-5 share), and a lot
  method comparison table (FIFO / LIFO / HIFO / Average).
- **Transactions** — every row with per-field filters, free-text search,
  symbol filter, and column visibility toggle.
- **Options** — cumulative P&L chart, open contracts (with DTE), closed
  trade history, per-underlying win/loss breakdown, lifetime stats.
- **Retirement** — contributions by year vs IRS limits with a per-year
  Roth Eligibility column (MAGI bar vs single-filer phase-out window),
  retirement balance over time, Roth vs Traditional inline stacked bar.
  Reads birthday and salary/bonus history from `data/metadata.csv`.
- **Planning** — scenario projection (5% / 7% / 9% / personal historical
  rate), Monte Carlo with All-Accounts ↔ Retirement-Only toggle (1000
  stochastic trials, equity bucket at 8% mean / 16% stdev plus a
  deterministic 4%-yield cash bucket for Savings / USD), a FIRE
  section (4% rule × `Annual Expenses`) with FI Number, FI Progress,
  Coast FI, P(reach FI in window), and per-percentile FI date table,
  plus the **Year-by-Year** table (year-end balances per account with
  Δ% / Δ$, contribution columns, targets, and a savings-rate column).
- **Income** — stat cards, 12-month total cash-flow forecast (salary +
  bonuses + projected dividends − projected retirement contributions −
  paycheck taxes & deductions − budget living expenses, with a
  projected-savings line), a **Paycheck** panel (gross → pre-tax
  benefits / 401(k) / estimated federal tax / payroll taxes / post-tax
  → take-home, per-paycheck and annualized — from `Paycheck Deduction`
  metadata rows), a **Budget** section (monthly/annual burn, category
  breakdown, passive-income coverage, and drift vs the `Annual
  Expenses` figure — from `Budget` metadata rows), 12-month dividend /
  interest forecast per held position, annual summary, monthly chart,
  by-source table.
- **Tax** — grouped into "Forward planning — actionable today" (income
  build-up, marginal rate estimates with override inputs, Tax Bracket
  Fill bar, Tax-Loss Harvest Candidates, Potential Wash Sales) and
  "Historical realizations" (Realized Gains by Year, By Asset). Realized
  gains are split short-term / long-term / Section 1256 (60/40 for
  broad-based index options); §1256 columns auto-hide when there's no
  §1256 activity. Current-year figures are extrapolated from YTD pace
  (salary, bonuses, dividends, 401K — capped at IRS limit; realized
  gains kept YTD-only since sells are lumpy). Includes an **estimated
  tax on realized gains** panel (federal + state + NIIT, net of any
  voluntary extra paycheck withholding, with a suggested quarterly
  amount on the remainder), a **withholding safe-harbor check** (vs
  100%/110% of the prior year's 1040 — needs `Tax Return` metadata
  rows), and a **Form 8949 CSV** export of taxable-account disposals.
- **Crypto** — per-coin holdings, realized & income, recent activity,
  conversion/wrap log.
- **Performance** — anchor stat cards (Total Return, Realized,
  Unrealized, Net Contributed, Fees Paid), account + window selectors,
  windowed cards (incl. Sharpe / Sortino / Calmar / Max Drawdown), then
  a **Returns ↔ Risk** sub-view.  Returns: Your Portfolio vs SPY / BND /
  VXUS / 60-40 multi-benchmark chart, By-Account TWR (Modified Dietz +
  money-weighted XIRR), Annual Returns table, top 10 winners / losers.
  Risk: Drawdown chart, year × month Monthly P&L heatmap (with YTD
  column + best/worst-month summary), Recent Daily P&L bars.

Dark theme, tabular-numeric formatting, mobile responsive
(including narrow mobile viewports), no JS framework.


## Input validation and recovery

Malformed nonblank numbers, nonfinite values, unsupported unvalued trades, and
recognized files whose transaction rows cannot be parsed stop publication. Error
messages identify source context without printing the problematic financial
value. Fix the source/export format and rerun; the last successful JSON and HTML
remain available if validation or rendering fails. `--dry-run` previews changes
without writing outputs. The full snapshot is validated before restore writes.

Coinbase assets are normalized using their broker context. A crypto-to-crypto
Coinbase Pro match without a USD valuation is reported as unsupported; provide a
supported export with USD values rather than accepting invented zero proceeds.
A ticker shared by a stock/ETF and cryptocurrency must be classified by source.

## Market data and privacy

Local ingestion can request prices and sectors for symbols through yfinance.
The provider sees those market-data requests, not an uploaded transaction ledger.
Generated dashboards contain their entire dataset; only share them intentionally.
The public demo uses independently generated fictional transactions and prices,
with a fixed sample date and no network access during its build.

See [CONTRIBUTING](../CONTRIBUTING.md) for tests and extension work, and
[Architecture](ARCHITECTURE.md) for design decisions and limitations.
