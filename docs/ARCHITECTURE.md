# Architecture

This document is about decisions, not features. What the project does is in
[the README](../README.md); how each stage works in detail is in
[`CLAUDE.md`](../CLAUDE.md), which is the working spec. What follows is why the
system is shaped the way it is, and what each choice cost.

---

## The problem, stated precisely

Given a directory of CSV exports from unrelated financial institutions, produce
one ledger and a set of derived figures — balances, cost basis, realized and
unrealized gain, time-weighted return, tax exposure — that a person can check
against their own statements.

Three properties of the input drive almost every decision below.

**The input is other people's file formats.** Nine institutions, nine date
formats, nine action vocabularies, nine ideas of what a negative number means.
None of them are versioned or documented, and any of them can change without
warning. The system's real job is translation, and its real failure mode is
translating silently and wrongly.

**The input is incomplete in ways it does not announce.** A broker's activity
export shows what happened *at that broker*. It does not show the other leg of
a transfer that left, the basis of an asset that arrived, or in most cases the
cash balance at all. Several stages exist only to reconstruct what one export
cannot see, and each of those reconstructions is an inference that can be
wrong.

**The output is money.** A wrong number is worse than no number, because the
user cannot tell the difference by looking. That asymmetry is the reason for
the reconciliation panel, the data-health invariants, the parity checks between
duplicated walkers, and the general preference throughout for reporting
"unpriceable" over substituting a plausible value.

Everything else is a consequence.

---

## Shape

```
data/*.csv  ─▶  scan  ─▶  parse  ─▶  normalise  ─▶  dedupe  ─▶  reconcile
                                                                    │
                            ┌───────────────────────────────────────┘
                            ▼
                        price  ─▶  walk lots  ─▶  history  ─▶  analytics
                                                                    │
                                        ┌───────────────────────────┘
                                        ▼
                    exports/transactions.json  ─▶  exports/dashboard.html
```

One process, one direction, no shared mutable state between stages beyond the
transaction list itself. `src/main.py` reads top to bottom as the spec; every
stage is a function that takes the list and returns it.

Roughly 20k lines of Python across 62 modules, 9.7k lines of dashboard
JavaScript, and one runtime dependency (`yfinance`, used only for price and
sector lookups, and only for symbols not already in the on-disk cache).

---

## Discovery and format detection

`scanner.detect_broker` identifies each file by **filename pattern first, CSV
header signature second**. That order is deliberate and slightly
counter-intuitive — headers are the more reliable signal.

The reason is that the pipeline also *renames* files into a canonical scheme
(`robinhood-1.csv`, `schwab-roth-ira.csv`), so after the first run the filename
is something the system itself chose and can trust. Header detection is the
cold-start path: it is what identifies `ExportedTransactions.csv` — a name so
generic that several institutions on the same online-banking platform emit it —
by the distinctive `Posting Date` + `Posting Status` column pair.

Detection returns one of four things: a broker key, `manual` (the
hand-maintained corrections file), `skip`, or `unknown`.

`skip` is not a failure. Some files in `data/` are *reference documents* rather
than transaction logs: `metadata.csv` (user-declared configuration and ground
truth), the Coinbase tax-centre gain/loss and raw-transaction reports, and
Robinhood's consolidated 1099. These are read by other parts of the system — the
1099 and gain/loss reports drive lot relief, `metadata.csv` drives almost every
tab — but ingesting them as transactions would double-count activity the
regular exports already carry. So they are classified out at the front door
rather than filtered downstream, and the `skip` check runs *before* the generic
filename matches, because `robinhood-1099-2024.csv` also contains the word
"robinhood".

**An unrecognised file is skipped, counted, and reported — never guessed at.**
`parse_all_files` ignores it and `main.py` prints a warning naming the files it
could not identify. Attempting a best-effort parse of an unknown format is the
one thing that would produce confidently wrong numbers, which is the failure
this project is most concerned with.

The subtler danger is the *recognised* file that parses to nothing. Every
parser drops an unparseable row with a bare `continue` — correct for one odd
row, but if a broker changes its date format that same `continue` takes the
whole file to zero, and the account simply vanishes from the portfolio with no
error anywhere. So the parse loop tracks dropped-row counts per file and warns
loudly on any recognised file that has data rows but yields zero transactions.
Silence is the bug; the warning is the fix.

