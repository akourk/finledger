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

// Chart mode.  'lines' = the multi-series line chart (default, with all
// its overlays/benchmarks/per-series pills).  'composition' = a stacked
// area of the portfolio broken down by a single dimension over time —
// answers "how has my mix evolved", which no line overlay shows.  The
// two modes have different interaction models, so composition renders
// through its own path (_renderComposition) and reuses only the axis /
// hover conventions.  Range filtering applies to both.
// Default to Composition — "how is my portfolio built and how has that
// evolved" is the more informative landing view for a buy-and-hold
// tracker than a single total line (which the top bar already shows).
let historyChartMode = 'composition';     // 'lines' | 'composition'
let historyCompositionDim = 'account';    // 'account' | 'type' | 'sector'

function setHistoryChartMode(m) {
  historyChartMode = m;
  renderHistControls();
  renderHistory();
}
function setHistoryCompositionDim(d) {
  historyCompositionDim = d;
  renderHistControls();
  renderHistory();
}

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
           aria-label="History chart range: from date"
           value="${historyCustomStart || minDate}"
           onchange="setHistoryCustomStart(this.value)">
    <span class="hist-label">To:</span>
    <input type="date" class="hist-date" min="${minDate}" max="${maxDate}"
           aria-label="History chart range: to date"
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

  // Chart-mode row (Lines / Composition).  In composition mode a
  // dimension selector (Account / Type / Sector) replaces the
  // per-series pills, and the line-only Overlay + series rows are
  // hidden since they don't apply to a stacked area.
  const composition = historyChartMode === 'composition';
  const modeBtn = (m, label) =>
    `<button class="tbtn${historyChartMode === m ? ' active' : ''}" onclick="setHistoryChartMode('${m}')">${label}</button>`;
  const dimBtn = (d, label) =>
    `<button class="tbtn${historyCompositionDim === d ? ' active' : ''}" onclick="setHistoryCompositionDim('${d}')">${label}</button>`;
  const modeRow = `
    <div class="hist-row">
      <span class="hist-label">Chart:</span>
      ${modeBtn('lines', 'Lines')}
      ${modeBtn('composition', 'Composition')}
      ${composition ? `
        <span class="hist-label" style="margin-left:16px;">By:</span>
        ${dimBtn('account', 'Account')}
        ${dimBtn('type', 'Type')}
        ${dimBtn('sector', 'Sector')}` : ''}
    </div>`;

  const overlayRow = composition ? '' : `
      <span class="hist-label" style="margin-left:16px;">Overlay:</span>
      <button class="${overlayBasisCls}" onclick="toggleHistoryOverlay('basis')">Cost Basis</button>
      <button class="${overlayGainCls}"  onclick="toggleHistoryOverlay('gain')">Unrealized Gain</button>
      <button class="${overlaySpyCls}"  onclick="toggleHistoryOverlay('spy')"  title="If every dollar you contributed had gone to SPY and stayed there (buy-and-hold), where would those dollars be now?  Filter-aware: respects the active account/type filter.  Withdrawals do not sell simulated shares — same semantics as Schwab/Fidelity 'vs index' charts.">SPY</button>
      <button class="${overlayBndCls}"  onclick="toggleHistoryOverlay('bnd')"  title="Same buy-and-hold simulation for BND (US aggregate bonds).  Filter-aware.">BND</button>
      <button class="${overlayVxusCls}" onclick="toggleHistoryOverlay('vxus')" title="Same buy-and-hold simulation for VXUS (international ex-US equities).  Filter-aware.">VXUS</button>
      <button class="${overlayContribCls}" onclick="toggleHistoryOverlay('netcontrib')">Net Contributed</button>
      <button class="${overlayYoyCls}" onclick="toggleHistoryOverlay('yoy')" title="Overlay portfolio value from one year ago at the same calendar position">Year-over-Year</button>`;

  const seriesRows = composition ? '' : `
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
    </div>`;

  el.innerHTML = `
    ${modeRow}
    <div class="hist-row">
      <span class="hist-label">Range:</span>
      ${rangePills}
      ${customRangeHtml}
      ${overlayRow}
    </div>
    ${seriesRows}
  `;

  // Update collapsed summary so the user can see what's selected without
  // expanding (e.g. "1Y · cost basis, SPY · 3 accounts").
  const status = document.getElementById('histControlsSummaryStatus');
  if (status && historyChartMode === 'composition') {
    const dimLabel = { account: 'accounts', type: 'types', sector: 'sectors' }[historyCompositionDim];
    status.textContent = `${PERF_TWR_PRESET_LABEL[historyRange] || historyRange} · composition by ${dimLabel}`;
  } else if (status) {
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

  // Composition mode renders through its own path (stacked areas), then
  // returns — the line-mode machinery below (series/overlays/benchmarks)
  // doesn't apply to it.
  if (historyChartMode === 'composition') {
    _renderComposition(svg, tooltip, legend, filteredHistory());
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

// Stacked-area composition chart: the portfolio broken down by a single
// dimension (account_group / account_type / sector) over time.  Reads
// the history snapshots' by_* fields (each sums to the total), stacks
// the categories bottom-to-top, and fills each band with its category
// color.  Self-contained — own axes/legend/hover, no overlays.
function _renderComposition(svg, tooltip, legend, hist) {
  if (!hist.length) { svg.innerHTML = ''; legend.innerHTML = ''; return; }
  const rect = svg.getBoundingClientRect();
  const W = rect.width || 800;
  const H = rect.height || 340;
  const PAD = { l: 64, r: 16, t: 12, b: 28 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const n = hist.length;
  const xOf = i => PAD.l + (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));

  const dim = historyCompositionDim;
  const field = dim === 'account' ? 'by_account_group'
    : dim === 'type' ? 'by_account_type' : 'by_sector';
  const colorMap = dim === 'account' ? ACCOUNT_COLORS
    : dim === 'type' ? TYPE_COLORS : SECTOR_COLORS;

  // Categories ordered by lifetime magnitude; fold the long tail (mostly
  // relevant for sectors) into "Other" so the stack stays legible.
  const totals = {};
  hist.forEach(h => {
    const m = h[field] || {};
    for (const k in m) if (typeof m[k] === 'number') totals[k] = (totals[k] || 0) + m[k];
  });
  let cats = Object.keys(totals).sort((a, b) => (totals[b] || 0) - (totals[a] || 0));
  const CAP = 8;
  const folded = cats.length > CAP ? cats.slice(CAP) : [];
  if (folded.length) cats = cats.slice(0, CAP);
  const drawCats = folded.length ? [...cats, '__other__'] : cats;
  const catLabel = c => c === '__other__' ? `Other (${folded.length})` : c;
  const catColor = c => c === '__other__' ? '#6b7280' : (colorMap[c] || '#9ca3af');
  const valAt = (h, c) => {
    const m = h[field] || {};
    if (c === '__other__') { let s = 0; for (const f of folded) s += (typeof m[f] === 'number' ? m[f] : 0); return s; }
    return typeof m[c] === 'number' ? m[c] : 0;
  };

  let maxTotal = 0;
  hist.forEach(h => { let s = 0; for (const c of drawCats) s += valAt(h, c); if (s > maxTotal) maxTotal = s; });
  const maxY = (maxTotal > 0 ? maxTotal : 1) * 1.02;
  const yOf = v => PAD.t + plotH - (v / maxY) * plotH;

  const parts = [];
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const v = (maxY * i) / yTicks;
    const y = yOf(v);
    parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
    parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${formatAxisMoney(v)}</text>`);
  }
  const xTicks = Math.min(6, n);
  for (let i = 0; i < xTicks; i++) {
    const idx = Math.round((i * (n - 1)) / (xTicks - 1 || 1));
    parts.push(`<text class="axis-label" x="${xOf(idx)}" y="${H - 8}" text-anchor="middle">${hist[idx].date.slice(0, 7)}</text>`);
  }
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);

  // Stack bottom-to-top: each band is a filled polygon between the
  // running cumulative bottom and bottom+value.
  const cum = new Array(n).fill(0);
  for (const c of drawCats) {
    const topPts = [];
    const botPts = [];
    for (let i = 0; i < n; i++) {
      const bottom = cum[i];
      const top = bottom + valAt(hist[i], c);
      botPts.push([xOf(i), yOf(bottom)]);
      topPts.push([xOf(i), yOf(top)]);
      cum[i] = top;
    }
    let d = 'M' + topPts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L');
    d += ' L' + botPts.reverse().map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L') + ' Z';
    parts.push(`<path d="${d}" fill="${catColor(c)}" fill-opacity="0.72" stroke="${catColor(c)}" stroke-opacity="0.9" stroke-width="0.6"/>`);
  }

  parts.push(`<line id="chartHoverV" class="hover-v" x1="0" y1="${PAD.t}" x2="0" y2="${PAD.t + plotH}" style="display:none"/>`);
  parts.push(`<rect id="chartCapture" x="${PAD.l}" y="${PAD.t}" width="${plotW}" height="${plotH}" fill="transparent"/>`);

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = parts.join('');

  // Legend (top-of-stack category listed first).  No click-to-hide in
  // composition mode — hiding a band would misrepresent the stack.
  legend.innerHTML = [...drawCats].reverse().map(c =>
    `<span class="legend-item"><span class="legend-swatch" style="background:${catColor(c)}"></span>${catLabel(c)}</span>`
  ).join('');
  legend.onclick = null;

  const capture = document.getElementById('chartCapture');
  const hoverV = document.getElementById('chartHoverV');
  capture.addEventListener('mousemove', ev => {
    const svgRect = svg.getBoundingClientRect();
    const mx = (ev.clientX - svgRect.left) * (W / svgRect.width);
    let idx = Math.round(((mx - PAD.l) / plotW) * (n - 1));
    idx = Math.max(0, Math.min(n - 1, idx));
    const x = xOf(idx);
    hoverV.setAttribute('x1', x); hoverV.setAttribute('x2', x); hoverV.style.display = '';
    const h = hist[idx];
    let total = 0; for (const c of drawCats) total += valAt(h, c);
    const rows = [...drawCats].reverse().map(c => {
      const v = valAt(h, c);
      const pct = total > 0 ? (v / total * 100) : 0;
      return `<div class="tt-row">
        <span class="tt-name"><span class="tt-swatch" style="background:${catColor(c)}"></span>${catLabel(c)}</span>
        <span>${formatMoney(v)} <span style="color:var(--text-dim);">${pct.toFixed(0)}%</span></span>
      </div>`;
    }).join('');
    tooltip.innerHTML = `<div class="tt-date">${h.date} · total ${formatMoney(total)}</div>${rows}`;
    tooltip.style.display = 'block';
    const wrapRect = document.getElementById('chartWrap').getBoundingClientRect();
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

