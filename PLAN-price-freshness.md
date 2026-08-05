# Plan: price freshness & settle-awareness

Status: **not started**. Written 2026-08-04 for a later session.

Read this top-to-bottom before touching code. It carries design decisions
that were already argued out — don't re-litigate them, just check the
"Rejected" section if something looks under-thought.

---

## The problem in one paragraph

`prices._missing_ranges` decides whether to fetch by comparing the
requested `end` against the symbol's stored `covered_end`
(`if end > ce`). The moment any run fetches today, `covered_end == today`
and **every later full run that day is a no-op** — prices freeze at
whatever the first run of the day captured. `--refresh-prices` is
currently the only escape, which makes a speed flag load-bearing for
correctness. Three related defects fall out of the same spot.

## The four defects

| # | Defect | Evidence |
|---|---|---|
| 1 | **Today never refetched by the full path.** First fetch of the day wins; later runs are no-ops. | `python -m src.main` twice → identical values; `--refresh-prices` moves them |
| 2 | **Crypto UTC boundary.** Crypto bars are UTC-dated, so an evening local run writes a bar for *tomorrow* and sets `covered_end` to tomorrow. Next day's full run sees "up to date" and **skips crypto for a whole local day.** | Observed at 22:42 local / 05:42 UTC: `ETH-USD` had a `2026-08-05` bar and `covered_end: 2026-08-05` while it was still 08-04 locally |
| 3 | **Mutual-fund NAV timing.** Funds strike NAV in the evening ET; equities close 16:00 ET. A run between those two times mixes today's equity closes with *yesterday's* fund NAVs in one snapshot. | Structural. Material here — mutual funds are a large share of the portfolio |
| 4 | **Intraday marks stored as EOD.** During market hours yfinance's daily bar carries a live price; fin writes it as the close and never labels it provisional. | The reconciliation panel disagreed with the broker mid-session, then again differently at close |

## Scope decision — why not higher fidelity

**Rejected: intraday / timestamped price series.** Measured: the cache is
437,005 daily points / 10.6 MB / ~24 bytes per point across 233 shards,
and it is **checked into git**. One-minute bars for one year across the
current symbol set would be ~22.3M points (~0.5 GB/yr) — and it cannot
be backfilled anyway (yfinance caps 1m history at 7 days, 2–90m at 60
days), so history would be a hybrid every consumer must special-case.

**Nothing would read it.** Every price consumer is daily-or-coarser:
`history.compute_history` (semimonthly), `history.compute_daily_totals`,
`analytics/_shared._value_at_date`, `analytics/header.py`,
`analytics/daily_pnl.py`, `prices.option_intrinsic`.

**Rejected: per-bar records** (`{date: {close, asof, settled}}`). 437k
points are historical and permanently settled; annotating all of them to
describe the ~233 that are "today" is the wrong ratio, breaks the
hand-editable `{"symbol":…, "prices": {date: close}}` shape CLAUDE.md
promises, and needs a cache migration.

**Chosen: metadata in the existing sidecar.** All four defects are
questions about *the provenance of one date's bar*, not about resolution.
`cache/price_cache_meta.json` already stores per-symbol `covered_start` /
`covered_end` / `last_fetch` / `failure_count` / `retry_after` /
`last_error` / `tombstone`. That is the right home.

---

## Phase 1 — stop lying about coverage  (small, ship first)

**Goal:** today's bar is always refetchable; `covered_end` can never name
a future local date.

### 1a. Cap `covered_end` at the local date

Two write sites, both in `src/prices.py`:
- ~line 791 — `entry["covered_end"] = end.isoformat() if not ce else max(ce, end.isoformat())`
- ~line 1027 — batch path, `if not ce or dt_str > ce: entry["covered_end"] = dt_str`

