# Engineering review

This review examined ingestion and failure recovery, financial valuation and
projection inputs, Python/JavaScript agreement, dashboard initialization,
development guidance, and publication checks. Reproducers and measurements use
independently constructed fictional data. Personal inputs were not used for
code testing or measurements.

## Implemented improvements

| Boundary | Previous behavior | Change and regression coverage |
| --- | --- | --- |
| Unit-price precision | Holdings rounded unit prices before lots and P&L consumed them; small prices could become zero. | Preserve calculation precision through holdings, history, and lot payloads. Round monetary display totals separately. `test_unit_price_precision.py` exercises downstream lots and window P&L. |
| Position materiality | Dust thresholds mixed raw shares with split-adjusted prices and omitted option multipliers. | Apply dollar thresholds to the resolved mark value, retaining raw quantity for the share-count guard. `test_valuation_kernel.py` covers splits and options. |
| Projection assumptions | A fixed number of snapshots was treated as months, although normal history is semimonthly; age used the wall clock. | Annualize contributions by observed elapsed days and use the shared snapshot clock for age. `test_monte_carlo_inputs.py` covers multiple cadences, shorter histories, leap days, and birthday boundaries. |
| Browser date boundaries | UTC date parsing mixed with local date mutation, shifting some cutoffs across daylight-saving changes. | Use calendar-date helpers, clamp month/year shifts at month end, and calculate calendar age. Regression cases run in multiple timezones. |
| Retirement defaults | Some browser totals added contribution reversals, counted transfers, included future rows, or omitted custom retirement groups. | Share signed, bounded trailing contributions and metadata-aware account classification; compare classification with Python in `test_frontend_review_regressions.py`. |
| Transaction startup | Up to thousands of hidden rows were formatted on every page load; search text was rebuilt each keystroke. | Render the table on its first visit and cache immutable row search text. Tests cover initial deep links, repeated visits, and hidden-column search. |
| CSV renaming | Fixed temporary filenames could collide; an I/O failure could leave inputs renamed or unavailable to the next import. | Use a unique staging directory, recover both phases, preserve every source on recovery failure, and block subsequent ingestion until recovery is resolved. `test_scanner.py` injects failures during staging, final renames, and rollback. |
| Cache persistence | In-place writes could leave truncated JSON or mismatched prices, splits, and coverage; deletion errors discarded pending work. | Serialize the entire dirty price set before mutation, stage replacements and original backups, retire old files last, and retain dirty state on failure. A recovery manifest blocks cold loads after an interrupted save or failed rollback. `test_cache_persistence.py` and `test_io_safe.py` cover serialization, disk errors, migration, rollback, and abrupt process exit. Sector saves use the same helper separately. |
| Drawdown meaning and dates | Raw balance declines were presented as investment risk, and a Calmar card divided investment return by cash-flow-sensitive balance drawdown. Python trailing windows also used the wall clock. | Label Balance Drawdown, disclose cash movements, sampling scope, and the lifetime size filter; remove the incompatible Calmar ratio. Anchor trailing windows to the latest data date with matching Python/JS durations. `test_drawdown_semantics.py` covers withdrawals, deposits, losses, bridges, and window agreement. |
| Weekend crypto coverage | Ordinary and forced requests clamped crypto dates to Friday, skipping weekend marks. | Share the asset calendar with settlement classification; retain weekend dates for crypto while preserving equity/fund cutoffs, provisional marks, and closed-position limits. `test_price_calendars.py` covers UTC settlement, mixed batches, proxy targets, and forced refreshes. |
| Transfer matching cost | Each inbound transfer scanned all outbound rows and repeatedly parsed dates, across every basis/history walk. | Index candidates by symbol and date while preserving greedy order, quantity tolerances, ties, and validation behavior. `test_transfer_pairing_index.py` compares with the exhaustive matcher across boundary cases and seeded fictional ledgers. |
| Mobile Risk view | The monthly returns table widened the page beyond the viewport; the smoke check visited only Returns. | Contain the table in a named, keyboard-scrollable region. Browser checks now open Risk on mobile, assert viewport containment, and exercise horizontal keyboard scrolling. |
| Account transfer attribution | Portfolio-neutral in-kind transfers could appear as account losses or gains. | Price verified cross-group pairs once and export scope-aware flow supplements for Python/JS returns, XIRR, annual tables, account contribution/P&L cards, and filtered benchmarks. Preserve whole-portfolio external contributions and report missing transfer valuations. |
| Delayed transfer valuation | Assets disappeared from portfolio measurements between outbound and inbound posting dates. | Retain a separately marked in-transit position with carried basis, included only in scopes containing both custodians. Reconcile history, daily totals, returns, holdings, allocation, and composition without changing posted account balances. Tests cover whole portfolios in transit, missing daily marks, splits/options, scope boundaries, and precision before rounding. |
| Test cleanup | The session isolation directory was never released. | Retain a `TemporaryDirectory` owner for process lifetime so normal interpreter shutdown cleans up the session's own files. |

