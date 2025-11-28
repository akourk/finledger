/**
 * Retirement Module
 * FIRE tracker, savings rate, projections, salary history
 */

// Format compact currency (1.2M, 500K, etc.)
function formatCompactCurrency(value) {
    if (value >= 1000000) {
        return '$' + (value / 1000000).toFixed(1) + 'M';
    } else if (value >= 1000) {
        return '$' + (value / 1000).toFixed(0) + 'K';
    }
    return formatCurrency(value);
}

// Get retirement data
function getRetirementData() {
    if (typeof retirementData !== 'undefined') {
        return retirementData;
    }
    return null;
}

// Create retirement section
function createRetirementSection() {
    var data = getRetirementData();
    if (!data) return;

    createFireTracker(data);
    createSavingsRate(data);
    createRetirementProjections(data);
    createMonteCarloSummary();
    createMonteCarloConeChart();
    createScenarioAnalysis();
    createWithdrawalAnalysis();
    createRetirementProjectionChart(data);
    createSalaryInfo(data);
    createContributionLimits(data);
    createSalaryHistoryChart(data);
    createRetirementAccountsChart(data);
    createBudgetBreakdown(data);
    createBonusHistoryTable(data);
    createSalaryHistoryTable(data);
}

// Get Monte Carlo data
function getMonteCarloData() {
    if (typeof monteCarloData !== 'undefined') {
        return monteCarloData;
    }
    return {};
}

// Create Monte Carlo summary cards
function createMonteCarloSummary() {
    var container = document.getElementById('monteCarloSummary');
    if (!container) return;

    var data = getMonteCarloData();

    if (!data.main_simulation) {
        container.innerHTML = '<p class="muted">No Monte Carlo data available. Run the portfolio aggregator to generate projections.</p>';
        return;
    }

    var sim = data.main_simulation;
    var params = sim.parameters || {};
    var stats = sim.final_statistics || {};
    var probs = sim.probabilities || {};
    var realStats = sim.real_final_statistics || {};

    var html = '<div class="monte-carlo-hero">';

    // Main projection
    html += '<div class="mc-main">';
    html += '<div class="mc-label">Projected Portfolio (' + params.years + ' years)</div>';
    html += '<div class="mc-range">';
    html += '<span class="mc-low">' + formatCompactCurrency(stats.p10) + '</span>';
    html += '<span class="mc-median positive">' + formatCompactCurrency(stats.median) + '</span>';
    html += '<span class="mc-high">' + formatCompactCurrency(stats.p90) + '</span>';
    html += '</div>';
    html += '<div class="mc-range-labels">';
    html += '<span>10th %ile</span><span>Median</span><span>90th %ile</span>';
    html += '</div>';
    html += '</div>';

    html += '</div>';

    // Probability cards
    html += '<div class="monte-carlo-cards">';

    // Parameters card
    html += '<div class="mc-card">';
    html += '<div class="mc-card-label">Simulation Parameters</div>';
    html += '<div class="mc-card-content">';
    html += '<div class="mc-stat-row"><span>Starting Value:</span><span>' + formatCurrency(params.starting_value) + '</span></div>';
    html += '<div class="mc-stat-row"><span>Annual Contribution:</span><span>' + formatCurrency(params.annual_contribution) + '</span></div>';
    html += '<div class="mc-stat-row"><span>Expected Return:</span><span>' + params.mean_return + '%</span></div>';
    html += '<div class="mc-stat-row"><span>Volatility:</span><span>' + params.volatility + '%</span></div>';
    html += '<div class="mc-stat-row"><span>Simulations:</span><span>' + params.num_simulations.toLocaleString() + '</span></div>';
    html += '</div>';
    html += '</div>';

    // Probabilities card
    html += '<div class="mc-card">';
    html += '<div class="mc-card-label">Outcome Probabilities</div>';
    html += '<div class="mc-card-content">';
    html += '<div class="mc-stat-row"><span>Double your money:</span><span class="positive">' + probs.double + '%</span></div>';
    html += '<div class="mc-stat-row"><span>Triple your money:</span><span class="positive">' + probs.triple + '%</span></div>';
    if (probs.million !== null) {
        html += '<div class="mc-stat-row"><span>Reach $1 million:</span><span class="positive">' + probs.million + '%</span></div>';
    }
    html += '<div class="mc-stat-row"><span>Lose money:</span><span class="negative">' + probs.loss + '%</span></div>';
    html += '</div>';
    html += '</div>';

    // Real returns card (inflation-adjusted)
    html += '<div class="mc-card">';
    html += '<div class="mc-card-label">Inflation-Adjusted (Real)</div>';
    html += '<div class="mc-card-content">';
    html += '<div class="mc-stat-row"><span>10th Percentile:</span><span>' + formatCompactCurrency(realStats.p10) + '</span></div>';
    html += '<div class="mc-stat-row"><span>Median:</span><span class="positive">' + formatCompactCurrency(realStats.median) + '</span></div>';
    html += '<div class="mc-stat-row"><span>90th Percentile:</span><span>' + formatCompactCurrency(realStats.p90) + '</span></div>';
    html += '<div class="mc-stat-row subdued"><span>Inflation Rate:</span><span>' + params.inflation_rate + '%/yr</span></div>';
    html += '</div>';
    html += '</div>';

    html += '</div>';

    container.innerHTML = html;
}

