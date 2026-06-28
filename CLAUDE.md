# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this project is

`fin` is a personal portfolio tracker. It reads raw transaction CSVs exported
from brokerages, normalizes them into a common schema, and produces a
self-contained HTML dashboard.

The app is ~45 Python modules under `src/` (~11k lines), organized into
the `parsers/`, `analytics/`, and `dashboard/` packages plus top-level
pipeline modules — with **no external dependencies beyond `yfinance`**
(used for sector lookups AND historical price fetching, with on-disk
caches and exponential-backoff failure handling to avoid repeated
calls). No framework, no database, no build step. See the "Architecture"
section below and README.md's project-layout tree for the module map.

## Run it

```bash
python -m src.main              # full pipeline
python -m src.main --dry-run    # preview renames, no side effects
python -m src.main --skip-rename
```

Output lands in `exports/transactions.json` and `exports/dashboard.html`.

## Pipeline (read `src/main.py` top to bottom — it is the spec)

1. **Scan** (`scanner.detect_broker`) — identify each CSV in `data/` by
   filename pattern, then CSV header fallback. Returns a broker key or
   `manual` / `skip` / `unknown`.
2. **Rename** (`scanner.rename_data_files`) — two-pass rename to
   `{broker-prefix}.csv` or `{broker-prefix}-{n}.csv`. Two-pass (`.tmp-*`
   intermediates) avoids collisions when swapping names.
3. **Parse** (`parsers.parse_all_files`) — dispatch to a per-broker parser.
   Every parser returns the same 10-field dict (`date, account, symbol,
   action, quantity, price, fees, amount, description, source`).
4. **Deduplicate** (`export.deduplicate`) — identical rows across *different*
   source files are overlapping exports; identical rows *within* one file are
   legitimate (e.g. three same-day CBETH sells). The hash-based dedupe keeps
   the max intra-file count.
5. **Reconcile USAA → Schwab Roth transfers** (`main._reconcile_usaa_to_schwab_transfer`) —
   Schwab reports the inbound "Security Transfer" leg but USAA's export doesn't
   contain the outbound leg. Synthesize matching `Synthetic Transfer Out` rows
   (and small `Transfer Reconcile` residuals for CSV-precision drift) so USAA
   balances zero out.
   Then **`_reconcile_coinbase_intra_transfers`** (runs *after* action
   normalization) re-tags Coinbase Pro `Deposit` / `Withdrawal` rows as
   `Transfer In` / `Transfer Out` when a same-date, same-amount counter-leg
   exists on the Coinbase regular side. Both wallets share `account_group =
   Coinbase`, so the pair becomes an intra-group no-op for the basis walker
   AND drops out of `net_contributed` / SPY-benchmark / FIRE accounting.
   Without this, every regular→Pro shuffle would inflate "external money in"
   by the transferred amount.
6. **Symbol normalization** — apply `SYMBOL_MAP` (e.g. `ETH2 → ETH-USD`), add
   `-USD` suffix to crypto tickers, map empty symbol → `USD`.
7. **Account tagging** — add `account_group` (Robinhood, Coinbase, Roth IRA,
   Rollover IRA, 401K, Apple Savings) and `account_type` (Taxable, Retirement,
   Savings) via `ACCOUNT_GROUPS` / `ACCOUNT_TYPES`.
8. **Action normalization** (`normalize.normalize_action`) — map raw broker
   action strings to a canonical vocabulary (`Buy`, `Sell`, `Dividend`,
   `Transfer In/Out`, `Reinvest`, `Contribution`, `Neutral`, …) via an
   ordered rule table. First match wins; unmatched actions pass through
   title-cased.
9. **Balance + value computation** — sort by `(date, account_group, symbol,
   direction)` where increases sort before decreases on the same day (so
   same-day `Transfer In` posts before `Transfer Out` and balances don't go
   negative). Running balance per `(account_group, symbol)`. `USD` balances
   are tracked only in Savings accounts — taxable/retirement cash balances
   from broker CSVs are incomplete (sell proceeds often not reported) and
   would be misleading.
10. **Holdings** — aggregate final non-zero balances per symbol (and per
    `account_group × symbol`). Apply the **dust filter**:
    - if we have a price: drop if `|qty × price| < $0.01`
    - if we don't: drop if `|qty| < 1e-6`
    This kills residuals from broker CSV precision mismatch (Coinbase Wrap
    reports 16 decimals; the paired Sell reports 7 — tiny residuals linger).
11. **Sector enrichment** (`sectors.enrich_holdings`) — lookup order: hard
    rules (USD→Cash, `-USD`→Cryptocurrency, multi-word→Mutual Funds) → cache
    (`cache/sector_cache.json`) → yfinance `.info` → `"Other"`. Cache is
    hand-editable; both hits and misses cache so no symbol is fetched twice
    per run.
12. **Price cache coverage** (`prices.ensure_coverage`) — for each symbol
    ever seen in transactions, make sure `cache/price_cache.json` covers
    `[earliest_txn_date, today]`. Missing ranges are fetched from yfinance
    and merged in. `cache/price_cache_meta.json` tracks per-symbol
    `covered_start`, `covered_end`, `last_fetch`, `failure_count`,
    `retry_after`, and `tombstone` (true after 5 consecutive failures).
    Delisted / renamed tickers stop being retried.  Prices use
    `auto_adjust=False` — yfinance's `Close` column is still split-adjusted
    (that adjustment can't be disabled), but not dividend-adjusted, so it
    matches actual market close on each date. Alongside prices, each
    symbol's split history is fetched and stored in
    `cache/splits_cache.json`; see `split_factor_since` and the History
    step for why.
13. **Current-price override for holdings** — `last_prices` starts from the
    most recent non-zero transaction price (fallback), then overrides with
    `prices.get_price(sym, today)` for every symbol with a non-zero
    balance. The cache is almost always fresher than the last transaction
    for buy-and-hold positions. Symbols the cache can't fetch (multi-word
    fund names, delisted tickers) keep the transaction-price fallback.
13b. **Cost basis** (`basis.compute_basis_default` + `compute_basis_all_methods`) —
    FIFO walker annotates every txn in place with `cost_basis`,
    `realized_gain` (sells only), and `basis_effect`. The walker runs
    four more times under LIFO / HIFO / Average to produce per-method
    comparison totals for the dashboard's "Lot Method Comparison"
    section. `compute_cash_summary` tallies cash-flow events
    (contributions, withdrawals, income) for the header stat cards.
    Basis rollup on holdings is merged into `holdings` /
    `holdings_by_account` in step 13.

