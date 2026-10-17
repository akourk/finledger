// =========================================================================
// Options tab
// =========================================================================

// Options tab filter state.
//   _optWindow: 'lifetime' (default), or a relative key
//               ('5y' | '3y' | '2y' | '1y' | 'ytd' | '6mo' | '3mo' | '30d'),
//               or an absolute year ('2026' | '2025' | …).
//   _optAccountFilter: null = all accounts; otherwise an account_group
//                      string to restrict the view to one broker.
// Completed-position outcomes and execution-date realized P&L are filtered
// independently. Open positions are filtered by account only.
let _optWindow = 'lifetime';
let _optAccountFilter = null;

function _setOptWindow(v) {
  _optWindow = v || 'lifetime';
  renderKeepingFocus(renderOptions);
}
function _setOptAccountFilter(v) {
  _optAccountFilter = (v === 'all' || !v) ? null : v;
  renderKeepingFocus(renderOptions);
}

// Resolve _optWindow into a concrete [start_iso, end_iso] date range,
// or null for "lifetime" (no filter).  YYYY values map to the full
// calendar year; relative keys anchor on today's date and walk back.
function _optWindowRange() {
  const today = snapshotDate();
  const isoToday = SNAPSHOT_DATE;
  const w = _optWindow;
  if (!w || w === 'lifetime') return null;
  if (/^\d{4}$/.test(w)) return [`${w}-01-01`, `${w}-12-31`];
  if (w === 'ytd') return [`${today.getFullYear()}-01-01`, isoToday];
  let m;
  let offset;
  if ((m = w.match(/^(\d+)y$/))) offset = { years: -parseInt(m[1], 10) };
  else if ((m = w.match(/^(\d+)mo$/))) offset = { months: -parseInt(m[1], 10) };
  else if ((m = w.match(/^(\d+)d$/))) offset = { days: -parseInt(m[1], 10) };
  else return null;
  return [shiftCalendarIso(isoToday, offset), isoToday];
}

function _optWindowLabel() {
  const w = _optWindow;
  if (!w || w === 'lifetime') return 'Lifetime';
  if (/^\d{4}$/.test(w)) return w;
  if (w === 'ytd') return `${snapshotYear()} YTD`;
  let m;
  if ((m = w.match(/^(\d+)y$/))) return `Last ${m[1]} year${m[1] === '1' ? '' : 's'}`;
  if ((m = w.match(/^(\d+)mo$/))) return `Last ${m[1]} month${m[1] === '1' ? '' : 's'}`;
  if ((m = w.match(/^(\d+)d$/))) return `Last ${m[1]} days`;
  return w;
}

// Position outcomes and execution-date P&L have deliberately separate scopes.
// Python builds episodes once; interactive windows select by final close date.
const _r2 = v => v == null ? null : Math.round(v * 100) / 100;
const _optSumRealized = rows => rows.some(c => c.realized == null) ? null
  : _r2(rows.reduce((sum, c) => sum + c.realized, 0));
