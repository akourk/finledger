// =========================================================================
// Performance tab — annual returns, best/worst positions, approximate CAGR
// =========================================================================

// Compute annual returns with a flexible account filter.
//   accountFilter = null                   → whole portfolio (Total)
//                   string                  → single account_group
//                   Set<string>             → sum over multiple groups
//                   '__retirement__'        → sugar for 401K+Roth IRA+Rollover IRA
//                   '__taxable__'           → sugar for taxable account_groups
//                                              (Robinhood, Coinbase) — money moves
//                                              freely between them with no tax /
//                                              regulatory friction
//                   '__investments__'       → everything EXCEPT Savings groups
// Returns rows with start/end value, net contribution, $ return, % return,
// and SPY market return for the same period.
const RETIREMENT_GROUP_SET = new Set(['401K', 'Roth IRA', 'Rollover IRA']);
const SAVINGS_GROUP_SET = new Set(['Apple Savings']);

// Derived from ACCOUNT_TYPE_OF at page load: every group whose
// account_type is "Taxable".  Symmetric with RETIREMENT_GROUP_SET.
// Apple Savings is account_type='Savings' (not 'Taxable') so it
// stays out — Savings is its own bucket in fin's classification.
const TAXABLE_GROUP_SET = new Set(
  Object.entries(ACCOUNT_TYPE_OF)
    .filter(([, type]) => type === 'Taxable')
    .map(([g]) => g)
);

// Derived from ACCOUNT_TYPE_OF at page load: everything except Savings.
// Rebuilt here (rather than filtering ACCOUNT_TYPE_OF) so it's stable
// even if historical account_groups are present.
const INVESTMENTS_GROUP_SET = new Set(
  [...Object.keys(ACCOUNT_TYPE_OF)].filter(g => !SAVINGS_GROUP_SET.has(g))
);

function _resolveAccountFilter(f) {
  if (f == null) return null;
  if (f === '__retirement__') return RETIREMENT_GROUP_SET;
  if (f === '__taxable__') return TAXABLE_GROUP_SET;
  if (f === '__investments__') return INVESTMENTS_GROUP_SET;
  if (f instanceof Set) return f;
  return new Set([f]);   // single string → set of one
}

// Map the legacy accountFilter argument to the analytics filter name
// produced by Python.  null → "Total"; '__retirement__' → "Retirement";
// '__investments__' → "Investments"; '__taxable__' → "Taxable";
// otherwise the literal account_group name.  When the precomputed
// entry is missing for a key (e.g. Taxable on older JSON exports),
// callers fall through to client-side TWR computation via
// computeTimeWeightedReturnForWindow.
function _analyticsFilterName(accountFilter) {
  if (accountFilter == null) return 'Total';
  if (accountFilter === '__retirement__') return 'Retirement';
  if (accountFilter === '__taxable__') return 'Taxable';
  if (accountFilter === '__investments__') return 'Investments';
  return accountFilter;
}

function computeAnnualReturns(accountFilter) {
  // Prefer pre-computed analytics (single source of truth).  Shape
  // matches what the old JS returned, with `pct` mapped to TWR % so
  // the existing rendering paths keep working.
  const filterName = _analyticsFilterName(accountFilter);
  const perf = ANALYTICS_PERF[filterName];
  if (perf && Array.isArray(perf.annual)) {
    return perf.annual.map(r => ({
      year: r.year,
      start: r.start,
      end: r.end,
      contrib: 0,               // not exposed per-direction in analytics
      withdraw: 0,
      net: r.net_contrib,
      dollar_return: r.dollar_return,
      pct: r.pct,
      spy_pct: r.spy_pct,
    }));
  }
  // Fallback: old JS computation (kept for accounts not included in
  // the pre-computed filter set, e.g. a hypothetical multi-group combo).
  const filterSet = _resolveAccountFilter(accountFilter);
  const byYear = {};
  for (const h of history) {
    const y = yearOf(h.date);
    if (!y) continue;
    if (!byYear[y]) byYear[y] = { snaps: [] };
    byYear[y].snaps.push(h);
  }
  const years = Object.keys(byYear).sort();

  const addActions = new Set(['Deposit', 'Contribution']);
  const subActions = new Set(['Withdrawal', 'Distribution']);
  for (const t of txns) {
    const y = yearOf(t.date);
    if (!y || !byYear[y]) continue;
    if (filterSet && !filterSet.has(t.account_group)) continue;
    if (addActions.has(t.action)) {
      byYear[y].contrib = (byYear[y].contrib || 0) + (t.amount || 0);
      continue;
    }
    if (subActions.has(t.action)) {
      // Distribution from Roth IRA / Rollover IRA is overwhelmingly a
      // custodian rollover (e.g. Voya 401K → Schwab Rollover IRA).
      // Skip to match the Transfer In skip on the receiving side, so
      // the two legs of a rollover cancel out instead of showing up
      // as net -$X for the year.
      if (t.action === 'Distribution'
        && (t.account_group === 'Roth IRA' || t.account_group === 'Rollover IRA')) {
        continue;
      }
      byYear[y].withdraw = (byYear[y].withdraw || 0) + (t.amount || 0);
      continue;
    }
    // USAA-style "Buy" with CURRENT/PRIOR YEAR CONTRIBUTION marker in
    // the description — treat as a user contribution.  Use calendar
    // year of the txn (not tax-attribution year) so period returns
    // line up with when the cash actually flowed.
    if (retirementContribInfo(t).isContrib) {
      byYear[y].contrib = (byYear[y].contrib || 0) + (t.amount || 0);
    }
  }

  // Value accessor: sum over filter set, or total for null filter.
  // Add rollover-bridge adjustment so year boundaries aren't distorted
  // by in-flight cash during custodian rollovers (Voya→Schwab, etc.).
  const rawValueFn = filterSet
    ? (h) => {
      if (!h.by_account_group) return 0;
      let s = 0;
      for (const g of filterSet) s += h.by_account_group[g] || 0;
      return s;
    }
    : (h) => h.total || 0;
  const valueFn = (h) => rawValueFn(h) + _rolloverBridgeAdjustment(h.date, filterSet);

  const rows = [];
  for (let i = 0; i < years.length; i++) {
    const y = years[i];
    const currentSnaps = byYear[y].snaps;
    // Year boundary: use previous year's final snapshot as the START so
    // we capture full calendar-year returns (otherwise the first ~30 days
    // are excluded because monthly snapshots skip Jan 1–31).
    const startSnap = i > 0 ? byYear[years[i - 1]].snaps.slice(-1)[0] : currentSnaps[0];
    const endSnap = currentSnaps[currentSnaps.length - 1];
    const startValue = valueFn(startSnap);
    const endValue = valueFn(endSnap);
    const netContrib = (byYear[y].contrib || 0) - (byYear[y].withdraw || 0);
    const dollarReturn = endValue - startValue - netContrib;
    const denom = startValue + netContrib / 2;
    const pctReturn = denom > 0 ? (dollarReturn / denom) * 100 : null;
    // SPY market return for the year — pure price change, independent
    // of the user's contribution timing.
    let spyPct = null;
    const spyStart = startSnap.benchmark_spy_price;
    const spyEnd = endSnap.benchmark_spy_price;
    if (spyStart && spyEnd && spyStart > 0) {
      spyPct = ((spyEnd / spyStart) - 1) * 100;
    }
    rows.push({
      year: y,
      start: startValue, end: endValue,
      contrib: byYear[y].contrib || 0,
      withdraw: byYear[y].withdraw || 0,
      net: netContrib,
      dollar_return: dollarReturn,
      pct: pctReturn,
      spy_pct: spyPct,
    });
  }
  return rows;
}

let performanceAccountFilter = null;   // null = Total
// Performance tab metric window: one of PERF_WINDOWS below.
// Affects the headline stat cards (Cum/Ann return, Sharpe, Sortino,
// MaxDD, Calmar) so the user can see "what's this portfolio doing
// recently" without lifetime history dominating.
let performanceWindow = 'lifetime';
// Rebase override for the benchmark chart.  null = "auto" (rebase iff
// window != lifetime).  true / false = explicit override.  Lets the
// user see absolute dollars in a windowed view, or rebased lines in
// the lifetime view, when they want to.
let benchRebaseOverride = null;
// Custom-range date inputs — only consulted when performanceWindow
// === 'custom'.  Single source of truth for the entire Performance
// tab; no separate "By Account TWR window" state any more.
let perfTwrStart = null;
let perfTwrEnd = null;

function setPerformanceWindow(w) {
  performanceWindow = PERF_TWR_PRESETS.includes(w) ? w : 'lifetime';
  // Switching to a non-custom preset clears any stale custom dates
  // so the preset's derived window unambiguously controls.
  if (performanceWindow !== 'custom') {
    perfTwrStart = null;
    perfTwrEnd = null;
  }
  renderPerformance();
}

// Three-state toggle for the benchmark chart's y-axis: auto (rebase
// when windowed) / on (always rebase) / off (always absolute).
function setBenchRebase(mode) {
  if (mode === 'auto') benchRebaseOverride = null;
  else if (mode === 'on') benchRebaseOverride = true;
  else if (mode === 'off') benchRebaseOverride = false;
  renderPerformance();
}

function setPerformanceAccountFilter(v) {
  performanceAccountFilter = v || null;
  renderPerformance();
}
// Editing either custom-date input automatically flips the window
// preset to 'custom' so the chip row reflects what's being computed.
function setPerfTwrStart(d) {
  perfTwrStart = d || null;
  if (perfTwrStart && perfTwrEnd && perfTwrStart > perfTwrEnd) perfTwrEnd = perfTwrStart;
  performanceWindow = 'custom';
  renderPerformance();
}
function setPerfTwrEnd(d) {
  perfTwrEnd = d || null;
  if (perfTwrStart && perfTwrEnd && perfTwrEnd < perfTwrStart) perfTwrStart = perfTwrEnd;
  performanceWindow = 'custom';
  renderPerformance();
}

// Time-weighted return using the Modified Dietz formula per sub-period.
// Removes the effect of contribution/withdrawal timing so you see what
// your investments actually did with the money, independent of when
// you happened to fund the account.
//
// Per-period return (Modified Dietz, midpoint-weighted flows):
//   r = (end − start − net_flow) / (start + net_flow / 2)
// Chain-link:   TWR_cum = ∏(1 + r_i) − 1
// Annualized:   (1 + TWR_cum) ^ (1 / years) − 1
//
// Using midpoint-weighted denominator instead of the classic "start"
// handles bootstrap periods gracefully (e.g. $100 starting balance +
// $5000 contribution in one month wouldn't produce a −75% return).
// This is the same approximation most brokerages use when they don't
// have daily flow data.
//
// Sub-periods here are the gaps between consecutive history snapshots
// (monthly resolution).  Returns null if the filter has no activity yet.
// Sourced from the action catalog (DATA.action_catalog) — adding a
// new contribution-/withdrawal-style action in src/actions.py
// automatically picks it up here.
const _TWR_ADD_ACTIONS = _actionsWith('cash_flow', 'in');
const _TWR_SUB_ACTIONS = _actionsWith('cash_flow', 'out');

function _netFlowBetween(prevDate, currDate, filterSet) {
  let net = 0;
  for (const t of txns) {
    if (!t.date || t.date <= prevDate || t.date > currDate) continue;
    if (filterSet && !filterSet.has(t.account_group)) continue;
    const amt = t.amount || 0;
    if (amt <= 0) continue;
    if (_TWR_ADD_ACTIONS.has(t.action)) { net += amt; continue; }
    if (_TWR_SUB_ACTIONS.has(t.action)) {
      // Distribution from Roth IRA / Rollover IRA is typically a
      // custodian rollover — skip to match the Transfer In skip on
      // the other side.  (See computeAnnualReturns for details.)
      if (t.action === 'Distribution'
        && (t.account_group === 'Roth IRA' || t.account_group === 'Rollover IRA')) {
        continue;
      }
      net -= amt;
      continue;
    }
    // USAA Roth IRA style: a "Buy" row with a contribution marker in
    // the description is actually cash flowing INTO the account.
    // retirementContribInfo handles these already; reuse so TWR and
    // the contribution-by-year table agree on what counts.
    if (retirementContribInfo(t).isContrib) net += amt;
  }
  return net;
}

function _twrWalk(startIdx, endIdx, valueFn, filterSet) {
  let cumulative = 1;
  let anyPeriod = false;
  for (let i = startIdx + 1; i <= endIdx; i++) {
    const prev = history[i - 1], curr = history[i];
    const sv = valueFn(prev), ev = valueFn(curr);
    const net = _netFlowBetween(prev.date, curr.date, filterSet);
    // Modified Dietz denominator: average capital during the period
    const denom = sv + net / 2;
    if (denom <= 0) continue;                // account effectively empty
    // Skip periods where the cash flow dwarfs the share value on both
    // sides.  Happens early in a brokerage account's life when user
    // ACH-deposits cash that sits uninvested — the share-only balance
    // we track via `by_account_group` doesn't move, but `net_flow`
    // records the full deposit, producing nonsense returns.  (Main.py
    // skips USD balance tracking in non-Savings accounts on purpose
    // because broker CSVs underreport sell proceeds; rebuilding full
    // account-level cash balances is a bigger architectural change.)
    const maxBalance = Math.max(sv, ev);
    if (maxBalance > 0 && Math.abs(net) > maxBalance * 0.8) continue;
    if (denom < 100) continue;               // too small for stable return
    const r = (ev - sv - net) / denom;
    if (r <= -1) continue;                   // pinning guard
    cumulative *= (1 + r);
    anyPeriod = true;
  }
  return anyPeriod ? cumulative - 1 : null;
}

