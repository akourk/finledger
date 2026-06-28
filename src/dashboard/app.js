const DATA = __JSON_DATA__;

const txns = DATA.transactions || [];
const holdingsByAsset = DATA.holdings || [];
const holdingsByAccount = DATA.holdings_by_account || [];
const history = DATA.history || [];
const basisMethods = DATA.basis_methods || {};
const cashSummary = DATA.cash_summary || {};
// Pre-computed analytics from src/analytics.py.  Single source of truth
// for rollover bridges, retirement contributions by year, and
// per-account annual returns + TWR summaries.  The dashboard should
// PREFER reading from here rather than recomputing — avoids drift
// between views that display the same metric.
const ANALYTICS = DATA.analytics || {};
const ANALYTICS_PERF = ANALYTICS.performance_by_filter || {};

// Rollover bridges now come from Python analytics (src/analytics.py).
// The dashboard-side helper is just a shim that applies the bridge
// to a snapshot date for a given account filter.
const _ROLLOVER_BRIDGES = ANALYTICS.rollover_bridges || [];

// User-maintained metadata from data/metadata.csv (birthday,
// salary history, bonus history, annual expenses, year-end targets).
// Hoisted to the top because renderAnnualBreakdown (Overview tab,
// rendered immediately on load) reads RETIREMENT_META.targets and
// RETIREMENT_META.birthday — the original declaration further down
// the file produced a temporal-dead-zone error that blocked every
// tab from loading.
const RETIREMENT_META = DATA.retirement_meta || {};

// --- Symbol → sector lookup (covers every symbol ever seen, not just
// current holdings).  Populated by main.py from sectors.get_sector().
const SECTOR_OF = DATA.sector_of || {};
// --- Symbol → short display name lookup.  One entry per proxy-mapped
// symbol (multi-word fund names, ticker-format aliases); the display
// defaults to the proxy ticker unless the proxy map entry has a
// `display` override.  Unmapped symbols render as-is.
const DISPLAY_OF = DATA.display_of || {};

// HTML-escape helper for the few places we inject raw symbols into
// attribute values (title="..." for tooltip).
function _htmlEsc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Render a symbol as compact HTML: proxy-mapped symbols show their
// short display ("VFIAX" rather than "Vanguard Employee Benefit Index
// Fund"), long unmapped symbols (option contracts) get CSS truncation
// via the `.sym-cell` class.  In both cases the raw symbol lives in a
// `title` attribute so hovering reveals the full name.  Callers
// wrap with <b>…</b> themselves if bold is wanted (existing convention).
function symLabel(sym) {
  if (!sym) return '';
  const display = DISPLAY_OF[sym] || sym;
  const needsTip = display !== sym || sym.length > 24;
  const tip = needsTip ? ` title="${_htmlEsc(sym)}"` : '';
  return `<span class="sym-cell"${tip}>${_htmlEsc(display)}</span>`;
}
// --- Account group → account type map.  Derived client-side from
// txns + current holdings (every row carries both fields) — covers
// every historical account group.
const ACCOUNT_TYPE_OF = (() => {
  const m = {};
  for (const t of txns) {
    if (t.account_group && t.account_type) m[t.account_group] = t.account_type;
  }
  for (const h of holdingsByAccount) {
    if (h.account_group && h.account_type) m[h.account_group] = h.account_type;
  }
  return m;
})();

// =========================================================================
// As-of-date state — lets the user view holdings / overview stats /
// allocation at any past snapshot date.  Uses the per-snapshot
// `positions` list emitted by src/history.py, so FIFO state is
// exactly what it was on that date (no JS replay of the basis walker).
// Default: the latest snapshot (today's state, identical to the live
// holdings table).
// =========================================================================
let asOfDate = history.length
  ? history[history.length - 1].date
  : new Date().toISOString().slice(0, 10);
const LATEST_DATE = asOfDate;

function isAsOfLatest() { return asOfDate === LATEST_DATE; }

// Find snapshot matching asOfDate (exact, else nearest <= that date).
function getAsOfSnapshot() {
  if (!history.length) return null;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].date <= asOfDate) return history[i];
  }
  return history[0];
}

// Position list shaped like holdings_by_account (enriches with
// account_type + sector + unrealized_gain).  For the latest snapshot we
// return the live holdingsByAccount directly so everything stays
// consistent with the basis methods table and live stat cards.
function asOfHoldingsByAccount() {
  if (isAsOfLatest()) return holdingsByAccount;
  const snap = getAsOfSnapshot();
  if (!snap || !snap.positions) return [];
  return snap.positions.map(p => {
    const value = p.value;
    const cb = p.cost_basis;
    const ug = (typeof value === 'number' && typeof cb === 'number')
      ? +(value - cb).toFixed(2) : null;
    return {
      account_group: p.account_group,
      account_type: ACCOUNT_TYPE_OF[p.account_group] || 'Taxable',
      symbol: p.symbol,
      quantity: p.quantity,
      price: p.price,
      value: value,
      cost_basis: cb,
      unrealized_gain: ug,
      sector: SECTOR_OF[p.symbol] || 'Other',
    };
  });
}

// Aggregate by symbol — same shape as holdings[].  Sector comes from
// SECTOR_OF; price is derived as value/quantity to match the aggregate.
function asOfHoldingsByAsset() {
  if (isAsOfLatest()) return holdingsByAsset;
  const rows = asOfHoldingsByAccount();
  const agg = {};
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
  const txt = isAsOfLatest() ? '' : ' (as of ' + asOfDate + ')';
  document.querySelectorAll('.as-of-label').forEach(el => { el.textContent = txt; });
  if (typeof renderStats === 'function') renderStats();
  if (typeof renderTopHoldings === 'function') renderTopHoldings();
  if (typeof renderAllocation === 'function') renderAllocation();
  if (typeof renderHoldings === 'function') renderHoldings();
  if (typeof renderByAssetTable === 'function') renderByAssetTable();
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
    return `<option value="${h.date}">${label}</option>`;
  }).join('');
  document.querySelectorAll('.as-of-picker').forEach(sel => {
    sel.innerHTML = opts;
    sel.value = asOfDate;
  });
})();

function _rolloverBridgeAdjustment(snapshotDate, filterSet) {
  if (!_ROLLOVER_BRIDGES.length || !snapshotDate) return 0;
  let adj = 0;
  for (const b of _ROLLOVER_BRIDGES) {
    if (filterSet && !filterSet.has(b.group)) continue;
    if (snapshotDate >= b.start_date && snapshotDate < b.end_date) adj += b.amount;
  }
  return adj;
}
document.getElementById('generated').textContent = DATA.generated || '';

// Render the persistent top-bar summary: portfolio value, total
// return ($ + %), and 1-day change ($ + %).  Reads precomputed
// header_summary from analytics.py for the 1-day figures (needs
// server-side price cache) and derives total return from cash
// summary + FIFO basis totals on the client.  Called once on load;
// the values are frozen at pipeline-run time, so a "refresh" is
// really "regenerate & reload".
function renderTopBarSummary() {
  const el = document.getElementById('topBarSummary');
  if (!el) return;
  const hs = (ANALYTICS.header_summary) || {};
  const fifo = (basisMethods.fifo && basisMethods.fifo.totals) || {};
  const netC = cashSummary.net_contributed || 0;

  const value = hs.value != null ? hs.value
    : (holdingsByAsset.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0));
  // Total return = what you have minus what you put in (net of withdrawals).
  // Realized/dividends already reflected in current value, so don't double-count.
  const totalReturn = value - netC;
  const totalReturnPct = netC > 0 ? (totalReturn / netC) * 100 : null;
  const ch = hs.change_1d;
  const chPct = hs.change_1d_pct != null ? hs.change_1d_pct * 100 : null;

  const clsOf = v => v == null ? '' : (v >= 0 ? 'positive' : 'negative');
  const pctStr = (v, d = 2) => v == null
    ? ''
    : ` <span class="sub">${v >= 0 ? '+' : ''}${v.toFixed(d)}%</span>`;

  const parts = [
    `<div class="tb-item">
       <span class="tb-label">Portfolio Value</span>
       <span class="tb-value positive">${fmtMoney(value)}</span>
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
  if (v == null || isNaN(v)) return '—';
  const n = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  return sign + '$' + n.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}
function fmtSigned(v) {
  if (v == null || isNaN(v)) return '—';
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

// --- Tab router ---
// Each tab has a render function; first-activation is when the tab
// content actually gets built (keeps initial load fast).
const TAB_RENDERERS = {};   // name -> function
const TAB_RENDERED = new Set();   // names that have been rendered at least once

function registerTabRenderer(name, fn) {
  TAB_RENDERERS[name] = fn;
}

function activateTab(name, opts) {
  opts = opts || {};
  const panel = document.getElementById('tab-' + name);
  if (!panel) return;
  // Swap visible panel + active pill
  document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el =>
    el.classList.toggle('active', el.dataset.tab === name));
  panel.classList.add('active');
  // Lazy render on first activation
  if (!TAB_RENDERED.has(name) && TAB_RENDERERS[name]) {
    try { TAB_RENDERERS[name](); } catch (e) { console.error('tab render failed', name, e); }
    TAB_RENDERED.add(name);
  }
  // Always re-call the Overview tab's chart render so resize/layout is right
  if (name === 'overview' && typeof renderHistory === 'function') renderHistory();
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
  const out = {};
  for (const row of rows) {
    const k = row[key] || 'Unknown';
    if (!out[k]) out[k] = { [key]: k, value: 0 };
    out[k].value += (typeof row.value === 'number' ? row.value : 0);
  }
  return Object.values(out);
}

// Same as groupBy but also rolls up cost_basis and unrealized_gain.
function groupByBasisAware(rows, key) {
  const out = {};
  for (const row of rows) {
    const k = row[key] || 'Unknown';
    if (!out[k]) out[k] = { [key]: k, value: 0, cost_basis: 0, unrealized_gain: 0, _anyBasis: false };
    if (typeof row.value === 'number') out[k].value += row.value;
    if (typeof row.cost_basis === 'number') {
      out[k].cost_basis += row.cost_basis;
      out[k]._anyBasis = true;
    }
    if (typeof row.unrealized_gain === 'number') out[k].unrealized_gain += row.unrealized_gain;
  }
  // If no row had a known cost_basis, zero it out for display cleanliness.
  return Object.values(out).map(r => {
    if (!r._anyBasis) { r.cost_basis = null; r.unrealized_gain = null; }
    delete r._anyBasis;
    return r;
  });
}

function renderHoldings() {
  // By Account: one row per account_group total.
  // By Type:    one row per account_type total (Taxable/Retirement/Savings).
  // By Sector:  one row per sector total — sourced from the by-asset holdings
  //             list so the symbol→sector mapping is 1:1.
  // When asOfDate != latest, rows come from the snapshot's `positions`
  // list (FIFO state as of that date) instead of live holdings.
  const byAccountSrc = asOfHoldingsByAccount();
  const byAssetSrc = asOfHoldingsByAsset();

  let data, cols, numCols;
  if (holdingsView === 'account') {
    data = groupByBasisAware(byAccountSrc, 'account_group');
    cols = ['account_group', 'value', 'cost_basis', 'unrealized_gain'];
    numCols = new Set(['value', 'cost_basis', 'unrealized_gain']);
  } else if (holdingsView === 'type') {
    data = groupByBasisAware(byAccountSrc, 'account_type');
    cols = ['account_type', 'value', 'cost_basis', 'unrealized_gain'];
    numCols = new Set(['value', 'cost_basis', 'unrealized_gain']);
  } else if (holdingsView === 'sector') {
    data = groupByBasisAware(byAssetSrc, 'sector');
    cols = ['sector', 'value', 'cost_basis', 'unrealized_gain'];
    numCols = new Set(['value', 'cost_basis', 'unrealized_gain']);
  }

  // Header
  const hRow = document.getElementById('holdingsHeaderRow');
  hRow.innerHTML = cols.map(col => {
    const cls = numCols.has(col) ? ' class="num"' : '';
    const arrow = holdingsSortCol === col ? (holdingsSortAsc ? ' ▲' : ' ▼') : '';
    return `<th${cls} data-hcol="${col}">${col}<span class="arrow">${arrow}</span></th>`;
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
        if (isNaN(n)) html = val;
        else {
          const fmt = col === 'quantity'
            ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })
            : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          const c = n < 0 ? 'negative' : (n > 0 ? 'positive' : '');
          html = `<span class="${c}">${fmt}</span>`;
        }
      } else if (col === 'symbol') {
        html = symLabel(val);
      } else if (col === 'account_group' && ACCOUNT_COLORS[val]) {
        html = `<span style="color:${ACCOUNT_COLORS[val]}">${val}</span>`;
      } else if (col === 'account_type' && TYPE_COLORS[val]) {
        html = `<span style="color:${TYPE_COLORS[val]}">${val}</span>`;
      } else if (col === 'sector' && SECTOR_COLORS[val]) {
        html = `<span style="color:${SECTOR_COLORS[val]}">${val}</span>`;
      } else {
        html = String(val);
      }
      return `<td${cls}>${html}</td>`;
    }).join('') + '</tr>';
  }).join('');

  // Total value
  const total = data.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  document.getElementById('holdingsTotalValue').textContent =
    total ? '$' + total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '';

  renderRebalancing();
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
      <h2><span style="color:var(--accent);">Target vs Actual</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">
        allocation drift by sector vs <code>Target Allocation</code> in metadata.csv · max drift ${rb.max_abs_drift.toFixed(1)}%</span>
    </div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Bucket</th><th class="num">Target</th><th class="num">Current</th>
          <th>Current vs Target</th><th class="num">Drift</th><th class="num">Suggested</th>
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
const REALIZED_BY_ACCT_SYM = (() => {
  const m = {};
  for (const t of txns) {
    const rg = t.realized_gain;
    if (typeof rg !== 'number' || rg === 0) continue;
    const k = (t.account_group || '') + '||' + (t.symbol || '');
    m[k] = (m[k] || 0) + rg;
  }
  return m;
})();

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
  _byAssetGroups.map(g => `<option value="${g}">${g}</option>`).join('');
const _byAssetSectors = [...new Set(holdingsByAsset.map(r => r.sector).filter(Boolean))].sort();
byAssetSectorSelect.innerHTML =
  '<option value="">All sectors</option>' +
  _byAssetSectors.map(s => `<option value="${s}">${s}</option>`).join('');

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

function renderByAssetTable() {
  const rows = asOfHoldingsByAccount().map(r => {
    const realized = REALIZED_BY_ACCT_SYM[(r.account_group || '') + '||' + (r.symbol || '')] || 0;
    const value = (typeof r.value === 'number') ? r.value : null;
    const cb = (typeof r.cost_basis === 'number') ? r.cost_basis : null;
    const unreal = (value != null && cb != null) ? +(value - cb).toFixed(2) : null;
    const totalGain = (unreal ?? 0) + realized;
    // % return uses cost_basis if known, otherwise null. Realized-only
    // closed positions divide by 0 here so render as null.
    const pct = (cb && cb > 0) ? +((totalGain / cb) * 100).toFixed(2) : null;
    return {
      account_group: r.account_group,
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

  const cols = ['account_group', 'symbol', 'sector', 'quantity', 'price',
    'value', 'cost_basis', 'unrealized_gain', 'realized_gain',
    'total_return', 'pct_return'];
  const numCols = new Set(['quantity', 'price', 'value', 'cost_basis',
    'unrealized_gain', 'realized_gain', 'total_return',
    'pct_return']);

  const hRow = document.getElementById('byAssetHeaderRow');
  hRow.innerHTML = cols.map(col => {
    const cls = numCols.has(col) ? ' class="num"' : '';
    const arrow = byAssetSortCol === col ? (byAssetSortAsc ? ' ▲' : ' ▼') : '';
    return `<th${cls} data-bcol="${col}">${col}<span class="arrow">${arrow}</span></th>`;
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
    return '<tr>' + cols.map(col => {
      const cls = numCols.has(col) ? ' class="num"' : '';
      const val = row[col];
      let html = '';
      if (val == null || val === '') html = '';
      else if (numCols.has(col)) {
        const n = typeof val === 'number' ? val : parseFloat(val);
        if (isNaN(n)) html = String(val);
        else {
          const fmt = col === 'quantity'
            ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })
            : (col === 'pct_return'
              ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%'
              : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
          const c = n < 0 ? 'negative' : (n > 0 ? 'positive' : '');
          html = `<span class="${c}">${fmt}</span>`;
        }
      } else if (col === 'symbol') {
        html = symLabel(val);
      } else if (col === 'account_group' && ACCOUNT_COLORS[val]) {
        html = `<span style="color:${ACCOUNT_COLORS[val]}">${val}</span>`;
      } else if (col === 'sector' && SECTOR_COLORS[val]) {
        html = `<span style="color:${SECTOR_COLORS[val]}">${val}</span>`;
      } else {
        html = String(val);
      }
      return `<td${cls}>${html}</td>`;
    }).join('') + '</tr>';
  }).join('');

  // Totals + count
  const totalValue = filtered.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  document.getElementById('byAssetTotalValue').textContent =
    totalValue ? '$' + totalValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '';
  byAssetCountPill.textContent = `${filtered.length} / ${rows.length}`;
}

document.getElementById('byAssetHeaderRow').addEventListener('click', e => {
  const th = e.target.closest('th');
  if (!th) return;
  const col = th.dataset.bcol;
  if (byAssetSortCol === col) byAssetSortAsc = !byAssetSortAsc;
  else { byAssetSortCol = col; byAssetSortAsc = false; }  // numeric cols default desc
  renderByAssetTable();
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
    return `<th${cls}>${labels[c]}</th>`;
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
}

renderBasisTable();

// =========================================================================
// History chart
// =========================================================================

// `history` is declared at the top of this script (shared with the
// as-of-date infrastructure).  Do not redeclare here.
// Each selected series is encoded as a string key:
//   'total'             — total portfolio value
//   'account:{NAME}'    — one account group
//   'type:{NAME}'       — one account type
//   'sector:{NAME}'     — one sector
// Users toggle any subset; the chart draws one line per selection.
const historySelection = new Set(['total']);
// Window enum — shared by both the History chart (Overview tab) and
// the Performance tab's stat cards / benchmark chart.  Defined here
// near the top so the History chart's filteredHistory() (which calls
// _windowCutoffIso) can use it during initial render.  The Performance
// tab consumes the same constants further down.
const PERF_WINDOWS = ['lifetime', '5y', '3y', '2y', '1y', '6mo', '3mo', '30day', 'ytd'];
const PERF_WINDOW_LABEL = {
  'lifetime': 'Lifetime', '5y': '5y', '3y': '3y', '2y': '2y', '1y': '1y',
  '6mo': '6mo', '3mo': '3mo', '30day': '30d', 'ytd': 'YTD',
};
const PERF_WINDOW_DAYS = {
  '5y': 5 * 365, '3y': 3 * 365, '2y': 2 * 365, '1y': 365,
  '6mo': 183, '3mo': 91, '30day': 30,
};
const PERF_TWR_PRESETS = [
  'lifetime', '5y', '3y', '2y', '1y', '6mo', '3mo', '30day', 'ytd', 'custom',
];
const PERF_TWR_PRESET_LABEL = {
  ...PERF_WINDOW_LABEL, 'custom': 'Custom',
};

// Resolve a window key to an ISO cutoff date (inclusive lower bound).
// ``referenceIso`` is the "today" anchor — when called from the
// dashboard it's the latest snapshot's date so the cutoff is stable
// across reloads.  YTD is calendar-based (Jan 1 of reference year).
// 'custom' resolves via perfTwrStart (caller is responsible for
// honoring perfTwrEnd separately).  'lifetime' returns '' (no cutoff).
function _windowCutoffIso(windowKey, referenceIso) {
  if (!referenceIso) return '';
  if (windowKey === 'lifetime') return '';
  if (windowKey === 'custom') return perfTwrStart || '';
  if (windowKey === 'ytd') return referenceIso.slice(0, 4) + '-01-01';
  const days = PERF_WINDOW_DAYS[windowKey];
  if (!days) return '';
  const ref = new Date(referenceIso);
  const cutoff = new Date(ref);
  cutoff.setDate(cutoff.getDate() - days);
  return cutoff.toISOString().slice(0, 10);
}

// Unified with the Performance tab's window enum (PERF_WINDOWS).
// 'lifetime' replaces the old 'all'.  History also supports 'custom'
// with explicit From/To dates via the date pickers.
let historyRange = 'lifetime';
let historyCustomStart = ''; // used when historyRange === 'custom'
let historyCustomEnd = '';
const historyOverlays = new Set();  // 'basis' / 'gain' / 'benchmark'
// Legend click-to-hide is kept as a secondary way to temporarily mute a
// series without untoggling its pill; the set is keyed on the
// display label (e.g. "Robinhood" or "Robinhood Basis").
const seriesHidden = new Set();

// Enumerate available groups for the pill bar, from the history data itself.
function availableKeys(field) {
  const keys = new Set();
  history.forEach(h => Object.keys(h[field] || {}).forEach(k => keys.add(k)));
  return [...keys].sort();
}
const ACCOUNT_OPTIONS = availableKeys('by_account_group');
const TYPE_OPTIONS = availableKeys('by_account_type');
const SECTOR_OPTIONS = availableKeys('by_sector');

function seriesColorFor(key) {
  if (key === 'total') return '#4ade80';
  const [kind, name] = key.split(':');
  if (kind === 'account') return ACCOUNT_COLORS[name] || '#9ca3af';
  if (kind === 'type') return TYPE_COLORS[name] || '#9ca3af';
  if (kind === 'sector') return SECTOR_COLORS[name] || '#9ca3af';
  return '#9ca3af';
}
function seriesLabel(key) {
  if (key === 'total') return 'Total';
  return key.split(':')[1];
}

function setHistoryRange(r) {
  historyRange = r;
  // Switching to a preset clears the custom window so the inputs
  // reflect the preset's derived range; switching *to* 'custom' when
  // no bounds are set yet seeds them from the current view.
  if (r === 'custom') {
    if (!historyCustomStart || !historyCustomEnd) {
      const curr = filteredHistory();
      if (curr.length) {
        historyCustomStart = historyCustomStart || curr[0].date;
        historyCustomEnd = historyCustomEnd || curr[curr.length - 1].date;
      }
    }
  }
  renderHistControls();
  renderHistory();
}

function setHistoryCustomStart(d) {
  historyCustomStart = d;
  if (historyCustomStart && historyCustomEnd && historyCustomStart > historyCustomEnd) {
    historyCustomEnd = historyCustomStart;
  }
  historyRange = 'custom';
  renderHistControls();
  renderHistory();
}
function setHistoryCustomEnd(d) {
  historyCustomEnd = d;
  if (historyCustomStart && historyCustomEnd && historyCustomEnd < historyCustomStart) {
    historyCustomStart = historyCustomEnd;
  }
  historyRange = 'custom';
  renderHistControls();
  renderHistory();
}

function toggleHistoryOverlay(name) {
  if (historyOverlays.has(name)) historyOverlays.delete(name);
  else historyOverlays.add(name);
  renderHistControls();
  renderHistory();
}

function toggleHistorySeries(key) {
  if (historySelection.has(key)) historySelection.delete(key);
  else historySelection.add(key);
  renderHistControls();
  renderHistory();
}

function setHistoryGroupAll(field, on) {
  // Bulk-toggle a whole category (e.g. check/uncheck all sectors)
  const keys = field === 'account' ? ACCOUNT_OPTIONS
    : field === 'type' ? TYPE_OPTIONS
      : SECTOR_OPTIONS;
  for (const k of keys) {
    const sk = field + ':' + k;
    if (on) historySelection.add(sk);
    else historySelection.delete(sk);
  }
  renderHistControls();
  renderHistory();
}

// Returns null if no filter is active (use lifetime simulation), or
// a (txn) => bool predicate matching the user's account/type filter.
// Sector filters fall through to null since attributing a benchmark
// simulation to a sector mix is ambiguous.
function _activeBenchFilterFn() {
  const accountKeys = [...historySelection].filter(k => k.startsWith('account:'));
  const typeKeys = [...historySelection].filter(k => k.startsWith('type:'));
  const sectorKeys = [...historySelection].filter(k => k.startsWith('sector:'));
  const hasTotal = historySelection.has('total');
  // Total selected → lifetime view.  Sector filter → not supported.
  if (hasTotal) return null;
  if (sectorKeys.length) return null;
  if (accountKeys.length) {
    const set = new Set(accountKeys.map(k => k.slice('account:'.length)));
    return (t) => set.has(t.account_group);
  }
  if (typeKeys.length) {
    const set = new Set(typeKeys.map(k => k.slice('type:'.length)));
    return (t) => set.has(t.account_type);
  }
  return null;
}

// Window-anchored mirror-withdrawals SPY/BND/VXUS simulation.
// Returns ``[{date, value}, ...]`` synced to ``hist`` snapshot dates,
// or null if no usable price data.
//
// **Methodology**: anchor a hypothetical SPY-equivalent portfolio at
// the value the user's filtered portfolio had at ``windowStartIso``,
// then walk filtered txns through the window mirroring every cash
// flow (deposits buy shares, withdrawals sell shares).  Shares cap
// at zero — you can't sell what you don't own.
//
// Why anchor at window start?  Two earlier attempts both broke:
//
//   1. Lifetime cap-at-0 (the original): for accounts where
//      cumulative withdrawals exceeded deposits before the window,
//      shares were already at zero by window-start, producing a
//      visually flat-at-zero SPY line for the whole window.
//      Uninformative.
//
//   2. Buy-and-hold (the recent attempt): only counted positive
//      cash flows, ignored withdrawals.  Worked for filtered views
//      but DOUBLE-COUNTED internal transfers when filter == null
//      (Total).  Example: user moves $95k from Coinbase to Apple
//      Savings — Coinbase withdrawal is ignored, Apple Savings
//      deposit buys $95k of simulated SPY.  Inflated the Total/
//      Lifetime SPY line by ~$100k around March 2024 alone.
//
// Window-anchored mirror handles both: gives the simulation a
// non-zero starting position equal to the user's filtered portfolio
// at window-start (so the line isn't stuck at zero), and mirrors
// withdrawals so paired internal transfers net out (a -$95k Coinbase
// withdrawal cancels the +$95k Apple Savings deposit when filter is
// null).  For lifetime view, ``windowStartIso = ''`` and the anchor
// is $0 — which produces the same result as Python's
// ``_compute_benchmark_series`` (the historic benchmark_spy field).
function _simulateFilteredBenchmark(filterFn, hist, priceField, windowStartIso, anchorValue) {
  if (!hist.length) return null;
  const sortedTxns = [...txns].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  const histDates = hist.map(h => h.date);
  const histPrices = hist.map(h => (typeof h[priceField] === 'number' ? h[priceField] : null));
  function priceAt(iso) {
    let p = null;
    for (let i = 0; i < histDates.length; i++) {
      if (histDates[i] > iso) break;
      if (histPrices[i] != null) p = histPrices[i];
    }
    return p;
  }

  // Compute starting position: anchorValue worth of shares at the
  // benchmark price as of windowStartIso.  For lifetime view (no
  // anchor), shares start at 0 and grow with cash flows — matches
  // Python's _compute_benchmark_series behavior.
  let shares = 0;
  if (windowStartIso && anchorValue > 0) {
    const startPx = priceAt(windowStartIso);
    if (startPx && startPx > 0) shares = anchorValue / startPx;
  }

  let txnIdx = 0;
  // Skip txns that happened BEFORE the window start.  Their effect
  // is already baked into ``anchorValue`` (the user's filtered
  // portfolio value at window start).
  if (windowStartIso) {
    while (txnIdx < sortedTxns.length && (sortedTxns[txnIdx].date || '') <= windowStartIso) {
      txnIdx++;
    }
  }

  const out = [];
  for (const h of hist) {
    while (txnIdx < sortedTxns.length && (sortedTxns[txnIdx].date || '') <= h.date) {
      const t = sortedTxns[txnIdx++];
      if (!filterFn(t)) continue;
      const cf = typeof t.cash_flow === 'number' ? t.cash_flow : 0;
      if (cf === 0) continue;
      const px = priceAt(t.date);
      if (!px || px <= 0) continue;
      if (cf > 0) {
        shares += cf / px;
      } else {
        shares -= Math.abs(cf) / px;
        if (shares < 0) shares = 0;
      }
    }
    const sp = histPrices[hist.indexOf(h)];
    if (sp != null && sp > 0) {
      out.push({ date: h.date, value: +(shares * sp).toFixed(2) });
    } else if (out.length) {
      out.push({ date: h.date, value: out[out.length - 1].value });
    } else {
      out.push({ date: h.date, value: 0 });
    }
  }
  return out;
}

// Filter the history array to the currently-selected range.  Keys
// match PERF_WINDOWS: 'lifetime' / '5y' / '3y' / '2y' / '1y' / '6mo' /
// '3mo' / '30day' / 'ytd' / 'custom'.  Cutoff resolution shared with
// the Performance tab via _windowCutoffIso, so "1y" means the same
// thing in both places.
function filteredHistory() {
  if (!history.length || historyRange === 'lifetime') return history;
  if (historyRange === 'custom') {
    const s = historyCustomStart || history[0].date;
    const e = historyCustomEnd || history[history.length - 1].date;
    return history.filter(h => h.date >= s && h.date <= e);
  }
  const ref = history[history.length - 1].date;
  const cutoffIso = _windowCutoffIso(historyRange, ref);
  if (!cutoffIso) return history;
  return history.filter(h => h.date >= cutoffIso);
}

// Render the per-item toggle pill bar in #histControls.
function renderHistControls() {
  const el = document.getElementById('histControls');
  if (!el) return;

  // Same window keys as the Performance tab — see PERF_TWR_PRESETS.
  // History also supports 'custom' with explicit date inputs.
  const rangePills = PERF_TWR_PRESETS.map(r => {
    const cls = 'tbtn' + (historyRange === r ? ' active' : '');
    return `<button class="${cls}" onclick="setHistoryRange('${r}')">${PERF_TWR_PRESET_LABEL[r]}</button>`;
  }).join('');

  // Date inputs for the custom range — shown only when 'Custom' is active.
  // Bounds are clamped to the history range so users can't pick dates
  // outside the data.
  const minDate = history.length ? history[0].date : '';
  const maxDate = history.length ? history[history.length - 1].date : '';
  const customRangeHtml = historyRange === 'custom' ? `
    <span class="hist-label" style="margin-left:12px;">From:</span>
    <input type="date" class="hist-date" min="${minDate}" max="${maxDate}"
           value="${historyCustomStart || minDate}"
           onchange="setHistoryCustomStart(this.value)">
    <span class="hist-label">To:</span>
    <input type="date" class="hist-date" min="${minDate}" max="${maxDate}"
           value="${historyCustomEnd || maxDate}"
           onchange="setHistoryCustomEnd(this.value)">
  ` : '';

  const totalPill = (() => {
    const cls = 'tbtn' + (historySelection.has('total') ? ' active' : '');
    const color = seriesColorFor('total');
    return `<button class="${cls}" onclick="toggleHistorySeries('total')">` +
      `<span class="pill-swatch" style="background:${color}"></span>Total</button>`;
  })();

  const categoryPills = (field, options) => {
    const pills = options.map(k => {
      const sk = field + ':' + k;
      const cls = 'tbtn' + (historySelection.has(sk) ? ' active' : '');
      const color = seriesColorFor(sk);
      const esc = k.replace(/'/g, "\\'");
      return `<button class="${cls}" onclick="toggleHistorySeries('${field}:${esc}')">` +
        `<span class="pill-swatch" style="background:${color}"></span>${k}</button>`;
    }).join('');
    // "all / none" bulk toggles at the end
    const allBtn = `<button class="tbtn" style="font-size:0.72rem;opacity:0.7;" onclick="setHistoryGroupAll('${field}', true)">all</button>`;
    const noneBtn = `<button class="tbtn" style="font-size:0.72rem;opacity:0.7;" onclick="setHistoryGroupAll('${field}', false)">none</button>`;
    return pills + allBtn + noneBtn;
  };

  const overlayBasisCls = 'tbtn' + (historyOverlays.has('basis') ? ' active' : '');
  const overlayGainCls = 'tbtn' + (historyOverlays.has('gain') ? ' active' : '');
  const overlaySpyCls = 'tbtn' + (historyOverlays.has('spy') ? ' active' : '');
  const overlayBndCls = 'tbtn' + (historyOverlays.has('bnd') ? ' active' : '');
  const overlayVxusCls = 'tbtn' + (historyOverlays.has('vxus') ? ' active' : '');
  const overlayContribCls = 'tbtn' + (historyOverlays.has('netcontrib') ? ' active' : '');
  const overlayYoyCls = 'tbtn' + (historyOverlays.has('yoy') ? ' active' : '');

  el.innerHTML = `
    <div class="hist-row">
      <span class="hist-label">Range:</span>
      ${rangePills}
      ${customRangeHtml}
      <span class="hist-label" style="margin-left:16px;">Overlay:</span>
      <button class="${overlayBasisCls}" onclick="toggleHistoryOverlay('basis')">Cost Basis</button>
      <button class="${overlayGainCls}"  onclick="toggleHistoryOverlay('gain')">Unrealized Gain</button>
      <button class="${overlaySpyCls}"  onclick="toggleHistoryOverlay('spy')"  title="If every dollar you contributed had gone to SPY and stayed there (buy-and-hold), where would those dollars be now?  Filter-aware: respects the active account/type filter.  Withdrawals do not sell simulated shares — same semantics as Schwab/Fidelity 'vs index' charts.">SPY</button>
      <button class="${overlayBndCls}"  onclick="toggleHistoryOverlay('bnd')"  title="Same buy-and-hold simulation for BND (US aggregate bonds).  Filter-aware.">BND</button>
      <button class="${overlayVxusCls}" onclick="toggleHistoryOverlay('vxus')" title="Same buy-and-hold simulation for VXUS (international ex-US equities).  Filter-aware.">VXUS</button>
      <button class="${overlayContribCls}" onclick="toggleHistoryOverlay('netcontrib')">Net Contributed</button>
      <button class="${overlayYoyCls}" onclick="toggleHistoryOverlay('yoy')" title="Overlay portfolio value from one year ago at the same calendar position">Year-over-Year</button>
    </div>
    <div class="hist-row">
      <span class="hist-label">Total:</span>
      ${totalPill}
    </div>
    <div class="hist-row">
      <span class="hist-label">Accounts:</span>
      ${categoryPills('account', ACCOUNT_OPTIONS)}
    </div>
    <div class="hist-row">
      <span class="hist-label">Types:</span>
      ${categoryPills('type', TYPE_OPTIONS)}
    </div>
    <div class="hist-row">
      <span class="hist-label">Sectors:</span>
      ${categoryPills('sector', SECTOR_OPTIONS)}
    </div>
  `;

  // Update collapsed summary so the user can see what's selected without
  // expanding (e.g. "1Y · cost basis, SPY · 3 accounts").
  const status = document.getElementById('histControlsSummaryStatus');
  if (status) {
    const bits = [];
    bits.push(PERF_TWR_PRESET_LABEL[historyRange] || historyRange);
    const overlayLabels = [];
    if (historyOverlays.has('basis')) overlayLabels.push('basis');
    if (historyOverlays.has('gain')) overlayLabels.push('unrealized');
    if (historyOverlays.has('spy')) overlayLabels.push('SPY');
    if (historyOverlays.has('bnd')) overlayLabels.push('BND');
    if (historyOverlays.has('vxus')) overlayLabels.push('VXUS');
    if (historyOverlays.has('netcontrib')) overlayLabels.push('net contrib');
    if (historyOverlays.has('yoy')) overlayLabels.push('YoY');
    if (overlayLabels.length) bits.push(overlayLabels.join(', '));
    const seriesCount = historySelection.size;
    if (seriesCount > 0) bits.push(`${seriesCount} series`);
    status.textContent = bits.join(' · ');
  }
}

// Latest value pill in the section header (shown regardless of open/closed)
(function setLatestValue() {
  if (!history.length) return;
  const last = history[history.length - 1];
  const v = last.total || 0;
  document.getElementById('historyLatestValue').textContent =
    '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
})();

// Build series data for the currently-selected set of series, range, and overlays.
// Returns [{key, colorResolved, points: [{date, value}, ...], dashed?}]
function buildHistorySeries() {
  const hist = filteredHistory();
  if (!hist.length) return [];
  const series = [];

  // Resolve {value, basis} accessors for each selected series key.
  // `basisFn` may return null for series where we don't track basis
  // (currently: sectors).  Value accessors add rollover-bridge
  // adjustment so in-flight cash during custodian rollovers doesn't
  // show up as a phantom dip on the chart.
  function accessors(key) {
    if (key === 'total') {
      return {
        valueFn: h => (h.total || 0) + _rolloverBridgeAdjustment(h.date, null),
        basisFn: h => (h.total_cost_basis || 0),
      };
    }
    const [kind, name] = key.split(':');
    if (kind === 'account') {
      return {
        valueFn: h => ((h.by_account_group && h.by_account_group[name]) || 0)
          + _rolloverBridgeAdjustment(h.date, new Set([name])),
        basisFn: h => (h.cost_basis_by_group && h.cost_basis_by_group[name]) || 0,
      };
    }
    if (kind === 'type') {
      // Rollover bridges are keyed on account_group; account_type
      // filter adjustment requires mapping types → groups.
      const groupsInType = new Set(
        holdingsByAccount
          .filter(h => h.account_type === name)
          .map(h => h.account_group)
      );
      return {
        valueFn: h => ((h.by_account_type && h.by_account_type[name]) || 0)
          + _rolloverBridgeAdjustment(h.date, groupsInType),
        basisFn: h => (h.cost_basis_by_type && h.cost_basis_by_type[name]) || 0,
      };
    }
    if (kind === 'sector') {
      return {
        valueFn: h => (h.by_sector && h.by_sector[name]) || 0,
        basisFn: null,   // no per-sector basis data
      };
    }
    return { valueFn: h => 0, basisFn: null };
  }

  // Stable display order: 'total' first, then account, type, sector.
  const kindOrder = { total: 0, account: 1, type: 2, sector: 3 };
  const selected = [...historySelection].sort((a, b) => {
    const ka = a === 'total' ? 'total' : a.split(':')[0];
    const kb = b === 'total' ? 'total' : b.split(':')[0];
    const ord = (kindOrder[ka] || 9) - (kindOrder[kb] || 9);
    return ord !== 0 ? ord : a.localeCompare(b);
  });

  for (const key of selected) {
    const { valueFn, basisFn } = accessors(key);
    const color = seriesColorFor(key);
    const label = seriesLabel(key);
    series.push({
      key: label,
      colorResolved: color,
      points: hist.map(h => ({ date: h.date, value: valueFn(h) })),
    });
    if (historyOverlays.has('basis') && basisFn) {
      series.push({
        key: label + ' Basis',
        colorResolved: color,
        dashed: true,
        points: hist.map(h => ({ date: h.date, value: basisFn(h) })),
      });
    }
    if (historyOverlays.has('gain') && basisFn) {
      series.push({
        key: label + ' Unrealized',
        colorResolved: color,
        dashed: true,
        dottedOverlay: true,
        points: hist.map(h => ({ date: h.date, value: valueFn(h) - basisFn(h) })),
      });
    }
  }

  // Benchmark overlays — SPY / BND / VXUS, all filter-aware.  When
  // the user has filtered to a subset of account_groups or
  // account_types, the overlay simulates "what if THE FILTERED
  // CONTRIBUTIONS had been invested in this benchmark" instead of
  // showing the lifetime full-portfolio simulation.  Sector filters
  // fall back to lifetime since per-symbol-to-benchmark attribution
  // is ambiguous for sector-mixed positions.
  const benchFilterFn = _activeBenchFilterFn();
  const _benchSpec = [
    { key: 'spy', ticker: 'SPY', color: '#60a5fa', priceField: 'benchmark_spy_price', dollarField: 'benchmark_spy' },
    { key: 'bnd', ticker: 'BND', color: '#f472b6', priceField: 'benchmark_bnd_price', dollarField: 'benchmark_bnd' },
    { key: 'vxus', ticker: 'VXUS', color: '#fb923c', priceField: 'benchmark_vxus_price', dollarField: 'benchmark_vxus' },
  ];
  for (const spec of _benchSpec) {
    if (!historyOverlays.has(spec.key)) continue;
    let points;
    let labelSuffix = '';
    if (benchFilterFn && hist.length && hist[0][spec.priceField] != null) {
      // Filter-aware: re-simulate contributions into this benchmark.
      const simulated = _simulateFilteredBenchmark(benchFilterFn, hist, spec.priceField);
      if (simulated) {
        points = simulated;
        labelSuffix = ' (filtered)';
      }
    }
    if (!points) {
      // Either no filter, no per-snapshot price, or sector filter active —
      // fall back to the lifetime simulation we already have on disk.
      points = hist.map(h => ({ date: h.date, value: h[spec.dollarField] || 0 }));
    }
    series.push({
      key: `${spec.ticker} Benchmark${labelSuffix}`,
      colorResolved: spec.color,
      dashed: true,
      points,
    });
  }

  // Net Contributed overlay — cumulative external money you've put in
  // (gross deposits − gross withdrawals).  Apples-to-apples vs SPY.
  if (historyOverlays.has('netcontrib')) {
    series.push({
      key: 'Net Contributed',
      colorResolved: '#9ca3af',   // neutral gray
      dashed: true,
      points: hist.map(h => ({ date: h.date, value: h.net_contributed || 0 })),
    });
  }

  // Year-over-Year overlay — for each visible snapshot, look up the
  // total value as of one year earlier (nearest snapshot ≤ that date).
  // Lets you see at a glance how the current curve tracks last year's
  // path at the same calendar position.
  if (historyOverlays.has('yoy')) {
    // Search the FULL history (not just `hist`, which is range-filtered)
    // so points near the start of a sliced view still find a year-ago
    // counterpart.
    const lookupYearAgo = (iso) => {
      const target = new Date(iso);
      target.setFullYear(target.getFullYear() - 1);
      const targetIso = target.toISOString().slice(0, 10);
      // Binary-ish search: history is sorted, find the largest snapshot ≤ targetIso
      let best = null;
      for (const h of history) {
        if (h.date && h.date <= targetIso) best = h;
        else break;
      }
      return best ? (best.total || 0) : null;
    };
    const yoyPts = hist.map(h => ({ date: h.date, value: lookupYearAgo(h.date) }))
      .filter(p => p.value != null);
    if (yoyPts.length > 1) {
      series.push({
        key: 'YoY (1 year ago)',
        colorResolved: '#fb923c',   // orange — distinct from greens/blues
        dashed: true,
        points: yoyPts,
      });
    }
  }

  return series;
}

function renderHistory() {
  const svg = document.getElementById('chartSvg');
  const tooltip = document.getElementById('chartTooltip');
  const legend = document.getElementById('chartLegend');

  if (!history.length) {
    svg.innerHTML = '';
    legend.innerHTML = '';
    const wrap = document.getElementById('chartWrap');
    if (!wrap.querySelector('.chart-empty')) {
      const e = document.createElement('div');
      e.className = 'chart-empty';
      e.textContent = 'No history data. Run the pipeline to build the price cache.';
      wrap.appendChild(e);
    }
    return;
  }

  const allSeries = buildHistorySeries();
  const series = allSeries.filter(s => !seriesHidden.has(s.key));
  const hist = filteredHistory();  // axis dates come from filtered slice

  // Dimensions
  const rect = svg.getBoundingClientRect();
  const W = rect.width || 800;
  const H = rect.height || 340;
  const PAD = { l: 64, r: 16, t: 12, b: 28 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;

  // X: dates → index-based (evenly spaced over the filtered range)
  const n = hist.length;
  const xOf = i => PAD.l + (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));

  // Y: float bounds to the data range so short windows (1Y / 3Y) don't
  // squish all lines into the top of an anchored-at-zero scale.  When
  // overlays cross zero (Unrealized Gain underwater), keep zero in view
  // since the existing zero-baseline line below is meaningful.
  let dataMin = Infinity, dataMax = -Infinity;
  series.forEach(s => s.points.forEach(p => {
    if (typeof p.value !== 'number' || isNaN(p.value)) return;
    if (p.value > dataMax) dataMax = p.value;
    if (p.value < dataMin) dataMin = p.value;
  }));
  if (!isFinite(dataMin) || !isFinite(dataMax)) { dataMin = 0; dataMax = 1; }
  const range0 = dataMax - dataMin;
  const padPx = range0 > 0 ? range0 * 0.08 : Math.max(1, Math.abs(dataMax) * 0.08);
  let minY = dataMin - padPx;
  let maxY = dataMax + padPx;
  // If data crosses zero (Unrealized Gain overlay can go negative),
  // ensure 0 stays inside the plotted range so the zero baseline line
  // drawn below is in view.
  if (dataMin < 0 && dataMax > 0) {
    if (minY > 0) minY = 0;
    if (maxY < 0) maxY = 0;
  }
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const yOf = v => PAD.t + plotH - ((v - minY) / (maxY - minY)) * plotH;

  // Axis labels
  const yTicks = 5;
  let parts = [];
  for (let i = 0; i <= yTicks; i++) {
    const v = minY + ((maxY - minY) * i) / yTicks;
    const y = yOf(v);
    parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
    parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${formatAxisMoney(v)}</text>`);
  }
  // Emphasize the zero line when min goes negative (unrealized gain overlay)
  if (minY < 0) {
    const y0 = yOf(0);
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${y0}" x2="${W - PAD.r}" y2="${y0}" stroke-opacity="0.6"/>`);
  }
  // X axis labels (5 evenly spaced)
  const xTicks = Math.min(6, n);
  for (let i = 0; i < xTicks; i++) {
    const idx = Math.round((i * (n - 1)) / (xTicks - 1 || 1));
    const x = xOf(idx);
    parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="middle">${hist[idx].date.slice(0, 7)}</text>`);
  }
  // Axis lines
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);

  // Series paths
  series.forEach(s => {
    const d = s.points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(p.value)}`).join(' ');
    // Value series: solid.
    // Basis overlay: dashed.  Unrealized-gain overlay: dotted.
    let dashAttr = '';
    let opacityAttr = '';
    if (s.dottedOverlay) {
      dashAttr = ' stroke-dasharray="1 4"';
      opacityAttr = ' stroke-opacity="0.75"';
    } else if (s.dashed) {
      dashAttr = ' stroke-dasharray="4 4"';
      opacityAttr = ' stroke-opacity="0.85"';
    }
    parts.push(`<path class="series-line" d="${d}" stroke="${s.colorResolved}"${dashAttr}${opacityAttr}/>`);
  });

  // Hover crosshair (hidden by default) + transparent capture rect
  parts.push(`<line id="chartHoverV" class="hover-v" x1="0" y1="${PAD.t}" x2="0" y2="${PAD.t + plotH}" style="display:none"/>`);
  parts.push(`<g id="chartHoverDots"></g>`);
  parts.push(`<rect id="chartCapture" x="${PAD.l}" y="${PAD.t}" width="${plotW}" height="${plotH}" fill="transparent"/>`);

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = parts.join('');

  // Legend
  legend.innerHTML = allSeries.map(s => {
    const muted = seriesHidden.has(s.key) ? ' muted' : '';
    return `<span class="legend-item${muted}" data-series="${s.key.replace(/"/g, '&quot;')}">
      <span class="legend-swatch" style="background:${s.colorResolved}"></span>${s.key}
    </span>`;
  }).join('');
  legend.onclick = e => {
    const item = e.target.closest('.legend-item');
    if (!item) return;
    const key = item.dataset.series;
    if (seriesHidden.has(key)) seriesHidden.delete(key);
    else seriesHidden.add(key);
    renderHistory();
  };

  // Hover interaction
  const capture = document.getElementById('chartCapture');
  const hoverV = document.getElementById('chartHoverV');
  const dotsG = document.getElementById('chartHoverDots');
  capture.addEventListener('mousemove', ev => {
    const svgRect = svg.getBoundingClientRect();
    // SVG viewBox is 0..W; scale mouse X into that space
    const mx = (ev.clientX - svgRect.left) * (W / svgRect.width);
    let idx = Math.round(((mx - PAD.l) / plotW) * (n - 1));
    idx = Math.max(0, Math.min(n - 1, idx));
    const x = xOf(idx);
    hoverV.setAttribute('x1', x);
    hoverV.setAttribute('x2', x);
    hoverV.style.display = '';
    dotsG.innerHTML = series.map(s => {
      const y = yOf(s.points[idx].value);
      return `<circle class="hover-dot" cx="${x}" cy="${y}" r="4" fill="${s.colorResolved}"/>`;
    }).join('');

    // Tooltip
    const rows = series.map(s => {
      const v = s.points[idx].value;
      return `<div class="tt-row">
        <span class="tt-name"><span class="tt-swatch" style="background:${s.colorResolved}"></span>${s.key}</span>
        <span>${formatMoney(v)}</span>
      </div>`;
    }).join('');
    tooltip.innerHTML = `<div class="tt-date">${hist[idx].date}</div>${rows}`;
    tooltip.style.display = 'block';

    const wrapRect = document.getElementById('chartWrap').getBoundingClientRect();
    // Position tooltip near mouse, clamp inside the chart wrap
    let tx = ev.clientX - wrapRect.left + 12;
    let ty = ev.clientY - wrapRect.top + 12;
    const tRect = tooltip.getBoundingClientRect();
    if (tx + tRect.width + 12 > wrapRect.width) tx = ev.clientX - wrapRect.left - tRect.width - 12;
    if (ty + tRect.height + 12 > wrapRect.height) ty = ev.clientY - wrapRect.top - tRect.height - 12;
    tooltip.style.left = tx + 'px';
    tooltip.style.top = ty + 'px';
  });
  capture.addEventListener('mouseleave', () => {
    hoverV.style.display = 'none';
    dotsG.innerHTML = '';
    tooltip.style.display = 'none';
  });
}

