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
// "2026-07-02T07:09:18" → "2026-07-02 07:09" (seconds are noise here)
document.getElementById('generated').textContent =
  (DATA.generated || '').replace('T', ' ').slice(0, 16);

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

// =========================================================================
// Per-lot inventory (ANALYTICS.lots) — expandable lot detail under each
// Holdings-by-Asset row.  Computed by src/analytics/lots.py from the
// same walker state as the cost-basis figures; the JS only renders.
// Rows expand only at the LATEST as-of date — the lot export describes
// today's pool, not a historical snapshot.
// =========================================================================
const LOTS_BY_KEY = {};
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
  return `<span${imminent ? ' style="color:var(--yellow);"' : ''} title="Long-term on ${l.lt_eligible_date}">${l.days_to_lt}d → LT</span>`;
}

function lotDetailHtml(p, colspan) {
  const fmtQty = q => q.toLocaleString(undefined, { maximumFractionDigits: 8 });
  const rows = (p.lots || []).map(l => `<tr>
      <td>${l.date || '—'}</td>
      <td class="num">${fmtQty(l.qty)}</td>
      <td class="num">${l.basis_per_share != null ? fmtMoney(l.basis_per_share, 2) : '—'}</td>
      <td class="num">${fmtMoney(l.cost_basis)}</td>
      <td class="num">${l.value != null ? fmtMoney(l.value) : '—'}</td>
      <td class="num">${l.unrealized_gain != null ? `<span class="${l.unrealized_gain < 0 ? 'negative' : 'positive'}">${fmtSigned(l.unrealized_gain)}</span>` : '—'}</td>
      <td class="num">${l.unrealized_pct != null ? fmtPct(l.unrealized_pct) : '—'}</td>
      <td class="num">${l.days_held != null ? l.days_held + 'd' : '—'}</td>
      <td>${lotTermCell(p, l)}</td>
    </tr>`);
  if (p.micro) {
    rows.push(`<tr>
      <td style="color:var(--text-dim);" title="Lots under $1 (reward/interest dust) folded into one row.  Totals include them.">··· ${p.micro.count} micro lot(s)</td>
      <td class="num" style="color:var(--text-dim);">${fmtQty(p.micro.qty)}</td>
      <td></td>
      <td class="num" style="color:var(--text-dim);">${fmtMoney(p.micro.cost_basis)}</td>
      <td class="num" style="color:var(--text-dim);">${p.micro.value != null ? fmtMoney(p.micro.value) : '—'}</td>
      <td colspan="4"></td>
    </tr>`);
  }
  return `<tr class="lot-detail"><td colspan="${colspan}">
    <div class="lot-detail-inner">
      <table class="lots-table">
        <thead><tr>
          <th>acquired</th><th class="num">qty</th><th class="num">basis/share</th>
          <th class="num">cost basis</th><th class="num">value</th>
          <th class="num">unrealized</th><th class="num">%</th>
          <th class="num">held</th><th>term</th>
        </tr></thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    </div>
  </td></tr>`;
}

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
    // Expandable per-lot detail — latest as-of only (see LOTS_BY_KEY).
    const lotKey = (row.account_group || '') + '||' + (row.symbol || '');
    const lp = isAsOfLatest() ? LOTS_BY_KEY[lotKey] : null;
    const expandable = !!(lp && ((lp.lots && lp.lots.length) || lp.micro));
    const expanded = expandable && lotsExpanded.has(lotKey);
    let rowHtml = `<tr${expandable ? ` class="lot-toggle" data-lotkey="${lotKey}"` : ''}>` + cols.map(col => {
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
        const chev = expandable
          ? `<span class="lot-chev">${expanded ? '▾' : '▸'}</span>` : '';
        html = chev + symLabel(val);
      } else if (col === 'account_group' && ACCOUNT_COLORS[val]) {
        html = `<span style="color:${ACCOUNT_COLORS[val]}">${val}</span>`;
      } else if (col === 'sector' && SECTOR_COLORS[val]) {
        html = `<span style="color:${SECTOR_COLORS[val]}">${val}</span>`;
      } else {
        html = String(val);
      }
      return `<td${cls}>${html}</td>`;
    }).join('') + '</tr>';
    if (expanded) rowHtml += lotDetailHtml(lp, cols.length);
    return rowHtml;
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

// Toggle a row's per-lot detail.  Delegated — rows re-render on every
// sort/filter change, so per-row listeners wouldn't survive.
document.getElementById('byAssetTbody').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-lotkey]');
  if (!tr) return;
  const k = tr.dataset.lotkey;
  if (lotsExpanded.has(k)) lotsExpanded.delete(k);
  else lotsExpanded.add(k);
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