13c. **Derived analytics** (`analytics.build_analytics`) — single source
    of truth for dashboard-facing derived figures. Precomputed and
    embedded in `exports/transactions.json` under the `analytics` key.
    The dashboard JS consumes these directly instead of recomputing.
    Sections (each is its own module under `src/analytics/`):
    - `rollover_bridges` — Distribution→Transfer-In pairs (custodian
      moves) with 90-day / 5% tolerance matching. Consumers apply a
      bridge adjustment to effective balance during the window.
    - `retirement_contributions_by_year` — tax-year-attributed
      contributions (USAA PRIOR YEAR handled). 401K and Rollover IRA
      merged since Rollover IRA is legacy 401K.
    - `performance_by_filter` — per-filter (Total, Retirement,
      Robinhood, Coinbase, 401K, Roth IRA, Rollover IRA, Apple Savings)
      dict of `{annual: [...], summary: {...}, filter_groups: [...]}`.
      Uses Modified Dietz per sub-period with bootstrap-noise guards.
    - `options` — open_contracts, closed_trades (with parsed
      underlying/expiry/type/strike + hold_days), by_underlying,
      annual_summary, cumulative_pnl, stats.
    - `crypto` — per_coin, recent_activity, conversions, stats.
    - `income` — by_year, by_month, by_source, total.
    - `tax` — realized_by_year / _by_symbol / _by_underlying,
      harvest_candidates, wash_sales, rate_estimates_by_year,
      section_1256_underlyings, `form_8949` (taxable-account
      disposals in IRS Form 8949 layout — description, acquired/sold
      dates, proceeds, basis, gain, term — for the Tax tab's CSV
      download).  `rate_estimates_by_year[Y]` includes
      `bracket_fill` (per-IRS-bracket fill amounts, drives the Tax
      tab's bracket bar; top bracket's `room_left` is `None`, never
      `inf`), `headroom_to_next_bracket`, `realized_st` /
      `realized_lt` (taxable-account sells classified by
      `_classify_realized` — feed AGI), the estimated tax on YTD
      realized gains (`est_cap_gains_tax_federal` / `_state` /
      `est_niit` / `_total` / `est_quarterly_payment`), and an
      `is_projection` flag with `year_fraction_observed` for the
      current year (salary, bonuses, dividends, and 401K extrapolated
      from YTD pace; 401K capped at IRS limit; realized gains kept
      YTD-only since sells are lumpy).
    - `rebalancing` — Target vs Actual sector-allocation drift
      (`{rows: [{bucket, target_pct, current_pct, drift_pct,
      action_value}], untargeted_pct, ...}`) from `Target Allocation`
      metadata rows.  `None` when no targets defined.
    - `reconciliation` — fin's computed figures vs broker-reported
      ground truth from `Reconcile *` rows in `metadata.csv`
      (`{rows: [{kind, account_group, label, reported, computed,
      delta, status, note, detail}], summary: {total, ok, warn,
      off}}`).  Kinds: `balance` (vs nearest history snapshot),
      `realized` (excl §1256), `section_1256`, `income` (div+int),
      `other_income` (rewards+lending, for crypto 1099-MISC).
      `status` = ok / warn / off (abs-$5 floor then 0.5% / 2%
      bands).  `None` when no `Reconcile` rows defined.  Computed by
      `analytics/reconcile.py`; rendered as a collapsible panel on
      the Overview tab.  Deltas are expected & explainable in known
      cases (wash-sale deferral, broker non-FIFO lot relief, broker
      cash-sweep interest absent from the activity CSV).
    - `positions` — per-symbol realized+unrealized rollup with
      pct_return, plus top-10 winners/losers.
    - `header_summary` — persistent top-bar (1-day change, etc.).
    - `concentration` — positions / sectors / account_groups /
      account_types each as a sorted-by-pct list, plus Herfindahl
      index, top-5 share, and risk flags.
    - `drawdown` — series of `{date, drawdown_pct, running_peak,
      value}`, max-drawdown window with peak/trough/recovery dates,
      and current_drawdown_pct.
    - `daily_pnl` — list of `{date, value, change, change_pct}` for
      the last 30 calendar days, walking back from today's positions
      and re-pricing at recent dates (market-only moves; same-day
      cash flows excluded).
    - `trading_heatmap` — per-day trade counts + dollar volume for
      the calendar heatmap on the Performance tab.
    - `income_calendar` — 12-month dividend / interest forecast per
      held position (flat extrapolation of the last trailing 12mo).
    - `monthly_pnl` — year × month grid of investment returns
      (start/end snapshot delta minus net_contributed delta) plus
      Sharpe / Sortino ratios.  Ratios filter on (start_value ≥ 1%
      of all-time peak) AND (|return| ≤ 50%) — a self-scaling
      denominator gate plus a magnitude sanity cap that protects
      against parser-induced data artifacts.  Best/worst-month
      figures are filtered the same way; the heatmap shows all months.
    - `monte_carlo` — `{retirement, all_accounts, fi_threshold,
      annual_expenses}`.  Both scenarios run 1000 trials with seed=42
      (deterministic), 8% mean / 16% stdev returns on the equity
      bucket, deterministic 4% yield on a cash bucket (Savings + USD
      positions), trailing-3-year average annual contribution.
      `all_accounts` carries a `fire` sub-dict with the year each
      percentile band first crosses `fi_threshold = annual_expenses
      × 25` (4% rule).
    - `changes` — run-over-run diff vs `cache/last_run.json`.  Saves
      a fresh snapshot at the end of each run.  First run returns
      `{first_run: True}`.
    - `alerts` — aggregated severity-tagged signals (concentration
      flags, harvest candidates, stale data, coverage gaps, failing
      tickers, CUSIP rename suggestions, long-term-soon thresholds).

    **Rule**: if the dashboard needs a derived figure that multiple
    views consume, compute it in `analytics/` and have all consumers
    read from the JSON. This has already caught several bugs where the
    JS recomputed something subtly differently than `main.py` /
    `basis.py` / `history.py`.

