// =========================================================================
// Positions board — the Holdings-by-Asset section's alternate layout.
//
// A monitoring view rather than a ledger view: one row per position with
// mark, market value and P&L over any combination of trailing windows,
// arranged as one to three independently-sorted panes so "biggest
// movers up" and "biggest movers down" can sit beside the value-ranked
// list.  All three panes render the SAME rows — they differ only in
// sort — so a position is never missing from one of them.
//
// Every figure here is READ, never derived.  The window columns come
// from `analytics.position_pnl` (flow-adjusted per-lot P&L — see that
// module for why a constant-share move is the wrong reading once a
// position is traded inside the window), and the level columns come
// from the same `holdings_by_account` rows the table layout renders.
// The board and the table are two arrangements of one dataset; they
// cannot disagree because neither computes anything.
//
// Window chips are INDEPENDENT toggles, not a radio group.  Each active
// window contributes a sortable $/% column pair, so "sort by 1D while
// also seeing YTD" is one click rather than a mode.  Zero active
// windows is a legitimate state — the levels-only view.
//
// LATEST-ONLY.  `position_pnl` is anchored to the newest snapshot, so
// trailing windows off a historical as-of date would be measured from
// the wrong end.  Rather than silently show today's movement under a
// past date's holdings, board mode says so and offers the way back —
// the same rule the expandable lot detail already follows.
// =========================================================================

const PPNL = ANALYTICS.position_pnl || null;
const PPNL_WINDOWS = (PPNL && PPNL.windows) || [];
const PPNL_WINDOW_LABEL = {};
const PPNL_WINDOW_START = {};
for (const w of PPNL_WINDOWS) {
  PPNL_WINDOW_LABEL[w.key] = w.label;
  PPNL_WINDOW_START[w.key] = w.start_date;
}
const DEFAULT_WINDOW = PPNL_WINDOWS.length ? PPNL_WINDOWS[0].key : '1d';

// Board layout state.  Pane 0 is the wide value-ranked list; panes 1
// and 2 are the stacked movers.  Defaults reproduce the arrangement
// this view was built for — biggest gainers above biggest losers — and
// every one of them is click-to-re-sort afterwards.
let boardLayout = 'table';          // 'table' | 'board'
let boardPaneCount = 3;             // 1 | 2 | 3
let boardGroupBy = 'symbol';        // 'symbol' | 'account'
let boardSearch = '';
let boardAccountFilter = '';
const boardPanes = [
  { sort: 'value',                   asc: false, wins: [DEFAULT_WINDOW] },
  { sort: 'w_pnl_' + DEFAULT_WINDOW, asc: false, wins: [DEFAULT_WINDOW] },
  { sort: 'w_pnl_' + DEFAULT_WINDOW, asc: true,  wins: [DEFAULT_WINDOW] },
];

// Board config survives a reload where the browser allows it.  A
// dashboard opened from disk can be origin-null, where touching
// localStorage throws rather than returning nothing — so every access
// is guarded and a failure just means the defaults above.
// v2: window selection became a list, and the movers panes lost their
// row cap.  A v1 entry is ignored rather than migrated.
const BOARD_STORE_KEY = 'fin.board.v2';
function boardSaveState() {
  try {
    localStorage.setItem(BOARD_STORE_KEY, JSON.stringify({
      layout: boardLayout, panes: boardPaneCount,
      groupBy: boardGroupBy, cfg: boardPanes,
    }));
  } catch (e) { /* private mode, file:// origin — defaults are fine */ }
}
function boardLoadState() {
  let raw = null;
  try { raw = localStorage.getItem(BOARD_STORE_KEY); } catch (e) { return; }
  if (!raw) return;
  try {
    const s = JSON.parse(raw);
    if (s.layout === 'board' || s.layout === 'table') boardLayout = s.layout;
    if ([1, 2, 3].includes(s.panes)) boardPaneCount = s.panes;
    if (s.groupBy === 'symbol' || s.groupBy === 'account') boardGroupBy = s.groupBy;
    if (Array.isArray(s.cfg)) {
      s.cfg.forEach((c, i) => {
        if (!boardPanes[i] || !c) return;
        // Validate against the live window set: a stored config from an
        // older bundle must not resurrect a window that no longer
        // exists and leave a pane rendering a column of undefineds.
        if (Array.isArray(c.wins)) {
          boardPanes[i].wins = c.wins.filter(w => PPNL_WINDOW_LABEL[w]);
        }
        boardPanes[i].asc = !!c.asc;
        if (typeof c.sort === 'string') boardPanes[i].sort = c.sort;
      });
    }
  } catch (e) { /* corrupt entry — defaults are fine */ }
  // Whatever came back, no pane may be left sorting by a column it is
  // not rendering.
  boardPanes.forEach(cfg => { cfg.sort = boardValidSort(cfg); });
}

