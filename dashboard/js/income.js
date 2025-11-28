/**
 * Income Module
 * Income section, projections, and charts
 */

// Create income projection section
function createIncomeProjection() {
    var container = document.getElementById('incomeProjection');
    if (!container) return;

    var incomeData = getIncomeByYear();
    var transactions = getMasterTransactions();
    var holdings = getHoldingsDetail();

    if (incomeData.length === 0) {
        container.innerHTML = '<p style="color: #888; text-align: center;">No income data available for projection</p>';
        return;
    }

    var currentYear = new Date().getFullYear();
    var currentMonth = new Date().getMonth() + 1;

    // Calculate trailing 12 month income
    var today = new Date();
    var oneYearAgo = new Date(today);
    oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);

    var trailing12m = { dividends: 0, interest: 0, staking: 0, total: 0 };
    var incomeBySymbol = {};

    transactions.forEach(function (t) {
        var txDate = new Date(t.date);
        if (txDate >= oneYearAgo && txDate <= today) {
            if (t.action === 'Dividend' && t.amount > 0) {
                trailing12m.dividends += t.amount;
                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = { amount: 0, type: 'dividend' };
                incomeBySymbol[t.symbol].amount += t.amount;
            } else if (t.action === 'Interest' && t.amount > 0) {
                trailing12m.interest += t.amount;
                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = { amount: 0, type: 'interest' };
                incomeBySymbol[t.symbol].amount += t.amount;
            } else if (t.action === 'Staking' && t.amount > 0) {
                trailing12m.staking += t.amount;
                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = { amount: 0, type: 'staking' };
                incomeBySymbol[t.symbol].amount += t.amount;
            }
        }
    });
    trailing12m.total = trailing12m.dividends + trailing12m.interest + trailing12m.staking;

    // Calculate YTD income
    var ytdIncome = { dividends: 0, interest: 0, staking: 0, total: 0 };
    var yearStart = new Date(currentYear, 0, 1);

    transactions.forEach(function (t) {
        var txDate = new Date(t.date);
        if (txDate >= yearStart && txDate <= today) {
            if (t.action === 'Dividend' && t.amount > 0) ytdIncome.dividends += t.amount;
            else if (t.action === 'Interest' && t.amount > 0) ytdIncome.interest += t.amount;
            else if (t.action === 'Staking' && t.amount > 0) ytdIncome.staking += t.amount;
        }
    });
    ytdIncome.total = ytdIncome.dividends + ytdIncome.interest + ytdIncome.staking;

    // Project full year
    var monthsElapsed = currentMonth;
    var projectedFullYear = {
        dividends: (ytdIncome.dividends / monthsElapsed) * 12,
        interest: (ytdIncome.interest / monthsElapsed) * 12,
        staking: (ytdIncome.staking / monthsElapsed) * 12,
        total: (ytdIncome.total / monthsElapsed) * 12
    };

    // Calculate portfolio yield
    var totalPortfolioValue = holdings.reduce(function (sum, h) { return sum + (h.current_value || 0); }, 0);
    var portfolioYield = totalPortfolioValue > 0 ? (trailing12m.total / totalPortfolioValue * 100) : 0;

    // YoY comparison
    var lastYearData = incomeData.find(function (d) { return d.year === currentYear - 1; }) || { total: 0 };
    var yoyChange = lastYearData.total > 0 ? ((projectedFullYear.total - lastYearData.total) / lastYearData.total * 100) : 0;

    // Top income sources
    var topPayers = Object.keys(incomeBySymbol).map(function (symbol) {
        return { symbol: symbol, amount: incomeBySymbol[symbol].amount, type: incomeBySymbol[symbol].type };
    }).sort(function (a, b) {
        return b.amount - a.amount;
    }).slice(0, 5);

    var monthlyAvg = trailing12m.total / 12;

    // Build display
    var html = '<div class="projection-grid">';

    html += '<div class="projection-card main">';
    html += '<div class="projection-label">Projected ' + currentYear + ' Income</div>';
    html += '<div class="projection-value">' + formatCurrency(projectedFullYear.total) + '</div>';
    html += '<div class="projection-detail">';
    if (yoyChange !== 0) {
        var changeClass = yoyChange >= 0 ? 'positive' : 'negative';
        var changeSign = yoyChange >= 0 ? '+' : '';
        html += '<span class="' + changeClass + '">' + changeSign + yoyChange.toFixed(1) + '% vs ' + (currentYear - 1) + '</span>';
    }
    html += '</div></div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Trailing 12 Months</div>';
    html += '<div class="projection-value">' + formatCurrency(trailing12m.total) + '</div>';
    html += '<div class="projection-detail">Based on actual payments</div></div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">YTD Income (' + currentYear + ')</div>';
    html += '<div class="projection-value">' + formatCurrency(ytdIncome.total) + '</div>';
    html += '<div class="projection-detail">' + monthsElapsed + ' months elapsed</div></div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Monthly Average</div>';
    html += '<div class="projection-value">' + formatCurrency(monthlyAvg) + '</div>';
    html += '<div class="projection-detail">Based on trailing 12m</div></div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Portfolio Yield</div>';
    html += '<div class="projection-value">' + portfolioYield.toFixed(2) + '%</div>';
    html += '<div class="projection-detail">Annual income / portfolio value</div></div>';

    html += '</div>';

    // Top payers
    if (topPayers.length > 0) {
        html += '<div class="top-payers"><h4>Top Income Sources (12m)</h4><div class="payers-list">';
        topPayers.forEach(function (p) {
            var pct = (p.amount / trailing12m.total * 100).toFixed(1);
            var color = p.type === 'dividend' ? '#4ade80' : p.type === 'interest' ? '#60a5fa' : '#a78bfa';
            html += '<div class="payer-item">';
            html += '<span class="payer-symbol" style="color: ' + color + ';">' + p.symbol + '</span>';
            html += '<span class="payer-amount">' + formatCurrency(p.amount) + '</span>';
            html += '<span class="payer-pct">(' + pct + '%)</span>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // Breakdown bars
    html += '<div class="income-breakdown"><h4>Trailing 12 Month Breakdown</h4><div class="breakdown-bars">';
    var categories = [
        { name: 'Dividends', value: trailing12m.dividends, color: '#4ade80' },
        { name: 'Interest', value: trailing12m.interest, color: '#60a5fa' },
        { name: 'Staking', value: trailing12m.staking, color: '#a78bfa' }
    ];

    categories.forEach(function (cat) {
        if (cat.value > 0) {
            var pct = (cat.value / trailing12m.total * 100);
            html += '<div class="breakdown-row">';
            html += '<span class="breakdown-label">' + cat.name + '</span>';
            html += '<div class="breakdown-bar-container">';
            html += '<div class="breakdown-bar" style="width: ' + pct + '%; background: ' + cat.color + ';"></div>';
            html += '</div>';
            html += '<span class="breakdown-value">' + formatCurrency(cat.value) + ' (' + pct.toFixed(1) + '%)</span>';
            html += '</div>';
        }
    });

    html += '</div></div>';
    container.innerHTML = html;
}

// Create income table and chart
function createIncomeSection() {
    createIncomeProjection();

    var data = getIncomeByYear();
    if (data.length === 0) return;

    // Table
    var tbody = document.getElementById('incomeBody');
    if (tbody) {
        var html = '';
        for (var i = 0; i < data.length; i++) {
            var row = data[i];
            html += '<tr>';
            html += '<td style="font-weight: 600">' + row.year + '</td>';
            html += '<td class="number positive">' + formatCurrency(row.dividends || 0) + '</td>';
            html += '<td class="number positive">' + formatCurrency(row.interest || 0) + '</td>';
            html += '<td class="number positive">' + formatCurrency(row.staking || 0) + '</td>';
            html += '<td class="number">' + formatCurrency(row.other || 0) + '</td>';
            html += '<td class="number" style="font-weight: 600">' + formatCurrency(row.total || 0) + '</td>';
            html += '</tr>';
        }
        tbody.innerHTML = html;
    }

    // Chart
    var ctx = document.getElementById('incomeChart');
    if (!ctx) return;

    var labels = data.map(function (r) { return (r.year || '').toString(); });
    var dividends = data.map(function (r) { return r.dividends || 0; });
    var interest = data.map(function (r) { return r.interest || 0; });
    var staking = data.map(function (r) { return r.staking || 0; });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                { label: 'Dividends', data: dividends, backgroundColor: '#4ade80', borderRadius: 4 },
                { label: 'Interest', data: interest, backgroundColor: '#60a5fa', borderRadius: 4 },
                { label: 'Staking', data: staking, backgroundColor: '#a78bfa', borderRadius: 4 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#888' } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: { stacked: true, ticks: { color: '#888' }, grid: { display: false } },
                y: {
                    stacked: true,
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create dividend calendar heatmap
function createDividendCalendar() {
    var transactions = getMasterTransactions();
    if (transactions.length === 0) return;

    var ctx = document.getElementById('dividendCalendarChart');
    if (!ctx) return;

    var incomeTypes = ['Dividend', 'Interest', 'Staking'];
    var income = transactions.filter(function (t) {
        return incomeTypes.indexOf(t.action) !== -1 && t.amount > 0;
    });

    if (income.length === 0) return;

    var monthlyIncome = {};
    income.forEach(function (t) {
        var yearMonth = t.date.substring(0, 7);
        if (!monthlyIncome[yearMonth]) {
            monthlyIncome[yearMonth] = { dividends: 0, interest: 0, staking: 0 };
        }
        if (t.action === 'Dividend') monthlyIncome[yearMonth].dividends += t.amount;
        else if (t.action === 'Interest') monthlyIncome[yearMonth].interest += t.amount;
        else if (t.action === 'Staking') monthlyIncome[yearMonth].staking += t.amount;
    });

    var months = Object.keys(monthlyIncome).sort().slice(-24);
    var dividends = months.map(function (m) { return monthlyIncome[m].dividends; });
    var interest = months.map(function (m) { return monthlyIncome[m].interest; });
    var staking = months.map(function (m) { return monthlyIncome[m].staking; });

    var labels = months.map(function (m) {
        var parts = m.split('-');
        var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return monthNames[parseInt(parts[1]) - 1] + ' ' + parts[0].slice(2);
    });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                { label: 'Dividends', data: dividends, backgroundColor: '#4ade80', borderRadius: 4 },
                { label: 'Interest', data: interest, backgroundColor: '#60a5fa', borderRadius: 4 },
                { label: 'Staking', data: staking, backgroundColor: '#f59e0b', borderRadius: 4 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, position: 'top', labels: { color: '#888' } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: { stacked: true, ticks: { color: '#888', maxRotation: 45 }, grid: { display: false } },
                y: {
                    stacked: true,
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}