---

## The common schema

Every parser returns the same ten-field dictionary:

```
date  account  symbol  action  quantity  price  fees  amount  description  source
```

Three properties of this shape do most of the work.

**Quantities, prices, fees and amounts are non-negative; direction lives in
`action`.** This is the invariant the whole pipeline rests on. Brokers disagree
wildly about sign convention — some sign the quantity, some the amount, some
neither and some both, and at least one signs the amount only for certain
transaction types. Rather than propagate nine conventions, each parser resolves
direction *at the point where it still has the broker's own context* and
encodes the answer in the action name: Robinhood's ambiguous `ACH` becomes
`ACH Deposit` or `ACH Withdrawal`; Coinbase Pro's `match` rows become `buy` or
`sell` from the raw amount's sign. Downstream, sign is a property of the action
catalog, looked up once.

The cost is that adding a directional action means splitting it *in the parser*.
Doing it downstream is impossible: `abs()` has already destroyed the evidence.

**`source` is on every row.** It is the filename the row came from, and it is
load-bearing rather than decorative — deduplication is defined in terms of it
(below), and it is the only way to answer "where did this number come from"
when a figure looks wrong.

**Fields are added by later stages, never replaced.** The basis walker
annotates `cost_basis` and `realized_gain`; the balance walker adds `balance`
and `value`; normalisation moves the original action to `raw_action` and puts
the canonical one in `action`. Keeping `raw_action` matters more than it looks:
normalisation collapses distinctions the broker made, and at least one
downstream rule (whether a transfer crosses the measurement boundary) can only
be decided from the broker's original vocabulary.

Shapes are declared as `TypedDict` in `src/schema.py` for editor and type-checker
support, but runtime code uses plain dicts. That is a deliberate half-measure:
the value of the types is catching `t["symbl"]` while writing, and full runtime
validation would buy little against CSV inputs that are already being validated
field by field.

---

## Normalisation, and one catalog

Each broker's action strings map to a canonical vocabulary (`Buy`, `Sell`,
`Dividend`, `Transfer In/Out`, `Reinvest`, `Contribution`, …) through an ordered
rule table scoped by account group. First match wins; anything unmatched passes
through title-cased and gets flagged by a data-health check rather than
silently misclassified.

The decision worth recording is that **the canonical vocabulary is defined
once**, in `src/actions.py`, and every consumer derives its sets from that
catalog. Each action declares four things: whether it adds to, subtracts from,
or leaves the balance alone; what it does to the lot queue; whether it is
external cash flow; and which income bucket it belongs to, if any.

Before that catalog existed, those classifications lived as literal sets in
`main.py`, `history.py`, `basis.py`, and the analytics modules — five places
that had to agree and periodically did not. Adding an action now threads
through every consumer from one edit.

The catalog is also serialised into the JSON export, so the dashboard
JavaScript derives its own membership sets from the same source rather than
maintaining a sixth copy. That pattern — **cross the language boundary by
exporting the verdict, not by reimplementing the rule** — recurs below.

---

## Deduplication

Broker exports overlap. Pull a fresh Robinhood CSV every quarter and the
January rows appear in four files. But identical rows *within* one export are
often legitimate: three identical same-day sells of the same size at the same
price is a real thing that happens.

So identity is a hash of the fields that describe the economic event —
`(date, account, symbol, action, quantity, price, amount)`, quantities and
prices at eight decimal places and amounts at two — and the rule is:

> for each identity, keep the **maximum number of occurrences seen in any single
> source file**.

Three copies in one file and one copy in another means the user really made
three trades and the second export overlaps. One copy in each of four files
means one trade, exported four times.

This is the honest reading of what the data can support. There is no
transaction ID to key on — most of these exports do not carry one, and the ones
that do are not stable across re-downloads — so intra-file multiplicity is the
only available evidence of genuine repetition. It is not airtight: a user who
splits one broker's history into two files, each containing two of three
identical trades, will lose one. That trade-off is chosen knowingly, because
the alternative failure — silently double-counting a quarter of overlapping
history — is both more likely and much harder to notice.

Rounding `amount` to two decimals and quantity to eight is a deliberate
loosening: brokers re-export the same row with different precision, and an
exact-float identity would fail to match rows that are obviously the same
event.

