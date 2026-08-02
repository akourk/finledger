// =========================================================================
// Overview tab: Top Holdings + Recent Transactions mini-tables
// =========================================================================

// Year-by-year breakdown table.  Columns:
//   Year, Age, [account: Σ, Δ%, Δ$] × N, Sum: Σ Δ% Δ$, Target
// Each account header is clickable to expand 3 extra contribution
// columns: contributed-this-year, cumulative contributed, year-over-
// year change in cumulative contributed.  State (which accounts are
// expanded) is per-page-load.
const _annualExpanded = new Set();   // account_group keys currently expanded
let _annualSumExpanded = false;       // Sum-vs-Target diff group expanded?

function _toggleAnnualExpand(acct) {
  if (_annualExpanded.has(acct)) _annualExpanded.delete(acct);
  else _annualExpanded.add(acct);
  renderAnnualBreakdown();
}

function _toggleAnnualSumExpand() {
  _annualSumExpanded = !_annualSumExpanded;
  renderAnnualBreakdown();
}

// Fidelity's age-based retirement savings benchmark — multiples of
// current salary that the average person should have saved by each
// age to stay on track for retirement at 67.  Interpolated linearly
// between checkpoints; flat outside the range.  Used as a fallback
// suggestion for the Year-by-Year Target column when no manual
// Target row exists for that year in metadata.csv.
const _FIDELITY_AGE_MULTIPLES = [
  [30, 1], [35, 2], [40, 3], [45, 4],
  [50, 6], [55, 7], [60, 8], [67, 10],
];
function _ageSalaryMultiple(age) {
  if (age == null || age === '') return null;
  const tbl = _FIDELITY_AGE_MULTIPLES;
  if (age <= tbl[0][0]) return tbl[0][1] * (age / tbl[0][0]);
  if (age >= tbl[tbl.length - 1][0]) return tbl[tbl.length - 1][1];
  for (let i = 0; i < tbl.length - 1; i++) {
    const [a1, m1] = tbl[i], [a2, m2] = tbl[i + 1];
    if (age >= a1 && age <= a2) return m1 + (m2 - m1) * (age - a1) / (a2 - a1);
  }
  return null;
}