// Create Monte Carlo probability cone chart
function createMonteCarloConeChart() {
    var ctx = document.getElementById('monteCarloConeChart');
    if (!ctx) return;

    var data = getMonteCarloData();

    if (!data.main_simulation || !data.main_simulation.yearly_values) {
        ctx.parentElement.innerHTML = '<p class="muted" style="text-align: center; padding: 50px;">No Monte Carlo projection data available</p>';
        return;
    }

    var sim = data.main_simulation;
    var years = sim.years_list || [];
    var values = sim.yearly_values || {};

    // Create datasets for the probability cone
    var datasets = [
        // 10-90 percentile band (outer)
        {
            label: '10th-90th Percentile',
            data: values.p90,
            borderColor: 'transparent',
            backgroundColor: 'rgba(74, 222, 128, 0.15)',
            fill: '+1',
            pointRadius: 0
        },
        {
            label: '',
            data: values.p10,
            borderColor: 'transparent',
            backgroundColor: 'rgba(74, 222, 128, 0.15)',
            fill: false,
            pointRadius: 0
        },
        // 25-75 percentile band (inner)
        {
            label: '25th-75th Percentile',
            data: values.p75,
            borderColor: 'transparent',
            backgroundColor: 'rgba(74, 222, 128, 0.25)',
            fill: '+1',
            pointRadius: 0
        },
        {
            label: '',
            data: values.p25,
            borderColor: 'transparent',
            backgroundColor: 'rgba(74, 222, 128, 0.25)',
            fill: false,
            pointRadius: 0
        },
        // Median line
        {
            label: 'Median (50th)',
            data: values.p50,
            borderColor: '#4ade80',
            backgroundColor: 'transparent',
            borderWidth: 3,
            fill: false,
            pointRadius: 0
        },
        // Mean line
        {
            label: 'Mean',
            data: values.mean,
            borderColor: '#60a5fa',
            backgroundColor: 'transparent',
            borderWidth: 2,
            borderDash: [5, 5],
            fill: false,
            pointRadius: 0
        }
    ];

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: years.map(function (y) { return 'Year ' + y; }),
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: '#888',
                        font: { size: 11 },
                        filter: function (item) {
                            return item.text !== '';
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            if (context.dataset.label === '') return null;
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#888', maxTicksLimit: 10 },
                    grid: { display: false }
                },
                y: {
                    ticks: {
                        color: '#888',
                        callback: function (val) { return formatCompactCurrency(val); }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create scenario analysis display
function createScenarioAnalysis() {
    var container = document.getElementById('scenarioAnalysis');
    if (!container) return;

    var data = getMonteCarloData();

    if (!data.scenarios) {
        container.innerHTML = '<p class="muted">No scenario data available</p>';
        return;
    }

    var scenarios = data.scenarios;
    var scenarioOrder = ['bear', 'conservative', 'moderate', 'bullish', 'optimistic'];

    // Find max for bar scaling
    var maxValue = 0;
    scenarioOrder.forEach(function (key) {
        if (scenarios[key]) {
            maxValue = Math.max(maxValue, scenarios[key].p90_final);
        }
    });

    var html = '<div class="scenario-list">';

    scenarioOrder.forEach(function (key) {
        var s = scenarios[key];
        if (!s) return;

        var barWidth = (s.median_final / maxValue) * 100;
        var rangeWidth = ((s.p90_final - s.p10_final) / maxValue) * 100;
        var rangeLeft = (s.p10_final / maxValue) * 100;

        var colorClass = key === 'bear' ? 'bear' :
            key === 'conservative' ? 'conservative' :
                key === 'moderate' ? 'moderate' :
                    key === 'bullish' ? 'bullish' : 'optimistic';

        html += '<div class="scenario-row">';
        html += '<div class="scenario-label">';
        html += '<span class="scenario-name">' + s.label + '</span>';
        html += '<span class="scenario-params">' + s.return + '% return, ' + s.volatility + '% vol</span>';
        html += '</div>';
        html += '<div class="scenario-bar-container">';
        html += '<div class="scenario-range" style="left: ' + rangeLeft + '%; width: ' + rangeWidth + '%;"></div>';
        html += '<div class="scenario-median ' + colorClass + '" style="left: ' + barWidth + '%;"></div>';
        html += '</div>';
        html += '<div class="scenario-values">';
        html += '<span class="scenario-median-val">' + formatCompactCurrency(s.median_final) + '</span>';
        html += '</div>';
        html += '</div>';
    });

    html += '</div>';

    html += '<div class="scenario-legend">';
    html += '<span>Range shows 10th-90th percentile outcomes</span>';
    html += '</div>';

    container.innerHTML = html;
}

// Create withdrawal analysis display
function createWithdrawalAnalysis() {
    var container = document.getElementById('withdrawalAnalysis');
    if (!container) return;

    var data = getMonteCarloData();

    if (!data.withdrawal_analysis) {
        container.innerHTML = '<p class="muted">No withdrawal analysis available</p>';
        return;
    }

    var wa = data.withdrawal_analysis;

    var html = '<div class="withdrawal-summary">';

    html += '<div class="withdrawal-hero">';
    html += '<div class="withdrawal-rate">' + wa.safe_withdrawal_rate + '%</div>';
    html += '<div class="withdrawal-label">Safe Withdrawal Rate</div>';
    html += '<div class="withdrawal-sublabel">' + wa.success_rate + '% probability of success over ' + wa.years + ' years</div>';
    html += '</div>';

    html += '<div class="withdrawal-details">';
    html += '<div class="withdrawal-row">';
    html += '<span class="withdrawal-detail-label">Annual Withdrawal</span>';
    html += '<span class="withdrawal-detail-value positive">' + formatCurrency(wa.annual_withdrawal) + '</span>';
    html += '</div>';
    html += '<div class="withdrawal-row">';
    html += '<span class="withdrawal-detail-label">Monthly Withdrawal</span>';
    html += '<span class="withdrawal-detail-value positive">' + formatCurrency(wa.monthly_withdrawal) + '</span>';
    html += '</div>';
    html += '</div>';

    html += '<div class="withdrawal-note">';
    html += '<p>Based on projected median portfolio value at retirement. The "4% rule" suggests ' + formatCurrency(data.main_simulation.final_statistics.median * 0.04) + '/year.</p>';
    html += '</div>';

    html += '</div>';

    container.innerHTML = html;
}

// Create FIRE Tracker
function createFireTracker(data) {
    var container = document.getElementById('fireTracker');
    if (!container) return;

    var personal = data.personal || {};
    var currentAge = personal.current_age || 35;
    var currentValue = portfolioData.TotalValue || 0;
    var salary = (data.salary && data.salary.current) || 0;
    var avgBonus = (data.bonuses && data.bonuses.avg_annual) || 0;
    var totalIncome = salary + avgBonus;

    var contributions = data.contributions || {};
    var annualSavings = (contributions.traditional_401k || 0) + (contributions.roth_ira || 0) + (data.employer_match || 0);

    var estimatedTakeHome = salary * 0.72;
    var estimatedExpenses = Math.max(estimatedTakeHome - annualSavings, salary * 0.4);
    var fireNumber = estimatedExpenses * 25;
    var fireProgress = (currentValue / fireNumber) * 100;

    var annualReturn = 0.07;
    var yearsToFire = 0;
    if (annualSavings > 0 && currentValue < fireNumber) {
        var projectedValue = currentValue;
        while (projectedValue < fireNumber && yearsToFire < 100) {
            projectedValue = projectedValue * (1 + annualReturn) + annualSavings;
            yearsToFire++;
        }
    }
    var fireAge = currentAge + yearsToFire;

    var milestones = [
        { label: '$100K', value: 100000 },
        { label: '$250K', value: 250000 },
        { label: '$500K', value: 500000 },
        { label: '$750K', value: 750000 },
        { label: '$1M', value: 1000000 },
        { label: 'FIRE', value: fireNumber }
    ];

    var html = '';

    html += '<div class="fire-header">';
    html += '<div class="fire-main-stat">';
    html += '<div class="fire-label">Current Net Worth</div>';
    html += '<div class="fire-value">' + formatCurrency(currentValue) + '</div>';
    html += '<div class="fire-subvalue">' + fireProgress.toFixed(1) + '% to FIRE</div>';
    html += '</div>';
    html += '<div class="fire-target">';
    html += '<div class="fire-label">FIRE Target (25x Expenses)</div>';
    html += '<div class="fire-value">' + formatCurrency(fireNumber) + '</div>';
    html += '</div></div>';

    html += '<div class="fire-progress-container">';
    html += '<div class="fire-progress-bar">';
    html += '<div class="fire-progress-fill" style="width: ' + Math.min(fireProgress, 100) + '%;">';
    if (fireProgress >= 10) html += '<span class="fire-progress-text">' + fireProgress.toFixed(1) + '%</span>';
    html += '</div></div>';

    html += '<div class="fire-milestones">';
    milestones.forEach(function (m) {
        var reached = currentValue >= m.value;
        html += '<div class="fire-milestone ' + (reached ? 'reached' : '') + '">';
        html += '<div class="milestone-marker"></div>';
        html += '<div class="milestone-label">' + m.label + '</div>';
        html += '</div>';
    });
    html += '</div></div>';

    html += '<div class="fire-stats">';
    html += '<div class="fire-stat-item"><div class="fire-stat-label">Est. Annual Expenses</div>';
    html += '<div class="fire-stat-value">' + formatCurrency(estimatedExpenses) + '</div></div>';
    html += '<div class="fire-stat-item"><div class="fire-stat-label">Years to FIRE</div>';
    html += '<div class="fire-stat-value positive">' + (yearsToFire > 0 ? '~' + yearsToFire + ' years' : 'Achieved!') + '</div></div>';
    html += '<div class="fire-stat-item"><div class="fire-stat-label">FIRE Age (currently ' + currentAge + ')</div>';
    html += '<div class="fire-stat-value">' + (yearsToFire > 0 ? fireAge : 'Now!') + '</div></div>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Savings Rate display
function createSavingsRate(data) {
    var container = document.getElementById('savingsRate');
    if (!container) return;

    var salary = (data.salary && data.salary.current) || 0;
    var avgBonus = (data.bonuses && data.bonuses.avg_annual) || 0;
    var totalGrossIncome = salary + avgBonus;

    var contributions = data.contributions || {};
    var contrib401k = contributions.traditional_401k || 0;
    var contribRothIRA = contributions.roth_ira || 0;
    var employerMatch = data.employer_match || 0;

    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;

    var annualized401k = yearProgress > 0 ? contrib401k / yearProgress : contrib401k;
    var annualizedRothIRA = yearProgress > 0 ? contribRothIRA / yearProgress : contribRothIRA;
    var totalAnnualizedSavings = annualized401k + annualizedRothIRA + employerMatch;

    var savingsRate = totalGrossIncome > 0 ? (totalAnnualizedSavings / totalGrossIncome) * 100 : 0;

    var html = '';
    var rateColor = savingsRate >= 20 ? 'var(--accent-green)' : savingsRate >= 15 ? '#fbbf24' : 'var(--accent-red)';
    html += '<div class="savings-rate-value" style="color: ' + rateColor + ';">' + savingsRate.toFixed(1) + '%</div>';
    html += '<div class="savings-rate-label">Total Savings Rate (incl. employer match)</div>';

    html += '<div class="savings-rate-breakdown">';
    html += '<div class="savings-rate-row"><span class="savings-rate-row-label">401(k) Contributions</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(annualized401k) + '/yr</span></div>';
    html += '<div class="savings-rate-row"><span class="savings-rate-row-label">Roth IRA Contributions</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(annualizedRothIRA) + '/yr</span></div>';
    html += '<div class="savings-rate-row"><span class="savings-rate-row-label">Employer Match</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(employerMatch) + '/yr</span></div>';
    html += '<div class="savings-rate-row" style="border-top: 1px solid var(--border-color); padding-top: 8px; margin-top: 8px;">';
    html += '<span class="savings-rate-row-label"><strong>Total Annual Savings</strong></span>';
    html += '<span class="savings-rate-row-value"><strong>' + formatCurrency(totalAnnualizedSavings) + '</strong></span></div>';
    html += '</div>';

    html += '<div class="savings-benchmark">';
    html += '<div class="benchmark-item"><div class="benchmark-value">10%</div><div class="benchmark-label">Minimum</div></div>';
    html += '<div class="benchmark-item"><div class="benchmark-value">15%</div><div class="benchmark-label">Recommended</div></div>';
    html += '<div class="benchmark-item"><div class="benchmark-value current">' + savingsRate.toFixed(0) + '%</div><div class="benchmark-label">Your Rate</div></div>';
    html += '<div class="benchmark-item"><div class="benchmark-value">20%+</div><div class="benchmark-label">Aggressive</div></div>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Retirement Projections display
function createRetirementProjections(data) {
    var container = document.getElementById('retirementProjections');
    if (!container) return;

    var currentValue = portfolioData.TotalValue || 0;
    var contributions = data.contributions || {};
    var annualContributions = (contributions.traditional_401k || 0) + (contributions.roth_ira || 0) + (data.employer_match || 0);

    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;
    annualContributions = yearProgress > 0 ? annualContributions / yearProgress : annualContributions;

    var personal = data.personal || {};
    var yearsToRetirement = personal.years_to_retirement || 30;

    var scenarios = [
        { name: 'Conservative', rate: 0.05, color: 'conservative' },
        { name: 'Moderate', rate: 0.07, color: 'moderate' },
        { name: 'Aggressive', rate: 0.09, color: 'aggressive' }
    ];

    var maxProjection = 0;
    scenarios.forEach(function (s) {
        var growthFactor = Math.pow(1 + s.rate, yearsToRetirement);
        s.projection = currentValue * growthFactor + annualContributions * (growthFactor - 1) / s.rate;
        maxProjection = Math.max(maxProjection, s.projection);
    });

    var html = '<div class="projection-scenarios">';
    scenarios.forEach(function (s) {
        var barWidth = (s.projection / maxProjection) * 100;
        html += '<div class="projection-scenario">';
        html += '<div class="scenario-label">' + s.name + ' (' + Math.round(s.rate * 100) + '%)</div>';
        html += '<div class="scenario-bar-container"><div class="scenario-bar ' + s.color + '" style="width: ' + barWidth + '%;"></div></div>';
        html += '<div class="scenario-value">' + formatCompactCurrency(s.projection) + '</div>';
        html += '</div>';
    });
    html += '</div>';

    var currentAge = (data.personal && data.personal.current_age) || 35;
    var retirementAge = (data.personal && data.personal.retirement_age) || 65;
    html += '<div class="projection-assumptions">';
    html += '<p><strong>Assumptions:</strong></p>';
    html += '<p>• Current portfolio: ' + formatCurrency(currentValue) + '</p>';
    html += '<p>• Annual contributions: ' + formatCurrency(annualContributions) + '</p>';
    html += '<p>• Current age: ' + currentAge + ' → Retire at ' + retirementAge + ' (' + yearsToRetirement + ' years)</p>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Retirement Projection Chart
function createRetirementProjectionChart(data) {
    var ctx = document.getElementById('retirementProjectionChart');
    if (!ctx) return;

    var personal = data.personal || {};
    var currentAge = personal.current_age || 35;
    var yearsToRetirement = personal.years_to_retirement || 30;

    var currentValue = portfolioData.TotalValue || 0;
    var contributions = data.contributions || {};
    var annualContributions = (contributions.traditional_401k || 0) + (contributions.roth_ira || 0) + (data.employer_match || 0);

    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;
    annualContributions = yearProgress > 0 ? annualContributions / yearProgress : annualContributions;

    var currentYear = now.getFullYear();
    var labels = [];
    var conservativeData = [];
    var moderateData = [];
    var aggressiveData = [];

    for (var i = 0; i <= yearsToRetirement; i += 5) {
        labels.push(currentYear + i + ' (age ' + (currentAge + i) + ')');

        var cGrowth = Math.pow(1.05, i);
        var mGrowth = Math.pow(1.07, i);
        var aGrowth = Math.pow(1.09, i);

        conservativeData.push(currentValue * cGrowth + (i > 0 ? annualContributions * (cGrowth - 1) / 0.05 : 0));
        moderateData.push(currentValue * mGrowth + (i > 0 ? annualContributions * (mGrowth - 1) / 0.07 : 0));
        aggressiveData.push(currentValue * aGrowth + (i > 0 ? annualContributions * (aGrowth - 1) / 0.09 : 0));
    }

    if (yearsToRetirement % 5 !== 0) {
        labels.push(currentYear + yearsToRetirement + ' (age 65)');
        var cGrowth = Math.pow(1.05, yearsToRetirement);
        var mGrowth = Math.pow(1.07, yearsToRetirement);
        var aGrowth = Math.pow(1.09, yearsToRetirement);
        conservativeData.push(currentValue * cGrowth + annualContributions * (cGrowth - 1) / 0.05);
        moderateData.push(currentValue * mGrowth + annualContributions * (mGrowth - 1) / 0.07);
        aggressiveData.push(currentValue * aGrowth + annualContributions * (aGrowth - 1) / 0.09);
    }

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                { label: 'Conservative (5%)', data: conservativeData, borderColor: '#60a5fa', backgroundColor: 'rgba(96, 165, 250, 0.1)', fill: true, tension: 0.3 },
                { label: 'Moderate (7%)', data: moderateData, borderColor: '#a78bfa', backgroundColor: 'rgba(167, 139, 250, 0.1)', fill: true, tension: 0.3 },
                { label: 'Aggressive (9%)', data: aggressiveData, borderColor: '#4ade80', backgroundColor: 'rgba(74, 222, 128, 0.1)', fill: true, tension: 0.3 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', labels: { color: '#888', font: { size: 11 } } },
                tooltip: { callbacks: { label: function (context) { return context.dataset.label + ': ' + formatCurrency(context.raw); } } }
            },
            scales: {
                x: { ticks: { color: '#888' }, grid: { display: false } },
                y: { ticks: { color: '#888', callback: function (val) { return formatCompactCurrency(val); } }, grid: { color: 'rgba(255,255,255,0.05)' } }
            }
        }
    });
}

// Create salary info display
function createSalaryInfo(data) {
    var container = document.getElementById('salaryInfo');
    if (!container) return;

    var salary = data.salary || {};
    var bonuses = data.bonuses || {};

    var html = '';
    html += '<div class="stat-row"><span class="stat-label">Current Salary</span>';
    html += '<span class="stat-value highlight">' + formatCurrency(salary.current || 0) + '/yr</span></div>';

    var monthlyGross = (salary.current || 0) / 12;
    html += '<div class="stat-row"><span class="stat-label">Monthly Gross</span>';
    html += '<span class="stat-value">' + formatCurrency(monthlyGross) + '</span></div>';

    html += '<div class="stat-row"><span class="stat-label">YTD Bonuses</span>';
    html += '<span class="stat-value">' + formatCurrency(bonuses.ytd || 0) + '</span></div>';

    html += '<div class="stat-row"><span class="stat-label">Avg Annual Bonus</span>';
    html += '<span class="stat-value">' + formatCurrency(bonuses.avg_annual || 0) + '</span></div>';

    var totalComp = (salary.current || 0) + (bonuses.avg_annual || 0);
    html += '<div class="stat-row"><span class="stat-label">Est. Total Comp</span>';
    html += '<span class="stat-value highlight">' + formatCurrency(totalComp) + '</span></div>';

    container.innerHTML = html;
}

// Create contribution limits and progress
function createContributionLimits(data) {
    var container = document.getElementById('contributionLimits');
    if (!container) return;

    var limits = data.limits || {};
    var contributions = data.contributions || {};

    var items = [
        { name: '401(k) (Pre-tax + Roth)', limit: limits.total_401k || 23500, contributed: (contributions.traditional_401k || 0) + (contributions.roth_401k || 0) },
        { name: 'Traditional IRA', limit: limits.traditional_ira || 7000, contributed: contributions.traditional_ira || 0 },
        { name: 'Roth IRA', limit: limits.roth_ira || 7000, contributed: contributions.roth_ira || 0 }
    ];

    var html = '';
    items.forEach(function (item) {
        var pct = item.limit > 0 ? Math.min((item.contributed / item.limit) * 100, 100) : 0;
        var colorClass = pct >= 100 ? 'high' : pct >= 50 ? 'medium' : 'low';

        html += '<div class="contribution-limit-item">';
        html += '<div class="contribution-limit-header">';
        html += '<span class="contribution-limit-name">' + item.name + '</span>';
        html += '<span class="contribution-limit-amount">' + formatCurrency(item.contributed) + ' / ' + formatCurrency(item.limit) + '</span>';
        html += '</div>';
        html += '<div class="contribution-progress-bar"><div class="contribution-progress-fill ' + colorClass + '" style="width: ' + Math.max(pct, 5) + '%;">';
        html += '<span class="contribution-progress-text">' + pct.toFixed(0) + '%</span>';
        html += '</div></div></div>';
    });

    if (data.employer_match) {
        html += '<div class="contribution-limit-item" style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border-color);">';
        html += '<div class="contribution-limit-header">';
        html += '<span class="contribution-limit-name">Est. Employer Match</span>';
        html += '<span class="contribution-limit-amount" style="color: var(--accent-green);">+' + formatCurrency(data.employer_match) + '/yr</span>';
        html += '</div></div>';
    }

    container.innerHTML = html;
}

// Create salary history chart
function createSalaryHistoryChart(data) {
    var ctx = document.getElementById('salaryHistoryChart');
    if (!ctx) return;

    var salaryHistory = (data.salary && data.salary.history) || [];
    if (salaryHistory.length === 0) return;

    var labels = salaryHistory.map(function (s) { return s.date.substring(0, 7); });
    var values = salaryHistory.map(function (s) { return s.amount; });

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Salary',
                data: values,
                borderColor: '#4ade80',
                backgroundColor: 'rgba(74, 222, 128, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 6,
                pointBackgroundColor: '#4ade80'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) { return formatCurrency(context.raw); },
                        afterLabel: function (context) {
                            var idx = context.dataIndex;
                            if (salaryHistory[idx] && salaryHistory[idx].note) return salaryHistory[idx].note;
                            return '';
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888' }, grid: { display: false } },
                y: { ticks: { color: '#888', callback: function (val) { return '$' + (val / 1000) + 'k'; } }, grid: { color: 'rgba(255,255,255,0.05)' } }
            }
        }
    });
}

