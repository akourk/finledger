// =========================================================================
// Retirement tab
// =========================================================================

// RETIREMENT_META is hoisted to the top of this file so the Overview
// tab's Year-by-Year breakdown (rendered immediately on load) can read
// it without a TDZ error.

// IRS annual contribution limits (most common variants only; the
// catch-up for 50+ isn't modeled).  Extend as years go on.
const IRA_LIMIT_BY_YEAR = {
  2018: 5500, 2019: 6000, 2020: 6000, 2021: 6000, 2022: 6000,
  2023: 6500, 2024: 7000, 2025: 7000, 2026: 7500,
};
// --- Federal tax reference tables ---
// Single source of truth is Python (src/analytics/tax.py), emitted into
// the JSON export under DATA.tax_tables.  We read from there so adding a
// new tax year is a one-file (Python) change.  `_TAX_TABLES` is always
// present in exports from current code (export.py always emits it, in the
// same run that generates this HTML).  `_loadBracketTable` converts the
// emitted `null` top-threshold sentinel back to Infinity.
const _TAX_TABLES = DATA.tax_tables || {};
function _loadBracketTable(emitted) {
  const out = Object.create(null);
  for (const yr of Object.keys(emitted || {})) {
    out[yr] = {};
    for (const status of Object.keys(emitted[yr])) {
      out[yr][status] = emitted[yr][status].map(
        row => [row[0] === null ? Infinity : row[0], row[1]]);
    }
  }
  return out;
}
const K401_LIMIT_BY_YEAR = _TAX_TABLES.k401_limit || {};
// Roth IRA MAGI phase-out windows per filing status.  Above the
// start, the allowable contribution scales linearly to zero across
// the window.  We use AGI as a MAGI proxy — close enough for most
// users (true MAGI adds back student-loan interest deduction,
// traditional IRA deduction, and a few rarer items).
// MFS has an unusually narrow $0–$10k window.
const ROTH_MAGI_PHASEOUT_BY_STATUS = _TAX_TABLES.roth_magi_phaseout || {
  'Single': {
    start: {
      2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
      2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
      2026: 153000
    },
    width: 15000,
  },
  'Head of Household': {
    start: {
      2018: 120000, 2019: 122000, 2020: 124000, 2021: 125000,
      2022: 129000, 2023: 138000, 2024: 146000, 2025: 150000,
      2026: 153000
    },
    width: 15000,
  },
  'Married Filing Jointly': {
    start: {
      2018: 189000, 2019: 193000, 2020: 196000, 2021: 198000,
      2022: 204000, 2023: 218000, 2024: 230000, 2025: 236000,
      2026: 240000
    },
    width: 10000,
  },
  'Married Filing Separately': {
    start: {
      2018: 0, 2019: 0, 2020: 0, 2021: 0, 2022: 0, 2023: 0,
      2024: 0, 2025: 0, 2026: 0
    },
    width: 10000,
  },
};

function filingStatus() {
  return (RETIREMENT_META && RETIREMENT_META.filing_status) || 'Single';
}

// Returns { status, allowedPct, magi, phaseStart, phaseEnd } for a given
// year string, or null if we can't estimate (no salary/AGI data).
//   status: 'eligible' | 'partial' | 'ineligible' | 'no-data'
//   allowedPct: 0..100 (the share of the IRS limit you can contribute)
function rothEligibilityFor(yr) {
  const filing = filingStatus();
  const ps = ROTH_MAGI_PHASEOUT_BY_STATUS[filing]
    || ROTH_MAGI_PHASEOUT_BY_STATUS['Single'];
  const start = ps.start[yr];
  const width = ps.width;
  if (start == null) return null;
  const est = ((ANALYTICS.tax || {}).rate_estimates_by_year || {})[yr];
  if (!est || !est.agi) return null;
  const magi = est.agi;
  const isProjection = !!est.is_projection;
  const end = start + width;
  const base = { magi, phaseStart: start, phaseEnd: end, isProjection };
  if (magi < start) return { ...base, status: 'eligible', allowedPct: 100 };
  if (magi >= end) return { ...base, status: 'ineligible', allowedPct: 0 };
  return {
    ...base, status: 'partial',
    allowedPct: Math.round(((end - magi) / (end - start)) * 100)
  };
}

