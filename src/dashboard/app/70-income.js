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

  // Trailing-12-months retirement contribution rate (used as projection).
  // Employer match / safe-harbor money is excluded — it never left the
  // user's paycheck, so counting it would overstate out-of-pocket
  // outflows (same description rule as analytics/tax.py's 401k
  // deduction).
  const yrAgo = new Date(today); yrAgo.setFullYear(yrAgo.getFullYear() - 1);
  let last12Contribs = 0;
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib || !t.date) continue;
    const desc = (t.description || '').toLowerCase();
    if (desc.includes('employer') || desc.includes('match')) continue;
    if (new Date(t.date) >= yrAgo) last12Contribs += (t.amount || 0);
  }
  const projContribs = Math.round(last12Contribs);

  const passive = Math.round(passiveProjected || 0);
  const grossIn = currentSalary + lastYearBonuses + passive;
  const totalNet = grossIn - projContribs;   // money that flows OUT of payroll into retirement reduces "available" cash

  if (currentSalary === 0 && passive === 0 && projContribs === 0) return '';

  // Budget outflow (when `Budget` metadata rows exist) — turns the
  // forecast from "cash hitting accounts" into projected savings
  // capacity.  Figure comes precomputed from analytics.budget.
  const _budget = ANALYTICS.budget || null;
  const budgetAnnual = _budget ? Math.round(_budget.annual_total || 0) : 0;

  // Paycheck taxes + benefit deductions (when `Paycheck Deduction`
  // metadata rows exist) — payroll taxes, est. federal income tax on
  // wages, and pre/post-tax benefits.  401(k) is NOT in this figure
  // (it's the Retirement contributions line above).
  const _pc = ANALYTICS.paycheck || null;
  // Extra withholding is included: it leaves the paycheck (and roughly
  // covers the tax on the investment income counted above pre-tax).
  const paycheckOutflows = _pc
    ? Math.round((_pc.taxes_annual || 0) + (_pc.pretax_annual || 0)
      + (_pc.posttax_annual || 0) + (_pc.est_federal_tax_annual || 0)
      + (_pc.withholding_annual || 0))
    : 0;

  const rowDefs = [
    ['Salary (current rate × 12mo)', currentSalary, 'positive'],
    ['Bonuses (last full-year actual)', lastYearBonuses, 'positive'],
    ['Passive investment income (proj.)', passive, 'positive'],
    ['Retirement contributions (proj.)', -projContribs, 'negative'],
  ];
  if (paycheckOutflows > 0) {
    rowDefs.push(['Paycheck taxes & deductions (est.)', -paycheckOutflows, 'negative']);
  }
  if (budgetAnnual > 0) {
    rowDefs.push(['Living expenses (budget)', -budgetAnnual, 'negative']);
  }
  const rows = rowDefs.map(([label, amt, cls]) => `<tr>
    <td>${label}</td>
    <td class="num"><span class="${cls}">${fmtSigned(amt)}</span></td>
  </tr>`).join('');
  const savingsNet = totalNet - budgetAnnual - paycheckOutflows;
  const savingsLabel = paycheckOutflows > 0
    ? 'Projected savings (net of taxes + budget)'
    : 'Projected savings (net of budget)';
  const savingsItem = (budgetAnnual > 0 || paycheckOutflows > 0)
    ? `<div class="item"><span class="label">${savingsLabel}</span><span class="value">${fmtMoney(savingsNet)}</span></div>`
    : '';

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">12-Month Cash-Flow Forecast</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Net income hitting your accounts over the next 12 months at current rates.</span>
    </div>
    <div class="income-forecast">
      <div class="if-totals">
        <div class="item"><span class="label">Gross inflows (proj.)</span><span class="value">${fmtMoney(grossIn)}</span></div>
        <div class="item"><span class="label">Net of retirement (proj.)</span><span class="value">${fmtMoney(totalNet)}</span></div>
        ${savingsItem}
      </div>
      <table class="mini-table">
        <thead><tr><th>Source</th><th class="num">Projected (next 12mo)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Salary: most recent rate × 12.  Bonuses: last full year's total (current-year bonuses are lumpy, so the prior year is a better planning estimate).  Retirement contributions: trailing-12-months pace, employee money only (employer match excluded — it never left your paycheck).${paycheckOutflows > 0 ? '  Paycheck taxes &amp; deductions come from your <b>Paycheck Deduction</b> metadata rows + estimated federal income tax on wages (see the Paycheck panel below); bonuses are shown pre-tax.' : '  This is pre-tax — federal/state income tax + FICA reduce the net further.'}${budgetAnnual > 0 ? '  Living expenses come from your <b>Budget</b> metadata rows (see the Budget section below).' : ''}
      </div>
    </div>`;
}

// Paycheck panel — gross → deductions → estimated take-home, from
// `Paycheck Deduction` metadata rows (analytics/paycheck.py).  Hidden
// when no rows exist.
function _buildPaycheckSection() {
  const p = ANALYTICS.paycheck || null;
  if (!p) return '';

  const cards = [
    { label: 'Take-Home / Paycheck', value: fmtMoney(p.take_home_per_paycheck),
      cls: 'positive',
      title: `Estimated net pay per ${p.frequency_label} paycheck: gross − pre-tax benefits − 401(k) − est. federal income tax − payroll taxes − post-tax deductions.` },
    { label: 'Take-Home / Year (est.)', value: fmtMoney(p.take_home_annual),
      title: p.take_home_pct != null ? `${p.take_home_pct.toFixed(1)}% of gross salary.` : '' },
    { label: 'Payroll Taxes / Year', value: fmtMoney(p.taxes_annual),
      title: 'Sum of the tax lines on your pay stub (OASDI, Medicare, state programs…) × pay periods.' },
    { label: 'Est. Federal Income Tax', value: fmtMoney(p.est_federal_tax_annual),
      title: `Estimated LIABILITY on wages only (salary − pre-tax benefits − 401(k) − standard deduction through the ${p.year} ordinary brackets)${p.est_federal_effective_pct != null ? ` — ${p.est_federal_effective_pct.toFixed(1)}% effective on gross` : ''}.  Not your W-4 withholding; bonuses and investment income are covered on the Tax tab.` },
  ];

  const line = (label, pp, ann, cls) => `<tr>
    <td>${label}</td>
    <td class="num"><span class="${cls || ''}">${fmtSigned(pp)}</span></td>
    <td class="num"><span class="${cls || ''}">${fmtSigned(ann)}</span></td>
  </tr>`;

  const rows = [];
  rows.push(line('<b>Gross pay</b>', p.gross_per_paycheck, p.salary_annual, 'positive'));
  for (const d of (p.pretax || [])) {
    rows.push(line(_htmlEsc(d.label) + ' <span class="sub">pre-tax</span>',
      -d.per_paycheck, -d.annual, d.per_paycheck > 0 ? 'negative' : 'positive'));
  }
  if (p.k401_annual > 0) {
    rows.push(line('401(k) elective deferral <span class="sub">pre-tax · from transactions</span>',
      -p.k401_per_paycheck, -p.k401_annual, 'negative'));
  }
  rows.push(line('Est. federal income tax <span class="sub">liability, not withholding</span>',
    -p.est_federal_tax_per_paycheck, -p.est_federal_tax_annual, 'negative'));
  for (const d of (p.taxes || [])) {
    rows.push(line(_htmlEsc(d.label) + ' <span class="sub">tax</span>',
      -d.per_paycheck, -d.annual, 'negative'));
  }
  for (const d of (p.posttax || [])) {
    rows.push(line(_htmlEsc(d.label) + ' <span class="sub">post-tax</span>',
      -d.per_paycheck, -d.annual, 'negative'));
  }
  for (const d of (p.withholding || [])) {
    rows.push(line(_htmlEsc(d.label) + ' <span class="sub">extra withholding — prepays EOY taxes</span>',
      -d.per_paycheck, -d.annual, 'negative'));
  }
  rows.push(`<tr style="border-top:1px solid var(--border);">
    <td><b>Take-home (est.)</b></td>
    <td class="num"><b class="positive">${fmtMoney(p.take_home_per_paycheck)}</b></td>
    <td class="num"><b class="positive">${fmtMoney(p.take_home_annual)}</b></td>
  </tr>`);

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Paycheck</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Gross → deductions → take-home, ${p.frequency_label} — from the <b>Paycheck Deduction</b> rows in data/metadata.csv.</span>
    </div>
    ${_renderStatCards(cards)}
    <div class="panel">
      <table class="mini-table">
        <thead><tr><th>Line</th><th class="num">Per Paycheck</th><th class="num">Annual</th></tr></thead>
        <tbody>${rows.join('')}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Wage-only view: bonuses, dividends, and realized gains are excluded here (the Tax tab covers the full picture).
        Federal tax is the estimated <b>liability</b> on wages${p.is_projection ? ' (current-year projection)' : ''}, not your actual withholding — compare it against your W-4 withholding to spot over/under-withholding.${(p.withholding && p.withholding.length) ? '  The extra-withholding line is a voluntary prepayment of the year-end bill — the Tax tab credits it against the estimated tax on realized gains.' : ''}
        The 401(k) line is your employee deferral derived from actual contribution transactions (projected at YTD pace, capped at the IRS limit).
        Payroll-tax lines are your pay-stub amounts × ${p.frequency} pay periods.
        Pre-tax benefit lines also reduce the AGI / MAGI used for Roth eligibility on the Tax tab.
      </div>
    </div>`;
}

