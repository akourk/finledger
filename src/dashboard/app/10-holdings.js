// =========================================================================
// As-of-date state — lets the user view holdings / overview stats /
// allocation at any past snapshot date.  Uses the per-snapshot
// `positions` list emitted by src/history.py, so position state is
// exactly what it was on that date (no JS replay of the basis walker).
// Default: the latest snapshot (today's state, identical to the live
// holdings table).
// =========================================================================
let asOfDate = history.length
  ? history[history.length - 1].date
  : SNAPSHOT_DATE;
const LATEST_DATE = asOfDate;

function isAsOfLatest() { return asOfDate === LATEST_DATE; }

// Scope labels describe the data actually shown, including the prior
// snapshot used when a requested date falls between exported snapshots.
function renderDateScopes() {
  const latest = `Latest · ${LATEST_DATE}`;
  const snapshot = isAsOfLatest() ? null : getAsOfSnapshot();
  const selected = isAsOfLatest() ? latest
    : (snapshot ? `As of · ${snapshot.date}` : 'No prior snapshot');
  document.querySelectorAll('.as-of-label').forEach(el => {
    el.textContent = selected;
    el.title = snapshot && snapshot.date !== asOfDate
      ? `Latest available snapshot on or before ${asOfDate}` : '';
  });
  document.querySelectorAll('.latest-scope-label').forEach(el => {
    el.textContent = latest;
  });
}

// Find snapshot matching asOfDate (exact, else nearest <= that date).
function getAsOfSnapshot() {
  if (!history.length) return null;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].date <= asOfDate) return history[i];
  }
  return null;
}

// Position list shaped like holdings_by_account (enriches with
// account_type + sector + unrealized_gain).  For the latest snapshot we
// return the live holdingsByAccount directly so everything stays
// consistent with the basis methods table and live stat cards.
function asOfHoldingsByAccount() {
  if (isAsOfLatest()) return holdingsByAccount;
  const snap = getAsOfSnapshot();
  if (!snap || !snap.positions) return [];
  const rows = [...(snap.valuation_precision?.positions || snap.positions), ...(snap.in_transit || []).map(p => ({
    ...p, account_group: 'In transit', _inTransit: true,
    _transitRoute: `${p.source_group} → ${p.destination_group}`,
  }))];
  return rows.map(p => {
    const value = p.value;
    const cb = p.cost_basis;
    const ug = (typeof value === 'number' && typeof cb === 'number')
      ? value - cb : null;
    return {
      account_group: p.account_group,
      account_type: p._inTransit ? 'In transit' : (ACCOUNT_TYPE_OF[p.account_group] || 'Taxable'),
      symbol: p.symbol,
      quantity: p.quantity,
      price: p.price,
      value: value,
      cost_basis: cb,
      unrealized_gain: ug,
      sector: SECTOR_OF[p.symbol] || 'Other',
      ...(p._inTransit ? { _inTransit: true, _transitRoute: p._transitRoute } : {}),
    };
  });
}

// Aggregate by symbol — same shape as holdings[].  Sector comes from
// SECTOR_OF; price is derived as value/quantity to match the aggregate.
function asOfHoldingsByAsset() {
  if (isAsOfLatest()) return holdingsByAsset;
  const rows = asOfHoldingsByAccount();
  const agg = Object.create(null);
  for (const r of rows) {
    const k = r.symbol;
    if (!agg[k]) agg[k] = {
      symbol: k, sector: r.sector,
      quantity: 0, _value: 0, _basis: 0,
      _anyValue: false, _anyBasis: false,
    };
    agg[k].quantity += r.quantity || 0;
    if (typeof r.value === 'number') { agg[k]._value += r.value; agg[k]._anyValue = true; }
    if (typeof r.cost_basis === 'number') { agg[k]._basis += r.cost_basis; agg[k]._anyBasis = true; }
  }
  return Object.values(agg).map(a => {
    const value = a._anyValue ? +a._value.toFixed(2) : null;
    const cb = a._anyBasis ? +a._basis.toFixed(2) : null;
    const price = (value != null && a.quantity) ? +(value / a.quantity).toFixed(4) : null;
    const ug = (value != null && cb != null) ? +(value - cb).toFixed(2) : null;
    return {
      symbol: a.symbol, sector: a.sector,
      quantity: a.quantity, price, value,
      cost_basis: cb, unrealized_gain: ug,
    };
  });
}

// Called by the picker change handlers.  Syncs both pickers, updates
// any "as of" labels, and re-renders the views that respond to the
// date: overview stats, top holdings, allocation, holdings tab, and
// recent transactions.  Lot method comparison and the Performance /
// Income / Tax tabs stay lifetime (they're either aggregates-to-today
// or already have their own period controls).
function setAsOfDate(date) {
  asOfDate = date || LATEST_DATE;
  document.querySelectorAll('.as-of-picker').forEach(el => { el.value = asOfDate; });
  renderDateScopes();
  if (typeof renderStats === 'function') renderStats();
  if (typeof renderTopHoldings === 'function') renderTopHoldings();
  if (typeof renderAllocation === 'function') renderAllocation();
  if (typeof renderHoldings === 'function') renderHoldings();
  if (typeof renderByAssetTable === 'function') renderByAssetTable();
  // Board mode is latest-only; it renders its own explanation when the
  // as-of date moves off latest (defined in 15-board.js, which is
  // concatenated after this file — hence the call-time guard).
  if (typeof renderPositionsBoard === 'function'
      && typeof boardLayout !== 'undefined' && boardLayout === 'board') {
    renderPositionsBoard();
  }
  if (typeof renderRecentTransactions === 'function') renderRecentTransactions();
}

// Populate the as-of-date pickers with every snapshot date.  Newest
// first so the default-selected option is at the top.  The last entry
// (latest) gets a "(latest)" label for clarity.
(function populateAsOfPickers() {
  if (!history.length) return;
  const latest = history[history.length - 1].date;
  const opts = [...history].reverse().map(h => {
    const label = h.date === latest ? `${h.date} (latest)` : h.date;
    return `<option value="${_htmlEsc(h.date)}">${_htmlEsc(label)}</option>`;
  }).join('');
  document.querySelectorAll('.as-of-picker').forEach(sel => {
    sel.innerHTML = opts;
    sel.value = asOfDate;
  });
})();
renderDateScopes();

function _rolloverBridgeAdjustment(snapshotDate, filterSet) {
  if (!_ROLLOVER_BRIDGES.length || !snapshotDate) return 0;
  let adj = 0;
  for (const b of _ROLLOVER_BRIDGES) {
    if (filterSet && !filterSet.has(b.group)) continue;
    if (snapshotDate >= b.start_date && snapshotDate < b.end_date) adj += b.amount;
  }
  return adj;
}
// "2026-07-02T07:09:18" → "2026-07-02 07:09" (seconds are noise here)
document.getElementById('generated').textContent =
  (DATA.generated || '').replace('T', ' ').slice(0, 16);