function formatAxisMoney(v) {
  if (Math.abs(v) >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
  if (Math.abs(v) >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'k';
  return '$' + Math.round(v);
}
function formatMoney(v) {
  return '$' + (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Re-render on resize (SVG dimensions change)
let _histResizeRaf = null;
window.addEventListener('resize', () => {
  if (_histResizeRaf) cancelAnimationFrame(_histResizeRaf);
  _histResizeRaf = requestAnimationFrame(renderHistory);
});

renderHistControls();
renderHistory();

// =========================================================================
// Overview tab: Top Holdings + Recent Transactions mini-tables
// =========================================================================

// Year-by-year breakdown table.  Columns:
//   Year, Age, [account: Σ, Δ%, Δ$] × N, Sum: Σ Δ% Δ$, Target
// Each account header is clickable to expand 3 extra contribution
// columns: contributed-this-year, cumulative contributed, year-over-
// year change in cumulative contributed.  State (which accounts are
// expanded) is per-page-load.
const _annualExpanded = new Set();   // account_group keys currently expanded
let _annualSumExpanded = false;       // Sum-vs-Target diff group expanded?

function _toggleAnnualExpand(acct) {
  if (_annualExpanded.has(acct)) _annualExpanded.delete(acct);
  else _annualExpanded.add(acct);
  renderAnnualBreakdown();
}

function _toggleAnnualSumExpand() {
  _annualSumExpanded = !_annualSumExpanded;
  renderAnnualBreakdown();
}

// Fidelity's age-based retirement savings benchmark — multiples of
// current salary that the average person should have saved by each
// age to stay on track for retirement at 67.  Interpolated linearly
// between checkpoints; flat outside the range.  Used as a fallback
// suggestion for the Year-by-Year Target column when no manual
// Target row exists for that year in metadata.csv.
const _FIDELITY_AGE_MULTIPLES = [
  [30, 1], [35, 2], [40, 3], [45, 4],
  [50, 6], [55, 7], [60, 8], [67, 10],
];
function _ageSalaryMultiple(age) {
  if (age == null || age === '') return null;
  const tbl = _FIDELITY_AGE_MULTIPLES;
  if (age <= tbl[0][0]) return tbl[0][1] * (age / tbl[0][0]);
  if (age >= tbl[tbl.length - 1][0]) return tbl[tbl.length - 1][1];
  for (let i = 0; i < tbl.length - 1; i++) {
    const [a1, m1] = tbl[i], [a2, m2] = tbl[i + 1];
    if (age >= a1 && age <= a2) return m1 + (m2 - m1) * (age - a1) / (a2 - a1);
  }
  return null;
}

// Convert "#rrggbb" → "rgba(r,g,b,a)" for low-alpha column tinting.
function _hexToRgba(hex, alpha) {
  if (!hex) return `rgba(167,139,250,${alpha})`;
  const h = hex.replace('#', '');
  const r = parseInt(h.substr(0, 2), 16);
  const g = parseInt(h.substr(2, 2), 16);
  const b = parseInt(h.substr(4, 2), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function renderAnnualBreakdown() {
  const host = document.getElementById('annualBreakdown');
  if (!host) return;
  if (!history.length) { host.innerHTML = ''; return; }

  // The accounts to show — fixed order matches the user's spreadsheet.
  // We derive the column list from groups that actually appear in the
  // user's data so the table doesn't render empty columns for accounts
  // they don't have.
  const PREFERRED_ORDER = ['Roth IRA', '401K', 'Rollover IRA', 'Robinhood', 'Coinbase', 'Apple Savings'];
  const SHORT_LABEL = { 'Apple Savings': 'Savings' };
  const seenGroups = new Set();
  for (const h of history) {
    for (const g of Object.keys(h.by_account_group || {})) seenGroups.add(g);
  }
  const accounts = PREFERRED_ORDER.filter(a => seenGroups.has(a));
  // Catch any account_groups not in the preferred order (e.g. user
  // adds a new broker) — append them so they're visible.
  for (const g of seenGroups) if (!accounts.includes(g)) accounts.push(g);

  // Year-end snapshots: pick the last snapshot of each calendar year.
  // For the current year, that's "today" (latest snapshot), which is
  // a valid year-end-so-far.
  const yearEndSnaps = {};
  for (const h of history) {
    const y = (h.date || '').slice(0, 4);
    if (y) yearEndSnaps[y] = h;
  }
  const years = Object.keys(yearEndSnaps).sort();

  // Per-year-per-account contribution events (net cash_flow).  Walk
  // txns once.  For accounts the user filters out we still walk
  // them — the cost is negligible vs the cleanliness of data flow.
  const contribByYearAcct = {};
  for (const t of txns) {
    const y = (t.date || '').slice(0, 4);
    const acct = t.account_group;
    const cf = typeof t.cash_flow === 'number' ? t.cash_flow : 0;
    if (!y || !acct || cf === 0) continue;
    if (!contribByYearAcct[y]) contribByYearAcct[y] = {};
    contribByYearAcct[y][acct] = (contribByYearAcct[y][acct] || 0) + cf;
  }
  // Cumulative contributions per account by year-end.
  const cumContribByAcct = {};
  for (const a of accounts) cumContribByAcct[a] = {};
  for (const a of accounts) {
    let running = 0;
    for (const y of years) {
      running += (contribByYearAcct[y] || {})[a] || 0;
      cumContribByAcct[a][y] = running;
    }
  }

  // Target lookup from metadata.csv (Type=Target rows).
  const targetsByYear = {};
  for (const t of (RETIREMENT_META.targets || [])) {
    targetsByYear[t.year] = t.amount;
  }

  // Birthday → age computation.  Year is the December-31-of-year.
  const birthdayDate = RETIREMENT_META.birthday ? new Date(RETIREMENT_META.birthday) : null;
  const ageAtYearEnd = (year) => {
    if (!birthdayDate) return '';
    const yearEnd = new Date(`${year}-12-31`);
    const age = yearEnd.getFullYear() - birthdayDate.getFullYear()
      - ((yearEnd.getMonth() < birthdayDate.getMonth() ||
        (yearEnd.getMonth() === birthdayDate.getMonth()
          && yearEnd.getDate() < birthdayDate.getDate())) ? 1 : 0);
    return age >= 0 ? age : '';
  };

  // Salary at year-end — find the latest Salary History entry whose
  // effective date is on or before Dec 31 of the given year.  Used for
  // the Fidelity age × salary suggested target.
  const _salariesSorted = (RETIREMENT_META.salary_history || [])
    .slice()
    .sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  const salaryAtYearEnd = (year) => {
    const cutoff = `${year}-12-31`;
    let latest = null;
    for (const s of _salariesSorted) {
      if ((s.date || '') <= cutoff) latest = s.amount;
      else break;
    }
    return latest;
  };

  // Per-account inline tints (background + accent color for header
  // text).  Subtle alphas — these read as a column-grouping cue, not
  // a paint job.
  const _acctTint = (a, alpha) => _hexToRgba(ACCOUNT_COLORS[a] || '#a78bfa', alpha);
  const TINT_HEAD = 0.10, TINT_CELL = 0.05, TINT_CONTRIB = 0.07;

  // Cell formatters
  const fmtCell = (v) => fmtMoneyShort(v);
  const fmtPctCell = (v) => v == null ? '—' : ((v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%');
  const fmtSignedCell = (v) => v == null ? '—' : (v >= 0 ? '+' : '−') + fmtMoneyShort(Math.abs(v)).replace('$', '$');
  const cls = (v) => v == null ? '' : (v > 0 ? 'positive' : v < 0 ? 'negative' : '');

  // ----- HEADER -------------------------------------------------------
  // Row 1: Year, Age, [account name (colspan = 3 collapsed / 6 expanded)]…, Sum (colspan 3), Target
  // Row 2: per-account sub-columns
  let h1 = '<tr><th rowspan="2" class="ab-sticky-col">Year</th><th rowspan="2">Age</th>';
  let h2 = '<tr>';
  for (const a of accounts) {
    const expanded = _annualExpanded.has(a);
    const colspan = expanded ? 6 : 3;
    const arrow = expanded ? '▾' : '▸';
    const label = SHORT_LABEL[a] || a;
    const acctColor = ACCOUNT_COLORS[a] || 'var(--accent)';
    const headTint = _acctTint(a, TINT_HEAD);
    const cellTint = _acctTint(a, TINT_CELL);
    const contribTint = _acctTint(a, TINT_CONTRIB);
    h1 += `<th colspan="${colspan}" class="ab-acct-head${expanded ? ' ab-expanded' : ''}" data-acct="${_htmlEsc(a)}" style="background:${headTint};color:${acctColor};border-bottom:2px solid ${acctColor};" title="Click to ${expanded ? 'collapse' : 'expand'} contribution columns">
      <span class="ab-acct-arrow">${arrow}</span>${_htmlEsc(label)}
    </th>`;
    if (expanded) {
      h2 += `<th class="num ab-sub" style="background:${contribTint};" title="Net cash flow into this account this year">contr.</th>
             <th class="num ab-sub" style="background:${contribTint};" title="Cumulative net cash flow into this account through year end">Σ contr.</th>
             <th class="num ab-sub" style="background:${contribTint};" title="Year-over-year change in cumulative contributions">Δ contr.</th>`;
    }
    h2 += `<th class="num ab-sub" style="background:${cellTint};" title="Year-end balance">Σ</th>
           <th class="num ab-sub" style="background:${cellTint};" title="Year-over-year % change of year-end balance">Δ%</th>
           <th class="num ab-sub" style="background:${cellTint};" title="Year-over-year $ change of year-end balance">Δ$</th>`;
  }
  // Sum group (always 3 columns)
  const sumExpanded = _annualSumExpanded;
  const sumArrow = sumExpanded ? '▾' : '▸';
  const sumTip = sumExpanded
    ? 'Click to hide Target comparison columns'
    : 'Click to show Target columns (year-end Sum vs target)';
  h1 += `<th colspan="3" class="ab-sum-head ab-sum-toggle${sumExpanded ? ' ab-expanded' : ''}" title="${sumTip}">
    <span class="ab-acct-arrow">${sumArrow}</span>Sum
  </th>`;
  h2 += `<th class="num ab-sub">Σ</th>
         <th class="num ab-sub">Δ%</th>
         <th class="num ab-sub">Δ$</th>`;
  if (sumExpanded) {
    h1 += `<th colspan="3" class="ab-target-head">Target</th>`;
    h2 += `<th class="num ab-sub" title="Year-end target">Σ</th>
           <th class="num ab-sub" title="% above (+) or below (−) target">Δ%</th>
           <th class="num ab-sub" title="$ above (+) or below (−) target">Δ$</th>`;
  }
  h1 += `</tr>`;
  h2 += `</tr>`;

  // ----- ROWS ---------------------------------------------------------
  const bodyRows = years.map((year, i) => {
    const snap = yearEndSnaps[year];
    const prevYear = i > 0 ? years[i - 1] : null;
    const prevSnap = prevYear ? yearEndSnaps[prevYear] : null;
    const cells = [
      `<th class="ab-sticky-col">${year}</th>`,
      `<td class="num">${ageAtYearEnd(year)}</td>`,
    ];
    let sumNow = 0, sumPrev = 0;
    for (const a of accounts) {
      const v = (snap.by_account_group || {})[a] || 0;
      const vPrev = prevSnap ? ((prevSnap.by_account_group || {})[a] || 0) : 0;
      const dDollar = v - vPrev;
      const dPct = vPrev > 0 ? dDollar / vPrev : (v > 0 && !prevSnap ? null : null);
      sumNow += v;
      sumPrev += vPrev;
      const cellBg = _acctTint(a, TINT_CELL);
      const contribBg = _acctTint(a, TINT_CONTRIB);
      const expanded = _annualExpanded.has(a);
      if (expanded) {
        const yc = (contribByYearAcct[year] || {})[a] || 0;
        const cumNow = cumContribByAcct[a][year] || 0;
        const cumPrev = prevYear ? (cumContribByAcct[a][prevYear] || 0) : 0;
        const dCum = cumNow - cumPrev;
        cells.push(
          `<td class="num ab-contrib ${cls(yc)}" style="background:${contribBg};">${yc !== 0 ? fmtSignedCell(yc) : '—'}</td>`,
          `<td class="num ab-contrib" style="background:${contribBg};">${cumNow !== 0 ? fmtCell(cumNow) : '—'}</td>`,
          `<td class="num ab-contrib ${cls(dCum)}" style="background:${contribBg};">${dCum !== 0 ? fmtSignedCell(dCum) : '—'}</td>`,
        );
      }
      cells.push(
        `<td class="num" style="background:${cellBg};">${v !== 0 ? fmtCell(v) : '—'}</td>`,
        `<td class="num ${cls(dPct)}" style="background:${cellBg};">${prevSnap ? fmtPctCell(dPct) : '—'}</td>`,
        `<td class="num ${cls(dDollar)}" style="background:${cellBg};">${prevSnap && dDollar !== 0 ? fmtSignedCell(dDollar) : '—'}</td>`,
      );
    }
    const dSumDollar = sumNow - sumPrev;
    const dSumPct = sumPrev > 0 ? dSumDollar / sumPrev : null;
    cells.push(
      `<td class="num ab-sum-cell"><b>${fmtCell(sumNow)}</b></td>`,
      `<td class="num ab-sum-cell ${cls(dSumPct)}">${prevSnap ? fmtPctCell(dSumPct) : '—'}</td>`,
      `<td class="num ab-sum-cell ${cls(dSumDollar)}">${prevSnap && dSumDollar !== 0 ? fmtSignedCell(dSumDollar) : '—'}</td>`,
    );

    // Target group: 3 cells — target value | %-diff vs sum | $-diff vs sum.
    // Hidden by default; revealed when the Sum header is clicked.
    // User-entered Target rows in metadata.csv win.  Otherwise
    // compute a Fidelity-style suggestion = current salary × age-based
    // multiple — rendered dimmed/italic with a tooltip so it's
    // obviously a suggestion, not a hard goal.  Δ% / Δ$ are signed:
    // positive = above target, negative = below.
    if (_annualSumExpanded) {
      const userTarget = targetsByYear[year];
      let targetVal = null;
      let targetIsSuggested = false;
      let targetTip = '';
      if (userTarget != null) {
        targetVal = userTarget;
      } else {
        const age = ageAtYearEnd(year);
        const salary = salaryAtYearEnd(year);
        const mult = _ageSalaryMultiple(age);
        if (salary != null && mult != null) {
          targetVal = salary * mult;
          targetIsSuggested = true;
          targetTip = `Suggested (Fidelity benchmark): ${mult.toFixed(1)}× salary at age ${age} — ${fmtMoney(salary)} × ${mult.toFixed(1)} = ${fmtMoney(targetVal)}`;
        }
      }
      if (targetVal != null) {
        const dDol = sumNow - targetVal;
        const dPct = targetVal > 0 ? dDol / targetVal : null;
        const valCls = targetIsSuggested ? 'ab-target-suggest' : 'ab-target-user';
        const dCls = targetIsSuggested ? 'ab-target-diff-suggest' : '';
        const tipAttr = targetTip ? ` title="${_htmlEsc(targetTip)}"` : '';
        cells.push(
          `<td class="num ab-target-cell ${valCls}"${tipAttr}>${fmtCell(targetVal)}</td>`,
          `<td class="num ab-target-diff ${dCls} ${cls(dPct)}">${dPct != null ? fmtPctCell(dPct) : '—'}</td>`,
          `<td class="num ab-target-diff ${dCls} ${cls(dDol)}">${dDol !== 0 ? fmtSignedCell(dDol) : '—'}</td>`,
        );
      } else {
        cells.push(
          `<td class="num ab-target-cell">—</td>`,
          `<td class="num ab-target-diff">—</td>`,
          `<td class="num ab-target-diff">—</td>`,
        );
      }
    }
    return '<tr>' + cells.join('') + '</tr>';
  }).join('');

  host.innerHTML = `<div class="ab-scroll">
    <table class="annual-breakdown">
      <thead>${h1}${h2}</thead>
      <tbody>${bodyRows}</tbody>
    </table>
  </div>`;

  // Wire account-header clicks for expand/collapse.  Delegated so the
  // single re-render hands off cleanly.
  host.querySelectorAll('.ab-acct-head').forEach(el => {
    el.addEventListener('click', () => _toggleAnnualExpand(el.dataset.acct));
  });
  // Sum header click reveals/hides the Target group.
  const sumToggle = host.querySelector('.ab-sum-toggle');
  if (sumToggle) sumToggle.addEventListener('click', _toggleAnnualSumExpand);
}

function renderTopHoldings() {
  const head = document.getElementById('topHoldingsHead');
  const body = document.getElementById('topHoldingsBody');
  const rows = asOfHoldingsByAsset()
    .filter(h => typeof h.value === 'number')
    .sort((a, b) => (b.value || 0) - (a.value || 0))
    .slice(0, 15);
  head.innerHTML = `
    <th>Symbol</th>
    <th>Sector</th>
    <th class="num">Quantity</th>
    <th class="num">Price</th>
    <th class="num">Value</th>
    <th class="num">Gain</th>`;
  body.innerHTML = rows.map(h => {
    const ug = typeof h.unrealized_gain === 'number' ? h.unrealized_gain : null;
    const ugStr = ug == null ? '—'
      : `<span class="${ug >= 0 ? 'positive' : 'negative'}">${fmtSigned(ug)}</span>`;
    const sectorColor = SECTOR_COLORS[h.sector] || '#9ca3af';
    const sectorStr = h.sector ? `<span style="color:${sectorColor}">${h.sector}</span>` : '';
    return `<tr>
      <td><b>${symLabel(h.symbol)}</b></td>
      <td>${sectorStr}</td>
      <td class="num">${(h.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="num">${fmtMoney(h.price)}</td>
      <td class="num">${fmtMoney(h.value)}</td>
      <td class="num">${ugStr}</td>
    </tr>`;
  }).join('');
}

function renderRecentTransactions() {
  const head = document.getElementById('recentTxnsHead');
  const body = document.getElementById('recentTxnsBody');
  // When an as-of-date is set, show the most recent 15 txns at or
  // before that date (so "recent" means "recent as of the selected
  // view date", not "recent lifetime").
  const cutoff = asOfDate;
  const rows = txns
    .filter(t => !t.date || t.date <= cutoff)
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 15);
  head.innerHTML = `
    <th>Date</th>
    <th>Account</th>
    <th>Symbol</th>
    <th>Action</th>
    <th class="num">Qty</th>
    <th class="num">Amount</th>`;
  body.innerHTML = rows.map(t => {
    const actionColor = ACTION_COLORS[t.action] || '';
    const actionSpan = actionColor
      ? `<span style="color:${actionColor}">${t.action || ''}</span>`
      : (t.action || '');
    const acctColor = ACCOUNT_COLORS[t.account_group] || '';
    const acctSpan = acctColor
      ? `<span style="color:${acctColor}">${t.account_group || ''}</span>`
      : (t.account_group || '');
    return `<tr>
      <td>${t.date || ''}</td>
      <td>${acctSpan}</td>
      <td>${symLabel(t.symbol || '')}</td>
      <td>${actionSpan}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="num">${fmtMoney(t.amount)}</td>
    </tr>`;
  }).join('');
}

// =========================================================================
// Overview tab: Allocation donut (By Account / Type / Sector)
// =========================================================================

// Three side-by-side donuts: By Account, By Type, By Sector.  No
// toggle — they all render at once.  Hover any slice for the
// segment's label + dollar value via SVG <title> tooltips, plus a
// compact legend below each donut.
function _renderOneAllocationDonut(svgEl, legendEl, field, palette) {
  if (!svgEl) return;
  let agg = {};
  if (isAsOfLatest()) {
    for (const h of holdingsByAccount) {
      if (typeof h.value !== 'number') continue;
      const k = h[field] || 'Unknown';
      agg[k] = (agg[k] || 0) + h.value;
    }
  } else {
    const snap = getAsOfSnapshot();
    const src = snap && (field === 'account_group' ? snap.by_account_group
      : field === 'account_type' ? snap.by_account_type
        : snap.by_sector);
    if (src) agg = { ...src };
  }
  const entries = Object.entries(agg)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);
  const total = entries.reduce((s, [, v]) => s + v, 0);

  // Compact donut sized for the 3-up grid.  ViewBox is square so
  // CSS can scale it responsively.
  const CX = 100, CY = 100, R_OUT = 80, R_IN = 50;
  const parts = [];
  let theta = -Math.PI / 2;
  for (const [k, v] of entries) {
    const frac = v / total;
    const end = theta + frac * Math.PI * 2;
    const large = (end - theta) > Math.PI ? 1 : 0;
    const x0 = CX + R_OUT * Math.cos(theta);
    const y0 = CY + R_OUT * Math.sin(theta);
    const x1 = CX + R_OUT * Math.cos(end);
    const y1 = CY + R_OUT * Math.sin(end);
    const xi1 = CX + R_IN * Math.cos(end);
    const yi1 = CY + R_IN * Math.sin(end);
    const xi0 = CX + R_IN * Math.cos(theta);
    const yi0 = CY + R_IN * Math.sin(theta);
    const color = palette[k] || '#9ca3af';
    const d = [
      `M${x0.toFixed(2)},${y0.toFixed(2)}`,
      `A${R_OUT},${R_OUT} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`,
      `L${xi1.toFixed(2)},${yi1.toFixed(2)}`,
      `A${R_IN},${R_IN} 0 ${large} 0 ${xi0.toFixed(2)},${yi0.toFixed(2)}`,
      'Z',
    ].join(' ');
    const pct = ((v / total) * 100).toFixed(1);
    const tipText = `${k}: ${fmtMoney(v)} (${pct}%)`;
    // <title> nested inside the path is the SVG-native hover tooltip.
    // Browser delay is ~0.5s.  Also use a CSS hover effect (set in
    // styles.css) for visual feedback on rollover.
    parts.push(
      `<path class="alloc-slice" d="${d}" fill="${color}" stroke="var(--bg)" stroke-width="1.5">` +
      `<title>${_htmlEsc(tipText)}</title></path>`
    );
    theta = end;
  }
  parts.push(`<text x="${CX}" y="${CY - 3}" text-anchor="middle" fill="var(--text-dim)" font-size="10">Total</text>`);
  parts.push(`<text x="${CX}" y="${CY + 13}" text-anchor="middle" fill="var(--text)" font-size="13" font-weight="600">${fmtMoneyShort(total)}</text>`);

  svgEl.setAttribute('viewBox', '0 0 200 200');
  svgEl.innerHTML = parts.join('');

  // Compact legend — top 5 entries inline; "+N more" if the rest spill.
  const TOP_N = 5;
  const top = entries.slice(0, TOP_N);
  const rest = entries.slice(TOP_N);
  const restTotal = rest.reduce((s, [, v]) => s + v, 0);
  const restPct = total > 0 ? ((restTotal / total) * 100).toFixed(1) : '0';
  const rows = top.map(([k, v]) => {
    const color = palette[k] || '#9ca3af';
    const pct = ((v / total) * 100).toFixed(1);
    return `<div class="allocation-legend-row" title="${_htmlEsc(k + ': ' + fmtMoney(v) + ' (' + pct + '%)')}">
      <span class="alloc-label"><span class="alloc-swatch" style="background:${color}"></span><span class="alloc-name">${_htmlEsc(k)}</span></span>
      <span class="alloc-value">${pct}%</span>
    </div>`;
  });
  if (rest.length) {
    rows.push(`<div class="allocation-legend-row alloc-more"
      title="${_htmlEsc(rest.map(([k, v]) => k + ': ' + fmtMoney(v)).join('\n'))}">
      <span class="alloc-label"><span class="alloc-swatch" style="background:#6b7280"></span>+${rest.length} more</span>
      <span class="alloc-value">${restPct}%</span>
    </div>`);
  }
  if (legendEl) legendEl.innerHTML = rows.join('');
}

function renderAllocation() {
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgGroup'),
    document.getElementById('allocationLegendGroup'),
    'account_group', ACCOUNT_COLORS,
  );
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgType'),
    document.getElementById('allocationLegendType'),
    'account_type', TYPE_COLORS,
  );
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgSector'),
    document.getElementById('allocationLegendSector'),
    'sector', SECTOR_COLORS,
  );
}

// Helper for compact money formatting used by the donut center label.
function fmtMoneyShort(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (n >= 1e6) return sign + '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return sign + '$' + (n / 1e3).toFixed(1) + 'k';
  return sign + '$' + n.toFixed(0);
}

// --- Overview feedback: alerts + "what changed since last run" ------------
// Both panels render as collapsible <details> elements (default-closed)
// to match the Data Health panel below — keeps the Overview tab's
// initial scroll-length tight.  The summary line surfaces the count
// + a severity-tinted chip so the user can decide whether to expand.
// Combined Overview status panels.  Renders into a single grid
// container two collapsible cards:
//   1. Status — Attention (actionable signals) + Data Health
//      (pipeline integrity) merged.  Different concerns but both
//      "things to know about your dashboard" — collapsing them into
//      one card cuts visual clutter while preserving distinction
//      via internal section headers.
//   2. What's Changed — period-over-period diff vs last run.
// Both cards default to collapsed.  When either is empty the slot
// stays empty so the grid auto-collapses to one column or none.
function renderOverviewStatus() {
  const host = document.getElementById('overviewStatus');
  if (!host) return;
  const alerts = ANALYTICS.alerts || [];
  const issues = ANALYTICS.data_health || [];
  const changes = ANALYTICS.changes || {};
  const parts = [];

  // ----- Combined Status card -----------------------------------------
  // Combines Attention (actionable) + Data Health (pipeline
  // integrity).  Aggregates severity across both.
  const haveAlerts = alerts.length > 0;
  const haveIssues = issues.length > 0;
  if (haveAlerts || haveIssues) {
    const counts = { high: 0, warn: 0, info: 0 };
    for (const a of alerts) counts[a.severity || 'info']++;
    for (const i of issues) counts[i.severity || 'info']++;
    const total = counts.high + counts.warn + counts.info;
    const dominant = counts.high > 0 ? 'high'
      : counts.warn > 0 ? 'warn' : 'info';
    const breakdown = ['high', 'warn', 'info']
      .filter(s => counts[s])
      .map(s => `<span class="dh-chip sev-${s}">${counts[s]} ${s}</span>`)
      .join(' ');

    const sections = [];
    if (haveAlerts) {
      const alertRows = alerts.map(a => {
        const sev = a.severity || 'info';
        return `<div class="alert-row">
          <span class="sev sev-${_htmlEsc(sev)}">${_htmlEsc(sev)}</span>
          <span>${_htmlEsc(a.message || '')}</span>
        </div>`;
      }).join('');
      sections.push(`<div class="dh-category">
        <h4>Attention <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${alerts.length} actionable signal${alerts.length === 1 ? '' : 's'}</span></h4>
        ${alertRows}
      </div>`);
    }
    if (haveIssues) {
      const byCat = {};
      for (const i of issues) {
        const c = i.category || 'Other';
        (byCat[c] = byCat[c] || []).push(i);
      }
      const issueParts = [];
      for (const cat of Object.keys(byCat)) {
        const rows = byCat[cat].map(i => {
          const detailsList = (i.details || []).map(d =>
            `<li>${_htmlEsc(d)}</li>`).join('');
          return `<div class="dh-issue">
            <div class="dh-issue-head">
              <span class="dh-chip sev-${_htmlEsc(i.severity)}">${_htmlEsc(i.severity)}</span>
              <span class="dh-message">${_htmlEsc(i.message)}</span>
            </div>
            ${detailsList ? `<ul class="dh-details">${detailsList}</ul>` : ''}
          </div>`;
        }).join('');
        issueParts.push(`<div class="dh-subcategory"><h5>${_htmlEsc(cat)}</h5>${rows}</div>`);
      }
      sections.push(`<div class="dh-category">
        <h4>Data Health <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${issues.length} pipeline-integrity item${issues.length === 1 ? '' : 's'}</span></h4>
        ${issueParts.join('')}
      </div>`);
    }

    parts.push(`<details class="feedback-panel feedback-collapsible">
      <summary class="dh-summary dh-${dominant}">
        <span class="dh-label">Status</span>
        <span class="dh-status">${total} item${total === 1 ? '' : 's'}</span>
        ${breakdown}
        <span class="dh-hint">click to expand</span>
      </summary>
      <div class="dh-body">${sections.join('')}</div>
    </details>`);
  } else {
    // Nothing flagged on either side → tiny "all clear" line so
    // the user can see the dashboard ran clean.
    parts.push(`<details class="feedback-panel feedback-collapsible" open style="opacity:0.55;">
      <summary class="dh-summary dh-clean">
        <span class="dh-label">Status</span>
        <span class="dh-status">all clear · no alerts, no integrity issues</span>
      </summary>
    </details>`);
  }

  // ----- What's Changed card ------------------------------------------
  if (changes && !changes.first_run && Object.keys(changes).length) {
    const fmt = n => n == null ? '—' : (n >= 0 ? '+' : '') + fmtMoney(n, 0);
    const cls = n => (n > 0 ? 'positive' : n < 0 ? 'negative' : '');
    const rows = [];
    if (changes.txn_count_delta != null && changes.txn_count_delta !== 0) {
      rows.push(['New Transactions', (changes.txn_count_delta >= 0 ? '+' : '') + changes.txn_count_delta, cls(changes.txn_count_delta)]);
    }
    if (changes.value_delta != null) rows.push(['Portfolio Value', fmt(changes.value_delta), cls(changes.value_delta)]);
    if (changes.basis_delta != null) rows.push(['Cost Basis', fmt(changes.basis_delta), cls(changes.basis_delta)]);
    if (changes.realized_delta != null) rows.push(['Realized P&L', fmt(changes.realized_delta), cls(changes.realized_delta)]);

    const moverRows = [
      ...(changes.top_gainers || []).slice(0, 3),
      ...(changes.top_losers || []).slice(0, 3),
    ].map(m => {
      const chg = m.delta || 0;
      return `<div class="mover-row">
        <span class="sym">${_htmlEsc(m.symbol || '')}</span>
        <span class="${cls(chg)}">${fmt(chg)}</span>
      </div>`;
    }).join('');

    const newClosed = [];
    if (changes.new_symbols && changes.new_symbols.length) {
      newClosed.push(`<div class="mover-row"><span class="sym">New:</span><span>${changes.new_symbols.slice(0, 5).map(_htmlEsc).join(', ')}</span></div>`);
    }
    if (changes.closed_symbols && changes.closed_symbols.length) {
      newClosed.push(`<div class="mover-row"><span class="sym">Closed:</span><span>${changes.closed_symbols.slice(0, 5).map(_htmlEsc).join(', ')}</span></div>`);
    }
    const prevRun = (changes.prev_run_at || '').slice(0, 10);
    const sinceLabel = prevRun ? `since ${_htmlEsc(prevRun)}` : 'since last run';
    if (rows.length || moverRows || newClosed.length) {
      let valueChip = '';
      if (changes.value_delta != null) {
        const vc = cls(changes.value_delta);
        valueChip = `<span class="dh-chip ${vc}" style="background:transparent;border:1px solid currentColor;">${fmt(changes.value_delta)}</span>`;
      }
      parts.push(`<details class="feedback-panel feedback-collapsible">
        <summary class="dh-summary dh-info">
          <span class="dh-label">What's Changed</span>
          <span class="dh-status">${sinceLabel}</span>
          ${valueChip}
          <span class="dh-hint">click to expand</span>
        </summary>
        <div class="dh-body">
          <div class="changes-list">
            ${rows.map(([l, v, c]) => `<div class="change-row"><span class="ch-label">${l}</span><span class="ch-val ${c}">${v}</span></div>`).join('')}
          </div>
          ${moverRows ? `<div class="changes-movers"><h4>Top Movers</h4>${moverRows}</div>` : ''}
          ${newClosed.length ? `<div class="changes-movers"><h4>Positions</h4>${newClosed.join('')}</div>` : ''}
        </div>
      </details>`);
    }
  }

  host.innerHTML = parts.join('');
}

// Backward-compat shim — older call sites kept invoking the old name.
function renderOverviewFeedback() { renderOverviewStatus(); }

// --- Monthly P&L grid (year × month heatmap) — Performance tab section ----
const _MONTH_LABELS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
const _MONTH_FULL = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function _buildMonthlyPnlSection() {
  const mp = ANALYTICS.monthly_pnl;
  if (!mp || !mp.rows || !mp.rows.length) return '';
  const minR = mp.min_return || 0, maxR = mp.max_return || 0;
  // Color scale: red below 0, green above; intensity proportional to
  // the row's distance from 0 vs the most-extreme observed |return|.
  const span = Math.max(Math.abs(minR), Math.abs(maxR), 0.001);
  const colorFor = (r) => {
    if (r == null) return 'transparent';
    const intensity = Math.min(1, Math.abs(r) / span);
    const alpha = 0.18 + intensity * 0.62;
    return r >= 0 ? `rgba(74, 222, 128, ${alpha.toFixed(2)})`
      : `rgba(248, 113, 113, ${alpha.toFixed(2)})`;
  };

  const headerCells = _MONTH_LABELS.map((m, i) =>
    `<th class="num" title="${_MONTH_FULL[i]}">${m}</th>`).join('');
  const rowsHtml = mp.rows.map(row => {
    let yearTotal = 0;
    const cells = [];
    for (let m = 1; m <= 12; m++) {
      const r = row.months[m];
      if (r == null) {
        cells.push(`<td class="mp-cell"></td>`);
      } else {
        yearTotal = (1 + yearTotal) * (1 + r) - 1;
        const tip = `${_MONTH_FULL[m - 1]} ${row.year}: ${(r * 100).toFixed(2)}%`;
        cells.push(`<td class="mp-cell" style="background:${colorFor(r)};" title="${_htmlEsc(tip)}">${(r * 100).toFixed(1)}</td>`);
      }
    }
    const ytdCls = yearTotal >= 0 ? 'positive' : 'negative';
    return `<tr>
      <th class="mp-year">${row.year}</th>
      ${cells.join('')}
      <td class="mp-ytd"><span class="${ytdCls}">${(yearTotal * 100).toFixed(1)}%</span></td>
    </tr>`;
  }).join('');

  const best = mp.best_month;
  const worst = mp.worst_month;
  const filteredNote = mp.n_months_filtered > 0
    ? ` <span style="margin-left:8px;">· ${mp.n_months_filtered} month(s) excluded from best/worst and Sharpe/Sortino (early-portfolio noise + |return| &gt; 50% data-artifact filter)</span>`
    : '';
  const summary = (best && worst) ? `
    <div style="display:flex;gap:24px;font-size:0.78rem;color:var(--text-dim);margin-top:8px;flex-wrap:wrap;">
      <span>Best month: <b class="positive">${_MONTH_FULL[best.month - 1]} ${best.year} +${(best.return * 100).toFixed(2)}%</b></span>
      <span>Worst month: <b class="negative">${_MONTH_FULL[worst.month - 1]} ${worst.year} ${(worst.return * 100).toFixed(2)}%</b></span>
      ${filteredNote}
    </div>` : '';

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Monthly P&L</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">
        Investment return per month — net of contributions and withdrawals.  Compounded across the row to give a YTD figure.
      </span>
    </div>
    <div class="panel">
      <table class="monthly-pnl-table">
        <thead><tr><th></th>${headerCells}<th class="num">YTD</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
      ${summary}
    </div>
  `;
}

// --- Daily P&L bars (last 30 days) — Performance tab section --------------
function _buildDailyPnlSection() {
  const rows = Array.isArray(ANALYTICS.daily_pnl) ? ANALYTICS.daily_pnl : [];
  if (!rows.length) return '';
  const maxAbs = Math.max(...rows.map(r => Math.abs(r.change || 0)), 1);
  const bars = rows.map(r => {
    const pnl = r.change || 0;
    const h = Math.max(1, (Math.abs(pnl) / maxAbs) * 100);
    const neg = pnl < 0;
    const sign = pnl >= 0 ? '+' : '';
    const pct = r.change_pct || 0;
    const pctStr = ` (${sign}${pct.toFixed(2)}%)`;
    const tipText = `${r.date}: ${sign}${fmtMoney(pnl, 0)}${pctStr}`;
    return `<div class="daily-pnl-bar ${neg ? 'neg' : ''}" title="${_htmlEsc(tipText)}">
      <div class="tip">${_htmlEsc(r.date)}: <b>${sign}${fmtMoney(pnl, 0)}</b>${pctStr}</div>
      <div class="bar" style="height:${h}%"></div>
    </div>`;
  }).join('');
  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Recent Daily P&L</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">
        Market-only moves (today's positions repriced at recent dates) — same-day cash flows excluded.
      </span>
    </div>
    <div class="daily-pnl-bars">${bars}</div>
  `;
}

// --- Concentration grid ---------------------------------------------------
function renderConcentration() {
  const host = document.getElementById('concentrationContainer');
  if (!host) return;
  const c = ANALYTICS.concentration || {};
  const hhiGlobal = c.herfindahl != null ? c.herfindahl.toFixed(0) : '—';
  const top5Global = c.top_5_concentration != null ? fmtPct(c.top_5_concentration, 1) : '—';
  // By Sector deliberately excludes Cash — see analytics/concentration.py
  // for the rationale.  Show a small footnote if cash was excluded so
  // the % values being "of equity" instead of "of total" is transparent.
  const cashExcluded = c.cash_excluded_from_sectors || 0;
  const sectorFootnote = cashExcluded > 0.01
    ? `<div class="conc-foot" title="Concentration risk is about equity exposure — cash is the absence of risk, so % values here are of invested capital, not total portfolio.">% of equity (excludes ${fmtMoney(cashExcluded)} cash)</div>`
    : '';
  const sections = [
    { rows: c.positions || [], title: 'By Position', labelKey: 'symbol', footnote: '' },
    { rows: c.sectors || [], title: 'By Sector', labelKey: 'sector', footnote: sectorFootnote },
    { rows: c.account_groups || [], title: 'By Account', labelKey: 'account_group', footnote: '' },
  ];
  const cards = sections.map((s, i) => {
    const top = s.rows.slice(0, 5);
    if (!top.length) return '';
    const sectionHhi = (top.reduce((sum, r) => sum + Math.pow((r.pct || 0) / 100, 2), 0) * 10000).toFixed(0);
    const sectionTop5 = fmtPct(top.reduce((sum, r) => sum + (r.pct || 0), 0), 1);
    const rows = top.map(r => {
      const pct = r.pct || 0;
      let cls = 'ok';
      if (pct >= 20) cls = 'high';
      else if (pct >= 10) cls = 'warn';
      const w = Math.min(100, pct * 2);
      const label = r[s.labelKey] || '';
      return `<div class="conc-row ${cls}">
        <span class="conc-label" title="${_htmlEsc(label)}">${_htmlEsc(label)}</span>
        <span class="conc-bar-wrap"><span class="conc-bar" style="width:${w}%"></span></span>
        <span class="conc-pct">${fmtPct(pct, 1)}</span>
      </div>`;
    }).join('');
    // Show global HHI/top5 on the first (positions) card; others get section-local
    const hhi = i === 0 ? hhiGlobal : sectionHhi;
    const top5 = i === 0 ? top5Global : sectionTop5;
    return `<div class="concentration-card">
      <h4>${s.title}</h4>
      <div class="conc-stats">
        <span>HHI <b>${hhi}</b></span>
        <span>Top 5 <b>${top5}</b></span>
      </div>
      ${rows}
      ${s.footnote}
    </div>`;
  }).join('');
  host.innerHTML = `<div class="concentration-grid">${cards}</div>`;
}

// --- Data Health collapsible panel (Overview tab) ------------------------
// Surfaces pipeline-integrity diagnostics from analytics.data_health.
// Default-collapsed via the <details> element so it doesn't crowd the
// page; the summary line shows the issue count + severity breakdown.
// Data Health is now folded into renderOverviewStatus().  This shim
// keeps any leftover call sites working as no-ops.
function renderDataHealth() { /* merged into renderOverviewStatus() */ }

// Overview tab renderer: kicks off top holdings / recent txns / allocation.
// Daily P&L moved to Performance tab — Overview is meant to be a snapshot.
registerTabRenderer('overview', () => {
  renderOverviewStatus();
  renderTopHoldings();
  renderRecentTransactions();
  renderAllocation();
  renderConcentration();
  renderAnnualBreakdown();
});
// Overview is active on initial load, so render its lazy bits now.
if (document.getElementById('tab-overview').classList.contains('active')) {
  renderOverviewStatus();
  renderTopHoldings();
  renderRecentTransactions();
  renderAllocation();
  renderConcentration();
  renderAnnualBreakdown();
  TAB_RENDERED.add('overview');
}

// Render a row of stat cards from a `[{label, value, cls?, title?}]`
// array.  Used by every tab that has a `<div class="stats">…</div>`
// block — Overview, Holdings, Performance, Options, etc.  Inline
// duplicates of the same template existed at every call site before
// this helper was extracted.
//
//   cards       — [{ label, value, cls?, title? }]
//   extraClass  — optional extra class for the wrapper (e.g.
//                 "opt-anchor-stats" for the Options anchor row)
function _renderStatCards(cards, extraClass) {
  const wrapCls = extraClass ? `stats ${extraClass}` : 'stats';
  return `<div class="${wrapCls}">` + cards.map(c => {
    const cls = c.cls ? `stat-card ${c.cls}` : 'stat-card';
    const titleAttr = c.title ? ` title="${_htmlEsc(c.title)}"` : '';
    return `<div class="${cls}"${titleAttr}><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('') + '</div>';
}

// Render one account-filter chip with the standardized look used
// across every filter bar: colored text from ACCOUNT_COLORS when
// inactive, full purple background when active.  Drop-in for any
// "Account:" toggle row.
//
//   account  — string account_group name, or null for the "All" reset
//   active   — true if this chip is the currently-selected filter
//   onclick  — JS string for the onclick attribute (already escaped)
//   label    — optional override for the visible text (defaults to
//              the account name, or "All" when account is null)
function _renderAccountChip(account, active, onclickJs, label) {
  const text = label != null ? label : (account || 'All');
  const color = account ? (ACCOUNT_COLORS[account] || '') : '';
  // Inactive chips with a known color use the color as text — matches
  // the colored-text pattern across all tabs.  Active chips inherit
  // the white-on-purple .tbtn.active styling, no inline color needed.
  const styleAttr = (!active && color) ? ` style="color:${color};"` : '';
  return `<button class="tbtn${active ? ' active' : ''}"${styleAttr} onclick="${onclickJs}">${text}</button>`;
}

// =========================================================================
// Options tab
// =========================================================================

// Options tab filter state.
//   _optWindow: 'lifetime' (default), or a relative key
//               ('5y' | '3y' | '2y' | '1y' | 'ytd' | '6mo' | '3mo' | '30d'),
//               or an absolute year ('2026' | '2025' | …).
//   _optAccountFilter: null = all accounts; otherwise an account_group
//                      string to restrict the view to one broker.
// Stats / call-put / by-underlying / annual / cumulative are all
// recomputed in JS from the filtered closed_trades subset whenever
// either filter is non-default, so the dashboard stays in sync with
// the visible window.  Open Contracts are filtered by account only
// (window doesn't apply to currently-held positions).
let _optWindow = 'lifetime';
let _optAccountFilter = null;

function _setOptWindow(v) {
  _optWindow = v || 'lifetime';
  renderOptions();
}
function _setOptAccountFilter(v) {
  _optAccountFilter = (v === 'all' || !v) ? null : v;
  renderOptions();
}

// Resolve _optWindow into a concrete [start_iso, end_iso] date range,
// or null for "lifetime" (no filter).  YYYY values map to the full
// calendar year; relative keys anchor on today's date and walk back.
function _optWindowRange() {
  const today = new Date();
  const isoToday = today.toISOString().slice(0, 10);
  const w = _optWindow;
  if (!w || w === 'lifetime') return null;
  if (/^\d{4}$/.test(w)) return [`${w}-01-01`, `${w}-12-31`];
  if (w === 'ytd') return [`${today.getFullYear()}-01-01`, isoToday];
  let m;
  const start = new Date(today);
  if ((m = w.match(/^(\d+)y$/))) start.setFullYear(start.getFullYear() - parseInt(m[1], 10));
  else if ((m = w.match(/^(\d+)mo$/))) start.setMonth(start.getMonth() - parseInt(m[1], 10));
  else if ((m = w.match(/^(\d+)d$/))) start.setDate(start.getDate() - parseInt(m[1], 10));
  else return null;
  return [start.toISOString().slice(0, 10), isoToday];
}

function _optWindowLabel() {
  const w = _optWindow;
  if (!w || w === 'lifetime') return 'Lifetime';
  if (/^\d{4}$/.test(w)) return w;
  if (w === 'ytd') return `${new Date().getFullYear()} YTD`;
  let m;
  if ((m = w.match(/^(\d+)y$/))) return `Last ${m[1]} year${m[1] === '1' ? '' : 's'}`;
  if ((m = w.match(/^(\d+)mo$/))) return `Last ${m[1]} month${m[1] === '1' ? '' : 's'}`;
  if ((m = w.match(/^(\d+)d$/))) return `Last ${m[1]} days`;
  return w;
}

// Stats + analytics rebuilders.  When the window or account filter is
// active, the precomputed Python aggregates (stats, by_underlying,
// annual_summary, cumulative_pnl) don't apply — so we rebuild them
// from the filtered closed_trades subset.

// Single-pass accumulator shared by _computeOptionsStats and
// _splitClosedByType.  Returns raw running totals; callers shape the
// final stat object (rounding, win_rate, etc.) from these.
function _accumulateTradeStats(trades) {
  let wins = 0, losses = 0, be = 0, pnl = 0, holdSum = 0, holdN = 0;
  let winSum = 0, lossSum = 0, topWin = null, topLoss = null;
  for (const c of trades) {
    const r = c.realized || 0;
    pnl += r;
    if (r > 0)      { wins++;   winSum  += r; if (topWin  == null || r > topWin)  topWin  = r; }
    else if (r < 0) { losses++; lossSum += r; if (topLoss == null || r < topLoss) topLoss = r; }
    else            { be++; }
    if (c.hold_days != null) { holdSum += c.hold_days; holdN++; }
  }
  return { n: trades.length, wins, losses, be, pnl, holdSum, holdN,
           winSum, lossSum, topWin, topLoss };
}

const _r2 = v => v == null ? null : Math.round(v * 100) / 100;

function _computeOptionsStats(closed) {
  const a = _accumulateTradeStats(closed);
  const decided = a.wins + a.losses;
  return {
    total_pnl: _r2(a.pnl),
    trades: a.n,
    wins: a.wins, losses: a.losses, breakevens: a.be,
    win_rate: decided > 0 ? a.wins / decided : null,
    avg_hold_days: a.holdN > 0 ? a.holdSum / a.holdN : null,
    biggest_winner: a.topWin,
    biggest_loser:  a.topLoss,
    profit_factor:  a.lossSum < 0 ? _r2(a.winSum / Math.abs(a.lossSum)) : null,
    expectancy:     a.n > 0 ? _r2(a.pnl / a.n) : null,
    avg_winner:     a.wins   > 0 ? _r2(a.winSum  / a.wins)   : null,
    avg_loser:      a.losses > 0 ? _r2(a.lossSum / a.losses) : null,
    call: _splitClosedByType(closed, 'Call'),
    put:  _splitClosedByType(closed, 'Put'),
  };
}

// Group closed trades by an arbitrary key (underlying / year /
// account_group …) and produce {keyField, trades, wins, realized,
// win_rate} rows sorted by the given comparator.
function _groupTrades(closed, keyFn, keyField, sortFn) {
  const by = Object.create(null);
  for (const c of closed) {
    const k = keyFn(c);
    if (k == null || k === '') continue;
    if (!by[k]) by[k] = { [keyField]: k, trades: 0, wins: 0, realized: 0 };
    by[k].trades++;
    if ((c.realized || 0) > 0) by[k].wins++;
    by[k].realized += c.realized || 0;
  }
  return Object.values(by)
    .map(r => ({ ...r, realized: _r2(r.realized),
                       win_rate: r.trades > 0 ? r.wins / r.trades : null }))
    .sort(sortFn);
}
const _computeByUnderlying = closed => _groupTrades(closed,
  c => c.underlying || '(unknown)', 'underlying',
  (a, b) => b.realized - a.realized);
const _computeAnnualOptionsSummary = closed => _groupTrades(closed,
  c => (c.close_date || '').slice(0, 4), 'year',
  (a, b) => b.year.localeCompare(a.year));
function _computeCumulativePnL(closed) {
  let run = 0;
  return [...closed]
    .sort((a, b) => (a.close_date || '').localeCompare(b.close_date || ''))
    .map(c => ({ date: c.close_date, value: Math.round((run += (c.realized || 0)) * 100) / 100 }));
}

// Fallback aggregator used when the Python analytics payload doesn't
// carry pre-computed call/put splits (older exports).  Mirrors the
// shape of analytics.options.stats.call / .put exactly so the
// rendering code can read either source uniformly.
function _splitClosedByType(closed, kind) {
  const subset = closed.filter(c => {
    const t = c.option_type || c.type || (parseOptionSymbol(c.symbol) || {}).type;
    return t === kind;
  });
  const a = _accumulateTradeStats(subset);
  const decided = a.wins + a.losses;
  return {
    trades: a.n,
    wins: a.wins, losses: a.losses, breakevens: a.be,
    win_rate: decided > 0 ? a.wins / decided : null,
    total_pnl: _r2(a.pnl),
    avg_hold_days: a.holdN > 0 ? a.holdSum / a.holdN : null,
    avg_winner: a.wins   > 0 ? _r2(a.winSum  / a.wins)   : null,
    avg_loser:  a.losses > 0 ? _r2(a.lossSum / a.losses) : null,
  };
}

// Parse a Robinhood option contract symbol like
// "META 12/18/2026 Call $800.00"  →
//   { underlying:"META", expiry:"2026-12-18", type:"Call", strike:800 }
function parseOptionSymbol(sym) {
  if (!sym) return null;
  const m = /^(\S+)\s+(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(Call|Put)\s+\$([\d,.]+)/.exec(sym);
  if (!m) return null;
  const [, under, mo, d, y, type, strikeRaw] = m;
  const pad = (s) => String(s).padStart(2, '0');
  return {
    underlying: under,
    expiry: y + '-' + pad(mo) + '-' + pad(d),
    type,
    strike: parseFloat(strikeRaw.replace(/,/g, '')),
  };
}

function isOptionSymbol(sym) {
  return !!sym && (sym.includes(' Call ') || sym.includes(' Put ') || sym.endsWith(' OPTION'));
}

function renderOptions() {
  const root = document.getElementById('optionsContent');
  if (!root) return;

  // Closed trades + open contracts come straight from the precomputed
  // analytics (analytics/options.py).  The stats / by-underlying /
  // annual / cumulative figures below ARE recomputed in JS — not as a
  // fallback, but because they're re-derived from the window/account-
  // filtered subset (lifetime+no-filter matches Python by construction).
  const opt = ANALYTICS.options || {};
  const allClosed = opt.closed_trades || [];
  const allOpen = opt.open_contracts || [];

  // ---- Apply window + account filters -----------------------------
  // Window: filter closed trades by close_date in range.  Open contracts
  // are not filtered by date (they're current positions — the window
  // refers to "when did the trade close", which doesn't apply to a
  // still-open contract).  Account filter applies to both.
  const range = _optWindowRange();
  let closed = allClosed;
  if (range) closed = closed.filter(c => (c.close_date || '') >= range[0] && (c.close_date || '') <= range[1]);
  if (_optAccountFilter) closed = closed.filter(c => c.account_group === _optAccountFilter);

  let open = allOpen;
  if (_optAccountFilter) open = open.filter(o => o.account_group === _optAccountFilter);

  // Stats / by_underlying / annual_summary / cumulative — always
  // recompute from the filtered subset.  When window=lifetime and no
  // account filter is set, the result matches the precomputed Python
  // analytics by construction.
  const stats = _computeOptionsStats(closed);
  const byUnderSorted = _computeByUnderlying(closed).map(r =>
    [r.underlying, { trades: r.trades, wins: r.wins, realized: r.realized }]);
  const yearOptSorted = _computeAnnualOptionsSummary(closed).map(r =>
    [r.year, { trades: r.trades, wins: r.wins, realized: r.realized }]);
  const cumPoints = _computeCumulativePnL(closed);

  const total = stats.total_pnl != null ? stats.total_pnl : 0;
  const wins = stats.wins;
  const losses = stats.losses;
  const breakevens = stats.breakevens;
  const decided = wins + losses;
  const winRate = stats.win_rate != null ? stats.win_rate * 100 : (decided > 0 ? (wins / decided) * 100 : 0);
  const avgHold = stats.avg_hold_days != null ? stats.avg_hold_days : 0;
  const topWinVal = stats.biggest_winner;
  const topLossVal = stats.biggest_loser;
  const winLabel = _optWindowLabel();

  // --- Render HTML ---
  // ---- Anchor cards: ALL OPTIONS · LIFETIME ---------------------
  // Mirrors the Performance tab's "Whole portfolio · all-time"
  // anchor row.  Computed from the UNFILTERED closed-trade set so
  // these numbers stay stable as the user changes the account /
  // window toggles below — a fixed reference point above the
  // filtered view.
  const lifetimeStats = _computeOptionsStats(allClosed);
  const lifetimeDecided = lifetimeStats.wins + lifetimeStats.losses;
  const lifetimeWinRate = lifetimeDecided > 0
    ? (lifetimeStats.win_rate * 100).toFixed(1) + '%' : '—';
  const lifetimePnl = lifetimeStats.total_pnl != null ? lifetimeStats.total_pnl : 0;
  const lifetimePf = lifetimeStats.profit_factor;
  const anchorCards = [
    {
      label: 'Lifetime Options P&L',
      value: fmtSigned(lifetimePnl),
      cls: lifetimePnl >= 0 ? 'positive' : 'negative',
      title: 'Realized P&L summed across every closed option trade in the dataset.  Doesn\'t change with the filters below.',
    },
    {
      label: 'Lifetime Win Rate',
      value: lifetimeWinRate,
      title: `Decided trades only (wins + losses).  Breakevens excluded.\n${lifetimeStats.wins} wins / ${lifetimeStats.losses} losses / ${lifetimeStats.breakevens} breakeven.`,
    },
    {
      label: 'Lifetime Trades',
      value: lifetimeStats.trades.toLocaleString(),
      title: 'Total closed option trades — sells, expirations, and exercises.',
    },
    {
      label: 'Lifetime Profit Factor',
      value: lifetimePf != null ? lifetimePf.toFixed(2) : '—',
      cls: lifetimePf != null && lifetimePf >= 1 ? 'positive' : (lifetimePf != null && lifetimePf < 1 ? 'negative' : ''),
      title: 'Gross dollar wins ÷ |gross dollar losses|.  >1 means winners outweigh losers in dollar terms.',
    },
    {
      label: 'Open Contracts',
      value: allOpen.length.toLocaleString(),
      title: 'Option contracts currently held.  Not affected by the window filter (current positions are intrinsically as-of-today).',
    },
  ];
  const anchorHtml =
    `<div class="opt-anchor-label">All options · lifetime</div>` +
    _renderStatCards(anchorCards, 'opt-anchor-stats');

  // Filter bars — Performance-tab style 2-row pattern: account on the
  // top row, window (relative + per-year) on the bottom.
  const optAccounts = [...new Set(allClosed.map(c => c.account_group || '').filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  const acctRow = optAccounts.length > 1 ? `
    <div class="toggles-row">
      <span class="toggles-label">Account:</span>
      ${_renderAccountChip(null, !_optAccountFilter, "_setOptAccountFilter('all')", 'All')}
      ${optAccounts.map(acct =>
        _renderAccountChip(acct, _optAccountFilter === acct,
          `_setOptAccountFilter('${acct.replace(/'/g, "\\'")}')`)
      ).join('')}
    </div>` : '';
  const optYears = [...new Set(allClosed.map(c => (c.close_date || '').slice(0, 4)).filter(Boolean))]
    .sort((a, b) => b.localeCompare(a));   // newest year first
  const winPill = (key, label) =>
    `<button class="tbtn ${_optWindow === key ? 'active' : ''}" onclick="_setOptWindow('${key}')">${label}</button>`;
  const winRow = `
    <div class="toggles-row">
      <span class="toggles-label">Window:</span>
      ${winPill('lifetime', 'Lifetime')}
      ${winPill('5y', '5y')}
      ${winPill('3y', '3y')}
      ${winPill('2y', '2y')}
      ${winPill('1y', '1y')}
      ${winPill('ytd', 'YTD')}
      ${winPill('6mo', '6mo')}
      ${winPill('3mo', '3mo')}
      ${winPill('30d', '30d')}
      ${optYears.length ? '<span class="toggles-sep">|</span>' : ''}
      ${optYears.map(y => winPill(y, y)).join('')}
    </div>`;
  const filterBar = `<div class="toggles-card">${acctRow}${winRow}</div>`;

  // Note: Open Contracts moved to the anchor row above — it's a
  // current-positions count and doesn't react to the window filter.
  const statCards = [
    { label: `${winLabel} Options P&L`, value: fmtSigned(total), cls: total >= 0 ? 'positive' : 'negative' },
    { label: 'Win Rate', value: decided > 0 ? winRate.toFixed(1) + '%' : '—' },
    { label: 'Trades (W / L / BE)', value: `${wins} / ${losses} / ${breakevens}` },
    { label: 'Avg Hold Days', value: avgHold ? avgHold.toFixed(1) : '—' },
    {
      label: 'Biggest Winner', value: topWinVal != null && topWinVal > 0 ? fmtSigned(topWinVal) : '—',
      cls: topWinVal != null && topWinVal > 0 ? 'positive' : ''
    },
    {
      label: 'Biggest Loser', value: topLossVal != null && topLossVal < 0 ? fmtSigned(topLossVal) : '—',
      cls: topLossVal != null && topLossVal < 0 ? 'negative' : ''
    },
  ];
  const statsHtml = _renderStatCards(statCards);

  // ---- Call vs Put breakdown + lifetime ratios -------------------
  // Three-column panel: per-type stats (Calls, Puts) and lifetime
  // ratios (profit factor + expectancy).  Surfaces "am I a better
  // call buyer than put buyer?" at a glance.  Falls back to JS
  // aggregation when the Python analytics payload is missing.
  const callS = stats.call || _splitClosedByType(closed, 'Call');
  const putS = stats.put || _splitClosedByType(closed, 'Put');
  function _fmtType(s) {
    const wr = s.win_rate != null ? (s.win_rate * 100).toFixed(1) + '%' : '—';
    const pnl = s.total_pnl != null ? s.total_pnl : 0;
    const pnlCls = pnl > 0 ? 'positive' : (pnl < 0 ? 'negative' : '');
    const wlbe = `${s.wins || 0} W / ${s.losses || 0} L${s.breakevens ? ' / ' + s.breakevens + ' BE' : ''}`;
    const hold = s.avg_hold_days != null && s.avg_hold_days > 0
      ? s.avg_hold_days.toFixed(1) + 'd' : '—';
    const avgWin = s.avg_winner != null ? fmtSigned(s.avg_winner) : '—';
    const avgLoss = s.avg_loser != null ? fmtSigned(s.avg_loser) : '—';
    return `
      <div style="display:grid;grid-template-columns:auto 1fr;column-gap:14px;row-gap:4px;font-size:0.85rem;">
        <div style="color:var(--text-dim);">Trades</div>      <div><b>${s.trades || 0}</b> <span style="color:var(--text-dim);font-size:0.78rem;">(${wlbe})</span></div>
        <div style="color:var(--text-dim);">Win Rate</div>    <div><b>${wr}</b></div>
        <div style="color:var(--text-dim);">Total P&amp;L</div>  <div><b><span class="${pnlCls}">${fmtSigned(pnl)}</span></b></div>
        <div style="color:var(--text-dim);">Avg Winner</div>  <div><span class="positive">${avgWin}</span></div>
        <div style="color:var(--text-dim);">Avg Loser</div>   <div><span class="negative">${avgLoss}</span></div>
        <div style="color:var(--text-dim);">Avg Hold</div>    <div>${hold}</div>
      </div>`;
  }
  const pf = stats.profit_factor;
  const exp = stats.expectancy;
  const callPutPanel = `
    <div class="overview-split-2" style="margin-top:16px;">
      <div class="panel">
        <h3 style="color:#60a5fa;">Calls</h3>
        ${_fmtType(callS)}
      </div>
      <div class="panel">
        <h3 style="color:#f59e0b;">Puts</h3>
        ${_fmtType(putS)}
      </div>
      <div class="panel">
        <h3>${winLabel} Ratios</h3>
        <div style="display:grid;grid-template-columns:auto 1fr;column-gap:14px;row-gap:4px;font-size:0.85rem;">
          <div style="color:var(--text-dim);" title="Gross wins ÷ |gross losses|.  >1 means winners outweigh losers in dollars.">Profit Factor</div>
          <div><b>${pf != null ? pf.toFixed(2) : '—'}</b></div>
          <div style="color:var(--text-dim);" title="Average realized P&amp;L per closed trade.">Expectancy</div>
          <div><b class="${exp > 0 ? 'positive' : (exp < 0 ? 'negative' : '')}">${exp != null ? fmtSigned(exp) : '—'}</b></div>
          <div style="color:var(--text-dim);">Avg Winner</div>
          <div class="positive">${stats.avg_winner != null ? fmtSigned(stats.avg_winner) : '—'}</div>
          <div style="color:var(--text-dim);">Avg Loser</div>
          <div class="negative">${stats.avg_loser != null ? fmtSigned(stats.avg_loser) : '—'}</div>
        </div>
      </div>
    </div>`;

  // Cumulative P&L chart (SVG, stand-alone mini)
  const chartHtml = renderMiniLineChart(cumPoints, {
    id: 'optionsCumSvg',
    color: total >= 0 ? '#4ade80' : '#f87171',
    height: 240,
    yFormatter: fmtMoneyShort,
    emptyMsg: 'No closed option trades yet.',
  });

  // Open contracts table
  const openCols = ['underlying', 'expiry', 'dte', 'type', 'strike', 'qty', 'open_date', 'entry_price'];
  const openHead = `<tr>
    <th>Underlying</th><th>Expiry</th><th class="num">DTE</th><th>Type</th>
    <th class="num">Strike</th><th class="num">Qty</th>
    <th>Opened</th><th class="num">Entry Premium</th></tr>`;
  const openRows = open.length ? open.map(o => {
    const otype = o.option_type || o.type || '';
    const dteStr = o.dte == null ? '—'
      : (o.dte < 0 ? `<span class="negative">${o.dte}</span>` : `<span class="${o.dte <= 7 ? 'negative' : ''}">${o.dte}</span>`);
    return `<tr>
      <td><b>${o.underlying || ''}</b></td>
      <td>${o.expiry || '—'}</td>
      <td class="num">${dteStr}</td>
      <td>${otype}</td>
      <td class="num">${o.strike != null ? fmtMoney(o.strike, 2) : '—'}</td>
      <td class="num">${o.qty}</td>
      <td>${o.open_date || '—'}</td>
      <td class="num">${o.entry_price != null ? fmtMoney(o.entry_price, 2) : '—'}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="${openCols.length}" style="color:var(--text-dim);padding:12px;">No open contracts.</td></tr>`;

  // Closed trades table (most recent first)
  const closedRecent = [...closed].sort((a, b) => (b.close_date || '').localeCompare(a.close_date || ''));
  const closedRows = closedRecent.slice(0, 200).map(c => {
    // Analytics-provided rows have these parsed fields inline; fall
    // back to local parsing for rows from the legacy JS builder.
    const underlying = c.underlying || (parseOptionSymbol(c.symbol) || {}).underlying || '';
    const expiry = c.expiry || (parseOptionSymbol(c.symbol) || {}).expiry || '';
    const otype = c.option_type || c.type || (parseOptionSymbol(c.symbol) || {}).type || '';
    const strike = c.strike != null ? c.strike : (parseOptionSymbol(c.symbol) || {}).strike;
    const hold = c.hold_days != null ? c.hold_days
      : (c.open_date && c.close_date
        ? Math.max(0, Math.round((new Date(c.close_date) - new Date(c.open_date)) / 86400000))
        : null);
    const rCls = c.realized > 0 ? 'positive' : (c.realized < 0 ? 'negative' : '');
    return `<tr>
      <td>${c.close_date || ''}</td>
      <td><b>${underlying}</b></td>
      <td>${expiry}</td>
      <td>${otype}</td>
      <td class="num">${strike != null ? fmtMoney(strike, 2) : '—'}</td>
      <td>${(c.action || '').replace('Option ', '')}</td>
      <td class="num">${c.qty}</td>
      <td class="num">${fmtMoney(c.proceeds)}</td>
      <td class="num">${fmtMoney(c.basis)}</td>
      <td class="num"><span class="${rCls}">${fmtSigned(c.realized)}</span></td>
      <td class="num">${hold != null ? hold : '—'}</td>
    </tr>`;
  }).join('');
  const closedTail = closedRecent.length > 200 ? `<tr><td colspan="11" style="color:var(--yellow);text-align:center;">Showing 200 most recent of ${closedRecent.length}.</td></tr>` : '';

  // P&L by underlying
  const underRows = byUnderSorted.map(([u, s]) => {
    const rCls = s.realized > 0 ? 'positive' : (s.realized < 0 ? 'negative' : '');
    const wr = s.trades > 0 ? ((s.wins / s.trades) * 100).toFixed(0) + '%' : '—';
    return `<tr>
      <td><b>${u}</b></td>
      <td class="num">${s.trades}</td>
      <td class="num">${wr}</td>
      <td class="num"><span class="${rCls}">${fmtSigned(s.realized)}</span></td>
    </tr>`;
  }).join('');

  // Annual options activity table
  const yearOptRows = yearOptSorted.map(([y, s]) => {
    const rCls = s.realized > 0 ? 'positive' : (s.realized < 0 ? 'negative' : '');
    const wr = s.trades > 0 ? ((s.wins / s.trades) * 100).toFixed(0) + '%' : '—';
    return `<tr>
      <td><b>${y}</b></td>
      <td class="num">${s.trades}</td>
      <td class="num">${wr}</td>
      <td class="num"><span class="${rCls}">${fmtSigned(s.realized)}</span></td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    ${anchorHtml}

    ${filterBar}

    ${statsHtml}

    ${callPutPanel}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Cumulative Realized P&amp;L</span></h2><span class="as-of-hint" style="margin-left:auto;">${winLabel}</span></div>
    ${chartHtml}

    <div class="overview-split">
      <div class="panel">
        <h3>P&amp;L by Underlying</h3>
        <div class="table-wrap">
          <table class="mini-table"><thead><tr>
            <th>Underlying</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Realized</th>
          </tr></thead><tbody>${underRows || '<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody></table>
        </div>
        <div class="panel-foot"><a onclick="activateTab('tax')">View ST / LT / §1256 split on the Tax tab →</a></div>
      </div>
      <div class="panel">
        <h3>Annual Options Summary</h3>
        <div class="table-wrap">
          <table class="mini-table"><thead><tr>
            <th>Year</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Realized</th>
          </tr></thead><tbody>${yearOptRows || '<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody></table>
        </div>
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Open Contracts (${open.length})</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Current positions — window filter doesn't apply${_optAccountFilter ? ` · filtered to ${_htmlEsc(_optAccountFilter)}` : ''}</span>
    </div>
    <div class="table-wrap">
      <table class="mini-table"><thead>${openHead}</thead><tbody>${openRows}</tbody></table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Closed Trades</span></h2></div>
    <div class="table-wrap">
      <table class="mini-table"><thead><tr>
        <th>Close Date</th><th>Underlying</th><th>Expiry</th><th>Type</th>
        <th class="num">Strike</th><th>Close</th><th class="num">Qty</th>
        <th class="num">Proceeds</th><th class="num">Basis</th><th class="num">Realized</th><th class="num">Hold Days</th>
      </tr></thead><tbody>${closedRows}${closedTail}</tbody></table>
    </div>
  `;
}

// Generic full-width line chart.  Renders at the container's actual
// pixel width so text and shapes don't distort (like the main history
// chart).  Uses queueMicrotask to measure the container after DOM
// insert, then injects the SVG content with the measured width.
function renderMiniLineChart(points, opts) {
  opts = opts || {};
  const id = opts.id || ('miniChart_' + Math.random().toString(36).slice(2, 7));
  const color = opts.color || '#a78bfa';
  const height = opts.height || 260;
  const yFmt = opts.yFormatter || fmtMoneyShort;
  if (!points || !points.length) {
    return `<div class="chart-empty">${opts.emptyMsg || 'No data.'}</div>`;
  }

  // Build SVG content at a given width (in CSS pixels).  Factored out
  // so we can call it once at initial render (with 800 default) and
  // again after we know the actual container width.
  const buildContent = (W) => _buildMiniLineChartContent(points, W, height, color, yFmt, id);

  // Schedule a post-insert pass that measures and re-renders at the
  // correct width.  Also rebinds hover (dependent on x/y scales).
  queueMicrotask(() => {
    const svg = document.getElementById(id);
    if (!svg) return;
    const actualW = Math.round(svg.getBoundingClientRect().width) || 800;
    const { content, bindHover } = buildContent(actualW);
    svg.setAttribute('viewBox', `0 0 ${actualW} ${height}`);
    svg.innerHTML = content;
    bindHover();
  });

  // Initial render uses a neutral placeholder viewBox; the microtask
  // above swaps in real content before the user sees anything.
  // The `chart-svg` class inherits axis / grid / line styles from the
  // page's main chart CSS (light-on-dark, not the browser default
  // black-on-dark).
  return `<div class="chart-wrap" style="padding:10px;position:relative;">
    <svg id="${id}" class="chart-svg" viewBox="0 0 800 ${height}"
         style="width:100%;height:${height}px;display:block;"></svg>
    <div class="chart-tooltip" id="${id}_tip"></div>
  </div>`;
}

// Pure content builder — returns the SVG inner string for a given
// pixel width.  No DOM side effects.  `bindHover` is a function to
// call after the content is in the DOM to wire up crosshair behaviour.
function _buildMiniLineChartContent(points, W, H, color, yFmt, id) {
  const PAD = { l: 64, r: 16, t: 12, b: 28 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const n = points.length;
  const xOf = i => PAD.l + (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));
  // Float y-axis to data range with 8% padding so short windows don't
  // squish to top of anchored-at-zero.  Keep zero visible when data
  // straddles zero (drawdown / cumulative-P&L charts use this helper).
  let dataMin = Infinity, dataMax = -Infinity;
  for (const p of points) {
    if (typeof p.value !== 'number' || isNaN(p.value)) continue;
    if (p.value > dataMax) dataMax = p.value;
    if (p.value < dataMin) dataMin = p.value;
  }
  if (!isFinite(dataMin) || !isFinite(dataMax)) { dataMin = 0; dataMax = 1; }
  const _r = dataMax - dataMin;
  const _pad = _r > 0 ? _r * 0.08 : Math.max(1, Math.abs(dataMax) * 0.08);
  let minY = dataMin - _pad;
  let maxY = dataMax + _pad;
  if (dataMin < 0 && dataMax > 0) {
    if (minY > 0) minY = 0;
    if (maxY < 0) maxY = 0;
  }
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
  for (let i = 0; i < xTicks; i++) {
    const idx = Math.round((i * (n - 1)) / (xTicks - 1 || 1));
    const x = xOf(idx);
    parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="middle">${(points[idx].date || '').slice(0, 7)}</text>`);
  }
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(p.value)}`).join(' ');
  parts.push(`<path class="series-line" d="${d}" stroke="${color}" stroke-width="1.8" fill="none"/>`);

  // Hover overlay (crosshair + dot + capture rect)
  parts.push(`<line class="hover-v" id="${id}_hv" x1="0" y1="${PAD.t}" x2="0" y2="${PAD.t + plotH}" style="display:none"/>`);
  parts.push(`<circle id="${id}_dot" cx="0" cy="0" r="0" fill="${color}" stroke="var(--bg)" stroke-width="1.5"/>`);
  parts.push(`<rect id="${id}_cap" x="${PAD.l}" y="${PAD.t}" width="${plotW}" height="${plotH}" fill="transparent"/>`);

  const content = parts.join('');
  const bindHover = () => {
    const svg = document.getElementById(id);
    if (!svg) return;
    const hv = document.getElementById(id + '_hv');
    const dot = document.getElementById(id + '_dot');
    const cap = document.getElementById(id + '_cap');
    const tip = document.getElementById(id + '_tip');
    if (!cap) return;
    cap.addEventListener('mousemove', ev => {
      const r = svg.getBoundingClientRect();
      const mx = (ev.clientX - r.left) * (W / r.width);
      let idx = Math.round(((mx - PAD.l) / plotW) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      const x = xOf(idx), y = yOf(points[idx].value);
      hv.setAttribute('x1', x); hv.setAttribute('x2', x);
      hv.style.display = '';
      dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', 4);
      if (tip) {
        tip.innerHTML =
          `<div class="tt-date">${points[idx].date || ''}</div>` +
          `<div class="tt-row"><span class="tt-name"><span class="tt-swatch" style="background:${color}"></span>Value</span>` +
          `<span>${fmtMoney(points[idx].value)}</span></div>`;
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
      dot.setAttribute('r', 0);
      if (tip) tip.style.display = 'none';
    });
  };

  return { content, bindHover };
}

registerTabRenderer('options', renderOptions);

// =========================================================================
// Retirement tab
// =========================================================================

// RETIREMENT_META is hoisted to the top of this file so the Overview
// tab's Year-by-Year breakdown (rendered immediately on load) can read
// it without a TDZ error.

// IRS annual contribution limits (most common variants only; the
// catch-up for 50+ isn't modeled).  Extend as years go on.
const IRA_LIMIT_BY_YEAR = {
  2018: 5500, 2019: 6000, 2020: 6000, 2021: 6000, 2022: 6000,
  2023: 6500, 2024: 7000, 2025: 7000, 2026: 7500,
};
// --- Federal tax reference tables ---
// Single source of truth is Python (src/analytics/tax.py), emitted into
// the JSON export under DATA.tax_tables.  We read from there so adding a
// new tax year is a one-file (Python) change.  `_TAX_TABLES` is always
// present in exports from current code (export.py always emits it, in the
// same run that generates this HTML).  `_loadBracketTable` converts the
// emitted `null` top-threshold sentinel back to Infinity.
const _TAX_TABLES = DATA.tax_tables || {};
function _loadBracketTable(emitted) {
  const out = {};
  for (const yr of Object.keys(emitted || {})) {
    out[yr] = {};
    for (const status of Object.keys(emitted[yr])) {
      out[yr][status] = emitted[yr][status].map(
        row => [row[0] === null ? Infinity : row[0], row[1]]);
    }
  }
  return out;
}
const K401_LIMIT_BY_YEAR = _TAX_TABLES.k401_limit || {};
// Roth IRA MAGI phase-out windows per filing status.  Above the
// start, the allowable contribution scales linearly to zero across
// the window.  We use AGI as a MAGI proxy — close enough for most
// users (true MAGI adds back student-loan interest deduction,
// traditional IRA deduction, and a few rarer items).
// MFS has an unusually narrow $0–$10k window.
const ROTH_MAGI_PHASEOUT_BY_STATUS = _TAX_TABLES.roth_magi_phaseout || {
  'Single': {
    start: {
      2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
      2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
      2026: 153000
    },
    width: 15000,
  },
  'Head of Household': {
    start: {
      2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
      2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
      2026: 153000
    },
    width: 15000,
  },
  'Married Filing Jointly': {
    start: {
      2018: 189000, 2019: 193000, 2020: 196000, 2021: 198000,
      2022: 204000, 2023: 218000, 2024: 230000, 2025: 236000,
      2026: 240000
    },
    width: 10000,
  },
  'Married Filing Separately': {
    start: {
      2018: 0, 2019: 0, 2020: 0, 2021: 0, 2022: 0, 2023: 0,
      2024: 0, 2025: 0, 2026: 0
    },
    width: 10000,
  },
};

function filingStatus() {
  return (RETIREMENT_META && RETIREMENT_META.filing_status) || 'Single';
}

// Returns { status, allowedPct, magi, phaseStart, phaseEnd } for a given
// year string, or null if we can't estimate (no salary/AGI data).
//   status: 'eligible' | 'partial' | 'ineligible' | 'no-data'
//   allowedPct: 0..100 (the share of the IRS limit you can contribute)
function rothEligibilityFor(yr) {
  const filing = filingStatus();
  const ps = ROTH_MAGI_PHASEOUT_BY_STATUS[filing]
    || ROTH_MAGI_PHASEOUT_BY_STATUS['Single'];
  const start = ps.start[yr];
  const width = ps.width;
  if (start == null) return null;
  const est = ((ANALYTICS.tax || {}).rate_estimates_by_year || {})[yr];
  if (!est || !est.agi) return null;
  const magi = est.agi;
  const isProjection = !!est.is_projection;
  const end = start + width;
  const base = { magi, phaseStart: start, phaseEnd: end, isProjection };
  if (magi < start) return { ...base, status: 'eligible', allowedPct: 100 };
  if (magi >= end) return { ...base, status: 'ineligible', allowedPct: 0 };
  return {
    ...base, status: 'partial',
    allowedPct: Math.round(((end - magi) / (end - start)) * 100)
  };
}

// Compact horizontal bar showing MAGI position vs Roth phase-out range.
// Reference width = phase-out end; yellow band marks the phase-out window;
// fill grows from left to MAGI position and is colored by status.
function rothMagiBarHtml(elig) {
  if (!elig) {
    return '<span style="color:var(--text-dim);font-size:0.78rem;">—</span>';
  }
  const refMax = elig.phaseEnd;
  const fillPct = Math.min(100, (elig.magi / refMax) * 100);
  const phaseStartPct = (elig.phaseStart / refMax) * 100;
  const fillColor = elig.status === 'eligible' ? 'var(--green)'
    : elig.status === 'partial' ? 'var(--yellow)'
      : 'var(--red)';
  const label = elig.status === 'eligible' ? 'Eligible'
    : elig.status === 'partial' ? `${elig.allowedPct}% allowed`
      : 'Ineligible';
  const labelColor = elig.status === 'eligible' ? 'var(--green)'
    : elig.status === 'partial' ? 'var(--yellow)'
      : 'var(--red)';
  const projNote = elig.isProjection ? ' (projected)' : '';
  const tip = `MAGI ≈ ${fmtMoney(elig.magi, 0)}${projNote} (AGI proxy)\nPhase-out: ${fmtMoney(elig.phaseStart, 0)} – ${fmtMoney(elig.phaseEnd, 0)} (single)\nAllowed: ${elig.allowedPct}% of IRS limit`;
  return `<div class="magi-cell" title="${_htmlEsc(tip)}">
    <div class="magi-bar">
      <div class="magi-band-phaseout" style="left:${phaseStartPct.toFixed(2)}%;"></div>
      <div class="magi-fill" style="width:${fillPct.toFixed(2)}%;background:${fillColor};"></div>
    </div>
    <div class="magi-label">
      <span style="color:${labelColor};">${label}${elig.isProjection ? ' *' : ''}</span>
      <span class="magi-amt">${fmtMoneyShort(elig.magi)}</span>
    </div>
  </div>`;
}
const RETIREMENT_GROUPS = { '401K': '401k', 'Roth IRA': 'ira_roth', 'Rollover IRA': 'ira_rollover' };
// Actions that count as contributions/deposits for retirement accounts.
const RETIREMENT_CONTRIB_ACTIONS = new Set(['Contribution', 'Deposit', 'Transfer In']);

function yearOf(iso) { return (iso || '').slice(0, 4); }

// Classify a txn as a retirement contribution or not, and if so, the
// year to attribute it to (USAA's "PRIOR YEAR CONTRIBUTION" rows belong
// to the preceding year — these can happen from Jan 1 through the tax
// deadline).  Returns { isContrib: bool, year: string }.
function retirementContribInfo(t) {
  const g = t.account_group;
  if (!(g in RETIREMENT_GROUPS)) return { isContrib: false };
  const amt = t.amount || 0;
  if (amt <= 0) return { isContrib: false };

  const desc = (t.description || '').toLowerCase();
  const isPriorYear = desc.includes('prior year contribution');
  const isCurrentYear = desc.includes('current year contribution');
  const y = yearOf(t.date);

  // Schwab / Vanguard style: explicit Contribution / Deposit actions
  if (RETIREMENT_CONTRIB_ACTIONS.has(t.action)) {
    // Skip Transfer In on Roth/Rollover (custodian moves like USAA→Schwab)
    if (t.action === 'Transfer In' && (g === 'Roth IRA' || g === 'Rollover IRA')) {
      return { isContrib: false };
    }
    return { isContrib: true, year: y };
  }

  // Voya employer/admin error reversal — counts as negative contribution
  // so the year's total correctly offsets the original.  Signed amount
  // is returned so callers can sum directly.
  if (t.action === 'Contribution Reversal') {
    return { isContrib: true, year: y, signedAmount: -amt };
  }

  // USAA style: Buy with contribution marker in the description
  if ((isPriorYear || isCurrentYear) && g === 'Roth IRA') {
    const attrYear = isPriorYear && y
      ? String(parseInt(y, 10) - 1)
      : y;
    return { isContrib: true, year: attrYear };
  }

  return { isContrib: false };
}

function computeRetirementContributionsByYear() {
  // Prefer the Python-computed version for a single source of truth.
  // Falls back to in-JS computation if analytics isn't available
  // (e.g. the dashboard was generated by an older pipeline).
  const pre = ANALYTICS.retirement_contributions_by_year;
  if (pre && typeof pre === 'object') return pre;

  const rows = {};
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib) continue;
    const y = info.year;
    if (!y) continue;
    const amt = t.amount || 0;
    if (!rows[y]) rows[y] = { '401K': 0, 'Roth IRA': 0, total: 0 };
    if (t.account_group === 'Roth IRA') rows[y]['Roth IRA'] += amt;
    else rows[y]['401K'] += amt;
    rows[y].total += amt;
  }
  return rows;
}

function computeRetirementSummary() {
  // Current balance + basis per account group, filtered to retirement.
  const byGroup = {};
  for (const h of holdingsByAccount) {
    if (!(h.account_group in RETIREMENT_GROUPS)) continue;
    if (!(h.account_group in byGroup)) byGroup[h.account_group] = { value: 0, basis: 0 };
    if (typeof h.value === 'number') byGroup[h.account_group].value += h.value;
    if (typeof h.cost_basis === 'number') byGroup[h.account_group].basis += h.cost_basis;
  }
  let value = 0, basis = 0;
  for (const g of Object.keys(byGroup)) { value += byGroup[g].value; basis += byGroup[g].basis; }
  return { byGroup, value, basis };
}

// Personal historical annualized return on retirement capital.
// Uses (current_value / total_contributions_ever) ^ (1/years) - 1.
// This is a simple approximation that treats all contributions as
// equivalent (no time-weighting); close enough for a projection display.
function computePersonalRetirementRate(summary, contribsByYear) {
  let total = 0;
  let firstYear = null;
  for (const y of Object.keys(contribsByYear)) {
    total += contribsByYear[y].total;
    if (firstYear === null || y < firstYear) firstYear = y;
  }
  if (total <= 0 || !firstYear) return null;
  const years = Math.max(1, (new Date() - new Date(firstYear + '-01-01')) / (365.25 * 86400000));
  if (years < 1) return null;
  const ratio = summary.value / total;
  if (ratio <= 0) return null;
  return Math.pow(ratio, 1 / years) - 1;
}

// Future value: FV = P*(1+r)^n + C*((1+r)^n - 1)/r
//   P = current principal, C = annual contribution, r = annual rate, n = years.
function projectFutureValue(principal, annualContrib, rate, years) {
  if (years <= 0) return principal;
  const g = Math.pow(1 + rate, years);
  if (Math.abs(rate) < 1e-9) return principal + annualContrib * years;
  return principal * g + annualContrib * ((g - 1) / rate);
}

// Default projection age comes from data/metadata.csv `Retirement Age`
// (default 67 when absent).  User can override per-render via the
// input on the Planning tab.
let retirementProjectionAge =
  (RETIREMENT_META && parseInt(RETIREMENT_META.retirement_age, 10)) || 67;
let retirementAnnualContrib = null;  // null = use auto-inferred

// --- Monte Carlo section (used by the Planning tab) ----------------------
let mcScenario = 'all_accounts';   // 'retirement' | 'all_accounts'

function _buildMonteCarloSection() {
  const mcRoot = ANALYTICS.monte_carlo;
  if (!mcRoot) return '';
  const mc = mcRoot[mcScenario] || mcRoot.all_accounts || mcRoot.retirement;
  if (!mc || !mc.bands || !mc.bands.length) return '';
  const bands = mc.bands;
  const s = mc.summary || {};
  const fire = mc.fire;
  const fiThreshold = mcRoot.fi_threshold;

  const startingTotal = (s.starting_equity || 0) + (s.starting_cash || 0);
  const cashStr = s.starting_cash > 0
    ? `<span style="color:var(--text-dim);font-size:0.72rem;"> · ${fmtMoneyShort(s.starting_cash)} cash @ ${((s.cash_yield || 0) * 100).toFixed(0)}%</span>`
    : '';
  const cards = [
    { label: 'Starting Balance', value: fmtMoneyShort(startingTotal), sub: cashStr },
    { label: 'Horizon', value: (s.years || 0) + 'y' },
    { label: 'P10 (Pessimistic)', value: fmtMoneyShort(s.p10_final || 0), cls: 'negative' },
    { label: 'P50 (Median)', value: fmtMoneyShort(s.p50_final || 0) },
    { label: 'P90 (Optimistic)', value: fmtMoneyShort(s.p90_final || 0), cls: 'positive' },
  ];
  const statsHtml = cards.map(c => `<div class="ds-card">
    <div class="ds-label">${c.label}</div>
    <div class="ds-value ${c.cls || ''}">${c.value}${c.sub || ''}</div>
  </div>`).join('');

  // Scenario toggle pills
  const togglePills = `
    <div class="toggle-group">
      <button class="tbtn ${mcScenario === 'all_accounts' ? 'active' : ''}"
              onclick="setMonteCarloScenario('all_accounts')">All Accounts</button>
      <button class="tbtn ${mcScenario === 'retirement' ? 'active' : ''}"
              onclick="setMonteCarloScenario('retirement')">Retirement Only</button>
    </div>`;

  const svgId = 'mcFanSvg';
  const fireNote = fire && mcScenario === 'all_accounts'
    ? ` Dashed orange line = FI threshold (4% rule × annual expenses).`
    : '';
  const chartHtml = `<div class="mc-fan-wrap">
    <svg class="mc-fan" id="${svgId}" preserveAspectRatio="none"></svg>
    <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;">
      Shaded bands: 10th–90th percentile (outer) and 25th–75th (inner).  Solid line = median.
      Assumes ${((s.mean_return || 0) * 100).toFixed(0)}% mean / ${((s.stdev_return || 0) * 100).toFixed(0)}% stdev normal returns on the equity bucket, $${(s.annual_contribution || 0).toLocaleString()}/yr contributions.${fireNote}
    </div>
  </div>`;

  queueMicrotask(() => {
    const svg = document.getElementById(svgId);
    if (!svg || !bands.length) return;
    const W = Math.round(svg.getBoundingClientRect().width) || 800;
    const H = 280, PAD = { l: 64, r: 16, t: 10, b: 28 };
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    let maxY = Math.max(...bands.map(b => b.p90)) * 1.05;
    if (fiThreshold && mcScenario === 'all_accounts') {
      maxY = Math.max(maxY, fiThreshold * 1.1);
    }
    const minY = 0;
    const xOf = i => PAD.l + (bands.length === 1 ? plotW / 2 : (i * plotW) / (bands.length - 1));
    const yOf = v => PAD.t + plotH - ((v - minY) / (maxY - minY || 1)) * plotH;
    const parts = [];
    for (let k = 0; k <= 5; k++) {
      const v = minY + ((maxY - minY) * k) / 5;
      const y = yOf(v);
      parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
      parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${fmtMoneyShort(v)}</text>`);
    }
    const outerUp = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p90)}`).join(' ');
    const outerDn = [...bands].reverse().map((b, i) => {
      const origIdx = bands.length - 1 - i;
      return `L${xOf(origIdx)},${yOf(b.p10)}`;
    }).join(' ');
    parts.push(`<path d="${outerUp} ${outerDn} Z" class="mc-band-90"/>`);
    const innerUp = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p75)}`).join(' ');
    const innerDn = [...bands].reverse().map((b, i) => {
      const origIdx = bands.length - 1 - i;
      return `L${xOf(origIdx)},${yOf(b.p25)}`;
    }).join(' ');
    parts.push(`<path d="${innerUp} ${innerDn} Z" class="mc-band-50"/>`);
    const median = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p50)}`).join(' ');
    parts.push(`<path d="${median}" class="mc-median"/>`);
    // FIRE threshold horizontal line (only in all-accounts mode)
    if (fiThreshold && mcScenario === 'all_accounts') {
      const fy = yOf(fiThreshold);
      parts.push(`<line x1="${PAD.l}" y1="${fy}" x2="${W - PAD.r}" y2="${fy}" stroke="#fb923c" stroke-width="1.5" stroke-dasharray="6 4"/>`);
      parts.push(`<text x="${W - PAD.r - 4}" y="${fy - 4}" text-anchor="end" fill="#fb923c" style="font-size:10px;">FI ${fmtMoneyShort(fiThreshold)}</text>`);
    }
    const xTicks = Math.min(6, bands.length);
    for (let i = 0; i < xTicks; i++) {
      const idx = Math.round((i * (bands.length - 1)) / (xTicks - 1 || 1));
      const x = xOf(idx);
      parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="middle">${bands[idx].calendar_year}</text>`);
    }
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join('');
  });

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Monte Carlo Projection</span></h2>
      ${togglePills}
      <span class="as-of-hint" style="margin-left:auto;">${mc.runs} stochastic runs — accounts for sequence-of-returns risk.</span>
    </div>
    <div class="mc-stats">${statsHtml}</div>
    ${chartHtml}
    ${_buildFireSection(mcRoot, mc)}
  `;
}

// --- FIRE (4% rule) section --------------------------------------------------
function _buildFireSection(mcRoot, mc) {
  const fire = mc.fire;
  const fi = mcRoot.fi_threshold;
  const annExp = mcRoot.annual_expenses;
  if (!fi || !annExp) {
    return `
      <div class="section-header" style="margin-top:24px;">
        <h2><span style="color:var(--accent);">FIRE (4% rule)</span></h2>
      </div>
      <div class="empty-state">
        Add an <code>Annual Expenses</code> row to <code>data/metadata.csv</code> to enable FIRE projections.  The classic 4% rule sets your FI number at 25× annual expenses.
      </div>`;
  }

  const startingTotal = (mc.summary.starting_equity || 0) + (mc.summary.starting_cash || 0);
  const fiProgress = fi > 0 ? Math.min(100, (startingTotal / fi) * 100) : 0;
  // Coast FI: balance today that, with no further contributions, grows
  // to FI by the end of the projection window at the mean return.
  // Solve: bal × (1 + r)^n = fi  →  coast = fi / (1 + r)^n
  const r = mc.summary.mean_return || 0.08;
  const n = mc.summary.years || 0;
  const coastFi = n > 0 ? fi / Math.pow(1 + r, n) : fi;
  const coastReached = startingTotal >= coastFi;

  // FIRE date estimation per percentile band
  const yearLabel = (yr) => yr == null ? 'never (within window)' : `${yr}y (age ${(yr + currentAgeFromMeta())})`;
  const reachPct = fire ? (fire.p_reaches_within_window * 100).toFixed(1) : null;

  const cards = [
    { label: 'Annual Expenses', value: fmtMoneyShort(annExp) },
    { label: 'FI Number (25×)', value: fmtMoneyShort(fi) },
    {
      label: 'FI Progress', value: fiProgress.toFixed(1) + '%',
      cls: fiProgress >= 100 ? 'positive' : ''
    },
    {
      label: 'Coast FI', value: fmtMoneyShort(coastFi),
      sub: coastReached
        ? '<span style="color:var(--green);font-size:0.72rem;"> ✓ already past it</span>'
        : `<span style="color:var(--text-dim);font-size:0.72rem;"> ${fmtMoneyShort(coastFi - startingTotal)} more</span>`
    },
  ];
  if (fire) {
    cards.push({
      label: 'P(reach FI in window)',
      value: reachPct + '%',
      cls: fire.p_reaches_within_window > 0.75 ? 'positive'
        : fire.p_reaches_within_window < 0.25 ? 'negative' : ''
    });
  }
  const statsHtml = cards.map(c => `<div class="ds-card">
    <div class="ds-label">${c.label}</div>
    <div class="ds-value ${c.cls || ''}">${c.value}${c.sub || ''}</div>
  </div>`).join('');

  // FIRE date table per percentile
  const ageRows = fire ? [
    ['P10 (Pessimistic)', fire.p10_first_year, 'negative'],
    ['P25', fire.p25_first_year, ''],
    ['P50 (Median)', fire.p50_first_year, ''],
    ['P75', fire.p75_first_year, ''],
    ['P90 (Optimistic)', fire.p90_first_year, 'positive'],
  ].map(([lbl, yr, cls]) => `<tr>
    <td>${lbl}</td>
    <td class="num"><span class="${cls}">${yearLabel(yr)}</span></td>
  </tr>`).join('') : '';

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">FIRE (4% rule)</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">25× annual expenses; "Coast FI" assumes no more contributions.</span>
    </div>
    <div class="mc-stats" style="grid-template-columns:repeat(${cards.length},1fr);">${statsHtml}</div>
    ${fire ? `<div class="panel" style="margin-top:14px;">
      <h3>Year first reaching FI (per percentile)</h3>
      <table class="mini-table">
        <thead><tr><th>Outcome</th><th class="num">Year offset (age at crossing)</th></tr></thead>
        <tbody>${ageRows}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Each row: the first year of the simulation in which that percentile band crosses your FI number.  All-accounts scenario only — the FI number is funded from total resources, not just retirement accounts.  Switch the toggle above to see retirement-only growth without the FIRE overlay.
      </div>
    </div>` : ''}
  `;
}