function _accumulateTradeStats(positions) {
  let wins = 0, losses = 0, be = 0, pnl = 0, holdSum = 0, holdN = 0;
  let winSum = 0, lossSum = 0, topWin = null, topLoss = null;
  for (const c of positions) {
    const r = c.realized;
    pnl += r;
    if (r > 0) { wins++; winSum += r; topWin = topWin == null ? r : Math.max(topWin, r); }
    else if (r < 0) { losses++; lossSum += r; topLoss = topLoss == null ? r : Math.min(topLoss, r); }
    else be++;
    if (c.hold_days != null) { holdSum += c.hold_days; holdN++; }
  }
  return { n: positions.length, wins, losses, be, pnl, holdSum, holdN, winSum, lossSum, topWin, topLoss };
}
function _positionStats(positions) {
  const a = _accumulateTradeStats(positions), decided = a.wins + a.losses;
  return {
    total_pnl: _r2(a.pnl), completed_pnl: _r2(a.pnl), trades: a.n,
    wins: a.wins, losses: a.losses, breakevens: a.be,
    win_rate: decided ? a.wins / decided : null,
    avg_hold_days: a.holdN ? a.holdSum / a.holdN : null,
    biggest_winner: a.topWin, biggest_loser: a.topLoss,
    // Round only for presentation, identically for exported and filtered values.
    profit_factor: a.lossSum < 0 ? a.winSum / Math.abs(a.lossSum) : null,
    expectancy: a.n ? _r2(a.pnl) / a.n : null,
    avg_winner: a.wins ? a.winSum / a.wins : null,
    avg_loser: a.losses ? a.lossSum / a.losses : null,
  };
}
function _splitClosedByType(positions, kind) {
  return _positionStats(positions.filter(c => c.option_type === kind));
}
function _computeOptionsStats(positions) {
  return { ..._positionStats(positions), call: _splitClosedByType(positions, 'Call'),
    put: _splitClosedByType(positions, 'Put') };
}
function _computeContractStats(closed) {
  let wins = 0, losses = 0, breakevens = 0, unknown = 0, contracts = 0;
  for (const c of closed) {
    const qty = c.qty || 0;
    contracts += qty;
    let covered = 0;
    for (const lot of c.lot_outcomes || []) {
      const q = lot.qty || 0;
      covered += q;
      if (lot.outcome === 'win') wins += q;
      else if (lot.outcome === 'loss') losses += q;
      else if (lot.outcome === 'breakeven') breakevens += q;
      else unknown += q;
    }
    unknown += Math.max(0, qty - covered);
  }
  const decided = wins + losses;
  return { wins, losses, breakevens, unknown, contracts,
    win_rate: decided ? wins / decided : null, coverage_complete: unknown < 1e-9 };
}
function _groupTrades(positions, closed, keyFn, keyField, sortFn) {
  const by = new Map();
  const group = key => {
    if (!by.has(key)) by.set(key, { [keyField]: key, trades: 0, wins: 0, losses: 0,
      breakevens: 0, completed_pnl: 0, realized: 0 });
    return by.get(key);
  };
  for (const c of positions) {
    const k = keyFn(c);
    if (!k) continue;
    const r = group(k);
    r.trades++;
    if (c.realized > 0) r.wins++;
    else if (c.realized < 0) r.losses++;
    else r.breakevens++;
    r.completed_pnl += c.realized;
  }
  for (const c of closed) {
    const k = keyFn(c);
    if (k) {
      const r = group(k);
      r.realized = r.realized == null || c.realized == null ? null : r.realized + c.realized;
    }
  }
  return [...by.values()].map(r => ({ ...r, completed_pnl: _r2(r.completed_pnl),
    realized: _r2(r.realized), win_rate: r.wins + r.losses ? r.wins / (r.wins + r.losses) : null })).sort(sortFn);
}
const _computeByUnderlying = (positions, closed = []) => _groupTrades(positions, closed,
  c => c.underlying || '(unknown)', 'underlying', (a, b) => (a.realized == null) - (b.realized == null)
    || (b.realized || 0) - (a.realized || 0) || a.underlying.localeCompare(b.underlying));
const _computeAnnualOptionsSummary = (positions, closed = []) => _groupTrades(positions, closed,
  c => (c.close_date || '').slice(0, 4), 'year', (a, b) => b.year.localeCompare(a.year));