// Convert "#rrggbb" → "rgba(r,g,b,a)" for low-alpha column tinting.
function _hexToRgba(hex, alpha) {
  if (!hex) return `rgba(167,139,250,${alpha})`;
  const h = hex.replace('#', '');
  const r = parseInt(h.substr(0, 2), 16);
  const g = parseInt(h.substr(2, 2), 16);
  const b = parseInt(h.substr(4, 2), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function renderAnnualBreakdown() {
  const host = document.getElementById('annualBreakdown');
  if (!host) return;
  if (!history.length) { host.innerHTML = ''; return; }

  // The accounts to show — fixed order matches the user's spreadsheet.
  // We derive the column list from groups that actually appear in the
  // user's data so the table doesn't render empty columns for accounts
  // they don't have.
  const PREFERRED_ORDER = ['Roth IRA', '401K', 'Rollover IRA', 'Robinhood', 'Coinbase', 'Apple Savings'];
  const SHORT_LABEL = { 'Apple Savings': 'Savings' };
  const seenGroups = new Set();
  for (const h of history) {
    for (const g of Object.keys(h.by_account_group || {})) seenGroups.add(g);
  }
  const accounts = PREFERRED_ORDER.filter(a => seenGroups.has(a));
  // Catch any account_groups not in the preferred order (e.g. user
  // adds a new broker) — append them so they're visible.
  for (const g of seenGroups) if (!accounts.includes(g)) accounts.push(g);

  // Year-end snapshots: pick the last snapshot of each calendar year.
  // For the current year, that's "today" (latest snapshot), which is
  // a valid year-end-so-far.
  const yearEndSnaps = {};
  for (const h of history) {
    const y = (h.date || '').slice(0, 4);
    if (y) yearEndSnaps[y] = h;
  }
  const years = Object.keys(yearEndSnaps).sort();

  // Per-year-per-account contribution events (net cash_flow).  Walk
  // txns once.  For accounts the user filters out we still walk
  // them — the cost is negligible vs the cleanliness of data flow.
  const contribByYearAcct = {};
  for (const t of txns) {
    const y = (t.date || '').slice(0, 4);
    const acct = t.account_group;
    const cf = typeof t.cash_flow === 'number' ? t.cash_flow : 0;
    if (!y || !acct || cf === 0) continue;
    if (!contribByYearAcct[y]) contribByYearAcct[y] = {};
    contribByYearAcct[y][acct] = (contribByYearAcct[y][acct] || 0) + cf;
  }
  // Cumulative contributions per account by year-end.
  const cumContribByAcct = {};
  for (const a of accounts) cumContribByAcct[a] = {};
  for (const a of accounts) {
    let running = 0;
    for (const y of years) {
      running += (contribByYearAcct[y] || {})[a] || 0;
      cumContribByAcct[a][y] = running;
    }
  }

  // Target lookup from metadata.csv (Type=Target rows).
  const targetsByYear = {};
  for (const t of (RETIREMENT_META.targets || [])) {
    targetsByYear[t.year] = t.amount;
  }

  // Birthday → age computation.  Year is the December-31-of-year.
  const birthdayDate = RETIREMENT_META.birthday ? new Date(RETIREMENT_META.birthday) : null;
  const ageAtYearEnd = (year) => {
    if (!birthdayDate) return '';
    const yearEnd = new Date(`${year}-12-31`);
    const age = yearEnd.getFullYear() - birthdayDate.getFullYear()
      - ((yearEnd.getMonth() < birthdayDate.getMonth() ||
        (yearEnd.getMonth() === birthdayDate.getMonth()
          && yearEnd.getDate() < birthdayDate.getDate())) ? 1 : 0);
    return age >= 0 ? age : '';
  };

  // Salary at year-end — find the latest Salary History entry whose
  // effective date is on or before Dec 31 of the given year.  Used for
  // the Fidelity age × salary suggested target.
  const _salariesSorted = (RETIREMENT_META.salary_history || [])
    .slice()
    .sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  const salaryAtYearEnd = (year) => {
    const cutoff = `${year}-12-31`;
    let latest = null;
    for (const s of _salariesSorted) {
      if ((s.date || '') <= cutoff) latest = s.amount;
      else break;
    }
    return latest;
  };

  // Per-account inline tints (background + accent color for header
  // text).  Subtle alphas — these read as a column-grouping cue, not
  // a paint job.
  const _acctTint = (a, alpha) => _hexToRgba(ACCOUNT_COLORS[a] || '#a78bfa', alpha);
  const TINT_HEAD = 0.10, TINT_CELL = 0.05, TINT_CONTRIB = 0.07;

  // Cell formatters
  const fmtCell = (v) => fmtMoneyShort(v);
  const fmtPctCell = (v) => v == null ? '—' : ((v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%');
  const fmtSignedCell = (v) => v == null ? '—' : (v >= 0 ? '+' : '−') + fmtMoneyShort(Math.abs(v)).replace('$', '$');
  const cls = (v) => v == null ? '' : (v > 0 ? 'positive' : v < 0 ? 'negative' : '');

  // ----- HEADER -------------------------------------------------------
  // Row 1: Year, Age, [account name (colspan = 3 collapsed / 6 expanded)]…, Sum (colspan 3), Target
  // Row 2: per-account sub-columns
  // Savings rate per year — external net contributions ÷ gross income
  // (salary + bonus from metadata).  Precomputed in analytics.savings_by_year.
  const savingsByYear = ANALYTICS.savings_by_year || {};
  const haveSavings = Object.keys(savingsByYear).length > 0;

  let h1 = '<tr><th rowspan="2" class="ab-sticky-col">Year</th><th rowspan="2">Age</th>';
  if (haveSavings) {
    h1 += '<th rowspan="2" title="Savings rate — external net contributions across all accounts ÷ gross income (salary + bonus from metadata.csv)">Sav%</th>';
  }
  let h2 = '<tr>';
  for (const a of accounts) {
    const expanded = _annualExpanded.has(a);
    const colspan = expanded ? 6 : 3;
    const arrow = expanded ? '▾' : '▸';
    const label = SHORT_LABEL[a] || a;
    const acctColor = ACCOUNT_COLORS[a] || 'var(--accent)';
    const headTint = _acctTint(a, TINT_HEAD);
    const cellTint = _acctTint(a, TINT_CELL);
    const contribTint = _acctTint(a, TINT_CONTRIB);
    h1 += `<th colspan="${colspan}" class="ab-acct-head${expanded ? ' ab-expanded' : ''}" data-acct="${_htmlEsc(a)}" style="background:${headTint};color:${acctColor};border-bottom:2px solid ${acctColor};" title="Click to ${expanded ? 'collapse' : 'expand'} contribution columns">
      <span class="ab-acct-arrow">${arrow}</span>${_htmlEsc(label)}
    </th>`;
    if (expanded) {
      h2 += `<th class="num ab-sub" style="background:${contribTint};" title="Net cash flow into this account this year">contr.</th>
             <th class="num ab-sub" style="background:${contribTint};" title="Cumulative net cash flow into this account through year end">Σ contr.</th>
             <th class="num ab-sub" style="background:${contribTint};" title="Year-over-year change in cumulative contributions">Δ contr.</th>`;
    }
    h2 += `<th class="num ab-sub" style="background:${cellTint};" title="Year-end balance">Σ</th>
           <th class="num ab-sub" style="background:${cellTint};" title="Year-over-year % change of year-end balance">Δ%</th>
           <th class="num ab-sub" style="background:${cellTint};" title="Year-over-year $ change of year-end balance">Δ$</th>`;
  }
  // Sum group (always 3 columns)
  const sumExpanded = _annualSumExpanded;
  const sumArrow = sumExpanded ? '▾' : '▸';
  const sumTip = sumExpanded
    ? 'Click to hide Target comparison columns'
    : 'Click to show Target columns (year-end Sum vs target)';
  h1 += `<th colspan="3" class="ab-sum-head ab-sum-toggle${sumExpanded ? ' ab-expanded' : ''}" title="${sumTip}">
    <span class="ab-acct-arrow">${sumArrow}</span>Sum
  </th>`;
  h2 += `<th class="num ab-sub">Σ</th>
         <th class="num ab-sub">Δ%</th>
         <th class="num ab-sub">Δ$</th>`;
  if (sumExpanded) {
    h1 += `<th colspan="3" class="ab-target-head">Target</th>`;
    h2 += `<th class="num ab-sub" title="Year-end target">Σ</th>
           <th class="num ab-sub" title="% above (+) or below (−) target">Δ%</th>
           <th class="num ab-sub" title="$ above (+) or below (−) target">Δ$</th>`;
  }
  h1 += `</tr>`;
  h2 += `</tr>`;

  // ----- ROWS ---------------------------------------------------------
  const bodyRows = years.map((year, i) => {
    const snap = yearEndSnaps[year];
    const prevYear = i > 0 ? years[i - 1] : null;
    const prevSnap = prevYear ? yearEndSnaps[prevYear] : null;
    const cells = [
      `<th class="ab-sticky-col">${year}</th>`,
      `<td class="num">${ageAtYearEnd(year)}</td>`,
    ];
    if (haveSavings) {
      const sv = savingsByYear[year];
      if (sv && sv.savings_rate_pct != null) {
        const tip = `Contributed ${fmtMoney(sv.net_contributed)} of ${fmtMoney(sv.gross_income)} gross income`;
        const svCls = sv.savings_rate_pct >= 0 ? 'positive' : 'negative';
        cells.push(`<td class="num ${svCls}" title="${_htmlEsc(tip)}">${sv.savings_rate_pct.toFixed(0)}%</td>`);
      } else {
        cells.push('<td class="num">—</td>');
      }
    }
    let sumNow = 0, sumPrev = 0;
    for (const a of accounts) {
      const v = (snap.by_account_group || {})[a] || 0;
      const vPrev = prevSnap ? ((prevSnap.by_account_group || {})[a] || 0) : 0;
      const dDollar = v - vPrev;
      const dPct = vPrev > 0 ? dDollar / vPrev : (v > 0 && !prevSnap ? null : null);
      sumNow += v;
      sumPrev += vPrev;
      const cellBg = _acctTint(a, TINT_CELL);
      const contribBg = _acctTint(a, TINT_CONTRIB);
      const expanded = _annualExpanded.has(a);
      if (expanded) {
        const yc = (contribByYearAcct[year] || {})[a] || 0;
        const cumNow = cumContribByAcct[a][year] || 0;
        const cumPrev = prevYear ? (cumContribByAcct[a][prevYear] || 0) : 0;
        const dCum = cumNow - cumPrev;
        cells.push(
          `<td class="num ab-contrib ${cls(yc)}" style="background:${contribBg};">${yc !== 0 ? fmtSignedCell(yc) : '—'}</td>`,
          `<td class="num ab-contrib" style="background:${contribBg};">${cumNow !== 0 ? fmtCell(cumNow) : '—'}</td>`,
          `<td class="num ab-contrib ${cls(dCum)}" style="background:${contribBg};">${dCum !== 0 ? fmtSignedCell(dCum) : '—'}</td>`,
        );
      }
      cells.push(
        `<td class="num" style="background:${cellBg};">${v !== 0 ? fmtCell(v) : '—'}</td>`,
        `<td class="num ${cls(dPct)}" style="background:${cellBg};">${prevSnap ? fmtPctCell(dPct) : '—'}</td>`,
        `<td class="num ${cls(dDollar)}" style="background:${cellBg};">${prevSnap && dDollar !== 0 ? fmtSignedCell(dDollar) : '—'}</td>`,
      );
    }
    const dSumDollar = sumNow - sumPrev;
    const dSumPct = sumPrev > 0 ? dSumDollar / sumPrev : null;
    cells.push(
      `<td class="num ab-sum-cell"><b>${fmtCell(sumNow)}</b></td>`,
      `<td class="num ab-sum-cell ${cls(dSumPct)}">${prevSnap ? fmtPctCell(dSumPct) : '—'}</td>`,
      `<td class="num ab-sum-cell ${cls(dSumDollar)}">${prevSnap && dSumDollar !== 0 ? fmtSignedCell(dSumDollar) : '—'}</td>`,
    );

    // Target group: 3 cells — target value | %-diff vs sum | $-diff vs sum.
    // Hidden by default; revealed when the Sum header is clicked.
    // User-entered Target rows in metadata.csv win.  Otherwise
    // compute a Fidelity-style suggestion = current salary × age-based
    // multiple — rendered dimmed/italic with a tooltip so it's
    // obviously a suggestion, not a hard goal.  Δ% / Δ$ are signed:
    // positive = above target, negative = below.
    if (_annualSumExpanded) {
      const userTarget = targetsByYear[year];
      let targetVal = null;
      let targetIsSuggested = false;
      let targetTip = '';
      if (userTarget != null) {
        targetVal = userTarget;
      } else {
        const age = ageAtYearEnd(year);
        const salary = salaryAtYearEnd(year);
        const mult = _ageSalaryMultiple(age);
        if (salary != null && mult != null) {
          targetVal = salary * mult;
          targetIsSuggested = true;
          targetTip = `Suggested (Fidelity benchmark): ${mult.toFixed(1)}× salary at age ${age} — ${fmtMoney(salary)} × ${mult.toFixed(1)} = ${fmtMoney(targetVal)}`;
        }
      }
      if (targetVal != null) {
        const dDol = sumNow - targetVal;
        const dPct = targetVal > 0 ? dDol / targetVal : null;
        const valCls = targetIsSuggested ? 'ab-target-suggest' : 'ab-target-user';
        const dCls = targetIsSuggested ? 'ab-target-diff-suggest' : '';
        const tipAttr = targetTip ? ` title="${_htmlEsc(targetTip)}"` : '';
        cells.push(
          `<td class="num ab-target-cell ${valCls}"${tipAttr}>${fmtCell(targetVal)}</td>`,
          `<td class="num ab-target-diff ${dCls} ${cls(dPct)}">${dPct != null ? fmtPctCell(dPct) : '—'}</td>`,
          `<td class="num ab-target-diff ${dCls} ${cls(dDol)}">${dDol !== 0 ? fmtSignedCell(dDol) : '—'}</td>`,
        );
      } else {
        cells.push(
          `<td class="num ab-target-cell">—</td>`,
          `<td class="num ab-target-diff">—</td>`,
          `<td class="num ab-target-diff">—</td>`,
        );
      }
    }
    return '<tr>' + cells.join('') + '</tr>';
  }).join('');

  host.innerHTML = `<div class="ab-scroll">
    <table class="annual-breakdown">
      <thead>${h1}${h2}</thead>
      <tbody>${bodyRows}</tbody>
    </table>
  </div>`;

  // Wire account-header clicks for expand/collapse.  Delegated so the
  // single re-render hands off cleanly.
  host.querySelectorAll('.ab-acct-head').forEach(el => {
    el.addEventListener('click', () => _toggleAnnualExpand(el.dataset.acct));
  });
  // Sum header click reveals/hides the Target group.
  const sumToggle = host.querySelector('.ab-sum-toggle');
  if (sumToggle) sumToggle.addEventListener('click', _toggleAnnualSumExpand);
}

function renderTopHoldings() {
  const head = document.getElementById('topHoldingsHead');
  const body = document.getElementById('topHoldingsBody');
  const rows = asOfHoldingsByAsset()
    .filter(h => typeof h.value === 'number')
    .sort((a, b) => (b.value || 0) - (a.value || 0))
    .slice(0, 15);
  head.innerHTML = `
    <th>Symbol</th>
    <th>Sector</th>
    <th class="num">Quantity</th>
    <th class="num">Price</th>
    <th class="num">Value</th>
    <th class="num">Gain</th>`;
  body.innerHTML = rows.map(h => {
    const ug = typeof h.unrealized_gain === 'number' ? h.unrealized_gain : null;
    const ugStr = ug == null ? '—'
      : `<span class="${ug >= 0 ? 'positive' : 'negative'}">${fmtSigned(ug)}</span>`;
    const sectorColor = SECTOR_COLORS[h.sector] || '#9ca3af';
    const sectorStr = h.sector ? `<span style="color:${sectorColor}">${h.sector}</span>` : '';
    return `<tr>
      <td><b>${symLabel(h.symbol)}</b></td>
      <td>${sectorStr}</td>
      <td class="num">${(h.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="num">${fmtMoney(h.price)}</td>
      <td class="num">${fmtMoney(h.value)}</td>
      <td class="num">${ugStr}</td>
    </tr>`;
  }).join('');
}

function renderRecentTransactions() {
  const head = document.getElementById('recentTxnsHead');
  const body = document.getElementById('recentTxnsBody');
  // When an as-of-date is set, show the most recent txns at or
  // before that date (so "recent" means "recent as of the selected
  // view date", not "recent lifetime").  15 rows inside a fixed-height
  // scroll region (matches Top Holdings so the two side-by-side panels
  // stay the same height); ~8 visible — a glanceable pulse-check —
  // and the "View all →" footer link covers the rest.
  const cutoff = asOfDate;
  const rows = txns
    .filter(t => !t.date || t.date <= cutoff)
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 15);
  head.innerHTML = `
    <th>Date</th>
    <th>Account</th>
    <th>Symbol</th>
    <th>Action</th>
    <th class="num">Qty</th>
    <th class="num">Amount</th>`;
  body.innerHTML = rows.map(t => {
    const actionColor = ACTION_COLORS[t.action] || '';
    const actionSpan = actionColor
      ? `<span style="color:${actionColor}">${t.action || ''}</span>`
      : (t.action || '');
    const acctColor = ACCOUNT_COLORS[t.account_group] || '';
    const acctSpan = acctColor
      ? `<span style="color:${acctColor}">${t.account_group || ''}</span>`
      : (t.account_group || '');
    return `<tr>
      <td>${t.date || ''}</td>
      <td>${acctSpan}</td>
      <td>${symLabel(t.symbol || '')}</td>
      <td>${actionSpan}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="num">${fmtMoney(t.amount)}</td>
    </tr>`;
  }).join('');
}

// =========================================================================
// Overview tab: Allocation donut (By Account / Type / Sector)
// =========================================================================

// Three side-by-side donuts: By Account, By Type, By Sector.  No
// toggle — they all render at once.  Hover any slice for the
// segment's label + dollar value via SVG <title> tooltips, plus a
// compact legend below each donut.
function _renderOneAllocationDonut(svgEl, legendEl, field, palette) {
  if (!svgEl) return;
  let agg = {};
  if (isAsOfLatest()) {
    for (const h of holdingsByAccount) {
      if (typeof h.value !== 'number') continue;
      const k = h[field] || 'Unknown';
      agg[k] = (agg[k] || 0) + h.value;
    }
  } else {
    const snap = getAsOfSnapshot();
    const src = snap && (field === 'account_group' ? snap.by_account_group
      : field === 'account_type' ? snap.by_account_type
        : snap.by_sector);
    if (src) agg = { ...src };
  }
  const entries = Object.entries(agg)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);
  const total = entries.reduce((s, [, v]) => s + v, 0);

  // Compact donut sized for the 3-up grid.  ViewBox is square so
  // CSS can scale it responsively.
  const CX = 100, CY = 100, R_OUT = 80, R_IN = 50;
  const parts = [];
  let theta = -Math.PI / 2;
  for (const [k, v] of entries) {
    const frac = v / total;
    const end = theta + frac * Math.PI * 2;
    const large = (end - theta) > Math.PI ? 1 : 0;
    const x0 = CX + R_OUT * Math.cos(theta);
    const y0 = CY + R_OUT * Math.sin(theta);
    const x1 = CX + R_OUT * Math.cos(end);
    const y1 = CY + R_OUT * Math.sin(end);
    const xi1 = CX + R_IN * Math.cos(end);
    const yi1 = CY + R_IN * Math.sin(end);
    const xi0 = CX + R_IN * Math.cos(theta);
    const yi0 = CY + R_IN * Math.sin(theta);
    const color = palette[k] || '#9ca3af';
    const d = [
      `M${x0.toFixed(2)},${y0.toFixed(2)}`,
      `A${R_OUT},${R_OUT} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`,
      `L${xi1.toFixed(2)},${yi1.toFixed(2)}`,
      `A${R_IN},${R_IN} 0 ${large} 0 ${xi0.toFixed(2)},${yi0.toFixed(2)}`,
      'Z',
    ].join(' ');
    const pct = ((v / total) * 100).toFixed(1);
    const tipText = `${k}: ${fmtMoney(v)} (${pct}%)`;
    // <title> nested inside the path is the SVG-native hover tooltip.
    // Browser delay is ~0.5s.  Also use a CSS hover effect (set in
    // styles.css) for visual feedback on rollover.
    parts.push(
      `<path class="alloc-slice" d="${d}" fill="${color}" stroke="var(--bg)" stroke-width="1.5">` +
      `<title>${_htmlEsc(tipText)}</title></path>`
    );
    theta = end;
  }
  parts.push(`<text x="${CX}" y="${CY - 3}" text-anchor="middle" fill="var(--text-dim)" font-size="10">Total</text>`);
  parts.push(`<text x="${CX}" y="${CY + 13}" text-anchor="middle" fill="var(--text)" font-size="13" font-weight="600">${fmtMoneyShort(total)}</text>`);

  svgEl.setAttribute('viewBox', '0 0 200 200');
  svgEl.innerHTML = parts.join('');

  // Compact legend — top 8 entries inline; "+N more" if the rest spill.
  // 8 rows ≈ the side-by-side donut's height, so the legend fills the
  // card instead of leaving dead space under 5 rows.
  const TOP_N = 8;
  const top = entries.slice(0, TOP_N);
  const rest = entries.slice(TOP_N);
  const restTotal = rest.reduce((s, [, v]) => s + v, 0);
  const restPct = total > 0 ? ((restTotal / total) * 100).toFixed(1) : '0';
  const rows = top.map(([k, v]) => {
    const color = palette[k] || '#9ca3af';
    const pct = ((v / total) * 100).toFixed(1);
    return `<div class="allocation-legend-row" title="${_htmlEsc(k + ': ' + fmtMoney(v) + ' (' + pct + '%)')}">
      <span class="alloc-label"><span class="alloc-swatch" style="background:${color}"></span><span class="alloc-name">${_htmlEsc(k)}</span></span>
      <span class="alloc-value">${pct}%</span>
    </div>`;
  });
  if (rest.length) {
    rows.push(`<div class="allocation-legend-row alloc-more"
      title="${_htmlEsc(rest.map(([k, v]) => k + ': ' + fmtMoney(v)).join('\n'))}">
      <span class="alloc-label"><span class="alloc-swatch" style="background:#6b7280"></span>+${rest.length} more</span>
      <span class="alloc-value">${restPct}%</span>
    </div>`);
  }
  if (legendEl) legendEl.innerHTML = rows.join('');
}

function renderAllocation() {
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgGroup'),
    document.getElementById('allocationLegendGroup'),
    'account_group', ACCOUNT_COLORS,
  );
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgType'),
    document.getElementById('allocationLegendType'),
    'account_type', TYPE_COLORS,
  );
  _renderOneAllocationDonut(
    document.getElementById('allocationSvgSector'),
    document.getElementById('allocationLegendSector'),
    'sector', SECTOR_COLORS,
  );
}