// Price freshness, next to the generation stamp.  `as_of` is only a
// DATE, so a snapshot reads as "current" all day even when the marks
// behind it were pulled at 7am — which is what makes a mid-session
// reconciliation against a broker statement confusing.  The stamp is
// the OLDEST fetch across held positions (a staleness floor), so it
// never flatters.  Same-day shows just the time; anything older keeps
// its date, because that is the case worth noticing.
function renderPricesAsOf() {
  const el = document.getElementById('pricesAsOf');
  if (!el) return;
  const stamp = (ANALYTICS.header_summary || {}).prices_as_of;
  if (!stamp) { el.textContent = ''; return; }
  const t = new Date(stamp);
  if (isNaN(t)) { el.textContent = ''; return; }
  const time = t.toLocaleTimeString(undefined,
    { hour: 'numeric', minute: '2-digit' });
  const sameDay = stamp.slice(0, 10) === (DATA.generated || '').slice(0, 10);
  const when = sameDay ? time : stamp.slice(0, 10) + ' ' + time;
  // "provisional" = at least one held position is marked from a bar
  // that can still change (a mid-session price, a fund NAV that has
  // not struck). Showing an intraday figure is fine; presenting it as
  // a close without saying so is what makes a broker reconciliation
  // disagree at 11am and then differently again after the bell.
  const live = (ANALYTICS.header_summary || {}).prices_provisional;
  el.innerHTML = ` · prices as of ${_htmlEsc(when)}`
    + (live ? ' <span class="tb-provisional">· provisional</span>' : '');
  el.title = 'Oldest price fetch across held positions — the marks '
           + 'behind this snapshot are no fresher than this.'
           + (live ? '\n\nProvisional: at least one position is marked from '
                   + 'a bar that has not settled yet, so its value will '
                   + 'still move.' : '');
}
renderPricesAsOf();

// Render the persistent top-bar summary: portfolio value, total
// return ($ + %), and 1-day change ($ + %).  Reads precomputed
// header_summary for the shared value and return figures. The fallback
// for older exports uses value less contributed cash. Called once on load;
// the values are frozen at pipeline-run time, so a "refresh" is
// really "regenerate & reload".
function renderTopBarSummary() {
  const el = document.getElementById('topBarSummary');
  if (!el) return;
  const hs = (ANALYTICS.header_summary) || {};

  const value = hs.value != null ? hs.value
    : (holdingsByAsset.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0));
  // Total return (value − net contributed) is precomputed in
  // analytics/header.py — the Performance tab's all-time anchor card
  // reads the same field, so the two always agree to the cent.
  // Fallback (header_summary absent, e.g. empty history): derive it
  // locally from the same formula.
  const netC = hs.net_contributed != null ? hs.net_contributed
    : (cashSummary.net_contributed || 0);
  const totalReturn = hs.total_return != null ? hs.total_return : (value - netC);
  const totalReturnPct = hs.total_return_pct != null ? hs.total_return_pct * 100
    : (netC > 0 ? (totalReturn / netC) * 100 : null);
  const ch = hs.change_1d;
  const chPct = hs.change_1d_pct != null ? hs.change_1d_pct * 100 : null;

  const clsOf = v => v == null ? '' : (v >= 0 ? 'positive' : 'negative');
  const pctStr = (v, d = 2) => v == null
    ? ''
    : ` <span class="sub">${v >= 0 ? '+' : ''}${v.toFixed(d)}%</span>`;

  const parts = [
    `<div class="tb-item">
       <span class="tb-label">Portfolio Value</span>
       <span class="tb-value tb-portfolio-value">${fmtMoney(value)}</span>
     </div>`,
    `<div class="tb-divider"></div>`,
    `<div class="tb-item">
       <span class="tb-label">Total Return</span>
       <span class="tb-value ${clsOf(totalReturn)}">${fmtSigned(totalReturn)}${pctStr(totalReturnPct, 1)}</span>
     </div>`,
  ];
  if (ch != null) {
    parts.push(`<div class="tb-divider"></div>`);
    parts.push(`<div class="tb-item">
         <span class="tb-label">1-Day Change</span>
         <span class="tb-value ${clsOf(ch)}">${fmtSigned(ch)}${pctStr(chPct, 2)}</span>
       </div>`);
  }
  el.innerHTML = parts.join('');
}
renderTopBarSummary();

// --- Number formatters (used by stats, basis table, etc.) ---
// Minus sign goes BEFORE the $ (US convention: -$100, not $-100).
function fmtMoney(v, digits = 2) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  return sign + '$' + n.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}
function fmtSigned(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  if (v >= 0) return '+' + fmtMoney(v);
  return fmtMoney(v);   // fmtMoney already produces "-$100"
}
function fmtPct(v, digits = 1) {
  if (v == null || isNaN(v) || !isFinite(v)) return '—';
  const s = v >= 0 ? '+' : '';
  return s + v.toFixed(digits) + '%';
}

// Action catalog — sourced from src/actions.py via the JSON export so
// the vocabulary stays single-source.  ACTION_COLORS and the TWR
// membership sets are built from the catalog rather than duplicated.
const _ACTION_CATALOG = (DATA.action_catalog && DATA.action_catalog.actions) || [];
const ACTION_COLORS = Object.fromEntries(_ACTION_CATALOG.map(a => [a.name, a.color]));
function _actionsWith(field, value) {
  return new Set(_ACTION_CATALOG.filter(a => a[field] === value).map(a => a.name));
}
const ACCOUNT_COLORS = {
  'Robinhood': '#4ade80',
  'Coinbase': '#60a5fa',
  'Rollover IRA': '#c084fc',
  'Roth IRA': '#a78bfa',
  '401K': '#f59e0b',
  'Apple Savings': '#fb923c',
};
const TYPE_COLORS = {
  'Taxable': '#60a5fa',
  'Retirement': '#a78bfa',
  'Savings': '#fb923c',
};
const SECTOR_COLORS = {
  'Technology': '#60a5fa',
  'Financial Services': '#a78bfa',
  'Healthcare': '#34d399',
  'Consumer Cyclical': '#fbbf24',
  'Consumer Defensive': '#facc15',
  'Communication Services': '#818cf8',
  'Industrials': '#fb923c',
  'Energy': '#f87171',
  'Utilities': '#2dd4bf',
  'Real Estate': '#f97316',
  'Basic Materials': '#c084fc',
  'Cryptocurrency': '#fcd34d',
  'ETFs': '#67e8f9',
  'Mutual Funds': '#e879f9',
  'Options': '#f472b6',
  'Cash': '#4ade80',
  'Other': '#9ca3af',
};

// Imported account names may be ordinary Object prototype names.
// Lookup tables must never treat those inherited properties as data.
for (const palette of [ACCOUNT_COLORS, TYPE_COLORS, SECTOR_COLORS]) Object.setPrototypeOf(palette, null);

// --- Tab router ---
// Each tab has a render function; first-activation is when the tab
// content actually gets built (keeps initial load fast).
const TAB_RENDERERS = Object.create(null);   // name -> function
const TAB_RENDERED = new Set();   // names that have been rendered at least once