---

## Reconciliation: reconstructing the legs nobody exports

Several stages exist because a single broker's export cannot see the whole of a
transaction. They are all the same shape of problem, and worth separating from
the mechanical stages above, because each is an **inference** rather than a
translation.

**Custodial transfers.** Move a Roth IRA from one custodian to another and the
receiving broker reports an inbound "Security Transfer"; the sending broker's
activity export often reports nothing at all. Left alone, the shares appear from
nowhere at the destination and never leave the source, so the source account's
balance never returns to zero and the portfolio total double-counts the
position for the rest of time.

The reconstruction (`main._reconcile_usaa_to_schwab_transfer`) matches each
inbound leg against the source account's position in that symbol immediately
before that date, and synthesises the outbound leg for the smaller of the two.
Anything left over — the sub-share residuals that come from the two brokers
printing different precision — is emitted as a separate small `Transfer
Reconcile` row rather than folded into the transfer, so the plug is visible in
the ledger instead of hidden inside a number that looks real.

The general principle: **when the system invents a row, the row says so.**
Synthesised transactions carry their own action names and a `source` of
`auto-reconcile-transfer`. Nothing is quietly adjusted.

**Rollovers in flight.** A distribution from one retirement account and the
matching deposit at another are separated by days or weeks of wire time, during
which the money exists in neither. Without handling, every custodial move reads
as a total loss followed by an equivalent windfall — which wrecks time-weighted
return, drawdown, and every risk statistic derived from them. `analytics/
rollover_bridges.py` pairs distributions to transfers-in within a 90-day, 5%
tolerance window and hands the resulting bridges to every consumer that walks
the value series.

**Intra-institution shuffles.** Coinbase's regular and Pro wallets export as
separate systems, so moving funds between them looks like a genuine deposit on
one side. Re-tagged as an intra-group transfer, the pair becomes a no-op. Left
alone, every shuffle would inflate "external money in" — and therefore
understate return by exactly the amount moved.

**Reconstructed cash.** Most broker activity exports do not report a cash
balance, so USD is deliberately *not* tracked for taxable and retirement
accounts: an incomplete cash balance is worse than none. But for a broker whose
export is provably complete, *not* reconstructing cash is itself corrupting — a
profitable sale converts tracked position value into untracked cash, which the
return calculation reads as a market loss of roughly the size of the gain. So
`cash_bridge.py` maintains a reconstructed balance for a small allow-list of
groups, and adding to that list is treated as a claim requiring validation
(walk the reconstructed series for persistent negatives; check the total
against a broker statement) rather than a configuration change. Getting it
wrong invents portfolio value, which is worse than the understatement it fixes.

---

## Pricing

Historical prices are fetched from Yahoo Finance and cached on disk, one JSON
shard per symbol (`cache/prices/{SYMBOL}.json`), with a metadata sidecar
tracking per-symbol coverage and fetch state. The cache is committed to the
repository: ~237 shards, ~11 MB, and it means a fresh clone can render the full
history offline.

Four decisions here are non-obvious.

**A date is covered only once its bar can no longer change.** The naive version
of a price cache records "I have data through today" and never revisits. Run the
pipeline at 11am and you freeze an intraday quote in as that day's close,
permanently, because only forward gaps ever get fetched. The cache therefore
tracks `settled_through` separately from `covered_end`, using late settle
horizons (16:20 ET for equities, 18:00 ET for a mutual-fund NAV strike, the
whole UTC day for crypto). Being wrong in the "not settled yet" direction costs
one redundant fetch that returns the same number; being wrong the other way
leaves a stale figure the user trusts. The asymmetry sets the direction of every
judgement call in this module.

**Split adjustment is applied to the balance, not the price.** Yahoo's `Close`
is always split-adjusted, whatever flags you pass. Rather than fight that, the
cache stores exactly what the provider returns and `history.py` scales the
*as-of-date share count* forward by the product of splits after that date. The
cache stays symmetric — one value per symbol per date — and `adjusted_qty ×
adjusted_price` gives the actual dollar value on that date. The cost is a rule
you must not forget: never multiply a historical balance by a historical cached
price without applying `split_factor_since` first.