// Compact horizontal bar showing MAGI position vs Roth phase-out range.
// Reference width = phase-out end; yellow band marks the phase-out window;
// fill grows from left to MAGI position and is colored by status.
function rothMagiBarHtml(elig) {
  if (!elig) {
    return '<span style="color:var(--text-dim);font-size:0.78rem;">—</span>';
  }
  const refMax = elig.phaseEnd;
  const fillPct = Math.min(100, (elig.magi / refMax) * 100);
  const phaseStartPct = (elig.phaseStart / refMax) * 100;
  const fillColor = elig.status === 'eligible' ? 'var(--green)'
    : elig.status === 'partial' ? 'var(--yellow)'
      : 'var(--red)';
  const label = elig.status === 'eligible' ? 'Eligible'
    : elig.status === 'partial' ? `${elig.allowedPct}% allowed`
      : 'Ineligible';
  const labelColor = elig.status === 'eligible' ? 'var(--green)'
    : elig.status === 'partial' ? 'var(--yellow)'
      : 'var(--red)';
  const projNote = elig.isProjection ? ' (projected)' : '';
  const tip = `MAGI ≈ ${fmtMoney(elig.magi, 0)}${projNote} (AGI proxy)\nPhase-out: ${fmtMoney(elig.phaseStart, 0)} – ${fmtMoney(elig.phaseEnd, 0)} (single)\nAllowed: ${elig.allowedPct}% of IRS limit`;
  return `<div class="magi-cell" title="${_htmlEsc(tip)}">
    <div class="magi-bar">
      <div class="magi-band-phaseout" style="left:${phaseStartPct.toFixed(2)}%;"></div>
      <div class="magi-fill" style="width:${fillPct.toFixed(2)}%;background:${fillColor};"></div>
    </div>
    <div class="magi-label">
      <span style="color:${labelColor};">${label}${elig.isProjection ? ' *' : ''}</span>
      <span class="magi-amt">${fmtMoneyShort(elig.magi)}</span>
    </div>
  </div>`;
}
const RETIREMENT_GROUPS = { '401K': '401k', 'Roth IRA': 'ira_roth', 'Rollover IRA': 'ira_rollover' };
function isRetirementGroup(group) {
  // The shared Performance set reads exported classifications and falls
  // back to account_type. Keep legacy names additive, matching Python's
  // retirement_groups() when an older export has incomplete metadata.
  return RETIREMENT_GROUP_SET.has(group) || Object.hasOwn(RETIREMENT_GROUPS, group);
}
// Actions that count as contributions/deposits for retirement accounts.
const RETIREMENT_CONTRIB_ACTIONS = new Set(['Contribution', 'Deposit']);

function yearOf(iso) { return (iso || '').slice(0, 4); }

// Classify a txn as a retirement contribution or not, and if so, the
// year to attribute it to (USAA's "PRIOR YEAR CONTRIBUTION" rows belong
// to the preceding year — these can happen from Jan 1 through the tax
// deadline).  Returns { isContrib: bool, year: string }.
function retirementContribInfo(t) {
  const g = t.account_group;
  if (!isRetirementGroup(g)) return { isContrib: false };
  const amt = t.amount || 0;
  if (amt <= 0) return { isContrib: false };

  const desc = (t.description || '').toLowerCase();
  const isPriorYear = desc.includes('prior year contribution');
  const isCurrentYear = desc.includes('current year contribution');
  const y = yearOf(t.date);

  // Schwab / Vanguard style: explicit Contribution / Deposit actions
  if (RETIREMENT_CONTRIB_ACTIONS.has(t.action)) {
    return { isContrib: true, year: y };
  }

  // Voya employer/admin error reversal — counts as negative contribution
  // so the year's total correctly offsets the original.  Signed amount
  // is returned so callers can sum directly.
  if (t.action === 'Contribution Reversal') {
    return { isContrib: true, year: y, signedAmount: -amt };
  }
  // Custodian moves are not contributions, including transfers into a
  // 401K (same rule as the Python classifier).
  if (t.action === 'Transfer In') return { isContrib: false };

  // USAA style: Buy with contribution marker in the description
  if ((isPriorYear || isCurrentYear) && g === 'Roth IRA') {
    const attrYear = isPriorYear && y
      ? String(parseInt(y, 10) - 1)
      : y;
    return { isContrib: true, year: attrYear };
  }

  return { isContrib: false };
}

// One dated, signed total feeds the scenario and cash-flow defaults.
// Comparing ISO dates keeps the inclusive first day independent of the
// browser timezone; the upper bound excludes future-dated imports.
function trailingRetirementContributions({ employeeOnly = false } = {}) {
  const start = shiftCalendarIso(SNAPSHOT_DATE, { years: -1 });
  let total = 0;
  for (const t of txns) {
    if (!t.date || t.date < start || t.date > SNAPSHOT_DATE) continue;
    const info = retirementContribInfo(t);
    if (!info.isContrib) continue;
    const desc = (t.description || '').toLowerCase();
    if (employeeOnly && (desc.includes('employer') || desc.includes('match'))) continue;
    total += info.signedAmount ?? (t.amount || 0);
  }
  return total;
}

