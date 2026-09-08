// =========================================================================
// Planning tab — forward-looking: scenario projection, Monte Carlo, FIRE.
// All retirement-tab content that's about the future moved here so the
// Retirement tab stays focused on account-specific facts and history.
// =========================================================================
function renderPlanning() {
  const root = document.getElementById('planningContent');
  if (!root) return;

  const summary = computeRetirementSummary();
  const contribs = computeRetirementContributionsByYear();
  const personal = computePersonalRetirementRate(summary, contribs);

  // Trailing-12-months retirement contribution (auto-inferred default for
  // the scenario projection's annual-contribution input)
  const autoAnnualContrib = Math.round(trailingRetirementContributions());
  const annualContribUsed = retirementAnnualContrib != null ? retirementAnnualContrib : autoAnnualContrib;

  // Years to projection age (default = metadata Retirement Age / 67 —
  // see retirementProjectionAge initializer)
  const currentAge = calendarAge(RETIREMENT_META.birthday);
  const yearsToRetire = currentAge != null ? Math.max(0, retirementProjectionAge - currentAge) : null;

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

  root.innerHTML = `
    <div class="section-header"><h2><span style="color:var(--accent);">Scenario Projection</span></h2>
      <span class="as-of-hint" style="margin-left:auto;">Constant-rate compound growth — simple but ignores volatility.  See Monte Carlo below for sequence-of-returns risk.</span>
    </div>
    <div class="panel">
      <div class="controls" style="margin-bottom:12px;">
        <label style="color:var(--text-dim);font-size:0.85rem;">Retirement age:
          <input type="number" id="retProjectionAge" value="${retirementProjectionAge}" min="40" max="90"
                 style="width:60px;" oninput="setRetirementProjectionAge(this.value)"/>
        </label>
        <label style="color:var(--text-dim);font-size:0.85rem;margin-left:16px;">Annual contribution:
          $<input type="number" id="retAnnualContrib" value="${annualContribUsed}" step="500"
                  style="width:100px;" oninput="setRetirementAnnualContrib(this.value)"/>
          <span style="color:var(--text-dim);font-size:0.72rem;"> (auto: ${fmtMoney(autoAnnualContrib)} — trailing 12 months)</span>
        </label>
      </div>
      <table class="mini-table"><caption class="sr-only">Projected portfolio value at retirement under each growth scenario</caption>
        <thead><tr>
          <th scope="col">Scenario</th><th scope="col" class="num">Annual Rate</th>
          <th scope="col" class="num">Value at age ${retirementProjectionAge}${yearsToRetire != null ? ` (${yearsToRetire}y)` : ''}</th>
          <th scope="col" class="num">Investment Gain</th>
        </tr></thead>
        <tbody>${projRows}</tbody>
      </table>
    </div>

    ${_buildMonteCarloSection()}
  `;

  // Year-by-Year table (goal tracking: balances vs targets + savings
  // rate) — its containers live in the tab-planning markup below
  // #planningContent, so re-render alongside the rest of the tab.
  if (typeof renderAnnualBreakdown === 'function') renderAnnualBreakdown();
}

// Re-render Planning when the projection-age / contribution inputs change
// (they were originally on Retirement, but they live on Planning now).
function setRetirementProjectionAge(v) {
  const n = parseInt(v, 10);
  if (!isNaN(n) && n > 20 && n < 100) {
    retirementProjectionAge = n;
    if (typeof renderPlanning === 'function') renderKeepingFocus(renderPlanning);
  }
}
function setRetirementAnnualContrib(v) {
  const n = parseFloat(v);
  if (!isNaN(n) && n >= 0) {
    retirementAnnualContrib = n;
    if (typeof renderPlanning === 'function') renderKeepingFocus(renderPlanning);
  }
}
// MC scenario toggle also lives on Planning now
function setMonteCarloScenario(s) {
  mcScenario = s;
  if (typeof renderPlanning === 'function') renderKeepingFocus(renderPlanning);
}

registerTabRenderer('planning', renderPlanning);