**Failure is a first-class state.** Delisted tickers, renamed tickers, and
multi-word mutual-fund display names that are not symbols at all will never
resolve. Each symbol carries a failure count and an exponential backoff, and
after five consecutive failures a tombstone stops the retries entirely. Without
this, every run pays for the same dead lookups forever.

**Missing is reported, not filled.** A weekend or holiday lookup walks backward
up to seven days; past that, `get_price` returns `None`, the position is skipped
from that snapshot, and the snapshot's `priced_pct` records the coverage gap.
Forward-filling would produce a smooth, plausible, wrong series during exactly
the outages the user most needs to know about.

The store is daily. Finer resolution was considered and rejected: every consumer
in the system samples daily or coarser, minute bars cannot be backfilled beyond
a week, and one year of minute data for this symbol set would be roughly fifty
times the entire current cache. Freshness questions are about the *provenance*
of one day's bar, which `settled_through` and `last_fetch` answer directly, and
which the dashboard surfaces as an explicit "prices as of" stamp showing the
**oldest** fetch across held positions — a staleness floor, not a flattering
maximum.

---

## Cost basis and lot tracking

A walker moves through transactions in order, maintaining a queue of open lots
per `(account_group, symbol)`, and annotates each transaction with its basis
effect, resulting cost basis, and realized gain.

**FIFO is the default because it is the IRS default**, which is what a broker
applies unless the customer elected otherwise, which makes it the method most
likely to match the 1099 the user will actually be reconciling against. It is
not a claim that FIFO is optimal.

**Other methods are already supported**, and the interesting part is what it
took. LIFO and HIFO are, structurally, just a different consumption order over
the same lot queue, so they cost a sort key. Average cost is not: its "lots" are
not lots, and it needs a different state shape. So the walker carries a
lot-list path and an average path, and the four-method comparison shown in the
dashboard runs four independent walks.

Method is configurable **per account**, through a `Lot Method` row in
`metadata.csv`, because brokers differ — several crypto exchanges default to
HIFO — and matching the broker matters more than internal consistency. This
changes which lots' basis is relieved, and therefore realized gain and holding
period, but never balances: the same total quantity is consumed either way, so
the basis-to-balance parity invariant holds regardless.

Above the method sits something stronger. When a broker's own per-lot report is
present — Coinbase's gain/loss report, Robinhood's consolidated 1099 — the
walker consumes *the specific lots the broker says it relieved*, and the
configured method only orders the remainder. This is a pure consume-*order*
strategy: the basis booked is always the actual pool lot's basis, so every
parity invariant holds unchanged, but the short-versus-long-term split of a
sale now matches the form the IRS already has.

The genuinely hard case is basis the exports cannot contain: an asset bought
elsewhere and transferred in. An unpaired transfer-in gets fair-market-value
basis at the transfer date, which is right if the asset was acquired at market
that day and wrong — usually generously so — if it was held first. That is a
data limit, not a bug, and the response is a `Cost Basis` metadata row where
the user can supply the broker's customer-provided figure, plus a reconciliation
row that surfaces the disagreement instead of hiding it.

### The duplicated-walker problem

This is the least elegant thing in the codebase and the most instructive.

The dashboard needs lot state at *every historical snapshot date*, not just at
the end. Threading that through the annotating walker would have meant either
storing the full lot queue at every snapshot date or restructuring the walker
around a checkpointing interface. Instead `history.py` keeps its own inline lot
walker.

Two walkers implementing the same rules is exactly the duplication that decays,
and it did — twice. Basis-rule changes landed in `basis.py` without the matching
change in `history.py`, silently desynchronising the Overview's cost-basis line
from the Holdings table.

The response was not to merge them, which the different state requirements make
awkward, but to make drift *detectable and structurally hard*:

- Every rule the two share was extracted to module level in `basis.py` and is
  **called** by both, rather than being implemented twice.
- A data-health check compares the two walkers numerically and fails the build
  on disagreement.
- A separate test compares them **structurally** — both must dispatch on the
  same set of basis effects, and the extracted shared rules must stay shared.
  This one matters because the numeric check only compares the states some
  fixture actually reaches; a branch present in one walker and missing from the
  other is invisible to it until data arrives at that branch.

The general lesson, which shows up throughout: when duplication cannot be
removed, make the *divergence* fail a test.