// Helper: current age from RETIREMENT_META.birthday — used to render
// "age at crossing" in the FIRE table.  Returns 0 if no birthday.
function currentAgeFromMeta() {
  const bd = RETIREMENT_META && RETIREMENT_META.birthday;
  if (!bd) return 0;
  const d = new Date(bd);
  return Math.floor((new Date() - d) / (365.25 * 86400000));
}

function renderRetirement() {
  const root = document.getElementById('retirementContent');
  if (!root) return;

  const summary = computeRetirementSummary();
  const contribs = computeRetirementContributionsByYear();
  const personal = computePersonalRetirementRate(summary, contribs);

  // Auto-infer annual contribution from the last 12 months of retirement contribs.
  const today = new Date();
  const yrAgo = new Date(today); yrAgo.setFullYear(yrAgo.getFullYear() - 1);
  let last12 = 0;
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib) continue;
    if (!t.date) continue;
    if (new Date(t.date) >= yrAgo) last12 += (t.amount || 0);
  }
  const autoAnnualContrib = Math.round(last12);
  const annualContribUsed = retirementAnnualContrib != null ? retirementAnnualContrib : autoAnnualContrib;

  // Age math
  const birthday = RETIREMENT_META.birthday ? new Date(RETIREMENT_META.birthday) : null;
  let currentAge = null;
  let yearsToRetire = null;
  if (birthday) {
    const diffMs = today - birthday;
    currentAge = Math.floor(diffMs / (365.25 * 86400000));
    yearsToRetire = Math.max(0, retirementProjectionAge - currentAge);
  }

  // Stat cards
  const ytdKey = String(today.getFullYear());
  const ytd = (contribs[ytdKey] && contribs[ytdKey].total) || 0;
  const totalContribs = Object.values(contribs).reduce((s, r) => s + r.total, 0);
  const totalGain = summary.value - summary.basis;

  const statCards = [
    { label: 'Total Retirement', value: fmtMoney(summary.value), cls: 'positive' },
    { label: 'Cost Basis', value: fmtMoney(summary.basis) },
    { label: 'Unrealized Gain', value: fmtSigned(totalGain), cls: totalGain >= 0 ? 'positive' : 'negative' },
    { label: 'Contributions ' + ytdKey, value: fmtMoney(ytd) },
    { label: 'Contributions (all-time)', value: fmtMoney(totalContribs) },
    { label: 'Personal Rate (est)', value: personal != null ? (personal * 100).toFixed(2) + '%' : '—' },
    { label: 'Current Age', value: currentAge != null ? String(currentAge) : '—' },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Contributions by year table (401K column includes former Rollover IRA).
  // Hover any contribution amount to see that year's IRS limit.
  const years = Object.keys(contribs).sort();
  const contribRows = years.map(y => {
    const r = contribs[y];
    const limit401 = K401_LIMIT_BY_YEAR[y];
    const limitIra = IRA_LIMIT_BY_YEAR[y];
    const tip401 = limit401 ? `IRS limit: ${fmtMoney(limit401, 0)} (${Math.round(r['401K'] / limit401 * 100)}% used)` : '';
    const tipIra = limitIra ? `IRS limit: ${fmtMoney(limitIra, 0)} (${Math.round(r['Roth IRA'] / limitIra * 100)}% used)` : '';
    const eligCell = `<td>${rothMagiBarHtml(rothEligibilityFor(y))}</td>`;
    return `<tr>
      <td><b>${y}</b></td>
      <td class="num" title="${_htmlEsc(tip401)}">${fmtMoney(r['401K'])}</td>
      <td class="num" title="${_htmlEsc(tipIra)}">${fmtMoney(r['Roth IRA'])}</td>
      ${eligCell}
      <td class="num"><b>${fmtMoney(r.total)}</b></td>
    </tr>`;
  }).join('');

  // Retirement balance over time (chart): use history snapshots' by_account_type.Retirement
  const retPoints = history
    .map(h => ({ date: h.date, value: (h.by_account_type && h.by_account_type.Retirement) || 0 }))
    .filter(p => p.value > 0);
  const retBasisPoints = history
    .map(h => ({ date: h.date, value: (h.cost_basis_by_type && h.cost_basis_by_type.Retirement) || 0 }))
    .filter(p => p.value > 0);

  const chartHtml = renderMiniLineChart(retPoints, {
    id: 'retBalanceSvg',
    color: '#a78bfa',
    height: 260,
    yFormatter: fmtMoneyShort,
    emptyMsg: 'No retirement history yet.',
  });

  // Projection scenarios
  const scenarios = [
    { name: 'Conservative', rate: 0.05 },
    { name: 'Moderate', rate: 0.07 },
    { name: 'Optimistic', rate: 0.09 },
  ];
  if (personal != null) scenarios.push({ name: 'Personal', rate: personal });

  const projRows = yearsToRetire != null ? scenarios.map(s => {
    const fv = projectFutureValue(summary.value, annualContribUsed, s.rate, yearsToRetire);
    const gain = fv - summary.value - annualContribUsed * yearsToRetire;
    return `<tr>
      <td>${s.name}</td>
      <td class="num">${(s.rate * 100).toFixed(2)}%</td>
      <td class="num"><b>${fmtMoneyShort(fv)}</b></td>
      <td class="num"><span class="positive">${fmtSigned(gain)}</span></td>
    </tr>`;
  }).join('') : `<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">Add a birthday to <code>data/metadata.csv</code> to enable projections.</td></tr>`;

  // Roth vs Traditional split — small inline stacked bar (replaces a
  // 2-slice pie which was visually weak for so few categories).
  let roth = 0, trad = 0;
  for (const h of holdingsByAccount) {
    if (typeof h.value !== 'number') continue;
    if (h.account_group === 'Roth IRA') roth += h.value;
    else if (h.account_group === '401K' || h.account_group === 'Rollover IRA') trad += h.value;
  }
  const splitTotal = roth + trad;
  const splitHtml = splitTotal > 0
    ? renderRothTradBar(roth, trad)
    : '<div style="color:var(--text-dim);padding:8px 0;">No retirement positions.</div>';

  // --- Render ---
  root.innerHTML = `
    ${statsHtml}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Contributions by Year</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Year</th>
          <th class="num">401K</th>
          <th class="num">Roth IRA</th>
          <th>Roth Eligibility</th>
          <th class="num">Total</th>
        </tr></thead>
        <tbody>${contribRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">No retirement contributions found.</td></tr>'}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Hover any contribution amount for the year's IRS limit.  Roth Eligibility bar: green fill = your MAGI; yellow band = the ${_htmlEsc(filingStatus())} phase-out range from <code>data/metadata.csv</code>.  MAGI estimated from salary + bonuses + portfolio income − 401K.  An asterisk means a year-end projection.
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Retirement Balance Over Time</span></h2></div>
    ${chartHtml}

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Roth vs Traditional</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">For per-account-group breakdown, see <a onclick="setHoldingsView('account');activateTab('holdings')">Holdings → By Account</a>.</span>
    </div>
    <div class="panel">${splitHtml}</div>

    <div style="color:var(--text-dim);font-size:0.78rem;margin-top:24px;text-align:center;line-height:1.5;">
      Looking for projections, Monte Carlo, or FIRE?  Those moved to the
      <a onclick="activateTab('planning')" style="color:var(--accent);cursor:pointer;">Planning</a> tab.
    </div>
  `;
}