// Create retirement accounts chart
function createRetirementAccountsChart(data) {
    var ctx = document.getElementById('retirementAccountsChart');
    if (!ctx) return;

    var accounts = (data.accounts && data.accounts.retirement) || {};
    var labels = Object.keys(accounts);
    var values = Object.values(accounts);

    if (labels.length === 0) {
        var holdings = getHoldingsDetail();
        var retirementKeywords = ['401k', '401K', 'IRA', 'ira', 'Rollover', 'Roth'];
        var retirementAccounts = {};

        holdings.forEach(function (h) {
            var isRetirement = retirementKeywords.some(function (kw) { return h.account.indexOf(kw) !== -1; });
            if (isRetirement) {
                if (!retirementAccounts[h.account]) retirementAccounts[h.account] = 0;
                retirementAccounts[h.account] += h.current_value || 0;
            }
        });

        labels = Object.keys(retirementAccounts);
        values = Object.values(retirementAccounts);
    }

    if (labels.length === 0) return;

    var colors = labels.map(function (_, i) {
        var palette = ['#4ade80', '#60a5fa', '#a78bfa', '#f472b6', '#fbbf24'];
        return palette[i % palette.length];
    });

    new Chart(ctx, {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#888', font: { size: 11 }, padding: 10 } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var val = context.raw;
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            return context.label + ': ' + formatCurrency(val) + ' (' + ((val / total) * 100).toFixed(1) + '%)';
                        }
                    }
                }
            }
        }
    });
}