14. **Portfolio history** (`history.compute_history`) — walks txns in
    chronological order, maintains running balances per
    `(account_group, symbol)`, and at each monthly sample date (+ today)
    prices every non-zero position. At each sample, the as-of-date share
    count is scaled by `prices.split_factor_since(sym, date)` (product of
    split ratios strictly AFTER the sample date) to convert to
    today-basis. This cancels out the split adjustment baked into
    yfinance's historical `Close`, so `adjusted_qty × adjusted_price`
    equals the actual dollar value on that date. For symbols the cache
    can't price (multi-word fund names, tombstoned delisted tickers), it
    falls back to the most recent transaction price at or before the
    sample date (no split adjustment — txn prices are as-of-trade).
    Emits snapshots with `total`, `by_account_group`, `by_account_type`,
    `by_sector`, `total_cost_basis` / `cost_basis_by_group` /
    `cost_basis_by_type`, `benchmark_spy` / `benchmark_spy_price` /
    `benchmark_bnd` / `benchmark_vxus` / `benchmark_60_40` (multiple
    benchmark series for the Performance tab's overlay),
    `net_contributed`, `priced_pct` (coverage canary — 1.0 means every
    position had a price), and `positions` (per-`(account_group,
    symbol)` rollup with `quantity`, `price`, `value`, `cost_basis` at
    that snapshot date).  The per-snapshot `positions` list captures
    FIFO state at every sample date and is what lets the dashboard
    show arbitrary-date holdings / unrealized P&L without a JS replay
    of the basis walker.  Dust filter on `positions` mirrors main.py's
    `_is_dust` so counts match the current holdings table exactly.
15. **Export** (`export.export_json`) — JSON with a fixed field order so
    dashboard columns stay logical.
16. **Dashboard** (`dashboard.generate_dashboard`) — string-interpolate the
    full transactions+holdings+history+basis+retirement-meta JSON into a
    single HTML file. One file, no external assets. The dashboard is
    organized as a **tabbed single-page app** with hash routing:

    - **Overview** — alerts/changes/reconciliation feedback panels
      (collapse if empty; reconciliation shows fin vs broker-reported
      figures with ok/warn/off status — see `analytics.reconciliation`),
      stat cards (Value, Cost Basis, Unrealized/Realized P&L, Net
      Contributed, Income, Total Return, Current Drawdown), history
      chart with overlay toggles (Cost Basis, Unrealized Gain, SPY
      Benchmark, Net Contributed, Year-over-Year), top 10 holdings,
      recent 15 transactions, allocation donut (by account / type /
      sector), concentration grid (positions/sectors/accounts).
    - **Holdings** — holdings table (by asset/account/type/sector) +
      lot method comparison table.
    - **Transactions** — full ledger with filters, search, column toggle.
    - **Options** — lifetime P&L, win rate, cumulative P&L chart, open
      contracts, closed trades, per-underlying breakdown (with cross-
      link to Tax tab for ST/LT/§1256 split).
    - **Retirement** — contributions-by-year table with Roth Eligibility
      column (per-year MAGI bar vs single-filer phase-out window —
      MAGI estimated from salary + bonuses + portfolio income − 401K,
      with current-year projection asterisk), retirement balance chart,
      Roth vs Traditional inline stacked bar.  Forward-looking
      content (projection, Monte Carlo, FIRE) lives on the **Planning**
      tab to keep this tab focused on account-specific facts.
    - **Planning** — scenario projection (5%/7%/9%/personal-rate),
      Monte Carlo with All-Accounts ↔ Retirement-Only toggle (uses
      equity bucket at 8% / 16% normal returns and a deterministic
      cash bucket at 4% yield for Savings + USD positions; FI
      threshold dashed line drawn on All-Accounts mode), FIRE
      section (4% rule × `Annual Expenses` from
      `data/metadata.csv` → FI number; FI Progress, Coast FI,
      P(reach FI in window), per-percentile FI date table).
    - **Income** — stat cards, 12-month total cash-flow forecast
      (salary + bonuses + projected dividends − projected retirement
      contributions), 12-month dividend / interest forecast per held
      position, annual summary, monthly chart, by-source table.
    - **Tax** — grouped into "Forward planning — actionable today"
      (Tax Rates & Income panel with override inputs, Tax Bracket
      Fill bar, Tax-Loss Harvest Candidates, Potential Wash Sales)
      and "Historical realizations" (Realized Gains by Year, By
      Asset).  §1256 columns are conditional — only rendered when
      any year/symbol has §1256 activity.  Section 1256 underlyings
      hardcoded in `dashboard/app.js` (SPX, NDX, NDXP, SPXW, XSP,
      RUT, DJX, VIX) get 60% LT / 40% ST regardless of holding
      period.
    - **Crypto** — per-coin holdings + realized + income, recent
      activity, conversion/wrap log.
    - **Performance** — stat cards (Total Gain, Realized, Unrealized,
      Net Contributed, Sharpe, Sortino), Your Portfolio vs SPY/BND/
      VXUS/60-40 multi-benchmark chart, Annual Returns table, By
      Account TWR section, Recent Daily P&L bars, Monthly P&L
      year×month heatmap (with YTD column + best/worst-month
      summary), Drawdown chart, Trading Activity heatmap, Top 10
      Winners / Losers.

    Each tab's renderer is registered with `registerTabRenderer(name,
    fn)` and runs lazily on first activation. The Overview tab renders
    immediately on load. URL hash (`#options`, `#planning`, …)
    deep-links and persists across reload.

## Invariants the code relies on

- **Quantities, amounts, and fees are always non-negative after parsing.**
  Direction is encoded in the normalized `action`. The parsers abs() these
  fields on the way in; main.py's balance logic picks a sign based on
  `_SUBTRACT_ACTIONS` / `_NEUTRAL_ACTIONS`.
- **Ambiguous actions must be split by sign *before* abs()**. Examples:
  - Robinhood `ACH` → `ACH Deposit` / `ACH Withdrawal`
  - Coinbase `Convert` / `Wrap Asset` / `Transfer` → `… In` / `… Out`
  - Voya `TRANSFER` → `TRANSFER IN` / `TRANSFER OUT`
  - Coinbase Pro `match` rows → `buy` / `sell` based on raw amount sign
  If you add a new directional action, split it in the parser, not downstream.