Clamp both to `date.today().isoformat()`. A bar dated in the future
(crypto's UTC day) may still be **stored in the shard** — it's real data,
and `get_price` for that date should return it once the date arrives.
Only the *coverage claim* is capped.

### 1b. Treat today as never covered

In `_missing_ranges` (~line 1371), the comparison `if end > ce` should
become "if `end` is after the last date whose bar is final". Simplest
Phase-1 form: clamp the effective `ce` to `min(ce, today - 1 day)` so a
request ending today always produces a 1-day gap.

**Watch out:** `_missing_ranges` starts with `end = _last_trading_day(end)`
(~line 1361), which walks weekends back to Friday and does **not** know
holidays. That's fine and deliberate for equities. Do not "fix" it here.

### 1c. Use the hook that already exists

`ensure_coverage(symbols, start, end, *, verbose, symbol_end_overrides,
force_today_for)` — `src/prices.py:1536`. **`force_today_for` is fully
implemented (see ~1628-1634) and no caller ever passes it.** Prefer
wiring this from `main.py` over inventing a new mechanism. Check whether
1b is even needed once `force_today_for` is passed the held-symbol set —
it may subsume it.

### Phase 1 tests

`tests/test_prices.py`. Required cases:
- `covered_end` never exceeds local today, even when the fetched frame
  contains a future-dated (UTC) bar
- a request ending today produces a fetch even when `covered_end == today`
- **regression for defect 2**: seed a crypto symbol with `covered_end`
  set to tomorrow, assert the next `ensure_coverage` still fetches

**Discipline used throughout this project — apply it here:** after each
test passes, *break the fix* and confirm the test fails. A test that
passes both ways is worse than no test. (This session caught two vacuous
tests that way, including one that only passed because module-level state
leaked between in-process runs.)

**Commit.** Phase 1 stands alone and is worth shipping by itself.

---

## Phase 1.5 — surface what we already know  (highest value per line)

`last_fetch` is **already recorded and already accurate** for every
symbol. It just never reaches the UI. Surfacing it would have made this
session's mid-session reconcile confusion self-diagnosing.

1. `analytics/header.py::compute_header_summary` already returns `as_of`,
   `prev_day`, `value`, `change_1d`, `unpriced_1d`, … Add a
   `prices_as_of` field: the **oldest** `last_fetch` among symbols with a
   non-zero balance (oldest, not newest — it's a staleness floor).
2. Render it in the dashboard header bar. Find it with
   `grep -rn "change_1d" src/dashboard/app/*.js` — the header renderer
   wasn't located this session, so budget a few minutes.
3. Suggested copy: `prices as of 11:04 AM` — and once Phase 2 lands,
   append `· provisional` when anything held is unsettled.

Test: `prices_as_of` is present and equals the oldest `last_fetch` across
held symbols.

**Commit.**

---

## Phase 2 — settle-awareness  (the real work; own a calendar)

**Goal:** refetch a date only *while it can still change*. This is
strictly better than both today's behaviour and blanket always-fetch:

```
run 11:00      today unsettled                → fetch
run 12:00      still unsettled                → fetch again
run 17:00 ET   equities settled, first fetch  → mark final
run 18:30 ET   equities final, funds just set → fetch funds only
run 20:00 ET   everything final               → skip entirely (offline-fast)
next morning   new date                       → fetch
```

`--refresh-prices` then stops being required for correctness and goes back
to meaning "skip CSV parsing".

### 2a. Asset-class classifier

New helper in `src/prices.py`, e.g. `_settle_class(symbol) -> str`
returning `equity` / `fund` / `crypto`:

- **crypto** — `symbol.endswith("-USD")` or in `config.CRYPTO_SYMBOLS`
- **fund** — resolve through `cache/symbol_proxy_map.json` first
  (`{orig: {method, proxy, note}}`; several proxies are real mutual funds
  e.g. `VWILX`, `VIVLX`), then treat multi-word display names as funds —
  same rule as `_classify_no_fetch` (`src/prices.py:730`), which already
  says "multi-word tickers are really fund display names"
- **equity** — everything else (incl. ETFs; they trade intraday)

Keep this classifier next to `_classify_no_fetch` and cross-reference it —
CLAUDE.md already warns that `_classify_no_fetch` gates both the sectors
and prices modules and the two must stay aligned.

### 2b. Settle times

| class | final after |
|---|---|
| equity / ETF | 16:00 ET + ~20 min tape settle, on a trading day |
| fund | ~18:00 ET (NAV strike) |
| crypto | the **UTC** day must be over |

`zoneinfo` is stdlib — **no new dependency**. But note: **this codebase has
no timezone logic today.** `grep -rn "zoneinfo\|pytz\|timezone" src/`
finds one `timezone.utc` stamp in `snapshot.py` and nothing else. Phase 2
introduces the first real calendar handling, which means DST, market
holidays, and half-days (day after Thanksgiving, Christmas Eve close
13:00 ET) become yours. **Half-day misjudgments are harmless** — you think
a bar is unsettled, you refetch once, you get the same value. Optimise for
never marking something final too early; the failure mode in that
direction is a stale number the user trusts.

### 2c. Persist and use it

Add per-symbol `settled_through` (ISO date) to
`price_cache_meta.json` — the newest date whose bar is final. Absent =
unknown, treat as unsettled (safe default, keeps old caches working with
no migration). `_missing_ranges` then fetches any date after
`settled_through` rather than after `covered_end`.

### 2d. Provisional labelling

Extend Phase 1.5's `prices_as_of` with a `prices_provisional` boolean
(true when any held symbol's latest bar is past its `settled_through`).
Render as `· provisional` in the header.

### Phase 2 tests

`tests/test_prices.py`, with a frozen clock (`freezegun` is **not** a
dependency — inject a `now` parameter or monkeypatch a `_now()` helper
rather than adding one):
- equity settles after 16:00 ET but not at 15:59
- fund still unsettled at 16:30 ET, settled at 18:30 ET
- crypto unsettled until the UTC day ends — **use the exact scenario from
  defect 2**: local 22:42 / UTC 05:42 next day
- DST boundary: same wall-clock time in March and November classifies
  correctly
- a settled symbol fetched after its settle time is not refetched
- an unsettled symbol is refetched regardless of `covered_end`

**Commit.**

---

## Will it fit in one 5-hour block?

**Honestly, no — plan for incremental.** Estimate: Phase 1 ~1h, Phase 1.5
~1h, Phase 2 ~2–4h, plus real-data verification runs (a full
`python -m src.main` is not instant) and debugging. That lands at 4–6h
before anything goes wrong.

The phases are deliberately ordered so each is **independently
shippable**, ends green, and gets its own commit. If the block runs out
after Phase 1 + 1.5 you've fixed the actual bug and made staleness
visible — which is most of the practical value. Phase 2 buys correct
labelling and offline-fast evenings, and can wait.

## Verification (run after every phase)

```bash
python -m pytest tests/ -q                      # 432 passing as of this plan
FIN_ASSERT_INVARIANTS=1 python -m src.main      # no InvariantViolation
```

Then confirm the two paths still agree — this is the invariant that broke
twice in one day:

```bash
python -m src.main && cp exports/transactions.json /tmp/full.json
python -m src.main --refresh-prices
# realized totals, tax.realized_by_year, per-txn cost_basis, account_types
# and holdings cost_basis must all match /tmp/full.json
```

`tests/test_pipeline_path_parity.py` already asserts all of that. It
clears `ACCOUNT_GROUPS` / `ACCOUNT_TYPES` before the refresh run to
simulate a fresh process — **do not remove that**, it's the only reason
the suite catches module-level-state divergence between the two paths.

## Traps found the hard way (don't rediscover these)

- **Module-level state leaks between in-process runs.** `ACCOUNT_GROUPS` /
  `ACCOUNT_TYPES` ship empty and metadata fills them, so an in-process
  second pipeline run inherits the first run's maps and hides ordering
  bugs. Any new test that runs both paths must clear them.
- **`_last_trading_day` has no holiday calendar, on purpose.** Leave it.
- **Benchmarks (`config.BENCHMARK_SYMBOLS`) are exempt from the
  closed-position clamp and the trivial-symbol filter.** Don't let new
  fetch-gating logic re-introduce that exclusion — a frozen benchmark
  reports `failure_count: 0` and nothing flags it.
- **Detection tip:** a cached symbol with a stale `covered_end` but
  `failure_count: 0` was never *requested* — it didn't fail.
- **Never introduce `NaN` into the cache.** `json.dump` writes a bare
  `NaN` literal, which is valid JS and reaches the dashboard as a `NaN`
  total rather than a parse error.
- **This repo is public.** No portfolio figures in commit messages, test
  fixtures, or tracked files.