// Create budget breakdown
function createBudgetBreakdown(data) {
    var container = document.getElementById('budgetBreakdown');
    if (!container) return;

    var budget = data.budget || {};
    if (!budget.monthly_gross) {
        container.innerHTML = '<p style="color: var(--text-secondary); padding: 20px;">No salary data available</p>';
        return;
    }

    var html = '';
    html += '<div class="budget-item"><span class="budget-label">Monthly Gross</span>';
    html += '<span class="budget-value income">' + formatCurrency(budget.monthly_gross) + '</span></div>';

    html += '<div class="budget-item"><span class="budget-label">Est. Taxes (~' + budget.effective_tax_rate + '%)</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_estimated_taxes) + '</span></div>';

    html += '<div class="budget-item"><span class="budget-label">Max 401(k) Contribution</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_max_401k) + '</span></div>';

    html += '<div class="budget-item"><span class="budget-label">Max IRA Contribution</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_max_ira) + '</span></div>';

    var remaining = budget.monthly_net - budget.monthly_max_401k - budget.monthly_max_ira;
    html += '<div class="budget-item total"><span class="budget-label">Est. Take-Home (after max savings)</span>';
    html += '<span class="budget-value">' + formatCurrency(remaining) + '</span></div>';

    container.innerHTML = html;
}