- **USD balance tracking is deliberately skipped for non-Savings accounts.**
  Don't try to "fix" this — sell proceeds are underreported in most broker
  exports and you'll produce worse data.

  **Carve-out: Coinbase USD bridge in `history.py` snapshots.**  After
  the GDAX-deprecation marker fix and the `_reconcile_coinbase_external_funding`
  cumulative-min synth, Coinbase USD movements are provably complete
  (the natural-flow walker ends at ~$0, matching the user's actual
  balance).  `history.compute_history` calls `main.coinbase_usd_series`
  to get the implicit USD wallet at each snapshot date and adds it to
  `by_account_group['Coinbase']`, `by_account_type['Taxable']`,
  `by_sector['Cash']`, `total`, `total_cost_basis`, and emits a
  synthetic `(Coinbase, USD)` position so dashboard as-of-date
  filtering shows the bridge.  Threshold is $1 (looser than the $0.01
  dust rule) to drop the floating-point drift the running sum
  accumulates over thousands of txns.  Without this bridge, snapshots
  that fell between a Sell and the next Buy/Withdrawal showed the USD
  proceeds as $0 of value, which corrupted Coinbase TWR (-87% vs the
  -37% that reflects actual crypto performance).  Coinbase-only —
  other brokers don't have the same data-completeness property and
  a similar bridge would inflate values.
- **ETH ↔ ETH2 Coinbase conversions are Neutral** (same underlying asset,
  just the old staking wrapper). Other conversions synthesize a paired
  `Convert In` leg for the acquired asset.
- **USAA "Dividend" rows are reclassified to a USD cash event** — USAA
  records both the dividend share-payout *and* a matching Buy reinvestment,
  so crediting shares from the Dividend row would double-count.
- **Robinhood option contracts live under their own symbol, separate
  from the underlying stock.** The parser rewrites the symbol for
  BTO / STC / OEXP / OEXCS rows to the contract's description
  (`"META 12/18/2026 Call $800.00"`) rather than the bare Instrument
  (`"META"`). BTO and OEXP descriptions differ by a `"Option Expiration
  for "` prefix on OEXP — `_option_contract_symbol` strips it so both
  legs of the same contract land on the same key. This keeps option
  basis, realized gain, and lot tracking entirely separate from the
  underlying stock's position. The new `"Options"` sector (detected in
  `sectors._classify_no_fetch` by ` Call ` / ` Put ` markers) catches
  these symbols before the multi-word → Mutual Funds fallback.
- **Robinhood option exercises (OEXCS + OCC) are paired in the parser.**
  Robinhood writes the contract disposal and its cash proceeds as two
  rows: OEXCS ("1S" quantity = 1 contract, empty amount) and OCC
  ("Option Maturity: Cash Component", qty=0, amount=cash payout). The
  Robinhood parser does a two-pass pair on `(date, underlying ticker)`
  (OCC's Description is generic so only Instrument is usable for the
  key) and rolls the OCC amount into the matching OEXCS row, dropping
  the OCC. Downstream: `Option Exercise` is in `_SUBTRACT_ACTIONS` and
  basis.py's `remove` class, so the contract leaves the lot queue and
  realized gain = cash_proceeds − basis. OEXP rows also default qty to
  1 when Robinhood leaves the field blank, so expired options actually
  vanish from the balance instead of piling up forever.
- **`metadata.csv` is user-maintained metadata + project config.** The
  scanner classifies it as "skip" so the transaction pipeline never
  touches it, but `metadata.parse_metadata` reads it early in main()
  to populate `retirement_meta` AND apply Account Group / Account
  Type overrides onto `config.ACCOUNT_GROUPS` / `ACCOUNT_TYPES`.
  Supported `Type` values:
  - `Personal Info` (Note=`Birthday`) — drives age math + Monte Carlo
    horizon.
  - `Salary History` — effective-as-of dates feed AGI / MAGI / Roth
    eligibility / cash-flow forecast / Year-by-Year suggested-Target.
  - `Bonus History` — per-event lump sums; the cash-flow forecast
    uses last full year as a planning estimate.
  - `Annual Expenses` — most recent entry × 25 = FI number for the
    Planning tab's FIRE section (4% safe withdrawal rule).  Without
    this row, the FIRE section renders an empty-state prompt instead
    of figures.
  - `Target` — per-year portfolio-value targets.  `Date` is the year
    (or full ISO date — only the year prefix is used).  `Amount` is
    the target value.  Surfaced in the Overview's Year-by-Year table.
    When absent, the dashboard falls back to a Fidelity age × salary
    benchmark suggestion.
  - `Account Group` — Symbol = broker's raw account name, Note =
    simplified group key (Roth IRA / 401K / etc.).  Mutates
    `ACCOUNT_GROUPS` in place at the start of step 4b-pre.
    `src/config.py`'s `ACCOUNT_GROUPS` starts empty; ALL mappings
    live in metadata.csv (they're user-specific — which 401K rolled
    into which IRA, what you want to call your Coinbase wallets).
    Fallback when missing: `account_group` defaults to the raw
    broker account name.
  - `Account Type` — Symbol = group key, Note = `Taxable` /
    `Retirement` / `Savings`.  Same mutate-in-place pattern.
  - `Filing Status` — Note = `Single` / `Married Filing Jointly` /
    `Married Filing Separately` / `Head of Household` (case-
    insensitive; aliases like `MFJ` / `joint` / `HoH` accepted).
    Drives `_BRACKETS_BY_YEAR_STATUS` / `_LTCG_BY_YEAR_STATUS` /
    `_STD_DED_BY_YEAR_STATUS` / `_ROTH_MAGI_PHASEOUT_BY_STATUS`
    selection in `src/analytics/tax.py`.  These tables are now the
    **single source of truth**: `tax_tables_to_json()` serializes
    them into the JSON export under `tax_tables`, and the dashboard
    JS reads `DATA.tax_tables` (the `FEDERAL_BRACKETS` etc. literals
    in `app.js` are emergency fallbacks only).  Adding a new tax year
    is a one-file change in `analytics/tax.py`.
  - `State` — Note = 2-letter state code (e.g. `WA`, `CA`).  A
    display label (surfaced in the Tax tab header); no automatic
    state-tax *brackets* are applied (state tax varies too much to
    encode reliably).  Pair it with a `State Tax Rate` row for a
    single representative marginal rate.
  - `State Tax Rate` — Amount = marginal state income tax rate as a
    decimal (e.g. `0.093`; `9.3` is also accepted and divided by 100;
    clamped to `[0, 0.20]`, default `0`).  Added to the federal
    marginal for the Tax tab's "Combined" rate display and the
    estimated-capital-gains-tax computation.
  - `Target Allocation` — Symbol = sector bucket (matches the
    holdings' sectors, e.g. `Technology` / `ETFs` / `Cash`),
    Amount = target percent of portfolio (accepts `60` or `0.6`).
    Drives the Holdings tab's **Target vs Actual** rebalancing-drift
    view (`analytics/rebalancing.py`).  Absent → the section hides.
  - `Lot Method` — Symbol = `account_group`, Note = the lot-relief
    method that account's broker actually uses (`FIFO` / `LIFO` /
    `HIFO`).  Overrides the FIFO default for that account's realized-gain
    / cost-basis attribution (e.g. Coinbase defaults to HIFO).  Parsed
    into `retirement_meta["lot_methods"]` and passed to
    `compute_basis_default(txns, account_methods=...)`.  Affects realized
    gains / holding period / MAGI / Roth eligibility, never balances.
  - `Reconcile Balance` / `Reconcile Realized` / `Reconcile Income`
    / `Reconcile Section 1256` / `Reconcile Other Income` —
    broker-reported ground truth to check fin against (statement
    balances, 1099-B realized / §1256, 1099-DIV box 1a + 1099-INT
    income, and crypto 1099-MISC "Other Income" = staking rewards
    via `Reconcile Other Income`, which sums the rewards+lending
    income buckets).  Symbol = `account_group`;
    Date = as-of date (balance) or year (the rest); Amount = the
    broker figure; Note = source.  Parsed into
    `retirement_meta["reconcile"]`, consumed by
    `analytics/reconcile.py`, surfaced in the Overview's
    Reconciliation panel.  Absent → the panel hides.  Realized rows
    should be the 1099-B *equity* grand-total (exclude §1256, which
    has its own row).
  - `Retirement Age` — Amount = integer age (sanity-clamped to
    30..100, default 67).  Drives the Monte Carlo simulation
    horizon (`analytics/__init__.py` `years_to_60` is now
    `target_age - age`) and the default value of the Planning
    tab's projection-age input.
    `ACCOUNT_TYPES` also starts empty in config.py; fallback when
    missing is `Taxable`.

  Hand-editable. See `src/metadata.py` for the column format.

  The legacy filename `retirement-data.csv` still works as a fallback;
  if both exist, `metadata.csv` wins.  `src/retirement.py` is a
  re-export shim — prefer `from .metadata import parse_metadata` in
  new code.