function computeRetirementContributionsByYear() {
  // Prefer the Python-computed version for a single source of truth.
  // Falls back to in-JS computation if analytics isn't available
  // (e.g. the dashboard was generated by an older pipeline).
  const pre = ANALYTICS.retirement_contributions_by_year;
  if (pre && typeof pre === 'object') return pre;

  const rows = Object.create(null);
  for (const t of txns) {
    const info = retirementContribInfo(t);
    if (!info.isContrib) continue;
    const y = info.year;
    if (!y) continue;
    const amt = info.signedAmount ?? (t.amount || 0);
    if (!rows[y]) rows[y] = { '401K': 0, 'Roth IRA': 0, total: 0 };
    if (t.account_group === 'Roth IRA') rows[y]['Roth IRA'] += amt;
    else rows[y]['401K'] += amt;
    rows[y].total += amt;
  }
  return rows;
}

function computeRetirementSummary() {
  // Current balance + basis per account group, filtered to retirement.
  const byGroup = Object.create(null);
  for (const h of holdingsByAccount) {
    if (!isRetirementGroup(h.account_group)) continue;
    if (!(h.account_group in byGroup)) byGroup[h.account_group] = { value: 0, basis: 0 };
    if (typeof h.value === 'number') byGroup[h.account_group].value += h.value;
    if (typeof h.cost_basis === 'number') byGroup[h.account_group].basis += h.cost_basis;
  }
  let value = 0, basis = 0;
  for (const g of Object.keys(byGroup)) { value += byGroup[g].value; basis += byGroup[g].basis; }
  return { byGroup, value, basis };
}

// Personal historical annualized return on retirement capital.
// Uses (current_value / total_contributions_ever) ^ (1/years) - 1.
// This is a simple approximation that treats all contributions as
// equivalent (no time-weighting); close enough for a projection display.
function computePersonalRetirementRate(summary, contribsByYear) {
  let total = 0;
  let firstYear = null;
  for (const y of Object.keys(contribsByYear)) {
    total += contribsByYear[y].total;
    if (firstYear === null || y < firstYear) firstYear = y;
  }
  if (total <= 0 || !firstYear) return null;
  const years = Math.max(1, (snapshotDate() - new Date(firstYear + '-01-01')) / (365.25 * 86400000));
  if (years < 1) return null;
  const ratio = summary.value / total;
  if (ratio <= 0) return null;
  return Math.pow(ratio, 1 / years) - 1;
}

// Future value: FV = P*(1+r)^n + C*((1+r)^n - 1)/r
//   P = current principal, C = annual contribution, r = annual rate, n = years.
function projectFutureValue(principal, annualContrib, rate, years) {
  if (years <= 0) return principal;
  const g = Math.pow(1 + rate, years);
  if (Math.abs(rate) < 1e-9) return principal + annualContrib * years;
  return principal * g + annualContrib * ((g - 1) / rate);
}

// Default projection age comes from data/metadata.csv `Retirement Age`
// (default 67 when absent).  User can override per-render via the
// input on the Planning tab.
let retirementProjectionAge =
  (RETIREMENT_META && parseInt(RETIREMENT_META.retirement_age, 10)) || 67;
let retirementAnnualContrib = null;  // null = use auto-inferred

// --- Monte Carlo section (used by the Planning tab) ----------------------
let mcScenario = 'all_accounts';   // 'retirement' | 'all_accounts'