function _computeCumulativePnL(closed) {
  const dates = new Map();
  for (const c of closed) {
    const prior = dates.has(c.close_date) ? dates.get(c.close_date) : 0;
    dates.set(c.close_date, prior == null || c.realized == null ? null : prior + c.realized);
  }
  let run = 0;
  return [...dates].sort(([a], [b]) => a.localeCompare(b))
    .map(([date, value]) => ({ date, value: _r2(run = run == null || value == null ? null : run + value) }));
}
function _optionsScope(opt, range, account) {
  const inAccount = c => !account || c.account_group === account;
  const eligible = c => inAccount(c) && (!range || c.close_date >= range[0] && c.close_date <= range[1]);
  const closed = (opt.closed_trades || []).filter(eligible);
  const completed = (opt.completed_positions || []).filter(eligible);
  const open = (opt.open_contracts || []).filter(inAccount);
  const stats = !range && !account && opt.stats && Array.isArray(opt.completed_positions)
    ? { ...opt.stats, call: { ...opt.stats.call }, put: { ...opt.stats.put } }
    : _computeOptionsStats(completed);
  const realizedPnl = _optSumRealized(closed);
  stats.realized_pnl = realizedPnl;
  stats.closed_count = closed.length;
  stats.closed_contracts = closed.reduce((sum, c) => sum + c.qty, 0);
  stats.open_count = open.length;
  stats.open_contract_count = open.reduce((sum, c) => sum + c.qty, 0);
  for (const [field, type] of [['call', 'Call'], ['put', 'Put']]) {
    stats[field].realized_pnl = _optSumRealized(closed.filter(c => c.option_type === type));
  }
  return { closed, completed, open, stats, contractStats: _computeContractStats(closed), realizedPnl,
    byUnderlying: _computeByUnderlying(completed, closed), annual: _computeAnnualOptionsSummary(completed, closed),
    cumulative: _computeCumulativePnL(closed) };
}
function parseOptionSymbol(sym) {
  if (!sym) return null;
  const m = /^(\S+)\s+(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(Call|Put)\s+\$([\d,.]+)/.exec(sym);
  if (!m) return null;
  const [, underlying, mo, d, y, type, strike] = m;
  return { underlying, expiry: `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`, type, strike: parseFloat(strike.replace(/,/g, '')) };
}
function isOptionSymbol(sym) {
  return !!sym && (sym.includes(' Call ') || sym.includes(' Put ') || sym.endsWith(' OPTION'));
}
const _optPct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const _optQty = v => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 6 });
const _optDays = v => v == null ? '—' : v.toFixed(1);
const _optSigned = v => v == null ? '—' : fmtSigned(v);
const _optPnlCell = v => `<span class="${v > 0 ? 'positive' : v < 0 ? 'negative' : ''}">${_optSigned(v)}</span>`;