// Level columns — the same in every pane regardless of window choice.
const BOARD_LEVEL_COLS = [
  { key: 'symbol',        label: 'Symbol',  num: false },
  { key: 'account_group', label: 'Account', num: false, groupOnly: 'account' },
  { key: 'quantity',      label: 'Qty',     num: true, fmt: 'qty' },
  { key: 'value',         label: 'Mkt val', num: true, fmt: 'money' },
  { key: 'price',         label: 'Mark',    num: true, fmt: 'money' },
];
const BOARD_TAIL_COLS = [
  { key: 'open_pnl',     label: 'Open P&L',   num: true, fmt: 'signed' },
  { key: 'open_pnl_pct', label: 'Open P&L %', num: true, fmt: 'pct' },
];

// Column list for one pane: levels, then a $/% pair per active window
// in canonical order (not click order — the timeline should read
// left-to-right the way the chips do), then the all-time pair.
function boardCols(cfg) {
  const cols = BOARD_LEVEL_COLS.filter(
    c => !c.groupOnly || c.groupOnly === boardGroupBy);
  const active = PPNL_WINDOWS.filter(w => cfg.wins.includes(w.key));
  const solo = active.length === 1;
  for (const w of active) {
    cols.push({
      key: 'w_pnl_' + w.key, num: true, fmt: 'signed', winStart: true,
      label: solo ? `${w.label} open P&L` : `${w.label} P&L`,
      title: `Open P&L since the close on ${w.start_date}`,
    });
    cols.push({
      key: 'w_pct_' + w.key, num: true, fmt: 'pct',
      label: solo ? `${w.label} open P&L %` : `${w.label} %`,
      title: `Open P&L since the close on ${w.start_date}, against the position's value then`,
    });
  }
  return cols.concat(BOARD_TAIL_COLS);
}

// The sort column a pane can actually honour.  A window chip switched
// off takes its columns with it; rather than sort by a value no row
// displays, fall back to the value ranking.
function boardValidSort(cfg) {
  return boardCols(cfg).some(c => c.key === cfg.sort) ? cfg.sort : 'value';
}

function boardHasData() {
  return !!(PPNL && (PPNL.by_symbol || []).length);
}

// One row per position, carrying EVERY window flattened — panes render
// whichever subset their chips select, so the rows are built once and
// shared.
function boardRows() {
  if (!boardHasData()) return [];
  const src = boardGroupBy === 'account' ? PPNL.by_account : PPNL.by_symbol;
  const rows = [];
  for (const r of src) {
    const row = {
      symbol: r.symbol,
      account_group: r.account_group || (r.accounts || []).join(', '),
      accounts: r.accounts || (r.account_group ? [r.account_group] : []),
      quantity: r.quantity,
      value: r.value,
      price: r.price,
      open_pnl: r.open_pnl,
      open_pnl_pct: r.open_pnl_pct,
      traded_in: r.traded_in || [],
    };
    for (const w of PPNL_WINDOWS) {
      const win = (r.windows || {})[w.key] || null;
      // null (not 0) when the window could not be priced — the
      // formatter renders it as an em dash so an unknown move never
      // reads as a flat one.
      row['w_pnl_' + w.key] = win ? win.pnl : null;
      row['w_pct_' + w.key] = win ? win.pct : null;
    }
    rows.push(row);
  }
  return rows.filter(r => {
    if (boardAccountFilter && !r.accounts.includes(boardAccountFilter)) return false;
    if (boardSearch) {
      const hay = (r.symbol + ' ' + r.accounts.join(' ')).toLowerCase();
      if (!hay.includes(boardSearch)) return false;
    }
    return true;
  });
}