- **Section 1256 underlyings are single-sourced from
  `SECTION_1256_UNDERLYINGS` in `src/analytics/tax.py`**.  They're
  emitted into the export (under `analytics.tax.section_1256_underlyings`
  AND `tax_tables.section_1256_underlyings`) and the dashboard JS reads
  them from there (the `app.js` literal is an emergency fallback only).
  These are cash-settled broad-based index options (SPX, NDX, NDXP,
  SPXW, XSP, RUT, DJX, VIX) that get 60% long-term / 40% short-term tax
  treatment regardless of holding period.  ETF options (SPY, QQQ) are
  NOT Section 1256.  To add a broad-based index option, edit the Python
  constant only.

- **External cash-flow accounting goes through one helper:
  `basis.txn_external_cash_flow(t) -> float`**.  Returns +amount for
  inflows, -amount for outflows, 0 for everything else.  Three
  carve-outs handled in this single place:
  1. `Contribution Reversal` → -amount (was missed by old local sets
     in basis.py / history.py — would silently drop the reversal,
     leaving the original Contribution un-balanced).
  2. `Distribution` on Roth IRA / Rollover IRA → 0 (custodial
     rollovers and intra-IRA fund consolidations are matched by a
     `Transfer In` on the destination side, not external money out).
  3. `Buy` on Roth IRA with description containing
     `PRIOR YEAR CONTRIBUTION` / `CURRENT YEAR CONTRIBUTION` →
     +amount (USAA Victory Capital exports represent contributions
     as Buys with these descriptive markers, not as separate Deposit
     lines).

  Consumers: `basis.compute_cash_summary`,
  `history._compute_net_contributed_series`,
  `history._compute_benchmark_series`,
  `analytics._shared.net_cash_flow`.  All four call this helper —
  if you find yourself duplicating cash-flow classification, route
  it through here instead.  Pinned by
  `tests/test_actions_catalog.py::test_external_cash_flow_helper_handles_all_carve_outs`.

- **Coinbase Pro trade-leg cash is NOT external money.** The Coinbase
  Pro parser pairs `match` rows by trade_id and emits two transactions:
  the crypto leg (Buy/Sell) AND a USD leg with a distinct action name
  (`trade_settle_in` / `trade_settle_out`).  These normalize to canonical
  `Trade Settle In` / `Trade Settle Out` actions (catalog: balance
  add/subtract, basis ignore, **cash_flow neutral**) so they stay out
  of `CASH_ADD_ACTIONS` / `CASH_SUB_ACTIONS` / `net_contributed`.
  Treating them as `Deposit` / `Withdrawal` (the original bug) inflated
  every crypto sell as $$ external inflow and corrupted FIRE, SPY-
  benchmark comparisons, and Sharpe/Sortino.  Pinned by
  `tests/test_actions_catalog.py::test_trade_settle_actions_are_cash_flow_neutral`.

- **Coinbase regular ↔ Pro intra-wallet transfers** look like a real
  Deposit on the Pro side because the GDAX export doesn't tag them.
  `_reconcile_coinbase_intra_transfers` in `main.py` runs after action
  normalization and pairs Pro non-match `Deposit` / `Withdrawal` with
  same-date, same-amount Coinbase regular `Transfer Out` / `Transfer
  In`; matched legs are re-tagged as `Transfer In` / `Transfer Out`
  on both sides → intra-`Coinbase` group → no-op.  Unpaired legs
  (e.g. legitimate bank-to-Pro deposits) are left as `Deposit`.
- **Orphan OEXP/OEXCS** (the BTO happened before the CSV window began)
  can push a contract balance negative. `_is_dust` in main.py treats
  `qty < 0 with no price` as dust so those phantom shorts don't appear
  in the holdings table. The ledger still records the subtract; we
  just don't display it.
- **`manual-adjustments.csv`, `metadata.csv`, and the legacy
  `retirement-data.csv` are never renamed** (see `SKIP_RENAME` in
  `src/config.py`).  `metadata.csv` is a personal/config file
  (birthday, salary, account-group overrides, etc.) that the
  transaction pipeline ignores via `broker = "skip"` in `scanner.py`.
  Don't confuse it with actual transaction data.

## Adding a new broker