// Helper for compact money formatting used by the donut center label.
function fmtMoneyShort(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (n >= 1e6) return sign + '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return sign + '$' + (n / 1e3).toFixed(1) + 'k';
  return sign + '$' + n.toFixed(0);
}

// --- Overview feedback: alerts + changes + reconciliation -----------------
// ONE collapsible card.  Three earlier cards (Status / What's Changed /
// Reconciliation) sat in a 2-column grid, which stretched a collapsed
// neighbor to the expanded card's height — a big blank panel.  Merging
// them into a single <details> keeps the Overview's initial scroll
// tight (one summary line carrying every signal as chips) and expands
// into clearly-headed sections:
//   Attention (actionable signals) · Data Health (pipeline integrity)
//   · What's Changed (diff vs last run) · Reconciliation (vs broker docs)
// Summary tint = the most severe signal across all sections.
// --- Reconciliation drill-down --------------------------------------------
// Click an income / realized reconcile row to expand the transactions
// composing fin's computed figure — turns every delta into a self-serve
// investigation.  Membership rules MIRROR analytics/reconcile.py through
// the same exported sources of truth: the action catalog's `income`
// field (income buckets) and `section_1256_underlyings` (realized
// split), so the drill-down's sum always reproduces the computed figure
// (the footer verifies it visually).  Balance rows have no txn
// composition — they compare against a history snapshot — so they
// don't expand.
const reconExpanded = new Set();
function toggleReconRow(i) {
  if (reconExpanded.has(i)) reconExpanded.delete(i); else reconExpanded.add(i);
  renderOverviewStatus();
}