function registerTabRenderer(name, fn) {
  TAB_RENDERERS[name] = fn;
}

function activateTab(name, opts) {
  opts = opts || {};
  const panel = document.getElementById('tab-' + name);
  if (!panel) return;
  // Swap visible panel + active pill.  `aria-selected` and the roving
  // tabindex move with the `active` class rather than being maintained
  // separately — the class IS the selection, so deriving the ARIA state
  // from the same assignment is what keeps them from drifting.
  document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => {
    const on = el.dataset.tab === name;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
    // Only the selected tab is in the tab order; Left/Right reach the
    // rest (WAI-ARIA authoring practices, tabs pattern).
    el.tabIndex = on ? 0 : -1;
  });
  panel.classList.add('active');
  // Lazy render on first activation
  if ((!TAB_RENDERED.has(name) && TAB_RENDERERS[name]) || name === 'overview') {
    panel.querySelector('[data-render-error]')?.remove();
    try {
      if (!TAB_RENDERED.has(name) && TAB_RENDERERS[name]) TAB_RENDERERS[name]();
      if (name === 'overview' && typeof renderHistory === 'function') renderHistory();
      TAB_RENDERED.add(name);
    } catch (e) {
      TAB_RENDERED.delete(name);
      console.error('tab render failed', name, e);
      const error = document.createElement('div');
      error.className = 'render-error panel';
      error.setAttribute('role', 'alert');
      error.setAttribute('data-render-error', name);
      const message = document.createElement('p');
      message.textContent = 'This section could not be displayed. Retry, or regenerate the dashboard if the problem continues.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'tbtn';
      retry.setAttribute('data-render-retry', name);
      retry.textContent = 'Retry section';
      retry.addEventListener('click', () => activateTab(name));
      error.append(message, retry);
      panel.prepend(error);
    }
  }
  // Most tables do not exist until their tab first renders, so the
  // scroll-region measurement has to run after, not once at load.
  if (typeof applyScrollRegionFocus === 'function') applyScrollRegionFocus();
  // Update URL hash without triggering scroll.  window.history is the
  // browser's History API — avoid the bare `history` reference because
  // later in this file we shadow it with `const history = DATA.history`.
  if (opts.replace !== false && location.hash.slice(1) !== name) {
    window.history.replaceState(null, '', '#' + name);
  }
}

// Tab click handlers
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});

// Keyboard support for the tablist.  A tablist is a single tab stop:
// Tab moves past it, Left/Right move between tabs, Home/End jump to the
// ends.  Hidden tabs (see hideEmptyTabs below) are skipped — arrowing
// onto a tab whose panel the portfolio has no data for would land focus
// on something the user cannot see.
function initTabA11y() {
  const nav = document.getElementById('tabnav');
  if (!nav) return;
  const visible = () => Array.from(nav.querySelectorAll('.tab-btn'))
    .filter(b => b.style.display !== 'none');
  nav.addEventListener('keydown', (e) => {
    const KEYS = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
    if (!(e.key in KEYS)) return;
    const btns = visible();
    const here = btns.indexOf(document.activeElement);
    if (here < 0) return;
    const step = KEYS[e.key];
    const next = step === 'first' ? 0
      : step === 'last' ? btns.length - 1
        : (here + step + btns.length) % btns.length;
    e.preventDefault();
    // Follow-focus (automatic activation): the panels are already in the
    // DOM and render lazily, so switching on arrow costs nothing and
    // matches what a mouse user gets from a single click.
    btns[next].focus();
    activateTab(btns[next].dataset.tab);
  });
}
initTabA11y();

// A container that scrolls must be reachable from the keyboard, or the
// content past its edge is mouse-only (SC 2.1.1).  Whether a given wrap
// actually scrolls depends on the viewport and on how many columns are
// visible, so this is measured rather than declared — marking every
// wrapper focusable would add tab stops to tables that fit.
function applyScrollRegionFocus() {
  // One entry per wrapper the stylesheet gives an `overflow: auto`.
  document.querySelectorAll(
    '.table-wrap, .mini-scroll, .annual-breakdown-wrap, .ab-scroll, ' +
    '.board-scroll, .lot-detail-inner, .recon-drill, .heatmap-wrap')
    .forEach(el => {
      const scrollsX = el.scrollWidth > el.clientWidth + 1;
      const scrolls = scrollsX || el.scrollHeight > el.clientHeight + 1;
      // The same measurement drives the visual cue and keyboard access;
      // always reset it when a resize or column change removes overflow.
      el.setAttribute('data-overflow-x', scrollsX ? 'true' : 'false');
      if (scrolls) {
        el.tabIndex = 0;
        // A focusable region needs a name; the table it wraps supplies
        // one through its caption, so point at that rather than
        // inventing a second description.
        const cap = el.querySelector('caption');
        if (cap && !el.hasAttribute('aria-label')) {
          el.setAttribute('role', 'group');
          el.setAttribute('aria-label', cap.textContent.trim());
        }
      } else if (el.tabIndex === 0) {
        el.removeAttribute('tabindex');
      }
    });
}
window.addEventListener('resize', applyScrollRegionFocus);

// Sortable column headers are plain header cells with a click
// handler, so
// without this they are mouse-only (SC 2.1.1).  One delegated handler
// covers every table: the headers already carry tabindex and aria-sort
// at their emit sites, and every sort path — delegated listener or
// inline onclick — is reachable by dispatching the click they all
// already listen for.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  if (e.target.closest && e.target.closest('button, input, select, a')) return;
  const th = e.target.closest && e.target.closest('th[tabindex="0"]');
  if (!th) return;
  e.preventDefault();
  th.click();
});

// Hide tabs with no underlying activity so the nav is honest about
// what matters for this portfolio.  The panel stays in the DOM (deep
// links still work); only the nav button hides.
(function hideEmptyTabs() {
  const hide = (name) => {
    const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
    if (btn) btn.style.display = 'none';
  };
  const optStats = ((ANALYTICS.options || {}).stats) || {};
  if (!(optStats.trades || optStats.open_count)) hide('options');
  const cryptoStats = ((ANALYTICS.crypto || {}).stats) || {};
  if (!cryptoStats.txn_count) hide('crypto');
})();
// Hash routing — deep links and back/forward button
window.addEventListener('hashchange', () => {
  const name = location.hash.slice(1);
  if (name) activateTab(name, { replace: false });
});
// Note: initial-hash honoring is deferred to the end of this script
// (see the initTab() call near `renderTable()` at the bottom).
// Running activateTab() up here fires renderHistory() which touches
// state declared further down (seriesHidden, historySelection, ...),
// and those are in the temporal dead zone until their `const/let`
// lines execute.  A refresh on #overview would throw and halt the
// rest of the script, leaving the page blank.

// --- Holdings table ---
// 'By Asset' lives in its own table below — see renderByAssetTable.
let holdingsView = 'account'; // 'account' | 'type' | 'sector'
let holdingsSortCol = 'value';
let holdingsSortAsc = false;

