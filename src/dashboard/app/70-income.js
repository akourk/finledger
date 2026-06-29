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