1. Add detection to `scanner.detect_broker` (filename pattern + header
   fallback in `_detect_by_headers`).
2. Add the key to `CANONICAL_PREFIXES` in `config.py`.
3. If it maps to an existing `account_group`, no `ACCOUNT_GROUPS` /
   `ACCOUNT_TYPES` change needed; otherwise add both.
4. Write a parser in `parsers.py` that returns the common 10-field dict.
   Use `_num`, `_date_mdy`/`_date_ymd`/`_date_dmy`/`_date_iso` helpers. Split
   ambiguous actions by sign *before* abs().
5. Register it in the `_PARSERS` dispatch dict.
6. Add action-normalization rules to `normalize.RULES` — scoped by
   `account_group` so you don't collide with other brokers' action names.

## Adding a new normalized action

The action vocabulary is centralized in **`src/actions.py`** (single
source of truth — see "Architecture: action catalog" below).  To add
a new canonical action:

1. Add an `Action(...)` row to the `_ACTIONS` tuple in `src/actions.py`.
   Set `balance` (add/subtract/neutral), `basis` (add/remove/zero_basis/
   transfer_in/transfer_out/split/ignore), `cash_flow` (in/out/neutral/
   ignore), and `color`.
2. Add normalize rule(s) to `normalize.RULES` so the broker-native
   action string maps to your new canonical name.

That's it.  `main.py`'s `_SUBTRACT_ACTIONS`, `history.py`'s sets,
`analytics.py`'s cash-flow sets, `basis.py`'s `BASIS_EFFECTS`,
`dashboard.py`'s `ACTION_COLORS` and `_TWR_*_ACTIONS` are all derived
from the catalog at import time.  Adding the action in one place
threads through every consumer.

## Architecture (post-refactor)

- **`src/pipeline_stages.py`** — composable pure-function stages
  shared by `main.main()` and `main._refresh_prices_only()`.  Each
  stage takes explicit inputs and returns explicit outputs.  Was
  pulled out after duplication-induced bugs (USD-non-Savings skip,
  cash-fold step) shipped because the refresh path manually
  re-implemented main()'s inline logic and silently disagreed.
  Stages: `is_dust`, `walk_balances`, `compute_position_endings`,
  `compute_cash_principal`, `build_holdings`,
  `fold_cash_into_basis_methods`, `build_basis_methods_totals`.

- **Invariant assertions** — `analytics.data_health.check_invariants`
  promotes high-severity data-health checks (snapshot rollup, lot-
  queue parity, negative basis, value≠qty×price, future-dated txn) to
  hard errors via `InvariantViolation`.  Off in production; on in
  tests via `conftest.py` setting `FIN_ASSERT_INVARIANTS=1`.  Force
  on locally to debug suspect data with the same env var.  Caught at
  the point of introduction rather than via "What's Changed" weeks
  later.

- **`src/actions.py`** — single source of truth for the canonical
  action vocabulary.  Owns the `(balance, basis, cash_flow, income,
  color)` classification for every action; downstream modules import
  the pre-baked sets (`SUBTRACT_ACTIONS`, `BASIS_EFFECTS`,
  `INCOME_ACTION_KINDS`, etc.).  The `income` field (bucket name or
  None) is the single source for income membership — `basis.py`,
  `analytics/_shared.py`, and `analytics/income_calendar.py` all derive
  their income sets from it rather than hardcoding.  The catalog is also
  serialized into the JSON export under `action_catalog`, which the
  dashboard JS consumes to derive its own membership sets (including
  `INCOME_ACTIONS` from the `income` field) — same source-of-truth on
  both sides.
- **`src/schema.py`** — `TypedDict` definitions for the core data
  structures (`Transaction`, `Holding`, `Snapshot`).  Used for IDE
  autocomplete and type-check catches of field-name drift.  Runtime
  code still uses plain dicts.
- **`src/reorgs.py`** — corp-action helper functions (CIL/MRGS/MRGC/
  LIQ/SOFF/CONV/SPR pooling, classification, description parsing).
  Pure, testable; the parsers call into these instead of inlining the
  pairing/classification logic.
- **`src/cusips.py`** — CUSIP extraction + collision diagnostic.
  Robinhood embeds CUSIPs in description fields; we capture them
  per-txn and surface ticker-rename candidates (different ticker
  strings sharing one CUSIP → likely retroactive rename) and
  ticker-reuse candidates (same ticker mapped to different CUSIPs →
  same string referring to different securities, e.g. RNA being
  Avidity then Atrium).  `main.py` prints novel collisions on each
  run so the user can decide whether to add them to
  `cache/ticker_renames.json`.
- **`src/coinbase_reconcile.py`** — Coinbase-specific *reconciliation*
  (named to disambiguate from `src/parsers/coinbase.py`, which only
  *parses* the raw CSV).
  Owns the three post-parse Coinbase quirk fixes referenced in pipeline
  steps 5 and the "USD balance tracking" carve-out: synthesizing
  Deposit rows for bank-funded Buys that lack an ACH row
  (`reconcile_external_funding`, cumulative-min approach), re-tagging
  Coinbase-regular↔Pro shuffles as intra-group Transfer In/Out
  (`reconcile_intra_transfers`), and exposing the implicit USD-wallet
  balance per-row / per-date (`usd_effect` / `usd_series`) for the
  history-snapshot bridge.  `main.py` keeps thin aliases
  (`main._reconcile_coinbase_external_funding`,
  `main._reconcile_coinbase_intra_transfers`, `main.coinbase_usd_series`)
  so older references still resolve — but the logic lives here.  Keeping
  it out of `main.py` lets `main` stay a generic orchestrator.
- **`src/parsers/`** — package directory.  `__init__.py` owns the
  broker→parser dispatch table and re-exports the public API;
  `_helpers.py` has the shared `_txn()`, `_num()`, `_date_*()`
  helpers and the ticker-rename layer; per-broker logic lives in
  `robinhood.py`, `coinbase.py`, `schwab.py`, `vanguard.py`,
  `voya.py`, `usaa.py`, `apple_savings.py`, `manual.py`.  Adding a
  new broker is a new file + one line in `__init__.py`.
