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
    label: 'Cumulative realized options profit and loss over time. The Closed Trades table below lists the same trades.',
    id: 'optionsCumSvg',
    color: total >= 0 ? '#4ade80' : '#f87171',
    height: 240,
    yFormatter: fmtMoneyShort,
    emptyMsg: 'No closed option trades yet.',
  });

  // Open contracts table
  const openCols = ['underlying', 'expiry', 'dte', 'type', 'strike', 'qty', 'open_date', 'entry_price'];
  const openHead = `<tr>
    <th scope="col">Underlying</th><th scope="col">Expiry</th><th scope="col" class="num">DTE</th><th scope="col">Type</th>
    <th scope="col" class="num">Strike</th><th scope="col" class="num">Qty</th>
    <th scope="col">Opened</th><th scope="col" class="num">Entry Premium</th></tr>`;
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

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Open Contracts (${open.length})</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Current positions — window filter doesn't apply${_optAccountFilter ? ` · filtered to ${_htmlEsc(_optAccountFilter)}` : ''}</span>
    </div>
    <div class="table-wrap">
      <table class="mini-table"><caption class="sr-only">Open option contracts</caption><thead>${openHead}</thead><tbody>${openRows}</tbody></table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Cumulative Realized P&amp;L</span></h2><span class="as-of-hint" style="margin-left:auto;">${winLabel}</span></div>
    ${chartHtml}

    <div class="overview-split">
      <div class="panel">
        <h3>P&amp;L by Underlying</h3>
        <div class="mini-scroll">
          <table class="mini-table"><caption class="sr-only">Option profit and loss by underlying</caption><thead><tr>
            <th scope="col">Underlying</th><th scope="col" class="num">Trades</th><th scope="col" class="num">Win %</th><th scope="col" class="num">Realized</th>
          </tr></thead><tbody>${underRows || '<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody></table>
        </div>
        <div class="panel-foot"><a onclick="activateTab('tax')">View ST / LT / §1256 split on the Tax tab →</a></div>
      </div>
      <div class="panel">
        <h3>Annual Options Summary</h3>
        <div class="mini-scroll">
          <table class="mini-table"><caption class="sr-only">Option trade count and profit and loss by year</caption><thead><tr>
            <th scope="col">Year</th><th scope="col" class="num">Trades</th><th scope="col" class="num">Win %</th><th scope="col" class="num">Realized</th>
          </tr></thead><tbody>${yearOptRows || '<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody></table>
        </div>
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Closed Trades</span></h2></div>
    <div class="table-wrap">
      <table class="mini-table"><caption class="sr-only">Closed option trades</caption><thead><tr>
        <th scope="col">Close Date</th><th scope="col">Underlying</th><th scope="col">Expiry</th><th scope="col">Type</th>
        <th scope="col" class="num">Strike</th><th scope="col">Close</th><th scope="col" class="num">Qty</th>
        <th scope="col" class="num">Proceeds</th><th scope="col" class="num">Basis</th><th scope="col" class="num">Realized</th><th scope="col" class="num">Hold Days</th>
      </tr></thead><tbody>${closedRows}${closedTail}</tbody></table>
    </div>
  `;
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
  return `<div class='tt-row'${st}><span class='tt-name'>${sw}${name}</span><span>${value}</span></div>`;
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
         role="img" aria-label="${_htmlEsc(label)}"
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