---

## Analytics: compute once

Every derived figure the dashboard displays is computed in Python, in
`src/analytics/`, and embedded in the JSON export. The JavaScript reads; it does
not calculate.

This rule exists because the alternative was tried. Figures recomputed in the
browser drifted from their Python originals in ways that were individually tiny
and collectively corrosive: a different rounding point, a slightly different
window boundary, a cash-flow classification missing one carve-out. The user sees
two numbers for the same quantity on two tabs and has no way to know which is
right — and neither, in practice, does the developer.

Two refinements were forced by experience.

**A published figure comes from the walker's own state, never from re-summing
per-transaction annotations.** Those annotations are rounded to cents for
display, so summing thousands of them accumulates a cent or two per position.
One code path did exactly that and the two pipeline paths disagreed on holdings
basis for identical input.

**Computing a figure once does not make two figures comparable.** A return and
a benchmark placed side by side are only meaningful over the same window, and
that pairing is invented at the *render site*, out of inputs the compute layer
never sees together. The system shipped an S&P 500 fund appearing to trail its
own index by hundreds of basis points a year, from exactly this: a correct
return measured over the account's own lifetime, printed beside a correct
benchmark measured over the chart's window. Both figures were computed once and
correctly.

The fix was structural — each precomputed filter now ships its benchmark
alongside its return, measured over the same window, and any consumer showing
the pair must read both from the same object — but the durable lesson is that
the compute-once rule has a boundary, and the render site is on the other side
of it. That is what `tests/test_dashboard_consistency.py` exists for: it runs
the real dashboard bundle under a DOM stub, reads the figures each tab actually
*displays*, and asserts relations between them across the whole (filter ×
window) matrix. It is the only test that reaches the render site, and it exists
because the two worst bugs this project has had were both invisible to any
harness that stops at the compute layer.

---

## Why there is no database

The dataset is a few thousand transactions and a few hundred thousand cached
prices. It fits in memory with room to spare, it is derived entirely from files
on disk, and it is rebuilt from scratch on every run.

**What that buys.** The pipeline has no schema to migrate, so changing the
shape of a transaction is an edit, not a migration plus a backfill. There is no
state that can be stale or subtly wrong relative to the inputs, which for a
system whose whole purpose is reconstructing figures from source documents is
worth a great deal: every run is reproducible from `data/` alone, and a bug fix
retroactively corrects all history rather than only new rows. The test suite can
run the entire pipeline end to end in a temp directory in under a second. And
installation is `pip install yfinance`.

**What it costs.** Everything is recomputed every run, so runtime scales with
total history rather than with what changed — a full run is seconds today and
would not be at a hundred times the size. There is no query interface: answering
a question the dashboard does not already answer means writing Python, not SQL.
Nothing is transactional; a crash mid-run leaves partial output (mitigated by
writing the dashboard to a temp file and atomically renaming, which is a
smaller fix than it sounds — a browser refresh during a run used to render a
blank page).

The line where this stops being right is when full recomputation gets too slow
to run casually, or when the ledger becomes something to *query* rather than to
render. Neither is close.

The caches are the interesting exception. Price, sector, split and dividend data
are external, expensive to fetch, and rate-limited, so they *are* persisted —
as plain, hand-editable JSON, one shard per symbol, committed to the repo.
Plain JSON was chosen over anything faster specifically so that a suspect price
can be inspected, corrected, or deleted with a text editor, and so that a
change to the cache shows up as a readable diff.

---

## Why the dashboard is one HTML file with no framework

The renderer concatenates a template, a stylesheet, thirteen JavaScript modules
and the full JSON payload into a single self-contained HTML file — around 1 MB
for the sample portfolio, with ten tabs, a dozen charts and every transaction
inlined. No browser runtime dependencies or CDN. Python and Node dependency
manifests lock the generation and validation tools, not assets needed to view
the resulting file.

The trade-off is portable output in exchange for explicitly managed frontend
state and rendering. Behavioral and parity tests protect those boundaries.

**The artifact has to survive being moved.** The output is a financial record.
Its realistic lifecycle includes being emailed to an accountant, dropped in a
cloud drive, opened from a USB stick years later, and archived alongside a
year's tax documents. A single file with no external references works in all of
those; anything with a `dist/` directory, a relative asset path, or a CDN
reference works in none of them reliably. The same property makes the privacy
story simple to state and simple to verify: the page makes **zero network
requests at view time**, which anyone can confirm by opening the network tab.

