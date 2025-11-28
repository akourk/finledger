/**
 * Overview Tab Module
 * Summary cards, allocation charts, and overview visualizations
 */

// ==========================================
// Portfolio Hero with Sparkline
// ==========================================

function createPortfolioHero() {
    if (typeof portfolioData === 'undefined') return;

    var heroValue = document.getElementById('heroValue');
    var heroChange = document.getElementById('heroChange');

    if (heroValue) {
        heroValue.textContent = formatCurrency(portfolioData.TotalValue);
    }

    // Use pre-calculated daily change from portfolio data
    if (heroChange) {
        var dailyChange = portfolioData.DailyChange || 0;
        var dailyPct = portfolioData.DailyChangePct || 0;

        var changeClass = dailyChange >= 0 ? 'positive' : 'negative';
        var sign = dailyChange >= 0 ? '+' : '';
        heroChange.className = 'hero-change ' + changeClass;
        heroChange.innerHTML = sign + formatCurrency(dailyChange) + ' (' + sign + dailyPct.toFixed(2) + '%) today';
    }

    createPortfolioSparkline();
}

function createPortfolioSparkline() {
    var ctx = document.getElementById('portfolioSparkline');
    if (!ctx) return;

    var historicalData = getHistoricalHoldings();
    if (!historicalData || historicalData.length < 2) return;

    // Get last 90 days of data
    var recentData = historicalData.slice(-90);
    var labels = recentData.map(function (d) { return d.date; });
    var values = recentData.map(function (d) { return d.totalValue; });

    // Determine color based on trend
    var startVal = values[0];
    var endVal = values[values.length - 1];
    var trendColor = endVal >= startVal ? '#4ade80' : '#f87171';
    var trendBg = endVal >= startVal ? 'rgba(74, 222, 128, 0.1)' : 'rgba(248, 113, 113, 0.1)';

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                borderColor: trendColor,
                backgroundColor: trendBg,
                fill: true,
                tension: 0.4,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function (context) {
                            return formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: { display: false },
                y: { display: false }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });
}

// ==========================================
// Summary Cards
// ==========================================

function createSummaryCards() {
    if (typeof portfolioData === 'undefined') return;

    var data = portfolioData;
    var container = document.getElementById('summaryCards');
    if (!container) return;

    var historicalData = getHistoricalHoldings();
    var timeChanges = calculateTimeChanges(historicalData);

    var diversification = calculateDiversificationScore(getHoldingsDetail());

    // Calculate total return (realized + unrealized)
    var totalReturn = (data.TotalUnrealizedGain || 0) + (data.AllTimeRealizedGain || 0);
    var totalReturnPct = data.TotalCostBasis > 0 ? (totalReturn / data.TotalCostBasis * 100) : 0;

    var cards = [
        {
            label: 'Unrealized Gain/Loss',
            value: formatCurrency(data.TotalUnrealizedGain),
            subvalue: formatPercent(data.UnrealizedGainPct),
            cls: (data.TotalUnrealizedGain || 0) >= 0 ? 'positive' : 'negative'
        },
        {
            label: 'Realized Gains (All-Time)',
            value: formatCurrency(data.AllTimeRealizedGain),
            subvalue: 'YTD: ' + formatCurrency(data.YTDRealizedGain || 0),
            cls: (data.AllTimeRealizedGain || 0) >= 0 ? 'positive' : 'negative'
        },
        {
            label: 'Total Return',
            value: formatCurrency(totalReturn),
            subvalue: (totalReturnPct >= 0 ? '+' : '') + totalReturnPct.toFixed(1) + '% all-time',
            cls: totalReturn >= 0 ? 'positive' : 'negative'
        },
        {
            label: 'YTD Income',
            value: formatCurrency(data.YTDIncome || 0),
            subvalue: 'All-time: ' + formatCurrency(data.AllTimeIncome || 0),
            cls: 'positive'
        },
        {
            label: '1 Month Change',
            value: formatCurrency(timeChanges.oneMonth.change),
            subvalue: (timeChanges.oneMonth.pct >= 0 ? '+' : '') + timeChanges.oneMonth.pct.toFixed(2) + '%',
            cls: timeChanges.oneMonth.change >= 0 ? 'positive' : 'negative'
        },
        {
            label: '3 Month Change',
            value: formatCurrency(timeChanges.threeMonth.change),
            subvalue: (timeChanges.threeMonth.pct >= 0 ? '+' : '') + timeChanges.threeMonth.pct.toFixed(2) + '%',
            cls: timeChanges.threeMonth.change >= 0 ? 'positive' : 'negative'
        },
        {
            label: 'YTD Change',
            value: formatCurrency(timeChanges.ytd.change),
            subvalue: (timeChanges.ytd.pct >= 0 ? '+' : '') + timeChanges.ytd.pct.toFixed(2) + '%',
            cls: timeChanges.ytd.change >= 0 ? 'positive' : 'negative'
        },
        {
            label: 'Diversification',
            value: diversification.score + '/100',
            subvalue: diversification.grade + ' • ' + (data.NumPositions || 0) + ' positions',
            cls: diversification.score >= 70 ? 'positive' : diversification.score >= 40 ? '' : 'negative'
        }
    ];

    var html = '';
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        html += '<div class="card">';
        html += '<div class="card-label">' + card.label + '</div>';
        html += '<div class="card-value ' + card.cls + '">' + card.value + '</div>';
        if (card.subvalue) {
            html += '<div class="card-subvalue ' + card.cls + '">' + card.subvalue + '</div>';
        }
        html += '</div>';
    }
    container.innerHTML = html;
}