function boardFmt(col, row, cfg) {
  const v = row[col.key];
  if (col.key === 'symbol') {
    // Flag rows whose figure omits realized gain: the position was
    // traded inside one of the windows on screen, so the P&L covers
    // only the shares still open.
    const hits = (row.traded_in || []).filter(w => (cfg.wins || []).includes(w));
    const badge = hits.length
      ? ` <span class="board-traded" title="Traded within ${hits.map(w => PPNL_WINDOW_LABEL[w] || w).join(', ')}. Those columns cover only the shares still open — realized gain on shares sold is not included.">&bull;</span>`
      : '';
    return symLabel(v) + badge;
  }
  if (col.key === 'account_group') {
    const c = ACCOUNT_COLORS[v];
    return c ? `<span style="color:${c}">${_htmlEsc(String(v))}</span>`
             : _htmlEsc(String(v || ''));
  }
  if (v == null || (typeof v === 'number' && isNaN(v))) {
    return '<span class="board-na" title="No price available for this window&#39;s start date, so the move is unknown — not zero.">—</span>';
  }
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (isNaN(n)) return _htmlEsc(String(v));
  if (col.fmt === 'qty') {
    return `<span>${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 })}</span>`;
  }
  if (col.fmt === 'money') {
    return `<span>${fmtMoney(n)}</span>`;   // a level, not a move
  }
  const cls = n < 0 ? 'negative' : (n > 0 ? 'positive' : '');
  const text = col.fmt === 'pct'
    ? (n > 0 ? '+' : '') + n.toFixed(2) + '%'
    : (n > 0 ? '+' : '') + fmtMoney(n);
  const arrow = n > 0 ? '▲ ' : (n < 0 ? '▼ ' : '');
  return `<span class="${cls}">${arrow}${text}</span>`;
}

function boardSortRows(rows, cfg) {
  const cols = boardCols(cfg);
  const col = cols.find(c => c.key === cfg.sort) || cols[0];
  return [...rows].sort((a, b) => {
    let va = a[col.key], vb = b[col.key];
    if (col.num) {
      // Unknown sorts to the bottom in either direction: a position we
      // could not price is not the day's biggest loser.
      const na = (va == null || isNaN(va));
      const nb = (vb == null || isNaN(vb));
      if (na && nb) return 0;
      if (na) return 1;
      if (nb) return -1;
      return cfg.asc ? va - vb : vb - va;
    }
    va = String(va ?? '').toLowerCase();
    vb = String(vb ?? '').toLowerCase();
    if (va < vb) return cfg.asc ? -1 : 1;
    if (va > vb) return cfg.asc ? 1 : -1;
    return 0;
  });
}