function _buildMonteCarloSection() {
  const mcRoot = ANALYTICS.monte_carlo;
  if (!mcRoot) return '';
  const mc = mcRoot[mcScenario] || mcRoot.all_accounts || mcRoot.retirement;
  if (!mc || !mc.bands || !mc.bands.length) return '';
  const bands = mc.bands;
  const s = mc.summary || {};
  const fire = mc.fire;
  const fiThreshold = mcRoot.fi_threshold;

  const startingTotal = (s.starting_equity || 0) + (s.starting_cash || 0);
  const cashStr = s.starting_cash > 0
    ? `<span style="color:var(--text-dim);font-size:0.72rem;"> · ${fmtMoneyShort(s.starting_cash)} cash @ ${((s.cash_yield || 0) * 100).toFixed(0)}%</span>`
    : '';
  const cards = [
    { label: 'Starting Balance', value: fmtMoneyShort(startingTotal), sub: cashStr },
    { label: 'Horizon', value: (s.years || 0) + 'y' },
    { label: 'P10 (Pessimistic)', value: fmtMoneyShort(s.p10_final || 0), cls: 'negative' },
    { label: 'P50 (Median)', value: fmtMoneyShort(s.p50_final || 0) },
    { label: 'P90 (Optimistic)', value: fmtMoneyShort(s.p90_final || 0), cls: 'positive' },
  ];
  const statsHtml = cards.map(c => `<div class="ds-card">
    <div class="ds-label">${_htmlEsc(c.label)}</div>
    <div class="ds-value ${c.cls || ''}">${c.value}${c.sub || ''}</div>
  </div>`).join('');

  // Scenario toggle pills
  const togglePills = `
    <div class="toggle-group">
      <button class="tbtn ${mcScenario === 'all_accounts' ? 'active' : ''}"
              onclick="setMonteCarloScenario('all_accounts')">All Accounts</button>
      <button class="tbtn ${mcScenario === 'retirement' ? 'active' : ''}"
              onclick="setMonteCarloScenario('retirement')">Retirement Only</button>
    </div>`;

  const svgId = 'mcFanSvg';
  const fireNote = fire && mcScenario === 'all_accounts'
    ? ` Dashed orange line = FI threshold (4% rule × annual expenses).`
    : '';
  const chartHtml = `<div class="mc-fan-wrap">
    <svg class="mc-fan" id="${svgId}" preserveAspectRatio="none"
         role="img"
         aria-label="Monte Carlo fan chart: simulated portfolio value to retirement, showing the median path and the 10th-to-90th and 25th-to-75th percentile bands. The cards above give the same percentiles as text."></svg>
    <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;">
      Shaded bands: 10th–90th percentile (outer) and 25th–75th (inner).  Solid line = median.
      Assumes ${((s.mean_return || 0) * 100).toFixed(0)}% mean / ${((s.stdev_return || 0) * 100).toFixed(0)}% stdev normal returns on the equity bucket, $${(s.annual_contribution || 0).toLocaleString()}/yr contributions.${fireNote}
    </div>
  </div>`;

  queueMicrotask(() => {
    const svg = document.getElementById(svgId);
    if (!svg || !bands.length) return;
    const W = Math.round(svg.getBoundingClientRect().width) || 800;
    const H = 280, PAD = { l: 64, r: 16, t: 10, b: 28 };
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    let maxY = Math.max(...bands.map(b => b.p90)) * 1.05;
    if (fiThreshold && mcScenario === 'all_accounts') {
      maxY = Math.max(maxY, fiThreshold * 1.1);
    }
    const minY = 0;
    const xOf = i => PAD.l + (bands.length === 1 ? plotW / 2 : (i * plotW) / (bands.length - 1));
    const yOf = v => PAD.t + plotH - ((v - minY) / (maxY - minY || 1)) * plotH;
    const parts = [];
    for (let k = 0; k <= 5; k++) {
      const v = minY + ((maxY - minY) * k) / 5;
      const y = yOf(v);
      parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
      parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${fmtMoneyShort(v)}</text>`);
    }
    const outerUp = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p90)}`).join(' ');
    const outerDn = [...bands].reverse().map((b, i) => {
      const origIdx = bands.length - 1 - i;
      return `L${xOf(origIdx)},${yOf(b.p10)}`;
    }).join(' ');
    parts.push(`<path d="${outerUp} ${outerDn} Z" class="mc-band-90"/>`);
    const innerUp = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p75)}`).join(' ');
    const innerDn = [...bands].reverse().map((b, i) => {
      const origIdx = bands.length - 1 - i;
      return `L${xOf(origIdx)},${yOf(b.p25)}`;
    }).join(' ');
    parts.push(`<path d="${innerUp} ${innerDn} Z" class="mc-band-50"/>`);
    const median = bands.map((b, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(b.p50)}`).join(' ');
    parts.push(`<path d="${median}" class="mc-median"/>`);
    // FIRE threshold horizontal line (only in all-accounts mode)
    if (fiThreshold && mcScenario === 'all_accounts') {
      const fy = yOf(fiThreshold);
      parts.push(`<line x1="${PAD.l}" y1="${fy}" x2="${W - PAD.r}" y2="${fy}" stroke="#fb923c" stroke-width="1.5" stroke-dasharray="6 4"/>`);
      parts.push(`<text x="${W - PAD.r - 4}" y="${fy - 4}" text-anchor="end" fill="#fb923c" style="font-size:10px;">FI ${fmtMoneyShort(fiThreshold)}</text>`);
    }
    const xTicks = Math.min(6, bands.length);
    for (let i = 0; i < xTicks; i++) {
      const idx = Math.round((i * (bands.length - 1)) / (xTicks - 1 || 1));
      const x = xOf(idx);
      parts.push(`<text class="axis-label" x="${x}" y="${H - 8}" text-anchor="middle">${bands[idx].calendar_year}</text>`);
    }
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
    parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join('');
  });

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Monte Carlo Projection</span></h2>
      ${togglePills}
      <span class="as-of-hint" style="margin-left:auto;">${mc.runs} stochastic runs — accounts for sequence-of-returns risk.</span>
    </div>
    <div class="mc-stats">${statsHtml}</div>
    ${chartHtml}
    ${_buildFireSection(mcRoot, mc)}
  `;
}

// --- FIRE (4% rule) section --------------------------------------------------
function _buildFireSection(mcRoot, mc) {
  const fire = mc.fire;
  const fi = mcRoot.fi_threshold;
  const annExp = mcRoot.annual_expenses;
  if (!fi || !annExp) {
    return `
      <div class="section-header" style="margin-top:24px;">
        <h2><span style="color:var(--accent);">FIRE (4% rule)</span></h2>
      </div>
      <div class="empty-state">
        Add an <code>Annual Expenses</code> row to <code>data/metadata.csv</code> to enable FIRE projections.  The classic 4% rule sets your FI number at 25× annual expenses.
      </div>`;
  }

  const startingTotal = (mc.summary.starting_equity || 0) + (mc.summary.starting_cash || 0);
  const fiProgress = fi > 0 ? Math.min(100, (startingTotal / fi) * 100) : 0;
  // Coast FI: balance today that, with no further contributions, grows
  // to FI by the end of the projection window at the mean return.
  // Solve: bal × (1 + r)^n = fi  →  coast = fi / (1 + r)^n
  const r = mc.summary.mean_return || 0.08;
  const n = mc.summary.years || 0;
  const coastFi = n > 0 ? fi / Math.pow(1 + r, n) : fi;
  const coastReached = startingTotal >= coastFi;

  // FIRE date estimation per percentile band
  const yearLabel = (yr) => yr == null ? 'never (within window)' : `${yr}y (age ${(yr + currentAgeFromMeta())})`;
  const reachPct = fire ? (fire.p_reaches_within_window * 100).toFixed(1) : null;

  const cards = [
    { label: 'Annual Expenses', value: fmtMoneyShort(annExp) },
    { label: 'FI Number (25×)', value: fmtMoneyShort(fi) },
    {
      label: 'FI Progress', value: fiProgress.toFixed(1) + '%',
      cls: fiProgress >= 100 ? 'positive' : ''
    },
    {
      label: 'Coast FI', value: fmtMoneyShort(coastFi),
      sub: coastReached
        ? '<span style="color:var(--green);font-size:0.72rem;"> ✓ already past it</span>'
        : `<span style="color:var(--text-dim);font-size:0.72rem;"> ${fmtMoneyShort(coastFi - startingTotal)} more</span>`
    },
  ];
  if (fire) {
    cards.push({
      label: 'P(reach FI in window)',
      value: reachPct + '%',
      cls: fire.p_reaches_within_window > 0.75 ? 'positive'
        : fire.p_reaches_within_window < 0.25 ? 'negative' : ''
    });
  }
  const statsHtml = cards.map(c => `<div class="ds-card">
    <div class="ds-label">${_htmlEsc(c.label)}</div>
    <div class="ds-value ${c.cls || ''}">${c.value}${c.sub || ''}</div>
  </div>`).join('');

  // FIRE date table per percentile
  const ageRows = fire ? [
    ['P10 (Pessimistic)', fire.p10_first_year, 'negative'],
    ['P25', fire.p25_first_year, ''],
    ['P50 (Median)', fire.p50_first_year, ''],
    ['P75', fire.p75_first_year, ''],
    ['P90 (Optimistic)', fire.p90_first_year, 'positive'],
  ].map(([lbl, yr, cls]) => `<tr>
    <td>${lbl}</td>
    <td class="num"><span class="${cls}">${yearLabel(yr)}</span></td>
  </tr>`).join('') : '';

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">FIRE (4% rule)</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">25× annual expenses; "Coast FI" assumes no more contributions.</span>
    </div>
    <!-- auto-fit, not a fixed count: an inline grid-template-columns
         beats the stylesheet's mobile rule, so repeat(N,1fr) held five
         columns at phone width and the last card hung off the screen.
         auto-fit collapses to as many columns as actually fit. -->
    <div class="mc-stats" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));">${statsHtml}</div>
    ${fire ? `<div class="panel" style="margin-top:14px;">
      <h3>Year first reaching FI (per percentile)</h3>
      <table class="mini-table"><caption class="sr-only">Year each simulated percentile first reaches financial independence</caption>
        <thead><tr><th scope="col">Outcome</th><th scope="col" class="num">Year offset (age at crossing)</th></tr></thead>
        <tbody>${ageRows}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Each row: the first year of the simulation in which that percentile band crosses your FI number.  All-accounts scenario only — the FI number is funded from total resources, not just retirement accounts.  Switch the toggle above to see retirement-only growth without the FIRE overlay.
      </div>
    </div>` : ''}
  `;
}

