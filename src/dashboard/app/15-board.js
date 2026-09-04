// =========================================================================
// Positions board — the Holdings-by-Asset section's alternate layout.
//
// A monitoring view rather than a ledger view: one row per position with
// mark, market value and P&L over a selectable window, arranged as one
// to three independently-sorted panes so "biggest movers up" and
// "biggest movers down" can sit beside the value-ranked list.
//
// Every figure here is READ, never derived.  The window columns come
// from `analytics.position_pnl` (flow-adjusted per-lot P&L — see that
// module for why a constant-share move is the wrong reading once a
// position is traded inside the window), and the level columns come
// from the same `holdings_by_account` rows the table layout renders.
// The board and the table are two arrangements of one dataset; they
// cannot disagree because neither computes anything.
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
// and 2 are the stacked movers.  Defaults reproduce the three-window
// arrangement this view was built for — biggest gainers above biggest
// losers — and every one of them is click-to-re-sort afterwards.
let boardLayout = 'table';          // 'table' | 'board'
let boardPaneCount = 3;             // 1 | 2 | 3
let boardGroupBy = 'symbol';        // 'symbol' | 'account'
let boardAllWindows = false;        // append every window's $ column
let boardSearch = '';
let boardAccountFilter = '';
const boardPanes = [
  { sort: 'value',    asc: false, win: DEFAULT_WINDOW, limit: 0 },
  { sort: 'win_pnl',  asc: false, win: DEFAULT_WINDOW, limit: 12 },
  { sort: 'win_pnl',  asc: true,  win: DEFAULT_WINDOW, limit: 12 },
];