**Frontend trade-offs.** A framework's central offering
is efficiently reconciling a mutable view with changing state. This view has no
changing state. The data is a frozen snapshot, baked in at generation time —
tabs render once, lazily, on first activation, and re-render wholesale on a
filter change. The rendering budget is dominated by string concatenation over a
few thousand rows, which is fast. Adopting React here would import a
reconciliation engine to solve a problem that does not exist, and pay for it in
a build step, a dependency tree, and a `node_modules` in the release path of a
program whose only runtime dependency today is one Python package.

**The dependency tree is an attack surface with a permanent maintenance bill.**
This project handles the user's complete financial position. A transitive
dependency graph in the thing that renders it is a standing supply-chain risk,
and a build step is a thing that breaks in two years when you return to fix a
parser.

**What it costs, honestly.** No component model, so shared UI is shared by
convention. No type checking across 9.7k lines of JavaScript. No hot reload —
the loop is regenerate, refresh. Concatenation is filename-ordered, so a
top-level definition must appear in a file that sorts before its use, which is
a real constraint that has caused real bugs (the numeric prefixes leave gaps so
a module can be slotted between two others). And the file grows linearly with
the ledger; at a hundred times the data, inlining the payload stops being
reasonable and the first thing to go would be shipping every transaction to the
browser.

The mitigations are conventional: the JavaScript is split into one module per
tab (`app/00-core.js` … `app/95-transactions.js`) rather than the single
7,500-line file it started as; each tab registers a render function and is
rendered lazily; and the parts that would most benefit from types — every
derived figure — are computed in Python instead, where they are typed and
tested.

The version of this decision I would defend hardest is not "no framework" but
**no build step**. Most of the benefit above comes from the output being a
single file that runs anywhere, and from the source being editable with nothing
installed.

---

## Privacy model

The threat being designed against is mundane and by far the most likely: the
author of a *public* repository accidentally committing their own financial
data. Not an attacker — a mistake.

Three layers, deliberately overlapping.

**Nothing sensitive is tracked.** All of `data/` and `exports/` are gitignored
at any extension, so a stray `.bak` or `.xlsx` dropped next to the CSVs cannot
leak. Generated price shards, the legacy price cache, price coverage metadata,
and `cache/last_run.json` stay local. Shared cache/config files remain tracked
and subject to privacy review. Price files and their coverage metadata must
travel together when privately migrating an installation.

**A mechanical backstop.** A tracked pre-commit hook blocks any commit whose
staged additions contain a token from a gitignored local denylist of real
emails, account IDs, and distinctive figures. This exists because the recurring
leak is not a raw CSV — those are gitignored — but a *real figure hardcoded
into a test fixture*, which looks exactly like ordinary code in review. The
denylist catches what a human reviewer will not.

**Tests make no network calls, and cannot touch real directories.** `yfinance`
is stubbed, and `conftest.py` pins every `FIN_*_DIR` environment variable at a
per-session temp directory, so no test can read `data/` or write `cache/` or
`exports/`. Both halves matter and for different reasons. No network means the
suite is deterministic, runs offline, and — the actual point — cannot transmit
anything anywhere. No real directories means the suite cannot corrupt the user's
own data, which is not hypothetical: tests once leaked writes to the real
run-state cache, zeroing it, which made the next real run report the entire
portfolio as new.

The published demo is built by CI from `samples/portfolio.snapshot.json`, a
fully synthetic portfolio, and the workflow *asserts* `data/` is empty before
building rather than assuming it. The demo banner is injected at publish time
rather than in the renderer, so a locally generated dashboard — which shows the
user's real money — is never labelled a demo.

---

## Known limitations

**Basis that predates the export window is not recoverable.** If an asset was
acquired outside the broker or before its export starts, the true basis is not
in any input file. The system estimates fair market value at the transfer,
flags it, and provides a metadata row to override it with the broker's figure —
but it cannot derive it.

**Broker lot relief is only known where a report exists.** Coinbase and
Robinhood publish per-lot reports and those are consumed directly. For other
brokers, the configured method is a model of what the broker did, not a record
of it.