const HOLDINGS_VIEW_BTNS = {
  account: 'btnByAccount',
  type: 'btnByType',
  sector: 'btnBySector',
};

function setHoldingsView(view) {
  // Back-compat: anything that still passes 'asset' lands on the
  // dedicated By-Asset table below — no change to the upper toggle.
  if (view === 'asset') {
    document.getElementById('byAssetHeader')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  holdingsView = view;
  for (const [v, id] of Object.entries(HOLDINGS_VIEW_BTNS)) {
    document.getElementById(id).classList.toggle('active', view === v);
  }
  holdingsSortCol = 'value';
  holdingsSortAsc = false;
  renderHoldings();
}

// Group an array of holdings rows by a single key field → [{key, value}, ...]
function groupBy(rows, key) {
  const out = Object.create(null);
  for (const row of rows) {
    const k = row[key] || 'Unknown';
    if (!out[k]) out[k] = { [key]: k, value: 0 };
    out[k].value += (typeof row.value === 'number' ? row.value : 0);
  }
  return Object.values(out);
}

// Same as groupBy but also rolls up cost_basis and unrealized_gain.
function groupByBasisAware(rows, key) {
  const out = Object.create(null);
  for (const row of rows) {
    const k = row[key] || 'Unknown';
    const mapKey = row._inTransit && key !== 'sector' ? _TRANSIT_BUCKET : k;
    if (!out[mapKey]) out[mapKey] = { [key]: k, value: 0, cost_basis: 0, unrealized_gain: 0, _anyBasis: false, _groups: new Set() };
    const aggregate = out[mapKey];
    // Which account groups this row is made of.  A precomputed TWR
    // filter covers a SET of account groups, so this is what lets a
    // grouped row find its own return without recomputing one.
    if (row.account_group && !row._inTransit) aggregate._groups.add(row.account_group);
    if (row._inTransit) {
      if (!aggregate._transitRoutes) aggregate._transitRoutes = new Set();
      aggregate._transitRoutes.add(row._transitRoute);
    }
    if (typeof row.value === 'number') aggregate.value += row.value;
    if (typeof row.cost_basis === 'number') {
      aggregate.cost_basis += row.cost_basis;
      aggregate._anyBasis = true;
    }
    if (typeof row.unrealized_gain === 'number') aggregate.unrealized_gain += row.unrealized_gain;
  }
  // If no row had a known cost_basis, zero it out for display cleanliness.
  return Object.values(out).map(r => {
    if (!r._anyBasis) { r.cost_basis = null; r.unrealized_gain = null; }
    delete r._anyBasis;
    return r;
  });
}

// ---------------------------------------------------------------------
// Performance figures for a grouped holdings row.
//
// `analytics.performance_by_filter` already carries a TWR summary and an
// XIRR for every account filter it precomputed — Total, Investments,
// Retirement, Taxable, and each individual account.  Each entry names
// the SET of account groups it covers, so a grouped row finds its own
// return by matching that set rather than deriving a second one.  A row
// whose set no filter covers (every sector row; a multi-account type
// with no combined filter) simply has no return, and says so.
// ---------------------------------------------------------------------
const PERF_BY_GROUPSET = (() => {
  const m = Object.create(null);
  for (const [name, entry] of Object.entries(ANALYTICS_PERF)) {
    const fg = entry && entry.filter_groups;
    if (!Array.isArray(fg) || !fg.length) continue;   // Total has none
    const key = [...fg].sort().join('\u0000');
    // First writer wins: individual accounts are registered last in
    // build_analytics, so a combined filter (Retirement, Taxable) keeps
    // the name a reader would expect when the sets coincide.
    if (!(key in m)) m[key] = { name, entry };
  }
  return m;
})();

function perfForGroups(groups) {
  if (!groups || !groups.size) return null;
  return PERF_BY_GROUPSET[[...groups].sort().join('\u0000')] || null;
}

// Attach the performance columns to a grouped row.  `unrealized %` is
// always available (it is just this row's own gain over its own basis);
// the TWR / XIRR pair only when a precomputed filter covers exactly
// this row's accounts.
function withPerformance(rows) {
  return rows.map(r => {
    const cb = r.cost_basis;
    r.unrealized_pct = (typeof cb === 'number' && cb > 0
                        && typeof r.unrealized_gain === 'number')
      ? +((r.unrealized_gain / cb) * 100).toFixed(2) : null;
    const hit = perfForGroups(r._groups);
    const historical = !isAsOfLatest() && r._groups.size
      ? historicalGroupPerformance(r._groups, getAsOfSnapshot()?.date || asOfDate) : null;
    const sum = isAsOfLatest() ? (hit && hit.entry.summary) : (historical && historical.summary);
    const mw = isAsOfLatest() ? (hit && hit.entry.money_weighted) : (historical && historical.money_weighted);
    r.twr_cum = sum && sum.cumulative != null ? +(sum.cumulative * 100).toFixed(2) : null;
    r.twr_ann = sum && sum.annualized != null ? +(sum.annualized * 100).toFixed(2) : null;
    r.xirr    = mw && mw.annualized != null ? +(mw.annualized * 100).toFixed(2) : null;
    // Every filter's window is its OWN — a 401K opened years into the
    // ledger is measured over a much shorter span than Robinhood.  The
    // span travels with the figure rather than being left implicit.
    r._perfSpan = sum ? `${sum.start_date} → ${sum.end_date} (${sum.years}y)` : '';
    delete r._groups;
    return r;
  });
}

// Display labels for the holdings table's performance columns.  The
// base columns use fieldLabel(); performance terms carry short explanations.
const HOLDINGS_COL_LABEL = {
  unrealized_pct: 'Unrealized %',
  twr_cum: 'TWR total %',
  twr_ann: 'TWR annual %',
  xirr: 'XIRR annual %',
};
// Four different questions, which is the whole reason to show all four.
const HOLDINGS_COL_TIP = {
  unrealized_pct: 'Gain on what you hold right now, over its cost basis. '
    + 'Says nothing about money already realized or withdrawn.',
  twr_cum: 'Time-weighted return from this group&#39;s first snapshot through the selected date, compounded. '
    + 'Contribution timing removed — it measures the investments, not the saving.',
  twr_ann: 'The same time-weighted return, per year. Hover a value for the span '
    + 'it was measured over — each group&#39;s window is its own.',
  xirr: 'Money-weighted (XIRR): what your actual dollars earned, contribution '
    + 'timing included. The gap against TWR is the behaviour gap.',
};
const HOLDINGS_PCT_COLS = new Set(['unrealized_pct', 'twr_cum', 'twr_ann', 'xirr']);
const HOLDINGS_SPAN_COLS = new Set(['twr_cum', 'twr_ann', 'xirr']);

function renderHoldings() {
  // By Account: one row per account_group total.
  // By Type:    one row per account_type total (Taxable/Retirement/Savings).
  // By Sector:  one row per sector total — sourced from the by-asset holdings
  //             list so the symbol→sector mapping is 1:1.
  // When asOfDate != latest, rows come from the snapshot's `positions`
  // list (position state as of that date) instead of live holdings.
  const byAccountSrc = asOfHoldingsByAccount();
  const byAssetSrc = asOfHoldingsByAsset();

  let data, cols, numCols;
  const keyCol = holdingsView === 'account' ? 'account_group'
    : (holdingsView === 'type' ? 'account_type' : 'sector');
  // Sector rows come from the by-ASSET list, which carries no account
  // group — so a sector legitimately has no time-weighted return, and
  // the TWR/XIRR columns drop out below rather than rendering a
  // column of dashes.
  data = withPerformance(groupByBasisAware(
    holdingsView === 'sector' ? byAssetSrc : byAccountSrc, keyCol));
  const anyPerf = data.some(r => r.twr_ann != null || r.xirr != null);
  cols = [keyCol, 'value', 'cost_basis', 'unrealized_gain', 'unrealized_pct'];
  if (anyPerf) cols.push('twr_cum', 'twr_ann', 'xirr');
  numCols = new Set(['value', 'cost_basis', 'unrealized_gain',
    'unrealized_pct', 'twr_cum', 'twr_ann', 'xirr']);

  // Header
  const hRow = document.getElementById('holdingsHeaderRow');
  hRow.innerHTML = cols.map(col => {
    const cls = numCols.has(col) ? ' class="num"' : '';
    const arrow = holdingsSortCol === col ? (holdingsSortAsc ? ' ▲' : ' ▼') : '';
    const label = HOLDINGS_COL_LABEL[col] || fieldLabel(col);
    const tip = HOLDINGS_COL_TIP[col] ? ` title="${HOLDINGS_COL_TIP[col]}"` : '';
    const sorted = holdingsSortCol === col
      ? ` aria-sort="${holdingsSortAsc ? 'ascending' : 'descending'}"` : '';
    return `<th scope="col"${cls} data-hcol="${col}"${tip}${sorted} tabindex="0" role="columnheader"
      >${label}<span class="arrow">${arrow}</span></th>`;
  }).join('');

  // Sort
  const sorted = [...data].sort((a, b) => {
    let va = a[holdingsSortCol] ?? '', vb = b[holdingsSortCol] ?? '';
    if (numCols.has(holdingsSortCol)) {
      va = typeof va === 'number' ? va : parseFloat(va) || 0;
      vb = typeof vb === 'number' ? vb : parseFloat(vb) || 0;
    } else {
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
    }
    if (va < vb) return holdingsSortAsc ? -1 : 1;
    if (va > vb) return holdingsSortAsc ? 1 : -1;
    return 0;
  });

  // Body
  const tbody = document.getElementById('holdingsTbody');
  tbody.innerHTML = sorted.map(row => {
    return '<tr>' + cols.map(col => {
      const cls = numCols.has(col) ? ' class="num"' : '';
      let val = row[col];
      let html = '';
      if (val == null || val === '') html = '';
      else if (numCols.has(col)) {
        const n = typeof val === 'number' ? val : parseFloat(val);
        if (!Number.isFinite(n)) html = _htmlEsc(val);
        else {
          const fmt = col === 'quantity'
            ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })
            : (HOLDINGS_PCT_COLS.has(col) ? n.toFixed(2) + '%' : fmtMoney(n));
          const c = col === 'value' || col === 'cost_basis' ? ''
            : (n < 0 ? 'negative' : (n > 0 ? 'positive' : ''));
          // A return is only meaningful with the span it was measured
          // over, and each filter's span is its own.
          const t = (HOLDINGS_SPAN_COLS.has(col) && row._perfSpan)
            ? ` title="Measured ${_htmlEsc(row._perfSpan)}"` : '';
          html = `<span class="${c}"${t}>${fmt}</span>`;
        }
      } else if (col === 'symbol') {
        html = symLabel(val);
      } else if ((col === 'account_group' || col === 'account_type') && row._transitRoutes) {
        html = `${_htmlEsc(val)} <span class="sub">${[...row._transitRoutes].map(_htmlEsc).join(', ')}</span>`;
      } else if (col === 'account_group' && ACCOUNT_COLORS[val]) {
        html = `<span style="color:${ACCOUNT_COLORS[val]}">${_htmlEsc(val)}</span>`;
      } else if (col === 'account_type' && TYPE_COLORS[val]) {
        html = `<span style="color:${TYPE_COLORS[val]}">${_htmlEsc(val)}</span>`;
      } else if (col === 'sector' && SECTOR_COLORS[val]) {
        html = `<span style="color:${SECTOR_COLORS[val]}">${_htmlEsc(val)}</span>`;
      } else {
        html = _htmlEsc(val);
      }
      return `<td${cls}>${html}</td>`;
    }).join('') + '</tr>';
  }).join('');

  // Total value
  const total = data.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  document.getElementById('holdingsTotalValue').textContent =
    total ? '$' + total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '';

  renderRebalancing();
  applyScrollRegionFocus();
}

