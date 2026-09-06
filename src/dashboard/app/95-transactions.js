// --- Stats ---
// Value / Cost Basis / Unrealized / Net Contributed / Total Return
// reflect the as-of-date when one is selected.  Realized P&L and
// Income stay LIFETIME — the current JSON doesn't slice realized or
// income activity to an as-of cutoff, and it would be misleading to
// pretend otherwise.  When the date isn't the latest, those cards get
// a "(lifetime)" sub-label so the user isn't confused.
function renderStats() {
  const el = document.getElementById('stats');

  // SOURCE, not just shape: `basisMethods` holds the Lot Method
  // Comparison table — four PURE single-method what-if walks.  The real
  // portfolio uses whatever method each broker actually applies, set by
  // `Lot Method` rows in metadata.csv, and that annotated walk is what
  // produces `holdings` and every per-txn `realized_gain`.
  //
  // Reading `basisMethods.fifo` here published the pure-FIFO hypothetical
  // as though it were the actual figure.  With one account on HIFO it
  // put this tab's Realized P&L roughly 19% below the same quantity on
  // Performance, and its Cost Basis / Unrealized below the Holdings
  // table — three headline numbers disagreeing with the rest of the app
  // under one unqualified label (docs/AUDIT.md F-033).  `basisMethods` is for
  // the comparison table and nothing else.
  let totalValue, costBasis, unrealized, netContrib;
  if (isAsOfLatest()) {
    totalValue = holdingsByAsset.reduce(
      (s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
    costBasis = holdingsByAsset.reduce(
      (s, r) => s + (typeof r.cost_basis === 'number' ? r.cost_basis : 0), 0);
    unrealized = holdingsByAsset.reduce(
      (s, r) => s + (typeof r.unrealized_gain === 'number' ? r.unrealized_gain : 0), 0);
    netContrib = cashSummary.net_contributed || 0;
  } else {
    const snap = getAsOfSnapshot();
    totalValue = (snap && snap.total) || 0;
    costBasis = (snap && snap.total_cost_basis) || 0;
    unrealized = +(totalValue - costBasis).toFixed(2);
    netContrib = (snap && snap.net_contributed) || 0;
  }
  // From the walker's own accumulator.  The per-txn `realized_gain`
  // annotations are rounded to cents for readability, so re-adding
  // thousands of them drifts — the reason CLAUDE.md says a published
  // basis figure never comes from re-summing them.  That sum is the
  // fallback for a JSON exported before `basis_totals` existed.
  const realized = basisTotals.realized_gain != null
    ? basisTotals.realized_gain
    : txns.reduce((s, t) => s + (t.realized_gain || 0), 0);
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
  const atAth = ddPct >= -0.005;
  // At an all-time high there is no "peak" to be down from, so the
  // "from peak" sub-label reads as nonsense — show a clean status
  // instead.  Otherwise show the % decline with the sub-label.
  const ddDisplay = atAth
    ? '0% <span class="sub">at all-time high</span>'
    : ddPct.toFixed(2) + '% <span class="sub">from peak</span>';
  const ddCls = atAth ? 'positive' : 'negative';

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
    { label: 'Current Drawdown', value: ddDisplay, cls: ddCls },
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
    const sorted = sortCol === col
      ? ` aria-sort="${sortAsc ? 'ascending' : 'descending'}"` : '';
    return `<th scope="col"${cls} data-col="${col}"${sorted} tabindex="0" role="columnheader">${col}<span class="arrow">${arrow}</span>` +
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