// Budget section — recurring living expenses from `Budget` metadata
// rows, rolled up in analytics/budget.py.  Hidden when no rows exist.
function _buildBudgetSection() {
  const b = ANALYTICS.budget || null;
  if (!b || !b.rows || !b.rows.length) return '';

  const cards = [
    { label: 'Monthly Budget', value: fmtMoney(b.monthly_total),
      title: 'Sum of all active budget rows, normalized to per-month (yearly ÷ 12, quarterly ÷ 3, weekly × 52/12).' },
    { label: 'Annualized', value: fmtMoney(b.annual_total) },
  ];
  if (b.income_coverage_pct != null) {
    cards.push({
      label: 'Covered by Passive Income',
      value: b.income_coverage_pct.toFixed(1) + '%',
      cls: b.income_coverage_pct >= 100 ? 'positive' : '',
      title: `Trailing-12-month portfolio income (${fmtMoney(b.ttm_income || 0)}) ÷ annualized budget (${fmtMoney(b.annual_total)}).  At 100%, dividends and interest pay every bill on this list.`,
    });
  }
  if (b.vs_annual_expenses) {
    const v = b.vs_annual_expenses;
    cards.push({
      label: 'vs Annual Expenses',
      value: fmtSigned(v.delta),
      cls: v.delta <= 0 ? 'positive' : 'negative',
      title: `Bottom-up budget (${fmtMoney(b.annual_total)}) minus the declared Annual Expenses metadata figure (${fmtMoney(v.annual_expenses)}).  A large positive gap means the Annual Expenses row (which drives the FIRE math) understates actual spending — or the budget includes items the lump excludes.`,
    });
  }

  // Category rollup with inline share bars.
  const maxPct = Math.max(...b.by_category.map(c => c.pct || 0), 1);
  const catRows = b.by_category.map(c => `<tr>
    <td><b>${_htmlEsc(c.category)}</b></td>
    <td class="num">${fmtMoney(c.monthly)}</td>
    <td class="num">${fmtMoney(c.annual)}</td>
    <td style="width:40%;">
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="flex:1;height:8px;background:var(--bg-card);border-radius:4px;overflow:hidden;">
          <div style="width:${((c.pct || 0) / maxPct * 100).toFixed(1)}%;height:100%;background:var(--accent);opacity:0.75;"></div>
        </div>
        <span style="color:var(--text-dim);font-size:0.75rem;min-width:44px;text-align:right;">${c.pct != null ? c.pct.toFixed(1) + '%' : '—'}</span>
      </div>
    </td>
  </tr>`).join('');

  const itemRows = b.rows.map(r => `<tr>
    <td><b>${_htmlEsc(r.label)}</b></td>
    <td>${_htmlEsc(r.category)}</td>
    <td>${_htmlEsc(r.cadence)}</td>
    <td class="num">${fmtMoney(r.amount)}</td>
    <td class="num"><b>${fmtMoney(r.monthly)}</b></td>
  </tr>`).join('');

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Budget</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Recurring living expenses — from the <b>Budget</b> rows in data/metadata.csv.</span>
    </div>
    ${_renderStatCards(cards)}
    <div class="panel">
      <table class="mini-table">
        <thead><tr><th>Category</th><th class="num">Monthly</th><th class="num">Annual</th><th>Share</th></tr></thead>
        <tbody>${catRows}</tbody>
      </table>
    </div>
    <details style="margin-top:10px;">
      <summary style="cursor:pointer;color:var(--text-dim);font-size:0.85rem;">All items (${b.count})</summary>
      <div class="panel" style="margin-top:8px;">
        <table class="mini-table">
          <thead><tr><th>Item</th><th>Category</th><th>Cadence</th><th class="num">Cost / period</th><th class="num">Monthly</th></tr></thead>
          <tbody>${itemRows}</tbody>
        </table>
      </div>
    </details>`;
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
  // Expense coverage — what % of annual spending passive income already
  // pays for.  The FIRE progress number: at 100%, the portfolio's income
  // covers the bills.  Needs an `Annual Expenses` metadata row.
  const _cal = ANALYTICS.income_calendar || {};
  if (_cal.expense_coverage_pct != null) {
    statCards.push({
      label: 'Covers Expenses',
      value: _cal.expense_coverage_pct.toFixed(1) + '%',
      cls: 'positive',
      title: `Trailing-12-month portfolio income (${fmtMoney(_cal.ttm_actual || 0)}) ÷ annual expenses (${fmtMoney(_cal.annual_expenses || 0)}).  At 100%, passive income pays all the bills — the FIRE finish line.`,
    });
  }
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

  // Paycheck + Budget — money out (hidden without their metadata rows).
  const paycheckHtml = _buildPaycheckSection();
  const budgetHtml = _buildBudgetSection();

  root.innerHTML = `
    ${statsHtml}
    ${flowHtml}
    ${paycheckHtml}
    ${budgetHtml}
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