// Target vs Actual allocation (Holdings tab).  Reads ANALYTICS.rebalancing
// (computed in analytics/rebalancing.py from data/metadata.csv "Target
// Allocation" rows).  Renders nothing if no targets are defined.  Always
// reflects the latest holdings (not the as-of-date picker).
function renderRebalancing() {
  const el = document.getElementById('rebalanceSection');
  if (!el) return;
  const rb = ANALYTICS.rebalancing;
  if (!rb || !rb.rows || !rb.rows.length) { el.innerHTML = ''; return; }
  const money = (v) => (v < 0 ? '−' : '') + '$' +
    Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
  const rows = rb.rows.map(r => {
    const driftCls = Math.abs(r.drift_pct) < 2 ? 'positive'
      : (Math.abs(r.drift_pct) < 5 ? '' : 'negative');
    const driftStr = (r.drift_pct >= 0 ? '+' : '') + r.drift_pct.toFixed(1) + '%';
    const action = Math.abs(r.action_value) < 1 ? '<span style="color:var(--text-dim);">on target</span>'
      : (r.action_value > 0
          ? `<span class="positive">Buy ${money(r.action_value)}</span>`
          : `<span class="negative">Sell ${money(-r.action_value)}</span>`);
    // Mini bar: current (filled) vs target (tick).
    const maxPct = Math.max(r.current_pct, r.target_pct, 1);
    const curW = (r.current_pct / maxPct) * 100;
    const tgtW = (r.target_pct / maxPct) * 100;
    const bar = `<div class="rb-bar"><div class="rb-bar-fill" style="width:${curW}%;"></div>`
      + `<div class="rb-bar-tick" style="left:${tgtW}%;" title="Target ${r.target_pct}%"></div></div>`;
    return `<tr>
      <td>${_htmlEsc(r.bucket)}</td>
      <td class="num">${r.target_pct.toFixed(1)}%</td>
      <td class="num">${r.current_pct.toFixed(1)}%</td>
      <td style="min-width:140px;">${bar}</td>
      <td class="num ${driftCls}">${driftStr}</td>
      <td class="num">${action}</td>
    </tr>`;
  }).join('');
  const untargeted = rb.untargeted_pct > 0.05
    ? `<div class="chart-empty" style="padding:8px 12px;text-align:left;">
         ${rb.untargeted_pct.toFixed(1)}% (${money(rb.untargeted_value)}) of the portfolio is in sectors with no target.
         ${Math.abs(rb.total_target_pct - 100) > 1 ? `Targets sum to ${rb.total_target_pct.toFixed(0)}%.` : ''}
       </div>`
    : '';
  el.innerHTML = `
    <div class="section-header" style="margin-top:32px;">
      <h2><span style="color:var(--accent);">Target vs Actual</span>
        <span class="scope-label">Latest · ${_htmlEsc(LATEST_DATE)}</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">
        Allocation by sector against your targets · max drift ${rb.max_abs_drift.toFixed(1)}%</span>
    </div>
    <div class="panel">
      <table class="mini-table"><caption class="sr-only">Sector allocation: target share against current share, with the drift and the trade that would close it</caption>
        <thead><tr>
          <th scope="col">Bucket</th><th scope="col" class="num">Target</th><th scope="col" class="num">Current</th>
          <th scope="col">Current vs Target</th><th scope="col" class="num">Drift</th><th scope="col" class="num">Suggested</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${untargeted}
    </div>`;
}