// ==========================================
// Time-Based Calculations
// ==========================================

function calculateTimeChanges(historicalData) {
    var result = {
        oneMonth: { change: 0, pct: 0 },
        threeMonth: { change: 0, pct: 0 },
        ytd: { change: 0, pct: 0 }
    };

    if (!historicalData || historicalData.length < 2) return result;

    var current = historicalData[historicalData.length - 1];
    var currentValue = current.totalValue || 0;
    var currentDate = new Date(current.date);

    var oneMonthAgo = new Date(currentDate);
    oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);

    var threeMonthsAgo = new Date(currentDate);
    threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);

    var yearStart = new Date(currentDate.getFullYear(), 0, 1);

    var oneMonthValue = null, threeMonthValue = null, ytdValue = null;

    for (var i = historicalData.length - 1; i >= 0; i--) {
        var entry = historicalData[i];
        var entryDate = new Date(entry.date);

        if (oneMonthValue === null && entryDate <= oneMonthAgo) oneMonthValue = entry.totalValue;
        if (threeMonthValue === null && entryDate <= threeMonthsAgo) threeMonthValue = entry.totalValue;
        if (ytdValue === null && entryDate <= yearStart) ytdValue = entry.totalValue;
    }

    var earliest = historicalData[0];
    if (oneMonthValue === null) oneMonthValue = earliest.totalValue;
    if (threeMonthValue === null) threeMonthValue = earliest.totalValue;
    if (ytdValue === null) ytdValue = earliest.totalValue;

    result.oneMonth.change = currentValue - oneMonthValue;
    result.oneMonth.pct = oneMonthValue > 0 ? (result.oneMonth.change / oneMonthValue * 100) : 0;

    result.threeMonth.change = currentValue - threeMonthValue;
    result.threeMonth.pct = threeMonthValue > 0 ? (result.threeMonth.change / threeMonthValue * 100) : 0;

    result.ytd.change = currentValue - ytdValue;
    result.ytd.pct = ytdValue > 0 ? (result.ytd.change / ytdValue * 100) : 0;

    return result;
}

// ==========================================
// Diversification Score
// ==========================================

