/**
 * History Module
 * Historical charts, growth comparison, value vs invested, monthly returns
 */

// Create historical chart with per-account lines
function createHistoricalChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('historicalChart');
    if (!ctx) return;

    var accounts = getHistoricalAccounts();
    var labels = data.map(function (h) { return h.date; });

    var datasets = [];

    // Total portfolio line
    datasets.push({
        label: 'Total Portfolio',
        data: data.map(function (h) { return h.totalValue; }),
        borderColor: '#ffffff',
        backgroundColor: 'rgba(255, 255, 255, 0.05)',
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 3,
        order: 0
    });

    // Per-account lines
    for (var i = 0; i < accounts.length; i++) {
        var account = accounts[i];
        var color = getAccountColor(account, i);

        datasets.push({
            label: account,
            data: data.map(function (h) { return h.accounts[account] || 0; }),
            borderColor: color,
            backgroundColor: 'transparent',
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 3,
            borderWidth: 1.5,
            order: i + 1
        });
    }

    new Chart(ctx, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#888', padding: 12, font: { size: 11 }, usePointStyle: true, pointStyle: 'line' }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888', maxTicksLimit: 12 }, grid: { display: false } },
                y: {
                    stacked: false,
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create growth comparison chart (Portfolio TWR vs S&P 500)
function createGrowthComparisonChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('growthComparisonChart');
    if (!ctx) return;

    var labels = data.map(function (h) { return h.date; });
    var portfolioValues = data.map(function (h) { return h.totalValue; });
    var whatIfSP500 = data.map(function (h) { return h.whatIfSP500 !== undefined ? h.whatIfSP500 : null; });

    var hasWhatIfSP500 = whatIfSP500.some(function (v) { return v !== null && v > 0; });

    var datasets = [];

    datasets.push({
        label: 'My Portfolio (Actual)',
        data: portfolioValues,
        borderColor: '#00ff88',
        backgroundColor: 'rgba(0, 255, 136, 0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 3
    });

    if (hasWhatIfSP500) {
        datasets.push({
            label: 'If Invested in S&P 500',
            data: whatIfSP500,
            borderColor: '#ffc107',
            backgroundColor: 'rgba(255, 193, 7, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 3
        });
    }

    // Calculate alpha
    var alpha = [];
    for (var i = 0; i < portfolioValues.length; i++) {
        var actual = portfolioValues[i] || 0;
        var whatIf = whatIfSP500[i] || 0;
        alpha.push(whatIf > 0 ? actual - whatIf : null);
    }

    var latestAlpha = null;
    for (var j = alpha.length - 1; j >= 0; j--) {
        if (alpha[j] !== null) {
            latestAlpha = alpha[j];
            break;
        }
    }

    new Chart(ctx, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#888', padding: 12, font: { size: 11 }, usePointStyle: true, pointStyle: 'line' }
                },
                title: {
                    display: latestAlpha !== null,
                    text: latestAlpha !== null ? (latestAlpha >= 0 ? 'You beat the S&P 500 by ' + formatCurrency(latestAlpha) : 'S&P 500 would have beaten you by ' + formatCurrency(Math.abs(latestAlpha))) : '',
                    color: latestAlpha >= 0 ? '#00ff88' : '#ff6b6b',
                    font: { size: 14, weight: 'bold' },
                    padding: { bottom: 10 }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        },
                        afterBody: function (tooltipItems) {
                            if (tooltipItems.length >= 2) {
                                var actual = tooltipItems[0].raw || 0;
                                var whatIf = tooltipItems[1].raw || 0;
                                var diff = actual - whatIf;
                                return 'Difference: ' + (diff >= 0 ? '+' : '') + formatCurrency(diff);
                            }
                            return '';
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888', maxTicksLimit: 12 }, grid: { display: false } },
                y: {
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create value vs cost basis chart with gain/loss overlay
function createValueVsInvestedChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('valueVsInvestedChart');
    if (!ctx) return;

    var labels = data.map(function (h) { return h.date; });
    var portfolioValues = data.map(function (h) { return h.totalValue; });

    var costBasisData = typeof portfolioCostBasisData !== 'undefined' ? portfolioCostBasisData : [];
    var costBasisByDate = {};
    costBasisData.forEach(function (entry) {
        costBasisByDate[entry.Date] = entry.TotalCostBasis;
    });

    var historicalCostBasis = [];
    var totalGainLoss = [];
    var lastKnownCostBasis = 0;
    var costBasisDates = costBasisData.map(function (e) { return e.Date; }).sort();

    labels.forEach(function (snapshotDate, index) {
        var applicableCostBasis = lastKnownCostBasis;
        for (var i = 0; i < costBasisDates.length; i++) {
            if (costBasisDates[i] <= snapshotDate) {
                applicableCostBasis = costBasisByDate[costBasisDates[i]];
                lastKnownCostBasis = applicableCostBasis;
            } else break;
        }
        historicalCostBasis.push(applicableCostBasis);
        var value = portfolioValues[index] || 0;
        totalGainLoss.push(value - applicableCostBasis);
    });

    var datasets = [
        {
            label: 'Portfolio Value',
            data: portfolioValues,
            borderColor: '#00ff88',
            backgroundColor: 'rgba(0, 255, 136, 0.1)',
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 3,
            yAxisID: 'y'
        },
        {
            label: 'Cost Basis',
            data: historicalCostBasis,
            borderColor: '#ff6b6b',
            backgroundColor: 'rgba(255, 107, 107, 0.1)',
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y'
        },
        {
            label: 'Total Gain/Loss',
            data: totalGainLoss,
            borderColor: '#9b59b6',
            backgroundColor: 'rgba(155, 89, 182, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y'
        }
    ];

    new Chart(ctx, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#888', padding: 12, font: { size: 11 }, usePointStyle: true, pointStyle: 'line' }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var label = context.dataset.label || '';
                            var value = context.raw;
                            if (label === 'Total Gain/Loss') {
                                return label + ': ' + (value >= 0 ? '+' : '') + formatCurrency(value);
                            }
                            return label + ': ' + formatCurrency(value);
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888', maxTicksLimit: 12 }, grid: { display: false } },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: { display: true, text: 'Dollars ($)', color: '#888' },
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create monthly returns chart
function createMonthlyReturnsChart() {
    var data = getHistoricalHoldings();
    if (data.length < 2) return;

    var ctx = document.getElementById('monthlyReturnsChart');
    if (!ctx) return;

    var monthlyReturns = {};
    for (var i = 1; i < data.length; i++) {
        var curr = data[i];
        var prev = data[i - 1];
        var yearMonth = curr.date.substring(0, 7);
        var currVal = curr.totalValue || 0;
        var prevVal = prev.totalValue || 0;

        if (prevVal > 0) {
            monthlyReturns[yearMonth] = {
                return: ((currVal - prevVal) / prevVal) * 100,
                startVal: prevVal,
                endVal: currVal
            };
        }
    }

    var months = Object.keys(monthlyReturns).sort().slice(-24);
    var returns = months.map(function (m) { return monthlyReturns[m].return; });
    var colors = returns.map(function (r) { return r >= 0 ? '#4ade80' : '#f87171'; });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: months.map(function (m) {
                var parts = m.split('-');
                var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                return monthNames[parseInt(parts[1]) - 1] + ' ' + parts[0].slice(2);
            }),
            datasets: [{ label: 'Monthly Return %', data: returns, backgroundColor: colors, borderRadius: 4 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var val = context.raw;
                            return (val >= 0 ? '+' : '') + val.toFixed(2) + '%';
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888', maxRotation: 45 }, grid: { display: false } },
                y: {
                    ticks: { color: '#888', callback: function (val) { return val + '%'; } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}