// Holdings header click → sort
document.getElementById('holdingsHeaderRow').addEventListener('click', e => {
  const th = e.target.closest('th');
  if (!th) return;
  const col = th.dataset.hcol;
  if (holdingsSortCol === col) holdingsSortAsc = !holdingsSortAsc;
  else { holdingsSortCol = col; holdingsSortAsc = true; }
  renderHoldings();
  dataControl(document.getElementById('holdingsHeaderRow'), 'hcol', col)?.focus({ preventScroll: true });
});

renderHoldings();

// =========================================================================
// Holdings By Asset — its own table below the rolled-up views.
//
// Sources:
//   - rows = (account_group × symbol) from asOfHoldingsByAccount()
//   - realized gain per symbol from analytics.positions.list
// We keep account_group on each row so users can see which account
// holds each lot — the same symbol can live in multiple accounts.
// =========================================================================

// Realized gain split by (account_group, symbol).  We can't read this
// straight from analytics.positions because that's per-symbol — same
// symbol held in two accounts (e.g., AAPL in Robinhood + Roth IRA)
// would double-count.  Walk the per-txn realized_gain (annotated by
// the FIFO basis walker) instead.
function realizedByAccountSymbol(cutoff) {
  const m = Object.create(null);
  for (const t of txns) {
    if (cutoff && (!t.date || t.date > cutoff)) continue;
    const rg = t.realized_gain;
    if (typeof rg !== 'number' || rg === 0) continue;
    const k = JSON.stringify([t.account_group || '', t.symbol || '']);
    m[k] = (m[k] || 0) + rg;
  }
  return m;
}

let byAssetSortCol = 'value';
let byAssetSortAsc = false;
let byAssetSearch = '';
let byAssetGroupFilter = '';
let byAssetSectorFilter = '';

const byAssetSearchInput = document.getElementById('byAssetSearch');
const byAssetGroupSelect = document.getElementById('byAssetAccountGroupFilter');
const byAssetSectorSelect = document.getElementById('byAssetSectorFilter');
const byAssetCountPill = document.getElementById('byAssetCountPill');

const _byAssetGroups = [...new Set(holdingsByAccount.map(r => r.account_group).filter(Boolean))].sort();
byAssetGroupSelect.innerHTML =
  '<option value="">All account groups</option>' +
  _byAssetGroups.map(g => `<option value="${_htmlEsc(g)}">${_htmlEsc(g)}</option>`).join('');
const _byAssetSectors = [...new Set(holdingsByAsset.map(r => r.sector).filter(Boolean))].sort();
byAssetSectorSelect.innerHTML =
  '<option value="">All sectors</option>' +
  _byAssetSectors.map(s => `<option value="${_htmlEsc(s)}">${_htmlEsc(s)}</option>`).join('');

byAssetSearchInput.addEventListener('input', () => {
  byAssetSearch = byAssetSearchInput.value.toLowerCase().trim();
  renderByAssetTable();
});
byAssetGroupSelect.addEventListener('change', () => {
  byAssetGroupFilter = byAssetGroupSelect.value;
  renderByAssetTable();
});
byAssetSectorSelect.addEventListener('change', () => {
  byAssetSectorFilter = byAssetSectorSelect.value;
  renderByAssetTable();
});

// =========================================================================
// Per-lot inventory (ANALYTICS.lots) — expandable lot detail under each
// Holdings-by-Asset row.  Computed by src/analytics/lots.py from the
// same walker state as the cost-basis figures; the JS only renders.
// Rows expand only at the LATEST as-of date — the lot export describes
// today's pool, not a historical snapshot.
// =========================================================================
const LOTS_BY_KEY = Object.create(null);
for (const p of ((ANALYTICS.lots || {}).positions || [])) {
  LOTS_BY_KEY[(p.account_group || '') + '||' + (p.symbol || '')] = p;
}
const lotsExpanded = new Set();

function lotTermCell(p, l) {
  if (!p.lt_relevant) {
    return '<span style="color:var(--text-dim);" title="No short/long-term distinction (retirement account or option contract)">—</span>';
  }
  if (l.is_long_term) return '<span class="positive" title="Long-term (held > 1 year)">LT</span>';
  if (l.days_to_lt == null) return '—';
  const imminent = l.days_to_lt <= 60;
  return `<span${imminent ? ' style="color:var(--yellow);"' : ''} title="Long-term on ${_htmlEsc(l.lt_eligible_date)}">${l.days_to_lt}d → LT</span>`;
}

function lotOriginCell(origin) {
  const o = origin || 'reconstructed';
  if (o === 'broker') return '<span class="lot-origin lot-origin-broker" title="Basis is a broker-reported figure (Cost Basis override, gain/loss report, RAWTX or 1099 stamping)">broker</span>';
  if (o === 'fmv') return '<span class="lot-origin lot-origin-fmv" title="Basis estimated at fair market value — the true acquisition cost is not visible to fin (e.g. asset received from an external wallet)">FMV est</span>';
  if (o === 'mixed') return '<span class="lot-origin" title="Folded micro lots have differing basis sources">mixed</span>';
  return '<span class="lot-origin" title="Basis reconstructed from the transaction history">txn</span>';
}