The rename workflow assumes a single writer, as does the rest of the pipeline.
It does not claim atomic multi-file transactions or protection against concurrent
processes mutating the same input directory. Recovery instructions are in
[Usage](USAGE.md#input-validation-and-recovery).

Cache saves likewise assume one writer. They preserve original bytes and a
local recovery manifest before replacing live files; a failed rollback requires
manual restoration before another process can load caches. This is not an
atomic multi-file filesystem transaction or a power-loss durability guarantee.
Recovery files remain private and are blocked by the publication guard even
when force-added. See [cache recovery](USAGE.md#cache-save-recovery).

## Development efficiency

- Reduced `CLAUDE.md` from 2,013 to 81 lines and retained its old section links.
  Detailed subsystem rules live in [INVARIANTS.md](INVARIANTS.md) and are read
  only when relevant. [AGENTS.md](../AGENTS.md) supplies shared instructions.
- Reduced the six project skills from 936 to 649 lines while correcting stale
  directions about action-derived income, strict CSV readers, mandatory account
  classifications, rounded basis reconstruction, dashboard module paths, and
  interactive calculations that intentionally mirror Python.
- Added a [source/test/skill map](../CONTRIBUTING.md#find-the-right-surface) and
  distinguished documentation checks from application and browser validation.
- Reused the existing deterministic demo, parity tests, and privacy hooks;
  introduced no application dependencies or financial data migrations.
- Removed pre-existing transaction-derived anchor fields from tracked proxy
  configuration after preserving a private local backup. This sanitizes the
  current tracked configuration; no historical commits were altered, and no
  whole-history audit was performed.

For a synthetic 5,000-row ledger, the actual old and new JavaScript bundles
were measured under the same Node DOM probe. Startup constructed 5,000
transaction rows before the change and zero afterward. Three initialization
runs were 283/291/287 ms before and 23/23/23 ms afterward. This is a local
JavaScript initialization measurement with a DOM stub, **not** a claim about
end-to-end browser loading speed. The first transaction visit still does the
necessary rendering work.

Profiling a transfer-heavy fictional ledger established a second bottleneck:
transfer pairing consumed about 96% of the profiled basis/history workload.
The symbol/date index removes repeated searches outside the eligible window.
Measured wall-clock times for five basis walks plus 144 history snapshots:

| Rows | Symbols | Exhaustive matching | Indexed matching |
| --- | --- | --- | --- |
| 1,000 | 1 | 0.782 s | 0.062 s |
| 2,000 | 1 | 4.732 s | 0.211 s |
| 4,000 | 1 | 16.762 s | 0.382 s |
| 4,000 | 8 | 3.364 s | 0.241 s |

These are local single-run measurements, excluding input/cache preparation and
network activity, not end-to-end pipeline speed claims. Every before/after
comparison produced identical transaction annotations, holdings, all four lot
methods, and history snapshots. The reusable
[transfer benchmark](../tools/benchmark_transfers.py) runs the same isolated
fictional workload, reports median timings, and emits a semantic digest for
comparison across future changes. Dense transfers within one date window still
require candidate comparisons; this is not a claim of universal linear scaling.

## Further work, in priority order

1. **Transfer coverage and share-unit reconciliation.** Verified paired in-kind
   transfers now preserve portfolio exposure between posting dates and cross
   account return boundaries correctly. USD, unmatched movements, and share-unit
   changes during transit need separate reconciliation evidence before extending coverage;
   do not reinterpret every unmatched transfer as external or asset units as
   dollar amounts. Preserve the selected scope and valuation dates across
   snapshots, charts, TWR, and XIRR when extending coverage.
2. **Single-category allocation charts.** The fictional transit visual check
   exposed an existing donut-rendering edge case: a single category fills the
   full circle, so the SVG arc's start and end coincide and the ring disappears.
   Use a complete-circle path and cover the one-category view in browser checks.
3. **Reduce duplicated financial state transitions gradually.** The lot walkers
   already share substantial helpers and strong parity coverage. Extract one
   verified transition at a time. A framework rewrite or database migration has
   no demonstrated benefit for the current workload.

## Validation and limits

Validation completed in the locked Python 3.12 environment: **1,512 tests
passed**, including JavaScript parity checks. One filesystem-symlink test
skipped because Windows did not grant symlink privileges. Chrome smoke checks
passed across all **10 tabs** of the deterministic fictional demo, and axe
accessibility checks passed across **12 states** with no serious or critical
violations.

The cache-recovery follow-up passed **1,568 tests**, with the same Windows
symlink skip. Its isolated demo HTML and provenance manifest exactly match the
approved artifact, so the existing browser checks and screenshot review still
describe the generated output. New tests include real subprocess exits after
filesystem mutations and interruptions around recovery-marker removal.

Artifact and worktree privacy scans both passed after the proxy-configuration
sanitization and screenshot review. A human visually approved all four refreshed
screenshots, and their provenance records now mark them reviewed. Documentation
checks validate skill metadata and local links and anchors; the bundled skill
validator could not run without PyYAML, so dependency-free structural checks
were used instead.

The drawdown/calendar/performance follow-up passed **1,667 tests**, with the
same Windows symlink skip. Chrome smoke checks passed across all **10 tabs**,
and axe checks passed across **12 states**. The updated Overview and Performance
screenshots were visually approved; Holdings and Tax remained byte-for-byte
identical to their previously approved images.

The account-transfer follow-up passed **1,722 tests**, with the same Windows
symlink skip, plus a focused regression for account cards without history added
after the full run. The new fictional cases exercise account boundaries,
TWR/XIRR, market movement, split/option marks, missing prices, external and
income exclusions, same-day price ordering, and export/reload reconstruction.
Chrome smoke checks passed across **10 tabs** and axe across **12 states**.
An additional Chrome check clicked **12 account filters** and verified that
full-range custom returns agree with the Python summaries. All **62 local
documentation links and anchors** checked in the updated guidance resolve.
All four regenerated screenshots are byte-for-byte identical to the approved
images; their registry now references the refreshed artifact.

The transit-valuation follow-up passed **1,771 tests**, with the same Windows
symlink skip. New cases cover missing daily marks between snapshots, matching
share units across arrival-date splits, full portfolios in transit, unchanged
posted balances and final basis, and Python/JS rounding agreement. Chrome checks
passed across **10 tabs**, **12 account filters**, and **12 accessibility states**.
A separate isolated fictional transit fixture passed desktop/mobile layout
checks and visual inspection. All four public screenshots exactly match their
previously approved bytes; their registry references the rebuilt demo.
Development guidance checks resolved **100 local links and anchors** and checked
all project skill frontmatter. The existing isolated transfer benchmark's median
times for five basis walks plus 144 snapshots were **0.331 s** for one symbol and
**0.252 s** for eight symbols, each with 4,000 fictional rows and three repeats.
These local measurements include the new transit handling and show no material
slowdown against the earlier workload; they are not full-pipeline timings.

An initial demo failure was traced to a working-copy sample using CRLF despite
the repository's LF attributes. Its parsed contents matched the generator;
restoring LF restored exact agreement with the committed fixture. Do not relax
byte-level provenance checks to hide this class of checkout problem.

The visual review follows [Privacy](PRIVACY.md) and remains separate from
automated scanning. Passing scans and fictional-input tests do not prove that
arbitrary financial content is free of private data. This review did not audit
all historical commits or verify current tax law.