// Inline horizontal stacked bar for the Roth/Trad split.  Cleaner than
// a 2-slice pie at a fraction of the pixels.
function renderRothTradBar(roth, trad) {
  const total = roth + trad;
  const rothPct = (roth / total) * 100;
  const tradPct = 100 - rothPct;
  return `
    <div style="display:flex;height:28px;border-radius:4px;overflow:hidden;border:1px solid var(--border);">
      <div style="width:${rothPct}%;background:${ACCOUNT_COLORS['Roth IRA'] || '#a78bfa'};"
           title="Roth: ${fmtMoney(roth)} (${rothPct.toFixed(1)}%)"></div>
      <div style="width:${tradPct}%;background:${ACCOUNT_COLORS['401K'] || '#f59e0b'};"
           title="Traditional: ${fmtMoney(trad)} (${tradPct.toFixed(1)}%)"></div>
    </div>
    <div style="display:flex;gap:24px;font-size:0.85rem;margin-top:8px;flex-wrap:wrap;">
      <span><span style="display:inline-block;width:10px;height:10px;background:${ACCOUNT_COLORS['Roth IRA']};margin-right:6px;border-radius:2px;vertical-align:middle;"></span>Roth: <b>${fmtMoney(roth)}</b> <span style="color:var(--text-dim);">(${rothPct.toFixed(1)}%)</span></span>
      <span><span style="display:inline-block;width:10px;height:10px;background:${ACCOUNT_COLORS['401K']};margin-right:6px;border-radius:2px;vertical-align:middle;"></span>Traditional: <b>${fmtMoney(trad)}</b> <span style="color:var(--text-dim);">(${tradPct.toFixed(1)}%)</span></span>
      <span style="color:var(--text-dim);">Total: ${fmtMoney(total)}</span>
    </div>
  `;
}