- **`src/analytics/`** — package directory.  `_shared.py` has
  constants, classifiers (contribution detection, rollover bridges),
  and TWR core helpers (`_period_return`, `_chain_link_return`,
  `_value_at_date`, etc.) that multiple per-tab modules depend on.
  Per-tab / per-section modules:
  - Tab-aligned: `options.py`, `crypto.py`, `income.py`, `tax.py`,
    `positions.py`, `header.py`
  - Cross-cutting: `concentration.py`, `drawdown.py`, `daily_pnl.py`,
    `trading_heatmap.py`, `monthly_pnl.py` (Sharpe / Sortino),
    `income_calendar.py` (12mo dividend forecast),
    `rebalancing.py` (Target vs Actual sector drift),
    `monte_carlo.py` (retirement + all-accounts scenarios with
    cash-bucket model + FIRE crossings)
  - Plumbing: `changes.py` (run-over-run diff vs `cache/last_run.json`),
    `alerts.py` (severity-tagged signal aggregator)

  `__init__.py` runs `build_analytics()` which composes them and
  also wires the dual Monte Carlo (retirement vs all-accounts) +
  FIRE threshold from `retirement_meta.annual_expenses`.
- **`src/dashboard/`** — package directory.  Layout:
  - `__init__.py` — bundler.  Reads the three asset files, splices
    them together, and writes a single self-contained HTML output
    (same external behaviour as the old monolith).
  - `template.html` — page structure with `/* @@STYLES@@ */` and
    `// @@APP_JS@@` placeholder markers.
  - `styles.css` — all styling.
  - `app.js` — all JavaScript.  Edit this for any dashboard behaviour
    change; it gets full editor support (syntax highlighting, lint)
    instead of being trapped in a Python triple-quoted string.
- **`src/config.py`** — paths resolve from env vars
  `FIN_PROJECT_ROOT` / `FIN_DATA_DIR` / `FIN_CACHE_DIR` /
  `FIN_EXPORT_DIR` (falling back to repo-relative defaults).  Tests
  use this to redirect the pipeline at a tmp dir.
- **`tests/`** — pytest suite (~105 tests).  Covers:
  - Parser corp-action handling (per-broker fixture CSVs)
  - Basis walker math (FIFO/LIFO/HIFO/Average)
  - Ticker rename layer
  - Action catalog invariants
  - CUSIP detection + rename suggestions
  - Dashboard bundling
  - Reorg helper functions
  - Schema shape conformance
  - **End-to-end pipeline snapshot** — synthetic multi-broker
    portfolio, full `main()` run, asserted against known-good figures.
    This is the safety net for structural refactors.

  Run with `python -m pytest tests/`.  All tests must pass before
  merging changes.

## Module reset functions (for tests)

Stateful modules expose explicit reset helpers that clear their
in-memory caches without touching `sys.modules`:

- `src.prices.reset_caches()` — drops price / meta / splits / proxy caches
- `src.sectors.reset_cache()` — drops sector cache
- `src.parsers._helpers.reset_ticker_renames_cache()` — drops rename cache

The test conftest combines these with a `sys.modules` purge to give
each test a clean slate under its own `FIN_*_DIR` env vars.  In
production code, you never call these — caches are loaded once per
process.

## Invariants the cost-basis walker relies on

- **`BASIS_EFFECTS` lives in `src/actions.py`** and is re-exported
  through `basis.BASIS_EFFECTS` for backward compatibility.  The
  source of truth is the catalog; `basis.py` imports.
- **Symbol-aware classifier**: `basis._basis_effect` returns `"ignore"`
  for any txn where symbol is `USD` or empty, regardless of action. Cash
  events belong in `cash_summary`, not the lot queue. All other
  classification is a direct `BASIS_EFFECTS` lookup.
- **Balance parity with `main.py`**: for every non-USD symbol, the final
  lot-queue quantity per `(account_group, symbol)` must match the
  running-balance quantity computed in `main.py`. If it doesn't, either
  an action is classified wrong in `BASIS_EFFECTS` or an action was
  added to `_SUBTRACT_ACTIONS` / `_NEUTRAL_ACTIONS` in `main.py`
  without a corresponding update here.
- **Transfer pairing is pre-computed**: `_pair_transfers` walks once to
  build `{id(TIN): TOUT}` so the walker doesn't care about same-day sort
  order. Intra-group pairs (same `(account_group, symbol)` on both legs)
  are marked as `intra_group_noop` and skipped entirely — main.py adds
  then subtracts for a net-zero balance change, and we leave lots
  undisturbed.
- **Same-day cross-group transfers** resolve via eager consumption: when
  a Transfer In walks before its paired Transfer Out (same date,
  alphabetically-later source account), the TIN eagerly consumes from
  the source queue and marks the TOUT `transfer_out_eager_consumed` so
  it's a no-op when its turn comes.
- **Unpaired Transfer In uses FMV-at-transfer basis** (`qty × price`)
  when the broker recorded a spot price, else `$0`.  These are external
  deposits with no visible origin leg (most commonly crypto received from
  an off-platform wallet).  FMV is the correct basis for assets acquired
  at market and a far better estimate than `$0` (which books the entire
  proceeds as gain on a later sale).  **Caveat / known limitation**: this
  is only an estimate — if the asset was acquired *before* the transfer
  (held, then moved in), its true basis is the original purchase, which
  fin can't see.  The authoritative basis lives with the broker (e.g.
  Coinbase's "Customer provided" cost-basis column).  fin cannot
  reconstruct off-platform basis; reconcile against the broker's
  gain/loss report and, where it matters for tax, supply the real figure.
- **FIFO is the annotated default, overridable per account**. The
  annotated walk (`compute_basis_default`) uses FIFO unless an account is
  given a different lot-relief method via the `Lot Method` row in
  `metadata.csv` (Symbol = `account_group`, Note = `FIFO`/`LIFO`/`HIFO`),
  threaded through as `account_methods={account_group: method}`.  This
  exists because brokers differ (Coinbase defaults to HIFO) and the
  method changes realized gain / holding period / MAGI / Roth
  eligibility — only *which* lots' basis is realized, never balances
  (same total qty consumed, so basis↔balance parity holds).  Overrides
  are restricted to the lot-list methods (fifo/lifo/hifo); an `avg`
  override falls back to FIFO (avg uses a different lot-queue structure).
  The Lot-Method Comparison table (`compute_basis_all_methods`) still
  reports *pure* single-method totals for what-if comparison, so its
  "FIFO" column can differ from the annotated realized total once any
  account is overridden — that's expected.  LIFO / HIFO / Average walks
  don't annotate txns — they produce summary totals only.
- **Realized gain is not split by account_type** in the export. A single
  sell can (in principle) affect lots across types — though in practice
  it doesn't — and the summation is clean at the portfolio level.