function computeTimeWeightedReturn(accountFilter) {
  // Prefer pre-computed analytics.  Returns the shape the callers
  // expect: {cumulative, annualized, years, start_date, end_date}.
  const perf = ANALYTICS_PERF[_analyticsFilterName(accountFilter)];
  if (perf && perf.summary) {
    const s = perf.summary;
    return {
      cumulative: s.cumulative,
      annualized: s.annualized,
      years: s.years,
      start_date: s.start_date,
      end_date: s.end_date,
    };
  }
  return computeTimeWeightedReturnForWindow(accountFilter, null, null);
}

// TWR over an arbitrary [startDate, endDate] window.  Snaps each bound
// to the nearest history snapshot (start = first snapshot >= requested,
// end = last snapshot <= requested), chain-links the per-period Modified
// Dietz returns in between, and annualizes over the actual span.  Null
// args mean "use the natural bounds" (first meaningful snapshot and
// latest snapshot respectively).
//
// Use this for user-driven range pickers (e.g. Performance tab's custom
// period for Schwab-style validation).  The result is independent of
// the pre-computed analytics summaries.
function computeTimeWeightedReturnForWindow(accountFilter, startDate, endDate) {
  if (history.length < 2) return null;
  const filterSet = _resolveAccountFilter(accountFilter);
  const rawValueFn = filterSet
    ? (h) => {
      let s = 0;
      if (h.by_account_group) for (const g of filterSet) s += h.by_account_group[g] || 0;
      return s;
    }
    : (h) => h.total || 0;
  const valueFn = (h) => rawValueFn(h) + _rolloverBridgeAdjustment(h.date, filterSet);

  // Snap each requested date to the NEAREST snapshot (by absolute day
  // distance).  Floor-snapping (last snap <= date) is wrong here — it
  // can skip a month-end rally if the user picks a mid-month date.
  // Using nearest is at most ~8 days off for a semimonthly cadence and
  // lets the user validate against brokerage statements whose exact
  // end date won't coincide with our snapshot dates.
  function nearestIdx(requested) {
    if (!requested) return null;
    const target = new Date(requested).getTime();
    let bestIdx = -1, bestAbs = Infinity;
    for (let i = 0; i < history.length; i++) {
      const d = Math.abs(new Date(history[i].date).getTime() - target);
      if (d < bestAbs) { bestAbs = d; bestIdx = i; }
    }
    return bestIdx;
  }

  let startIdx = 0;
  if (startDate) {
    startIdx = nearestIdx(startDate);
    if (startIdx == null || startIdx < 0) return null;
  } else {
    // Default: first snapshot where the account has any activity.
    while (startIdx < history.length && valueFn(history[startIdx]) <= 0
      && _netFlowBetween('', history[startIdx].date, filterSet) <= 0) {
      startIdx++;
    }
  }
  let endIdx = history.length - 1;
  if (endDate) {
    const e = nearestIdx(endDate);
    if (e == null || e < 0) return null;
    endIdx = e;
  }
  if (startIdx >= endIdx) return null;

  const cum = _twrWalk(startIdx, endIdx, valueFn, filterSet);
  if (cum == null) return null;
  const yearsSpan = (new Date(history[endIdx].date)
    - new Date(history[startIdx].date)) / (365.25 * 86400000);
  const annualized = yearsSpan > 0 && (1 + cum) > 0
    ? Math.pow(1 + cum, 1 / yearsSpan) - 1
    : null;
  return {
    cumulative: cum,
    annualized,
    years: yearsSpan,
    start_date: history[startIdx].date,
    end_date: history[endIdx].date,
  };
}

// Per-year TWR for the selected filter.  Same sub-period chain-link
// approach as computeTimeWeightedReturn, but computed year-by-year
// using the previous year's final snapshot as the starting value (so
// full calendar-year boundaries are honoured).
function computeAnnualTWR(accountFilter) {
  // Prefer pre-computed analytics.  Analytics returns twr_pct as
  // percentage (e.g. 12.5 means 12.5%); the caller expects fractions
  // (0.125), so convert.
  const perf = ANALYTICS_PERF[_analyticsFilterName(accountFilter)];
  if (perf && Array.isArray(perf.annual)) {
    const out = {};
    for (const r of perf.annual) {
      out[r.year] = r.twr_pct != null ? r.twr_pct / 100 : null;
    }
    return out;
  }
  const filterSet = _resolveAccountFilter(accountFilter);
  const rawValueFn = filterSet
    ? (h) => {
      let s = 0;
      if (h.by_account_group) for (const g of filterSet) s += h.by_account_group[g] || 0;
      return s;
    }
    : (h) => h.total || 0;
  const valueFn = (h) => rawValueFn(h) + _rolloverBridgeAdjustment(h.date, filterSet);

  // Group snapshots by year
  const byYear = {};
  for (const h of history) {
    const y = yearOf(h.date);
    if (!y) continue;
    if (!byYear[y]) byYear[y] = [];
    byYear[y].push(h);
  }
  const years = Object.keys(byYear).sort();
  const out = {};

  for (let i = 0; i < years.length; i++) {
    const y = years[i];
    // Use the prior year's last snapshot as the period start (captures
    // Jan 1 → first-snap-of-year segment).  For the first year, use
    // that year's first snapshot.
    const startSnap = i > 0 ? byYear[years[i - 1]].slice(-1)[0] : byYear[y][0];
    const yearSnaps = i > 0 ? byYear[y] : byYear[y].slice(1);
    const periodSnaps = [startSnap, ...yearSnaps];

    let cumulative = 1;
    let anyValid = false;
    for (let j = 1; j < periodSnaps.length; j++) {
      const prev = periodSnaps[j - 1], curr = periodSnaps[j];
      const sv = valueFn(prev), ev = valueFn(curr);
      const net = _netFlowBetween(prev.date, curr.date, filterSet);
      // Modified Dietz denominator
      const denom = sv + net / 2;
      if (denom <= 0) continue;
      const r = (ev - sv - net) / denom;
      if (r <= -1) continue;
      cumulative *= (1 + r);
      anyValid = true;
    }
    out[y] = anyValid ? cumulative - 1 : null;
  }
  return out;
}

// SPY's market return over the same time window as a TWR result.
// For a single-asset buy-and-hold benchmark, TWR collapses to the
// simple start-to-end price ratio.  When called with the exact start
// and end dates from an analytics summary, we can short-circuit and
// pull the pre-computed spy_cumulative / spy_annualized.
function computeSPYReturnOverPeriod(startDate, endDate) {
  // Try to find a matching analytics summary first.  Scanning EVERY
  // filter is safe on purpose: SPY's return over a window is a market
  // fact, identical in every filter's summary that shares the window.
  // But note what that means when the caller is wrong — it hands back
  // another filter's plausible-looking number instead of nothing.  It
  // did exactly that when the benchmark card passed the chart's window
  // instead of the metric's (AUDIT.md F-031).  Pass the window the
  // return you're pairing against was actually measured over.
  for (const perf of Object.values(ANALYTICS_PERF)) {
    const s = perf && perf.summary;
    if (s && s.start_date === startDate && s.end_date === endDate) {
      return {
        cumulative: s.spy_cumulative,
        annualized: s.spy_annualized,
        years: s.years,
      };
    }
  }
  if (!history.length) return null;
  // Nearest-snapshot snap (same policy as the TWR walker) so SPY is
  // measured over the same window we report in the Period card.
  function nearestSnap(target) {
    if (!target) return null;
    const t = new Date(target).getTime();
    let best = null, bestAbs = Infinity;
    for (const h of history) {
      const d = Math.abs(new Date(h.date).getTime() - t);
      if (d < bestAbs) { bestAbs = d; best = h; }
    }
    return best;
  }
  const startSnap = nearestSnap(startDate) || history[0];
  const endSnap = nearestSnap(endDate) || history[history.length - 1];
  const p0 = startSnap.benchmark_spy_price;
  const p1 = endSnap.benchmark_spy_price;
  if (!p0 || !p1 || p0 <= 0) return null;
  const cum = (p1 / p0) - 1;
  const years = (new Date(endSnap.date) - new Date(startSnap.date)) / (365.25 * 86400000);
  const ann = years > 0 ? Math.pow(1 + cum, 1 / years) - 1 : null;
  return { cumulative: cum, annualized: ann, years };
}

// Best / worst positions by realized dollar total, current unrealized, or
// total return percentage on current positions.
function computePositionReturns() {
  // Prefer pre-computed analytics when available.
  const pos = ANALYTICS.positions;
  if (pos && Array.isArray(pos.positions)) {
    // Match the old shape: rows with pctReturn (camelCase) + total_gain
    return pos.positions.map(p => ({
      ...p,
      pctReturn: p.pct_return != null ? p.pct_return : null,
    }));
  }
  const perSym = {};
  for (const t of txns) {
    const sym = t.symbol;
    if (!sym || sym === 'USD') continue;
    if (!perSym[sym]) perSym[sym] = { symbol: sym, realized: 0 };
    if (typeof t.realized_gain === 'number') perSym[sym].realized += t.realized_gain;
  }
  for (const h of holdingsByAsset) {
    if (!perSym[h.symbol]) perSym[h.symbol] = { symbol: h.symbol, realized: 0 };
    perSym[h.symbol].value = h.value;
    perSym[h.symbol].basis = h.cost_basis;
    perSym[h.symbol].unrealized = h.unrealized_gain;
    perSym[h.symbol].sector = h.sector;
  }
  const rows = Object.values(perSym).map(p => {
    const total_gain = (p.realized || 0) + (p.unrealized || 0);
    const invested = (p.basis || 0) + Math.max(0, p.realized || 0) ? (p.basis || 0) : 0;
    // Rough total return %: (realized + unrealized) / (total dollars in)
    // Approximate "dollars in" = current basis + |realized gain| when realized>0
    // Keep it simple: use current basis if present, else skip.
    const pctReturn = (p.basis && p.basis > 0) ? (total_gain / p.basis) * 100 : null;
    return { ...p, total_gain, pctReturn };
  });
  return rows;
}