function lotDetailHtml(p, colspan) {
  const fmtQty = q => q.toLocaleString(undefined, { maximumFractionDigits: 8 });
  const rows = (p.lots || []).map(l => `<tr>
      <td>${_htmlEsc(l.date || '—')}</td>
      <td class="num">${fmtQty(l.qty)}</td>
      <td class="num">${l.basis_per_share != null ? fmtMoney(l.basis_per_share, 2) : '—'}</td>
      <td class="num">${fmtMoney(l.cost_basis)}</td>
      <td class="num">${l.value != null ? fmtMoney(l.value) : '—'}</td>
      <td class="num">${l.unrealized_gain != null ? `<span class="${l.unrealized_gain < 0 ? 'negative' : 'positive'}">${fmtSigned(l.unrealized_gain)}</span>` : '—'}</td>
      <td class="num">${l.unrealized_pct != null ? fmtPct(l.unrealized_pct) : '—'}</td>
      <td class="num">${l.days_held != null ? l.days_held + 'd' : '—'}</td>
      <td>${lotTermCell(p, l)}</td>
      <td>${lotOriginCell(l.origin)}</td>
    </tr>`);
  if (p.micro) {
    rows.push(`<tr>
      <td style="color:var(--text-dim);" title="Lots under $1 (reward/interest dust) folded into one row.  Totals include them.">··· ${p.micro.count} micro lot(s)</td>
      <td class="num" style="color:var(--text-dim);">${fmtQty(p.micro.qty)}</td>
      <td></td>
      <td class="num" style="color:var(--text-dim);">${fmtMoney(p.micro.cost_basis)}</td>
      <td class="num" style="color:var(--text-dim);">${p.micro.value != null ? fmtMoney(p.micro.value) : '—'}</td>
      <td colspan="4"></td>
      <td>${lotOriginCell(p.micro.origin)}</td>
    </tr>`);
  }
  return `<tr class="lot-detail"><td colspan="${colspan}">
    <div class="lot-detail-inner">
      <table class="lots-table"><caption class="sr-only">Open tax lots for this position</caption>
        <thead><tr>
          <th scope="col">acquired</th><th scope="col" class="num">qty</th><th scope="col" class="num">basis/share</th>
          <th scope="col" class="num">cost basis</th><th scope="col" class="num">value</th>
          <th scope="col" class="num">unrealized</th><th scope="col" class="num">%</th>
          <th scope="col" class="num">held</th><th scope="col">term</th><th scope="col">src</th>
        </tr></thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    </div>
  </td></tr>`;
}

// The heading is shared by Table and Board. Its amount belongs to the
// visible rows, even though the two views keep independent filters.
// A Board notice has no rows, so null clears the amount; an empty filter
// result is a real $0.00 subtotal.
function renderPositionsTotal(total, scope = '') {
  const el = document.getElementById('byAssetTotalValue');
  if (!el) return;
  el.textContent = total == null ? '' : fmtMoney(total);
  el.title = scope;
}

function renderByAssetTable() {
  const realizedToDate = realizedByAccountSymbol(isAsOfLatest() ? null : getAsOfSnapshot()?.date || asOfDate);
  const rows = asOfHoldingsByAccount().map(r => {
    const realized = r._inTransit ? 0 : (realizedToDate[JSON.stringify([r.account_group || '', r.symbol || ''])] || 0);
    const value = (typeof r.value === 'number') ? r.value : null;
    const cb = (typeof r.cost_basis === 'number') ? r.cost_basis : null;
    const unreal = (value != null && cb != null) ? +(value - cb).toFixed(2) : null;
    const totalGain = (unreal ?? 0) + realized;
    // % return uses cost_basis if known, otherwise null. Realized-only
    // closed positions divide by 0 here so render as null.
    const pct = (cb && cb > 0) ? +((totalGain / cb) * 100).toFixed(2) : null;
    return {
      account_group: r._inTransit ? `In transit · ${r._transitRoute}` : r.account_group,
      symbol: r.symbol,
      sector: r.sector,
      quantity: r.quantity,
      price: r.price,
      value,
      cost_basis: cb,
      unrealized_gain: unreal,
      realized_gain: +realized.toFixed(2),
      total_return: +totalGain.toFixed(2),
      pct_return: pct,
    };
  });

  const filtered = rows.filter(r => {
    if (byAssetGroupFilter && r.account_group !== byAssetGroupFilter) return false;
    if (byAssetSectorFilter && r.sector !== byAssetSectorFilter) return false;
    if (byAssetSearch) {
      const hay = [r.symbol, r.sector, r.account_group].map(x => String(x ?? '').toLowerCase()).join(' ');
      if (!hay.includes(byAssetSearch)) return false;
    }
    return true;
  });

  const cols = ['symbol', 'account_group', 'sector', 'quantity', 'price',
    'value', 'cost_basis', 'unrealized_gain', 'realized_gain',
    'total_return', 'pct_return'];
  const numCols = new Set(['quantity', 'price', 'value', 'cost_basis',
    'unrealized_gain', 'realized_gain', 'total_return',
    'pct_return']);

  const hRow = document.getElementById('byAssetHeaderRow');
  hRow.innerHTML = cols.map(col => {
    const cls = numCols.has(col) ? ' class="num"' : '';
    const arrow = byAssetSortCol === col ? (byAssetSortAsc ? ' ▲' : ' ▼') : '';
    const sorted = byAssetSortCol === col
      ? ` aria-sort="${byAssetSortAsc ? 'ascending' : 'descending'}"` : '';
    return `<th scope="col"${cls} data-bcol="${col}"${sorted} tabindex="0" role="columnheader"
      >${fieldLabel(col)}<span class="arrow">${arrow}</span></th>`;
  }).join('');

  filtered.sort((a, b) => {
    let va = a[byAssetSortCol] ?? '', vb = b[byAssetSortCol] ?? '';
    if (numCols.has(byAssetSortCol)) {
      va = typeof va === 'number' ? va : (parseFloat(va) || 0);
      vb = typeof vb === 'number' ? vb : (parseFloat(vb) || 0);
    } else {
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
    }
    if (va < vb) return byAssetSortAsc ? -1 : 1;
    if (va > vb) return byAssetSortAsc ? 1 : -1;
    return 0;
  });

  const tbody = document.getElementById('byAssetTbody');
  tbody.innerHTML = filtered.map(row => {
    // Expandable per-lot detail — latest as-of only (see LOTS_BY_KEY).
    const lotKey = (row.account_group || '') + '||' + (row.symbol || '');
    const lp = isAsOfLatest() ? LOTS_BY_KEY[lotKey] : null;
    const expandable = !!(lp && ((lp.lots && lp.lots.length) || lp.micro));
    const expanded = expandable && lotsExpanded.has(lotKey);
    let rowHtml = `<tr${expandable ? ` class="lot-toggle" data-lotkey="${_htmlEsc(lotKey)}"` : ''}>` + cols.map(col => {
      const cls = numCols.has(col) ? ' class="num"' : '';
      const val = row[col];
      let html = '';
      if (val == null || val === '') html = '';
      else if (numCols.has(col)) {
        const n = typeof val === 'number' ? val : parseFloat(val);
        if (!Number.isFinite(n)) html = _htmlEsc(val);
        else {
          const fmt = col === 'quantity'
            ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })
            : (col === 'pct_return'
              ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%'
              : fmtMoney(n));
          const c = ['quantity', 'price', 'value', 'cost_basis'].includes(col) ? ''
            : (n < 0 ? 'negative' : (n > 0 ? 'positive' : ''));
          html = `<span class="${c}">${fmt}</span>`;
        }
      } else if (col === 'symbol') {
        const chev = expandable
          ? `<span class="lot-chev">${expanded ? '▾' : '▸'}</span>` : '';
        html = expandable
          ? `<button type="button" class="lot-disclosure" data-lotkey="${_htmlEsc(lotKey)}" aria-expanded="${expanded}" aria-label="${_htmlEsc('Lots for ' + val + ' in ' + row.account_group)}">${chev}${symLabel(val)}</button>`
          : symLabel(val);
      } else if (col === 'account_group' && ACCOUNT_COLORS[val]) {
        html = `<span style="color:${ACCOUNT_COLORS[val]}">${_htmlEsc(val)}</span>`;
      } else if (col === 'sector' && SECTOR_COLORS[val]) {
        html = `<span style="color:${SECTOR_COLORS[val]}">${_htmlEsc(val)}</span>`;
      } else {
        html = _htmlEsc(val);
      }
      return `<td${cls}>${html}</td>`;
    }).join('') + '</tr>';
    if (expanded) rowHtml += lotDetailHtml(lp, cols.length);
    return rowHtml;
  }).join('');

  // Totals + count
  const totalValue = filtered.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  // Read the visible layout from the DOM: this renderer also runs while
  // the bundle loads, before 15-board.js initializes its layout state.
  if (document.getElementById('byAssetBody')?.style.display !== 'none') {
    renderPositionsTotal(totalValue, 'Market value of the filtered Table positions');
  }
  byAssetCountPill.textContent = `${filtered.length} / ${rows.length}`;
  applyScrollRegionFocus();
}

