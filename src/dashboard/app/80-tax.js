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
      capitalLossDisallowed: py.capital_loss_disallowed || 0,
      k401: py.k401,
      k401Ytd: py.k401_ytd,
      k401Limit: py.k401_limit,
      pretaxDeductions: py.pretax_deductions || 0,
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
      extraWithholding: py.extra_withholding || 0,
      estTaxAfterWithholding: py.est_tax_after_withholding,
      safeHarbor: py.safe_harbor || null,
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
        ${e.extraWithholding > 0 ? item('Extra withholding (annualized)', -e.extraWithholding, 'positive') : ''}
        ${e.extraWithholding > 0 ? item('Still to cover', e.estTaxAfterWithholding, e.estTaxAfterWithholding > 0 ? 'negative' : 'positive') : ''}
        ${item('≈ per quarter (÷4)', e.estQuarterlyPayment)}
      </div>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:10px;line-height:1.5;">
        Rough set-aside for IRS Form 1040-ES on <b>taxable-account</b> realized gains only:
        ST at your ordinary marginal rate, LT at the LTCG rate, plus state (gains taxed as
        ordinary income in most states) and NIIT (3.8% above the MAGI threshold).
        ${e.extraWithholding > 0 ? 'Your voluntary extra paycheck withholding is credited first — the IRS treats withholding as paid evenly through the year, making it the cleanest way to cover lumpy gains.  The quarterly figure is on the remainder.' : ''}
        Not a substitute for a tax pro — ignores credits, AMT, and safe-harbor
        prior-year rules.  Even-quarters split is a simplification; gains realized late in
        the year may shift the due date.
      </div>
    </div>`;
}

// --- Safe-harbor check -----------------------------------------------------
// IRS Form 2210 safe harbor: withholding ≥ min(90% of this year's tax,
// 100%/110% of last year's).  Needs `Tax Return` metadata rows (prior
// year's Total Tax + AGI).  All figures precomputed in analytics/tax.py.
function _buildSafeHarborSection(e) {
  const sh = e && e.safeHarbor;
  if (!sh) return '';
  const item = (label, val, cls) => `
    <div class="item"><span class="label">${label}</span>
      <span class="value ${cls || ''}">${typeof val === 'number' ? fmtMoney(val, 0) : val}</span></div>`;
  const statusChip = sh.covered
    ? '<span class="dh-chip positive" style="background:transparent;border:1px solid currentColor;">covered ✓</span>'
    : '<span class="dh-chip negative" style="background:transparent;border:1px solid currentColor;">short ' + fmtMoney(sh.shortfall, 0) + '</span>';
  const suggestion = (!sh.covered && sh.suggested_extra_per_paycheck != null)
    ? `<div style="margin-top:8px;font-size:0.85rem;">To reach the safe harbor from withholding alone: add about <b>${fmtMoney(sh.suggested_extra_per_paycheck)}</b> extra federal withholding per paycheck for the remaining ${sh.remaining_paychecks} paychecks this year.</div>`
    : '';
  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Withholding Safe Harbor</span></h2>
      <span style="margin-left:12px;color:var(--text-dim);font-size:0.8rem;">no underpayment penalty if withholding reaches the target</span>
      <span style="margin-left:auto;">${statusChip}</span>
    </div>
    <div class="panel">
      <div class="bracket-summary">
        ${item(`${sh.prior_year} total tax (1040 line 24)`, sh.prior_year_tax)}
        ${item(`Prior-year prong (× ${(sh.threshold_pct * 100).toFixed(0)}%)`, sh.prior_year_prong)}
        ${item(`90% of this year's est. tax`, sh.ninety_pct_prong)}
        ${item('Safe-harbor target (lesser)', sh.effective_target, 'negative')}
        ${item('Projected withholding', sh.projected_withholding, sh.covered ? 'positive' : '')}
        ${sh.covered ? item('Margin', sh.projected_withholding - sh.effective_target, 'positive') : item('Shortfall', sh.shortfall, 'negative')}
      </div>
      ${suggestion}
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:10px;line-height:1.5;">
        Prior-year prong uses ${(sh.threshold_pct * 100).toFixed(0)}% because ${sh.prior_year} AGI was ${sh.prior_year_agi != null ? fmtMoney(sh.prior_year_agi, 0) : 'unknown'} (110% applies above $150k, $75k MFS).
        Projected withholding is an <b>estimate</b>: fin's wage-tax figure (${fmtMoney(sh.w4_withholding_est, 0)}, a proxy for an accurately-filled W-4) + your extra withholding (${fmtMoney(sh.extra_withholding, 0)}).
        Check a recent pay stub's YTD federal withholding to confirm the real pace.  This year's estimated tax (${fmtMoney(sh.est_total_federal_tax, 0)} federal) projects current YTD pace — realizing more gains raises the 90% prong but never the prior-year prong, which is why the prior-year safe harbor is the reliable one in a big-gain year.
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
        ${est.ltcg_headroom != null ? `
        <div class="item" title="How much MORE long-term gain you can realize this year before the LTCG rate steps up${est.ltcg_next_rate != null ? ' to ' + (est.ltcg_next_rate * 100).toFixed(0) + '%' : ''}.  LTCG stacks on top of ordinary taxable income.">
          <span class="label">LTCG Headroom (${(est.marginal_long * 100).toFixed(0)}% rate)</span>
          <span class="value">${fmtMoney(est.ltcg_headroom, 0)}</span></div>` : ''}
        ${est.niit_headroom != null ? `
        <div class="item" title="AGI distance to the ${fmtMoney(est.niit_threshold || 200000, 0)} Net Investment Income Tax threshold — investment income above it picks up an extra 3.8%.">
          <span class="label">NIIT Headroom</span>
          <span class="value">${fmtMoney(est.niit_headroom, 0)}</span></div>` : ''}
      </div>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:8px;line-height:1.4;">
        Filled = income already allocated to that bracket.  Partial (gradient) shows the bracket where your taxable income ends.  Use <b>Room in Bracket</b> for short-term gains (they stack with ordinary income at ${(currentRate * 100).toFixed(0)}%); use <b>LTCG Headroom</b> for long-term gains — the amount you can realize at the current ${est.marginal_long != null ? (est.marginal_long * 100).toFixed(0) + '%' : ''} LTCG rate before the next tier bites.
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
          ${est.capitalLossDisallowed ? `<div>Capital-loss cap (−$3,000 max vs ordinary): <b class="positive">${fmtSigned(est.capitalLossDisallowed)}</b> <span style="color:var(--text-dim);font-size:0.75rem;">added back — excess carries forward (not modeled)</span></div>` : ''}
          <div>401K contribution${est.isProjection ? ' (projected)' : ''}: <b class="negative">${fmtMoney(-est.k401)}</b>${est.isProjection && est.k401Ytd != null ? ` <span style="color:var(--text-dim);font-size:0.75rem;">YTD ${fmtMoney(est.k401Ytd)}${est.k401Limit ? ` · cap ${fmtMoney(est.k401Limit, 0)}` : ''}</span>` : ''}</div>
          ${est.pretaxDeductions ? `<div>Pre-tax paycheck deductions: <b class="negative">${fmtMoney(-est.pretaxDeductions)}</b> <span style="color:var(--text-dim);font-size:0.75rem;">medical etc. — reduce W-2 wages</span></div>` : ''}
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

    ${_buildSafeHarborSection(est)}

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
        Pairs with the Harvest Candidates above: harvest now, or wait N days for LT treatment.
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