function boardPaneHtml(paneIdx) {
  const cfg = boardPanes[paneIdx];
  const cols = boardCols(cfg);
  const rows = boardSortRows(boardRows(), cfg);

  const chips = PPNL_WINDOWS.map(w =>
    `<button class="tbtn board-chip${cfg.wins.includes(w.key) ? ' active' : ''}"
             title="Open P&amp;L since the close on ${w.start_date} — toggles independently of the others"
             onclick="toggleBoardPaneWindow(${paneIdx}, '${w.key}')">${w.label}</button>`
  ).join('');

  const head = cols.map(c => {
    const active = cfg.sort === c.key;
    const arrow = active ? (cfg.asc ? ' ▲' : ' ▼') : '';
    const tip = c.title ? ` title="${_htmlEsc(c.title)}"` : '';
    return `<th class="${c.num ? 'num ' : ''}board-th${active ? ' sorted' : ''}${c.winStart ? ' board-wstart' : ''}"${tip}
                onclick="setBoardPaneSort(${paneIdx}, '${c.key}')"
                >${_htmlEsc(c.label)}<span class="arrow">${arrow}</span></th>`;
  }).join('');

  const body = rows.map(r =>
    '<tr>' + cols.map(c =>
      `<td class="${c.num ? 'num' : ''}${c.winStart ? ' board-wstart' : ''}">${boardFmt(c, r, cfg)}</td>`
    ).join('') + '</tr>').join('');

  // The meta bar sums market value, plus the column the pane is sorted
  // by when that column is a dollar move — so the number beside the
  // ranking is the one the ranking is about.
  const sortCol = cols.find(c => c.key === cfg.sort);
  const sumsMove = !!sortCol && sortCol.fmt === 'signed';
  const total = rows.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  const sortSum = sumsMove
    ? rows.reduce((s, r) => s + (typeof r[sortCol.key] === 'number' ? r[sortCol.key] : 0), 0)
    : null;

  return `
    <div class="board-pane" data-pane="${paneIdx}">
      <div class="board-pane-head">
        <div class="board-chips">${chips}</div>
        <div class="board-pane-meta">
          <span class="pill">${rows.length}</span>
          <span class="board-pane-total" title="Total market value of the rows shown">${fmtMoney(total)}</span>
          ${sortSum === null ? '' : `<span class="board-pane-total ${
            sortSum < 0 ? 'negative' : (sortSum > 0 ? 'positive' : '')}"
            title="Sum of the ${_htmlEsc(sortCol.label)} column over the rows shown">${
            (sortSum > 0 ? '+' : '') + fmtMoney(sortSum)}</span>`}
        </div>
      </div>
      <div class="board-scroll">
        <table>
          <thead><tr>${head}</tr></thead>
          <tbody>${body || `<tr><td colspan="${cols.length}" class="board-empty">No positions match.</td></tr>`}</tbody>
        </table>
      </div>
    </div>`;
}

// Window chips are independent toggles.  Switching one off can orphan
// the pane's sort column, so revalidate afterwards.
function toggleBoardPaneWindow(idx, win) {
  const cfg = boardPanes[idx];
  cfg.wins = cfg.wins.includes(win)
    ? cfg.wins.filter(w => w !== win)
    : cfg.wins.concat([win]);
  cfg.sort = boardValidSort(cfg);
  boardSaveState();
  renderBoardPanes();
}

function setBoardPaneSort(idx, col) {
  const cfg = boardPanes[idx];
  if (cfg.sort === col) cfg.asc = !cfg.asc;
  else {
    cfg.sort = col;
    // First click on a numeric column sorts biggest-first, which is
    // what you want from a movers list; text sorts A→Z.
    const c = boardCols(cfg).find(x => x.key === col);
    cfg.asc = !(c && c.num);
  }
  boardSaveState();
  renderBoardPanes();
}

function setBoardLayout(mode) {
  boardLayout = mode;
  boardSaveState();
  document.getElementById('btnBoardTable')?.classList.toggle('active', mode === 'table');
  document.getElementById('btnBoardBoard')?.classList.toggle('active', mode === 'board');
  const tableEl = document.getElementById('byAssetBody');
  const boardEl = document.getElementById('boardBody');
  if (tableEl) tableEl.style.display = mode === 'board' ? 'none' : '';
  if (boardEl) boardEl.style.display = mode === 'board' ? '' : 'none';
  if (mode === 'board') renderPositionsBoard();
}

function setBoardPaneCount(n) {
  boardPaneCount = n;
  boardSaveState();
  // The pane-count buttons live in the controls bar, not in a pane, so
  // redrawing only the panes left their active state frozen at whatever
  // it was on first render.  Anything that changes a value the controls
  // bar DISPLAYS has to redraw the controls bar too.
  renderBoardControls();
  renderBoardPanes();
}