// Multi-line chart used by the Performance "You vs SPY" section.
// Accepts an array of series and renders a shared-axis chart with a
// unified hover tooltip that shows all series at the hovered date.
function renderMultiLineChart(seriesArr, opts) {
  opts = opts || {};
  const id = opts.id || ('multiChart_' + Math.random().toString(36).slice(2, 7));
  const height = opts.height || 300;
  const yFmt = opts.yFormatter || fmtMoneyShort;
  if (!seriesArr || !seriesArr.length || !seriesArr[0].points.length) {
    return `<div class="chart-empty">${opts.emptyMsg || 'No data.'}</div>`;
  }
  const n = seriesArr[0].points.length;

  const buildContent = (W) => {
    const H = height, PAD = { l: 64, r: 16, t: 12, b: 28 };
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    const xOf = i => PAD.l + (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));
    // True data range — float the y-axis so short windows (6mo/3mo/30d)
    // don't squish the lines to the top of an anchored-at-zero scale.
    // Schwab et al do this — y-axis bounds reflect the data's actual
    // spread.  Pad each side by 8% of the range so lines don't kiss
    // the chart borders.
    let dataMin = Infinity, dataMax = -Infinity;
    for (const s of seriesArr) for (const p of s.points) {
      if (typeof p.value !== 'number' || isNaN(p.value)) continue;
      if (p.value > dataMax) dataMax = p.value;
      if (p.value < dataMin) dataMin = p.value;
    }
    if (!isFinite(dataMin) || !isFinite(dataMax)) { dataMin = 0; dataMax = 1; }
    const range = dataMax - dataMin;
    const padPx = range > 0 ? range * 0.08 : Math.max(1, Math.abs(dataMax) * 0.08);
    let minY = dataMin - padPx;
    let maxY = dataMax + padPx;
    // Caller can opt back into "include zero" for charts where 0 is a
    // meaningful reference (P&L, drawdown).  Default off so dollar-
    // value charts can spread.
    if (opts.includeZero) {
      if (minY > 0) minY = 0;
      if (maxY < 0) maxY = 0;
    }
    // Edge case: identical-value series → give it a token range so the
    // line still draws as a flat horizontal instead of a divide-by-0.
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const yOf = v => PAD.t + plotH - ((v - minY) / (maxY - minY)) * plotH;

    const parts = [];
    const yTicks = 5;
    for (let i = 0; i <= yTicks; i++) {
      const v = minY + ((maxY - minY) * i) / yTicks;
      const y = yOf(v);
      parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
      parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${yFmt(v)}</text>`);
    }
    if (minY < 0) {
      const y0 = yOf(0);
      parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${y0}" x2="${W - PAD.r}" y2="${y0}" stroke-opacity="0.6"/>`);
    }
    const xTicks = Math.min(6, n);
    const firstSeries = seriesArr[0].points;
    for (let i = 0; i < xTicks; i++) {
      const idx = Math.round((i * (n - 1)) / (xTicks - 1 || 1));
      const x = xOf(idx);
      parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="middle">${(firstSeries[idx].date || '').slice(0, 7)}</text>`);
    }
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);

    // Each series as its own path (solid primary, dashed benchmarks)
    for (const s of seriesArr) {
      const d = s.points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(p.value)}`).join(' ');
      const dash = s.dashed ? ' stroke-dasharray="4 4"' : '';
      const op = s.dashed ? ' stroke-opacity="0.85"' : '';
      parts.push(`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.8"${dash}${op}/>`);
    }

    // Hover overlay
    parts.push(`<line class="hover-v" id="${id}_hv" x1="0" y1="${PAD.t}" x2="0" y2="${PAD.t + plotH}" style="display:none"/>`);
    parts.push(`<g id="${id}_dots"></g>`);
    parts.push(`<rect id="${id}_cap" x="${PAD.l}" y="${PAD.t}" width="${plotW}" height="${plotH}" fill="transparent"/>`);

    return { content: parts.join(''), xOf, yOf, W, H, PAD, plotW };
  };

  queueMicrotask(() => {
    const svg = document.getElementById(id);
    if (!svg) return;
    const actualW = Math.round(svg.getBoundingClientRect().width) || 800;
    const { content, xOf, yOf, W, plotW, PAD } = buildContent(actualW);
    svg.setAttribute('viewBox', `0 0 ${actualW} ${height}`);
    svg.innerHTML = content;
    const hv = document.getElementById(id + '_hv');
    const dots = document.getElementById(id + '_dots');
    const cap = document.getElementById(id + '_cap');
    const tip = document.getElementById(id + '_tip');
    if (!cap) return;
    cap.addEventListener('mousemove', ev => {
      const r = svg.getBoundingClientRect();
      const mx = (ev.clientX - r.left) * (W / r.width);
      let idx = Math.round(((mx - PAD.l) / plotW) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      const x = xOf(idx);
      hv.setAttribute('x1', x); hv.setAttribute('x2', x);
      hv.style.display = '';
      dots.innerHTML = seriesArr.map(s => {
        const y = yOf(s.points[idx].value);
        return `<circle cx="${x}" cy="${y}" r="4" fill="${s.color}" stroke="var(--bg)" stroke-width="1.5"/>`;
      }).join('');
      if (tip) {
        const rows = seriesArr.map(s => `<div class="tt-row">
          <span class="tt-name"><span class="tt-swatch" style="background:${s.color}"></span>${s.label}</span>
          <span>${fmtMoney(s.points[idx].value)}</span>
        </div>`).join('');
        tip.innerHTML = `<div class="tt-date">${seriesArr[0].points[idx].date || ''}</div>${rows}`;
        tip.style.display = 'block';
        const wrap = tip.parentElement.getBoundingClientRect();
        let tx = ev.clientX - wrap.left + 12;
        let ty = ev.clientY - wrap.top + 12;
        const tr = tip.getBoundingClientRect();
        if (tx + tr.width + 12 > wrap.width) tx = ev.clientX - wrap.left - tr.width - 12;
        if (ty + tr.height + 12 > wrap.height) ty = ev.clientY - wrap.top - tr.height - 12;
        tip.style.left = tx + 'px';
        tip.style.top = ty + 'px';
      }
    });
    cap.addEventListener('mouseleave', () => {
      hv.style.display = 'none';
      dots.innerHTML = '';
      if (tip) tip.style.display = 'none';
    });
  });

  const legend = seriesArr.map(s =>
    `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${s.label}</span>`
  ).join('');
  return `<div class="chart-wrap" style="padding:10px;position:relative;">
    <svg id="${id}" class="chart-svg" viewBox="0 0 800 ${height}"
         style="width:100%;height:${height}px;display:block;"></svg>
    <div class="chart-tooltip" id="${id}_tip"></div>
    <div class="chart-legend">${legend}</div>
  </div>`;
}