// Board config survives a reload where the browser allows it.  A
// dashboard opened from disk can be origin-null, where touching
// localStorage throws rather than returning nothing — so every access
// is guarded and a failure just means the defaults above.
const BOARD_STORE_KEY = 'fin.board.v1';
function boardSaveState() {
  try {
    localStorage.setItem(BOARD_STORE_KEY, JSON.stringify({
      layout: boardLayout, panes: boardPaneCount, groupBy: boardGroupBy,
      all: boardAllWindows, cfg: boardPanes,
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
    boardAllWindows = !!s.all;
    if (Array.isArray(s.cfg)) {
      s.cfg.forEach((c, i) => {
        if (!boardPanes[i] || !c) return;
        // Validate against the live column/window sets: a stored config
        // from an older bundle must not resurrect a column that no
        // longer exists and leave a pane sorting by undefined.
        if (BOARD_COLS.some(col => col.key === c.sort)) boardPanes[i].sort = c.sort;
        if (PPNL_WINDOW_LABEL[c.win]) boardPanes[i].win = c.win;
        boardPanes[i].asc = !!c.asc;
        if (typeof c.limit === 'number' && c.limit >= 0) boardPanes[i].limit = c.limit;
      });
    }
  } catch (e) { /* corrupt entry — defaults are fine */ }
}

// Column catalog.  `win: true` marks the pair whose meaning depends on
// the pane's selected window; everything else is a level and reads the
// same in every pane.
const BOARD_COLS = [
  { key: 'symbol',       label: 'Symbol',   num: false },
  { key: 'account_group', label: 'Account', num: false, groupOnly: 'account' },
  { key: 'quantity',     label: 'Qty',      num: true, fmt: 'qty' },
  { key: 'value',        label: 'Mkt val',  num: true, fmt: 'money' },
  { key: 'price',        label: 'Mark',     num: true, fmt: 'money' },
  { key: 'win_pnl',      label: 'open P&L', num: true, fmt: 'signed', win: true },
  { key: 'win_pct',      label: 'open P&L %', num: true, fmt: 'pct', win: true },
  { key: 'open_pnl',     label: 'Open P&L', num: true, fmt: 'signed' },
  { key: 'open_pnl_pct', label: 'Open P&L %', num: true, fmt: 'pct' },
];

function boardHasData() {
  return !!(PPNL && (PPNL.by_symbol || []).length);
}

// One row per position, already merged with the pane's window.  The
// window fields are resolved per pane (each pane may show a different
// one), so this takes the window key rather than baking one in.
function boardRows(winKey) {
  if (!boardHasData()) return [];
  const src = boardGroupBy === 'account' ? PPNL.by_account : PPNL.by_symbol;
  const rows = [];
  for (const r of src) {
    const w = (r.windows || {})[winKey] || null;
    rows.push({
      symbol: r.symbol,
      account_group: r.account_group || (r.accounts || []).join(', '),
      accounts: r.accounts || (r.account_group ? [r.account_group] : []),
      quantity: r.quantity,
      value: r.value,
      price: r.price,
      // null (not 0) when the window could not be priced — the
      // formatter renders it as an em dash so an unknown move never
      // reads as a flat one.
      win_pnl: w ? w.pnl : null,
      win_pct: w ? w.pct : null,
      open_pnl: r.open_pnl,
      open_pnl_pct: r.open_pnl_pct,
      traded: (r.traded_in || []).includes(winKey),
    });
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

function boardVisibleCols() {
  return BOARD_COLS.filter(c => !c.groupOnly || c.groupOnly === boardGroupBy);
}

function boardFmt(col, row) {
  const v = row[col.key];
  if (col.key === 'symbol') {
    const badge = row.traded
      ? ` <span class="board-traded" title="This position was traded inside the window. The figure covers only the shares still open — realized gain on shares sold is not in this column.">•</span>`
      : '';
    return symLabel(v) + badge;
  }
  if (col.key === 'account_group') {
    const c = ACCOUNT_COLORS[v];
    return c ? `<span style="color:${c}">${_htmlEsc(String(v))}</span>` : _htmlEsc(String(v || ''));
  }
  if (v == null || (typeof v === 'number' && isNaN(v))) {
    return '<span class="board-na" title="No price available for this window’s start date, so the move is unknown — not zero.">—</span>';
  }
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (isNaN(n)) return _htmlEsc(String(v));
  const cls = n < 0 ? 'negative' : (n > 0 ? 'positive' : '');
  let text;
  if (col.fmt === 'qty') {
    text = n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 8 });
    return `<span>${text}</span>`;            // quantity is not a gain
  } else if (col.fmt === 'money') {
    text = fmtMoney(n);
    return `<span>${text}</span>`;            // a level, not a move
  } else if (col.fmt === 'signed') {
    text = (n > 0 ? '+' : '') + fmtMoney(n);
  } else {
    text = (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }
  const arrow = n > 0 ? '▲' : (n < 0 ? '▼' : '');
  return `<span class="${cls}">${arrow ? arrow + ' ' : ''}${text}</span>`;
}

function boardSortRows(rows, cfg) {
  const col = BOARD_COLS.find(c => c.key === cfg.sort) || BOARD_COLS[0];
  return [...rows].sort((a, b) => {
    let va = a[cfg.sort], vb = b[cfg.sort];
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
  const rows = boardSortRows(boardRows(cfg.win), cfg);
  const shown = cfg.limit > 0 ? rows.slice(0, cfg.limit) : rows;
  const cols = boardVisibleCols();
  const label = PPNL_WINDOW_LABEL[cfg.win] || cfg.win.toUpperCase();

  const chips = PPNL_WINDOWS.map(w =>
    `<button class="tbtn board-chip${w.key === cfg.win ? ' active' : ''}"
             title="Measured from the close on ${w.start_date}"
             onclick="setBoardPaneWindow(${paneIdx}, '${w.key}')">${w.label}</button>`
  ).join('');

  const head = cols.map(c => {
    const heading = c.win ? `${label} ${c.label}` : c.label;
    const active = cfg.sort === c.key;
    const arrow = active ? (cfg.asc ? ' ▲' : ' ▼') : '';
    return `<th class="${c.num ? 'num ' : ''}board-th${active ? ' sorted' : ''}"
                onclick="setBoardPaneSort(${paneIdx}, '${c.key}')"
                >${_htmlEsc(heading)}<span class="arrow">${arrow}</span></th>`;
  }).join('');

  // Every window's $ column, appended — the "compare the timeframes"
  // view, as opposed to the chips' "pick one timeframe" view.
  const extraHead = boardAllWindows ? PPNL_WINDOWS.map(w =>
    `<th class="num board-th board-allcol">${w.label}</th>`).join('') : '';

  const body = shown.map(r => {
    const cells = cols.map(c =>
      `<td class="${c.num ? 'num' : ''}">${boardFmt(c, r)}</td>`).join('');
    let extra = '';
    if (boardAllWindows) {
      const src = boardGroupBy === 'account' ? PPNL.by_account : PPNL.by_symbol;
      const full = src.find(x => x.symbol === r.symbol
        && (boardGroupBy !== 'account' || x.account_group === r.account_group));
      extra = PPNL_WINDOWS.map(w => {
        const win = full ? (full.windows || {})[w.key] : null;
        return `<td class="num board-allcol">${boardFmt(
          { key: '_w', fmt: 'signed', num: true }, { _w: win ? win.pnl : null })}</td>`;
      }).join('');
    }
    return `<tr>${cells}${extra}</tr>`;
  }).join('');

  const total = shown.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  const winTotal = shown.reduce((s, r) => s + (typeof r.win_pnl === 'number' ? r.win_pnl : 0), 0);
  const capped = cfg.limit > 0 && rows.length > cfg.limit;

  return `
    <div class="board-pane" data-pane="${paneIdx}">
      <div class="board-pane-head">
        <div class="board-chips">${chips}</div>
        <div class="board-pane-meta">
          <span class="pill">${shown.length}${capped ? ` / ${rows.length}` : ''}</span>
          <span class="board-pane-total">${fmtMoney(total)}</span>
          <span class="board-pane-total ${winTotal < 0 ? 'negative' : (winTotal > 0 ? 'positive' : '')}"
                title="Sum of the ${label} column over the rows shown">${
                  (winTotal > 0 ? '+' : '') + fmtMoney(winTotal)}</span>
        </div>
      </div>
      <div class="board-scroll">
        <table>
          <thead><tr>${head}${extraHead}</tr></thead>
          <tbody>${body || `<tr><td colspan="${cols.length + (boardAllWindows ? PPNL_WINDOWS.length : 0)}" class="board-empty">No positions match.</td></tr>`}</tbody>
        </table>
      </div>
    </div>`;
}

function setBoardPaneWindow(idx, win) {
  boardPanes[idx].win = win;
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
    const c = BOARD_COLS.find(x => x.key === col);
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
  renderPositionsBoard();
}

function setBoardGroupBy(mode) {
  boardGroupBy = mode;
  boardSaveState();
  renderPositionsBoard();
}

function toggleBoardAllWindows() {
  boardAllWindows = !boardAllWindows;
  boardSaveState();
  renderPositionsBoard();
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
      <button class="tbtn${boardAllWindows ? ' active' : ''}" onclick="toggleBoardAllWindows()"
              title="Append one column per window so the timeframes can be compared side by side">All windows</button>
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