function setBoardGroupBy(mode) {
  boardGroupBy = mode;
  boardPanes.forEach(cfg => { cfg.sort = boardValidSort(cfg); });
  boardSaveState();
  renderBoardControls();
  renderBoardPanes();
}

function renderPositionsBoard() {
  const host = document.getElementById('boardBody');
  if (!host) return;
  if (!boardHasData()) {
    host.innerHTML = `<div class="board-note">No position data available.</div>`;
    return;
  }
  // Board figures are anchored to the latest snapshot.  Rendering
  // today's trailing windows beside a past date's holdings would put
  // two different dates in one row, so say so instead.
  if (typeof isAsOfLatest === 'function' && !isAsOfLatest()) {
    host.innerHTML = `<div class="board-note">
      The board shows trailing windows measured from the latest prices, so it
      is only meaningful for the latest snapshot.
      <button class="tbtn" onclick="setAsOfDate(null)">Back to latest</button>
    </div>`;
    return;
  }
  if (!document.getElementById('boardPanes')) {
    host.innerHTML = `<div id="boardControls"></div><div id="boardPanes"></div>`;
  }
  renderBoardControls();
  renderBoardPanes();
}

// Controls and panes render separately so typing in the search box
// does not rebuild the input underneath the cursor.
function renderBoardControls() {
  const el = document.getElementById('boardControls');
  if (!el) return;
  const accounts = [...new Set((PPNL.by_account || []).map(r => r.account_group))].sort();
  el.innerHTML = `
    <div class="controls board-controls">
      <input type="text" id="boardSearch" placeholder="Search symbol or account..."
             value="${_htmlEsc(boardSearch)}" oninput="boardSearch = this.value.trim().toLowerCase(); renderBoardPanes();" />
      <select id="boardAccountFilter" onchange="boardAccountFilter = this.value; renderBoardPanes();">
        <option value="">All account groups</option>
        ${accounts.map(a => `<option value="${_htmlEsc(a)}"${a === boardAccountFilter ? ' selected' : ''}>${_htmlEsc(a)}</option>`).join('')}
      </select>
      <div class="toggle-group">
        <button class="tbtn${boardGroupBy === 'symbol' ? ' active' : ''}" onclick="setBoardGroupBy('symbol')">By Symbol</button>
        <button class="tbtn${boardGroupBy === 'account' ? ' active' : ''}" onclick="setBoardGroupBy('account')">By Account</button>
      </div>
      <div class="toggle-group">
        ${[1, 2, 3].map(n => `<button class="tbtn${boardPaneCount === n ? ' active' : ''}" onclick="setBoardPaneCount(${n})" title="${n} pane${n > 1 ? 's' : ''}">${n}&#9646;</button>`).join('')}
      </div>
      <span class="board-asof">as of ${_htmlEsc(PPNL.as_of || '')}</span>
    </div>`;
}

function renderBoardPanes() {
  const el = document.getElementById('boardPanes');
  if (!el) return;
  if (boardPaneCount === 1) {
    el.innerHTML = `<div class="board-grid board-grid-1">${boardPaneHtml(0)}</div>`;
  } else if (boardPaneCount === 2) {
    el.innerHTML = `<div class="board-grid board-grid-2">${boardPaneHtml(0)}${boardPaneHtml(1)}</div>`;
  } else {
    // The arrangement this view exists for: the value-ranked list at
    // full height beside a stacked pair of movers.
    el.innerHTML = `<div class="board-grid board-grid-3">
        <div class="board-col-main">${boardPaneHtml(0)}</div>
        <div class="board-col-side">${boardPaneHtml(1)}${boardPaneHtml(2)}</div>
      </div>`;
  }
}

boardLoadState();
// Apply a restored layout once the DOM exists.  setBoardLayout also
// does the first render, so board mode survives a reload without the
// Holdings tab needing its own renderer registration.
if (boardLayout === 'board') setBoardLayout('board');