function renderOptions() {
  const root = document.getElementById('optionsContent');
  if (!root) return;
  const opt = ANALYTICS.options || {};
  const scope = _optionsScope(opt, _optWindowRange(), _optAccountFilter);
  const { closed, completed, open, stats, contractStats, realizedPnl } = scope;
  const lifetime = _optionsScope(opt, null, null);
  const label = _optWindowLabel(), accountLabel = _optAccountFilter || 'All accounts';
  const modern = Array.isArray(opt.completed_positions);
  const outsideCompleted = closed.filter(c => c.completed_position === false).length;
  const accounts = [...new Set([...(opt.closed_trades || []), ...(opt.completed_positions || []), ...(opt.open_contracts || [])]
    .map(c => c.account_group).filter(Boolean))].sort();
  const accountButton = (value, text, id) => `<button id="opt-account-${id}" class="tbtn ${_optAccountFilter === value ? 'active' : ''}"
    aria-pressed="${_optAccountFilter === value}" onclick="${_htmlEsc(`_setOptAccountFilter(${value == null ? 'null' : _jsString(value)})`)}">${_htmlEsc(text)}</button>`;
  const years = [...new Set([...(opt.closed_trades || []), ...(opt.completed_positions || [])]
    .map(c => (c.close_date || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  const windowButton = (key, text) => `<button id="opt-window-${key}" class="tbtn ${_optWindow === key ? 'active' : ''}"
    aria-pressed="${_optWindow === key}" onclick="_setOptWindow('${key}')">${_htmlEsc(text)}</button>`;
  const filters = `<div class="toggles-card">
    <div class="toggles-row" role="group" aria-label="Options account"><span class="toggles-label">Account:</span>
      ${accountButton(null, 'All', 'all')}${accounts.map((a, i) => accountButton(a, a, i)).join('')}</div>
    <div class="toggles-row" role="group" aria-label="Options window"><span class="toggles-label">Window:</span>
      ${[['lifetime','Lifetime'],['5y','5y'],['3y','3y'],['2y','2y'],['1y','1y'],['ytd','YTD'],['6mo','6mo'],['3mo','3mo'],['30d','30d']]
        .map(([k,v]) => windowButton(k,v)).join('')}${years.map(y => windowButton(y,y)).join('')}</div></div>`;
  const summary = _renderStatCards([
    { label: 'Realized P&L', value: _optSigned(realizedPnl), cls: realizedPnl == null ? '' : realizedPnl >= 0 ? 'positive' : 'negative', title: 'All closing executions dated within the selected window, including partial closes.' },
    { label: 'Position Win Rate', value: _optPct(stats.win_rate), title: 'Winning completed positions ÷ winning and losing completed positions. Breakevens excluded.' },
    { label: 'Completed Positions', value: modern ? _optQty(stats.trades) : '—', title: 'Positions whose final close falls within the selected window.' },
    { label: 'Completed Position P&L', value: modern ? fmtSigned(stats.completed_pnl) : '—', title: 'All lifetime realized P&L of the positions completed in this window, including earlier partial closes.' },
  ]);
  const fixedReference = `<p class="as-of-hint">All accounts · lifetime reference: realized P&amp;L ${_optSigned(lifetime.realizedPnl)}
    · completed positions ${modern ? _optQty(lifetime.stats.trades) : '—'} · position win rate ${_optPct(lifetime.stats.win_rate)}</p>`;
  const ratios = s => `<dl class="opt-metrics">
    <dt>Completed Positions</dt><dd>${modern ? `${_optQty(s.trades)} (${s.wins} W / ${s.losses} L / ${s.breakevens} BE)` : '—'}</dd>
    <dt>Position Win Rate</dt><dd>${_optPct(s.win_rate)}</dd>
    <dt>Completed Position P&amp;L</dt><dd>${modern ? _optPnlCell(s.completed_pnl) : '—'}</dd>
    <dt>Realized P&amp;L</dt><dd>${_optPnlCell(s.realized_pnl)}</dd>
    <dt>Avg Winner</dt><dd>${_optPnlCell(s.avg_winner)}</dd>
    <dt>Avg Loser</dt><dd>${_optPnlCell(s.avg_loser)}</dd>
    <dt>Avg Position Days</dt><dd>${_optDays(s.avg_hold_days)}</dd></dl>`;
  const outcomePanel = `<div class="overview-split-2 options-outcomes" style="margin-top:16px;">
    <div class="panel"><h3>Calls · completed positions</h3>${ratios(stats.call)}</div>
    <div class="panel"><h3>Puts · completed positions</h3>${ratios(stats.put)}</div>
    <div class="panel"><h3>Completed-position outcomes</h3><dl class="opt-metrics">
      <dt>Wins / Losses / Breakevens</dt><dd>${modern ? `${stats.wins} / ${stats.losses} / ${stats.breakevens}` : '—'}</dd>
      <dt>Profit Factor</dt><dd>${stats.profit_factor == null ? '—' : stats.profit_factor.toFixed(2)}</dd>
      <dt>Avg P&amp;L / Position</dt><dd>${_optPnlCell(stats.expectancy)}</dd>
      <dt>Avg Winner</dt><dd>${_optPnlCell(stats.avg_winner)}</dd><dt>Avg Loser</dt><dd>${_optPnlCell(stats.avg_loser)}</dd>
      <dt>Biggest Winner</dt><dd>${_optPnlCell(stats.biggest_winner)}</dd><dt>Biggest Loser</dt><dd>${_optPnlCell(stats.biggest_loser)}</dd>
      <dt>Avg Position Days</dt><dd>${_optDays(stats.avg_hold_days)}</dd></dl>
      <p class="as-of-hint">One outcome per completed position. Profit factor is unavailable when there are no losing positions.</p>
    </div></div>`;
  const contractPanel = `<details id="options-contract-stats" class="section-disclosure"><summary>Contract outcomes · matched purchase lots</summary>
    ${_renderStatCards([{label:'Contract Win Rate',value:_optPct(contractStats.win_rate)},
      {label:'Contracts Closed',value:_optQty(contractStats.contracts)},
      {label:'Contracts (W / L / BE)',value:`${_optQty(contractStats.wins)} / ${_optQty(contractStats.losses)} / ${_optQty(contractStats.breakevens)}`},
      {label:'Unclassified Contracts',value:_optQty(contractStats.unknown)}])}
    <p class="as-of-hint">Closing dates use the selected window. Each matched purchase lot is classified separately and weighted by contract quantity.
      Breakevens and unclassified contracts are excluded from the rate.${contractStats.unknown > 0 ? ' Coverage is incomplete: the rate describes only classified contracts.' : ''}</p></details>`;
  const guide = `<details id="options-metric-guide" class="metric-guide"><summary>How options metrics are calculated</summary>
    <p>A position is one account and one exact option contract (underlying, call/put, strike and expiry), from opening until its quantity returns to zero.
    Scaling in and partial exits belong to the same position; reopening after flat starts a new one. Different option legs are separate positions, not a combined strategy.</p>
    <p>Position win rate is winners ÷ (winners + losers). All outcome averages, biggest winners/losers and profit factor use completed positions.
    Average P&amp;L per position includes breakevens. Profit factor is gross winning position P&amp;L ÷ absolute gross losing position P&amp;L.
    Position days measure calendar days from first entry through final close, with same-day positions shown as zero.</p>
    <p>Position statistics are assigned to the final close date and include the position's full P&amp;L. Realized P&amp;L and the cumulative chart use each execution's actual closing date,
    including partial exits of positions still open. Consequently, these two P&amp;L totals can differ within a selected year or window.</p>
    <p>Only long positions with a complete supported opening/closing history and known realized P&amp;L enter position outcomes.
    Transfers and unmatched history can prevent classification. Contract outcomes use the ledger's matched lot basis and allocated proceeds; missing or inconsistent lot evidence is unclassified.
    P&amp;L follows the ledger annotations and their recorded costs; no extra fee estimate is added here.</p>
    <p>The ledger has dates rather than reliable execution times: buys on a date precede closes, so a same-day exit and re-entry may be grouped together.
    Open quantities are current as of ${_htmlEsc(SNAPSHOT_DATE)}, filtered by account only. Avg Basis Premium is remaining basis ÷ contracts ÷ the contract multiplier, not the last purchase price.
    Execution hold days use matched purchase lots and quantity weighting; position days use the whole position's span.</p></details>`;
  const chart = realizedPnl == null ? '<p class="notice">Realized P&amp;L is unavailable because closing records lack realized-gain annotations.</p>' : renderMiniLineChart(scope.cumulative, { id:'optionsCumSvg', color:realizedPnl >= 0 ? '#4ade80' : '#f87171',
    height:240, yFormatter:fmtMoneyShort, calendarAxis:true,
    label:'Cumulative realized options P&L by closing date in the selected window. Closing executions are listed below.', emptyMsg:'No closing executions in this window.' });
  const openRows = open.map(o => `<tr><td>${_htmlEsc(o.account_group || '—')}</td><td><b>${_htmlEsc(o.underlying || o.symbol || '')}</b></td>
    <td>${_htmlEsc(o.expiry || '—')}</td><td class="num">${o.dte == null ? '—' : o.dte}</td><td>${_htmlEsc(o.option_type || o.type || '')}</td>
    <td class="num">${o.strike == null ? '—' : fmtMoney(o.strike,2)}</td><td class="num">${_optQty(o.qty)}</td>
    <td>${_htmlEsc(o.open_date || '—')}</td><td class="num">${!modern || o.entry_price == null ? '—' : fmtMoney(o.entry_price,2)}</td></tr>`).join('');
  const table = (caption, headings, rows, scrollable = false) => `<div class="table-wrap${scrollable ? ' mini-scroll' : ''}"><table class="mini-table"><caption class="sr-only">${caption}</caption>
    <thead><tr>${headings.map(h => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${headings.length}">No matching records.</td></tr>`}</tbody></table></div>`;
  const contractCells = c => `<td>${_htmlEsc(c.account_group || '—')}</td><td><b>${_htmlEsc(c.underlying || c.symbol || '')}</b></td>
    <td>${_htmlEsc(c.expiry || '')}</td><td>${_htmlEsc(c.option_type || '')}</td><td class="num">${c.strike == null ? '—' : fmtMoney(c.strike,2)}</td>`;
  const recent = rows => [...rows].sort((a,b) => (b.close_date || '').localeCompare(a.close_date || '')).slice(0,200);
  const completedRows = recent(completed).map(c => `<tr><td>${_htmlEsc(c.close_date)}</td>${contractCells(c)}<td>${_htmlEsc(c.open_date || '—')}</td>
    <td class="num">${_optQty(c.qty)}</td><td class="num">${_optPnlCell(c.realized)}</td><td class="num">${_optDays(c.hold_days)}</td></tr>`).join('');
  const closedRows = recent(closed).map(c => `<tr><td>${_htmlEsc(c.close_date)}</td>${contractCells(c)}<td>${_htmlEsc((c.action || '').replace('Option ',''))}</td>
    <td class="num">${_optQty(c.qty)}</td><td class="num">${c.proceeds == null ? '—' : fmtMoney(c.proceeds)}</td>
    <td class="num">${c.basis == null ? '—' : fmtMoney(c.basis)}</td><td class="num">${_optPnlCell(c.realized)}</td><td class="num">${_optDays(c.hold_days)}</td></tr>`).join('');
  const groupedRows = (rows,key) => rows.map(r => `<tr><td>${_htmlEsc(r[key])}</td><td class="num">${modern ? _optQty(r.trades) : '—'}</td>
    <td class="num">${_optPct(r.win_rate)}</td><td class="num">${modern ? _optPnlCell(r.completed_pnl) : '—'}</td><td class="num">${_optPnlCell(r.realized)}</td></tr>`).join('');
  const limitNote = rows => rows.length > 200 ? `<p class="as-of-hint">Showing the 200 most recent of ${rows.length} records. Statistics include every matching record.</p>` : '';
  renderDisclosureContent(root, `${filters}<h2>${_htmlEsc(label)} · ${_htmlEsc(accountLabel)}</h2>
    <p class="as-of-hint">Realized P&amp;L: execution closing dates. Position outcomes: final close dates. Breakevens excluded from win rates.</p>
    ${!modern ? '<p class="notice">Completed-position analytics are unavailable in this older export. Regenerate the dashboard to see position outcomes.</p>' : ''}
    ${outsideCompleted ? `<p class="as-of-hint">${outsideCompleted} closing execution${outsideCompleted === 1 ? '' : 's'} belong to positions still open or lacking a complete supported history. Their realized P&amp;L remains included.</p>` : ''}
    ${summary}${fixedReference}${guide}${outcomePanel}${contractPanel}
    <div class="section-header" style="margin-top:24px;"><h2>Open positions</h2><span class="as-of-hint">As of ${_htmlEsc(SNAPSHOT_DATE)} · account filter only</span></div>
    ${_renderStatCards([{label:'Open Contracts',value:_optQty(stats.open_contract_count)},{label:'Open Positions',value:_optQty(open.length)}])}
    ${table('Open option positions',['Account','Underlying','Expiry','DTE','Type','Strike','Contracts','Opened','Avg Basis Premium'],openRows)}
    <div class="section-header" style="margin-top:24px;"><h2>Cumulative Realized P&amp;L</h2><span class="as-of-hint">${_htmlEsc(label)} · starts at zero before this window's first close</span></div>${chart}
    <div class="overview-split options-summaries"><div class="panel"><h3>By underlying</h3>
      ${table('Option profit and loss by underlying',['Underlying','Completed Positions','Position Win %','Completed P&amp;L','Realized P&amp;L'],groupedRows(scope.byUnderlying,'underlying'),true)}</div>
    <div class="panel"><h3>Annual Options Summary</h3>
      ${table('Option outcomes and realized profit and loss by year',['Year','Completed Positions','Position Win %','Completed P&amp;L','Realized P&amp;L'],groupedRows(scope.annual,'year'),true)}</div></div>
    <p class="as-of-hint">Completed P&amp;L assigns the whole position to its final-close year; realized P&amp;L stays in each execution's year. Both tables follow the selected filters.</p>
    <details id="options-completed-positions" class="section-disclosure" open><summary>Completed positions (${modern ? completed.length : 'unavailable'})</summary>
      ${modern ? table('Completed option positions',['Final Close','Account','Underlying','Expiry','Type','Strike','Opened','Contracts Closed','Position P&amp;L','Position Days'],completedRows) + limitNote(completed)
        : '<p class="notice">Regenerate this older export to see completed positions.</p>'}</details>
    <details id="options-closing-executions" class="section-disclosure"><summary>Closing executions (${closed.length})</summary>
      <p class="as-of-hint">Includes partial exits, expirations and exercises. Each row is an execution, not a completed position.</p>
      ${table('Option closing executions',['Close Date','Account','Underlying','Expiry','Type','Strike','Action','Contracts','Proceeds','Basis','Realized P&amp;L','Lot Hold Days'],closedRows)}${limitNote(closed)}</details>`);
  applyScrollRegionFocus();
}

// Generic full-width line chart.  Renders at the container's actual
// pixel width so text and shapes don't distort (like the main history
// chart).  Uses queueMicrotask to measure the container after DOM
// insert, then injects the SVG content with the measured width.
// One tooltip row (swatch + name + value), styled with the shared
// tt-* classes.  Inline styles use SINGLE quotes so the whole block can
// live inside a double-quoted `data-tip="..."` attribute without any
// escaping.  `color` empty → no swatch (e.g. a Total row).
function _tipRow(color, name, value, bold) {
  const sw = color ? `<span class='tt-swatch' style='background:${color}'></span>` : '';
  const st = bold ? " style='font-weight:600;'" : '';
  return `<div class='tt-row'${st}><span class='tt-name'>${sw}${_htmlEsc(name)}</span><span>${value}</span></div>`;
}

// Attach the custom hover tooltip to discrete bars (contributions,
// waterfall).  Each bar carries its tooltip HTML in a `data-tip`
// attribute; on hover we show the shared `.chart-tooltip` div near the
// cursor and brighten the bar.  Delegation-free (rects are recreated on
// each build, so per-rect listeners never accumulate).  Continuous
// charts keep their crosshair hover; this is for bar-at-a-time hover.
function _bindBarTooltips(svg, tip, wrap) {
  if (!svg || !tip || !wrap) return;
  const place = (ev) => {
    const wrapRect = wrap.getBoundingClientRect();
    let tx = ev.clientX - wrapRect.left + 12;
    let ty = ev.clientY - wrapRect.top + 12;
    const tRect = tip.getBoundingClientRect();
    if (tx + tRect.width + 12 > wrapRect.width) tx = ev.clientX - wrapRect.left - tRect.width - 12;
    if (ty + tRect.height + 12 > wrapRect.height) ty = ev.clientY - wrapRect.top - tRect.height - 12;
    tip.style.left = Math.max(0, tx) + 'px';
    tip.style.top = Math.max(0, ty) + 'px';
  };
  svg.querySelectorAll('[data-tip]').forEach(el => {
    el.style.cursor = 'default';
    el.addEventListener('mouseenter', ev => {
      tip.innerHTML = el.getAttribute('data-tip');
      tip.style.display = 'block';
      el.style.filter = 'brightness(1.18)';
      place(ev);
    });
    el.addEventListener('mousemove', place);
    el.addEventListener('mouseleave', () => {
      tip.style.display = 'none';
      el.style.filter = '';
    });
  });
}

function renderMiniLineChart(points, opts) {
  opts = opts || {};
  const id = opts.id || ('miniChart_' + Math.random().toString(36).slice(2, 7));
  const color = opts.color || '#a78bfa';
  const height = opts.height || 260;
  const yFmt = opts.yFormatter || fmtMoneyShort;
  // A chart is an image: without a name a screen reader announces
  // nothing at all for it.  The <title> elements inside describe
  // individual marks; this names the whole plot.
  const label = opts.label || 'Line chart';
  if (!points || !points.length) {
    return `<div class="chart-empty">${opts.emptyMsg || 'No data.'}</div>`;
  }

  // Build SVG content at a given width (in CSS pixels).  Factored out
  // so we can call it once at initial render (with 800 default) and
  // again after we know the actual container width.
  const buildContent = (W) => _buildMiniLineChartContent(points, W, height, color, yFmt, id, opts.calendarAxis);

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
         role="img" aria-label="${_htmlEsc(label)}"
         style="width:100%;height:${height}px;display:block;"></svg>
    <div class="chart-tooltip" id="${id}_tip"></div>
  </div>`;
}

// Pure content builder — returns the SVG inner string for a given
// pixel width.  No DOM side effects.  `bindHover` is a function to
// call after the content is in the DOM to wire up crosshair behaviour.
function _buildMiniLineChartContent(points, W, H, color, yFmt, id, calendarAxis = false) {
  const PAD = { l: 64, r: 16, t: 12, b: 28 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const n = points.length;
  const times = calendarAxis ? points.map(p => Date.parse(p.date + 'T00:00:00Z')) : [];
  const dated = calendarAxis && times.every(Number.isFinite) && times[n - 1] > times[0];
  const xOf = i => PAD.l + (n === 1 ? plotW / 2 : dated
    ? (times[i] - times[0]) * plotW / (times[n - 1] - times[0]) : i * plotW / (n - 1));
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
  let previousLabelX = -Infinity;
  for (let i = 0; i < xTicks; i++) {
    const idx = Math.round((i * (n - 1)) / (xTicks - 1 || 1));
    const x = xOf(idx);
    if (dated && x - previousLabelX < 76) continue;
    previousLabelX = x;
    const anchor = dated && idx === n - 1 ? 'end' : 'middle';
    parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="${anchor}">${_htmlEsc((points[idx].date || '').slice(0, dated ? 10 : 7))}</text>`);
  }
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);
  // Realized P&L changes on execution dates; do not imply gains between closes.
  const d = points.map((p, i) => dated && i > 0
    ? `H${xOf(i)} V${yOf(p.value)}` : `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(p.value)}`).join(' ');
  parts.push(`<path class="series-line" d="${d}" stroke="${color}" stroke-width="1.8" fill="none"/>`);
  if (n === 1) parts.push(`<circle cx="${xOf(0)}" cy="${yOf(points[0].value)}" r="3" fill="${color}"/>`);

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
      if (dated) {
        let low = 0, high = n - 1;
        while (low < high) {
          const mid = Math.floor((low + high) / 2);
          if (xOf(mid) < mx) low = mid + 1; else high = mid;
        }
        idx = low > 0 && mx - xOf(low - 1) < xOf(low) - mx ? low - 1 : low;
      }
      const x = xOf(idx), y = yOf(points[idx].value);
      hv.setAttribute('x1', x); hv.setAttribute('x2', x);
      hv.style.display = '';
      dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', 4);
      if (tip) {
        tip.innerHTML =
          `<div class="tt-date">${_htmlEsc(points[idx].date || '')}</div>` +
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

