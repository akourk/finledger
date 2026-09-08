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
| Test cleanup | The session isolation directory was never released. | Retain a `TemporaryDirectory` owner for process lifetime so normal interpreter shutdown cleans up the session's own files. |

The rename workflow assumes a single writer, as does the rest of the pipeline.
It does not claim atomic multi-file transactions or protection against concurrent
processes mutating the same input directory. Recovery instructions are in
[Usage](USAGE.md#input-validation-and-recovery).

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

## Further work, in priority order

1. **Cache persistence and crash recovery.** `prices.save_caches` and
   `sectors.save_cache` write JSON in place. Investigate staged replacement of
   shards and related sidecars, preserving dirty state and migration backups on
   failure. Cover interruption, disk errors, and split/coverage consistency
   before changing this subsystem; a per-file atomic write alone does not make
   the entire cache update transactional.
2. **Clarify drawdown semantics.** Some drawdown views use raw portfolio-value
   declines, so external withdrawals contribute to the reported drawdown.
   Decide whether each view describes balance declines or investment
   performance. Add a withdrawal-only fixture before changing labels or math.
3. **Market calendars by asset class.** The existing weekend crypto limitation
   remains: coverage scheduling clamps through the weekday calendar. Any fix
   must respect crypto settlement, equity close timing, and provisional marks;
   test those independently of live quotes.
4. **Profile repeated walks before broader optimization.** Transfer pairing and
   historical lot processing are plausible scaling costs, but this review did
   not establish an end-to-end bottleneck. Measure larger fictional ledgers
   before introducing indexes, checkpoints, or persistent incremental state.
5. **Reduce duplicated financial state transitions gradually.** The lot walkers
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

Artifact and worktree privacy scans both passed after the proxy-configuration
sanitization and screenshot review. A human visually approved all four refreshed
screenshots, and their provenance records now mark them reviewed. Documentation
checks validate skill metadata and local links and anchors; the bundled skill
validator could not run without PyYAML, so dependency-free structural checks
were used instead.

An initial demo failure was traced to a working-copy sample using CRLF despite
the repository's LF attributes. Its parsed contents matched the generator;
restoring LF restored exact agreement with the committed fixture. Do not relax
byte-level provenance checks to hide this class of checkout problem.

The visual review follows [Privacy](PRIVACY.md) and remains separate from
automated scanning. Passing scans and fictional-input tests do not prove that
arbitrary financial content is free of private data. This review did not audit
all historical commits or verify current tax law.