function calculateDiversificationScore(holdings) {
    var result = { score: 0, grade: 'N/A', details: {} };

    if (!holdings || holdings.length === 0) return result;

    var totalValue = holdings.reduce(function (sum, h) { return sum + (h.current_value || 0); }, 0);
    if (totalValue === 0) return result;

    // Aggregate by symbol
    var bySymbol = {};
    holdings.forEach(function (h) {
        if (!bySymbol[h.symbol]) bySymbol[h.symbol] = 0;
        bySymbol[h.symbol] += h.current_value || 0;
    });
    var symbolValues = Object.values(bySymbol).sort(function (a, b) { return b - a; });

    var topHoldingPct = symbolValues.length > 0 ? (symbolValues[0] / totalValue * 100) : 100;
    var top5Pct = symbolValues.slice(0, 5).reduce(function (s, v) { return s + v; }, 0) / totalValue * 100;

    // Concentration score (max 30)
    var concentrationScore = 30;
    if (topHoldingPct > 50) concentrationScore -= 20;
    else if (topHoldingPct > 30) concentrationScore -= 10;
    else if (topHoldingPct > 20) concentrationScore -= 5;
    if (top5Pct > 80) concentrationScore -= 10;
    else if (top5Pct > 60) concentrationScore -= 5;
    concentrationScore = Math.max(0, concentrationScore);

    // Positions score (max 20)
    var numSymbols = Object.keys(bySymbol).length;
    var positionsScore = 0;
    if (numSymbols >= 20) positionsScore = 20;
    else if (numSymbols >= 15) positionsScore = 17;
    else if (numSymbols >= 10) positionsScore = 14;
    else if (numSymbols >= 5) positionsScore = 10;
    else positionsScore = numSymbols * 2;

    // Sector score (max 30)
    var bySector = {};
    holdings.forEach(function (h) {
        var sector = h.sector || 'Unknown';
        if (!bySector[sector]) bySector[sector] = 0;
        bySector[sector] += h.current_value || 0;
    });
    var numSectors = Object.keys(bySector).filter(function (s) { return s !== 'Unknown' && s !== 'Cash'; }).length;
    var sectorValues = Object.values(bySector).sort(function (a, b) { return b - a; });
    var topSectorPct = sectorValues.length > 0 ? (sectorValues[0] / totalValue * 100) : 100;

    var sectorScore = 0;
    if (numSectors >= 8) sectorScore = 15;
    else if (numSectors >= 5) sectorScore = 12;
    else if (numSectors >= 3) sectorScore = 8;
    else sectorScore = numSectors * 2;
    if (topSectorPct <= 30) sectorScore += 15;
    else if (topSectorPct <= 50) sectorScore += 10;
    else if (topSectorPct <= 70) sectorScore += 5;
    sectorScore = Math.min(30, sectorScore);

    // Account score (max 20)
    var byAccount = {};
    holdings.forEach(function (h) {
        if (!byAccount[h.account]) byAccount[h.account] = 0;
        byAccount[h.account] += h.current_value || 0;
    });
    var numAccounts = Object.keys(byAccount).length;
    var accountScore = 0;
    if (numAccounts >= 5) accountScore = 20;
    else if (numAccounts >= 3) accountScore = 15;
    else if (numAccounts >= 2) accountScore = 10;
    else accountScore = 5;

    var totalScore = Math.min(100, Math.max(0, concentrationScore + positionsScore + sectorScore + accountScore));

    var grade;
    if (totalScore >= 80) grade = 'Excellent';
    else if (totalScore >= 65) grade = 'Good';
    else if (totalScore >= 50) grade = 'Fair';
    else if (totalScore >= 35) grade = 'Needs Work';
    else grade = 'Poor';

    return {
        score: Math.round(totalScore),
        grade: grade,
        details: { concentration: concentrationScore, positions: positionsScore, sectors: sectorScore, accounts: accountScore }
    };
}

// ==========================================
// Allocation Charts
// ==========================================

function createAllocationChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('allocationChart');
    if (!ctx) return;

    var data = portfolioData.AccountAllocation;
    if (!data || typeof data !== 'object') return;

    var labels = Object.keys(data);
    var totalValue = portfolioData.TotalValue || 0;
    var values = Object.values(data).map(function (pct) { return (pct / 100) * totalValue; });
    var colors = labels.map(function (label, i) { return getAccountColor(label, i); });

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{ data: values, backgroundColor: colors, borderWidth: 1, borderColor: '#121212' }]
        },
        options: getDonutChartOptions()
    });
}

function createReturnsChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('returnsChart');
    if (!ctx) return;

    var costBasis = portfolioData.TotalCostBasis || 0;
    var unrealizedGain = portfolioData.TotalUnrealizedGain || 0;
    var realizedGains = portfolioData.AllTimeRealizedGain || 0;
    var income = portfolioData.AllTimeIncome || 0;

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Cost Basis', 'Unrealized', 'Realized', 'Income'],
            datasets: [{
                data: [costBasis, unrealizedGain, realizedGains, income],
                backgroundColor: ['#60a5fa', unrealizedGain >= 0 ? '#4ade80' : '#f87171', '#a78bfa', '#fbbf24'],
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: function (context) { return formatCurrency(context.raw); } } }
            },
            scales: {
                x: { ticks: { color: '#888' }, grid: { display: false } },
                y: {
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

function createPortfolioGrowthChart() {
    var ctx = document.getElementById('portfolioGrowthChart');
    if (!ctx) return;

    var historicalData = getHistoricalHoldings();
    if (!historicalData || historicalData.length < 2) return;

    // Get last 12 months of data
    var recentData = historicalData.slice(-12);
    var labels = recentData.map(function (d) {
        var date = new Date(d.date);
        return date.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
    });
    var values = recentData.map(function (d) { return d.totalValue; });

    // Get historical cost basis (if available) or fall back to current
    var costBasisData = recentData.map(function (d) {
        return d.costBasis || 0;
    });

    // Check if we have valid cost basis data
    var hasCostBasisHistory = costBasisData.some(function (cb) { return cb > 0; });

    var datasets = [
        {
            label: 'Portfolio Value',
            data: values,
            borderColor: '#4ade80',
            backgroundColor: 'rgba(74, 222, 128, 0.1)',
            fill: true,
            tension: 0.3,
            borderWidth: 2
        }
    ];

    if (hasCostBasisHistory) {
        datasets.push({
            label: 'Cost Basis',
            data: costBasisData,
            borderColor: '#f59e0b',
            backgroundColor: 'rgba(245, 158, 11, 0.05)',
            fill: true,
            tension: 0.3,
            borderWidth: 2
        });
    }

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#888', font: { size: 11 } }
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
                x: {
                    ticks: { color: '#888' },
                    grid: { display: false }
                },
                y: {
                    ticks: {
                        color: '#888',
                        callback: function (val) {
                            return '$' + (val / 1000).toFixed(0) + 'k';
                        }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

function createSectorAllocationChart() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var ctx = document.getElementById('sectorAllocationChart');
    if (!ctx) return;

    var sectorTotals = {};
    data.forEach(function (h) {
        var sector = h.sector || 'Unknown';
        if (!sectorTotals[sector]) sectorTotals[sector] = 0;
        sectorTotals[sector] += h.current_value || 0;
    });

    var sectorArray = Object.keys(sectorTotals).map(function (sector) {
        return { sector: sector, value: sectorTotals[sector] };
    }).sort(function (a, b) { return b.value - a.value; });

    var labels = sectorArray.map(function (item) { return item.sector; });
    var values = sectorArray.map(function (item) { return item.value; });
    var colors = labels.map(function (label, i) { return chartColorPalette[i % chartColorPalette.length]; });

    new Chart(ctx, {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 1, borderColor: '#121212' }] },
        options: getDonutChartOptions()
    });
}

function createTopHoldingsTable() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var tbody = document.getElementById('topHoldingsBody');
    if (!tbody) return;

    var sorted = data.slice().sort(function (a, b) { return b.current_value - a.current_value; });
    var top10 = sorted.slice(0, 10);

    var html = '';
    for (var i = 0; i < top10.length; i++) {
        var h = top10[i];
        var gainClass = h.unrealized_gain >= 0 ? 'positive' : 'negative';
        html += '<tr>';
        html += '<td><span class="symbol-badge">' + h.symbol + '</span></td>';
        html += '<td>' + h.account + '</td>';
        html += '<td class="number">' + formatCurrency(h.current_value) + '</td>';
        html += '<td class="number">' + formatCurrency(h.cost_basis) + '</td>';
        html += '<td class="number ' + gainClass + '">' + formatCurrency(h.unrealized_gain) + '</td>';
        html += '<td class="number ' + gainClass + '">' + formatPercent(h.unrealized_gain_pct) + '</td>';
        html += '</tr>';
    }
    tbody.innerHTML = html;
}