const _RECON_INCOME_BUCKETS = {
  income: ['dividends', 'interest', 'lending'],
  other_income: ['rewards', 'lending'],
};
const _RECON_DRILLABLE = new Set(['income', 'other_income',
                                  'realized', 'section_1256']);

function _reconIncomeKindByAction() {
  const m = {};
  for (const a of ((DATA.action_catalog || {}).actions || [])) {
    if (a.income) m[a.name] = a.income;
  }
  return m;
}

function reconDrillHtml(r, colspan) {
  const year = (r.date || '').slice(0, 4);
  if (!year) return '';
  const rows = [];
  let total = 0;
  if (r.kind === 'income' || r.kind === 'other_income') {
    const kinds = new Set(_RECON_INCOME_BUCKETS[r.kind]);
    const kindOf = _reconIncomeKindByAction();
    for (const t of DATA.transactions || []) {
      if (t.account_group !== r.account_group) continue;
      if ((t.date || '').slice(0, 4) !== year) continue;
      const k = kindOf[t.action];
      if (!k || !kinds.has(k)) continue;
      const amt = +t.amount || 0;
      total += amt;
      rows.push({ date: t.date, action: t.action, sym: t.symbol || '',
                  desc: t.description || '', v: amt });
    }
  } else if (r.kind === 'realized' || r.kind === 'section_1256') {
    const want1256 = r.kind === 'section_1256';
    for (const t of DATA.transactions || []) {
      if (t.account_group !== r.account_group) continue;
      if ((t.date || '').slice(0, 4) !== year) continue;
      if (typeof t.realized_gain !== 'number') continue;
      if (isSection1256Symbol(t.symbol) !== want1256) continue;
      total += t.realized_gain;
      rows.push({ date: t.date, action: t.action, sym: t.symbol || '',
                  desc: t.description || '', v: t.realized_gain });
    }
  } else {
    return '';
  }

  // Per-source subtotals — the investigative view.  A single security's
  // payments summing to the delta (the pattern behind most reconcile
  // drift) jumps out here where a chronological list hides it.
  const bySrc = new Map();
  for (const x of rows) {
    const key = x.sym || x.action;
    const cur = bySrc.get(key) || { n: 0, v: 0 };
    cur.n += 1; cur.v += x.v;
    bySrc.set(key, cur);
  }
  const srcChips = [...bySrc.entries()]
    .sort((a, b) => Math.abs(b[1].v) - Math.abs(a[1].v))
    .map(([k, s]) => `<span class="recon-src-chip">${_htmlEsc(k)}&nbsp;·&nbsp;${s.n}×&nbsp;·&nbsp;${fmtMoney(s.v, 2)}</span>`)
    .join(' ');

  const isRealized = (r.kind === 'realized' || r.kind === 'section_1256');
  const valHead = isRealized ? 'realized' : 'amount';
  const body = rows
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    .map(x => `<tr>
        <td>${_htmlEsc(x.date || '')}</td>
        <td>${_htmlEsc(x.action)}</td>
        <td title="${_htmlEsc(x.desc)}">${_htmlEsc(x.sym)}</td>
        <td class="num ${x.v < 0 ? 'negative' : ''}">${fmtMoney(x.v, 2)}</td>
      </tr>`).join('');
  const parity = (r.computed != null && Math.abs(total - r.computed) < 0.01)
    ? '<span class="positive" title="The listed transactions reproduce fin\'s computed figure exactly">✓ matches fin</span>'
    : `<span class="negative" title="Drill-down sum differs from fin's computed figure — worth reporting">Σ ${fmtMoney(total, 2)} ≠ fin ${fmtMoney(r.computed, 2)}</span>`;
  // .recon-drill-outer (width:0 / min-width:100%) detaches this cell's
  // content from the outer table's layout algorithm — without it, wide
  // drill content sets a minimum width for the WHOLE reconcile table
  // and the browser spreads the extra across all columns, pushing
  // Reported/fin/Δ out of the visible half-column.  Wide content
  // scrolls inside .recon-drill instead.
  return `<tr class="recon-drill-row"><td colspan="${colspan}">
      <div class="recon-drill-outer">
        <div class="recon-drill">
          <div class="recon-src-chips">${srcChips}</div>
          <table class="lots-table">
            <thead><tr><th>date</th><th>action</th><th>symbol</th>
              <th class="num">${valHead}</th></tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
        <div class="recon-drill-foot">${rows.length} transaction${rows.length === 1 ? '' : 's'} · Σ ${fmtMoney(total, 2)} · ${parity}</div>
      </div>
    </td></tr>`;
}