// Create bonus history table
function createBonusHistoryTable(data) {
    var tbody = document.getElementById('bonusHistoryBody');
    if (!tbody) return;

    var bonuses = (data.bonuses && data.bonuses.history) || [];
    if (bonuses.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary);">No bonus history available</td></tr>';
        return;
    }

    bonuses = bonuses.slice().sort(function (a, b) { return new Date(b.date) - new Date(a.date); });

    var html = '';
    bonuses.forEach(function (bonus) {
        html += '<tr>';
        html += '<td>' + formatDate(bonus.date) + '</td>';
        html += '<td style="text-align: right; color: var(--accent-green);">' + formatCurrency(bonus.amount) + '</td>';
        html += '<td>' + (bonus.note || '') + '</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

// Create salary history table
function createSalaryHistoryTable(data) {
    var tbody = document.getElementById('salaryHistoryBody');
    if (!tbody) return;

    var salaries = (data.salary && data.salary.history) || [];
    if (salaries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No salary history available</td></tr>';
        return;
    }

    salaries = salaries.slice().sort(function (a, b) { return new Date(b.date) - new Date(a.date); });

    var html = '';
    for (var i = 0; i < salaries.length; i++) {
        var salary = salaries[i];
        var prevSalary = salaries[i + 1];
        var change = '';
        var changeClass = '';

        if (prevSalary) {
            var diff = salary.amount - prevSalary.amount;
            var pct = ((diff / prevSalary.amount) * 100).toFixed(1);
            change = (diff >= 0 ? '+' : '') + formatCurrency(diff) + ' (' + (diff >= 0 ? '+' : '') + pct + '%)';
            changeClass = diff >= 0 ? 'positive' : 'negative';
        }

        html += '<tr>';
        html += '<td>' + formatDate(salary.date) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(salary.amount) + '</td>';
        html += '<td style="text-align: right;" class="' + changeClass + '">' + change + '</td>';
        html += '<td>' + (salary.note || '') + '</td>';
        html += '</tr>';
    }

    tbody.innerHTML = html;
}