// Helper: current age from RETIREMENT_META.birthday — used to render
// "age at crossing" in the FIRE table.  Returns 0 if no birthday.
function currentAgeFromMeta() {
  return calendarAge(RETIREMENT_META.birthday) ?? 0;
}

function renderRetirement() {
  const root = document.getElementById('retirementContent');
  if (!root) return;

  const summary = computeRetirementSummary();
  const contribs = computeRetirementContributionsByYear();
  const personal = computePersonalRetirementRate(summary, contribs);

  // Auto-infer annual contribution from the last 12 months of retirement contribs.
  const today = snapshotDate();
  const autoAnnualContrib = Math.round(trailingRetirementContributions());
  const annualContribUsed = retirementAnnualContrib != null ? retirementAnnualContrib : autoAnnualContrib;

  // Age math
  const currentAge = calendarAge(RETIREMENT_META.birthday);
  const yearsToRetire = currentAge != null ? Math.max(0, retirementProjectionAge - currentAge) : null;

  // Stat cards
  const ytdKey = String(today.getFullYear());
  const ytd = (contribs[ytdKey] && contribs[ytdKey].total) || 0;
  const totalContribs = Object.values(contribs).reduce((s, r) => s + r.total, 0);
  const totalGain = summary.value - summary.basis;

  const statCards = [
    { label: 'Total Retirement', value: fmtMoney(summary.value), cls: 'positive' },
    { label: 'Cost Basis', value: fmtMoney(summary.basis) },
    { label: 'Unrealized Gain', value: fmtSigned(totalGain), cls: totalGain >= 0 ? 'positive' : 'negative' },
    { label: 'Contributions ' + ytdKey, value: fmtMoney(ytd) },
    { label: 'Contributions (all-time)', value: fmtMoney(totalContribs) },
    { label: 'Personal Rate (est)', value: personal != null ? (personal * 100).toFixed(2) + '%' : '—' },
    { label: 'Current Age', value: currentAge != null ? String(currentAge) : '—' },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Contributions by year table (401K column includes former Rollover IRA).
  // Hover any contribution amount to see that year's IRS limit.
  const years = Object.keys(contribs).sort();
  const contribRows = years.map(y => {
    const r = contribs[y];
    const limit401 = K401_LIMIT_BY_YEAR[y];
    const limitIra = IRA_LIMIT_BY_YEAR[y];
    const tip401 = limit401 ? `IRS limit: ${fmtMoney(limit401, 0)} (${Math.round(r['401K'] / limit401 * 100)}% used)` : '';
    const tipIra = limitIra ? `IRS limit: ${fmtMoney(limitIra, 0)} (${Math.round(r['Roth IRA'] / limitIra * 100)}% used)` : '';
    const eligCell = `<td>${rothMagiBarHtml(rothEligibilityFor(y))}</td>`;
    return `<tr>
      <td><b>${y}</b></td>
      <td class="num" title="${_htmlEsc(tip401)}">${fmtMoney(r['401K'])}</td>
      <td class="num" title="${_htmlEsc(tipIra)}">${fmtMoney(r['Roth IRA'])}</td>
      ${eligCell}
      <td class="num"><b>${fmtMoney(r.total)}</b></td>
    </tr>`;
  }).join('');

  // Retirement balance over time (chart): use history snapshots' by_account_type.Retirement
  const retPoints = history
    .map(h => ({ date: h.date, value: (h.by_account_type && h.by_account_type.Retirement) || 0 }))
    .filter(p => p.value > 0);
  const retBasisPoints = history
    .map(h => ({ date: h.date, value: (h.cost_basis_by_type && h.cost_basis_by_type.Retirement) || 0 }))
    .filter(p => p.value > 0);

  const chartHtml = renderMiniLineChart(retPoints, {
    id: 'retBalanceSvg',
    color: '#a78bfa',
    height: 260,
    yFormatter: fmtMoneyShort,
    emptyMsg: 'No retirement history yet.',
  });

  // Projection scenarios
  const scenarios = [
    { name: 'Conservative', rate: 0.05 },
    { name: 'Moderate', rate: 0.07 },
    { name: 'Optimistic', rate: 0.09 },
  ];
  if (personal != null) scenarios.push({ name: 'Personal', rate: personal });

  const projRows = yearsToRetire != null ? scenarios.map(s => {
    const fv = projectFutureValue(summary.value, annualContribUsed, s.rate, yearsToRetire);
    const gain = fv - summary.value - annualContribUsed * yearsToRetire;
    return `<tr>
      <td>${s.name}</td>
      <td class="num">${(s.rate * 100).toFixed(2)}%</td>
      <td class="num"><b>${fmtMoneyShort(fv)}</b></td>
      <td class="num"><span class="positive">${fmtSigned(gain)}</span></td>
    </tr>`;
  }).join('') : `<tr><td colspan="4" style="color:var(--text-dim);padding:12px;">Add a birthday to <code>data/metadata.csv</code> to enable projections.</td></tr>`;

  // Roth vs Traditional split — small inline stacked bar (replaces a
  // 2-slice pie which was visually weak for so few categories).
  let roth = 0, trad = 0;
  for (const h of holdingsByAccount) {
    if (typeof h.value !== 'number') continue;
    if (h.account_group === 'Roth IRA') roth += h.value;
    else if (h.account_group === '401K' || h.account_group === 'Rollover IRA') trad += h.value;
  }
  const splitTotal = roth + trad;
  const splitHtml = splitTotal > 0
    ? renderRothTradBar(roth, trad)
    : '<div style="color:var(--text-dim);padding:8px 0;">No retirement positions.</div>';

  // --- Render ---
  root.innerHTML = `
    ${statsHtml}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Contributions by Year</span></h2></div>
    <div class="panel">
      ${years.length ? `
        ${renderContribBars(years, contribs, { id: 'contribBars' })}
        <div style="display:flex;gap:24px;font-size:0.85rem;margin:4px 0 14px 10px;flex-wrap:wrap;">
          ${_legendSwatch(ACCOUNT_COLORS['401K'] || '#f59e0b', '401K (incl. Rollover IRA)')}
          ${_legendSwatch(ACCOUNT_COLORS['Roth IRA'] || '#a78bfa', 'Roth IRA')}
        </div>` : ''}
      <table class="mini-table"><caption class="sr-only">Retirement contributions by year and account, with Roth eligibility</caption>
        <thead><tr>
          <th scope="col">Year</th>
          <th scope="col" class="num">401K</th>
          <th scope="col" class="num">Roth IRA</th>
          <th scope="col">Roth Eligibility</th>
          <th scope="col" class="num">Total</th>
        </tr></thead>
        <tbody>${contribRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">No retirement contributions found.</td></tr>'}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.72rem;margin-top:6px;line-height:1.4;">
        Hover any contribution amount for the year's IRS limit.  Roth Eligibility bar: green fill = your MAGI; yellow band = the ${_htmlEsc(filingStatus())} phase-out range from <code>data/metadata.csv</code>.  MAGI estimated from salary + bonuses + portfolio income − 401K.  An asterisk means a year-end projection.
      </div>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Retirement Balance Over Time</span></h2></div>
    ${chartHtml}

    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Roth vs Traditional</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">For per-account-group breakdown, see <a onclick="setHoldingsView('account');activateTab('holdings')">Holdings → By Account</a>.</span>
    </div>
    <div class="panel">${splitHtml}</div>

    <div style="color:var(--text-dim);font-size:0.78rem;margin-top:24px;text-align:center;line-height:1.5;">
      Looking for projections, Monte Carlo, or FIRE?  Those moved to the
      <a onclick="activateTab('planning')" style="color:var(--accent);cursor:pointer;">Planning</a> tab.
    </div>
  `;
}

// Round a value up to a "nice" axis maximum (1/2/5 × 10ⁿ).
function _niceCeil(v) {
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * mag;
}

// Stacked bar chart of contributions per year (401K + Roth IRA).  Shows
// the trajectory a table can't: which years you front-loaded the Roth,
// when the 401K ramped, whether totals are trending up.  Same measure-
// then-rerender pattern as renderMiniLineChart so axis text stays crisp
// at any container width.
function renderContribBars(years, contribs, opts) {
  opts = opts || {};
  const id = opts.id || 'contribBars';
  const H = opts.height || 240;
  const label = opts.label ||
    'Retirement contributions per year, stacked by account. The table below carries the same figures.';
  if (!years.length) return `<div class="chart-empty">No contributions yet.</div>`;
  const build = (W) => _renderContribBarsContent(years, contribs, W, H);
  queueMicrotask(() => {
    const svg = document.getElementById(id);
    if (!svg) return;
    const W = Math.round(svg.getBoundingClientRect().width) || 800;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = build(W);
    _bindBarTooltips(svg, document.getElementById(id + '_tip'), svg.closest('.chart-wrap'));
  });
  return `<div class="chart-wrap" style="padding:10px;position:relative;">
    <svg id="${id}" class="chart-svg" viewBox="0 0 800 ${H}"
         role="img" aria-label="${_htmlEsc(label)}"
         style="width:100%;height:${H}px;display:block;"></svg>
    <div class="chart-tooltip" id="${id}_tip"></div>
  </div>`;
}

function _renderContribBarsContent(years, contribs, W, H) {
  const PAD = { l: 56, r: 16, t: 16, b: 28 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const n = years.length;
  const maxTotal = Math.max(...years.map(y => (contribs[y] || {}).total || 0), 1);
  const maxY = _niceCeil(maxTotal * 1.08);
  const yOf = v => PAD.t + plotH - (v / maxY) * plotH;
  const bandW = plotW / n;
  const barW = Math.min(52, bandW * 0.62);
  const colors = {
    '401K': ACCOUNT_COLORS['401K'] || '#f59e0b',
    'Roth IRA': ACCOUNT_COLORS['Roth IRA'] || '#a78bfa',
  };
  const parts = [];
  const yTicks = 4;
  for (let i = 0; i <= yTicks; i++) {
    const v = (maxY * i) / yTicks;
    const y = yOf(v);
    parts.push(`<line class="grid-line" x1="${PAD.l}" y1="${y}" x2="${W - PAD.r}" y2="${y}"/>`);
    parts.push(`<text class="axis-label" x="${PAD.l - 6}" y="${y + 3}" text-anchor="end">${fmtMoneyShort(v)}</text>`);
  }
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + plotH}"/>`);
  parts.push(`<line class="axis-line" x1="${PAD.l}" y1="${PAD.t + plotH}" x2="${W - PAD.r}" y2="${PAD.t + plotH}"/>`);
  years.forEach((y, i) => {
    const cx = PAD.l + bandW * i + bandW / 2;
    const x = cx - barW / 2;
    const r = contribs[y] || {};
    // One tooltip per year (full breakdown) shared by both segments.
    const tip = `<div class='tt-date'>${y}</div>`
      + ((r['401K'] || 0) > 0 ? _tipRow(colors['401K'], '401K', fmtMoney(r['401K'])) : '')
      + ((r['Roth IRA'] || 0) > 0 ? _tipRow(colors['Roth IRA'], 'Roth IRA', fmtMoney(r['Roth IRA'])) : '')
      + _tipRow('', 'Total', fmtMoney(r.total || 0), true);
    let cursor = yOf(0);
    for (const key of ['401K', 'Roth IRA']) {
      const val = r[key] || 0;
      if (val <= 0) continue;
      const h = (val / maxY) * plotH;
      const top = cursor - h;
      parts.push(`<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" fill="${colors[key]}" rx="1.5" data-tip="${_htmlEsc(tip)}"/>`);
      cursor = top;
    }
    if ((r.total || 0) > 0) {
      parts.push(`<text class="axis-label" x="${cx}" y="${(yOf(r.total) - 5).toFixed(1)}" text-anchor="middle" style="font-weight:600;">${fmtMoneyShort(r.total)}</text>`);
    }
    parts.push(`<text class="axis-label" x="${cx}" y="${H - 8}" text-anchor="middle">${y}</text>`);
  });
  return parts.join('');
}

// Small inline legend swatch + label (shared by the contrib bars).
function _legendSwatch(color, label) {
  return `<span><span style="display:inline-block;width:10px;height:10px;background:${color};margin-right:6px;border-radius:2px;vertical-align:middle;"></span>${label}</span>`;
}

// Inline horizontal stacked bar for the Roth/Trad split.  Cleaner than
// a 2-slice pie at a fraction of the pixels.
function renderRothTradBar(roth, trad) {
  const total = roth + trad;
  const rothPct = (roth / total) * 100;
  const tradPct = 100 - rothPct;
  return `
    <div style="display:flex;height:28px;border-radius:4px;overflow:hidden;border:1px solid var(--border);">
      <div style="width:${rothPct}%;background:${ACCOUNT_COLORS['Roth IRA'] || '#a78bfa'};"
           title="Roth: ${fmtMoney(roth)} (${rothPct.toFixed(1)}%)"></div>
      <div style="width:${tradPct}%;background:${ACCOUNT_COLORS['401K'] || '#f59e0b'};"
           title="Traditional: ${fmtMoney(trad)} (${tradPct.toFixed(1)}%)"></div>
    </div>
    <div style="display:flex;gap:24px;font-size:0.85rem;margin-top:8px;flex-wrap:wrap;">
      <span><span style="display:inline-block;width:10px;height:10px;background:${ACCOUNT_COLORS['Roth IRA']};margin-right:6px;border-radius:2px;vertical-align:middle;"></span>Roth: <b>${fmtMoney(roth)}</b> <span style="color:var(--text-dim);">(${rothPct.toFixed(1)}%)</span></span>
      <span><span style="display:inline-block;width:10px;height:10px;background:${ACCOUNT_COLORS['401K']};margin-right:6px;border-radius:2px;vertical-align:middle;"></span>Traditional: <b>${fmtMoney(trad)}</b> <span style="color:var(--text-dim);">(${tradPct.toFixed(1)}%)</span></span>
      <span style="color:var(--text-dim);">Total: ${fmtMoney(total)}</span>
    </div>
  `;
}

registerTabRenderer('retirement', renderRetirement);