function renderOverviewStatus() {
  const host = document.getElementById('overviewStatus');
  if (!host) return;
  // Re-renders happen on drill-down toggles — keep the card's
  // open/closed state instead of collapsing it under the click.
  const prevDetails = host.querySelector('details.feedback-collapsible');
  const wasOpen = prevDetails ? prevDetails.open : false;
  const alerts = ANALYTICS.alerts || [];
  const issues = ANALYTICS.data_health || [];
  const changes = ANALYTICS.changes || {};

  const sections = [];      // left column: Attention / Data Health / What's Changed
  let reconSection = '';    // right column: Reconciliation (table-heavy)
  const chips = [];         // summary-line chips
  const sevRank = { info: 0, warn: 1, high: 2 };
  let dominant = 'info';
  const bump = s => { if (sevRank[s] > sevRank[dominant]) dominant = s; };

  // ----- Attention + Data Health sections ------------------------------
  const haveAlerts = alerts.length > 0;
  const haveIssues = issues.length > 0;
  if (haveAlerts || haveIssues) {
    const counts = { high: 0, warn: 0, info: 0 };
    for (const a of alerts) counts[a.severity || 'info']++;
    for (const i of issues) counts[i.severity || 'info']++;
    bump(counts.high > 0 ? 'high' : counts.warn > 0 ? 'warn' : 'info');
    for (const s of ['high', 'warn', 'info']) {
      if (counts[s]) chips.push(`<span class="dh-chip sev-${s}">${counts[s]} ${s}</span>`);
    }
    if (haveAlerts) {
      const alertRows = alerts.map(a => {
        const sev = a.severity || 'info';
        return `<div class="alert-row">
          <span class="sev sev-${_htmlEsc(sev)}">${_htmlEsc(sev)}</span>
          <span>${_htmlEsc(a.message || '')}</span>
        </div>`;
      }).join('');
      sections.push(`<div class="dh-category">
        <h4>Attention <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${alerts.length} actionable signal${alerts.length === 1 ? '' : 's'}</span></h4>
        ${alertRows}
      </div>`);
    }
    if (haveIssues) {
      const byCat = {};
      for (const i of issues) {
        const c = i.category || 'Other';
        (byCat[c] = byCat[c] || []).push(i);
      }
      const issueParts = [];
      for (const cat of Object.keys(byCat)) {
        const rows = byCat[cat].map(i => {
          const detailsList = (i.details || []).map(d =>
            `<li>${_htmlEsc(d)}</li>`).join('');
          return `<div class="dh-issue">
            <div class="dh-issue-head">
              <span class="dh-chip sev-${_htmlEsc(i.severity)}">${_htmlEsc(i.severity)}</span>
              <span class="dh-message">${_htmlEsc(i.message)}</span>
            </div>
            ${detailsList ? `<ul class="dh-details">${detailsList}</ul>` : ''}
          </div>`;
        }).join('');
        issueParts.push(`<div class="dh-subcategory"><h5>${_htmlEsc(cat)}</h5>${rows}</div>`);
      }
      sections.push(`<div class="dh-category">
        <h4>Data Health <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${issues.length} pipeline-integrity item${issues.length === 1 ? '' : 's'}</span></h4>
        ${issueParts.join('')}
      </div>`);
    }
  }

  // ----- What's Changed section ----------------------------------------
  if (changes && !changes.first_run && Object.keys(changes).length) {
    const fmt = n => n == null ? '—' : (n >= 0 ? '+' : '') + fmtMoney(n, 0);
    const cls = n => (n > 0 ? 'positive' : n < 0 ? 'negative' : '');
    const rows = [];
    if (changes.txn_count_delta != null && changes.txn_count_delta !== 0) {
      rows.push(['New Transactions', (changes.txn_count_delta >= 0 ? '+' : '') + changes.txn_count_delta, cls(changes.txn_count_delta)]);
    }
    // Zero deltas are noise — only surface figures that actually moved.
    if (changes.value_delta) rows.push(['Portfolio Value', fmt(changes.value_delta), cls(changes.value_delta)]);
    if (changes.basis_delta) rows.push(['Cost Basis', fmt(changes.basis_delta), cls(changes.basis_delta)]);
    if (changes.realized_delta) rows.push(['Realized P&L', fmt(changes.realized_delta), cls(changes.realized_delta)]);

    const moverRows = [
      ...(changes.top_gainers || []).slice(0, 3),
      ...(changes.top_losers || []).slice(0, 3),
    ].map(m => {
      const chg = m.delta || 0;
      return `<div class="mover-row">
        <span class="sym">${_htmlEsc(m.symbol || '')}</span>
        <span class="${cls(chg)}">${fmt(chg)}</span>
      </div>`;
    }).join('');

    const newClosed = [];
    if (changes.new_symbols && changes.new_symbols.length) {
      newClosed.push(`<div class="mover-row"><span class="sym">New:</span><span>${changes.new_symbols.slice(0, 5).map(_htmlEsc).join(', ')}</span></div>`);
    }
    if (changes.closed_symbols && changes.closed_symbols.length) {
      newClosed.push(`<div class="mover-row"><span class="sym">Closed:</span><span>${changes.closed_symbols.slice(0, 5).map(_htmlEsc).join(', ')}</span></div>`);
    }
    const prevRun = (changes.prev_run_at || '').slice(0, 10);
    const sinceLabel = prevRun ? `since ${_htmlEsc(prevRun)}` : 'since last run';
    if (rows.length || moverRows || newClosed.length) {
      if (changes.value_delta) {
        const vc = cls(changes.value_delta);
        chips.push(`<span class="dh-chip ${vc}" style="background:transparent;border:1px solid currentColor;">${fmt(changes.value_delta)}</span>`);
      }
      sections.push(`<div class="dh-category">
        <h4>What's Changed <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${sinceLabel}</span></h4>
        <div class="changes-list">
          ${rows.map(([l, v, c]) => `<div class="change-row"><span class="ch-label">${l}</span><span class="ch-val ${c}">${v}</span></div>`).join('')}
        </div>
        ${moverRows ? `<div class="changes-movers"><h4>Top Movers</h4>${moverRows}</div>` : ''}
        ${newClosed.length ? `<div class="changes-movers"><h4>Positions</h4>${newClosed.join('')}</div>` : ''}
      </div>`);
    }
  }

  // ----- Reconciliation section -----------------------------------------
  // fin's computed figures vs broker-reported ground truth (statement
  // balances, 1099-B realized / §1256, 1099-DIV+INT income) supplied via
  // `Reconcile *` rows in metadata.csv.  Surfaces drift rather than
  // hiding it — wash sales, broker non-FIFO lot relief, and cash-sweep
  // interest that brokers report on the 1099 but omit from the CSV all
  // show up here as expected, explainable deltas.
  const recon = ANALYTICS.reconciliation;
  if (recon && recon.rows && recon.rows.length) {
    const sevOf = { ok: 'info', explained: 'info', warn: 'warn',
                    off: 'high', nodata: 'info' };
    const s = recon.summary || {};
    bump(s.off ? 'high' : s.warn ? 'warn' : 'info');
    const fmtN = v => v == null ? '—' : fmtMoney(v, 2);
    const fmtD = v => v == null ? '—' : (v >= 0 ? '+' : '') + fmtMoney(v, 2);
    const bodyRows = recon.rows.map((r, i) => {
      const drillable = _RECON_DRILLABLE.has(r.kind)
        && r.computed != null && (r.date || '').length >= 4;
      const expanded = drillable && reconExpanded.has(i);
      const chev = drillable
        ? `<span class="recon-chev">${expanded ? '▾' : '▸'}</span> `
        : '';
      let html = `<tr${drillable ? ` class="recon-clickable" onclick="toggleReconRow(${i})" title="Click to see the transactions composing fin's figure"` : ''}>
        <td>${chev}${_htmlEsc(r.account_group || '')}</td>
        <td${r.note ? ` title="${_htmlEsc(r.note)}" class="recon-noted"` : ''}>${_htmlEsc(r.label || '')}</td>
        <td style="text-align:right;">${fmtN(r.reported)}</td>
        <td style="text-align:right;">${fmtN(r.computed)}</td>
        <td style="text-align:right;" class="${r.delta > 0 ? 'positive' : r.delta < 0 ? 'negative' : ''}">${fmtD(r.delta)}</td>
        <td><span class="dh-chip sev-${sevOf[r.status] || 'info'}">${_htmlEsc(r.status)}</span>${r.detail ? ` <span style="color:var(--text-dim);font-size:0.85em;">${_htmlEsc(r.detail)}</span>` : ''}</td>
      </tr>`;
      if (expanded) html += reconDrillHtml(r, 6);
      return html;
    }).join('');
    const total = s.total || recon.rows.length;
    for (const k of ['off', 'warn']) {
      if (s[k]) chips.push(`<span class="dh-chip sev-${sevOf[k]}">${s[k]} ${k}</span>`);
    }
    const okish = (s.ok || 0) + (s.explained || 0);
    chips.push(`<span class="dh-chip sev-info">${okish}/${total} reconcile${s.explained ? ` (${s.explained} explained)` : ''}</span>`);
    reconSection = `<div class="dh-category">
      <h4>Reconciliation <span style="color:var(--text-dim);font-weight:400;text-transform:none;letter-spacing:0;">— ${total} check${total === 1 ? '' : 's'} vs broker docs</span></h4>
      <table>
        <thead><tr>
          <th>Account</th><th>Check</th>
          <th style="text-align:right;">Reported</th>
          <th style="text-align:right;">fin</th>
          <th style="text-align:right;">Δ</th>
          <th>Status</th>
        </tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
      <div style="color:var(--text-dim);font-size:0.8rem;margin-top:8px;">
        Click an income / realized row to see the transactions composing
        fin's figure.  Add <code>Reconcile Balance/Realized/Income/Section 1256</code>
        rows to <code>metadata.csv</code> (Symbol = account group, Date = as-of
        date or year) to check more accounts.
      </div>
    </div>`;
  }

  // ----- Assemble the single card ---------------------------------------
  if (!sections.length && !reconSection) {
    // Nothing flagged anywhere → tiny "all clear" line so the user can
    // see the dashboard ran clean.
    host.innerHTML = `<details class="feedback-panel feedback-collapsible" open style="opacity:0.55;">
      <summary class="dh-summary dh-clean">
        <span class="dh-label">Status</span>
        <span class="dh-status">all clear · no alerts, no integrity issues</span>
      </summary>
    </details>`;
    return;
  }
  // Two-column body at wide widths: Attention + Data Health + What's
  // Changed stack in the left half; the (table-heavy) Reconciliation
  // takes the right half — keeps the expanded card compact.  Collapses
  // to one column when either side is absent or the window is narrow
  // (media query in styles.css).
  const body = (sections.length && reconSection)
    ? `<div class="dh-body-cols"><div>${sections.join('')}</div><div>${reconSection}</div></div>`
    : (sections.join('') + reconSection);
  host.innerHTML = `<details class="feedback-panel feedback-collapsible"${wasOpen ? ' open' : ''}>
    <summary class="dh-summary dh-${dominant}">
      <span class="dh-label">Status</span>
      ${chips.join(' ')}
      <span class="dh-hint">click to expand</span>
    </summary>
    <div class="dh-body">${body}</div>
  </details>`;
}