// --- Drawdown section (Performance tab) ------------------------------------
function _buildDrawdownSection() {
  const dd = ANALYTICS.drawdown || {};
  const series = dd.series || [];
  if (!series.length) return '';
  const win = dd.max_drawdown_window || {};
  const curDd = dd.current_drawdown_pct || 0;
  const maxDd = dd.max_drawdown || 0;

  const cards = [
    {
      label: 'Max Drawdown',
      value: (maxDd * 100).toFixed(2) + '%',
      cls: maxDd < 0 ? 'negative' : '',
      sub: win.peak_date && win.trough_date
        ? `${win.peak_date} → ${win.trough_date}`
        : ''
    },
    {
      label: 'Days to Trough',
      value: win.days_to_trough != null ? win.days_to_trough : '—',
      sub: ''
    },
    {
      label: 'Days to Recover',
      value: win.days_to_recover != null ? win.days_to_recover
        : (win.recovery_date === null ? 'not recovered' : '—'),
      sub: win.recovery_date || ''
    },
    {
      label: 'Current Drawdown',
      value: (curDd * 100).toFixed(2) + '%',
      cls: curDd < 0 ? 'negative' : '',
      sub: curDd < 0 ? 'from peak' : 'at all-time high'
    },
  ];
  const statsHtml = cards.map(c => `<div class="ds-card">
    <div class="ds-label">${c.label}</div>
    <div class="ds-value ${c.cls || ''}">${c.value}</div>
    ${c.sub ? `<div class="ds-sub">${_htmlEsc(c.sub)}</div>` : ''}
  </div>`).join('');

  // Inline mini chart of drawdown series (bars going down from 0)
  const chartId = 'drawdownChart';
  const chartHtml = `<div class="chart-wrap">
    <svg class="chart-svg" id="${chartId}" preserveAspectRatio="none" style="height:180px;"></svg>
  </div>`;

  // Render after DOM insertion
  queueMicrotask(() => {
    const svg = document.getElementById(chartId);
    if (!svg || !series.length) return;
    const W = Math.round(svg.getBoundingClientRect().width) || 800;
    const H = 180, PAD = { l: 48, r: 12, t: 10, b: 22 };
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    const minDd = Math.min(...series.map(s => s.drawdown_pct));
    const lo = Math.min(-0.01, minDd * 1.05);
    const xOf = i => PAD.l + (series.length === 1 ? plotW / 2 : (i * plotW) / (series.length - 1));
    const yOf = v => PAD.t + ((v - 0) / (lo - 0 || 1)) * plotH;
    const parts = [];
    for (let k = 0; k <= 4; k++) {
      const v = (lo * k) / 4;
      const y = yOf(v);
      parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
      parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${(v * 100).toFixed(0)}%</text>`);
    }
    const d = series.map((s, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(s.drawdown_pct)}`).join(' ');
    const area = `M${xOf(0)},${yOf(0)} ` + series.map((s, i) => `L${xOf(i)},${yOf(s.drawdown_pct)}`).join(' ') +
      ` L${xOf(series.length - 1)},${yOf(0)} Z`;
    parts.push(`<path d="${area}" fill="rgba(248,113,113,0.2)"/>`);
    parts.push(`<path d="${d}" fill="none" stroke="#f87171" stroke-width="1.6"/>`);
    const xTicks = Math.min(6, series.length);
    for (let i = 0; i < xTicks; i++) {
      const idx = Math.round((i * (series.length - 1)) / (xTicks - 1 || 1));
      const x = xOf(idx);
      parts.push(`<text class="axis-label" x="${x}" y="${H - 6}" text-anchor="middle">${(series[idx].date || '').slice(0, 7)}</text>`);
    }
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join('');
  });

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Drawdown</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Peak-to-trough portfolio decline — shows historical risk.</span>
    </div>
    <div class="drawdown-stats">${statsHtml}</div>
    ${chartHtml}
  `;
}

// --- Trading activity heatmap: REMOVED -------------------------------------
// A GitHub-style graph of trade counts rewards activity — the opposite
// of what a buy-and-hold tracker should emphasize.  The analytics
// (analytics.trading_heatmap) still ship in the JSON for anyone who
// wants the data; only the UI section was dropped.

// Single source of truth for windowed performance metrics on the
// Performance tab.  Returns ``{cum, ann, sharpe, sortino, mdd,
// mddPeak, mddTrough, calmar, nMonths, nInRatio}`` for any
// (filterKey, windowKey) pair.  Both the top stat cards and the
// "By Account" TWR section consume this so toggling the window or
// account selector updates every figure consistently — no drift
// between the cumulative return at the top and the TWR shown
// further down.
//
// - ``cum`` / ``ann``: chain-linked Modified Dietz from
//   ``computeTimeWeightedReturnForWindow`` (existing TWR engine).
//   Lifetime+null filter reads the precomputed analytics summary
//   so it matches the rest of the dashboard.
// - ``sharpe`` / ``sortino``: arithmetic mean / stdev over the
//   significant per-period returns (start_value ≥ 1% of the
//   filtered all-time peak, |return| ≤ 50% magnitude cap).  Same
//   filter rules as Python's ``monthly_pnl.compute_monthly_pnl``.
// - ``mdd``: deepest peak-to-trough decline within the window's
//   filtered values.  In percentage points (-65.16 = -65.16%).
// - ``calmar``: ``ann × 100 / |mdd|``.  > 1 solid, > 3 exceptional.
function computeWindowedMetrics(filterKey, windowKey) {
  const empty = {
    cum: null, ann: null, sharpe: null, sortino: null,
    mdd: null, mddPeak: null, mddTrough: null, calmar: null,
    nMonths: 0, nInRatio: 0,
    startDate: null, endDate: null, spyCum: null, spyAnn: null,
  };
  if (!history.length) return empty;
  const filterSet = _resolveAccountFilter(filterKey);
  // Apply rollover-bridge adjustment so custodial transfers (Voya
  // 401K → Schwab Rollover IRA, etc.) don't show as -100% drawdowns
  // when the snapshot catches money mid-flight between accounts.
  // Same value-getter shape as computeTimeWeightedReturnForWindow,
  // ensuring the top stat cards and the By Account section consume
  // identical values for Sharpe/Sortino/MaxDD computations.
  const rawValueAt = filterSet
    ? (h) => {
      if (!h.by_account_group) return 0;
      let s = 0;
      for (const g of filterSet) s += h.by_account_group[g] || 0;
      return s;
    }
    : (h) => h.total || 0;
  const valueAt = (h) => rawValueAt(h) + _rolloverBridgeAdjustment(h.date, filterSet);

  const ref = history[history.length - 1].date;
  // 'custom' = caller-driven start/end via perfTwrStart / perfTwrEnd.
  // Fall through to all-history if dates missing.
  let lowerIso = '', upperIso = '';
  if (windowKey === 'custom') {
    lowerIso = perfTwrStart || '';
    upperIso = perfTwrEnd || ref;
  } else {
    lowerIso = _windowCutoffIso(windowKey, ref);
    upperIso = ref;
  }
  const windowed = history.filter(h =>
    (!lowerIso || h.date >= lowerIso) && (!upperIso || h.date <= upperIso));
  if (windowed.length < 2) return empty;

  // Cum / ann via the existing TWR engine — same code path the By
  // Account TWR section uses, so the two figures match exactly.
  // For lifetime, ALWAYS prefer the precomputed Python summary
  // (regardless of filter) so the top cards and By Account cards
  // agree.  When no precomputed entry exists for a filter (e.g.
  // __taxable__ on older JSON), fall through to JS computation.
  //
  // ``startDate`` / ``endDate`` report the window cum/ann ACTUALLY
  // covers, which is not always the sliced ``windowed`` array:
  //   - lifetime + account filter -> the ACCOUNT's natural window
  //     (first snapshot where it held value), which can start years
  //     after the portfolio's first snapshot.
  //   - trailing presets -> the nearest-snapshot snap inside
  //     computeTimeWeightedReturnForWindow, not the raw cutoff.
  // The benchmark card pairs a SPY return against these dates, so
  // reporting the wrong ones measures SPY over a different span than
  // the return it sits next to.
  let cum = null, ann = null, spyCum = null, spyAnn = null;
  let startDate = windowed[0].date;
  let endDate = windowed[windowed.length - 1].date;
  if (windowKey === 'lifetime') {
    const summary = ANALYTICS_PERF[_analyticsFilterName(filterKey)]?.summary;
    if (summary) {
      cum = summary.cumulative; ann = summary.annualized;
      if (summary.start_date) startDate = summary.start_date;
      if (summary.end_date) endDate = summary.end_date;
      // Python already measured SPY over exactly this window.
      if (summary.spy_cumulative != null) spyCum = summary.spy_cumulative;
      if (summary.spy_annualized != null) spyAnn = summary.spy_annualized;
    }
  }
  if (cum == null) {
    const twr = windowKey === 'custom'
      ? computeTimeWeightedReturnForWindow(filterKey, perfTwrStart, perfTwrEnd)
      : computeTimeWeightedReturnForWindow(
        filterKey,
        windowKey === 'lifetime' ? null : startDate,
        windowKey === 'lifetime' ? null : endDate,
      );
    if (twr) {
      cum = twr.cumulative; ann = twr.annualized;
      if (twr.start_date) startDate = twr.start_date;
      if (twr.end_date) endDate = twr.end_date;
    }
  }

  // Per-period returns within the window for Sharpe/Sortino.
  // Significant-base filter: start_value ≥ 1% of FILTERED all-time
  // peak (consistent with monthly_pnl.compute_monthly_pnl).  Magnitude
  // cap at ±50% to drop data-artifact months from the ratio calc.
  const filteredPeak = Math.max(1, ...history.map(valueAt));
  const SIG_THRESH = filteredPeak * 0.01;
  const MAG_CAP = 0.50;
  const periodReturns = [];
  const sigReturns = [];
  for (let i = 1; i < windowed.length; i++) {
    const prev = windowed[i - 1];
    const curr = windowed[i];
    const startV = valueAt(prev);
    const endV = valueAt(curr);
    if (startV <= 0) continue;
    let flow = 0;
    for (const t of txns) {
      const d = t.date || '';
      if (d <= prev.date || d > curr.date) continue;
      if (filterSet && !filterSet.has(t.account_group)) continue;
      if (typeof t.cash_flow === 'number') flow += t.cash_flow;
    }
    const ret = (endV - startV - flow) / startV;
    periodReturns.push(ret);
    if (startV >= SIG_THRESH && Math.abs(ret) <= MAG_CAP) {
      sigReturns.push(ret);
    }
  }

  const RF_MONTHLY = 0.04 / 12;
  let sharpe = null, sortino = null;
  if (sigReturns.length >= 6) {
    const excess = sigReturns.map(r => r - RF_MONTHLY);
    const mean = excess.reduce((s, e) => s + e, 0) / excess.length;
    const variance = excess.reduce((s, e) => s + (e - mean) ** 2, 0) / Math.max(1, excess.length - 1);
    const sd = Math.sqrt(variance);
    if (sd > 0) sharpe = +(mean / sd * Math.sqrt(12)).toFixed(3);
    const downside = excess.filter(e => e < 0);
    if (downside.length >= 2) {
      const dsVar = downside.reduce((s, e) => s + e * e, 0) / downside.length;
      const dsSd = Math.sqrt(dsVar);
      if (dsSd > 0) sortino = +(mean / dsSd * Math.sqrt(12)).toFixed(3);
    }
  }

  // Max drawdown over the windowed FILTERED values.  Resets the
  // running peak at window start so a high pre-window peak doesn't
  // dominate.  For the LIFETIME window, mirror Python's
  // drawdown.compute_drawdown small-base rule: drawdowns whose peak
  // was below 5% of the filtered all-time peak are excluded — an
  // early-portfolio crash on a tiny base isn't comparable to today's
  // risk profile (the Calmar tooltip has always described this rule;
  // the JS recompute previously skipped it and disagreed with the
  // Python Drawdown section on the same tab).  Shorter windows skip
  // the gate, same as Python's windowed slices.
  const ddSmallBase = windowKey === 'lifetime' ? filteredPeak * 0.05 : 0;
  let runningPeak = 0;
  let curPeakDate = windowed[0].date;
  let mdd = 0, mddPeak = null, mddTrough = null;
  for (const h of windowed) {
    const v = valueAt(h);
    if (v >= runningPeak) {
      runningPeak = v;
      curPeakDate = h.date;
    } else if (runningPeak > 0) {
      const dd = (v - runningPeak) / runningPeak;
      if (dd < mdd && runningPeak >= ddSmallBase) {
        mdd = dd;
        mddPeak = curPeakDate;
        mddTrough = h.date;
      }
    }
  }
  const mddPct = mdd < 0 ? +(mdd * 100).toFixed(2) : 0;
  const calmar = (ann != null && mddPct < -0.01)
    ? (ann * 100) / Math.abs(mddPct)
    : null;

  return {
    cum, ann, sharpe, sortino,
    mdd: mddPct, mddPeak, mddTrough,
    calmar,
    nMonths: periodReturns.length,
    nInRatio: sigReturns.length,
    startDate, endDate, spyCum, spyAnn,
  };
}

function renderPerformance() {
  const root = document.getElementById('performanceContent');
  if (!root) return;

  // Annual returns table (filtered to the selected account).  The
  // earlier "Total" version of this table was removed — it duplicated
  // the same data shown when this filter is set to Total.
  // Drop leading years the filter didn't exist for.  They render as a
  // row of $0.00 with a real SPY percentage beside them, which reads
  // as "this account flatlined while the market compounded" — the same
  // unpaired-comparison trap as the benchmark cards, one column over.
  // Only strictly-empty years go (no value at either end, no flows),
  // so a real year that merely round-trips to zero is kept.
  const annualByAcct = (() => {
    const rows = computeAnnualReturns(performanceAccountFilter);
    let i = 0;
    while (i < rows.length && !rows[i].start && !rows[i].end && !rows[i].net) i++;
    return rows.slice(i);
  })();
  const annualTwrByAcct = computeAnnualTWR(performanceAccountFilter);
  const positions = computePositionReturns();

  // Available account groups for the selector
  const availableAccounts = [...new Set(holdingsByAccount.map(h => h.account_group).filter(Boolean))].sort();

  // Account-group selector pills — single source of truth, used by
  // the centralized control bar at the top of the tab.  Defined
  // BEFORE statsHtml is built (which references ${acctPills} in a
  // template literal that evaluates immediately).
  //
  // Order: Total → Investments → Taxable → Retirement → individual
  // groups.  Type-aggregate buttons cluster together at the front;
  // individual account_groups follow alphabetically.  Investments
  // and Taxable each show only when there's more than one group in
  // their bucket (otherwise they'd just duplicate a single pill).
  const _retirementActive = performanceAccountFilter === '__retirement__';
  const _taxableActive = performanceAccountFilter === '__taxable__';
  const _investmentsActive = performanceAccountFilter === '__investments__';
  const _hasSavingsAccount = availableAccounts.some(a => SAVINGS_GROUP_SET.has(a));
  const _taxableCount = availableAccounts.filter(a => TAXABLE_GROUP_SET.has(a)).length;
  // Aggregate chips ("Investments", "Taxable", "Retirement") use a
  // colored text style sourced from TYPE_COLORS for consistency with
  // individual account chips below.  Active state still uses the
  // shared .tbtn.active purple background.
  const _aggChip = (key, label, active, color, title) => {
    const titleAttr = title ? ` title="${_htmlEsc(title)}"` : '';
    const styleAttr = (!active && color) ? ` style="color:${color};"` : '';
    return `<button class="tbtn${active ? ' active' : ''}"${styleAttr}${titleAttr} onclick="setPerformanceAccountFilter('${key}')">${label}</button>`;
  };
  const acctPills = [
    `<button class="tbtn${performanceAccountFilter === null ? ' active' : ''}" onclick="setPerformanceAccountFilter(null)">Total</button>`,
    ...(_hasSavingsAccount ? [
      _aggChip('__investments__', 'Investments', _investmentsActive, '#4ade80'),
    ] : []),
    ...(_taxableCount > 1 ? [
      _aggChip('__taxable__', 'Taxable', _taxableActive,
        TYPE_COLORS.Taxable || '#fbbf24',
        'Combined view of after-tax accounts (Robinhood + Coinbase, etc.).  Money moves freely between them — no contribution limits or withdrawal penalties like retirement accounts have.'),
    ] : []),
    _aggChip('__retirement__', 'Retirement', _retirementActive,
      TYPE_COLORS.Retirement || '#a78bfa'),
    ...availableAccounts.map(a => {
      const escaped = a.replace(/'/g, "\\'");
      return _renderAccountChip(a, performanceAccountFilter === a,
        `setPerformanceAccountFilter('${escaped}')`);
    }),
  ].join('');

  // Top-row anchor cards: WHOLE PORTFOLIO, LIFETIME — never filtered
  // by anything.  Always shows the same numbers as the Top bar and
  // Overview tab, giving the user a fixed reference point above the
  // toggles.
  // Same authoritative source as the Overview's Realized card — the
  // annotated walker's own accumulator, not a re-sum of the cent-rounded
  // per-txn annotations.  Two cards showing one quantity should read one
  // field; they used to derive it independently and disagreed (F-033).
  const _whole_realized = basisTotals.realized_gain != null
    ? basisTotals.realized_gain
    : txns.reduce((s, t) => s + (t.realized_gain || 0), 0);
  const _whole_unrealized = holdingsByAccount.reduce(
    (s, h) => s + (h.unrealized_gain || 0), 0);
  // Value / net contributed / total return come precomputed from
  // analytics/header.py — the same fields the Top bar renders — so the
  // anchor card can never disagree with the header by a rounding cent.
  // Fallback: derive locally from holdings + cash summary.
  const _hs = ANALYTICS.header_summary || {};
  const _whole_value = _hs.value != null ? _hs.value
    : holdingsByAccount.reduce(
        (s, h) => s + (typeof h.value === 'number' ? h.value : 0), 0);
  const _whole_netContrib = _hs.net_contributed != null
    ? _hs.net_contributed : (cashSummary.net_contributed || 0);
  const _whole_totalReturn = _hs.total_return != null
    ? _hs.total_return : (_whole_value - _whole_netContrib);
  const _whole_totalReturnPct = _hs.total_return_pct != null
    ? _hs.total_return_pct * 100
    : (_whole_netContrib > 0
        ? (_whole_totalReturn / _whole_netContrib) * 100 : null);

  // Bottom-row aggregate stats: filtered by the active account AND
  // bounded by the active window.  Dollar P&L over the window, not
  // lifetime.  When window=lifetime + filter=null, these match the
  // top-row numbers exactly.
  const _aggFilterSet = _resolveAccountFilter(performanceAccountFilter);
  const _aggMatchesTxn = _aggFilterSet
    ? (t) => _aggFilterSet.has(t.account_group)
    : (_t) => true;
  const _aggMatchesHolding = _aggFilterSet
    ? (h) => _aggFilterSet.has(h.account_group)
    : (_h) => true;
  // Lifetime + unfiltered: every dollar figure in this row then
  // describes EXACTLY the quantity its twin in the anchor row above
  // describes.  So it reads the anchor's field rather than deriving
  // the same number a second way.
  //
  // Deriving it twice is not free.  The per-txn `realized_gain` /
  // `cash_flow` annotations are rounded to cents for readability, so
  // re-adding thousands of them lands a cent or two off the walker's
  // own accumulator — which is why CLAUDE.md says a published basis
  // figure never comes from the re-sum, and why the Transactions and
  // Crypto tabs read `basis_totals` with the re-sum kept only as a
  // legacy fallback.  This row was the last place still re-summing as
  // its primary path, and it put two adjacent cards under one label a
  // cent apart.  The drift scales with fill count, so it does not stay
  // a cent.
  //
  // Real windows still have to sum the annotations — "realized within
  // 3mo" is not recoverable from a lifetime accumulator.  The point is
  // only that the all-time case must not.
  const _isWholeLifetime = performanceWindow === 'lifetime'
    && performanceAccountFilter === null;
  // Window bounds — same logic as everywhere else on the tab.
  const _winRefIso = history.length ? history[history.length - 1].date : '';
  let _winLowerIso = '', _winUpperIso = _winRefIso;
  if (performanceWindow === 'custom') {
    _winLowerIso = perfTwrStart || '';
    _winUpperIso = perfTwrEnd || _winRefIso;
  } else if (performanceWindow !== 'lifetime') {
    _winLowerIso = _windowCutoffIso(performanceWindow, _winRefIso);
  }
  // ANCHOR the window on a snapshot we actually have, and measure every
  // dollar figure in this row from THAT date.
  //
  // The nominal cutoff almost never coincides with a snapshot — history
  // is semimonthly — so "value at the window start" has to resolve to a
  // neighbour, and the choice has to agree with the flow window or the
  // Modified Dietz numerator (end − start − flows) counts the same money
  // twice.  It did: the start value was read from the first snapshot AT
  // OR AFTER the cutoff while flows were counted from the cutoff, so
  // every deposit in the gap landed on both sides.  Usually that gap is
  // one snapshot period and the error is invisible.  When the window
  // reaches back past the portfolio's first snapshot the gap swallows
  // the founding deposit, and a 5y view of a 4-year-old portfolio
  // printed a LOSS next to a +57% cumulative return (AUDIT.md F-032).
  //
  // So: anchor = the last snapshot at or before the cutoff, and flows
  // are counted strictly after the anchor's own date.  No snapshot that
  // early means the portfolio did not exist yet — start value 0, flows
  // from inception, and the window correctly collapses to lifetime.
  // Anchoring BACKWARD rather than forward also matches the benchmark
  // chart, which prepends the pre-cutoff snapshot for the same reason.
  let _winAnchorIso = '';
  let _winStartValue = 0;
  if (_winLowerIso) {
    let anchor = null;
    for (const h of history) {
      if ((h.date || '') <= _winLowerIso) anchor = h; else break;
    }
    if (anchor) _winAnchorIso = anchor.date || '';
  }
  // Realized in window: sum per-txn realized_gain on filtered txns
  // where date is within (_winAnchorIso, _winUpperIso].  A close at
  // the very start of the window doesn't count toward window-period
  // realized — it was banked before the window opened.
  const totalRealized = _isWholeLifetime ? _whole_realized : txns.reduce((s, t) => {
    if (!_aggMatchesTxn(t)) return s;
    const d = t.date || '';
    if (_winAnchorIso && d <= _winAnchorIso) return s;
    if (_winUpperIso && d > _winUpperIso) return s;
    return s + (t.realized_gain || 0);
  }, 0);
  // Portion of that realized figure that comes from custodial-rollover
  // liquidations (Distribution rows on IRA groups): when a custodian
  // sells everything to wire the account elsewhere, FIFO books the
  // market-value-vs-basis gap as "realized" on that one day.  That's
  // correct lot bookkeeping but has no tax meaning inside the wrapper
  // and reads as "I lost $X trading" — the recovery lives in the
  // replacement lots' unrealized.  Surfaced as context on the card
  // when it dominates the number.
  const _ROLLOVER_GROUPS_JS = new Set(['Roth IRA', 'Rollover IRA']);
  let rolloverRealized = 0;
  const rolloverDates = new Set();
  for (const t of txns) {
    if (!_aggMatchesTxn(t)) continue;
    const d = t.date || '';
    if (_winAnchorIso && d <= _winAnchorIso) continue;
    if (_winUpperIso && d > _winUpperIso) continue;
    if (t.action !== 'Distribution') continue;
    if (!_ROLLOVER_GROUPS_JS.has(t.account_group)) continue;
    if (!t.realized_gain) continue;
    rolloverRealized += t.realized_gain;
    if (d) rolloverDates.add(d);
  }
  const rolloverDominates = Math.abs(rolloverRealized) >= 100
    && Math.abs(rolloverRealized) >= Math.abs(totalRealized) * 0.5;
  // Net contributed in window: sum per-txn cash_flow over the same
  // filter+window.
  const netContrib = _isWholeLifetime ? _whole_netContrib : txns.reduce((s, t) => {
    if (!_aggMatchesTxn(t)) return s;
    const d = t.date || '';
    if (_winAnchorIso && d <= _winAnchorIso) return s;
    if (_winUpperIso && d > _winUpperIso) return s;
    return s + (t.cash_flow || 0);
  }, 0);
  // For unrealized / total value at window END, prefer snapshot
  // positions when window != lifetime so we get the as-of-window-end
  // values; for lifetime, use live holdings_by_account so the figure
  // matches the rest of the dashboard exactly.
  let totalUnrealized = 0, totalValue = 0;
  if (_isWholeLifetime) {
    totalUnrealized = _whole_unrealized;
    totalValue = _whole_value;
  } else {
    // Find the snapshot at upper bound and compute filtered value/unrealized
    const endSnap = history.find(h => h.date === _winUpperIso) ||
      history[history.length - 1];
    if (endSnap && Array.isArray(endSnap.positions)) {
      for (const p of endSnap.positions) {
        if (_aggFilterSet && !_aggFilterSet.has(p.account_group)) continue;
        if (typeof p.value === 'number') totalValue += p.value;
        if (typeof p.value === 'number' && typeof p.cost_basis === 'number') {
          totalUnrealized += (p.value - p.cost_basis);
        }
      }
    } else {
      // Fallback to live holdings (lifetime + filter)
      totalUnrealized = holdingsByAccount.reduce(
        (s, h) => s + (_aggMatchesHolding(h) ? (h.unrealized_gain || 0) : 0), 0);
      totalValue = holdingsByAccount.reduce(
        (s, h) => s + (_aggMatchesHolding(h) && typeof h.value === 'number' ? h.value : 0), 0);
    }
  }
  // Total Return $ over the window for the filter: dollar gain net of
  // capital flows.  Equivalent to the Modified Dietz numerator — the
  // "money my investments made" answer for the window.
  if (_winAnchorIso) {
    const anchorSnap = history.find(h => h.date === _winAnchorIso);
    if (anchorSnap && Array.isArray(anchorSnap.positions)) {
      for (const p of anchorSnap.positions) {
        if (_aggFilterSet && !_aggFilterSet.has(p.account_group)) continue;
        if (typeof p.value === 'number') _winStartValue += p.value;
      }
    }
  }
  const totalReturn = _isWholeLifetime
    ? _whole_totalReturn
    : (totalValue - _winStartValue - netContrib);
  // Pct of net_contrib only meaningful when net_contrib > 0
  const totalReturnPct = netContrib > 0
    ? (totalReturn / netContrib) * 100 : null;

  // Window-aware metrics — single source of truth, filter-aware.
  // ``computeWindowedMetrics(filterKey, windowKey)`` is shared with
  // the By Account TWR section so every figure on the Performance tab
  // (top stat cards + the table below) flows from the same primitive.
  // Avoids drift between e.g. "1y / Coinbase" computed two different
  // ways producing two different numbers.
  const win = computeWindowedMetrics(performanceAccountFilter, performanceWindow);
  const filteredHint = ' Sharpe/Sortino require ≥6 months of data with start balance ≥ 1% of all-time peak; tiny-base early months are excluded so the stdev isn\'t dominated by percent swings on a near-zero denominator.';
  const cumStr = win.cum != null ? ((win.cum >= 0 ? '+' : '') + (win.cum * 100).toFixed(2) + '%') : '—';
  const annStr = win.ann != null ? ((win.ann >= 0 ? '+' : '') + (win.ann * 100).toFixed(2) + '%') : '—';
  const sharpeStr = win.sharpe != null ? win.sharpe.toFixed(2) : '—';
  const sortinoStr = win.sortino != null ? win.sortino.toFixed(2) : '—';
  const mddStr = win.mdd != null && win.mdd < 0 ? win.mdd.toFixed(2) + '%' : '—';
  const calmarStr = win.calmar != null ? ((win.calmar >= 0 ? '+' : '') + win.calmar.toFixed(2)) : '—';
  const cumCls = win.cum != null ? (win.cum >= 0 ? 'positive' : 'negative') : '';
  const annCls = win.ann != null ? (win.ann >= 0 ? 'positive' : 'negative') : '';
  const sharpeCls = win.sharpe != null ? (win.sharpe >= 1 ? 'positive' : (win.sharpe < 0 ? 'negative' : '')) : '';
  const sortinoCls = win.sortino != null ? (win.sortino >= 1 ? 'positive' : (win.sortino < 0 ? 'negative' : '')) : '';
  const mddCls = win.mdd != null && win.mdd < 0 ? 'negative' : '';
  const calmarCls = win.calmar != null ? (win.calmar >= 1 ? 'positive' : (win.calmar < 0 ? 'negative' : '')) : '';

  const calmarTitle = win.calmar != null
    ? (
      `${(win.ann * 100).toFixed(2)}% annualized return ÷ ${Math.abs(win.mdd).toFixed(1)}% max drawdown ` +
      `(${win.mddPeak || '?'} → ${win.mddTrough || '?'}) = ${win.calmar.toFixed(3)}.\n\n` +
      `Calmar measures return per unit of worst-case pain.  > 1 is solid, > 3 is exceptional, ` +
      `< 0.5 means drawdowns swamp returns.\n\n` +
      `Window: ${performanceWindow}.  Lifetime drawdowns from when the portfolio was below 5% of all-time peak are excluded.`
    )
    : 'Annualized return ÷ |max drawdown|.';

  const windowChips = PERF_TWR_PRESETS.map(w =>
    `<button class="tbtn ${w === performanceWindow ? 'active' : ''}" data-perf-window="${w}">${PERF_TWR_PRESET_LABEL[w]}</button>`
  ).join('');
  // Custom date inputs — only render when 'custom' window is active.
  // Editing either input is wired to setPerfTwrStart / setPerfTwrEnd
  // which re-flips the window to 'custom' (keeps the chip in sync).
  const _customActive = performanceWindow === 'custom';
  const _perfWindowMin = history.length ? history[0].date : '';
  const _perfWindowMax = history.length ? history[history.length - 1].date : '';
  const _customInputsHtml = _customActive ? `
    <span class="hist-label" style="margin-left:10px;">From</span>
    <input type="date" class="hist-date" min="${_perfWindowMin}" max="${_perfWindowMax}"
           value="${perfTwrStart || ''}" onchange="setPerfTwrStart(this.value)">
    <span class="hist-label">To</span>
    <input type="date" class="hist-date" min="${_perfWindowMin}" max="${_perfWindowMax}"
           value="${perfTwrEnd || ''}" onchange="setPerfTwrEnd(this.value)">` : '';

  const _totalReturnTitle = 'Current portfolio value minus net contributed (deposits − withdrawals).  Same formula as the Top bar and the Overview tab — the "did I make money?" answer.\n\nIt is NOT Realized + Unrealized, and no simple sum reaches it. Sale proceeds get redeployed into new positions, so a dollar of gain can end up inside the cost basis of something you still hold rather than in either figure. Income arrives as cash without being a realized gain on any lot, and cash outside Savings accounts is not in the holdings value.\n\nTreat Realized and Unrealized as two views of the portfolio, not two halves of this number.';

  // Row 1 — fixed anchor: WHOLE PORTFOLIO, ALL-TIME.  Doesn't react
  // to any toggles.  Gives the user a stable reference point above
  // the toggles so they can compare the windowed view (row 4) to
  // their full portfolio's lifetime numbers at a glance.
  // The headline answer, on its own row.  It used to sit in a flat line
  // of five peers, which invites the reader to add Realized + Unrealized
  // and wonder why the total is wrong (F-019).  No clean sum reaches it —
  // see _totalReturnTitle — so the fix is to stop the cards LOOKING like
  // addends, and to show visibly (not on hover) the one identity that
  // does hold exactly.
  const anchorHeadline = [
    {
      label: 'Total Return',
      value: fmtSigned(_whole_totalReturn) + (_whole_totalReturnPct != null ? ` <span class="sub">${(_whole_totalReturnPct >= 0 ? '+' : '') + _whole_totalReturnPct.toFixed(1)}%</span>` : ''),
      cls: _whole_totalReturn >= 0 ? 'positive' : 'negative',
      note: `${fmtMoney(_whole_value)} value − ${fmtMoney(_whole_netContrib)} contributed`,
      title: _totalReturnTitle
    },
  ];

  const anchorCards = [
    { label: 'Realized', value: fmtSigned(_whole_realized) },
    { label: 'Unrealized', value: fmtSigned(_whole_unrealized) },
    { label: 'Net Contributed', value: fmtMoney(_whole_netContrib) },
    (() => {
      // Lifetime broker fees/spread — parsed on every txn, aggregated
      // in analytics.fees.  Tooltip carries the per-account breakdown.
      const fees = ANALYTICS.fees || {};
      const byAcct = fees.by_account || {};
      const breakdown = Object.entries(byAcct)
        .map(([a, v]) => `${a}: ${fmtMoney(v)}`).join('\n');
      return {
        label: 'Fees Paid',
        value: fmtMoney(fees.total || 0),
        title: 'Lifetime broker fees / spread across all transactions.'
          + (breakdown ? '\n\n' + breakdown : ''),
      };
    })(),
  ];

  // Row 4 — same metrics, but filtered + windowed.  Plus Cumulative
  // and Annualized Return so the dollar and percentage views sit
  // side by side.
  const _winLabel = performanceWindow;
  const totalReturnCls = totalReturn >= 0 ? 'positive' : (totalReturn < 0 ? 'negative' : '');
  const _filterWord = performanceAccountFilter === null ? ''
    : (performanceAccountFilter === '__investments__' ? 'investments'
      : performanceAccountFilter === '__retirement__' ? 'retirement'
        : performanceAccountFilter === '__taxable__' ? 'taxable'
          : performanceAccountFilter.toLowerCase());
  // Every card in this row is measured over the SAME anchored span.
  // Saying so on each of them is what lets a reader check that the
  // dollar figures and the return figures describe one period.
  const _rowSpan = `Measured ${_winAnchorIso || 'inception'} to ${_winUpperIso}.`;
  const filteredCards = [
    {
      label: `Total Return <span class="sub">${_winLabel}</span>`,
      value: fmtSigned(totalReturn) + (totalReturnPct != null ? ` <span class="sub">${(totalReturnPct >= 0 ? '+' : '') + totalReturnPct.toFixed(1)}%</span>` : ''),
      cls: totalReturnCls,
      title: `Dollar return over the window for the active filter — Modified Dietz numerator: end value − start value − net cash flow.  Window: ${performanceWindow}.

${_rowSpan}`
    },
    {
      label: `Realized <span class="sub">${_winLabel}</span>`
        + (rolloverDominates ? ' <span class="sub" style="color:var(--yellow);">incl. rollover</span>' : ''),
      value: fmtSigned(totalRealized),
      cls: totalRealized >= 0 ? 'positive' : (totalRealized < 0 ? 'negative' : ''),
      title: `Realized gains for the active filter, on txns dated within the window.  Window: ${performanceWindow}.

${_rowSpan}`
        + (rolloverDominates
          ? `\n\n${fmtSigned(rolloverRealized)} of this is the custodial-rollover liquidation`
            + ` (${[...rolloverDates].sort().join(', ')}) — the custodian sold everything to`
            + ` transfer the account, crystallizing market-value-vs-contributions on that day.`
            + ` Bookkeeping, not a taxable event (retirement wrapper); the recovery since`
            + ` shows up as UNREALIZED gain on the replacement lots, so this figure stays`
            + ` fixed no matter how the account performs.`
          : '')
    },
    {
      // The one card in this row that is a LEVEL, not a flow — so it
      // is labelled with the date it is measured AT, never with the
      // window.  Every trailing window ends on the same day, so the
      // figure is identical across lifetime / 5y / 3mo and only the
      // account filter (or a custom end date) moves it.  Labelled
      // `3mo` it read as "unrealized accrued over three months" and
      // invited the reasonable question of why it never changed.
      // Same reason the Options tab keeps Open Contracts out of its
      // windowed row.
      label: `Unrealized <span class="sub">${_filterWord ? _filterWord + ' · ' : ''}as of ${_winUpperIso}</span>`,
      value: fmtSigned(totalUnrealized),
      cls: totalUnrealized >= 0 ? 'positive' : (totalUnrealized < 0 ? 'negative' : ''),
      title: `Unrealized P&L on positions still held on ${_winUpperIso}${_filterWord ? ', ' + _filterWord + ' only' : ''}.

This is a LEVEL measured at one date, not a gain accrued over the window — so unlike its neighbours it does NOT move when you change the window.  Every trailing window ends today; only a custom window with an earlier end date, or the account filter, changes it.`
    },
    {
      label: `Net Contributed <span class="sub">${_winLabel}</span>`,
      value: fmtMoney(netContrib),
      title: `Net cash flow into the filtered account(s) during the window (deposits − withdrawals).  Window: ${performanceWindow}.

${_rowSpan}`
    },
    {
      label: `Cumulative Return <span class="sub">${performanceWindow}</span>`,
      value: cumStr,
      cls: cumCls,
      title: `Cumulative return over the selected window (geometric chain-link of monthly returns).  Window: ${performanceWindow}.`
    },
    {
      label: `Annualized Return <span class="sub">${performanceWindow}</span>`,
      value: annStr,
      cls: annCls,
      title: `Annualized return: cumulative return scaled to per-year using the actual months covered.  Window: ${performanceWindow}.`
    },
  ];

  // Row 5 — risk-adjusted ratios + max drawdown.  Same window/filter.
  const ratioCards = [
    {
      label: `Sharpe Ratio <span class="sub">${performanceWindow}</span>`,
      value: sharpeStr + ' <span class="sub">vs 4% rf</span>',
      cls: sharpeCls,
      title: `Annualized risk-adjusted return: (mean monthly excess return) / stdev × √12.  > 1 is solid, > 2 is great.  Window: ${performanceWindow}.` + filteredHint
    },
    {
      label: `Sortino Ratio <span class="sub">${performanceWindow}</span>`,
      value: sortinoStr + ' <span class="sub">downside-only</span>',
      cls: sortinoCls,
      title: `Like Sharpe, but only counts downside volatility (months below the risk-free return).  Closer to "how much pain per unit of return".  Window: ${performanceWindow}.` + filteredHint
    },
    {
      label: `Calmar Ratio <span class="sub">${performanceWindow}</span>`,
      value: calmarStr + ' <span class="sub">return / max DD</span>',
      cls: calmarCls,
      title: calmarTitle
    },
    {
      label: `Max Drawdown <span class="sub">${performanceWindow}</span>`,
      value: mddStr + (win.mddPeak ? ` <span class="sub">${win.mddPeak}→${win.mddTrough}</span>` : ''),
      cls: mddCls,
      title: `Largest peak-to-trough decline within the selected window.  Window: ${performanceWindow}.`
    },
  ];

  const _renderCardRow = (cards) => _renderStatCards(cards);

  // Layout:
  //   Row 1. Whole-portfolio lifetime cards (anchor; toggle-independent)
  //   Row 2. Account selector
  //   Row 3. Window selector (+ custom-date inputs when applicable)
  //   Row 4. Same 4 metrics + Cumulative/Annualized — filtered + windowed
  //   Row 5. Risk-adjusted ratios + Max Drawdown — filtered + windowed
  const statsHtml =
    `<div class="perf-anchor-label" style="color:var(--text-dim);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Whole portfolio · all-time</div>` +
    _renderCardRow(anchorHeadline) +
    `<div class="stats-caption">The figures below are separate views of the portfolio — they do <strong>not</strong> add up to Total Return. Proceeds from a sale get redeployed, so a gain can end up inside the cost basis of a position you still hold rather than in either one.</div>` +
    _renderCardRow(anchorCards) +
    // Both toggle rows live inside a single card so they read as one
    // grouped control surface (Account on top, Window below).  Shared
    // .toggles-card / .toggles-row / .toggles-label classes are used
    // across the Performance, Options, and Tax tabs.
    `<div class="toggles-card">` +
    `<div class="toggles-row">` +
    `<span class="toggles-label">Account:</span>` +
    `<div class="toggle-group" style="flex-wrap:wrap;">${acctPills}</div>` +
    `</div>` +
    `<div class="toggles-row">` +
    `<span class="toggles-label">Window:</span>` +
    `<div class="toggle-group">${windowChips}</div>` +
    _customInputsHtml +
    `<span class="toggles-hint">${win.nMonths || 0} months · ${win.nInRatio || 0} in ratio calc</span>` +
    `</div>` +
    `</div>` +
    _renderCardRow(filteredCards) +
    _renderCardRow(ratioCards);

  // Annual returns — row builder (reused for Total and per-account tables)
  const annualRow = (r, twrMap) => {
    const twr = twrMap ? twrMap[r.year] : null;
    const twrCls = twr == null ? '' : (twr >= 0 ? 'positive' : 'negative');
    const twrStr = twr == null ? '—'
      : `<span class="${twrCls}">${twr >= 0 ? '+' : ''}${(twr * 100).toFixed(2)}%</span>`;
    const dollCls = r.dollar_return >= 0 ? 'positive' : 'negative';
    const dollStr = `<span class="${dollCls}">${fmtSigned(r.dollar_return)}</span>`;
    let spyCell;
    if (r.spy_pct == null) spyCell = '<td class="num">—</td>';
    else {
      const cls = r.spy_pct >= 0 ? 'positive' : 'negative';
      spyCell = `<td class="num"><span class="${cls}">${r.spy_pct >= 0 ? '+' : ''}${r.spy_pct.toFixed(2)}%</span></td>`;
    }
    return `<tr>
      <td><b>${r.year}</b></td>
      <td class="num">${fmtMoney(r.start)}</td>
      <td class="num">${fmtMoney(r.end)}</td>
      <td class="num">${fmtMoney(r.net)}</td>
      <td class="num">${dollStr}</td>
      <td class="num">${twrStr}</td>
      ${spyCell}
    </tr>`;
  };
  const annualByAcctRows = annualByAcct.map(r => annualRow(r, annualTwrByAcct)).join('');

  // TWR summary for the active (filter, window) — resolved through
  // the same single-source-of-truth ``performanceWindow`` state used
  // elsewhere on the tab.
  //   - 'lifetime' → natural window (first meaningful snapshot → latest)
  //   - 'custom'   → honor perfTwrStart / perfTwrEnd as-is
  //   - any other preset → trailing-N-months from latest snapshot
  const _latestHistDate = history.length ? history[history.length - 1].date : null;
  let _twrWindowStart = null, _twrWindowEnd = null;
  if (performanceWindow === 'custom') {
    _twrWindowStart = perfTwrStart;
    _twrWindowEnd = perfTwrEnd;
  } else if (performanceWindow !== 'lifetime' && _latestHistDate) {
    _twrWindowStart = _windowCutoffIso(performanceWindow, _latestHistDate);
    _twrWindowEnd = _latestHistDate;
  }
  const perfWindowCustom = _twrWindowStart != null || _twrWindowEnd != null;
  const acctTwr = perfWindowCustom
    ? computeTimeWeightedReturnForWindow(performanceAccountFilter, _twrWindowStart, _twrWindowEnd)
    : computeTimeWeightedReturn(performanceAccountFilter);
  const acctSpy = acctTwr ? computeSPYReturnOverPeriod(acctTwr.start_date, acctTwr.end_date) : null;
  const fmtPctSigned = (v, digits = 2) => v == null ? '—'
    : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%';
  const acctLabel = performanceAccountFilter === null ? 'Total Portfolio'
    : performanceAccountFilter === '__investments__' ? 'Investments (excl. Savings)'
      : performanceAccountFilter === '__retirement__' ? 'Retirement (combined)'
        : performanceAccountFilter === '__taxable__' ? 'Taxable (combined)'
          : performanceAccountFilter;

  // Daily TWR (Schwab-style) — precomputed server-side for retirement
  // filters only (see analytics.py::compute_twr_daily_summary for why).
  // Covers the natural window; falls back to null for custom windows
  // since we don't have daily precomputes for arbitrary bounds.
  const analyticsFilt = _analyticsFilterName(performanceAccountFilter);
  const acctDaily = (!perfWindowCustom && ANALYTICS_PERF[analyticsFilt])
    ? (ANALYTICS_PERF[analyticsFilt].summary_daily || null)
    : null;

  const twrLabelSuffix = acctDaily ? ' — TWR (Mod. Dietz)' : ' — TWR';
  const acctSummaryCards = [
    {
      label: acctLabel + twrLabelSuffix,
      value: acctTwr ? fmtPctSigned(acctTwr.cumulative) +
        (acctTwr.annualized != null ? ` <span class="sub">ann. ${fmtPctSigned(acctTwr.annualized)}</span>` : '')
        : '—',
      cls: acctTwr && acctTwr.cumulative >= 0 ? 'positive' : (acctTwr ? 'negative' : '')
    },
  ];
  // Only retirement filters carry a precomputed daily TWR.  Show it
  // alongside the Modified Dietz card when available so the user can
  // see both methods at once; differences of a percentage point or so
  // are the expected Dietz-vs-daily approximation gap.
  if (acctDaily) {
    acctSummaryCards.push({
      label: acctLabel + ' — Rate of Return (Daily)',
      value: fmtPctSigned(acctDaily.cumulative) +
        (acctDaily.annualized != null
          ? ` <span class="sub">ann. ${fmtPctSigned(acctDaily.annualized)}</span>`
          : ''),
      cls: acctDaily.cumulative >= 0 ? 'positive' : 'negative',
    });
  }
  // Money-weighted return (XIRR) — precomputed server-side over the
  // natural window per filter.  TWR measures the portfolio; XIRR
  // measures what the user's DOLLARS earned, contribution timing
  // included.  The gap between them is the "behavior gap".
  const acctXirr = (!perfWindowCustom && performanceWindow === 'lifetime'
    && ANALYTICS_PERF[analyticsFilt])
    ? (ANALYTICS_PERF[analyticsFilt].money_weighted || null)
    : null;
  if (acctXirr && acctXirr.annualized != null) {
    const gapNote = (acctTwr && acctTwr.annualized != null)
      ? (acctXirr.annualized >= acctTwr.annualized
        ? 'Above TWR: your contribution timing HELPED.'
        : 'Below TWR: your contribution timing HURT (money tended to arrive before dips).')
      : '';
    acctSummaryCards.push({
      label: acctLabel + ' — Money-Weighted (XIRR)',
      value: fmtPctSigned(acctXirr.annualized) + ' <span class="sub">ann.</span>',
      cls: acctXirr.annualized >= 0 ? 'positive' : 'negative',
      title: 'What YOUR dollars earned per year, contribution timing included '
        + `(TWR deliberately ignores timing).  ${gapNote}\n`
        + `Window: ${acctXirr.start_date} → ${acctXirr.end_date}; `
        + `total invested ${fmtMoney(acctXirr.total_invested)}.`,
    });
  }
  acctSummaryCards.push(
    {
      label: 'SPY — same period',
      value: acctSpy ? fmtPctSigned(acctSpy.cumulative) +
        (acctSpy.annualized != null ? ` <span class="sub">ann. ${fmtPctSigned(acctSpy.annualized)}</span>` : '')
        : '—',
      cls: acctSpy && acctSpy.cumulative >= 0 ? 'positive' : (acctSpy ? 'negative' : '')
    },
    {
      label: 'Vs SPY (annualized)',
      value: (acctTwr && acctSpy && acctTwr.annualized != null && acctSpy.annualized != null)
        ? fmtPctSigned(acctTwr.annualized - acctSpy.annualized) + ' <span class="sub">pts/yr</span>'
        : '—',
      cls: (acctTwr && acctSpy && acctTwr.annualized != null && acctSpy.annualized != null
        && acctTwr.annualized > acctSpy.annualized) ? 'positive'
        : ((acctTwr && acctSpy && acctTwr.annualized != null && acctSpy.annualized != null) ? 'negative' : '')
    },
    {
      label: 'Period',
      value: acctTwr ? `${acctTwr.start_date} → ${acctTwr.end_date}` : '—'
    },
  );
  const acctSummaryHtml = '<div class="stats">' + acctSummaryCards.map(c => {
    const cls = c.cls ? `stat-card ${c.cls}` : 'stat-card';
    const titleAttr = c.title ? ` title="${_htmlEsc(c.title)}"` : '';
    return `<div class="${cls}"${titleAttr}><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('') + '</div>';

  // No per-section window/account selectors anymore — both live at
  // the top of the Performance tab and drive every figure below.

  // --- Strategy vs SPY Benchmark ---
  // (acctPills already built above, just after availableAccounts.)

  // Resolve the active account filter to a Set of account_group
  // strings (or null = total).  The Benchmark chart and the By Account
  // TWR section share this state so toggling on either side updates
  // both.  All four lines (Portfolio + SPY/BND/VXUS) become
  // filter-aware when the filter is non-null.
  const benchFilterSet = _resolveAccountFilter(performanceAccountFilter);
  const benchFilterActive = benchFilterSet != null;
  // Per-snapshot value-getter for the portfolio line: sums
  // by_account_group entries when filtered, else uses h.total.
  // Coinbase USD bridge stays included since it's part of by_account_group.
  const portfolioValueAt = (h) => {
    if (!benchFilterSet) return h.total || 0;
    if (!h.by_account_group) return 0;
    let s = 0;
    for (const g of benchFilterSet) s += h.by_account_group[g] || 0;
    return s;
  };
  // Per-txn predicate matching the filter — for filter-aware
  // benchmark simulations and net-contributed totals.
  const benchTxnFilter = benchFilterActive
    ? (t) => benchFilterSet.has(t.account_group)
    : null;
  // Cumulative net_contributed for the filter at each snapshot date.
  // Walks once, joins by date.
  const filteredNetContribAt = (() => {
    if (!benchFilterActive) {
      return (h) => h.net_contributed || 0;
    }
    const sortedTxns = [...txns].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    const cache = new Map();
    return (h) => {
      if (cache.has(h.date)) return cache.get(h.date);
      let s = 0;
      for (const t of sortedTxns) {
        if ((t.date || '') > h.date) break;
        if (!benchTxnFilter(t)) continue;
        if (typeof t.cash_flow === 'number') s += t.cash_flow;
      }
      cache.set(h.date, s);
      return s;
    };
  })();

  // Slice the history to the active performance window so the chart
  // and stat cards both reflect the user's current selector.  Helpers
  // resolve a window key to a chronological cutoff ISO date.
  let windowedHistory = history;
  if (performanceWindow !== 'lifetime' && history.length) {
    const cutoffIso = _windowCutoffIso(performanceWindow,
      history[history.length - 1].date);
    windowedHistory = history.filter(h => (h.date || '') >= cutoffIso);
    // Always include at least the snapshot just BEFORE cutoff as the
    // anchor — otherwise the chart starts at the first in-window
    // point with no "before" reference.
    if (windowedHistory.length && windowedHistory[0] !== history[0]) {
      const idx = history.indexOf(windowedHistory[0]);
      if (idx > 0) windowedHistory = [history[idx - 1], ...windowedHistory];
    }
  }

  // Build series from the WINDOWED history.  Three core series
  // (Portfolio, SPY, Net Contributed) plus optional bond / int'l /
  // 60-40 if the history snapshots carry them.
  //
  // Schwab-style rebasing: additively shift each comparison line so
  // it starts at the portfolio's window-start value.  The shape
  // (within-window deltas) is preserved; lines begin at the same
  // y-position so the user can read "I beat SPY by $X" directly off
  // the chart.  Default: rebase iff window != lifetime (lifetime
  // lines naturally start near zero).  ``benchRebaseOverride`` lets
  // the user flip this — see setBenchRebase().
  const _rebaseDefault = performanceWindow !== 'lifetime';
  const _rebaseActive = (benchRebaseOverride != null ? benchRebaseOverride : _rebaseDefault)
    && windowedHistory.length > 0;
  // Build the portfolio line first (filtered if needed), then use its
  // window-start value as the rebase anchor for every other line.
  const portfolioPoints = windowedHistory.map(h => ({
    date: h.date, value: portfolioValueAt(h),
  }));
  const _portStart = _rebaseActive && portfolioPoints.length
    ? portfolioPoints[0].value
    : 0;
  // Benchmark series — single methodology across all filters and
  // windows: window-anchored mirror-withdrawals.  See
  // ``_simulateFilteredBenchmark`` docstring for why this is the
  // right choice (avoids both flat-at-zero AND inflated-from-internal-
  // transfers).
  //
  // Anchor value: the FILTERED portfolio's value at window start.
  // For lifetime (no cutoff), windowStartIso = '' and anchor = 0
  // (line grows from zero with lifetime cash flows, matching the
  // legacy Python simulation).
  const _passthroughFilter = (_t) => true;
  const _windowStartIso = (windowedHistory.length && performanceWindow !== 'lifetime')
    ? windowedHistory[0].date : '';
  const _anchorValue = _windowStartIso ? portfolioValueAt(windowedHistory[0]) : 0;
  const _filteredBenchPoints = (priceField, lifetimeKey) => {
    const filterFn = benchTxnFilter || _passthroughFilter;
    if (windowedHistory[0] && windowedHistory[0][priceField] != null) {
      const sim = _simulateFilteredBenchmark(filterFn, history, priceField,
        _windowStartIso, _anchorValue);
      if (sim) {
        return sim.filter(p => windowedHistory.some(h => h.date === p.date));
      }
    }
    return windowedHistory.map(h => ({
      date: h.date, value: typeof h[lifetimeKey] === 'number' ? h[lifetimeKey] : 0,
    }));
  };
  const _shiftedPointsFromArr = (pts) => {
    if (!_rebaseActive || !pts.length) return pts;
    const v0 = pts[0].value || 0;
    const offset = _portStart - v0;
    return pts.map(p => ({ date: p.date, value: p.value + offset }));
  };
  const benchSpyRaw = _filteredBenchPoints('benchmark_spy_price', 'benchmark_spy');
  const benchBndRaw = _filteredBenchPoints('benchmark_bnd_price', 'benchmark_bnd');
  const benchVxusRaw = _filteredBenchPoints('benchmark_vxus_price', 'benchmark_vxus');
  const benchNcRaw = windowedHistory.map(h => ({
    date: h.date, value: filteredNetContribAt(h),
  }));
  const bench6040Raw = windowedHistory.map(h => ({
    date: h.date,
    value: typeof h.benchmark_60_40 === 'number' ? h.benchmark_60_40 : 0,
  }));

  const filtSuffix = benchFilterActive ? ' (filtered)' : '';
  const benchSeries = [
    {
      label: 'Your Portfolio', color: '#4ade80', dashed: false,
      points: portfolioPoints
    },
    {
      label: `SPY Benchmark${filtSuffix}`, color: '#60a5fa', dashed: true,
      points: _shiftedPointsFromArr(benchSpyRaw)
    },
    {
      label: `Net Contributed${filtSuffix}`, color: '#9ca3af', dashed: true,
      points: _shiftedPointsFromArr(benchNcRaw)
    },
  ];
  if (windowedHistory.length && windowedHistory[0].benchmark_bnd != null) {
    benchSeries.push({
      label: `BND (Bonds)${filtSuffix}`, color: '#f472b6', dashed: true,
      points: _shiftedPointsFromArr(benchBndRaw)
    });
  }
  if (windowedHistory.length && windowedHistory[0].benchmark_vxus != null) {
    benchSeries.push({
      label: `VXUS (Int\'l)${filtSuffix}`, color: '#fb923c', dashed: true,
      points: _shiftedPointsFromArr(benchVxusRaw)
    });
  }
  // 60/40 only renders un-filtered (it's a SPY+BND blend at the
  // lifetime level — filter-aware version would need a per-filter
  // 60/40 simulation, which we don't have).  Skip when filtered.
  if (!benchFilterActive && windowedHistory.length && windowedHistory[0].benchmark_60_40 != null) {
    benchSeries.push({
      label: '60/40 Portfolio', color: '#facc15', dashed: true,
      points: _shiftedPointsFromArr(bench6040Raw)
    });
  }
  const benchChart = renderMultiLineChart(benchSeries, {
    id: 'benchmarkCompareSvg', height: 320, emptyMsg: 'No history data yet.',
  });
  // Rebase toggle — lets the user override the auto behavior (rebase
  // iff non-lifetime).  Three states: Auto (default), Rebase always,
  // Absolute always.  A small chip row above the chart so it's
  // discoverable without crowding the main controls.
  const _rebaseLabel =
    benchRebaseOverride === null ? `Auto <span style="opacity:0.7;font-weight:400;">(${_rebaseActive ? 'rebased' : 'absolute'})</span>` :
      benchRebaseOverride === true ? 'Rebased' :
        'Absolute';
  const benchToggleHtml = `
    <div class="bench-toggle-bar">
      <span class="hist-label">Y-axis:</span>
      <button class="tbtn-fixed-width tbtn ${benchRebaseOverride === null ? 'active' : ''}" data-bench-rebase="auto"
              title="Rebase comparison lines to portfolio start when a window is active; absolute dollars on lifetime view.">${_rebaseLabel.includes('Auto') ? _rebaseLabel : 'Auto'}</button>
      <button class="tbtn ${benchRebaseOverride === true ? 'active' : ''}" data-bench-rebase="on"
              title="Always rebase every comparison line to start at the portfolio's value on the first in-view date.">Rebased</button>
      <button class="tbtn ${benchRebaseOverride === false ? 'active' : ''}" data-bench-rebase="off"
              title="Show absolute dollar values for every line (no rebasing).  Useful when the simulated SPY portfolio's lifetime trajectory is what you want to see.">Absolute</button>
    </div>`;
  const benchChartParts = [];
  if (_rebaseActive) {
    benchChartParts.push(`All comparison lines rebased to portfolio's value on ${windowedHistory[0].date} (Schwab-style) — read window-relative deltas directly off the chart.`);
  }
  if (benchFilterActive) {
    benchChartParts.push(`<b>SPY/BND/VXUS lines</b> simulate the same cash flows (deposits + withdrawals) on the chosen ${performanceAccountFilter === '__retirement__' ? 'retirement accounts' : performanceAccountFilter === '__investments__' ? 'investment accounts' : performanceAccountFilter === '__taxable__' ? 'taxable accounts' : performanceAccountFilter} but invested in the benchmark instead.  Apples-to-apples: deposits buy benchmark shares, withdrawals sell shares.  Anchored at the portfolio's value on the window-start date.`);
  }
  const benchChartNote = benchChartParts.length
    ? `<div style="color:var(--text-dim);font-size:0.72rem;margin-top:4px;padding:0 4px;line-height:1.5;">${benchChartParts.join(' ')}</div>`
    : '';

  // End-of-window values for the stat cards — read directly from the
  // chart series so they match the displayed lines exactly (filtered
  // when the account toggle is active, rebased when the y-axis toggle
  // says so).
  const _lastPoint = (pts) => pts.length ? pts[pts.length - 1].value : 0;
  const _firstPoint = (pts) => pts.length ? pts[0].value : 0;
  const finalValue = _lastPoint(portfolioPoints);
  const finalSpy = _lastPoint(benchSeries.find(s => s.label.startsWith('SPY')).points);
  const finalNC = _lastPoint(benchSeries.find(s => s.label.startsWith('Net Contributed')).points);

  // Dollar Advantage = portfolio gain over the window − SPY gain
  // over the same window.  Both sides are mirror-withdrawal
  // simulations (the SPY line tracks the user's filtered cash flows
  // including withdrawals), so deposits and withdrawals cancel
  // symmetrically and the formula reduces to the simple delta-vs-
  // delta comparison.
  const portfolioDelta = finalValue - _firstPoint(portfolioPoints);
  const spyDelta = finalSpy - _firstPoint(benchSeries.find(s => s.label.startsWith('SPY')).points);
  const dollarAdvantage = portfolioDelta - spyDelta;

  // Window-aware TWR for the user's portfolio under the active
  // account filter — read from the same centralized helper as the
  // top stat cards so "Your Return" here matches "Cumulative Return"
  // up there for the same (filter, window) pair.
  const twr = (win.cum != null && windowedHistory.length >= 2)
    ? {
      cumulative: win.cum,
      annualized: win.ann,
      // The window the RETURN covers, not the window the CHART covers.
      // On a filtered lifetime view those differ: the chart spans all
      // history, the account's TWR starts when the account did.
      start_date: win.startDate || windowedHistory[0].date,
      end_date: win.endDate || windowedHistory[windowedHistory.length - 1].date,
    }
    : null;
  // SPY return over the same window — uses the raw SPY close prices
  // stored on each snapshot, not the simulated SPY-benchmark dollar
  // value (which compounds with our contribution stream).  Pure
  // market return over the window is what you actually compare a
  // TWR against.
  const spyR = !twr ? null
    : (win.spyCum != null
      ? { cumulative: win.spyCum, annualized: win.spyAnn }
      : computeSPYReturnOverPeriod(twr.start_date, twr.end_date));

  const pctStr = (v, digits = 2) => v == null
    ? '—'
    : (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%';
  const twrMainStr = twr ? pctStr(twr.cumulative, 2) : '—';
  const twrSubStr = twr && twr.annualized != null
    ? `<span class="sub">ann. ${pctStr(twr.annualized, 2)}</span>` : '';
  const spyMainStr = spyR ? pctStr(spyR.cumulative, 2) : '—';
  const spySubStr = spyR && spyR.annualized != null
    ? `<span class="sub">ann. ${pctStr(spyR.annualized, 2)}</span>` : '';

  const twrCls = twr && twr.cumulative >= 0 ? 'positive' : (twr ? 'negative' : '');
  const spyCls = spyR && spyR.cumulative >= 0 ? 'positive' : (spyR ? 'negative' : '');

  // TWR minus SPY, annualized, in percentage points per year.
  const vsSpyAnnPp = (twr && twr.annualized != null && spyR && spyR.annualized != null)
    ? (twr.annualized - spyR.annualized) * 100
    : null;
  const vsSpyAnnStr = vsSpyAnnPp == null
    ? '—'
    : (vsSpyAnnPp >= 0 ? '+' : '') + vsSpyAnnPp.toFixed(2) + ' pp/yr';
  const vsSpyAnnCls = vsSpyAnnPp == null ? '' : (vsSpyAnnPp >= 0 ? 'positive' : 'negative');

  const winLabel = performanceWindow === 'lifetime' ? 'lifetime' : performanceWindow;
  // Both sides of the comparison are measured over this exact span.
  // Worth spelling out: on a filtered lifetime view "lifetime" is the
  // ACCOUNT's lifetime, which is shorter than the chart's x-axis.
  const spanNote = twr ? `Measured ${twr.start_date} to ${twr.end_date}.` : '';
  const benchStatCards = [
    {
      label: `Your Return (TWR) <span class="sub">${winLabel}</span>`,
      value: twrMainStr + ' ' + twrSubStr, cls: twrCls,
      title: `Time-weighted return for the active account filter.

${spanNote}`
    },
    {
      label: `SPY Return <span class="sub">${winLabel}</span>`,
      value: spyMainStr + ' ' + spySubStr, cls: spyCls,
      title: `SPY's market return over the SAME span as Your Return, so the two are directly comparable.

${spanNote}`
    },
    {
      label: 'Vs SPY (annualized)', value: vsSpyAnnStr, cls: vsSpyAnnCls,
      title: `Your annualized TWR minus SPY's annualized return over the same span.

${spanNote}`
    },
    { label: 'Your Portfolio', value: fmtMoney(finalValue) },
    { label: 'SPY Benchmark', value: fmtMoney(finalSpy) },
    { label: 'Net Contributed', value: fmtMoney(finalNC) },
    {
      label: 'Dollar Advantage', value: fmtSigned(dollarAdvantage),
      cls: dollarAdvantage >= 0 ? 'positive' : 'negative',
      title: `Your portfolio's gain over the window minus SPY's gain over the same window, both anchored to the same starting capital and mirroring the same cash flows (deposits buy SPY, withdrawals sell SPY).\n\nApples-to-apples: positive means your picks beat SPY in dollars over this period; negative means SPY would have done better with the same cash-flow timing.`
    },
  ];
  const benchStatsHtml = '<div class="stats">' + benchStatCards.map(c => {
    const cls = c.cls ? `stat-card ${c.cls}` : 'stat-card';
    const titleAttr = c.title ? ` title="${_htmlEsc(c.title)}"` : '';
    return `<div class="${cls}"${titleAttr}><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('') + '</div>';

  // Top 10 winners — POSITIVE total_gain only.  Without the sign
  // filter, a portfolio with fewer than 10 winning positions padded
  // the table with losers rendered green (and the losers table showed
  // winners).  Empty state instead of padding.
  const sortedByGain = [...positions].sort((a, b) => (b.total_gain || 0) - (a.total_gain || 0));
  const _emptyRow = (msg) =>
    `<tr><td colspan="5" style="color:var(--text-dim);">${msg}</td></tr>`;
  const winnerRows = sortedByGain
    .filter(p => (p.total_gain || 0) > 0)
    .slice(0, 10).map(p => {
      const pctStr = p.pctReturn != null ? (p.pctReturn >= 0 ? '+' : '') + p.pctReturn.toFixed(1) + '%' : '—';
      return `<tr>
      <td><b>${symLabel(p.symbol)}</b></td>
      <td>${p.sector || ''}</td>
      <td class="num">${fmtMoney(p.value)}</td>
      <td class="num"><span class="positive">${fmtSigned(p.total_gain)}</span></td>
      <td class="num positive">${pctStr}</td>
    </tr>`;
    }).join('') || _emptyRow('No positions in the green');

  // Top 10 losers — NEGATIVE total_gain only (most-negative first).
  const loserRows = [...sortedByGain].reverse()
    .filter(p => (p.total_gain || 0) < 0)
    .slice(0, 10).map(p => {
      const pctStr = p.pctReturn != null ? (p.pctReturn >= 0 ? '+' : '') + p.pctReturn.toFixed(1) + '%' : '—';
      return `<tr>
      <td><b>${symLabel(p.symbol)}</b></td>
      <td>${p.sector || ''}</td>
      <td class="num">${fmtMoney(p.value)}</td>
      <td class="num"><span class="negative">${fmtSigned(p.total_gain)}</span></td>
      <td class="num negative">${pctStr}</td>
    </tr>`;
    }).join('') || _emptyRow('No positions in the red');

  root.innerHTML = `
    ${statsHtml}

    <div class="toggles-card" style="margin-top:16px;">
      <div class="toggles-row">
        <span class="toggles-label">View:</span>
        <div class="toggle-group">
          <button class="tbtn${_perfView === 'returns' ? ' active' : ''}" id="perfViewBtnReturns" onclick="setPerfView('returns')">Returns</button>
          <button class="tbtn${_perfView === 'risk' ? ' active' : ''}" id="perfViewBtnRisk" onclick="setPerfView('risk')">Risk</button>
        </div>
        <span class="toggles-hint">Returns: benchmark comparison, per-account TWR, winners &amp; losers.  Risk: drawdown, monthly P&amp;L, daily moves.</span>
      </div>
    </div>

    <div id="perfViewReturns" style="${_perfView === 'returns' ? '' : 'display:none;'}">
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Your Portfolio vs SPY Benchmark</span></h2>
    </div>
    ${benchStatsHtml}
    ${benchToggleHtml}
    ${benchChart}
    ${benchChartNote}
    <div style="color:var(--text-dim);font-size:0.8rem;margin-top:8px;padding:0 4px;line-height:1.5;">
      <b>Two different comparisons are at work here — don't confuse them.</b><br>
      <b>Vs SPY (annualized)</b> is the honest measure of investment
      decisions: TWR chains the returns between every contribution and
      withdrawal, so cash-flow timing doesn't distort the number.  It's
      the same method Schwab and Fidelity use.  If this is positive, your
      picks beat buy-and-hold SPY <i>per dollar invested per unit time</i>.<br>
      <b>Dollar Advantage</b> (the gap between the green and blue lines)
      is TWR × contribution timing × scale.  Big early gains on a small
      balance inflate the dollar gap even if your per-period returns
      trailed SPY later; a huge contribution right before a crash widens
      it the other way.  Use it for "did I end up with more money?" not
      "did I pick better than the market?".<br>
      Gray line is cumulative external cash in (deposits + contributions
      − withdrawals).  Blue is that same cash flow invested in SPY
      instead.  Green is your actual portfolio.<br>
      SPY values here and in the tables below are <b>total return</b>
      (dividends reinvested), matching what Schwab/Fidelity report.  Your
      own portfolio TWR already includes reinvested dividends via your
      Reinvest / Buy transactions, so the comparison is apples-to-apples.
    </div>

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">By Account</span></h2>
    </div>
    ${acctSummaryHtml}
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Year</th><th class="num">Start Value</th><th class="num">End Value</th>
          <th class="num">Net Contributed</th><th class="num">Return $</th>
          <th class="num">TWR %</th><th class="num">SPY %</th>
        </tr></thead>
        <tbody>${annualByAcctRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">No data for this account.</td></tr>'}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">
        TWR % is the time-weighted return for that year — removes the
        effect of contribution timing.  SPY % is SPY's total return
        (dividends reinvested) for the year.
        ${performanceAccountFilter === null
      ? ' Total includes <b>Apple Savings</b>, which dilutes TWR vs. SPY — pick <b>Investments</b> for an apples-to-apples equity comparison.'
      : performanceAccountFilter === '__investments__'
        ? ' Figures cover all investment accounts (everything except Savings) — the right comparison for an equity benchmark.'
        : performanceAccountFilter === '__retirement__'
          ? ' Figures cover <b>401K, Roth IRA, and Rollover IRA</b> combined.'
          : ' Figures cover the <b>' + performanceAccountFilter + '</b> account only.'}
      </div>
    </div>

    <div class="overview-split" style="margin-top:24px;">
      <div class="panel">
        <h3>Top 10 Winners</h3>
        <table class="mini-table">
          <thead><tr>
            <th>Symbol</th><th>Sector</th><th class="num">Value</th>
            <th class="num">Total Gain</th><th class="num">% Return</th>
          </tr></thead>
          <tbody>${winnerRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody>
        </table>
      </div>
      <div class="panel">
        <h3>Top 10 Losers</h3>
        <table class="mini-table">
          <thead><tr>
            <th>Symbol</th><th>Sector</th><th class="num">Value</th>
            <th class="num">Total Gain</th><th class="num">% Return</th>
          </tr></thead>
          <tbody>${loserRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    </div>

    <div id="perfViewRisk" style="${_perfView === 'risk' ? '' : 'display:none;'}">
    ${_buildDrawdownSection()}
    ${_buildMonthlyPnlSection()}
    ${_buildDailyPnlSection()}
    </div>
  `;
}

// Returns ↔ Risk sub-view for the Performance tab.  Both views render
// into the DOM (string-built SVGs scale via viewBox), so switching is a
// pure display toggle — no chart re-render needed.
let _perfView = 'returns';
function setPerfView(v) {
  _perfView = v === 'risk' ? 'risk' : 'returns';
  const ret = document.getElementById('perfViewReturns');
  const risk = document.getElementById('perfViewRisk');
  if (ret) ret.style.display = _perfView === 'returns' ? '' : 'none';
  if (risk) risk.style.display = _perfView === 'risk' ? '' : 'none';
  const bR = document.getElementById('perfViewBtnReturns');
  const bK = document.getElementById('perfViewBtnRisk');
  if (bR) bR.classList.toggle('active', _perfView === 'returns');
  if (bK) bK.classList.toggle('active', _perfView === 'risk');
}

registerTabRenderer('performance', renderPerformance);

// Delegated click handler for the Performance tab's window selector
// chips.  Attached to performanceContent so it survives re-renders.
// Handles both the top stat-cards window selector and the By Account
// TWR window-preset selector.
document.getElementById('performanceContent')?.addEventListener('click', e => {
  // Single window selector at the top of the tab — the prior
  // duplicate "data-acct-twr-preset" handler is gone.
  const topBtn = e.target.closest('[data-perf-window]');
  if (topBtn) { setPerformanceWindow(topBtn.dataset.perfWindow); return; }
  const rebaseBtn = e.target.closest('[data-bench-rebase]');
  if (rebaseBtn) { setBenchRebase(rebaseBtn.dataset.benchRebase); return; }
});

// Detect columns from data
const NUMERIC_FIELDS = new Set([
  'quantity', 'price', 'fees', 'amount', 'balance', 'value',
  'cost_basis', 'realized_gain', 'cash_flow', 'holding_days', 'seq',
]);
const HIDDEN_BY_DEFAULT = new Set([
  'description', 'account', 'source', 'raw_action',
  // Basis-walker outputs — useful but noisy by default; visible via
  // the "show:" chip list when the user wants them.
  'cost_basis', 'realized_gain', 'cash_flow', 'holding_days', 'basis_effect',
  // Ingest-order index — plumbing for the lot walkers' sort, not a
  // figure anyone reads off the ledger.
  'seq',
]);

const columns = txns.length > 0
  ? Object.keys(txns[0]).filter(k => k !== '_hash')
  : ['date', 'account_group', 'account_type', 'account', 'symbol', 'action', 'quantity', 'price', 'fees', 'amount', 'description', 'source'];

// Column visibility state
const colVisible = {};
columns.forEach(col => colVisible[col] = !HIDDEN_BY_DEFAULT.has(col));
const visibleCols = () => columns.filter(c => colVisible[c]);

// Filter fields: categorical columns with reasonable cardinality
const FILTER_FIELDS = columns.filter(col => {
  if (NUMERIC_FIELDS.has(col)) return false;
  const uniq = new Set(txns.map(t => t[col]));
  return uniq.size > 1 && uniq.size <= 80;
});

// Filter state: field -> Set of selected values (empty = all)
const filterState = {};
FILTER_FIELDS.forEach(f => filterState[f] = new Set());

// Precompute unique values per filter field
const filterValues = {};
FILTER_FIELDS.forEach(f => {
  filterValues[f] = [...new Set(txns.map(t => t[f]))].filter(v => v != null && v !== '').sort();
});

// Track which popover is open (field name or 'columns' or null)
let openPopover = null;