## Invariants the price cache relies on

- **Canonical keys are post-normalization** (`BTC-USD`, not `BTC`;
  `ETH-USD`, not `ETH2`). On first load after this feature landed,
  `prices._migrate_legacy_keys` folded the pre-existing bare-ticker crypto
  entries into `-USD` keys and set `migrated_v1: true` in the meta file
  so it won't run again. If you change `CRYPTO_SYMBOLS` or `SYMBOL_MAP`,
  you may need to write a v2 migration.
- **yfinance's `Close` is always split-adjusted**, regardless of the
  `auto_adjust` flag. We store it as-is and apply the split correction on
  the *balance* side in `history.compute_history` via
  `split_factor_since`. This keeps the price cache symmetric (one value
  per date per symbol) and matches what yfinance naturally returns.
  **Never multiply a historical balance by a historical cache price
  without applying `split_factor_since` first** — for any asset with
  splits after the balance date (SMX, reverse-split penny stocks, pre-2020
  AAPL holdings, etc.), you'll get a wildly wrong number.
- **Splits cache (`cache/splits_cache.json`) is refreshed whenever prices
  are fetched** for a symbol. If you hand-edit the price cache to cover a
  new date range, also clear the corresponding splits entry so the
  backfill loop refetches it.
- **Weekend / holiday lookup walks backward** up to 7 days. Outside that
  window, `get_price` returns None and `compute_history` silently skips
  the position (reflected in `priced_pct`). Don't "fix" this with
  forward-fill — you'd inflate values during data gaps.
- **NaN closes are never cached or returned.** yfinance emits `NaN` for
  a row whose close hasn't posted yet (the most-recent day during /
  just after market hours, holiday rows). `float(NaN)` stays `NaN`, so
  `_fetch_range` drops those rows on write and `get_price` skips any
  lingering `NaN` on read (walking back to the prior real close). This
  matters because a single `NaN` in the cache propagates through
  `qty × price` into value / unrealized / totals everywhere — and since
  Python's `json.dump` writes a bare `NaN` literal (valid JS when
  embedded), it reaches the dashboard as `NaN` Total Return rather than
  a parse error. If you hand-edit the cache, don't introduce `NaN`.
- **Failure backoff is exponential** (1d → 2d → 4d → 8d → 16d, capped at
  30d). A successful fetch resets `failure_count` to 0 and clears
  `retry_after` / `last_error` / `tombstone`. "No data returned" only
  counts as a failure when the requested range is ≥7 days — shorter
  ranges get a free pass (weekend-run forgiveness).
- **`_classify_no_fetch` gates both the sectors module and the prices
  module** on the same rule set (empty, USD, multi-word). Keep them
  aligned if you change one.
- **Weekly deep cache refresh** (`prices.revalidate_stale_caches`)
  closes two staleness gaps the daily incremental fetch can't catch:
  (1) splits on symbols whose price coverage is already current —
  `_fetch_splits` is normally only re-run alongside a price-cache
  extension, so a split AFTER you've run the pipeline would silently
  break historical balance scaling; (2) tombstoned symbols that
  may have been re-listed.  Throttled by ``last_deep_refresh`` field
  in the meta file.  Force with ``--refresh-caches`` flag.
- **Closed-position fetch clamping** (``main.py``'s
  ``closed_position_ends`` dict, plumbed through
  ``ensure_coverage(..., symbol_end_overrides=...)``).  For symbols
  whose final balance is zero (cash mergers, written-off positions
  like LUNA-USD), we still need historical prices for the snapshots
  when held but should NOT keep asking yfinance for today's price on
  a position the user no longer holds — many such symbols are
  delisted post-merger and just produce noisy "possibly delisted"
  stderr.  Each closed symbol's fetch range is clamped to its
  last-non-zero-balance date.
- **Trivial-symbol filter** (``main.py``).  Symbols whose max
  historical balance was < 1 share AND final balance is dust-filtered
  (corp-action artifacts like ENVXW spinoff warrants the user got
  for 0.5000 shares and immediately sold) never produce material
  snapshot value — skip the price fetch entirely.

## Files that *look* like they could be shared infrastructure but aren't

- `metadata.csv` (legacy: `retirement-data.csv`) — personal config +
  metadata, not transactions.  `scanner` returns `"skip"` for both names.
- `cache/sector_cache.json` and `cache/price_cache.json` — both live. Hand-
  editable (the cache format is plain JSON). Delete freely to force a full
  refetch; the next run will rebuild them.
- `cache/price_cache_meta.json` — fetch-state sidecar for the price cache.
  If you hand-edit or delete `price_cache.json`, also delete the meta file
  so the "covered range" tracking doesn't claim coverage that doesn't
  exist anymore.
- `cache/symbol_proxy_map.json` `anchor_date` / `anchor_price` are
  auto-populated from the user's earliest txn price.  When publishing
  a fork, scrub them — they get repopulated on next run.

## Snapshot feature

`src/snapshot.py` bundles every CSV in `data/` into a single JSON file
(plain text, diffable).  CLI: `--export-snapshot PATH` /
`--import-snapshot PATH` (with `--force` to overwrite).  Used by
`tools/build_sample_snapshot.py` to generate the synthetic
`samples/portfolio.snapshot.json` that ships with the repo.

## Data is sensitive

Everything under `data/` and `exports/` is gitignored.  `cache/` is
checked in EXCEPT `cache/last_run.json` (which carries portfolio
totals).  Treat CSV contents as private financial data — don't paste
them into external services, issue bodies, or anywhere public.

**This repo is public.**  NEVER put the user's actual portfolio figures
— dollar amounts, realized gains, balances, dividend/interest totals,
account values — into **commit messages, PR descriptions, or any
git-tracked/public file**.  Commit messages are an easy gap to forget
(the gitignore only covers `data/` + `exports/`).  Describe changes
qualitatively: the mechanism, the files, the bug class.  "Coinbase
reconciles closer under HIFO" — not the specific figure.  When in doubt,
strip every `$amount` / `N.NN` from the message before committing.

## Adding sample data

`tools/build_sample_snapshot.py` builds `samples/portfolio.snapshot.json`
from a fully-fictional synthetic portfolio (Sam Sample, b. 1990-06-15,
mostly index funds + a bit of crypto).  Re-run it after parser changes
to make sure the shipped snapshot still works.  The script's
per-broker writers double as per-broker CSV format documentation.