// Backward-compat shim — older call sites kept invoking the old name.
function renderOverviewFeedback() { renderOverviewStatus(); }

// --- Monthly P&L grid (year × month heatmap) — Performance tab section ----
const _MONTH_LABELS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
const _MONTH_FULL = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function _buildMonthlyPnlSection() {
  const mp = ANALYTICS.monthly_pnl;
  if (!mp || !mp.rows || !mp.rows.length) return '';
  const minR = mp.min_return || 0, maxR = mp.max_return || 0;
  // Color scale: red below 0, green above; intensity proportional to
  // the row's distance from 0 vs the most-extreme observed |return|.
  const span = Math.max(Math.abs(minR), Math.abs(maxR), 0.001);
  const colorFor = (r) => {
    if (r == null) return 'transparent';
    const intensity = Math.min(1, Math.abs(r) / span);
    const alpha = 0.18 + intensity * 0.62;
    return r >= 0 ? `rgba(74, 222, 128, ${alpha.toFixed(2)})`
      : `rgba(248, 113, 113, ${alpha.toFixed(2)})`;
  };

  const headerCells = _MONTH_LABELS.map((m, i) =>
    `<th class="num" title="${_MONTH_FULL[i]}">${m}</th>`).join('');
  const rowsHtml = mp.rows.map(row => {
    let yearTotal = 0;
    const cells = [];
    for (let m = 1; m <= 12; m++) {
      const r = row.months[m];
      if (r == null) {
        cells.push(`<td class="mp-cell"></td>`);
      } else {
        yearTotal = (1 + yearTotal) * (1 + r) - 1;
        const tip = `${_MONTH_FULL[m - 1]} ${row.year}: ${(r * 100).toFixed(2)}%`;
        cells.push(`<td class="mp-cell" style="background:${colorFor(r)};" title="${_htmlEsc(tip)}">${(r * 100).toFixed(1)}</td>`);
      }
    }
    const ytdCls = yearTotal >= 0 ? 'positive' : 'negative';
    return `<tr>
      <th class="mp-year">${row.year}</th>
      ${cells.join('')}
      <td class="mp-ytd"><span class="${ytdCls}">${(yearTotal * 100).toFixed(1)}%</span></td>
    </tr>`;
  }).join('');

  const best = mp.best_month;
  const worst = mp.worst_month;
  const filteredNote = mp.n_months_filtered > 0
    ? ` <span style="margin-left:8px;">· ${mp.n_months_filtered} month(s) excluded from best/worst and Sharpe/Sortino (early-portfolio noise + |return| &gt; 50% data-artifact filter)</span>`
    : '';
  const summary = (best && worst) ? `
    <div style="display:flex;gap:24px;font-size:0.78rem;color:var(--text-dim);margin-top:8px;flex-wrap:wrap;">
      <span>Best month: <b class="positive">${_MONTH_FULL[best.month - 1]} ${best.year} +${(best.return * 100).toFixed(2)}%</b></span>
      <span>Worst month: <b class="negative">${_MONTH_FULL[worst.month - 1]} ${worst.year} ${(worst.return * 100).toFixed(2)}%</b></span>
      ${filteredNote}
    </div>` : '';

  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Monthly P&L</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">
        Investment return per month — net of contributions and withdrawals.  YTD compounds the row's monthly returns, so it can differ slightly from the Annual Returns table (Modified Dietz with mid-period flow weighting) — both are correct, they just weight intra-period flows differently.
      </span>
    </div>
    <div class="panel">
      <table class="monthly-pnl-table">
        <thead><tr><th></th>${headerCells}<th class="num">YTD</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
      ${summary}
    </div>
  `;
}

// --- Daily P&L bars (last 30 days) — Performance tab section --------------
function _buildDailyPnlSection() {
  const rows = Array.isArray(ANALYTICS.daily_pnl) ? ANALYTICS.daily_pnl : [];
  if (!rows.length) return '';
  const maxAbs = Math.max(...rows.map(r => Math.abs(r.change || 0)), 1);
  const bars = rows.map(r => {
    const pnl = r.change || 0;
    const h = Math.max(1, (Math.abs(pnl) / maxAbs) * 100);
    const neg = pnl < 0;
    const sign = pnl >= 0 ? '+' : '';
    const pct = r.change_pct || 0;
    const pctStr = ` (${sign}${pct.toFixed(2)}%)`;
    const tipText = `${r.date}: ${sign}${fmtMoney(pnl, 0)}${pctStr}`;
    return `<div class="daily-pnl-bar ${neg ? 'neg' : ''}" title="${_htmlEsc(tipText)}">
      <div class="tip">${_htmlEsc(r.date)}: <b>${sign}${fmtMoney(pnl, 0)}</b>${pctStr}</div>
      <div class="bar" style="height:${h}%"></div>
    </div>`;
  }).join('');
  return `
    <div class="section-header" style="margin-top:24px;">
      <h2><span style="color:var(--accent);">Recent Daily P&L</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">
        Market-only moves (today's positions repriced at recent dates) — same-day cash flows excluded.
      </span>
    </div>
    <div class="daily-pnl-bars">${bars}</div>
  `;
}

// --- Concentration grid ---------------------------------------------------
function renderConcentration() {
  const host = document.getElementById('concentrationContainer');
  if (!host) return;
  const c = ANALYTICS.concentration || {};
  const hhiGlobal = c.herfindahl != null ? c.herfindahl.toFixed(0) : '—';
  const top5Global = c.top_5_concentration != null ? fmtPct(c.top_5_concentration, 1) : '—';
  // By Sector deliberately excludes Cash — see analytics/concentration.py
  // for the rationale.  Show a small footnote if cash was excluded so
  // the % values being "of equity" instead of "of total" is transparent.
  const cashExcluded = c.cash_excluded_from_sectors || 0;
  const sectorFootnote = cashExcluded > 0.01
    ? `<div class="conc-foot" title="Concentration risk is about equity exposure — cash is the absence of risk, so % values here are of invested capital, not total portfolio.">% of equity (excludes ${fmtMoney(cashExcluded)} cash)</div>`
    : '';
  const sections = [
    { rows: c.positions || [], title: 'By Position', labelKey: 'symbol', footnote: '' },
    { rows: c.sectors || [], title: 'By Sector', labelKey: 'sector', footnote: sectorFootnote },
    { rows: c.account_groups || [], title: 'By Account', labelKey: 'account_group', footnote: '' },
  ];
  const cards = sections.map((s, i) => {
    const top = s.rows.slice(0, 5);
    if (!top.length) return '';
    const sectionTop5 = fmtPct(top.reduce((sum, r) => sum + (r.pct || 0), 0), 1);
    const rows = top.map(r => {
      const pct = r.pct || 0;
      let cls = 'ok';
      if (pct >= 20) cls = 'high';
      else if (pct >= 10) cls = 'warn';
      const w = Math.min(100, pct * 2);
      const label = r[s.labelKey] || '';
      return `<div class="conc-row ${cls}">
        <span class="conc-label" title="${_htmlEsc(label)}">${_htmlEsc(label)}</span>
        <span class="conc-bar-wrap"><span class="conc-bar" style="width:${w}%"></span></span>
        <span class="conc-pct">${fmtPct(pct, 1)}</span>
      </div>`;
    }).join('');
    // One HHI is enough: the global (positions) figure on the first
    // card.  Per-card HHIs were quant noise — the bars + Top-5 share
    // already communicate the concentration story.
    const top5 = i === 0 ? top5Global : sectionTop5;
    const hhiChip = i === 0
      ? `<span title="Herfindahl index of position weights — under 1000 is diversified, over 2500 concentrated.">HHI <b>${hhiGlobal}</b></span>`
      : '';
    return `<div class="concentration-card">
      <h4>${s.title}</h4>
      <div class="conc-stats">
        ${hhiChip}
        <span>Top 5 <b>${top5}</b></span>
      </div>
      ${rows}
      ${s.footnote}
    </div>`;
  }).join('');
  host.innerHTML = `<div class="concentration-grid">${cards}</div>`;
}

// --- Data Health collapsible panel (Overview tab) ------------------------
// Surfaces pipeline-integrity diagnostics from analytics.data_health.
// Default-collapsed via the <details> element so it doesn't crowd the
// page; the summary line shows the issue count + severity breakdown.
// Data Health is now folded into renderOverviewStatus().  This shim
// keeps any leftover call sites working as no-ops.
function renderDataHealth() { /* merged into renderOverviewStatus() */ }

// Overview tab renderer: kicks off top holdings / recent txns / allocation.
// Overview is a snapshot: value, change, anything wrong.  Concentration
// moved to Holdings (holdings-risk view); Year-by-Year moved to Planning
// (goal tracking); Daily P&L lives on Performance.
registerTabRenderer('overview', () => {
  renderOverviewStatus();
  renderTopHoldings();
  renderRecentTransactions();
  renderAllocation();
});
// Overview is active on initial load, so render its lazy bits now.
if (document.getElementById('tab-overview').classList.contains('active')) {
  renderOverviewStatus();
  renderTopHoldings();
  renderRecentTransactions();
  renderAllocation();
  TAB_RENDERED.add('overview');
}
// Concentration renders into the Holdings tab's container.  Holdings
// content is built eagerly at load (not via the lazy tab router), so
// render this once here too — the container is simply hidden until the
// tab activates.
renderConcentration();

// Render a row of stat cards from a `[{label, value, cls?, title?}]`
// array.  Used by every tab that has a `<div class="stats">…</div>`
// block — Overview, Holdings, Performance, Options, etc.  Inline
// duplicates of the same template existed at every call site before
// this helper was extracted.
//
//   cards       — [{ label, value, cls?, title? }]
//   extraClass  — optional extra class for the wrapper (e.g.
//                 "opt-anchor-stats" for the Options anchor row)
function _renderStatCards(cards, extraClass) {
  const wrapCls = extraClass ? `stats ${extraClass}` : 'stats';
  return `<div class="${wrapCls}">` + cards.map(c => {
    const cls = c.cls ? `stat-card ${c.cls}` : 'stat-card';
    const titleAttr = c.title ? ` title="${_htmlEsc(c.title)}"` : '';
    return `<div class="${cls}"${titleAttr}><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`;
  }).join('') + '</div>';
}

// Render one account-filter chip with the standardized look used
// across every filter bar: colored text from ACCOUNT_COLORS when
// inactive, full purple background when active.  Drop-in for any
// "Account:" toggle row.
//
//   account  — string account_group name, or null for the "All" reset
//   active   — true if this chip is the currently-selected filter
//   onclick  — JS string for the onclick attribute (already escaped)
//   label    — optional override for the visible text (defaults to
//              the account name, or "All" when account is null)
function _renderAccountChip(account, active, onclickJs, label) {
  const text = label != null ? label : (account || 'All');
  const color = account ? (ACCOUNT_COLORS[account] || '') : '';
  // Inactive chips with a known color use the color as text — matches
  // the colored-text pattern across all tabs.  Active chips inherit
  // the white-on-purple .tbtn.active styling, no inline color needed.
  const styleAttr = (!active && color) ? ` style="color:${color};"` : '';
  return `<button class="tbtn${active ? ' active' : ''}"${styleAttr} onclick="${onclickJs}">${text}</button>`;
}