document.getElementById('byAssetHeaderRow').addEventListener('click', e => {
  const th = e.target.closest('th');
  if (!th) return;
  const col = th.dataset.bcol;
  if (byAssetSortCol === col) byAssetSortAsc = !byAssetSortAsc;
  else { byAssetSortCol = col; byAssetSortAsc = false; }  // numeric cols default desc
  renderByAssetTable();
  dataControl(document.getElementById('byAssetHeaderRow'), 'bcol', col)?.focus({ preventScroll: true });
});

// Toggle a row's per-lot detail.  Delegated — rows re-render on every
// sort/filter change, so per-row listeners wouldn't survive.
document.getElementById('byAssetTbody').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-lotkey]');
  if (!tr) return;
  const k = tr.dataset.lotkey;
  if (lotsExpanded.has(k)) lotsExpanded.delete(k);
  else lotsExpanded.add(k);
  renderByAssetTable();
  const disclosure = [...document.querySelectorAll('button.lot-disclosure')].find(b => b.dataset.lotkey === k);
  if (disclosure) disclosure.focus({ preventScroll: true });
  applyScrollRegionFocus();
});

renderByAssetTable();

// =========================================================================
// Lot Method Comparison
// =========================================================================

let basisTypeFilter = 'Retirement';
const BASIS_TYPE_BTNS = {
  'all': 'btnBasisAll',
  'Taxable': 'btnBasisTax',
  'Retirement': 'btnBasisRet',
};

function setBasisTypeFilter(t) {
  basisTypeFilter = t;
  for (const [v, id] of Object.entries(BASIS_TYPE_BTNS)) {
    document.getElementById(id).classList.toggle('active', v === t);
  }
  renderBasisTable();
}

function renderBasisTable() {
  const tbody = document.getElementById('basisTbody');
  const head = document.getElementById('basisHeaderRow');
  const cols = ['method', 'cost_basis', 'value', 'unrealized_gain', 'realized_gain'];
  const labels = {
    method: 'Method',
    cost_basis: 'Cost Basis',
    value: 'Value',
    unrealized_gain: 'Unrealized P&L',
    realized_gain: 'Realized P&L',
  };
  const numCols = new Set(['cost_basis', 'value', 'unrealized_gain', 'realized_gain']);
  head.innerHTML = cols.map(c => {
    const cls = numCols.has(c) ? ' class="num"' : '';
    return `<th scope="col"${cls}>${labels[c]}</th>`;
  }).join('');

  if (!basisMethods || !Object.keys(basisMethods).length) {
    tbody.innerHTML = `<tr><td colspan="${cols.length}" style="color:var(--text-dim);padding:12px;">
      No basis data. Run the pipeline.</td></tr>`;
    return;
  }

  const methodList = ['fifo', 'lifo', 'hifo', 'avg'];
  const methodLabels = { fifo: 'FIFO', lifo: 'LIFO', hifo: 'HIFO', avg: 'Average' };

  tbody.innerHTML = methodList.map(m => {
    const block = basisMethods[m] || {};
    let row;
    if (basisTypeFilter === 'all') {
      row = block.totals || {};
    } else {
      row = (block.totals_by_type && block.totals_by_type[basisTypeFilter]) || {};
    }
    // Realized P&L isn't split by account_type in the export (sells can
    // span types).  Show the whole-portfolio realized for every method.
    const realized = (block.totals && block.totals.realized_gain) || 0;
    const showAll = basisTypeFilter === 'all';

    const cells = cols.map(c => {
      if (c === 'method') return `<td>${methodLabels[m]}</td>`;
      let val = c === 'realized_gain' ? realized : row[c];
      if (val == null) return '<td class="num"></td>';
      let fmt, cls = 'num';
      if (c === 'unrealized_gain' || c === 'realized_gain') {
        fmt = fmtSigned(val);
        cls += val < 0 ? ' negative' : (val > 0 ? ' positive' : '');
      } else {
        fmt = fmtMoney(val);
      }
      const titleAttr = (c === 'realized_gain' && !showAll)
        ? ' title="Realized P&amp;L is not split by account type; shows the whole-portfolio figure for this method."'
        : '';
      return `<td class="${cls}"${titleAttr}>${fmt}</td>`;
    });
    return `<tr>${cells.join('')}</tr>`;
  }).join('');

  // One-line summary on the collapsed <details> so the headline delta
  // is visible without expanding: widest realized-P&L spread between
  // FIFO and any alternative method.
  const line = document.getElementById('basisSummaryLine');
  if (line) {
    const fifoRe = ((basisMethods.fifo || {}).totals || {}).realized_gain;
    let maxDelta = 0, maxMethod = null;
    for (const m of ['lifo', 'hifo', 'avg']) {
      const re = ((basisMethods[m] || {}).totals || {}).realized_gain;
      if (typeof re === 'number' && typeof fifoRe === 'number'
          && Math.abs(re - fifoRe) > Math.abs(maxDelta)) {
        maxDelta = re - fifoRe;
        maxMethod = methodLabels[m];
      }
    }
    line.textContent = (maxMethod && Math.abs(maxDelta) >= 1)
      ? `realized P&L under ${maxMethod} differs from FIFO by ${fmtSigned(maxDelta)}`
      : 'FIFO vs LIFO / HIFO / Average — negligible difference';
  }
}

renderBasisTable();