**Some disagreements are structural and permanent.** One exchange's tax engine
books an internal wallet transfer as a fresh acquisition, so its inventory
contains lots the physical position cannot support, and it relieves them in a
later year. No consume-order strategy can reproduce that, because it is not a
lot-selection difference — it is a different set of lots. These cases are
documented as expected deltas with a declared tolerance, so they show as
*explained* on the reconciliation panel and re-flag if reality drifts from the
explanation.

**Open options are marked at last trade, floored at intrinsic value.** Yahoo
does not price individual contracts, so a contract's mark is its last traded
premium unless the underlying's price implies a higher intrinsic value. Time
value is not modelled; the floor only ever raises the price. A deep
in-the-money contract tracks its underlying instead of going flat, which is the
important case, but the mark is an approximation.

**Only one account per credit-union export.** That export carries no account
identifier and every account at the institution exports in an identical shape,
so a second one would be parsed into the first one's balances. Supporting more
needs a per-file account hint, which does not exist yet.

**Cash is not tracked for most account types, on purpose.** Sell proceeds are
underreported in most activity exports, so a reconstructed cash balance would be
confidently wrong. The reconstructed-cash allow-list is small and each entry is
a validated claim about that broker's data completeness.

**Weekend crypto marks lag by a day.** The fetch window clamps to the last
trading day for every asset class, so Saturday's crypto bar is not fetched until
the next weekday run. Nothing is lost permanently — the gap is filled — but the
weekend displays Friday's mark.

---

## At ten times the data

What would actually break first, in order.

**Full recomputation.** Everything downstream of parsing runs over all history
every time. Ten times the ledger is ten times the walk, plus a snapshot series
that grows with both time and position count. The fix is not a database, it is
**incrementalisation**: lot state and the value series are both append-only in
practice, so checkpoint the walker at a date, persist the checkpoint, and replay
only from the earliest transaction that changed. That requires the walkers to be
resumable, which is a real refactor — and the current duplicated-walker
situation would have to be resolved first, because two walkers with two
checkpoint formats is not a thing to build.

**Inlining the payload.** A single self-contained file stops being reasonable
somewhere in the tens of megabytes. The move I would make is to keep the single
file but stop shipping the *raw ledger*: the dashboard already reads
precomputed analytics for almost everything, and the transaction table is the
one view that genuinely needs row-level data. Paginating or summarising that one
table preserves everything the single-file decision is actually buying.

**JSON price shards.** ~237 files is comfortable; ten times that with denser
history is where per-symbol JSON parse time starts to show. This is the one
place I would reach for a real store — SQLite or Parquet — because prices are
the only genuinely tabular, append-mostly, query-shaped data in the system. I
would keep a JSON export path, though: the hand-editability of that cache has
been worth more than its performance has cost.

**Detection by header scan.** Reading the first ten lines of every file is fine
at tens of files and wasteful at thousands. A manifest keyed by content hash,
with the scan as the cold path, is the obvious fix.

**What would *not* change.** The common schema, the single action catalog, the
compute-once rule, and the parity checks between duplicated logic all get *more*
valuable with scale, not less — they are the things that make the system
debuggable, and debugging is what gets harder when there is more data. The
no-build-step decision would survive too. The one thing I would revisit
regardless of scale is the two lot walkers: the tests that pin them together are
good, but they are compensating for a structure that should not need them.

## Validated publication and reproducible demonstration

The public artifact is built by `tools/build_demo.py` using independently generated
fictional transactions and illustrative price curves at a fixed sample date. All
input/cache/output paths are temporary and network access is disabled. Embedded
provenance and the artifact manifest identify source and output hashes. CI tests
the final bannered artifact before Pages can deploy it.

The ordinary pipeline requires explicit account classifications and valid, finite
numeric input before output publication. JSON and HTML are prepared together; a
failed parse, invariant, or render does not replace the last usable output. See
[Usage](USAGE.md) and [Privacy](PRIVACY.md) for operational details.

The snapshot clock keeps archived dashboards anchored to their data. Historical
holdings cut off realized activity at the selected date, and monthly risk ratios
aggregate calendar-month observations before annualizing. These contracts are
covered by behavioral parity tests rather than source-text assertions alone.