registerTabRenderer('retirement', renderRetirement);

// =========================================================================
// Planning tab — forward-looking: scenario projection, Monte Carlo, FIRE.
// All retirement-tab content that's about the future moved here so the
// Retirement tab stays focused on account-specific facts and history.
// =========================================================================
function renderPlanning() {
  const root = document.getElementById('planningContent');
  if (!root) return;

  const summary = computeRetirementSummary();
  const contribs = computeRetirementContributionsByYear();
  const personal = computePersonalRetirementRate(summary, contribs);

  // Trailing-12-months retirement contribution (auto-inferred default for
  // the scenario projection's annual-contribution input)
  const today = new Date();
  const yrAgo = new Date(today); yrAgo.setFullYear(yrAgo.getFullYear() - 1);
  let last12 = 0;
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib) continue;
    if (!t.date) continue;
    if (new Date(t.date) >= yrAgo) last12 += (t.amount || 0);
  }
  const autoAnnualContrib = Math.round(last12);
  const annualContribUsed = retirementAnnualContrib != null ? retirementAnnualContrib : autoAnnualContrib;

  // Years to projection age (default = metadata Retirement Age / 67 —
  // see retirementProjectionAge initializer)
  const birthday = RETIREMENT_META.birthday ? new Date(RETIREMENT_META.birthday) : null;
  let yearsToRetire = null;
  let currentAge = null;
  if (birthday) {
    currentAge = Math.floor((today - birthday) / (365.25 * 86400000));
    yearsToRetire = Math.max(0, retirementProjectionAge - currentAge);
  }

  const scenarios = [
    { name: 'Conservative', rate: 0.05 },
    { name: 'Moderate', rate: 0.07 },
    { name: 'Optimistic', rate: 0.09 },
  ];
  if (personal != null) scenarios.push({ name: 'Personal', rate: personal });

  const projRows = yearsToRetire != null ? scenarios.map(s => {
    const fv = projectFutureValue(summary.value, annualContribUsed, s.rate, yearsToRetire);
    const gain = fv - summary.value - annualContribUsed * yearsToRetire;
    return `<tr>
      <td>${s.name}</td>
      <td class="num">${(s.rate * 100).toFixed(2)}%</td>
      <td class="num"><b>${fmtMoneyShort(fv)}</b></td>
      <td class="num"><span class="positive">${fmtSigned(gain)}</span></td>
    </tr>`;
  }).join('') : `<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">Add a birthday to <code>data/metadata.csv</code> to enable projections.</td></tr>`;

  root.innerHTML = `
    <div class="section-header"><h2><span style="color:var(--accent);">Scenario Projection</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Constant-rate compound growth — simple but ignores volatility.  See Monte Carlo below for sequence-of-returns risk.</span>
    </div>
    <div class="panel">
      <div class="controls" style="margin-bottom:12px;">
        <label style="color:var(--text-dim);font-size:0.85rem;">Retirement age:
          <input type="number" id="retProjectionAge" value="${retirementProjectionAge}" min="40" max="90"
                 style="width:60px;" oninput="setRetirementProjectionAge(this.value)"/>
        </label>
        <label style="color:var(--text-dim);font-size:0.85rem;margin-left:16px;">Annual contribution:
          $<input type="number" id="retAnnualContrib" value="${annualContribUsed}" step="500"
                  style="width:100px;" oninput="setRetirementAnnualContrib(this.value)"/>
          <span style="color:var(--text-dim);font-size:0.72rem;"> (auto: ${fmtMoney(autoAnnualContrib)} — trailing 12 months)</span>
        </label>
      </div>
      <table class="mini-table">
        <thead><tr>
          <th>Scenario</th><th class="num">Annual Rate</th>
          <th class="num">Value at age ${retirementProjectionAge}${yearsToRetire != null ? ` (${yearsToRetire}y)` : ''}</th>
          <th class="num">Investment Gain</th>
        </tr></thead>
        <tbody>${projRows}</tbody>
      </table>
    </div>

    ${_buildMonteCarloSection()}
  `;
}

// Re-render Planning when the projection-age / contribution inputs change
// (they were originally on Retirement, but they live on Planning now).
function setRetirementProjectionAge(v) {
  const n = parseInt(v, 10);
  if (!isNaN(n) && n > 20 && n < 100) {
    retirementProjectionAge = n;
    if (typeof renderPlanning === 'function') renderPlanning();
  }
}
function setRetirementAnnualContrib(v) {
  const n = parseFloat(v);
  if (!isNaN(n) && n >= 0) {
    retirementAnnualContrib = n;
    if (typeof renderPlanning === 'function') renderPlanning();
  }
}
// MC scenario toggle also lives on Planning now
function setMonteCarloScenario(s) {
  mcScenario = s;
  if (typeof renderPlanning === 'function') renderPlanning();
}

registerTabRenderer('planning', renderPlanning);

// =========================================================================
// Income tab — dividends, interest, staking rewards, lending rebates
// =========================================================================

// Derived from the action catalog's `income` field (single source of
// truth — src/actions.py), mapping action name → income bucket.
const INCOME_ACTIONS = Object.fromEntries(
  _ACTION_CATALOG.filter(a => a.income).map(a => [a.name, a.income]));

// Build the 12-month total cash-flow forecast (Income tab).  Combines:
//   - Passive investment income (already projected by analytics.income_calendar)
//   - W-2 salary at current rate
//   - Bonus history (uses last full year as a planning estimate)
//   - Projected retirement contributions (last 12 months' rate)
// Returns HTML for an `<div class="income-forecast">` block.
function _buildCashFlowForecast(passiveProjected) {
  // Current salary = most recent salary-history entry on or before today
  const today = new Date();
  const salaries = (RETIREMENT_META.salary_history || []).filter(s => s.date && s.date <= today.toISOString().slice(0, 10));
  salaries.sort((a, b) => a.date.localeCompare(b.date));
  const currentSalary = salaries.length ? salaries[salaries.length - 1].amount : 0;

  // Bonus estimate = last full calendar year's bonus total (most stable
  // proxy; this year's bonuses may be partially booked already).
  const lastFullYear = today.getFullYear() - 1;
  const lastYearBonuses = (RETIREMENT_META.bonus_history || [])
    .filter(b => yearOf(b.date) === String(lastFullYear))
    .reduce((s, b) => s + (b.amount || 0), 0);

  // Trailing-12-months retirement contribution rate (used as projection)
  const yrAgo = new Date(today); yrAgo.setFullYear(yrAgo.getFullYear() - 1);
  let last12Contribs = 0;
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib || !t.date) continue;
    if (new Date(t.date) >= yrAgo) last12Contribs += (t.amount || 0);
  }
  const projContribs = Math.round(last12Contribs);

  const passive = Math.round(passiveProjected || 0);
  const grossIn = currentSalary + lastYearBonuses + passive;
  const totalNet = grossIn - projContribs;   // money that flows OUT of payroll into retirement reduces "available" cash

  if (currentSalary === 0 && passive === 0 && projContribs === 0) return '';

  const rows = [
    ['Salary (current rate × 12mo)', currentSalary, 'positive'],
    ['Bonuses (last full-year actual)', lastYearBonuses, 'positive'],
    ['Passive investment income (proj.)', passive, 'positive'],
    ['Retirement contributions (proj.)', -projContribs, 'negative'],
  ].map(([label, amt, cls]) => `<tr>
    <td>${label}</td>
    <td class="num"><span class="${cls}">${fmtSigned(amt)}</span></td>
  </tr>`).join('');

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">12-Month Cash-Flow Forecast</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Net income hitting your accounts over the next 12 months at current rates.</span>
    </div>
    <div class="income-forecast">
      <div class="if-totals">
        <div class="item"><span class="label">Gross inflows (proj.)</span><span class="value">${fmtMoney(grossIn)}</span></div>
        <div class="item"><span class="label">Net of retirement (proj.)</span><span class="value">${fmtMoney(totalNet)}</span></div>
      </div>
      <table class="mini-table">
        <thead><tr><th>Source</th><th class="num">Projected (next 12mo)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Salary: most recent rate × 12.  Bonuses: last full year's total (current-year bonuses are lumpy, so the prior year is a better planning estimate).  Retirement contributions: trailing-12-months pace.  This is pre-tax — federal/state income tax + FICA reduce the net further.
      </div>
    </div>`;
}

function renderIncome() {
  const root = document.getElementById('incomeContent');
  if (!root) return;

  // Straight from the precomputed analytics (compute_income_analytics).
  // Empty arrays render the empty-state cleanly — no JS recompute needed.
  const inc = ANALYTICS.income || {};
  const byYear = {};
  const byMonth = {};
  const bySource = {};
  const total = inc.total || 0;
  for (const r of (inc.by_year || [])) byYear[r.year] = r;
  for (const p of (inc.by_month || [])) {
    const key = (p.date || '').slice(0, 7);
    if (key) byMonth[key] = p.value;
  }
  for (const s of (inc.by_source || [])) bySource[s.source] = s;

  // Stat cards
  const ytdKey = String(new Date().getFullYear());
  const ytd = (byYear[ytdKey] && byYear[ytdKey].total) || 0;
  const allTimeDiv = Object.values(byYear).reduce((s, r) => s + r.dividends, 0);
  const allTimeInt = Object.values(byYear).reduce((s, r) => s + r.interest, 0);
  const allTimeRew = Object.values(byYear).reduce((s, r) => s + r.rewards, 0);
  const allTimeLend = Object.values(byYear).reduce((s, r) => s + r.lending, 0);
  const statCards = [
    { label: 'Total Income (all-time)', value: fmtMoney(total), cls: 'positive' },
    { label: 'Income ' + ytdKey, value: fmtMoney(ytd) },
    { label: 'Dividends (all-time)', value: fmtMoney(allTimeDiv) },
    { label: 'Interest (all-time)', value: fmtMoney(allTimeInt) },
    { label: 'Rewards (all-time)', value: fmtMoney(allTimeRew) },
    { label: 'Lending (all-time)', value: fmtMoney(allTimeLend) },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Annual summary table
  const years = Object.keys(byYear).sort();
  const yearRows = years.map(y => {
    const r = byYear[y];
    return `<tr>
      <td><b>${y}</b></td>
      <td class="num">${fmtMoney(r.dividends)}</td>
      <td class="num">${fmtMoney(r.interest)}</td>
      <td class="num">${fmtMoney(r.rewards)}</td>
      <td class="num">${fmtMoney(r.lending)}</td>
      <td class="num"><b>${fmtMoney(r.total)}</b></td>
    </tr>`;
  }).join('');

  // Monthly line chart (compact cumulative-by-month view)
  const monthKeys = Object.keys(byMonth).sort();
  const monthPoints = monthKeys.map(m => ({ date: m + '-01', value: byMonth[m] }));
  const monthChart = renderMiniLineChart(monthPoints, {
    id: 'incomeMonthSvg',
    color: '#34d399',
    height: 220,
    yFormatter: fmtMoneyShort,
    emptyMsg: 'No income events recorded.',
  });

  // By source table (top 20 by total)
  const sourceRows = Object.entries(bySource)
    .sort(([, a], [, b]) => b.total - a.total)
    .slice(0, 30)
    .map(([src, s]) => {
      const acctColor = ACCOUNT_COLORS[s.account] || '';
      const acctSpan = s.account && acctColor ? `<span style="color:${acctColor}">${s.account}</span>` : (s.account || '');
      return `<tr>
        <td><b>${src}</b></td>
        <td>${acctSpan}</td>
        <td class="num">${fmtMoney(s.dividends)}</td>
        <td class="num">${fmtMoney(s.interest)}</td>
        <td class="num">${fmtMoney(s.rewards)}</td>
        <td class="num">${fmtMoney(s.lending)}</td>
        <td class="num"><b>${fmtMoney(s.total)}</b></td>
      </tr>`;
    }).join('');

  // 12-month forward forecast (from analytics.income_calendar) — passive
  // investment income only (dividends, interest, lending rebates, rewards).
  const cal = ANALYTICS.income_calendar || {};
  const forecast = cal.forecast_12mo || [];
  const fcTotal = cal.forecast_total || 0;
  const ttm = cal.ttm_actual || 0;
  const forecastHtml = forecast.length ? `
    <div class="income-forecast">
      <div class="if-totals">
        <div class="item"><span class="label">Trailing 12mo (actual)</span><span class="value">${fmtMoney(ttm)}</span></div>
        <div class="item"><span class="label">Next 12mo (projected)</span><span class="value">${fmtMoney(fcTotal)}</span></div>
      </div>
      <table class="mini-table">
        <thead><tr>
          <th>Symbol</th>
          <th class="num">Last 12mo</th>
          <th class="num">Projected Next 12mo</th>
          <th class="num" title="Yield-on-cost: TTM income ÷ cost basis. Tells you the return your contributions earn — rises over time as the position grows the dividend.">YoC</th>
          <th class="num" title="Current yield: TTM income ÷ current value. What new money invested today would earn at recent income levels.">Cur Yield</th>
        </tr></thead>
        <tbody>${forecast.slice(0, 20).map(f => {
    const yoc = f.yield_on_cost != null ? `${f.yield_on_cost.toFixed(2)}%` : '—';
    const cury = f.current_yield != null ? `${f.current_yield.toFixed(2)}%` : '—';
    return `<tr>
            <td><b>${symLabel(f.symbol)}</b></td>
            <td class="num">${fmtMoney(f.last_12mo_income)}</td>
            <td class="num">${fmtMoney(f.projected_annual)}</td>
            <td class="num">${yoc}</td>
            <td class="num">${cury}</td>
          </tr>`;
  }).join('')}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Projection = flat extrapolation of the last 12 months' dividends/interest per currently-held position.  Doesn't account for dividend cuts, rate changes, or share-count changes since.
        <b>YoC</b> uses current cost basis (rises as the company hikes the dividend); <b>Current yield</b> uses current market value (falls when the stock appreciates faster than the payout).
      </div>
    </div>` : '';

  // 12-month total cash-flow forecast — combines passive income + W-2
  // pay (salary, bonuses) + planned retirement contributions, all
  // projected forward from current rates.  Gives a "what's hitting my
  // accounts in the next year" view that complements FIRE planning.
  const flowHtml = _buildCashFlowForecast(fcTotal);

  root.innerHTML = `
    ${statsHtml}
    ${flowHtml}
    ${forecastHtml ? `<div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Dividend / Interest Forecast (12mo)</span></h2></div>${forecastHtml}` : ''}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Annual Summary</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Year</th>
          <th class="num">Dividends</th>
          <th class="num">Interest</th>
          <th class="num">Rewards</th>
          <th class="num">Lending</th>
          <th class="num">Total</th>
        </tr></thead>
        <tbody>${yearRows || '<tr><td colspan="6" style="color:var(--text-dim);padding:12px;">No income events.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Monthly Income</span></h2></div>
    ${monthChart}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">By Source</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Source</th>
          <th>Account</th>
          <th class="num">Dividends</th>
          <th class="num">Interest</th>
          <th class="num">Rewards</th>
          <th class="num">Lending</th>
          <th class="num">Total</th>
        </tr></thead>
        <tbody>${sourceRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

registerTabRenderer('income', renderIncome);

// =========================================================================
// Tax tab — realized gains (ST/LT), Section 1256, harvest candidates, wash sales
// =========================================================================

// Section 1256 contracts: broad-based cash-settled index options get
// 60% long-term / 40% short-term tax treatment regardless of holding
// period.  This covers the major cash-settled indices; ETF options
// (SPY, QQQ, etc.) are NOT Section 1256.
//
// Single source of truth lives in src/analytics/tax.py — the analytics
// payload carries the list under `analytics.tax.section_1256_underlyings`
// and we mirror it into a Set here.  The fallback list keeps older
// exports rendering correctly when the analytics payload is missing.
const SECTION_1256_UNDERLYINGS = new Set(
  ((ANALYTICS.tax || {}).section_1256_underlyings) || [
    'SPX', 'SPXW', 'NDX', 'NDXP', 'XSP', 'RUT', 'RUTW', 'DJX', 'VIX',
  ]
);

function isSection1256Symbol(sym) {
  const parsed = parseOptionSymbol(sym);
  return !!(parsed && SECTION_1256_UNDERLYINGS.has(parsed.underlying));
}

// Federal ordinary-income brackets, LTCG brackets, and standard
// deduction keyed by year → filing status.  SINGLE SOURCE OF TRUTH is
// Python (src/analytics/tax.py), emitted into DATA.tax_tables; the
// literals below are an emergency fallback only (kept for parity with
// the §1256 pattern) and are NOT the place to add a new tax year — do
// that in analytics/tax.py.  The tax tab applies these to estimate a
// marginal rate from W-2 income + 401K + portfolio income.
const FEDERAL_BRACKETS = _TAX_TABLES.federal_brackets
  ? _loadBracketTable(_TAX_TABLES.federal_brackets)
  : {
  2024: {
    'Single': [[11600, 0.10], [47150, 0.12], [100525, 0.22], [191950, 0.24], [243725, 0.32], [609350, 0.35], [Infinity, 0.37]],
    'Married Filing Jointly': [[23200, 0.10], [94300, 0.12], [201050, 0.22], [383900, 0.24], [487450, 0.32], [731200, 0.35], [Infinity, 0.37]],
    'Married Filing Separately': [[11600, 0.10], [47150, 0.12], [100525, 0.22], [191950, 0.24], [243725, 0.32], [365600, 0.35], [Infinity, 0.37]],
    'Head of Household': [[16550, 0.10], [63100, 0.12], [100500, 0.22], [191950, 0.24], [243700, 0.32], [609350, 0.35], [Infinity, 0.37]],
  },
  2025: {
    'Single': [[11925, 0.10], [48475, 0.12], [103350, 0.22], [197300, 0.24], [250525, 0.32], [626350, 0.35], [Infinity, 0.37]],
    'Married Filing Jointly': [[23850, 0.10], [96950, 0.12], [206700, 0.22], [394600, 0.24], [501050, 0.32], [751600, 0.35], [Infinity, 0.37]],
    'Married Filing Separately': [[11925, 0.10], [48475, 0.12], [103350, 0.22], [197300, 0.24], [250525, 0.32], [375800, 0.35], [Infinity, 0.37]],
    'Head of Household': [[17000, 0.10], [64850, 0.12], [103350, 0.22], [197300, 0.24], [250500, 0.32], [626350, 0.35], [Infinity, 0.37]],
  },
};
const LTCG_BRACKETS = _TAX_TABLES.ltcg_brackets
  ? _loadBracketTable(_TAX_TABLES.ltcg_brackets)
  : {
  2024: {
    'Single': [[47025, 0.00], [518900, 0.15], [Infinity, 0.20]],
    'Married Filing Jointly': [[94050, 0.00], [583750, 0.15], [Infinity, 0.20]],
    'Married Filing Separately': [[47025, 0.00], [291850, 0.15], [Infinity, 0.20]],
    'Head of Household': [[63000, 0.00], [551350, 0.15], [Infinity, 0.20]],
  },
  2025: {
    'Single': [[48350, 0.00], [533400, 0.15], [Infinity, 0.20]],
    'Married Filing Jointly': [[96700, 0.00], [600050, 0.15], [Infinity, 0.20]],
    'Married Filing Separately': [[48350, 0.00], [300000, 0.15], [Infinity, 0.20]],
    'Head of Household': [[64750, 0.00], [566700, 0.15], [Infinity, 0.20]],
  },
};
const STD_DEDUCTION = _TAX_TABLES.std_deduction || {
  2024: { 'Single': 14600, 'Married Filing Jointly': 29200, 'Married Filing Separately': 14600, 'Head of Household': 21900 },
  2025: { 'Single': 15000, 'Married Filing Jointly': 30000, 'Married Filing Separately': 15000, 'Head of Household': 22500 },
};

// Pick (year, status) from any of the three tables above, falling
// back to the most-recent year for this status, then to Single.
function _bracketsFor(table, year, status) {
  const y = table[year] || table[Object.keys(table).sort().reverse()[0]];
  return y[status] || y['Single'];
}

// Estimate marginal rates from salary history, bonuses, 401K contributions,
// and portfolio income for a given year.  Returns an object suitable for
// displaying the assumptions.
function estimateTaxRates(year) {
  const y = String(year || new Date().getFullYear());
  const yNum = parseInt(y, 10);

  // Prefer the precomputed Python estimate (handles end-of-year
  // projection of 401K + portfolio income for the current year).
  // Fall back to JS recomputation if analytics is missing for this year.
  const py = ((ANALYTICS.tax || {}).rate_estimates_by_year || {})[y];
  if (py) {
    return {
      year: py.year,
      salary: py.salary,
      bonuses: py.bonuses,
      portfolioIncome: py.portfolio_income,
      portfolioIncomeYtd: py.portfolio_income_ytd,
      realizedST: py.realized_st || 0,
      realizedLT: py.realized_lt || 0,
      k401: py.k401,
      k401Ytd: py.k401_ytd,
      k401Limit: py.k401_limit,
      grossIncome: py.gross_income,
      agi: py.agi,
      taxableIncome: py.taxable_income,
      taxableOrdinary: py.taxable_ordinary,
      stdDed: py.std_deduction,
      marginalShort: py.marginal_short,
      marginalLong: py.marginal_long,
      estCapGainsTaxFederal: py.est_cap_gains_tax_federal,
      estCapGainsTaxState: py.est_cap_gains_tax_state,
      estNiit: py.est_niit,
      estCapGainsTaxTotal: py.est_cap_gains_tax_total,
      estQuarterlyPayment: py.est_quarterly_payment,
      isProjection: !!py.is_projection,
      yearFraction: py.year_fraction_observed,
    };
  }

  // Fallback: JS recomputation (no projection).  Used when the analytics
  // payload doesn't carry data for this year (rare — only when there are
  // no transactions in that year at all).
  const filing = filingStatus();
  const brackets = _bracketsFor(FEDERAL_BRACKETS, yNum, filing);
  const ltcg = _bracketsFor(LTCG_BRACKETS, yNum, filing);
  const stdDed = _bracketsFor(STD_DEDUCTION, yNum, filing);
  const salaries = RETIREMENT_META.salary_history || [];
  const cutoff = y + '-12-31';
  let salary = 0;
  for (const s of salaries) {
    if (s.date && s.date <= cutoff) salary = s.amount;
  }
  const bonuses = (RETIREMENT_META.bonus_history || [])
    .filter(b => yearOf(b.date) === y)
    .reduce((s, b) => s + (b.amount || 0), 0);
  let portfolioIncome = 0;
  let k401 = 0;
  for (const t of txns) {
    if (yearOf(t.date) !== y) continue;
    if (INCOME_ACTIONS[t.action] !== undefined) portfolioIncome += (t.amount || 0);
    const info = retirementContribInfo(t);
    if (info.isContrib && (t.account_group === '401K' || t.account_group === 'Rollover IRA')) {
      k401 += (t.amount || 0);
    }
  }
  const grossIncome = salary + bonuses + portfolioIncome;
  const agi = Math.max(0, grossIncome - k401);
  const taxableIncome = Math.max(0, agi - stdDed);
  let marginalShort = brackets[0][1];
  for (const [cap, rate] of brackets) {
    marginalShort = rate;
    if (taxableIncome <= cap) break;
  }
  let marginalLong = ltcg[0][1];
  for (const [cap, rate] of ltcg) {
    marginalLong = rate;
    if (taxableIncome <= cap) break;
  }
  return {
    year: yNum, salary, bonuses, portfolioIncome, k401,
    grossIncome, agi, taxableIncome, stdDed,
    marginalShort, marginalLong,
    isProjection: false,
  };
}

// Default tax rates — user can adjust in the UI.  Auto-populated from
// the estimator on first render.
let taxShortRate = null;
let taxLongRate = null;

function setTaxShortRate(v) {
  const n = parseFloat(v);
  if (!isNaN(n) && n >= 0 && n <= 1) { taxShortRate = n; renderTax(); }
}
function setTaxLongRate(v) {
  const n = parseFloat(v);
  if (!isNaN(n) && n >= 0 && n <= 1) { taxLongRate = n; renderTax(); }
}
function resetTaxRatesToEstimate() {
  const y = taxYearFilter === 'all' ? new Date().getFullYear() : parseInt(taxYearFilter, 10);
  const e = estimateTaxRates(y);
  taxShortRate = e.marginalShort;
  taxLongRate = e.marginalLong;
  renderTax();
}
let taxYearFilter = String(new Date().getFullYear());
function setTaxYearFilter(v) {
  taxYearFilter = v;
  // Refresh estimate when year changes
  const y = v === 'all' ? new Date().getFullYear() : parseInt(v, 10);
  const e = estimateTaxRates(y);
  taxShortRate = e.marginalShort;
  taxLongRate = e.marginalLong;
  renderTax();
}

// "Approaching Long-Term Status" — interaction state.
//   _ltExpanded:       which asset rows are showing per-lot detail
//                      (keyed by "account_group|symbol")
//   _ltSortKey:        which column is driving the sort.  'default'
//                      = actionable-first (ST sorted by soonest
//                      crossing, fully-LT assets at the bottom by
//                      value desc).  Other keys: 'symbol', 'account',
//                      'qty', 'next_lt', 'value', 'basis', 'unrealized'.
//   _ltSortDir:        1 ascending, -1 descending
//   _ltAccountFilter:  null = all accounts; otherwise an account_group
//                      string to filter the table down to one broker.
const _ltExpanded = new Set();
let _ltSortKey = 'default';
let _ltSortDir = 1;
let _ltAccountFilter = null;

function _setLtSort(key) {
  if (_ltSortKey === key) {
    _ltSortDir = -_ltSortDir;
  } else {
    _ltSortKey = key;
    // Sensible default direction per column type: text asc, numeric
    // desc (biggest first feels right for value/unrealized).
    _ltSortDir = (key === 'symbol' || key === 'account' || key === 'next_lt') ? 1 : -1;
  }
  renderTax();
}

function _setLtAccountFilter(name) {
  _ltAccountFilter = (name === 'all' || !name) ? null : name;
  renderTax();
}

function _toggleLtAsset(key) {
  if (_ltExpanded.has(key)) _ltExpanded.delete(key);
  else _ltExpanded.add(key);
  renderTax();
}

// Classify a closing txn into short-term / long-term / Section 1256.
// Returns { st: dollars_short, lt: dollars_long, kind: 'normal' | '1256' }.
function classifyRealized(t) {
  const gain = t.realized_gain || 0;
  if (isSection1256Symbol(t.symbol || '')) {
    return { st: gain * 0.4, lt: gain * 0.6, kind: '1256' };
  }
  const days = t.holding_days;
  const isLT = days != null && days > 365;
  return isLT ? { st: 0, lt: gain, kind: 'normal' } : { st: gain, lt: 0, kind: 'normal' };
}

// --- Tax bracket fill bar --------------------------------------------------
// Estimated tax on YTD realized capital gains (federal + state + NIIT)
// with a naive even-quarters suggested estimated payment.  All figures
// precomputed in Python (analytics/tax.py rate_estimates_by_year).
// Renders nothing when there are no realized gains for the year.
function _buildEstimatedTaxSection(e) {
  if (!e || e.estCapGainsTaxTotal == null) return '';
  const total = e.estCapGainsTaxTotal;
  const realized = (e.realizedST || 0) + (e.realizedLT || 0);
  if (Math.abs(total) < 0.5 && Math.abs(realized) < 0.5) return '';
  const proj = e.isProjection
    ? ` <span style="color:var(--text-dim);font-size:0.75rem;">(YTD, ${(e.yearFraction * 100).toFixed(0)}% of year)</span>` : '';
  const item = (label, val, cls) => `
    <div class="item"><span class="label">${label}</span>
      <span class="value ${cls || ''}">${fmtMoney(val, 0)}</span></div>`;
  const stateItem = (e.estCapGainsTaxState > 0)
    ? item('State', e.estCapGainsTaxState) : '';
  const niitItem = (e.estNiit > 0)
    ? item('NIIT (3.8%)', e.estNiit) : '';
  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Estimated Tax on Realized Gains</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">${e.year}${proj} · what to set aside for estimated payments</span>
    </div>
    <div class="panel">
      <div class="bracket-summary">
        ${item('Federal (ST + LT)', e.estCapGainsTaxFederal)}
        ${stateItem}
        ${niitItem}
        ${item('Total estimated', total, 'negative')}
        ${item('≈ per quarter (÷4)', e.estQuarterlyPayment)}
      </div>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:10px;line-height:1.5;">
        Rough set-aside for IRS Form 1040-ES on <b>taxable-account</b> realized gains only:
        ST at your ordinary marginal rate, LT at the LTCG rate, plus state (gains taxed as
        ordinary income in most states) and NIIT (3.8% above the MAGI threshold).  Not a
        substitute for a tax pro — ignores withholding, credits, AMT, and safe-harbor
        prior-year rules.  Even-quarters split is a simplification; gains realized late in
        the year may shift the due date.
      </div>
    </div>`;
}

function _buildBracketSection(year) {
  const rates = ((ANALYTICS.tax || {}).rate_estimates_by_year) || {};
  const est = rates[String(year)] || rates[year] || null;
  if (!est || !Array.isArray(est.bracket_fill) || !est.bracket_fill.length) return '';
  const bf = est.bracket_fill;
  // Upper cap for chart = top of the first bracket that has room left,
  // so most users land in a readable range instead of filling a $0-$609k
  // bar with a tiny sliver in the first couple brackets.
  let chartCap = 0;
  for (const b of bf) {
    chartCap = b.upper != null ? b.upper : (bf[bf.length - 2]?.upper || 500000);
    if (b.room_left > 0) break;
  }
  // Always show at least the bracket containing their income plus one more
  const lastFilledIdx = bf.findIndex(b => b.room_left > 0);
  if (lastFilledIdx !== -1 && lastFilledIdx + 1 < bf.length) {
    const nextUp = bf[lastFilledIdx + 1].upper;
    if (nextUp != null) chartCap = Math.max(chartCap, nextUp);
  }
  chartCap = chartCap || (bf[bf.length - 2]?.upper || 500000);

  const segments = bf.map((b, i) => {
    const lower = b.lower;
    const upper = b.upper != null ? b.upper : chartCap;
    if (lower >= chartCap) return '';
    const segUpper = Math.min(upper, chartCap);
    const width = ((segUpper - lower) / chartCap) * 100;
    if (width <= 0) return '';
    const bracketWidth = upper - lower;
    let cls = '';
    let fillPct = 0;
    if (b.in_bracket >= bracketWidth - 0.01 && bracketWidth > 0) {
      cls = 'filled';
    } else if (b.in_bracket > 0) {
      cls = 'partial';
      fillPct = (b.in_bracket / bracketWidth) * 100;
    }
    const roomStr = b.room_left == null ? 'no upper limit' : fmtMoney(b.room_left);
    const label = `${(b.rate * 100).toFixed(0)}% bracket: ${fmtMoney(lower, 0)}–${b.upper != null ? fmtMoney(upper, 0) : '∞'}\nIncome in this bracket: ${fmtMoney(b.in_bracket)}\nRoom left: ${roomStr}`;
    const styleVars = cls === 'partial' ? `--fill:${fillPct}%;` : '';
    return `<div class="bracket-segment ${cls}" style="width:${width}%;${styleVars}" title="${_htmlEsc(label)}"></div>`;
  }).join('');

  const legend = bf.filter(b => b.lower < chartCap).map(b => `
    <span class="seg">
      <span class="swatch" style="background:${b.in_bracket > 0 ? 'var(--accent)' : 'rgba(167,139,250,0.25)'};"></span>
      ${(b.rate * 100).toFixed(0)}% · ${fmtMoney(b.lower, 0)}${b.upper != null ? '–' + fmtMoney(b.upper, 0) : '+'}
    </span>`).join('');

  const headroom = est.headroom_to_next_bracket || 0;
  const nextRate = est.next_bracket_rate;
  const current = (bf.find(b => b.in_bracket > 0 && b.in_bracket < (b.upper != null ? b.upper - b.lower : Infinity)) ||
    bf.slice().reverse().find(b => b.in_bracket > 0));
  const currentRate = current ? current.rate : 0;
  const totalTax = bf.reduce((s, b) => s + (b.tax_paid || 0), 0);

  const projTag = est.is_projection
    ? ' <span style="color:var(--yellow);font-size:0.75rem;font-weight:400;">(year-end projection)</span>'
    : '';
  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Tax Bracket Fill — ${year}</span>${projTag}</h2>
    </div>
    <div class="bracket-wrap">
      <div class="bracket-bar">${segments}</div>
      <div class="bracket-legend">${legend}</div>
      <div class="bracket-summary">
        <div class="item"><span class="label">Taxable Income</span><span class="value">${fmtMoney(est.taxable_income || 0, 0)}</span></div>
        <div class="item"><span class="label">Current Bracket</span><span class="value">${(currentRate * 100).toFixed(0)}%</span></div>
        <div class="item"><span class="label">Room in Bracket</span><span class="value">${fmtMoney(headroom, 0)}</span></div>
        <div class="item"><span class="label">Next Rate</span><span class="value">${nextRate != null ? (nextRate * 100).toFixed(0) + '%' : 'top'}</span></div>
        <div class="item"><span class="label">Federal Income Tax (est)</span><span class="value">${fmtMoney(totalTax, 0)}</span></div>
      </div>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:8px;line-height:1.4;">
        Filled = income already allocated to that bracket.  Partial (gradient) shows the bracket where your taxable income ends.  Use <b>Room in Bracket</b> to estimate how much additional short-term capital gain would stay at ${(currentRate * 100).toFixed(0)}% vs push into the next tier.
      </div>
    </div>
  `;
}

// Generic client-side CSV download.  rows = array of objects; columns =
// [[key, header], ...].  Triggers a browser download of a .csv file.
function _downloadCsv(filename, columns, rows) {
  const esc = (v) => {
    const s = (v == null) ? '' : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [columns.map(c => esc(c[1])).join(',')];
  for (const r of rows) lines.push(columns.map(c => esc(r[c[0]])).join(','));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

// Download taxable-account realized disposals as a Form 8949-style CSV.
// Term maps to 8949 logic: long/§1256 → Part II (long-term box), short →
// Part I.  Precomputed in Python (analytics.tax.form_8949).
function downloadForm8949() {
  const rows = (ANALYTICS.tax || {}).form_8949 || [];
  if (!rows.length) return;
  _downloadCsv('form_8949_realized_gains.csv', [
    ['description', 'Description'],
    ['date_acquired', 'Date Acquired'],
    ['date_sold', 'Date Sold'],
    ['proceeds', 'Proceeds'],
    ['cost_basis', 'Cost Basis'],
    ['gain', 'Gain/Loss'],
    ['term', 'Term'],
    ['account', 'Account'],
  ], rows);
}

function renderTax() {
  const root = document.getElementById('taxContent');
  if (!root) return;

  // All txns with realized_gain set
  const realized = txns.filter(t => typeof t.realized_gain === 'number');
  const allYears = [...new Set(realized.map(t => yearOf(t.date)).filter(Boolean))].sort();

  const filtered = taxYearFilter === 'all' ? realized
    : realized.filter(t => yearOf(t.date) === taxYearFilter);

  // Auto-populate tax rates from the estimator on first render (using
  // the filtered year, or current year if "all").  User can still
  // override in the UI.
  const estYear = taxYearFilter === 'all' ? new Date().getFullYear() : parseInt(taxYearFilter, 10);
  const est = estimateTaxRates(estYear);
  if (taxShortRate === null) taxShortRate = est.marginalShort;
  if (taxLongRate === null) taxLongRate = est.marginalLong;

  // Aggregate
  let totalST = 0, totalLT = 0;
  let total1256gain = 0;
  const byYear = {};
  for (const t of filtered) {
    const c = classifyRealized(t);
    totalST += c.st;
    totalLT += c.lt;
    if (c.kind === '1256') total1256gain += (t.realized_gain || 0);
    const y = yearOf(t.date);
    if (!byYear[y]) byYear[y] = { st: 0, lt: 0, s1256: 0, count: 0, proceeds: 0, basis: 0 };
    byYear[y].st += c.st;
    byYear[y].lt += c.lt;
    if (c.kind === '1256') byYear[y].s1256 += (t.realized_gain || 0);
    byYear[y].count++;
    byYear[y].proceeds += (t.amount || 0);
    byYear[y].basis += (t.cost_basis || 0);
  }
  const stTax = totalST * taxShortRate;
  const ltTax = totalLT * taxLongRate;
  const estTax = stTax + ltTax;

  // Stat cards
  const statCards = [
    { label: 'Short-term Gain', value: fmtSigned(totalST), cls: totalST >= 0 ? 'positive' : 'negative' },
    { label: 'Long-term Gain', value: fmtSigned(totalLT), cls: totalLT >= 0 ? 'positive' : 'negative' },
    { label: '§1256 Gain (incl. above)', value: fmtSigned(total1256gain) },
    {
      label: 'Estimated Tax', value: fmtMoney(Math.max(0, estTax)),
      cls: estTax > 0 ? 'negative' : ''
    },
    { label: 'Closed Trades', value: filtered.length.toString() },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Year filter pills
  const yearPills = ['all', ...allYears].map(y => {
    const cls = 'tbtn' + (taxYearFilter === y ? ' active' : '');
    const lbl = y === 'all' ? 'All Years' : y;
    return `<button class="${cls}" onclick="setTaxYearFilter('${y}')">${lbl}</button>`;
  }).join('');

  // Gains table by year — §1256 column only shown if any year has any
  // §1256 activity (most users never trade index options, so the column
  // is just empty noise otherwise).
  const showS1256ByYear = Object.values(byYear).some(r => r.s1256 !== 0);
  const yearRows = Object.keys(byYear).sort().map(y => {
    const r = byYear[y];
    return `<tr>
      <td><b>${y}</b></td>
      <td class="num">${r.count}</td>
      <td class="num">${fmtMoney(r.proceeds)}</td>
      <td class="num">${fmtMoney(r.basis)}</td>
      <td class="num"><span class="${r.st >= 0 ? 'positive' : 'negative'}">${fmtSigned(r.st)}</span></td>
      <td class="num"><span class="${r.lt >= 0 ? 'positive' : 'negative'}">${fmtSigned(r.lt)}</span></td>
      ${showS1256ByYear ? `<td class="num">${r.s1256 !== 0 ? fmtSigned(r.s1256) : ''}</td>` : ''}
    </tr>`;
  }).join('');

  // Per-symbol breakdown (who's driving the tax bill).  Shows ST / LT /
  // §1256 components + est. tax contribution per symbol.  Filtered by
  // the current year selector.
  const bySym = {};
  for (const t of filtered) {
    const sym = t.symbol || '(unknown)';
    const c = classifyRealized(t);
    if (!bySym[sym]) {
      bySym[sym] = {
        symbol: sym, trades: 0, proceeds: 0, basis: 0, st: 0, lt: 0, s1256: 0,
        underlying: parseOptionSymbol(sym) ? parseOptionSymbol(sym).underlying : sym
      };
    }
    bySym[sym].trades++;
    bySym[sym].proceeds += (t.amount || 0);
    bySym[sym].basis += (t.cost_basis || 0);
    bySym[sym].st += c.st;
    bySym[sym].lt += c.lt;
    if (c.kind === '1256') bySym[sym].s1256 += (t.realized_gain || 0);
  }
  const showS1256BySym = Object.values(bySym).some(r => r.s1256 !== 0);
  const symRows = Object.values(bySym)
    .sort((a, b) => Math.abs((b.st + b.lt)) - Math.abs((a.st + a.lt)))
    .slice(0, 50)
    .map(r => {
      const totalGain = r.st + r.lt;
      const tax = r.st * taxShortRate + r.lt * taxLongRate;
      const totalCls = totalGain >= 0 ? 'positive' : 'negative';
      return `<tr>
        <td><b>${symLabel(r.symbol)}</b></td>
        <td class="num">${r.trades}</td>
        <td class="num">${fmtMoney(r.proceeds)}</td>
        <td class="num">${fmtMoney(r.basis)}</td>
        <td class="num"><span class="${r.st >= 0 ? 'positive' : 'negative'}">${fmtSigned(r.st)}</span></td>
        <td class="num"><span class="${r.lt >= 0 ? 'positive' : 'negative'}">${fmtSigned(r.lt)}</span></td>
        ${showS1256BySym ? `<td class="num">${r.s1256 !== 0 ? fmtSigned(r.s1256) : ''}</td>` : ''}
        <td class="num"><span class="${totalCls}">${fmtSigned(totalGain)}</span></td>
        <td class="num"><span class="${tax >= 0 ? 'negative' : 'positive'}">${tax >= 0 ? fmtMoney(tax) : '-' + fmtMoney(Math.abs(tax))}</span></td>
      </tr>`;
    }).join('');

  // (Options "By Underlying" intentionally lives on the Options tab —
  // the per-symbol "By Asset" table further down already shows ST/LT/§1256
  // per option contract for tax purposes.)

  // Harvest candidates: current holdings with unrealized loss
  const harvestCandidates = holdingsByAsset
    .filter(h => typeof h.unrealized_gain === 'number' && h.unrealized_gain < -10)
    .sort((a, b) => a.unrealized_gain - b.unrealized_gain);   // most-negative first
  const harvestRows = harvestCandidates.slice(0, 25).map(h => {
    const potentialSave = Math.abs(h.unrealized_gain) * taxShortRate;
    return `<tr>
      <td><b>${symLabel(h.symbol)}</b></td>
      <td>${h.sector || ''}</td>
      <td class="num">${(h.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="num">${fmtMoney(h.value)}</td>
      <td class="num">${fmtMoney(h.cost_basis)}</td>
      <td class="num"><span class="negative">${fmtSigned(h.unrealized_gain)}</span></td>
      <td class="num">${fmtMoney(potentialSave)}</td>
    </tr>`;
  }).join('');

  // Wash sale detection: find sells at a loss with a matching buy within 30 days.
  // For each (symbol, account_group), scan pairs of txns.
  const buysBySym = {};  // symbol -> [{date, date_obj}]
  for (const t of txns) {
    if (t.action !== 'Buy' && t.action !== 'Reinvest' && t.action !== 'Contribution') continue;
    if (!t.symbol || !t.date) continue;
    if (!buysBySym[t.symbol]) buysBySym[t.symbol] = [];
    buysBySym[t.symbol].push(new Date(t.date));
  }
  const washSales = [];
  for (const t of realized) {
    if ((t.realized_gain || 0) >= 0) continue;   // only losses can trigger wash
    if (t.action !== 'Sell') continue;            // and only actual sells
    const buys = buysBySym[t.symbol] || [];
    const closeD = new Date(t.date);
    const min = new Date(closeD); min.setDate(min.getDate() - 30);
    const max = new Date(closeD); max.setDate(max.getDate() + 30);
    const offender = buys.find(bd => bd >= min && bd <= max && bd.toISOString().slice(0, 10) !== t.date);
    if (offender) {
      washSales.push({
        date: t.date,
        symbol: t.symbol,
        loss: t.realized_gain,
        offending_buy_date: offender.toISOString().slice(0, 10),
      });
    }
  }
  const washRows = washSales.map(w => `<tr>
    <td>${w.date}</td>
    <td><b>${symLabel(w.symbol)}</b></td>
    <td class="num"><span class="negative">${fmtSigned(w.loss)}</span></td>
    <td>${w.offending_buy_date}</td>
  </tr>`).join('');

  // ---- Approaching Long-Term Status -------------------------------
  // Per-asset roll-up of LT eligibility.  One row per
  // (account_group, symbol); click to expand inline per-lot detail.
  // Answers the actionable question "how many shares of X can I sell
  // at LT rates today, and when does the next batch qualify?".
  //
  // Lot dust-filter: |value| or |cost_basis| ≥ $10 so sub-penny
  // crypto residuals don't bloat the count.  Sort: assets with ST
  // lots first (asc by their soonest LT crossing — most actionable
  // on top); fully-LT assets at the bottom (desc by value, so big
  // positions you can already sell at LT are easy to spot).
  const ltLots = ((ANALYTICS.tax || {}).lt_horizon || []).filter(r => {
    const v = r.value != null ? r.value : (r.cost_basis || 0);
    return Math.abs(v) >= 10 || Math.abs(r.cost_basis || 0) >= 10;
  });
  // Roll lots up per (account_group, symbol).
  const ltAssetMap = new Map();
  for (const r of ltLots) {
    const k = r.account_group + '​|' + r.symbol;
    let a = ltAssetMap.get(k);
    if (!a) {
      a = {
        key: k, account_group: r.account_group, symbol: r.symbol,
        price: r.price, lots: [],
        total_qty: 0, lt_qty: 0, st_qty: 0,
        total_value: 0, total_basis: 0, total_unrealized: 0,
        lt_value: 0, st_value: 0,
        next_lt_days: null, next_lt_date: null,
      };
      ltAssetMap.set(k, a);
    }
    a.lots.push(r);
    a.total_qty += r.qty;
    a.total_basis += r.cost_basis || 0;
    if (r.value != null) a.total_value += r.value;
    if (r.unrealized_gain != null) a.total_unrealized += r.unrealized_gain;
    if (r.is_long_term) {
      a.lt_qty += r.qty;
      if (r.value != null) a.lt_value += r.value;
    } else {
      a.st_qty += r.qty;
      if (r.value != null) a.st_value += r.value;
      if (a.next_lt_days === null || r.days_to_lt < a.next_lt_days) {
        a.next_lt_days = r.days_to_lt;
        a.next_lt_date = r.lt_eligible_date;
      }
    }
  }
  // Account-filter chip row needs to know what's available BEFORE
  // filtering, so derive it from the unfiltered map.
  const ltAccounts = [...new Set([...ltAssetMap.values()].map(a => a.account_group))]
    .sort((a, b) => a.localeCompare(b));

  // Apply account filter (single-select; null = all).
  const ltFiltered = _ltAccountFilter
    ? [...ltAssetMap.values()].filter(a => a.account_group === _ltAccountFilter)
    : [...ltAssetMap.values()];

  // Sort.  The 'default' sort puts actionable assets (any ST lot) on
  // top, ordered by their soonest LT crossing; fully-LT assets fall
  // to the bottom sorted by value desc.  All other sort keys are a
  // single comparator with the configured direction.
  const _next = a => a.next_lt_days === null ? Infinity : a.next_lt_days;
  const sortComp = {
    'default': (a, b) => {
      const aFully = a.next_lt_days === null;
      const bFully = b.next_lt_days === null;
      if (aFully !== bFully) return aFully ? 1 : -1;
      if (!aFully) return a.next_lt_days - b.next_lt_days;
      return (b.total_value || 0) - (a.total_value || 0);
    },
    'symbol': (a, b) => a.symbol.localeCompare(b.symbol),
    'account': (a, b) => a.account_group.localeCompare(b.account_group),
    'qty': (a, b) => (a.total_qty || 0) - (b.total_qty || 0),
    'next_lt': (a, b) => _next(a) - _next(b),
    'value': (a, b) => (a.total_value || 0) - (b.total_value || 0),
    'basis': (a, b) => (a.total_basis || 0) - (b.total_basis || 0),
    'unrealized': (a, b) => (a.total_unrealized || 0) - (b.total_unrealized || 0),
  };
  const cmp = sortComp[_ltSortKey] || sortComp['default'];
  const ltAssets = _ltSortKey === 'default'
    ? ltFiltered.sort(cmp)
    : ltFiltered.sort((a, b) => _ltSortDir * cmp(a, b));

  // Render — main row + (optionally) an expansion row with per-lot
  // detail.  The expansion <tr> spans the full table width and
  // contains its own mini-table.
  const LT_LIMIT = 100;
  const ltAssetRows = ltAssets.slice(0, LT_LIMIT).map(a => {
    const fully = a.next_lt_days === null;
    const ltPct = a.total_qty > 0 ? (a.lt_qty / a.total_qty) * 100 : 0;
    const stPct = Math.max(0, 100 - ltPct);
    const imminent = !fully && a.next_lt_days <= 60;
    const expanded = _ltExpanded.has(a.key);
    const arrow = expanded ? '▾' : '▸';
    // Qty rendering — show LT/Total with the visual bar inline.
    const fmtQ = q => q.toLocaleString(undefined, { maximumFractionDigits: 4 });
    const qtyCell = `
      <div style="display:flex;flex-direction:column;gap:3px;">
        <div style="font-size:0.85rem;">
          <b>${fmtQ(a.lt_qty)}</b> <span style="color:var(--text-dim);">LT</span>
          <span style="color:var(--text-dim);"> / ${fmtQ(a.total_qty)}</span>
        </div>
        <div class="lt-bar" title="LT: ${ltPct.toFixed(0)}% · ST: ${stPct.toFixed(0)}%">
          <div class="lt-bar-lt" style="width:${ltPct.toFixed(2)}%;"></div>
          <div class="lt-bar-st" style="width:${stPct.toFixed(2)}%;"></div>
        </div>
      </div>`;
    const nextCell = fully
      ? '<span style="color:var(--green);font-weight:600;">✓ Fully LT</span>'
      : `<b${imminent ? ' style="color:var(--yellow);"' : ''}>${a.next_lt_days}d</b>`
      + ` <div style="color:var(--text-dim);font-size:0.78rem;">→ ${a.next_lt_date}</div>`;
    const ugCls = a.total_unrealized > 0 ? 'positive' : (a.total_unrealized < 0 ? 'negative' : '');
    const rowBg = imminent ? ' style="background:rgba(245,158,11,0.04);"' : '';

    // Expanded lot detail — sorted by days_to_lt asc (imminent ST
    // first, then LT lots in chronological order).
    let expansion = '';
    if (expanded) {
      const sortedLots = [...a.lots].sort((x, y) => x.days_to_lt - y.days_to_lt);
      const lotRows = sortedLots.map(l => {
        const lImm = !l.is_long_term && l.days_to_lt <= 60;
        const lUgCls = l.unrealized_gain == null ? '' : (l.unrealized_gain > 0 ? 'positive' : (l.unrealized_gain < 0 ? 'negative' : ''));
        const statusCell = l.is_long_term
          ? `<span style="color:var(--green);">LT · held ${l.days_held}d</span>`
          : `<b${lImm ? ' style="color:var(--yellow);"' : ''}>${l.days_to_lt}d</b> <span style="color:var(--text-dim);font-size:0.78rem;">→ ${l.lt_eligible_date}</span>`;
        return `<tr>
          <td style="padding-left:24px;color:var(--text-dim);">↳ ${l.open_date}</td>
          <td class="num">${fmtQ(l.qty)}</td>
          <td class="num">${fmtMoney(l.cost_basis)}</td>
          <td class="num">${l.value != null ? fmtMoney(l.value) : '—'}</td>
          <td class="num"><span class="${lUgCls}">${l.unrealized_gain != null ? fmtSigned(l.unrealized_gain) : '—'}</span></td>
          <td>${statusCell}</td>
        </tr>`;
      }).join('');
      expansion = `<tr class="lt-expansion"><td colspan="7" style="padding:8px 24px 12px;background:rgba(167,139,250,0.03);border-top:0;">
        <table class="mini-table" style="font-size:0.82rem;">
          <thead><tr>
            <th>Acquired</th><th class="num">Qty</th><th class="num">Basis</th>
            <th class="num">Value</th><th class="num">Unrealized</th><th>Status</th>
          </tr></thead>
          <tbody>${lotRows}</tbody>
        </table>
      </td></tr>`;
    }

    return `<tr class="lt-asset-row" onclick="_toggleLtAsset('${a.key.replace(/'/g, "\\'")}')"${rowBg}>
      <td><span class="ab-acct-arrow">${arrow}</span> <b>${symLabel(a.symbol)}</b></td>
      <td><span style="color:${ACCOUNT_COLORS[a.account_group] || ''};">${a.account_group}</span></td>
      <td>${qtyCell}</td>
      <td>${nextCell}</td>
      <td class="num">${a.total_value ? fmtMoney(a.total_value) : '—'}</td>
      <td class="num">${fmtMoney(a.total_basis)}</td>
      <td class="num"><span class="${ugCls}">${fmtSigned(a.total_unrealized)}</span></td>
    </tr>${expansion}`;
  }).join('');
  const ltMoreNote = ltAssets.length > LT_LIMIT
    ? `<tr><td colspan="7" style="color:var(--yellow);text-align:center;padding:6px;">Showing ${LT_LIMIT} of ${ltAssets.length} assets.</td></tr>`
    : '';
  // Quick stats above the table — total ST shares pending vs total LT,
  // weighted by dollar value to highlight the magnitude of the
  // currently-locked gains.  Reflects the filtered view so the totals
  // line up with what's actually rendered below.
  const stPendingValue = ltAssets.reduce((s, a) => s + a.st_value, 0);
  const ltAlreadyValue = ltAssets.reduce((s, a) => s + a.lt_value, 0);
  const totalPositions = ltAssets.length;
  const fullyLtPositions = ltAssets.filter(a => a.next_lt_days === null).length;
  const ltSummary = `
    <span style="margin-left:12px;color:var(--text-dim);font-size:0.78rem;">
      ${totalPositions} taxable position${totalPositions === 1 ? '' : 's'}
      <span style="color:var(--green);">· ${fmtMoney(ltAlreadyValue)} already LT</span>
      <span style="color:var(--yellow);">· ${fmtMoney(stPendingValue)} still ST</span>
      <span style="color:var(--green);">· ${fullyLtPositions} fully LT</span>
    </span>`;

  // Account-filter chip bar — single-select.  "All" reset chip + one
  // chip per account_group that actually has taxable lots.  Uses the
  // shared _renderAccountChip helper for consistent styling across tabs.
  const ltAcctChips = ltAccounts.length > 1 ? `
    <div class="toggles-row" style="margin:8px 0 12px;">
      <span class="toggles-label">Account:</span>
      ${_renderAccountChip(null, !_ltAccountFilter, "_setLtAccountFilter('all')", 'All')}
      ${ltAccounts.map(acct => _renderAccountChip(
        acct, _ltAccountFilter === acct,
        `_setLtAccountFilter('${acct.replace(/'/g, "\\'")}')`
      )).join('')}
    </div>` : '';

  // Sortable headers.  Each clickable <th> shows a sort indicator
  // when active; inactive ones get a faint glyph as an affordance.
  function _sortHdr(key, label, cls) {
    const active = _ltSortKey === key;
    const arrow = active ? (_ltSortDir > 0 ? '↑' : '↓') : '↕';
    const aCls = active ? 'style="color:var(--accent);"' : 'style="color:var(--text-dim);opacity:0.5;"';
    return `<th class="${cls || ''}" style="cursor:pointer;user-select:none;" onclick="_setLtSort('${key}')">${label} <span ${aCls}>${arrow}</span></th>`;
  }
  const ltHeadHtml = `<tr>
    ${_sortHdr('symbol', 'Symbol')}
    ${_sortHdr('account', 'Account')}
    ${_sortHdr('qty', 'Qty (LT / Total)')}
    ${_sortHdr('next_lt', 'Next LT')}
    ${_sortHdr('value', 'Value', 'num')}
    ${_sortHdr('basis', 'Basis', 'num')}
    ${_sortHdr('unrealized', 'Unrealized', 'num')}
  </tr>`;

  // --- Render ---
  root.innerHTML = `
    <div style="margin-bottom:12px;">${yearPills}</div>
    ${statsHtml}

    <div class="panel" style="margin-top:16px;">
      <h3>Tax Rates &amp; Income — ${est.year}${est.isProjection ? ' <span style="color:var(--yellow);font-size:0.75rem;font-weight:400;">(year-end projection)</span>' : ''}</h3>
      <div style="color:var(--text-dim);font-size:0.75rem;margin-bottom:8px;">
        Filing status: <b style="color:var(--text);">${_htmlEsc(filingStatus())}</b>
        ${RETIREMENT_META.state ? ` · State: <b style="color:var(--text);">${_htmlEsc(RETIREMENT_META.state)}</b>` : ''}
        <span style="margin-left:8px;opacity:0.7;">(edit <code>data/metadata.csv</code> to change)</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:0.85rem;">
        <div>
          <div style="color:var(--text-dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px;">Income build-up</div>
          <div>Salary (effective ${est.year}-12-31): <b>${fmtMoney(est.salary)}</b></div>
          <div>Bonuses paid in ${est.year}: <b>${fmtMoney(est.bonuses)}</b></div>
          <div>Portfolio income${est.isProjection ? ' (projected)' : ''}: <b>${fmtMoney(est.portfolioIncome)}</b>${est.isProjection && est.portfolioIncomeYtd != null ? ` <span style="color:var(--text-dim);font-size:0.75rem;">YTD ${fmtMoney(est.portfolioIncomeYtd)}</span>` : ''}</div>
          <div>Realized ST gains${est.isProjection ? ' (YTD)' : ''}: <b class="${(est.realizedST || 0) >= 0 ? 'positive' : 'negative'}">${fmtSigned(est.realizedST || 0)}</b></div>
          <div>Realized LT gains${est.isProjection ? ' (YTD)' : ''}: <b class="${(est.realizedLT || 0) >= 0 ? 'positive' : 'negative'}">${fmtSigned(est.realizedLT || 0)}</b></div>
          <div>401K contribution${est.isProjection ? ' (projected)' : ''}: <b class="negative">${fmtMoney(-est.k401)}</b>${est.isProjection && est.k401Ytd != null ? ` <span style="color:var(--text-dim);font-size:0.75rem;">YTD ${fmtMoney(est.k401Ytd)}${est.k401Limit ? ` · cap ${fmtMoney(est.k401Limit, 0)}` : ''}</span>` : ''}</div>
          <div>Standard deduction: <b class="negative">${fmtMoney(-est.stdDed)}</b></div>
          <div style="margin-top:4px;border-top:1px solid var(--border);padding-top:4px;">
            Taxable ordinary income: <b>${fmtMoney(est.taxableOrdinary != null ? est.taxableOrdinary : est.taxableIncome)}</b>
            <span style="color:var(--text-dim);font-size:0.75rem;margin-left:8px;">drives ordinary brackets</span>
          </div>
          <div>
            Total taxable income: <b>${fmtMoney(est.taxableIncome)}</b>
            <span style="color:var(--text-dim);font-size:0.75rem;margin-left:8px;">drives LTCG bracket</span>
          </div>
        </div>
        <div>
          <div style="color:var(--text-dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px;">Marginal rates</div>
          <div>Estimated ordinary rate: <b>${(est.marginalShort * 100).toFixed(1)}%</b></div>
          <div>Estimated LTCG rate: <b>${(est.marginalLong * 100).toFixed(1)}%</b></div>
          ${(RETIREMENT_META.state_tax_rate || 0) > 0 ? `
          <div style="margin-top:6px;padding-top:6px;border-top:1px dotted var(--border);">
            <span style="color:var(--text-dim);font-size:0.8rem;">+ ${_htmlEsc(RETIREMENT_META.state || 'State')} marginal: <b style="color:var(--text);">${(RETIREMENT_META.state_tax_rate * 100).toFixed(1)}%</b></span>
          </div>
          <div style="margin-top:2px;font-size:0.85rem;" title="Federal marginal + state marginal.  Useful for back-of-envelope 'if I realize $X, what's the all-in tax?' math.">
            <b>Combined ordinary: ${((est.marginalShort + (RETIREMENT_META.state_tax_rate || 0)) * 100).toFixed(1)}%</b>
            <span style="color:var(--text-dim);font-size:0.78rem;margin-left:6px;">(fed + state)</span>
          </div>
          <div style="font-size:0.85rem;">
            <b>Combined LTCG: ${((est.marginalLong + (RETIREMENT_META.state_tax_rate || 0)) * 100).toFixed(1)}%</b>
            <span style="color:var(--text-dim);font-size:0.78rem;margin-left:6px;">(states tax LTCG as ordinary)</span>
          </div>` : (RETIREMENT_META.state ? `
          <div style="margin-top:6px;padding-top:6px;border-top:1px dotted var(--border);color:var(--text-dim);font-size:0.78rem;">
            ${_htmlEsc(RETIREMENT_META.state)} has no state income tax — federal-only.
          </div>` : '')}
          <div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border);">
            <div style="color:var(--text-dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;">Override (used for the tables below)</div>
            <label style="font-size:0.85rem;color:var(--text-dim);">Short-term:
              <input type="number" value="${(taxShortRate * 100).toFixed(1)}" step="0.5" min="0" max="50"
                     style="width:60px;" oninput="setTaxShortRate(this.value/100)"/>%
            </label>
            <label style="margin-left:14px;font-size:0.85rem;color:var(--text-dim);">Long-term:
              <input type="number" value="${(taxLongRate * 100).toFixed(1)}" step="0.5" min="0" max="50"
                     style="width:60px;" oninput="setTaxLongRate(this.value/100)"/>%
            </label>
            <button class="tbtn" style="margin-left:10px;font-size:0.74rem;padding:3px 8px;"
                    onclick="resetTaxRatesToEstimate()">Reset</button>
          </div>
          <div style="color:var(--text-dim);font-size:0.72rem;margin-top:10px;line-height:1.5;">
            ${est.isProjection
      ? `Current-year figures are extrapolated from YTD pace (${(est.yearFraction * 100).toFixed(0)}% of year elapsed); 401K capped at IRS limit.  Realized gains are YTD only.  `
      : ''}ST gains stack with ordinary income; LT gains use the LTCG schedule but feed MAGI.  Retirement sells excluded.  §1256 contracts (SPX, NDX, NDXP, SPXW, XSP, RUT, DJX, VIX) get 60% LT / 40% ST regardless of hold period.
          </div>
        </div>
      </div>
    </div>

    ${_buildBracketSection(est.year)}

    ${_buildEstimatedTaxSection(est)}

    <h3 class="tax-group-header">Forward planning — actionable today</h3>

    <div class="section-header" style="margin-top:16px;">
      <h2><span style="color:var(--accent);">Tax-Loss Harvest Candidates</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">current positions with unrealized loss &gt; $10</span>
    </div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Symbol</th><th>Sector</th>
          <th class="num">Quantity</th><th class="num">Value</th><th class="num">Basis</th>
          <th class="num">Unrealized Loss</th><th class="num">Tax Save (est)</th>
        </tr></thead>
        <tbody>${harvestRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">No positions with unrealized loss &gt; $10.</td></tr>'}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">
        Note: selling these would realize losses that offset gains.  Avoid re-buying within 30 days
        (wash-sale rule).
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Potential Wash Sales</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">sold at a loss + bought same symbol within 30 days</span>
    </div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr><th>Sell Date</th><th>Symbol</th><th class="num">Loss</th><th>Offending Buy</th></tr></thead>
        <tbody>${washRows || '<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">No potential wash sales detected.</td></tr>'}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">
        Heuristic check: exact-symbol match only.  The IRS definition of "substantially identical"
        is broader (includes options on the same underlying, some ETFs, etc.) — treat as a heads-up, not a rule.
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Long-Term Eligibility by Asset</span></h2>
      ${ltSummary}
    </div>
    <div class="panel">
      ${ltAcctChips}
      <table class="mini-table lt-asset-table">
        <thead>${ltHeadHtml}</thead>
        <tbody>${ltAssetRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">No open taxable lots.</td></tr>'}${ltMoreNote}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">
        One row per <em>(account, symbol)</em>.  Click any row to see its individual lots and timing;
        click a column header to sort.  Yellow rows have a lot crossing into long-term within 60
        days — selling those sooner means paying the higher ordinary-income rate.  Retirement
        accounts are excluded (tax-deferred — the short/long distinction doesn't apply).
      </div>
    </div>

    <h3 class="tax-group-header">Historical realizations</h3>

    <div class="section-header" style="margin-top:16px;display:flex;align-items:center;justify-content:space-between;">
      <h2><span style="color:var(--accent);">Realized Gains by Year</span></h2>
      ${((ANALYTICS.tax || {}).form_8949 || []).length
        ? '<button class="tbtn" onclick="downloadForm8949()" title="Download taxable-account disposals as a Form 8949-style CSV (description, dates, proceeds, basis, gain, term) for your tax software / preparer.">⬇ Form 8949 CSV</button>'
        : ''}
    </div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Year</th><th class="num">Trades</th>
          <th class="num">Proceeds</th><th class="num">Basis</th>
          <th class="num">Short-term</th><th class="num">Long-term</th>
          ${showS1256ByYear ? '<th class="num">§1256 (of which)</th>' : ''}
        </tr></thead>
        <tbody>${yearRows || `<tr><td colspan="${showS1256ByYear ? 7 : 6}" style="color:var(--text-dim);padding:12px;">No realized gains.</td></tr>`}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">By Asset</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">contributors to the tax bill — sorted by gain magnitude</span>
    </div>
    <div class="panel">
      <div class="table-wrap">
        <table class="mini-table">
          <thead><tr>
            <th>Symbol</th><th class="num">Trades</th>
            <th class="num">Proceeds</th><th class="num">Basis</th>
            <th class="num">Short-term</th><th class="num">Long-term</th>
            ${showS1256BySym ? '<th class="num">§1256</th>' : ''}
            <th class="num">Total Gain</th>
            <th class="num">Est. Tax</th>
          </tr></thead>
          <tbody>${symRows || `<tr><td colspan="${showS1256BySym ? 9 : 8}" style="color:var(--text-dim);padding:12px;">No realized gains in this range.</td></tr>`}</tbody>
        </table>
      </div>
    </div>
  `;
}

registerTabRenderer('tax', renderTax);

// =========================================================================
// Crypto tab — per-coin holdings, income, activity timeline, conversions
// =========================================================================

function isCryptoSymbol(sym) {
  return !!sym && sym.endsWith('-USD');
}

function renderCrypto() {
  const root = document.getElementById('cryptoContent');
  if (!root) return;

  // Prefer pre-computed analytics
  const cr = ANALYTICS.crypto || {};
  const perCoin = cr.per_coin || null;
  const preRecent = cr.recent_activity || null;
  const preConvs = cr.conversions || null;
  const stats = cr.stats || null;

  // Crypto txns + holdings (used for fallback aggregation and by
  // the rest of the renderer)
  const cryptoTxns = txns.filter(t => isCryptoSymbol(t.symbol));
  const cryptoHoldings = holdingsByAsset.filter(h => isCryptoSymbol(h.symbol));

  // byCoin comes straight from the precomputed analytics (per_coin is a
  // 1:1 structural match — see analytics/crypto.py).  The old in-JS
  // re-aggregation was a dead fallback for pre-analytics exports and a
  // recompute-divergence hazard; removed.
  const byCoin = {};
  for (const c of (perCoin || [])) byCoin[c.symbol] = { ...c };

  const coinRows = Object.values(byCoin)
    .sort((a, b) => (b.value || 0) - (a.value || 0))
    .map(c => {
      const valStr = c.value != null ? fmtMoney(c.value) : '—';
      const basisStr = c.basis != null ? fmtMoney(c.basis) : '—';
      const urStr = c.unrealized != null
        ? `<span class="${c.unrealized >= 0 ? 'positive' : 'negative'}">${fmtSigned(c.unrealized)}</span>`
        : '—';
      const rCls = c.realized > 0 ? 'positive' : (c.realized < 0 ? 'negative' : '');
      return `<tr>
        <td><b>${symLabel(c.symbol)}</b></td>
        <td class="num">${c.quantity != null ? c.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 }) : '—'}</td>
        <td class="num">${c.price != null ? fmtMoney(c.price, 4) : '—'}</td>
        <td class="num">${valStr}</td>
        <td class="num">${basisStr}</td>
        <td class="num">${urStr}</td>
        <td class="num"><span class="${rCls}">${fmtSigned(c.realized)}</span></td>
        <td class="num">${fmtMoney(c.income)}</td>
        <td class="num">${c.txn_count}</td>
      </tr>`;
    }).join('');

  // Aggregates — analytics provides these pre-computed
  const totalValue = stats ? stats.total_value : cryptoHoldings.reduce((s, h) => s + (h.value || 0), 0);
  const totalBasis = stats ? stats.total_basis : cryptoHoldings.reduce((s, h) => s + (h.cost_basis || 0), 0);
  const totalUnrealized = stats ? stats.total_unrealized : (totalValue - totalBasis);
  const totalRealized = stats ? stats.total_realized
    : cryptoTxns.reduce((s, t) => s + (t.realized_gain || 0), 0);
  const totalIncome = stats ? stats.total_income
    : cryptoTxns.filter(t => t.action === 'Reward' || t.action === 'Interest')
      .reduce((s, t) => s + (t.amount || 0), 0);
  const txnCount = stats ? stats.txn_count : cryptoTxns.length;
  const coinCount = stats ? stats.coins_ever : Object.keys(byCoin).length;

  const statCards = [
    { label: 'Crypto Value', value: fmtMoney(totalValue), cls: 'positive' },
    { label: 'Crypto Basis', value: fmtMoney(totalBasis) },
    {
      label: 'Unrealized', value: fmtSigned(totalUnrealized),
      cls: totalUnrealized >= 0 ? 'positive' : 'negative'
    },
    {
      label: 'Realized (all-time)', value: fmtSigned(totalRealized),
      cls: totalRealized >= 0 ? 'positive' : 'negative'
    },
    { label: 'Staking / Reward Income', value: fmtMoney(totalIncome) },
    { label: 'Coins Held / Ever', value: `${cryptoHoldings.length} / ${coinCount}` },
    { label: 'Transactions', value: txnCount.toString() },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Recent activity (last 30 crypto txns)
  const recent = preRecent || [...cryptoTxns]
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 30);
  const recentRows = recent.map(t => {
    const actionColor = ACTION_COLORS[t.action] || '';
    const actionSpan = actionColor
      ? `<span style="color:${actionColor}">${t.action || ''}</span>`
      : (t.action || '');
    const rg = t.realized_gain;
    const rgStr = typeof rg === 'number'
      ? `<span class="${rg >= 0 ? 'positive' : 'negative'}">${fmtSigned(rg)}</span>`
      : '';
    return `<tr>
      <td>${t.date || ''}</td>
      <td><b>${symLabel(t.symbol)}</b></td>
      <td>${actionSpan}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
      <td class="num">${fmtMoney(t.price, 4)}</td>
      <td class="num">${fmtMoney(t.amount)}</td>
      <td class="num">${rgStr}</td>
    </tr>`;
  }).join('');

  // Conversions: actions containing "Convert" / "Wrap" / "Unwrap"
  const convActions = new Set(['Convert In', 'Convert Out', 'Wrap Asset In', 'Wrap Asset Out', 'Unwrap In', 'Unwrap Out']);
  // For display, use raw_action if we still have it; otherwise the normalized.
  const conversions = preConvs || cryptoTxns.filter(t =>
    convActions.has(t.action) || convActions.has(t.raw_action || '') || /Convert|Wrap|Unwrap/i.test(t.raw_action || ''));
  const convRows = conversions
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 30)
    .map(t => `<tr>
      <td>${t.date || ''}</td>
      <td><b>${symLabel(t.symbol)}</b></td>
      <td>${t.action}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
      <td>${(t.description || '').slice(0, 80)}</td>
    </tr>`).join('');

  root.innerHTML = `
    ${statsHtml}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Per-Coin</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Coin</th>
          <th class="num">Quantity</th>
          <th class="num">Price</th>
          <th class="num">Value</th>
          <th class="num">Basis</th>
          <th class="num">Unrealized</th>
          <th class="num">Realized</th>
          <th class="num">Income</th>
          <th class="num">Txns</th>
        </tr></thead>
        <tbody>${coinRows || '<tr><td colspan="9" style="color:var(--text-dim);padding:12px;">No crypto activity.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Recent Crypto Activity</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Date</th><th>Coin</th><th>Action</th>
          <th class="num">Quantity</th><th class="num">Price</th><th class="num">Amount</th><th class="num">Realized</th>
        </tr></thead>
        <tbody>${recentRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Conversions / Wraps</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Date</th><th>Coin</th><th>Action</th><th class="num">Quantity</th><th>Note</th>
        </tr></thead>
        <tbody>${convRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">No conversion/wrap events recorded.</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

registerTabRenderer('crypto', renderCrypto);

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
  // Using nearest is at most ~15 days off for a monthly cadence and
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
  // Try to find a matching analytics summary first
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

// --- Trading activity heatmap (Performance tab) ----------------------------
function _buildTradingHeatmapSection() {
  const hm = ANALYTICS.trading_heatmap || {};
  const rows = hm.by_date || [];
  if (!rows.length) return '';
  const maxCount = hm.max_count || 1;
  const byDate = {};
  rows.forEach(r => { byDate[r.date] = r; });

  // Range: one year back from last_date
  const lastStr = hm.last_date || rows[rows.length - 1].date;
  const lastD = new Date(lastStr + 'T00:00:00');
  const startD = new Date(lastD);
  startD.setFullYear(startD.getFullYear() - 1);
  // Align start to the Sunday of its week so columns line up cleanly
  while (startD.getDay() !== 0) startD.setDate(startD.getDate() - 1);

  const cells = [];
  const d = new Date(startD);
  while (d <= lastD) {
    const iso = d.toISOString().slice(0, 10);
    const rec = byDate[iso];
    let lvl = 0;
    if (rec && rec.count > 0) {
      const ratio = rec.count / maxCount;
      if (ratio > 0.75) lvl = 4;
      else if (ratio > 0.5) lvl = 3;
      else if (ratio > 0.25) lvl = 2;
      else lvl = 1;
    }
    const tip = rec
      ? `${iso}: ${rec.count} trades, ${fmtMoney(rec.volume, 0)}`
      : `${iso}: no trades`;
    cells.push(`<div class="heatmap-cell l${lvl}" title="${_htmlEsc(tip)}"></div>`);
    d.setDate(d.getDate() + 1);
  }

  const legend = `<div class="heatmap-legend">
    <span>Less</span>
    <span class="swatch heatmap-cell"></span>
    <span class="swatch heatmap-cell l1"></span>
    <span class="swatch heatmap-cell l2"></span>
    <span class="swatch heatmap-cell l3"></span>
    <span class="swatch heatmap-cell l4"></span>
    <span>More</span>
    <span style="margin-left:auto;">${hm.total_trades || 0} trades total</span>
  </div>`;

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Trading Activity</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Buy/sell/reinvest/convert counts per day — last 12 months.</span>
    </div>
    <div class="heatmap-wrap">
      <div class="heatmap-grid">${cells.join('')}</div>
      ${legend}
    </div>
  `;
}

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
  let cum = null, ann = null;
  if (windowKey === 'lifetime') {
    const summary = ANALYTICS_PERF[_analyticsFilterName(filterKey)]?.summary;
    if (summary) { cum = summary.cumulative; ann = summary.annualized; }
  }
  if (cum == null) {
    if (windowKey === 'custom') {
      const twr = computeTimeWeightedReturnForWindow(filterKey, perfTwrStart, perfTwrEnd);
      if (twr) { cum = twr.cumulative; ann = twr.annualized; }
    } else {
      const startDate = windowed[0].date;
      const endDate = windowed[windowed.length - 1].date;
      const twr = computeTimeWeightedReturnForWindow(
        filterKey,
        windowKey === 'lifetime' ? null : startDate,
        windowKey === 'lifetime' ? null : endDate,
      );
      if (twr) { cum = twr.cumulative; ann = twr.annualized; }
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
  // dominate.
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
      if (dd < mdd) {
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
  };
}

function renderPerformance() {
  const root = document.getElementById('performanceContent');
  if (!root) return;

  // Annual returns table (filtered to the selected account).  The
  // earlier "Total" version of this table was removed — it duplicated
  // the same data shown when this filter is set to Total.
  const annualByAcct = computeAnnualReturns(performanceAccountFilter);
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
  const _whole_realized = txns.reduce((s, t) => s + (t.realized_gain || 0), 0);
  const _whole_unrealized = holdingsByAccount.reduce(
    (s, h) => s + (h.unrealized_gain || 0), 0);
  const _whole_value = holdingsByAccount.reduce(
    (s, h) => s + (typeof h.value === 'number' ? h.value : 0), 0);
  const _whole_netContrib = cashSummary.net_contributed || 0;
  const _whole_totalReturn = _whole_value - _whole_netContrib;
  const _whole_totalReturnPct = _whole_netContrib > 0
    ? (_whole_totalReturn / _whole_netContrib) * 100 : null;

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
  // Window bounds — same logic as everywhere else on the tab.
  const _winRefIso = history.length ? history[history.length - 1].date : '';
  let _winLowerIso = '', _winUpperIso = _winRefIso;
  if (performanceWindow === 'custom') {
    _winLowerIso = perfTwrStart || '';
    _winUpperIso = perfTwrEnd || _winRefIso;
  } else if (performanceWindow !== 'lifetime') {
    _winLowerIso = _windowCutoffIso(performanceWindow, _winRefIso);
  }
  // Realized in window: sum per-txn realized_gain on filtered txns
  // where date is within (_winLowerIso, _winUpperIso].  A close at
  // the very start of the window doesn't count toward window-period
  // realized — it was banked before the window opened.
  const totalRealized = txns.reduce((s, t) => {
    if (!_aggMatchesTxn(t)) return s;
    const d = t.date || '';
    if (_winLowerIso && d <= _winLowerIso) return s;
    if (_winUpperIso && d > _winUpperIso) return s;
    return s + (t.realized_gain || 0);
  }, 0);
  // Net contributed in window: sum per-txn cash_flow over the same
  // filter+window.
  const netContrib = txns.reduce((s, t) => {
    if (!_aggMatchesTxn(t)) return s;
    const d = t.date || '';
    if (_winLowerIso && d <= _winLowerIso) return s;
    if (_winUpperIso && d > _winUpperIso) return s;
    return s + (t.cash_flow || 0);
  }, 0);
  // For unrealized / total value at window END, prefer snapshot
  // positions when window != lifetime so we get the as-of-window-end
  // values; for lifetime, use live holdings_by_account so the figure
  // matches the rest of the dashboard exactly.
  let totalUnrealized = 0, totalValue = 0;
  if (performanceWindow === 'lifetime' && performanceAccountFilter === null) {
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
  let _winStartValue = 0;
  if (performanceWindow !== 'lifetime' && _winLowerIso) {
    const startSnap = history.find(h => h.date >= _winLowerIso);
    if (startSnap && Array.isArray(startSnap.positions)) {
      for (const p of startSnap.positions) {
        if (_aggFilterSet && !_aggFilterSet.has(p.account_group)) continue;
        if (typeof p.value === 'number') _winStartValue += p.value;
      }
    }
  }
  const totalReturn = totalValue - _winStartValue - netContrib;
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

  const _totalReturnTitle = 'Current portfolio value minus net contributed (deposits − withdrawals).  Same formula as the Top bar and the Overview tab — the "did I make money?" answer.\n\nDoesn\'t equal Realized + Unrealized exactly because Income (dividends/interest/rewards) shows up in either side as cash without being a realized gain on a specific lot, and any cash outside Savings accounts isn\'t in the holdings value.';

  // Row 1 — fixed anchor: WHOLE PORTFOLIO, ALL-TIME.  Doesn't react
  // to any toggles.  Gives the user a stable reference point above
  // the toggles so they can compare the windowed view (row 4) to
  // their full portfolio's lifetime numbers at a glance.
  const anchorCards = [
    {
      label: 'Total Return',
      value: fmtSigned(_whole_totalReturn) + (_whole_totalReturnPct != null ? ` <span class="sub">${(_whole_totalReturnPct >= 0 ? '+' : '') + _whole_totalReturnPct.toFixed(1)}%</span>` : ''),
      cls: _whole_totalReturn >= 0 ? 'positive' : 'negative',
      title: _totalReturnTitle
    },
    { label: 'Realized', value: fmtSigned(_whole_realized) },
    { label: 'Unrealized', value: fmtSigned(_whole_unrealized) },
    { label: 'Net Contributed', value: fmtMoney(_whole_netContrib) },
  ];

  // Row 4 — same metrics, but filtered + windowed.  Plus Cumulative
  // and Annualized Return so the dollar and percentage views sit
  // side by side.
  const _winLabel = performanceWindow;
  const totalReturnCls = totalReturn >= 0 ? 'positive' : (totalReturn < 0 ? 'negative' : '');
  const filteredLabel = performanceAccountFilter === null ? '' : ` <span class="sub">${performanceAccountFilter === '__investments__' ? 'investments' : performanceAccountFilter === '__retirement__' ? 'retirement' : performanceAccountFilter === '__taxable__' ? 'taxable' : performanceAccountFilter.toLowerCase()}</span>`;
  const filteredCards = [
    {
      label: `Total Return <span class="sub">${_winLabel}</span>`,
      value: fmtSigned(totalReturn) + (totalReturnPct != null ? ` <span class="sub">${(totalReturnPct >= 0 ? '+' : '') + totalReturnPct.toFixed(1)}%</span>` : ''),
      cls: totalReturnCls,
      title: `Dollar return over the window for the active filter — Modified Dietz numerator: end value − start value − net cash flow.  Window: ${performanceWindow}.`
    },
    {
      label: `Realized <span class="sub">${_winLabel}</span>`,
      value: fmtSigned(totalRealized),
      cls: totalRealized >= 0 ? 'positive' : (totalRealized < 0 ? 'negative' : ''),
      title: `Realized gains for the active filter, on txns dated within the window.  Window: ${performanceWindow}.`
    },
    {
      label: `Unrealized <span class="sub">${_winLabel}</span>`,
      value: fmtSigned(totalUnrealized),
      cls: totalUnrealized >= 0 ? 'positive' : (totalUnrealized < 0 ? 'negative' : ''),
      title: `Unrealized P&L on positions held at window end (filtered).  For lifetime view, this is current unrealized; for shorter windows, it's the as-of-window-end snapshot.  Window: ${performanceWindow}.`
    },
    {
      label: `Net Contributed <span class="sub">${_winLabel}</span>`,
      value: fmtMoney(netContrib),
      title: `Net cash flow into the filtered account(s) during the window (deposits − withdrawals).  Window: ${performanceWindow}.`
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
    return `<div class="${cls}"><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
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
      start_date: windowedHistory[0].date,
      end_date: windowedHistory[windowedHistory.length - 1].date,
    }
    : null;
  // SPY return over the same window — uses the raw SPY close prices
  // stored on each snapshot, not the simulated SPY-benchmark dollar
  // value (which compounds with our contribution stream).  Pure
  // market return over the window is what you actually compare a
  // TWR against.
  const spyR = twr ? computeSPYReturnOverPeriod(twr.start_date, twr.end_date) : null;

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
  const benchStatCards = [
    {
      label: `Your Return (TWR) <span class="sub">${winLabel}</span>`,
      value: twrMainStr + ' ' + twrSubStr, cls: twrCls
    },
    {
      label: `SPY Return <span class="sub">${winLabel}</span>`,
      value: spyMainStr + ' ' + spySubStr, cls: spyCls
    },
    { label: 'Vs SPY (annualized)', value: vsSpyAnnStr, cls: vsSpyAnnCls },
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
    return `<div class="${cls}"><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('') + '</div>';

  // Top 10 winners (by total_gain)
  const sortedByGain = [...positions].sort((a, b) => (b.total_gain || 0) - (a.total_gain || 0));
  const winnerRows = sortedByGain.slice(0, 10).map(p => {
    const pctStr = p.pctReturn != null ? (p.pctReturn >= 0 ? '+' : '') + p.pctReturn.toFixed(1) + '%' : '—';
    return `<tr>
      <td><b>${symLabel(p.symbol)}</b></td>
      <td>${p.sector || ''}</td>
      <td class="num">${fmtMoney(p.value)}</td>
      <td class="num"><span class="positive">${fmtSigned(p.total_gain)}</span></td>
      <td class="num positive">${pctStr}</td>
    </tr>`;
  }).join('');

  // Top 10 losers (most-negative total_gain)
  const loserRows = [...sortedByGain].reverse().slice(0, 10).map(p => {
    const pctStr = p.pctReturn != null ? (p.pctReturn >= 0 ? '+' : '') + p.pctReturn.toFixed(1) + '%' : '—';
    const cls = (p.total_gain || 0) < 0 ? 'negative' : '';
    return `<tr>
      <td><b>${symLabel(p.symbol)}</b></td>
      <td>${p.sector || ''}</td>
      <td class="num">${fmtMoney(p.value)}</td>
      <td class="num"><span class="${cls}">${fmtSigned(p.total_gain)}</span></td>
      <td class="num ${cls}">${pctStr}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    ${statsHtml}

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

    ${_buildDailyPnlSection()}
    ${_buildMonthlyPnlSection()}
    ${_buildDrawdownSection()}
    ${_buildTradingHeatmapSection()}

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
  `;
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
  'cost_basis', 'realized_gain', 'cash_flow', 'holding_days',
]);
const HIDDEN_BY_DEFAULT = new Set([
  'description', 'account', 'source', 'raw_action',
  // Basis-walker outputs — useful but noisy by default; visible via
  // the "show:" chip list when the user wants them.
  'cost_basis', 'realized_gain', 'cash_flow', 'holding_days', 'basis_effect',
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

// --- Stats ---
// Value / Cost Basis / Unrealized / Net Contributed / Total Return
// reflect the as-of-date when one is selected.  Realized P&L and
// Income stay LIFETIME — the current JSON doesn't slice realized or
// income activity to an as-of cutoff, and it would be misleading to
// pretend otherwise.  When the date isn't the latest, those cards get
// a "(lifetime)" sub-label so the user isn't confused.
function renderStats() {
  const el = document.getElementById('stats');

  let totalValue, costBasis, unrealized, netContrib;
  if (isAsOfLatest()) {
    totalValue = holdingsByAsset.reduce(
      (s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
    const fifo = (basisMethods.fifo && basisMethods.fifo.totals) || {};
    costBasis = fifo.cost_basis || 0;
    unrealized = fifo.unrealized_gain || 0;
    netContrib = cashSummary.net_contributed || 0;
  } else {
    const snap = getAsOfSnapshot();
    totalValue = (snap && snap.total) || 0;
    costBasis = (snap && snap.total_cost_basis) || 0;
    unrealized = +(totalValue - costBasis).toFixed(2);
    netContrib = (snap && snap.net_contributed) || 0;
  }
  const fifoLifetime = (basisMethods.fifo && basisMethods.fifo.totals) || {};
  const realized = fifoLifetime.realized_gain || 0;
  const income = cashSummary.income || 0;

  const unrealPct = costBasis > 0 ? (unrealized / costBasis) * 100 : null;
  // Total gain = what you have in accounts, minus what you put in
  // (net of withdrawals).  Realized gains and dividends are already
  // reflected in current value (either as cash in-account or as
  // reinvested shares), so don't double-count them here.
  const totalGain = totalValue - netContrib;
  const totalReturnPct = netContrib > 0 ? (totalGain / netContrib) * 100 : null;

  const gainCls = (v) => v == null ? '' : (v >= 0 ? 'positive' : 'negative');
  const lifeTag = isAsOfLatest() ? '' : ' <span class="sub">lifetime</span>';

  // Current drawdown — peak-to-trough decline as of latest snapshot.
  // Shown only when latest is in view (the as-of snapshot is what most
  // users default to).  0 means "at all-time high".
  const dd = (ANALYTICS.drawdown && ANALYTICS.drawdown.current_drawdown_pct) || 0;
  const ddPct = dd * 100;
  const ddDisplay = ddPct >= -0.005 ? 'at ATH' : ddPct.toFixed(2) + '%';
  const ddCls = ddPct < -0.005 ? 'negative' : 'positive';

  // Trimmed from the original 12-card row:
  //   - Value & Total Return are already in the Top bar (always visible)
  //     so duplicating them here was clutter.
  //   - Transactions / Accounts / Symbols / Date Range are metadata
  //     that doesn't drive any decision — moved out for a cleaner
  //     focal area.  Transaction count is still on the Transactions
  //     tab; account / symbol lists are throughout the dashboard.
  const cards = [
    { label: 'Cost Basis', value: fmtMoney(costBasis) },
    {
      label: 'Unrealized P&L', value: fmtSigned(unrealized) + (unrealPct != null ? ` <span class="sub">${fmtPct(unrealPct)}</span>` : ''),
      cls: gainCls(unrealized)
    },
    { label: 'Realized P&L', value: fmtSigned(realized) + lifeTag, cls: gainCls(realized) },
    { label: 'Net Contributed', value: fmtMoney(netContrib) },
    { label: 'Income', value: fmtMoney(income) + lifeTag },
    { label: 'Current Drawdown', value: ddDisplay + ' <span class="sub">from peak</span>', cls: ddCls },
  ];
  el.innerHTML = cards.map(c => {
    const cls = c.cls ? `stat-card ${c.cls}` : 'stat-card';
    return `<div class="${cls}"><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('');
}
renderStats();

// --- Filter bar with popover dropdowns ---
const filterBar = document.getElementById('filterBar');

function renderFilterBar() {
  filterBar.innerHTML = FILTER_FIELDS.map(field => {
    const count = filterState[field].size;
    const hasCls = count > 0 ? ' has-filter' : '';
    const badge = count > 0 ? `<span class="badge">${count}</span>` : '';
    const isOpen = openPopover === field;

    let popover = '';
    if (isOpen) {
      const btns = filterValues[field].map(v => {
        const esc = String(v).replace(/"/g, '&quot;');
        const cls = filterState[field].has(String(v)) ? 'tbtn active' : 'tbtn';
        return `<button class="${cls}" data-field="${field}" data-val="${esc}">${v}</button>`;
      }).join('');
      popover = `<div class="popover" data-popover="${field}">${btns}</div>`;
    }

    return `<div style="position:relative;display:inline-block;">` +
      `<button class="fbtn${hasCls}" data-toggle="${field}">${field}${badge}</button>` +
      popover + `</div>`;
  }).join('');
}
renderFilterBar();

filterBar.addEventListener('click', e => {
  e.stopPropagation();
  // Click on a field button to open/close its popover
  const fbtn = e.target.closest('.fbtn');
  if (fbtn) {
    const field = fbtn.dataset.toggle;
    openPopover = openPopover === field ? null : field;
    renderFilterBar();
    renderColToggle();
    return;
  }
  // Click on a value toggle inside a popover
  const tbtn = e.target.closest('.tbtn');
  if (tbtn) {
    const field = tbtn.dataset.field;
    const val = tbtn.dataset.val;
    const selected = filterState[field];
    if (selected.has(val)) { selected.delete(val); }
    else { selected.add(val); }
    renderFilterBar();
    renderTable();
    return;
  }
});

// Close filter popover on outside click.
document.addEventListener('click', e => {
  if (openPopover && !e.target.closest('.filter-bar')) {
    openPopover = null;
    renderFilterBar();
  }
});

// --- Column visibility ---
// Inline list of hidden columns next to the search box; click a chip
// to re-show that column.  An "x" inside each visible header hides
// that column.  Replaces the old popover-behind-a-button design.
const colWrap = document.getElementById('colToggleWrap');

function renderColToggle() {
  const hidden = columns.filter(c => !colVisible[c]);
  if (hidden.length === 0) {
    colWrap.innerHTML = '';
    return;
  }
  colWrap.innerHTML =
    `<span class="label">show:</span>` +
    hidden.map(col =>
      `<button class="col-chip" data-col="${col}" title="Show ${col} column">${col}</button>`
    ).join('');
}
renderColToggle();

colWrap.addEventListener('click', e => {
  const chip = e.target.closest('.col-chip');
  if (!chip) return;
  const col = chip.dataset.col;
  colVisible[col] = true;
  renderColToggle();
  renderHeader();
  renderTable();
});

// --- Header ---
const headerRow = document.getElementById('headerRow');
let sortCol = columns[0], sortAsc = true;

function renderHeader() {
  const vis = visibleCols();
  headerRow.innerHTML = vis.map(col => {
    const cls = NUMERIC_FIELDS.has(col) ? ' class="num"' : '';
    const arrow = sortCol === col ? (sortAsc ? ' ▲' : ' ▼') : '';
    return `<th${cls} data-col="${col}">${col}<span class="arrow">${arrow}</span>` +
      `<span class="col-x" data-hide="${col}" title="Hide ${col} column">×</span></th>`;
  }).join('');
}
renderHeader();

headerRow.addEventListener('click', e => {
  // "x" inside the header → hide the column.  Don't fall through to sort.
  const x = e.target.closest('.col-x');
  if (x) {
    e.stopPropagation();
    const col = x.dataset.hide;
    colVisible[col] = false;
    // If we just hid the active sort column, snap the sort back to the
    // first visible column so the table doesn't sort by an invisible
    // field (which is confusing).
    if (sortCol === col) {
      const firstVis = columns.find(c => colVisible[c]);
      if (firstVis) { sortCol = firstVis; sortAsc = true; }
    }
    renderColToggle();
    renderHeader();
    renderTable();
    return;
  }
  const th = e.target.closest('th');
  if (!th) return;
  const col = th.dataset.col;
  if (sortCol === col) sortAsc = !sortAsc;
  else { sortCol = col; sortAsc = true; }
  renderHeader();
  renderTable();
});

// --- Search ---
const searchInput = document.getElementById('search');
const symbolFilterInput = document.getElementById('symbolFilter');
searchInput.addEventListener('input', () => renderTable());
symbolFilterInput.addEventListener('input', () => renderTable());

// --- Date-range filter ---
// Two pickers + quick-range chips ("30d", "90d", "YTD", "1y", "All").
// Empty pickers = no bound on that side; filtering is inclusive.
const dateFromInput = document.getElementById('dateFrom');
const dateToInput = document.getElementById('dateTo');
let _activeQuickRange = '';

function _setDateRange(from, to, quickKey) {
  dateFromInput.value = from || '';
  dateToInput.value = to || '';
  _activeQuickRange = quickKey || '';
  document.querySelectorAll('.date-range-group .date-quick').forEach(b => {
    b.classList.toggle('active', b.dataset.range === _activeQuickRange);
  });
  renderTable();
}

dateFromInput.addEventListener('input', () => {
  _activeQuickRange = '';
  document.querySelectorAll('.date-range-group .date-quick').forEach(b => b.classList.remove('active'));
  renderTable();
});
dateToInput.addEventListener('input', () => {
  _activeQuickRange = '';
  document.querySelectorAll('.date-range-group .date-quick').forEach(b => b.classList.remove('active'));
  renderTable();
});

document.querySelectorAll('.date-range-group .date-quick').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.range;
    const today = new Date();
    const isoDate = (d) => d.toISOString().slice(0, 10);
    const todayIso = isoDate(today);
    if (key === 'all') {
      _setDateRange('', '', 'all');
    } else if (key === '30d') {
      const d = new Date(today); d.setDate(d.getDate() - 30);
      _setDateRange(isoDate(d), todayIso, '30d');
    } else if (key === '90d') {
      const d = new Date(today); d.setDate(d.getDate() - 90);
      _setDateRange(isoDate(d), todayIso, '90d');
    } else if (key === 'ytd') {
      _setDateRange(`${today.getFullYear()}-01-01`, todayIso, 'ytd');
    } else if (key === '1y') {
      const d = new Date(today); d.setFullYear(d.getFullYear() - 1);
      _setDateRange(isoDate(d), todayIso, '1y');
    }
  });
});

// --- Table rendering ---
const tbody = document.getElementById('tbody');
const countPill = document.getElementById('countPill');

function formatCell(val, col) {
  if (val == null || val === '') return '';
  if (NUMERIC_FIELDS.has(col)) {
    const n = typeof val === 'number' ? val : parseFloat(val);
    if (isNaN(n)) return val;
    const formatted = col === 'quantity'
      ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })
      : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const cls = n < 0 ? 'negative' : (n > 0 ? 'positive' : '');
    return `<span class="${cls}">${formatted}</span>`;
  }
  const s = String(val);
  if (col === 'symbol') {
    return symLabel(s);
  }
  if (col === 'action' && ACTION_COLORS[s]) {
    return `<span style="color:${ACTION_COLORS[s]}">${s}</span>`;
  }
  if (col === 'account_group' && ACCOUNT_COLORS[s]) {
    return `<span style="color:${ACCOUNT_COLORS[s]}">${s}</span>`;
  }
  return s;
}

function renderTable() {
  const query = searchInput.value.toLowerCase();
  const symbolQuery = symbolFilterInput.value.toLowerCase().trim();
  const dateFrom = dateFromInput.value;   // "" or "YYYY-MM-DD"
  const dateTo = dateToInput.value;
  const vis = visibleCols();

  let filtered = txns.filter(t => {
    for (const [field, selected] of Object.entries(filterState)) {
      if (selected.size > 0 && !selected.has(String(t[field]))) return false;
    }
    if (symbolQuery) {
      const symbol = String(t.symbol ?? '').toLowerCase();
      if (!symbol.includes(symbolQuery)) return false;
    }
    if (dateFrom || dateTo) {
      const d = String(t.date ?? '');
      if (dateFrom && d < dateFrom) return false;
      if (dateTo && d > dateTo) return false;
    }
    if (query) {
      const haystack = columns.map(c => String(t[c] ?? '')).join(' ').toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  // Sort
  filtered.sort((a, b) => {
    let va = a[sortCol] ?? '', vb = b[sortCol] ?? '';
    if (NUMERIC_FIELDS.has(sortCol)) {
      va = typeof va === 'number' ? va : parseFloat(va) || 0;
      vb = typeof vb === 'number' ? vb : parseFloat(vb) || 0;
    } else {
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
    }
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return 0;
  });

  countPill.textContent = `${filtered.length} / ${txns.length}`;

  const show = filtered.slice(0, 5000);
  tbody.innerHTML = show.map(t =>
    '<tr>' + vis.map(col => {
      const cls = NUMERIC_FIELDS.has(col) ? ' class="num"' : '';
      return `<td${cls}>${formatCell(t[col], col)}</td>`;
    }).join('') + '</tr>'
  ).join('');

  if (filtered.length > 5000) {
    tbody.innerHTML += `<tr><td colspan="${vis.length}" style="color:var(--yellow);text-align:center;">
      Showing 5,000 of ${filtered.length.toLocaleString()} rows. Use filters to narrow.</td></tr>`;
  }
}

renderTable();

// Honor initial URL hash — deferred to here (bottom of script) so
// every `const`/`let` declaration has already executed.  If the URL
// is `dashboard.html#overview`, activateTab() calls renderHistory()
// which reads state declared further up the file (seriesHidden,
// historySelection, etc.).  Running initTab at the TOP would hit
// a TDZ ReferenceError on refresh and leave the page blank — the
// original symptom this block fixes.
(function initTab() {
  const name = location.hash.slice(1);
  if (name && document.getElementById('tab-' + name)) {
    activateTab(name, { replace: false });
  }
})();