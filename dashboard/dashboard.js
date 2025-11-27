/* Portfolio Dashboard JavaScript */

// Data normalization - map exported variable names and convert field names
function normalizeHoldings(data) {
    if (!data) return [];

    return data.map(function (h) {
        return {
            account: h.Account,
            symbol: h.Symbol,
            quantity: h.Quantity,
            cost_basis: h.CostBasis,
            current_price: h.CurrentPrice,
            current_value: h.CurrentValue,
            unrealized_gain: h.UnrealizedGain,
            unrealized_gain_pct: h.UnrealizedGainPct,
            change_7d: h.Change7D,
            change_30d: h.Change30D,
            sector: h.Sector || 'Unknown',
            first_buy_date: h.FirstPurchaseDate || null
        }

            ;
    }

    );
}

function normalizeAccounts(data) {
    if (!data) return [];

    return data.map(function (a) {
        return {
            account: a.Account,
            total_value: a.TotalValue,
            total_cost_basis: a.TotalCostBasis,
            unrealized_gain: a.TotalUnrealizedGain,
            unrealized_gain_pct: a.UnrealizedGainPct
        }

            ;
    }

    );
}

function normalizeIncome(data) {
    if (!data) return [];

    return data.map(function (r) {
        return {
            year: r.Year,
            dividends: r.Dividend || 0,
            interest: r.Interest || 0,
            staking: r.Staking || 0,
            other: r.StockLending || 0,
            total: r.Total || 0
        }

            ;
    }

    );
}

function normalizeHistory(data) {
    if (!data) return [];

    // The new format includes per-account columns, SP500 benchmark, TWR, TotalInvested, and WhatIfSP500
    // We need to extract account names from the first row
    if (data.length === 0) return [];

    var firstRow = data[0];
    var accounts = [];
    var excludeKeys = ['Date',
        'TotalValue',
        'NumPositions',
        'SP500',
        'TWR',
        'PeriodReturn',
        'TotalInvested',
        'WhatIfSP500'];

    for (var key in firstRow) {
        if (excludeKeys.indexOf(key) === -1) {
            accounts.push(key);
        }
    }

    return data.map(function (h) {
        var result = {

            date: h.Date,
            totalValue: h.TotalValue,
            totalInvested: h.TotalInvested || 0,
            sp500: h.SP500 || null,
            twr: h.TWR || 0,
            periodReturn: h.PeriodReturn || 0,
            whatIfSP500: h.WhatIfSP500 || null,
            accounts: {}
        }

            ;

        for (var i = 0; i < accounts.length; i++) {
            result.accounts[accounts[i]] = h[accounts[i]] || 0;
        }

        return result;
    }

    );
}

// Get list of accounts from historical data
function getHistoricalAccounts() {
    if (typeof historicalHoldingsData === 'undefined' || historicalHoldingsData.length === 0) {
        return [];
    }

    var firstRow = historicalHoldingsData[0];
    var accounts = [];
    var excludeKeys = ['Date',
        'TotalValue',
        'NumPositions',
        'SP500',
        'TWR',
        'PeriodReturn',
        'TotalInvested',
        'WhatIfSP500'];

    for (var key in firstRow) {
        if (excludeKeys.indexOf(key) === -1) {
            accounts.push(key);
        }
    }

    return accounts.sort();
}

// Lazy-loaded normalized data
var holdingsDetail = null;
var accountSummary = null;
var incomeByYear = null;
var historicalHoldings = null;
var masterTransactions = null;

function getHoldingsDetail() {
    if (holdingsDetail === null && typeof holdingsDetailData !== 'undefined') {
        holdingsDetail = normalizeHoldings(holdingsDetailData);
    }

    return holdingsDetail || [];
}

function getAccountSummary() {
    if (accountSummary === null && typeof accountSummaryData !== 'undefined') {
        accountSummary = normalizeAccounts(accountSummaryData);
    }

    return accountSummary || [];
}

function getIncomeByYear() {
    if (incomeByYear === null && typeof incomeByYearData !== 'undefined') {
        incomeByYear = normalizeIncome(incomeByYearData);
    }

    return incomeByYear || [];
}

function getHistoricalHoldings() {
    if (historicalHoldings === null && typeof historicalHoldingsData !== 'undefined') {
        historicalHoldings = normalizeHistory(historicalHoldingsData);
    }

    return historicalHoldings || [];
}

// Cash balances data
var cashBalances = null;

function getCashBalances() {
    if (cashBalances === null && typeof cashBalancesData !== 'undefined') {
        cashBalances = cashBalancesData.map(function (c) {
            return {
                account: c.Account,
                balance: c.CurrentBalance,
                deposits: c.TotalDeposits,
                withdrawals: c.TotalWithdrawals,
                netContributions: c.NetContributions,
                totalInterest: c.TotalInterest,
                returnPct: c.ReturnPct
            }

                ;
        }

        );
    }

    return cashBalances || [];
}

function getMasterTransactions() {
    if (masterTransactions === null && typeof masterTransactionsData !== 'undefined') {
        masterTransactions = masterTransactionsData.map(function (t) {
            return {
                date: t.Date,
                account: t.Account,
                symbol: t.Symbol,
                action: t.Action,
                quantity: t.Quantity,
                price: t.Price,
                fee: t.Fee,
                amount: t.Amount,
                note: t.Note,
                runningBalance: t.RunningBalance,
                runningValue: t.RunningValue
            }

                ;
        }

        );
    }

    return masterTransactions || [];
}

function getTransactionsForHolding(symbol, account) {
    var allTx = getMasterTransactions();

    return allTx.filter(function (t) {
        return t.symbol === symbol && t.account === account;
    }

    ).sort(function (a, b) {
        return b.date.localeCompare(a.date);
    }

    );
}

// Chart color palettes - muted tones
const chartColorPalette = ['#60a5fa',
    '#a78bfa',
    '#f59e0b',
    '#34d399',
    '#f472b6',
    '#fbbf24',
    '#2dd4bf',
    '#818cf8',
    '#fb7185',
    '#4ade80'
];

const accountColors = {
    'default': '#60a5fa'
}

    ;

const assetColors = {
    'default': '#60a5fa'
}

    ;

// Shared donut chart options for consistent appearance
function getDonutChartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '55%',
        plugins: {
            legend: {
                position: 'right',
                labels: {
                    color: '#888',
                    padding: 10,
                    font: {
                        size: 11
                    },
                    boxWidth: 12,
                    usePointStyle: true
                }
            },
            tooltip: {
                callbacks: {
                    label: function (context) {
                        var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                        var pct = ((context.raw / total) * 100).toFixed(1);
                        return context.label + ': ' + formatCurrency(context.raw) + ' (' + pct + '%)';
                    }
                }
            }
        }
    };
}

// Formatters
function formatCurrency(value) {
    if (value === undefined || value === null || isNaN(value)) return '$0.00';

    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2
    }

    ).format(value);
}

function formatPercent(value) {
    if (value === undefined || value === null || isNaN(value)) return '+0.00%';
    return (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
}

function formatNumber(value, decimals) {
    if (value === undefined || value === null || isNaN(value)) return '0';
    if (decimals === undefined) decimals = 4;
    if (Math.abs(value) < 0.0001) return value.toExponential(2);

    return value.toLocaleString('en-US', {
        maximumFractionDigits: decimals
    }

    );
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    var date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

// Get color for asset
function getAssetColor(symbol, index) {
    return assetColors[symbol] || chartColorPalette[index % chartColorPalette.length];
}

// Get color for account
function getAccountColor(account, index) {
    for (var key in accountColors) {
        if (key !== 'default' && account.toLowerCase().includes(key.toLowerCase())) {
            return accountColors[key];
        }
    }

    return chartColorPalette[index % chartColorPalette.length];
}

// Tab switching
function initTabs() {
    var tabBtns = document.querySelectorAll('.tab-btn');
    var tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var tabId = btn.dataset.tab;

            tabBtns.forEach(function (b) {
                b.classList.remove('active');
            }

            );

            tabContents.forEach(function (c) {
                c.classList.remove('active');
            }

            );

            btn.classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        );
    }

    );
}

// Account card expansion
function initAccountCards() {
    document.querySelectorAll('.account-header').forEach(function (header) {
        header.addEventListener('click', function () {
            header.parentElement.classList.toggle('expanded');
        }

        );
    }

    );
}

// Holding row click to show transactions
function initHoldingRowClicks(container) {
    var rows = container.querySelectorAll('.holding-row.clickable');

    rows.forEach(function (row) {
        row.addEventListener('click', function (e) {
            e.stopPropagation(); // Prevent bubbling to account header

            var symbol = row.getAttribute('data-symbol');
            var account = row.getAttribute('data-account');
            var rowId = row.getAttribute('data-row-id');
            var detailRow = document.getElementById(rowId);

            if (!detailRow) return;

            // Toggle visibility
            var isVisible = detailRow.style.display !== 'none';

            if (isVisible) {
                detailRow.style.display = 'none';
                row.classList.remove('expanded');
            }

            else {
                // Build transaction table if not already built
                var cell = detailRow.querySelector('td');

                if (cell && cell.innerHTML === '') {
                    var transactions = getTransactionsForHolding(symbol, account);
                    cell.innerHTML = buildTransactionTable(transactions, symbol);
                }

                detailRow.style.display = '';
                row.classList.add('expanded');
            }
        }

        );
    }

    );
}

// Build transaction history table HTML with running holdings balance
// Now uses pre-calculated RunningBalance and RunningValue from Python
function buildTransactionTable(transactions, symbol) {
    if (transactions.length === 0) {
        return '<div class="transaction-table-wrapper"><p class="no-transactions">No transactions found for ' + symbol + '</p></div>';
    }

    var html = '<div class="transaction-table-wrapper">';
    html += '<table class="transaction-table">';
    html += '<thead><tr>';
    html += '<th>Date</th><th>Action</th><th style="text-align:right">Quantity</th>';
    html += '<th style="text-align:right">Price</th><th style="text-align:right">Amount</th>';
    html += '<th style="text-align:right">Holdings</th><th style="text-align:right">Value</th>';
    html += '<th>Note</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < transactions.length; i++) {
        var t = transactions[i];
        var actionClass = '';
        if (t.action === 'Buy') actionClass = 'action-buy';
        else if (t.action === 'Sell') actionClass = 'action-sell';
        else if (t.action === 'Dividend' || t.action === 'Interest' || t.action === 'Staking') actionClass = 'action-income';

        html += '<tr>';
        html += '<td>' + t.date + '</td>';
        html += '<td><span class="action-badge ' + actionClass + '">' + t.action + '</span></td>';
        html += '<td class="number">' + formatNumber(t.quantity) + '</td>';
        html += '<td class="number">' + formatCurrency(t.price) + '</td>';
        html += '<td class="number">' + formatCurrency(t.amount) + '</td>';
        html += '<td class="number">' + formatNumber(t.runningBalance, 4) + '</td>';
        html += '<td class="number">' + formatCurrency(t.runningValue) + '</td>';
        html += '<td class="note">' + (t.note || '') + '</td>';
        html += '</tr>';
    }

    html += '</tbody></table></div>';
    return html;
}

// Holdings search
function initHoldingsSearch() {
    var searchBox = document.getElementById('holdingsSearch');
    if (!searchBox) return;

    searchBox.addEventListener('input', function (e) {
        var term = e.target.value.toLowerCase();

        document.querySelectorAll('#allHoldingsBody tr.holding-row').forEach(function (row) {
            var text = row.textContent.toLowerCase();
            var rowId = row.getAttribute('data-row-id');
            var detailRow = document.getElementById(rowId);

            if (text.includes(term)) {
                row.style.display = '';
            }

            else {
                row.style.display = 'none';
                if (detailRow) detailRow.style.display = 'none';
            }
        }

        );
    }

    );
}

// Create summary cards
function createSummaryCards() {
    if (typeof portfolioData === 'undefined') return;

    var data = portfolioData;
    var container = document.getElementById('summaryCards');

    // Calculate time-based changes from historical data
    var historicalData = getHistoricalHoldings();
    var timeChanges = calculateTimeChanges(historicalData);

    // Calculate diversification score
    var holdings = getHoldingsDetail();
    var diversification = calculateDiversificationScore(holdings);

    var cards = [{
        label: 'Total Portfolio Value', value: formatCurrency(data.TotalValue), cls: ''
    }

        ,
    {
        label: 'Investment Value', value: formatCurrency(data.InvestmentValue), cls: ''
    }

        ,
    {
        label: 'Cash & Savings', value: formatCurrency(data.CashValue), cls: ''
    }

        ,
    {
        label: 'Unrealized Gain/Loss',
        value: formatCurrency(data.TotalUnrealizedGain),
        subvalue: formatPercent(data.UnrealizedGainPct),
        cls: (data.TotalUnrealizedGain || 0) >= 0 ? 'positive' : 'negative'
    }

        ,
    {
        label: '1 Month Change',
        value: formatCurrency(timeChanges.oneMonth.change),
        subvalue: (timeChanges.oneMonth.pct >= 0 ? '+' : '') + timeChanges.oneMonth.pct.toFixed(2) + '%',
        cls: timeChanges.oneMonth.change >= 0 ? 'positive' : 'negative'
    }

        ,
    {
        label: '3 Month Change',
        value: formatCurrency(timeChanges.threeMonth.change),
        subvalue: (timeChanges.threeMonth.pct >= 0 ? '+' : '') + timeChanges.threeMonth.pct.toFixed(2) + '%',
        cls: timeChanges.threeMonth.change >= 0 ? 'positive' : 'negative'
    }

        ,
    {
        label: 'YTD Change',
        value: formatCurrency(timeChanges.ytd.change),
        subvalue: (timeChanges.ytd.pct >= 0 ? '+' : '') + timeChanges.ytd.pct.toFixed(2) + '%',
        cls: timeChanges.ytd.change >= 0 ? 'positive' : 'negative'
    }

        ,
    {
        label: 'Diversification Score',
        value: diversification.score + '/100',
        subvalue: diversification.grade,
        cls: diversification.score >= 70 ? 'positive' : diversification.score >= 40 ? '' : 'negative'
    }

        ,
    {
        label: 'Total Positions', value: (data.NumPositions || 0).toString(), cls: ''
    }

        ,
    {
        label: 'Total Accounts', value: (data.NumAccounts || 0).toString(), cls: ''
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

// Calculate portfolio changes over time
function calculateTimeChanges(historicalData) {
    var result = {
        oneMonth: { change: 0, pct: 0 },
        threeMonth: { change: 0, pct: 0 },
        ytd: { change: 0, pct: 0 }
    };

    if (!historicalData || historicalData.length < 2) return result;

    // Get current value (most recent)
    var current = historicalData[historicalData.length - 1];
    var currentValue = current.totalValue || 0;
    var currentDate = new Date(current.date);

    // Find values at different time periods (using monthly data)
    var oneMonthAgo = new Date(currentDate);
    oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);

    var threeMonthsAgo = new Date(currentDate);
    threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);

    var yearStart = new Date(currentDate.getFullYear(), 0, 1);

    var oneMonthValue = null, threeMonthValue = null, ytdValue = null;

    // Find closest data points
    for (var i = historicalData.length - 1; i >= 0; i--) {
        var entry = historicalData[i];
        var entryDate = new Date(entry.date);

        if (oneMonthValue === null && entryDate <= oneMonthAgo) {
            oneMonthValue = entry.totalValue;
        }
        if (threeMonthValue === null && entryDate <= threeMonthsAgo) {
            threeMonthValue = entry.totalValue;
        }
        if (ytdValue === null && entryDate <= yearStart) {
            ytdValue = entry.totalValue;
        }
    }

    // Use earliest available if we don't have old enough data
    var earliest = historicalData[0];
    if (oneMonthValue === null) oneMonthValue = earliest.totalValue;
    if (threeMonthValue === null) threeMonthValue = earliest.totalValue;
    if (ytdValue === null) ytdValue = earliest.totalValue;

    // Calculate changes
    result.oneMonth.change = currentValue - oneMonthValue;
    result.oneMonth.pct = oneMonthValue > 0 ? (result.oneMonth.change / oneMonthValue * 100) : 0;

    result.threeMonth.change = currentValue - threeMonthValue;
    result.threeMonth.pct = threeMonthValue > 0 ? (result.threeMonth.change / threeMonthValue * 100) : 0;

    result.ytd.change = currentValue - ytdValue;
    result.ytd.pct = ytdValue > 0 ? (result.ytd.change / ytdValue * 100) : 0;

    return result;
}

// Calculate diversification score (0-100)
function calculateDiversificationScore(holdings) {
    var result = { score: 0, grade: 'N/A', details: {} };

    if (!holdings || holdings.length === 0) return result;

    var totalValue = holdings.reduce(function (sum, h) { return sum + (h.current_value || 0); }, 0);
    if (totalValue === 0) return result;

    // 1. Concentration score (max 30 points) - penalize if top holding is too large
    var holdingsByValue = holdings.slice().sort(function (a, b) {
        return (b.current_value || 0) - (a.current_value || 0);
    });

    // Aggregate by symbol
    var bySymbol = {};
    holdings.forEach(function (h) {
        if (!bySymbol[h.symbol]) bySymbol[h.symbol] = 0;
        bySymbol[h.symbol] += h.current_value || 0;
    });
    var symbolValues = Object.values(bySymbol).sort(function (a, b) { return b - a; });

    var topHoldingPct = symbolValues.length > 0 ? (symbolValues[0] / totalValue * 100) : 100;
    var top5Pct = symbolValues.slice(0, 5).reduce(function (s, v) { return s + v; }, 0) / totalValue * 100;

    var concentrationScore = 30;
    if (topHoldingPct > 50) concentrationScore -= 20;
    else if (topHoldingPct > 30) concentrationScore -= 10;
    else if (topHoldingPct > 20) concentrationScore -= 5;

    if (top5Pct > 80) concentrationScore -= 10;
    else if (top5Pct > 60) concentrationScore -= 5;

    concentrationScore = Math.max(0, concentrationScore);

    // 2. Number of positions score (max 20 points)
    var numSymbols = Object.keys(bySymbol).length;
    var positionsScore = 0;
    if (numSymbols >= 20) positionsScore = 20;
    else if (numSymbols >= 15) positionsScore = 17;
    else if (numSymbols >= 10) positionsScore = 14;
    else if (numSymbols >= 5) positionsScore = 10;
    else positionsScore = numSymbols * 2;

    // 3. Sector diversification (max 30 points)
    var bySector = {};
    holdings.forEach(function (h) {
        var sector = h.sector || 'Unknown';
        if (!bySector[sector]) bySector[sector] = 0;
        bySector[sector] += h.current_value || 0;
    });

    var numSectors = Object.keys(bySector).filter(function (s) {
        return s !== 'Unknown' && s !== 'Cash';
    }).length;
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

    // 4. Account diversification (max 20 points)
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

    // Total score
    var totalScore = concentrationScore + positionsScore + sectorScore + accountScore;
    totalScore = Math.min(100, Math.max(0, totalScore));

    // Grade
    var grade;
    if (totalScore >= 80) grade = 'Excellent';
    else if (totalScore >= 65) grade = 'Good';
    else if (totalScore >= 50) grade = 'Fair';
    else if (totalScore >= 35) grade = 'Needs Work';
    else grade = 'Poor';

    return {
        score: Math.round(totalScore),
        grade: grade,
        details: {
            concentration: concentrationScore,
            positions: positionsScore,
            sectors: sectorScore,
            accounts: accountScore
        }
    };
}

// Create allocation chart
function createAllocationChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('allocationChart');
    if (!ctx) return;

    var data = portfolioData.AccountAllocation;
    if (!data || typeof data !== 'object') return;

    var labels = Object.keys(data);
    // Convert percentages to actual values for the chart
    var totalValue = portfolioData.TotalValue || 0;

    var values = Object.values(data).map(function (pct) {
        return (pct / 100) * totalValue;
    });

    var colors = labels.map(function (label, i) {
        return getAccountColor(label, i);
    });

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 1,
                borderColor: '#121212'
            }]
        },
        options: getDonutChartOptions()
    });
}

// Create investment vs cash chart
function createInvestmentCashChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('investmentCashChart');
    if (!ctx) return;

    var investmentValue = portfolioData.InvestmentValue || 0;
    var cashValue = portfolioData.CashValue || 0;

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Investments', 'Cash'],
            datasets: [{
                data: [investmentValue, cashValue],
                backgroundColor: ['#60a5fa', '#4ade80'],
                borderWidth: 1,
                borderColor: '#121212'
            }]
        },
        options: getDonutChartOptions()
    });
}

// Create returns breakdown chart
function createReturnsChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('returnsChart');
    if (!ctx) return;

    var costBasis = portfolioData.TotalCostBasis || 0;
    var unrealizedGain = portfolioData.TotalUnrealizedGain || 0;
    var realizedGains = portfolioData.AllTimeRealizedGain || 0;

    new Chart(ctx, {

        type: 'bar',
        data: {

            labels: ['Cost Basis', 'Unrealized', 'Realized'],
            datasets: [{
                data: [costBasis, unrealizedGain, realizedGains],
                backgroundColor: ['#60a5fa', unrealizedGain >= 0 ? '#4ade80' : '#f87171', '#a78bfa'],
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return formatCurrency(context.raw);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888'
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create tax lot breakdown chart
function createTaxLotChart() {
    if (typeof portfolioData === 'undefined') return;

    var ctx = document.getElementById('taxLotChart');
    if (!ctx) return;

    var longTerm = portfolioData.LongTermValue || 0;
    var shortTerm = portfolioData.ShortTermValue || 0;

    if (longTerm === 0 && shortTerm === 0) {
        // No data available
        return;
    }

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Long-term', 'Short-term'],
            datasets: [{
                data: [longTerm, shortTerm],
                backgroundColor: ['#4ade80', '#f59e0b'],
                borderWidth: 1,
                borderColor: '#121212'
            }]
        },
        options: getDonutChartOptions()
    });
}

// Create asset allocation chart (Top 10 holdings + Other)
function createAssetAllocationChart() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var ctx = document.getElementById('assetAllocationChart');
    if (!ctx) return;

    // Group by symbol and sum values across accounts
    var symbolTotals = {};

    data.forEach(function (h) {
        if (!symbolTotals[h.symbol]) {
            symbolTotals[h.symbol] = 0;
        }
        symbolTotals[h.symbol] += h.current_value || 0;
    });

    // Convert to array and sort by value
    var symbolArray = Object.keys(symbolTotals).map(function (symbol) {
        return { symbol: symbol, value: symbolTotals[symbol] };
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    // Take top 14 and group rest as "Other"
    var top14 = symbolArray.slice(0, 14);
    var otherValue = symbolArray.slice(14).reduce(function (sum, item) {
        return sum + item.value;
    }, 0);

    var labels = top14.map(function (item) { return item.symbol; });
    var values = top14.map(function (item) { return item.value; });

    if (otherValue > 0) {
        labels.push('Other (' + (symbolArray.length - 14) + ')');
        values.push(otherValue);
    }

    var colors = labels.map(function (label, i) {
        return chartColorPalette[i % chartColorPalette.length];
    });

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 1,
                borderColor: '#121212'
            }]
        },
        options: getDonutChartOptions()
    });
}

// Create sector allocation chart
function createSectorAllocationChart() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var ctx = document.getElementById('sectorAllocationChart');
    if (!ctx) return;

    // Group by sector and sum values
    var sectorTotals = {};

    data.forEach(function (h) {
        var sector = h.sector || 'Unknown';
        if (!sectorTotals[sector]) {
            sectorTotals[sector] = 0;
        }
        sectorTotals[sector] += h.current_value || 0;
    });

    // Convert to array and sort by value
    var sectorArray = Object.keys(sectorTotals).map(function (sector) {
        return { sector: sector, value: sectorTotals[sector] };
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    var labels = sectorArray.map(function (item) { return item.sector; });
    var values = sectorArray.map(function (item) { return item.value; });

    var colors = labels.map(function (label, i) {
        return chartColorPalette[i % chartColorPalette.length];
    });

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 1,
                borderColor: '#121212'
            }]
        },
        options: getDonutChartOptions()
    });
}

// Create top holdings table
function createTopHoldingsTable() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var tbody = document.getElementById('topHoldingsBody');
    if (!tbody) return;

    var sorted = data.slice().sort(function (a, b) {
        return b.current_value - a.current_value;
    }

    );
    ;
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

// Create account cards with holdings
function createAccountCards() {
    var accounts = getAccountSummary();
    var holdings = getHoldingsDetail();
    var cashAccounts = getCashBalances();
    if (accounts.length === 0) return;

    var container = document.getElementById('accountsGrid');
    if (!container) return;

    // Group holdings by account
    var holdingsByAccount = {}

        ;

    holdings.forEach(function (h) {
        if (!holdingsByAccount[h.account]) {
            holdingsByAccount[h.account] = [];
        }

        holdingsByAccount[h.account].push(h);
    }

    );

    // Create a map of cash-only accounts for quick lookup
    var cashAccountMap = {}

        ;

    cashAccounts.forEach(function (c) {
        cashAccountMap[c.account] = c;
    }

    );

    var html = '';

    for (var i = 0; i < accounts.length; i++) {
        var account = accounts[i];
        var acctHoldings = holdingsByAccount[account.account] || [];
        var gainClass = account.unrealized_gain >= 0 ? 'positive' : 'negative';

        // For cash-only accounts (like Apple Savings), create a synthetic USD holding
        if (acctHoldings.length === 0 && cashAccountMap[account.account]) {
            var cashData = cashAccountMap[account.account];

            acctHoldings = [{
                symbol: 'USD',
                account: account.account,
                quantity: cashData.balance,
                current_price: 1.0,
                current_value: cashData.balance,
                cost_basis: cashData.netContributions,
                unrealized_gain: cashData.totalInterest,
                unrealized_gain_pct: cashData.returnPct
            }

            ];
        }

        // Use different icon for cash accounts
        var isCashAccount = cashAccountMap[account.account] ? true : false;
        var accountIcon = isCashAccount ? '🏦' : '💼';

        html += '<div class="account-card">';
        html += '<div class="account-header">';
        html += '<div class="account-name"><span class="icon">' + accountIcon + '</span>' + account.account + '</div>';
        html += '<div class="account-value">';
        html += '<div class="account-total">' + formatCurrency(account.total_value) + '</div>';
        html += '<div class="account-gain ' + gainClass + '">' + formatCurrency(account.unrealized_gain) + ' (' + formatPercent(account.unrealized_gain_pct) + ')</div>';
        html += '</div>';
        html += '<span class="expand-icon">▼</span>';
        html += '</div>';
        html += '<div class="account-holdings">';

        if (acctHoldings.length > 0) {
            html += '<table class="holdings-table"><thead><tr>';
            html += '<th>Symbol</th><th style="text-align:right">Quantity</th>';
            html += '<th style="text-align:right">Price</th><th style="text-align:right">Value</th>';
            html += '<th style="text-align:right">Gain/Loss</th></tr></thead><tbody>';

            for (var j = 0; j < acctHoldings.length; j++) {
                var h = acctHoldings[j];
                var hGainClass = h.unrealized_gain >= 0 ? 'positive' : 'negative';
                var rowId = 'acct-holding-' + i + '-' + j;
                html += '<tr class="holding-row clickable" data-symbol="' + h.symbol + '" data-account="' + h.account + '" data-row-id="' + rowId + '">';
                html += '<td><span class="symbol-badge">' + h.symbol + '</span> <span class="expand-hint">▶</span></td>';
                html += '<td class="number">' + formatNumber(h.quantity) + '</td>';
                html += '<td class="number">' + formatCurrency(h.current_price) + '</td>';
                html += '<td class="number">' + formatCurrency(h.current_value) + '</td>';
                html += '<td class="number ' + hGainClass + '">' + formatCurrency(h.unrealized_gain) + '<br><small>' + formatPercent(h.unrealized_gain_pct) + '</small></td>';
                html += '</tr>';
                html += '<tr class="transaction-detail-row" id="' + rowId + '" style="display:none;"><td colspan="5"></td></tr>';
            }

            html += '</tbody></table>';
        }

        html += '</div></div>';
    }

    container.innerHTML = html;
    initAccountCards();
    initHoldingRowClicks(container);
}

// Format percent change with color class
function formatPriceChange(value) {
    if (value === null || value === undefined || isNaN(value)) return '<span class="muted">—</span>';
    var cls = value >= 0 ? 'positive' : 'negative';
    return '<span class="' + cls + '">' + formatPercent(value) + '</span>';
}

// Holdings table sorting state
var holdingsSortColumn = 'current_value';
var holdingsSortDirection = 'desc';

// Render holdings table rows
function renderHoldingsTableRows(data) {
    var tbody = document.getElementById('allHoldingsBody');
    if (!tbody) return;

    var html = '';

    for (var i = 0; i < data.length; i++) {
        var h = data[i];
        var gainClass = h.unrealized_gain >= 0 ? 'positive' : 'negative';
        var rowId = 'all-holding-' + i;
        html += '<tr class="holding-row clickable" data-symbol="' + h.symbol + '" data-account="' + h.account + '" data-row-id="' + rowId + '">';
        html += '<td><span class="symbol-badge">' + h.symbol + '</span> <span class="expand-hint">▶</span></td>';
        html += '<td>' + h.account + '</td>';
        html += '<td class="number">' + formatNumber(h.quantity) + '</td>';
        html += '<td class="number">' + formatCurrency(h.current_price) + '</td>';
        html += '<td class="number">' + formatCurrency(h.current_value) + '</td>';
        html += '<td class="number">' + formatCurrency(h.cost_basis) + '</td>';
        html += '<td class="number ' + gainClass + '">' + formatCurrency(h.unrealized_gain) + '</td>';
        html += '<td class="number ' + gainClass + '">' + formatPercent(h.unrealized_gain_pct) + '</td>';
        html += '<td class="number">' + formatPriceChange(h.change_7d) + '</td>';
        html += '<td class="number">' + formatPriceChange(h.change_30d) + '</td>';
        html += '</tr>';
        html += '<tr class="transaction-detail-row" id="' + rowId + '" style="display:none;"><td colspan="10"></td></tr>';
    }

    tbody.innerHTML = html;
    initHoldingRowClicks(tbody);
}

// Sort holdings data
function sortHoldingsData(data, column, direction) {
    return data.slice().sort(function (a, b) {
        var aVal = a[column];
        var bVal = b[column];

        // Handle null/undefined values - put them at the end
        if (aVal === null || aVal === undefined) aVal = direction === 'asc' ? Infinity : -Infinity;
        if (bVal === null || bVal === undefined) bVal = direction === 'asc' ? Infinity : -Infinity;

        // String comparison for text columns
        if (column === 'symbol' || column === 'account') {
            aVal = (aVal || '').toString().toLowerCase();
            bVal = (bVal || '').toString().toLowerCase();

            if (direction === 'asc') {
                return aVal.localeCompare(bVal);
            }

            else {
                return bVal.localeCompare(aVal);
            }
        }

        // Numeric comparison
        if (direction === 'asc') {
            return aVal - bVal;
        }

        else {
            return bVal - aVal;
        }
    }

    );
}

// Update sort header indicators
function updateSortIndicators(column, direction) {
    var headers = document.querySelectorAll('#allHoldingsTable th.sortable');

    headers.forEach(function (th) {
        th.classList.remove('sort-asc', 'sort-desc');

        if (th.getAttribute('data-sort') === column) {
            th.classList.add(direction === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    }

    );
}

// Initialize sortable table headers
function initSortableTable() {
    var headers = document.querySelectorAll('#allHoldingsTable th.sortable');

    headers.forEach(function (th) {
        th.addEventListener('click', function () {
            var column = th.getAttribute('data-sort');

            // Toggle direction if same column, otherwise default to desc for numbers, asc for text
            if (column === holdingsSortColumn) {
                holdingsSortDirection = holdingsSortDirection === 'asc' ? 'desc' : 'asc';
            }

            else {
                holdingsSortColumn = column;

                // Default: ascending for text, descending for numbers
                if (column === 'symbol' || column === 'account') {
                    holdingsSortDirection = 'asc';
                }

                else {
                    holdingsSortDirection = 'desc';
                }
            }

            // Sort and re-render
            var data = getHoldingsDetail();
            var sorted = sortHoldingsData(data, holdingsSortColumn, holdingsSortDirection);
            renderHoldingsTableRows(sorted);
            updateSortIndicators(holdingsSortColumn, holdingsSortDirection);
        }

        );
    }

    );

    // Set initial sort indicator
    updateSortIndicators(holdingsSortColumn, holdingsSortDirection);
}

// Create all holdings table
function createAllHoldingsTable() {
    var data = getHoldingsDetail();
    if (data.length === 0) return;

    var sorted = sortHoldingsData(data, holdingsSortColumn, holdingsSortDirection);
    renderHoldingsTableRows(sorted);
    initSortableTable();
}

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

    // Get the current year and last complete year
    var currentYear = new Date().getFullYear();
    var currentMonth = new Date().getMonth() + 1; // 1-12

    // Calculate trailing 12 month income from transactions
    var today = new Date();
    var oneYearAgo = new Date(today);
    oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);

    var trailing12m = {
        dividends: 0, interest: 0, staking: 0, total: 0
    }

        ;

    var incomeBySymbol = {}

        ;

    transactions.forEach(function (t) {
        var txDate = new Date(t.date);

        if (txDate >= oneYearAgo && txDate <= today) {
            if (t.action === 'Dividend' && t.amount > 0) {
                trailing12m.dividends += t.amount;

                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = {
                    amount: 0, type: 'dividend'
                }

                    ;
                incomeBySymbol[t.symbol].amount += t.amount;
            }

            else if (t.action === 'Interest' && t.amount > 0) {
                trailing12m.interest += t.amount;

                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = {
                    amount: 0, type: 'interest'
                }

                    ;
                incomeBySymbol[t.symbol].amount += t.amount;
            }

            else if (t.action === 'Staking' && t.amount > 0) {
                trailing12m.staking += t.amount;

                if (!incomeBySymbol[t.symbol]) incomeBySymbol[t.symbol] = {
                    amount: 0, type: 'staking'
                }

                    ;
                incomeBySymbol[t.symbol].amount += t.amount;
            }
        }
    }

    );
    trailing12m.total = trailing12m.dividends + trailing12m.interest + trailing12m.staking;

    // Calculate YTD income
    var ytdIncome = {
        dividends: 0, interest: 0, staking: 0, total: 0
    }

        ;
    var yearStart = new Date(currentYear, 0, 1);

    transactions.forEach(function (t) {
        var txDate = new Date(t.date);

        if (txDate >= yearStart && txDate <= today) {
            if (t.action === 'Dividend' && t.amount > 0) {
                ytdIncome.dividends += t.amount;
            }

            else if (t.action === 'Interest' && t.amount > 0) {
                ytdIncome.interest += t.amount;
            }

            else if (t.action === 'Staking' && t.amount > 0) {
                ytdIncome.staking += t.amount;
            }
        }
    }

    );
    ytdIncome.total = ytdIncome.dividends + ytdIncome.interest + ytdIncome.staking;

    // Project full year based on YTD (annualized)
    var monthsElapsed = currentMonth;

    var projectedFullYear = {
        dividends: (ytdIncome.dividends / monthsElapsed) * 12,
        interest: (ytdIncome.interest / monthsElapsed) * 12,
        staking: (ytdIncome.staking / monthsElapsed) * 12,
        total: (ytdIncome.total / monthsElapsed) * 12
    }

        ;

    // Calculate portfolio yield based on trailing 12m and current portfolio value
    var totalPortfolioValue = holdings.reduce(function (sum, h) {
        return sum + (h.current_value || 0);
    }

        , 0);
    var portfolioYield = totalPortfolioValue > 0 ? (trailing12m.total / totalPortfolioValue * 100) : 0;

    // Get last complete year for comparison
    var lastYearData = incomeData.find(function (d) {
        return d.year === currentYear - 1;
    }

    ) || {
        total: 0
    }

        ;
    var yoyChange = lastYearData.total > 0 ? ((projectedFullYear.total - lastYearData.total) / lastYearData.total * 100) : 0;

    // Get top income sources (with type info for coloring)
    var topPayers = Object.keys(incomeBySymbol).map(function (symbol) {
        return {
            symbol: symbol, amount: incomeBySymbol[symbol].amount, type: incomeBySymbol[symbol].type
        }

            ;
    }

    ).sort(function (a, b) {
        return b.amount - a.amount;
    }

    ).slice(0, 5);

    // Monthly average
    var monthlyAvg = trailing12m.total / 12;

    // Build the projection display
    var html = '<div class="projection-grid">';

    // Main projection cards
    html += '<div class="projection-card main">';
    html += '<div class="projection-label">Projected ' + currentYear + ' Income</div>';
    html += '<div class="projection-value">' + formatCurrency(projectedFullYear.total) + '</div>';
    html += '<div class="projection-detail">';

    if (yoyChange !== 0) {
        var changeClass = yoyChange >= 0 ? 'positive' : 'negative';
        var changeSign = yoyChange >= 0 ? '+' : '';
        html += '<span class="' + changeClass + '">' + changeSign + yoyChange.toFixed(1) + '% vs ' + (currentYear - 1) + '</span>';
    }

    html += '</div>';
    html += '</div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Trailing 12 Months</div>';
    html += '<div class="projection-value">' + formatCurrency(trailing12m.total) + '</div>';
    html += '<div class="projection-detail">Based on actual payments</div>';
    html += '</div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">YTD Income (' + currentYear + ')</div>';
    html += '<div class="projection-value">' + formatCurrency(ytdIncome.total) + '</div>';
    html += '<div class="projection-detail">' + monthsElapsed + ' months elapsed</div>';
    html += '</div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Monthly Average</div>';
    html += '<div class="projection-value">' + formatCurrency(monthlyAvg) + '</div>';
    html += '<div class="projection-detail">Based on trailing 12m</div>';
    html += '</div>';

    html += '<div class="projection-card">';
    html += '<div class="projection-label">Portfolio Yield</div>';
    html += '<div class="projection-value">' + portfolioYield.toFixed(2) + '%</div>';
    html += '<div class="projection-detail">Annual income / portfolio value</div>';
    html += '</div>';

    html += '</div>';

    // Top dividend payers section
    if (topPayers.length > 0) {
        html += '<div class="top-payers">';
        html += '<h4>Top Income Sources (12m)</h4>';
        html += '<div class="payers-list">';

        topPayers.forEach(function (p) {
            var pct = (p.amount / trailing12m.total * 100).toFixed(1);
            // Color based on income type: green=dividend, blue=interest, purple=staking
            var color = p.type === 'dividend' ? '#4ade80' : p.type === 'interest' ? '#60a5fa' : '#a78bfa';
            html += '<div class="payer-item">';
            html += '<span class="payer-symbol" style="color: ' + color + ';">' + p.symbol + '</span>';
            html += '<span class="payer-amount">' + formatCurrency(p.amount) + '</span>';
            html += '<span class="payer-pct">(' + pct + '%)</span>';
            html += '</div>';
        }

        );
        html += '</div>';
        html += '</div>';
    }

    // Income breakdown
    html += '<div class="income-breakdown">';
    html += '<h4>Trailing 12 Month Breakdown</h4>';
    html += '<div class="breakdown-bars">';

    var categories = [{
        name: 'Dividends', value: trailing12m.dividends, color: '#4ade80'
    }

        ,
    {
        name: 'Interest', value: trailing12m.interest, color: '#60a5fa'
    }

        ,
    {
        name: 'Staking', value: trailing12m.staking, color: '#a78bfa'
    }

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
    }

    );

    html += '</div>';
    html += '</div>';

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

    var labels = data.map(function (r) {
        return (r.year || '').toString();
    }

    );

    var dividends = data.map(function (r) {
        return r.dividends || 0;
    }

    );

    var interest = data.map(function (r) {
        return r.interest || 0;
    }

    );

    var staking = data.map(function (r) {
        return r.staking || 0;
    }

    );

    new Chart(ctx, {

        type: 'bar',
        data: {

            labels: labels,
            datasets: [{
                label: 'Dividends',
                data: dividends,
                backgroundColor: '#4ade80',
                borderRadius: 4
            }

                ,
            {
                label: 'Interest',
                data: interest,
                backgroundColor: '#60a5fa',
                borderRadius: 4
            }

                ,
            {
                label: 'Staking',
                data: staking,
                backgroundColor: '#a78bfa',
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: '#888'
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {

                    stacked: true,
                    ticks: {
                        color: '#888'
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {

                    stacked: true,
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create historical chart with per-account lines (dollar values)
function createHistoricalChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('historicalChart');
    if (!ctx) return;

    var accounts = getHistoricalAccounts();

    var labels = data.map(function (h) {
        return h.date;
    }

    );

    // Create datasets - one for total and one for each account
    var datasets = [];

    // Total portfolio line (thicker, on top)
    datasets.push({

        label: 'Total Portfolio',
        data: data.map(function (h) {
            return h.totalValue;
        }

        ),
        borderColor: '#ffffff',
        backgroundColor: 'rgba(255, 255, 255, 0.05)',
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 3,
        order: 0
    }

    );

    // Per-account lines
    for (var i = 0; i < accounts.length; i++) {
        var account = accounts[i];
        var color = getAccountColor(account, i);

        datasets.push({

            label: account,
            data: data.map(function (h) {
                return h.accounts[account] || 0;
            }

            ),
            borderColor: color,
            backgroundColor: 'transparent',
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 3,
            borderWidth: 1.5,
            order: i + 1
        }

        );
    }

    new Chart(ctx, {

        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            }

            ,
            plugins: {
                legend: {

                    display: true,
                    position: 'top',
                    labels: {

                        color: '#888',
                        padding: 12,
                        font: {
                            size: 11
                        }

                        ,
                        usePointStyle: true,
                        pointStyle: 'line'
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 12
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {

                    stacked: false,
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create growth comparison chart (Portfolio TWR vs S&P 500 as percentage)
function createGrowthComparisonChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('growthComparisonChart');
    if (!ctx) return;

    var labels = data.map(function (h) {
        return h.date;
    }

    );

    // Get actual portfolio values
    var portfolioValues = data.map(function (h) {
        return h.totalValue;
    }

    );

    // Get "What If S&P 500" values - what portfolio would be worth if all invested in S&P 500
    var whatIfSP500 = data.map(function (h) {
        return h.whatIfSP500 !== undefined ? h.whatIfSP500 : null;
    }

    );

    // Check if What If S&P 500 data is available
    var hasWhatIfSP500 = whatIfSP500.some(function (v) {
        return v !== null && v > 0;
    }

    );

    var datasets = [];

    // Actual Portfolio Value line
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
    }

    );

    // "What If S&P 500" line
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
        }

        );
    }

    // Calculate the difference (alpha) for display
    var alpha = [];

    for (var i = 0; i < portfolioValues.length; i++) {
        var actual = portfolioValues[i] || 0;
        var whatIf = whatIfSP500[i] || 0;

        if (whatIf > 0) {
            alpha.push(actual - whatIf);
        }

        else {
            alpha.push(null);
        }
    }

    // Get latest alpha for the title
    var latestAlpha = null;

    for (var j = alpha.length - 1; j >= 0; j--) {
        if (alpha[j] !== null) {
            latestAlpha = alpha[j];
            break;
        }
    }

    new Chart(ctx, {

        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            }

            ,
            plugins: {
                legend: {

                    display: true,
                    position: 'top',
                    labels: {

                        color: '#888',
                        padding: 12,
                        font: {
                            size: 11
                        }

                        ,
                        usePointStyle: true,
                        pointStyle: 'line'
                    }
                }

                ,
                title: {

                    display: latestAlpha !== null,
                    text: latestAlpha !== null ? (latestAlpha >= 0 ? 'You beat the S&P 500 by ' + formatCurrency(latestAlpha) : 'S&P 500 would have beaten you by ' + formatCurrency(Math.abs(latestAlpha))) : '',
                    color: latestAlpha >= 0 ? '#00ff88' : '#ff6b6b',
                    font: {
                        size: 14, weight: 'bold'
                    }

                    ,
                    padding: {
                        bottom: 10
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }

                        ,
                        afterBody: function (tooltipItems) {
                            if (tooltipItems.length >= 2) {
                                var actual = tooltipItems[0].raw || 0;
                                var whatIf = tooltipItems[1].raw || 0;
                                var diff = actual - whatIf;
                                var sign = diff >= 0 ? '+' : '';
                                return 'Difference: ' + sign + formatCurrency(diff);
                            }

                            return '';
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 12
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create value vs cost basis chart with gain/loss overlay
function createValueVsInvestedChart() {
    var data = getHistoricalHoldings();
    if (data.length === 0) return;

    var ctx = document.getElementById('valueVsInvestedChart');
    if (!ctx) return;

    var labels = data.map(function (h) {
        return h.date;
    }

    );

    var portfolioValues = data.map(function (h) {
        return h.totalValue;
    }

    );

    // Get historical cost basis data
    var costBasisData = typeof portfolioCostBasisData !== 'undefined' ? portfolioCostBasisData : [];

    // Build a lookup map for cost basis by date
    var costBasisByDate = {}

        ;

    costBasisData.forEach(function (entry) {
        costBasisByDate[entry.Date] = entry.TotalCostBasis;
    }

    );

    // For each snapshot date, find the most recent cost basis value
    var historicalCostBasis = [];
    var totalGainLoss = [];
    var lastKnownCostBasis = 0;

    // Get all cost basis dates sorted
    var costBasisDates = costBasisData.map(function (e) {
        return e.Date;
    }

    ).sort();

    labels.forEach(function (snapshotDate, index) {
        // Find the latest cost basis date that is <= snapshotDate
        var applicableCostBasis = lastKnownCostBasis;

        for (var i = 0; i < costBasisDates.length; i++) {
            if (costBasisDates[i] <= snapshotDate) {
                applicableCostBasis = costBasisByDate[costBasisDates[i]];
                lastKnownCostBasis = applicableCostBasis;
            }

            else {
                break;
            }
        }

        historicalCostBasis.push(applicableCostBasis);

        // Calculate gain/loss (Value - Cost Basis)
        var value = portfolioValues[index] || 0;
        var gain = value - applicableCostBasis;
        totalGainLoss.push(gain);
    }

    );

    var datasets = [];

    // Portfolio Value line (left Y-axis)
    datasets.push({
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
    }

    );

    // Historical Cost Basis line (left Y-axis)
    datasets.push({
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
    }

    );

    // Total Gain/Loss line (same Y-axis as value)
    datasets.push({
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

    );

    new Chart(ctx, {

        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            }

            ,
            plugins: {
                legend: {

                    display: true,
                    position: 'top',
                    labels: {

                        color: '#888',
                        padding: 12,
                        font: {
                            size: 11
                        }

                        ,
                        usePointStyle: true,
                        pointStyle: 'line'
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var label = context.dataset.label || '';
                            var value = context.raw;

                            if (label === 'Total Gain/Loss') {
                                var sign = value >= 0 ? '+' : '';
                                return label + ': ' + sign + formatCurrency(value);
                            }

                            return label + ': ' + formatCurrency(value);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 12
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {

                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Dollars ($)',
                        color: '#888'
                    }

                    ,
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create holdings treemap visualization
function createHoldingsTreemap() {
    var container = document.getElementById('holdingsTreemap');
    if (!container) return;

    var holdings = getHoldingsDetail();
    if (holdings.length === 0) return;

    // Aggregate by symbol (combine across accounts)
    var bySymbol = {}

        ;

    holdings.forEach(function (h) {
        if (!bySymbol[h.symbol]) {
            bySymbol[h.symbol] = {
                symbol: h.symbol,
                value: 0,
                cost_basis: 0,
                gain: 0,
                gain_pct: 0,
                sector: h.sector || 'Other'
            }

                ;
        }

        bySymbol[h.symbol].value += h.current_value || 0;
        bySymbol[h.symbol].cost_basis += h.cost_basis || 0;
        bySymbol[h.symbol].gain += h.unrealized_gain || 0;
    }

    );

    // Calculate gain percentage and sort by value (descending)
    var symbols = Object.values(bySymbol).map(function (s) {
        s.gain_pct = s.cost_basis > 0 ? (s.gain / s.cost_basis * 100) : 0;
        return s;
    }

    ).filter(function (s) {
        return s.value > 0;
    }

    ).sort(function (a, b) {
        return b.value - a.value;
    }

    );

    // Take top 20 for readability
    var topSymbols = symbols.slice(0, 20);

    var totalValue = topSymbols.reduce(function (sum, s) {
        return sum + s.value;
    }

        , 0);

    // Use percentage-based layout with CSS flexbox
    // Group items into rows, each row's height is proportional to its value
    var rows = [];
    var currentRow = [];
    var currentRowValue = 0;
    var itemsPerRow = Math.ceil(Math.sqrt(topSymbols.length)); // Aim for roughly square grid

    topSymbols.forEach(function (s, idx) {
        currentRow.push(s);
        currentRowValue += s.value;

        // Start new row after itemsPerRow items, or adjust based on value
        if (currentRow.length >= itemsPerRow || idx === topSymbols.length - 1) {
            rows.push({
                items: currentRow, totalValue: currentRowValue
            }

            );
            currentRow = [];
            currentRowValue = 0;
        }
    }

    );

    // Build HTML using flexbox for proper sizing
    var html = '<div class="treemap-rows">';

    rows.forEach(function (row) {
        var rowHeightPct = (row.totalValue / totalValue * 100);
        html += '<div class="treemap-row" style="height: ' + rowHeightPct + '%;">';

        row.items.forEach(function (s) {
            var widthPct = (s.value / row.totalValue * 100);
            var pct = (s.value / totalValue * 100);

            // Color based on gain/loss
            var color;
            if (s.gain_pct >= 50) color = '#16a34a';
            else if (s.gain_pct >= 20) color = '#22c55e';
            else if (s.gain_pct >= 5) color = '#4ade80';
            else if (s.gain_pct >= 0) color = '#86efac';
            else if (s.gain_pct >= -5) color = '#fca5a5';
            else if (s.gain_pct >= -20) color = '#f87171';
            else color = '#dc2626';

            html += '<div class="treemap-cell" style="width: ' + widthPct + '%; background: ' + color + ';" ';
            html += 'title="' + s.symbol + '\nValue: ' + formatCurrency(s.value) + ' (' + pct.toFixed(1) + '%)\nGain/Loss: ' + formatCurrency(s.gain) + ' (' + s.gain_pct.toFixed(1) + '%)\nSector: ' + s.sector + '">';
            html += '<div class="treemap-label">' + s.symbol + '</div>';
            html += '<div class="treemap-value">' + formatCurrency(s.value) + '</div>';
            html += '<div class="treemap-pct">' + (s.gain_pct >= 0 ? '+' : '') + s.gain_pct.toFixed(1) + '%</div>';
            html += '</div>';
        }

        );

        html += '</div>';
    }

    );

    html += '</div>';

    // Add legend
    html += '<div class="treemap-legend">';
    html += '<span class="legend-item"><span class="legend-color" style="background: #dc2626;"></span> &lt;-20%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #f87171;"></span> -20% to -5%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #fca5a5;"></span> -5% to 0%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #86efac;"></span> 0% to 5%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #4ade80;"></span> 5% to 20%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #22c55e;"></span> 20% to 50%</span>';
    html += '<span class="legend-item"><span class="legend-color" style="background: #16a34a;"></span> &gt;50%</span>';
    html += '</div>';

    container.innerHTML = html;
}

// Create holdings heatmap visualization
function createHoldingsHeatmap() {
    var container = document.getElementById('holdingsHeatmap');
    if (!container) return;

    var holdings = getHoldingsDetail();
    if (holdings.length === 0) return;

    // Aggregate by symbol
    var bySymbol = {}

        ;

    holdings.forEach(function (h) {
        if (!bySymbol[h.symbol]) {
            bySymbol[h.symbol] = {
                symbol: h.symbol,
                value: 0,
                change_7d: h.change_7d || 0,
                change_30d: h.change_30d || 0,
                gain_pct: 0,
                cost_basis: 0,
                gain: 0
            }

                ;
        }

        bySymbol[h.symbol].value += h.current_value || 0;
        bySymbol[h.symbol].cost_basis += h.cost_basis || 0;
        bySymbol[h.symbol].gain += h.unrealized_gain || 0;
    }

    );

    // Calculate gain pct and sort by value
    var symbols = Object.values(bySymbol).map(function (s) {
        s.gain_pct = s.cost_basis > 0 ? (s.gain / s.cost_basis * 100) : 0;
        return s;
    }

    ).sort(function (a, b) {
        return b.value - a.value;
    }

    );

    // Take top 20 for the heatmap
    var topSymbols = symbols.slice(0, 20);

    // Build heatmap grid - 3 columns: 7D, 30D, Total Return
    var html = '<div class="heatmap-wrapper">';

    // Header row
    html += '<div class="heatmap-header">';
    html += '<div class="heatmap-header-cell symbol-header">Symbol</div>';
    html += '<div class="heatmap-header-cell">7D Change</div>';
    html += '<div class="heatmap-header-cell">30D Change</div>';
    html += '<div class="heatmap-header-cell">Total Return</div>';
    html += '</div>';

    topSymbols.forEach(function (s) {
        html += '<div class="heatmap-row">';
        html += '<div class="heatmap-symbol">' + s.symbol + '</div>';

        // 7D cell
        var color7d = getHeatmapColor(s.change_7d);
        html += '<div class="heatmap-cell" style="background: ' + color7d + ';">';
        html += (s.change_7d >= 0 ? '+' : '') + s.change_7d.toFixed(2) + '%';
        html += '</div>';

        // 30D cell
        var color30d = getHeatmapColor(s.change_30d);
        html += '<div class="heatmap-cell" style="background: ' + color30d + ';">';
        html += (s.change_30d >= 0 ? '+' : '') + s.change_30d.toFixed(2) + '%';
        html += '</div>';

        // Total return cell
        var colorTotal = getHeatmapColor(s.gain_pct, 50); // Wider scale for total return
        html += '<div class="heatmap-cell" style="background: ' + colorTotal + ';">';
        html += (s.gain_pct >= 0 ? '+' : '') + s.gain_pct.toFixed(1) + '%';
        html += '</div>';

        html += '</div>';
    }

    );

    html += '</div>';

    container.innerHTML = html;
}

// Helper function to get heatmap color based on percentage
function getHeatmapColor(pct, scale) {
    scale = scale || 10; // Default scale: +/- 10%

    // Clamp to scale
    var normalized = Math.max(-1, Math.min(1, pct / scale));

    if (normalized >= 0) {
        // Green gradient
        var intensity = normalized;
        var r = Math.round(134 - intensity * 112);
        var g = Math.round(239 - intensity * 74);
        var b = Math.round(172 - intensity * 128);
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }

    else {
        // Red gradient
        var intensity = -normalized;
        var r = Math.round(252 - intensity * 32);
        var g = Math.round(165 - intensity * 127);
        var b = Math.round(165 - intensity * 127);
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }
}

// Initialize dashboard
function initDashboard() {

    // Check if data is loaded
    if (typeof portfolioData === 'undefined') {
        document.getElementById('errorMessage').style.display = 'block';
        return;
    }

    // Show dashboard content
    document.getElementById('dashboardContent').style.display = 'block';

    // Update timestamp
    var timestamp = document.getElementById('timestamp');

    if (timestamp && portfolioData.GeneratedAt) {
        timestamp.textContent = 'Generated: ' + new Date(portfolioData.GeneratedAt).toLocaleString();
    }

    // Initialize tabs
    initTabs();

    // Create all sections
    createSummaryCards();
    createAllocationChart();
    createInvestmentCashChart();
    createReturnsChart();
    createTaxLotChart();
    createAssetAllocationChart();
    createSectorAllocationChart();
    createTopHoldingsTable();
    createAccountCards();
    createAllHoldingsTable();
    createHoldingsTreemap();
    createHoldingsHeatmap();
    initHoldingsSearch();
    createIncomeSection();
    createHistoricalChart();
    createGrowthComparisonChart();
    createValueVsInvestedChart();
    createMonthlyReturnsChart();
    createDividendCalendar();
    createTaxSection();
    createRetirementSection();
    createOptionsSection();
    createRecentActivity();
    createTopMovers();
    createCryptoSection();
    createTransactionsSection();
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
    createRetirementProjectionChart(data);
    createSalaryInfo(data);
    createContributionLimits(data);
    createSalaryHistoryChart(data);
    createRetirementAccountsChart(data);
    createBudgetBreakdown(data);
    createBonusHistoryTable(data);
    createSalaryHistoryTable(data);
}

// Create FIRE Tracker
function createFireTracker(data) {
    var container = document.getElementById('fireTracker');
    if (!container) return;

    // Get age info from data
    var personal = data.personal || {};
    var currentAge = personal.current_age || 35;

    // Get current portfolio value
    var currentValue = portfolioData.TotalValue || 0;
    var salary = (data.salary && data.salary.current) || 0;
    var avgBonus = (data.bonuses && data.bonuses.avg_annual) || 0;
    var totalIncome = salary + avgBonus;

    // Calculate annual expenses (rough estimate: income - savings)
    // Use contributions as a proxy for savings
    var contributions = data.contributions || {};
    var annualSavings = (contributions.traditional_401k || 0) +
        (contributions.roth_ira || 0) +
        (data.employer_match || 0);

    // Estimate annual expenses (take-home minus what's going to investments)
    // This is approximate - ideally user would input actual expenses
    var estimatedTakeHome = salary * 0.72; // After ~28% taxes
    var estimatedExpenses = Math.max(estimatedTakeHome - annualSavings, salary * 0.4);

    // FIRE number = 25x annual expenses (4% rule)
    var fireNumber = estimatedExpenses * 25;

    // Progress percentage
    var fireProgress = (currentValue / fireNumber) * 100;

    // Years to FIRE calculation
    var annualReturn = 0.07; // 7% assumed return
    var yearsToFire = 0;
    if (annualSavings > 0 && currentValue < fireNumber) {
        // Use formula: FV = PV(1+r)^n + PMT*((1+r)^n - 1)/r
        // Solve for n when FV = fireNumber
        var projectedValue = currentValue;
        while (projectedValue < fireNumber && yearsToFire < 100) {
            projectedValue = projectedValue * (1 + annualReturn) + annualSavings;
            yearsToFire++;
        }
    }

    // Calculate FIRE age
    var fireAge = currentAge + yearsToFire;

    // Milestones
    var milestones = [
        { label: '$100K', value: 100000 },
        { label: '$250K', value: 250000 },
        { label: '$500K', value: 500000 },
        { label: '$750K', value: 750000 },
        { label: '$1M', value: 1000000 },
        { label: 'FIRE', value: fireNumber }
    ];

    var html = '';

    // Header with main stats
    html += '<div class="fire-header">';
    html += '<div class="fire-main-stat">';
    html += '<div class="fire-label">Current Net Worth</div>';
    html += '<div class="fire-value">' + formatCurrency(currentValue) + '</div>';
    html += '<div class="fire-subvalue">' + fireProgress.toFixed(1) + '% to FIRE</div>';
    html += '</div>';
    html += '<div class="fire-target">';
    html += '<div class="fire-label">FIRE Target (25x Expenses)</div>';
    html += '<div class="fire-value">' + formatCurrency(fireNumber) + '</div>';
    html += '</div>';
    html += '</div>';

    // Progress bar
    html += '<div class="fire-progress-container">';
    html += '<div class="fire-progress-bar">';
    html += '<div class="fire-progress-fill" style="width: ' + Math.min(fireProgress, 100) + '%;">';
    if (fireProgress >= 10) {
        html += '<span class="fire-progress-text">' + fireProgress.toFixed(1) + '%</span>';
    }
    html += '</div>';
    html += '</div>';

    // Milestones
    html += '<div class="fire-milestones">';
    milestones.forEach(function (m) {
        var reached = currentValue >= m.value;
        html += '<div class="fire-milestone ' + (reached ? 'reached' : '') + '">';
        html += '<div class="milestone-marker"></div>';
        html += '<div class="milestone-label">' + m.label + '</div>';
        html += '</div>';
    });
    html += '</div>';
    html += '</div>';

    // Stats row
    html += '<div class="fire-stats">';
    html += '<div class="fire-stat-item">';
    html += '<div class="fire-stat-label">Est. Annual Expenses</div>';
    html += '<div class="fire-stat-value">' + formatCurrency(estimatedExpenses) + '</div>';
    html += '</div>';
    html += '<div class="fire-stat-item">';
    html += '<div class="fire-stat-label">Years to FIRE</div>';
    html += '<div class="fire-stat-value positive">' + (yearsToFire > 0 ? '~' + yearsToFire + ' years' : 'Achieved!') + '</div>';
    html += '</div>';
    html += '<div class="fire-stat-item">';
    html += '<div class="fire-stat-label">FIRE Age (currently ' + currentAge + ')</div>';
    html += '<div class="fire-stat-value">' + (yearsToFire > 0 ? fireAge : 'Now!') + '</div>';
    html += '</div>';
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

    // Get contribution data
    var contributions = data.contributions || {};
    var contrib401k = contributions.traditional_401k || 0;
    var contribRothIRA = contributions.roth_ira || 0;
    var employerMatch = data.employer_match || 0;

    // Annualize contributions (assume we're partway through the year)
    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;

    var annualized401k = yearProgress > 0 ? contrib401k / yearProgress : contrib401k;
    var annualizedRothIRA = yearProgress > 0 ? contribRothIRA / yearProgress : contribRothIRA;
    var totalAnnualizedSavings = annualized401k + annualizedRothIRA + employerMatch;

    // Savings rate calculation
    var savingsRate = totalGrossIncome > 0 ? (totalAnnualizedSavings / totalGrossIncome) * 100 : 0;

    // Rate without employer match
    var personalSavingsRate = totalGrossIncome > 0 ? ((annualized401k + annualizedRothIRA) / totalGrossIncome) * 100 : 0;

    var html = '';

    // Big savings rate number
    var rateColor = savingsRate >= 20 ? 'var(--accent-green)' : savingsRate >= 15 ? '#fbbf24' : 'var(--accent-red)';
    html += '<div class="savings-rate-value" style="color: ' + rateColor + ';">' + savingsRate.toFixed(1) + '%</div>';
    html += '<div class="savings-rate-label">Total Savings Rate (incl. employer match)</div>';

    // Breakdown
    html += '<div class="savings-rate-breakdown">';
    html += '<div class="savings-rate-row">';
    html += '<span class="savings-rate-row-label">401(k) Contributions</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(annualized401k) + '/yr</span>';
    html += '</div>';
    html += '<div class="savings-rate-row">';
    html += '<span class="savings-rate-row-label">Roth IRA Contributions</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(annualizedRothIRA) + '/yr</span>';
    html += '</div>';
    html += '<div class="savings-rate-row">';
    html += '<span class="savings-rate-row-label">Employer Match</span>';
    html += '<span class="savings-rate-row-value">' + formatCurrency(employerMatch) + '/yr</span>';
    html += '</div>';
    html += '<div class="savings-rate-row" style="border-top: 1px solid var(--border-color); padding-top: 8px; margin-top: 8px;">';
    html += '<span class="savings-rate-row-label"><strong>Total Annual Savings</strong></span>';
    html += '<span class="savings-rate-row-value"><strong>' + formatCurrency(totalAnnualizedSavings) + '</strong></span>';
    html += '</div>';
    html += '</div>';

    // Benchmarks
    html += '<div class="savings-benchmark">';
    html += '<div class="benchmark-item">';
    html += '<div class="benchmark-value">10%</div>';
    html += '<div class="benchmark-label">Minimum</div>';
    html += '</div>';
    html += '<div class="benchmark-item">';
    html += '<div class="benchmark-value">15%</div>';
    html += '<div class="benchmark-label">Recommended</div>';
    html += '</div>';
    html += '<div class="benchmark-item">';
    html += '<div class="benchmark-value current">' + savingsRate.toFixed(0) + '%</div>';
    html += '<div class="benchmark-label">Your Rate</div>';
    html += '</div>';
    html += '<div class="benchmark-item">';
    html += '<div class="benchmark-value">20%+</div>';
    html += '<div class="benchmark-label">Aggressive</div>';
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Retirement Projections display
function createRetirementProjections(data) {
    var container = document.getElementById('retirementProjections');
    if (!container) return;

    var currentValue = portfolioData.TotalValue || 0;
    var contributions = data.contributions || {};
    var annualContributions = (contributions.traditional_401k || 0) +
        (contributions.roth_ira || 0) +
        (data.employer_match || 0);

    // Annualize
    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;
    annualContributions = yearProgress > 0 ? annualContributions / yearProgress : annualContributions;

    // Get age info from data
    var personal = data.personal || {};
    var currentAge = personal.current_age || 35;
    var retirementAge = personal.retirement_age || 65;
    var yearsToRetirement = personal.years_to_retirement || (retirementAge - currentAge);

    // Calculate projections at different rates
    var scenarios = [
        { name: 'Conservative', rate: 0.05, color: 'conservative' },
        { name: 'Moderate', rate: 0.07, color: 'moderate' },
        { name: 'Aggressive', rate: 0.09, color: 'aggressive' }
    ];

    var maxProjection = 0;
    scenarios.forEach(function (s) {
        // FV = PV(1+r)^n + PMT*((1+r)^n - 1)/r
        var growthFactor = Math.pow(1 + s.rate, yearsToRetirement);
        s.projection = currentValue * growthFactor +
            annualContributions * (growthFactor - 1) / s.rate;
        maxProjection = Math.max(maxProjection, s.projection);
    });

    var html = '';
    html += '<div class="projection-scenarios">';

    scenarios.forEach(function (s) {
        var barWidth = (s.projection / maxProjection) * 100;
        html += '<div class="projection-scenario">';
        html += '<div class="scenario-label">' + s.name + ' (' + Math.round(s.rate * 100) + '%)</div>';
        html += '<div class="scenario-bar-container">';
        html += '<div class="scenario-bar ' + s.color + '" style="width: ' + barWidth + '%;"></div>';
        html += '</div>';
        html += '<div class="scenario-value">' + formatCompactCurrency(s.projection) + '</div>';
        html += '</div>';
    });

    html += '</div>';

    // Assumptions
    html += '<div class="projection-assumptions">';
    html += '<p><strong>Assumptions:</strong></p>';
    html += '<p>• Current portfolio: ' + formatCurrency(currentValue) + '</p>';
    html += '<p>• Annual contributions: ' + formatCurrency(annualContributions) + '</p>';
    html += '<p>• Current age: ' + currentAge + ' → Retire at ' + retirementAge + ' (' + yearsToRetirement + ' years)</p>';
    html += '</div>';

    container.innerHTML = html;
}

// Format compact currency (1.2M, 500K, etc.)
function formatCompactCurrency(value) {
    if (value >= 1000000) {
        return '$' + (value / 1000000).toFixed(1) + 'M';
    } else if (value >= 1000) {
        return '$' + (value / 1000).toFixed(0) + 'K';
    }
    return formatCurrency(value);
}

// Create Retirement Projection Chart
function createRetirementProjectionChart(data) {
    var ctx = document.getElementById('retirementProjectionChart');
    if (!ctx) return;

    // Get age info from data
    var personal = data.personal || {};
    var currentAge = personal.current_age || 35;
    var yearsToRetirement = personal.years_to_retirement || 30;

    var currentValue = portfolioData.TotalValue || 0;
    var contributions = data.contributions || {};
    var annualContributions = (contributions.traditional_401k || 0) +
        (contributions.roth_ira || 0) +
        (data.employer_match || 0);

    // Annualize
    var now = new Date();
    var dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / (1000 * 60 * 60 * 24));
    var yearProgress = dayOfYear / 365;
    annualContributions = yearProgress > 0 ? annualContributions / yearProgress : annualContributions;

    var currentYear = now.getFullYear();
    var yearsToProject = yearsToRetirement;

    // Generate data for each scenario (every 5 years, plus final year)
    var labels = [];
    var conservativeData = [];
    var moderateData = [];
    var aggressiveData = [];

    for (var i = 0; i <= yearsToProject; i += 5) {
        labels.push(currentYear + i + ' (age ' + (currentAge + i) + ')');

        var conservativeGrowth = Math.pow(1.05, i);
        var moderateGrowth = Math.pow(1.07, i);
        var aggressiveGrowth = Math.pow(1.09, i);

        conservativeData.push(
            currentValue * conservativeGrowth +
            (i > 0 ? annualContributions * (conservativeGrowth - 1) / 0.05 : 0)
        );
        moderateData.push(
            currentValue * moderateGrowth +
            (i > 0 ? annualContributions * (moderateGrowth - 1) / 0.07 : 0)
        );
        aggressiveData.push(
            currentValue * aggressiveGrowth +
            (i > 0 ? annualContributions * (aggressiveGrowth - 1) / 0.09 : 0)
        );
    }

    // Add final retirement year if not already included
    if (yearsToProject % 5 !== 0) {
        labels.push(currentYear + yearsToProject + ' (age 65)');

        var conservativeGrowth = Math.pow(1.05, yearsToProject);
        var moderateGrowth = Math.pow(1.07, yearsToProject);
        var aggressiveGrowth = Math.pow(1.09, yearsToProject);

        conservativeData.push(
            currentValue * conservativeGrowth +
            annualContributions * (conservativeGrowth - 1) / 0.05
        );
        moderateData.push(
            currentValue * moderateGrowth +
            annualContributions * (moderateGrowth - 1) / 0.07
        );
        aggressiveData.push(
            currentValue * aggressiveGrowth +
            annualContributions * (aggressiveGrowth - 1) / 0.09
        );
    }

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Conservative (5%)',
                    data: conservativeData,
                    borderColor: '#60a5fa',
                    backgroundColor: 'rgba(96, 165, 250, 0.1)',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Moderate (7%)',
                    data: moderateData,
                    borderColor: '#a78bfa',
                    backgroundColor: 'rgba(167, 139, 250, 0.1)',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Aggressive (9%)',
                    data: aggressiveData,
                    borderColor: '#4ade80',
                    backgroundColor: 'rgba(74, 222, 128, 0.1)',
                    fill: true,
                    tension: 0.3
                }
            ]
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
                            return formatCompactCurrency(val);
                        }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
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

    // Current salary
    html += '<div class="stat-row">';
    html += '<span class="stat-label">Current Salary</span>';
    html += '<span class="stat-value highlight">' + formatCurrency(salary.current || 0) + '/yr</span>';
    html += '</div>';

    // Monthly gross
    var monthlyGross = (salary.current || 0) / 12;
    html += '<div class="stat-row">';
    html += '<span class="stat-label">Monthly Gross</span>';
    html += '<span class="stat-value">' + formatCurrency(monthlyGross) + '</span>';
    html += '</div>';

    // YTD Bonuses
    html += '<div class="stat-row">';
    html += '<span class="stat-label">YTD Bonuses</span>';
    html += '<span class="stat-value">' + formatCurrency(bonuses.ytd || 0) + '</span>';
    html += '</div>';

    // Average Annual Bonus
    html += '<div class="stat-row">';
    html += '<span class="stat-label">Avg Annual Bonus</span>';
    html += '<span class="stat-value">' + formatCurrency(bonuses.avg_annual || 0) + '</span>';
    html += '</div>';

    // Total Compensation Estimate
    var totalComp = (salary.current || 0) + (bonuses.avg_annual || 0);
    html += '<div class="stat-row">';
    html += '<span class="stat-label">Est. Total Comp</span>';
    html += '<span class="stat-value highlight">' + formatCurrency(totalComp) + '</span>';
    html += '</div>';

    container.innerHTML = html;
}

// Create contribution limits and progress
function createContributionLimits(data) {
    var container = document.getElementById('contributionLimits');
    if (!container) return;

    var limits = data.limits || {};
    var contributions = data.contributions || {};

    var items = [
        {
            name: '401(k) (Pre-tax + Roth)',
            limit: limits.total_401k || 23500,
            contributed: (contributions.traditional_401k || 0) + (contributions.roth_401k || 0),
        },
        {
            name: 'Traditional IRA',
            limit: limits.traditional_ira || 7000,
            contributed: contributions.traditional_ira || 0,
        },
        {
            name: 'Roth IRA',
            limit: limits.roth_ira || 7000,
            contributed: contributions.roth_ira || 0,
        }
    ];

    var html = '';

    items.forEach(function (item) {
        var pct = item.limit > 0 ? Math.min((item.contributed / item.limit) * 100, 100) : 0;
        var colorClass = 'low';
        if (pct >= 100) colorClass = 'high';
        else if (pct >= 75) colorClass = 'medium';
        else if (pct >= 50) colorClass = 'medium';

        html += '<div class="contribution-limit-item">';
        html += '<div class="contribution-limit-header">';
        html += '<span class="contribution-limit-name">' + item.name + '</span>';
        html += '<span class="contribution-limit-amount">' + formatCurrency(item.contributed) + ' / ' + formatCurrency(item.limit) + '</span>';
        html += '</div>';
        html += '<div class="contribution-progress-bar">';
        html += '<div class="contribution-progress-fill ' + colorClass + '" style="width: ' + Math.max(pct, 5) + '%;">';
        html += '<span class="contribution-progress-text">' + pct.toFixed(0) + '%</span>';
        html += '</div>';
        html += '</div>';
        html += '</div>';
    });

    // Add employer match estimate
    if (data.employer_match) {
        html += '<div class="contribution-limit-item" style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border-color);">';
        html += '<div class="contribution-limit-header">';
        html += '<span class="contribution-limit-name">Est. Employer Match</span>';
        html += '<span class="contribution-limit-amount" style="color: var(--accent-green);">+' + formatCurrency(data.employer_match) + '/yr</span>';
        html += '</div>';
        html += '</div>';
    }

    container.innerHTML = html;
}

// Create salary history chart
function createSalaryHistoryChart(data) {
    var ctx = document.getElementById('salaryHistoryChart');
    if (!ctx) return;

    var salaryHistory = (data.salary && data.salary.history) || [];
    if (salaryHistory.length === 0) return;

    var labels = salaryHistory.map(function (s) {
        return s.date.substring(0, 7); // YYYY-MM
    });

    var values = salaryHistory.map(function (s) {
        return s.amount;
    });

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
                        label: function (context) {
                            return formatCurrency(context.raw);
                        },
                        afterLabel: function (context) {
                            var idx = context.dataIndex;
                            if (salaryHistory[idx] && salaryHistory[idx].note) {
                                return salaryHistory[idx].note;
                            }
                            return '';
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
                            return '$' + (val / 1000) + 'k';
                        }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
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
        // Use holdings detail if retirement data doesn't have accounts
        var holdings = getHoldingsDetail();
        var retirementKeywords = ['401k', '401K', 'IRA', 'ira', 'Rollover', 'Roth'];
        var retirementAccounts = {};

        holdings.forEach(function (h) {
            var isRetirement = retirementKeywords.some(function (kw) {
                return h.account.indexOf(kw) !== -1;
            });
            if (isRetirement) {
                if (!retirementAccounts[h.account]) {
                    retirementAccounts[h.account] = 0;
                }
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
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#888', font: { size: 11 }, padding: 10 }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var val = context.raw;
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            var pct = ((val / total) * 100).toFixed(1);
                            return context.label + ': ' + formatCurrency(val) + ' (' + pct + '%)';
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

    // Monthly Gross
    html += '<div class="budget-item">';
    html += '<span class="budget-label">Monthly Gross</span>';
    html += '<span class="budget-value income">' + formatCurrency(budget.monthly_gross) + '</span>';
    html += '</div>';

    // Estimated Taxes
    html += '<div class="budget-item">';
    html += '<span class="budget-label">Est. Taxes (~' + budget.effective_tax_rate + '%)</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_estimated_taxes) + '</span>';
    html += '</div>';

    // Max 401k Contribution
    html += '<div class="budget-item">';
    html += '<span class="budget-label">Max 401(k) Contribution</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_max_401k) + '</span>';
    html += '</div>';

    // Max IRA Contribution  
    html += '<div class="budget-item">';
    html += '<span class="budget-label">Max IRA Contribution</span>';
    html += '<span class="budget-value expense">-' + formatCurrency(budget.monthly_max_ira) + '</span>';
    html += '</div>';

    // Remaining
    var remaining = budget.monthly_net - budget.monthly_max_401k - budget.monthly_max_ira;
    html += '<div class="budget-item total">';
    html += '<span class="budget-label">Est. Take-Home (after max savings)</span>';
    html += '<span class="budget-value">' + formatCurrency(remaining) + '</span>';
    html += '</div>';

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

    // Sort by date descending
    bonuses = bonuses.slice().sort(function (a, b) {
        return new Date(b.date) - new Date(a.date);
    });

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

    // Sort by date descending
    salaries = salaries.slice().sort(function (a, b) {
        return new Date(b.date) - new Date(a.date);
    });

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

// Create monthly/yearly returns chart
function createMonthlyReturnsChart() {
    var data = getHistoricalHoldings();
    if (data.length < 2) return;

    var ctx = document.getElementById('monthlyReturnsChart');
    if (!ctx) return;

    // Calculate monthly returns
    var monthlyReturns = {}

        ;

    for (var i = 1; i < data.length; i++) {
        var curr = data[i];
        var prev = data[i - 1];

        // Extract year-month
        var yearMonth = curr.date.substring(0, 7); // YYYY-MM
        var currVal = curr.totalValue || 0;
        var prevVal = prev.totalValue || 0;

        if (prevVal > 0) {
            var monthReturn = ((currVal - prevVal) / prevVal) * 100;

            // Store the last return for each month (end of month value)
            monthlyReturns[yearMonth] = {
                return: monthReturn,
                startVal: prevVal,
                endVal: currVal
            }

                ;
        }
    }

    // Convert to arrays and take last 24 months
    var months = Object.keys(monthlyReturns).sort().slice(-24);

    var returns = months.map(function (m) {
        return monthlyReturns[m].return;
    }

    );

    // Color bars based on positive/negative
    var colors = returns.map(function (r) {
        return r >= 0 ? '#4ade80' : '#f87171';
    }

    );

    new Chart(ctx, {

        type: 'bar',
        data: {
            labels: months.map(function (m) {
                var parts = m.split('-');
                var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                return monthNames[parseInt(parts[1]) - 1] + ' ' + parts[0].slice(2);
            }

            ),
            datasets: [{
                label: 'Monthly Return %',
                data: returns,
                backgroundColor: colors,
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var val = context.raw;
                            return (val >= 0 ? '+' : '') + val.toFixed(2) + '%';
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888', maxRotation: 45
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return val + '%';
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create dividend calendar heatmap
function createDividendCalendar() {
    var transactions = getMasterTransactions();
    if (transactions.length === 0) return;

    var ctx = document.getElementById('dividendCalendarChart');
    if (!ctx) return;

    // Filter to dividends, interest, and staking income
    var incomeTypes = ['Dividend',
        'Interest',
        'Staking'];

    var income = transactions.filter(function (t) {
        return incomeTypes.indexOf(t.action) !== -1 && t.amount > 0;
    }

    );

    if (income.length === 0) return;

    // Group by month
    var monthlyIncome = {}

        ;

    income.forEach(function (t) {
        var yearMonth = t.date.substring(0, 7); // YYYY-MM

        if (!monthlyIncome[yearMonth]) {
            monthlyIncome[yearMonth] = {
                dividends: 0, interest: 0, staking: 0
            }

                ;
        }

        if (t.action === 'Dividend') {
            monthlyIncome[yearMonth].dividends += t.amount;
        }

        else if (t.action === 'Interest') {
            monthlyIncome[yearMonth].interest += t.amount;
        }

        else if (t.action === 'Staking') {
            monthlyIncome[yearMonth].staking += t.amount;
        }
    }

    );

    // Get last 24 months
    var months = Object.keys(monthlyIncome).sort().slice(-24);

    var dividends = months.map(function (m) {
        return monthlyIncome[m].dividends;
    }

    );

    var interest = months.map(function (m) {
        return monthlyIncome[m].interest;
    }

    );

    var staking = months.map(function (m) {
        return monthlyIncome[m].staking;
    }

    );

    var labels = months.map(function (m) {
        var parts = m.split('-');
        var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return monthNames[parseInt(parts[1]) - 1] + ' ' + parts[0].slice(2);
    }

    );

    new Chart(ctx, {

        type: 'bar',
        data: {

            labels: labels,
            datasets: [{
                label: 'Dividends',
                data: dividends,
                backgroundColor: '#4ade80',
                borderRadius: 4
            }

                ,
            {
                label: 'Interest',
                data: interest,
                backgroundColor: '#60a5fa',
                borderRadius: 4
            }

                ,
            {
                label: 'Staking',
                data: staking,
                backgroundColor: '#f59e0b',
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {

                    display: true,
                    position: 'top',
                    labels: {
                        color: '#888'
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {

                    stacked: true,
                    ticks: {
                        color: '#888', maxRotation: 45
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {

                    stacked: true,
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// Create tax section with loss harvesting opportunities
function createTaxSection() {
    var holdings = getHoldingsDetail();
    var realizedGains = typeof realizedGainsData !== 'undefined' ? realizedGainsData : [];

    // Tax Loss Harvesting Table
    createTaxLossHarvestingTable(holdings);

    // Realized Gains Table
    createRealizedGainsTable(realizedGains);

    // Short-term vs Long-term breakdown charts
    createGainsBreakdownChart(holdings);
    createHoldingPeriodChart(holdings);
}

function createTaxLossHarvestingTable(holdings) {
    var tbody = document.getElementById('taxLossBody');
    if (!tbody) return;

    // Filter to holdings with unrealized losses
    var losses = holdings.filter(function (h) {
        return h.unrealized_gain < -10; // At least $10 loss
    }

    ).sort(function (a, b) {
        return a.unrealized_gain - b.unrealized_gain; // Most negative first
    }

    );

    if (losses.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #888;">No tax loss harvesting opportunities (no positions with losses > $10)</td></tr>';
        return;
    }

    var html = '';
    var totalLoss = 0;
    var today = new Date();

    losses.forEach(function (h) {
        totalLoss += h.unrealized_gain;

        // Calculate holding period
        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        var holdingDays = buyDate ? Math.floor((today - buyDate) / (1000 * 60 * 60 * 24)) : 0;
        var holdingPeriod = holdingDays > 365 ? 'Long-term' : 'Short-term';

        // Calculate wash sale end date (30 days after today if sold)
        var washSaleEnd = new Date(today);
        washSaleEnd.setDate(washSaleEnd.getDate() + 30);
        var washSaleStr = washSaleEnd.toLocaleDateString();

        html += '<tr>';
        html += '<td><span class="symbol-badge">' + h.symbol + '</span></td>';
        html += '<td>' + h.account + '</td>';
        html += '<td class="number">' + formatNumber(h.quantity, 4) + '</td>';
        html += '<td class="number">' + formatCurrency(h.cost_basis) + '</td>';
        html += '<td class="number">' + formatCurrency(h.current_value) + '</td>';
        html += '<td class="number negative">' + formatCurrency(h.unrealized_gain) + '</td>';
        html += '<td>' + holdingPeriod + '</td>';
        html += '<td>' + washSaleStr + '</td>';
        html += '</tr>';
    }

    );

    // Add total row
    html += '<tr class="total-row" style="background: rgba(248, 113, 113, 0.1); font-weight: bold;">';
    html += '<td colspan="5"><strong>Total Harvestable Losses</strong></td>';
    html += '<td class="number negative"><strong>' + formatCurrency(totalLoss) + '</strong></td>';
    html += '<td colspan="2"></td>';
    html += '</tr>';

    tbody.innerHTML = html;
}

function createRealizedGainsTable(realizedGains) {
    var tbody = document.getElementById('realizedGainsBody');
    if (!tbody) return;

    // Filter to current year
    var currentYear = new Date().getFullYear().toString();

    var thisYearGains = realizedGains.filter(function (g) {
        return g.Date && g.Date.startsWith(currentYear);
    }

    ).sort(function (a, b) {
        return new Date(b.Date) - new Date(a.Date); // Most recent first
    }

    );

    if (thisYearGains.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #888;">No realized gains/losses this year</td></tr>';
        return;
    }

    var html = '';
    var totalGain = 0;

    thisYearGains.forEach(function (g) {
        var gain = g.RealizedGain || 0;
        totalGain += gain;
        var gainClass = gain >= 0 ? 'positive' : 'negative';
        var term = g.HoldingPeriod === 'Long-term' ? 'Long' : 'Short';

        html += '<tr>';
        html += '<td><span class="symbol-badge">' + g.Symbol + '</span></td>';
        html += '<td>' + g.Date + '</td>';
        html += '<td class="number">' + formatNumber(Math.abs(g.Quantity || 0), 4) + '</td>';
        html += '<td class="number">' + formatCurrency(g.Proceeds || 0) + '</td>';
        html += '<td class="number">' + formatCurrency(g.CostBasis || 0) + '</td>';
        html += '<td class="number ' + gainClass + '">' + formatCurrency(gain) + '</td>';
        html += '<td>' + term + '</td>';
        html += '</tr>';
    }

    );

    // Add total row
    var totalClass = totalGain >= 0 ? 'positive' : 'negative';
    html += '<tr class="total-row" style="background: rgba(255, 255, 255, 0.05); font-weight: bold;">';
    html += '<td colspan="5"><strong>Total ' + currentYear + ' Realized Gains/Losses</strong></td>';
    html += '<td class="number ' + totalClass + '"><strong>' + formatCurrency(totalGain) + '</strong></td>';
    html += '<td></td>';
    html += '</tr>';

    tbody.innerHTML = html;
}

function createGainsBreakdownChart(holdings) {
    var ctx = document.getElementById('gainsBreakdownChart');
    if (!ctx) return;

    var today = new Date();
    var shortTermGain = 0;
    var shortTermLoss = 0;
    var longTermGain = 0;
    var longTermLoss = 0;

    holdings.forEach(function (h) {
        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        var isLongTerm = buyDate && (today - buyDate) / (1000 * 60 * 60 * 24) > 365;
        var gain = h.unrealized_gain || 0;

        if (isLongTerm) {
            if (gain >= 0) longTermGain += gain;
            else longTermLoss += Math.abs(gain);
        }

        else {
            if (gain >= 0) shortTermGain += gain;
            else shortTermLoss += Math.abs(gain);
        }
    }

    );

    new Chart(ctx, {

        type: 'bar',
        data: {

            labels: ['Short-Term', 'Long-Term'],
            datasets: [{
                label: 'Gains',
                data: [shortTermGain, longTermGain],
                backgroundColor: '#4ade80',
                borderRadius: 4
            }

                ,
            {
                label: 'Losses',
                data: [-shortTermLoss, -longTermLoss],
                backgroundColor: '#f87171',
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {

                    display: true,
                    position: 'top',
                    labels: {
                        color: '#888'
                    }
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(Math.abs(context.raw));
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888'
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

function createHoldingPeriodChart(holdings) {
    var ctx = document.getElementById('holdingPeriodChart');
    if (!ctx) return;

    var today = new Date();

    var periods = {
        '< 1 month': {
            value: 0, gain: 0
        }

        ,
        '1-3 months': {
            value: 0, gain: 0
        }

        ,
        '3-6 months': {
            value: 0, gain: 0
        }

        ,
        '6-12 months': {
            value: 0, gain: 0
        }

        ,
        '1-2 years': {
            value: 0, gain: 0
        }

        ,
        '2+ years': {
            value: 0, gain: 0
        }
    }

        ;

    holdings.forEach(function (h) {
        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        if (!buyDate) return;

        var days = (today - buyDate) / (1000 * 60 * 60 * 24);
        var key;

        if (days < 30) key = '< 1 month';
        else if (days < 90) key = '1-3 months';
        else if (days < 180) key = '3-6 months';
        else if (days < 365) key = '6-12 months';
        else if (days < 730) key = '1-2 years';
        else key = '2+ years';

        periods[key].value += h.current_value || 0;
        periods[key].gain += h.unrealized_gain || 0;
    }

    );

    var labels = Object.keys(periods);

    var gains = labels.map(function (k) {
        return periods[k].gain;
    }

    );

    var colors = gains.map(function (g) {
        return g >= 0 ? '#4ade80' : '#f87171';
    }

    );

    new Chart(ctx, {

        type: 'bar',
        data: {

            labels: labels,
            datasets: [{
                label: 'Unrealized Gain/Loss',
                data: gains,
                backgroundColor: colors,
                borderRadius: 4
            }

            ]
        }

        ,
        options: {

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }

                ,
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var val = context.raw;
                            return (val >= 0 ? 'Gain: +' : 'Loss: ') + formatCurrency(Math.abs(val));
                        }

                        ,
                        afterLabel: function (context) {
                            var key = context.label;
                            return 'Value: ' + formatCurrency(periods[key].value);
                        }
                    }
                }
            }

            ,
            scales: {
                x: {
                    ticks: {
                        color: '#888', maxRotation: 45
                    }

                    ,
                    grid: {
                        display: false
                    }
                }

                ,
                y: {
                    ticks: {

                        color: '#888',
                        callback: function (val) {
                            return formatCurrency(val);
                        }
                    }

                    ,
                    grid: {
                        color: 'rgba(255,255,255,0.05)'
                    }
                }
            }
        }
    }

    );
}

// =============================================================================
// OPTIONS SECTION
// =============================================================================

// Parse option contract details from note field (e.g., "NVDA 2/7/2025 Call $123.00")
function parseOptionContract(note) {
    if (!note) return null;

    // Pattern: SYMBOL MM/DD/YYYY Type $Strike or SYMBOL MM/DD/YYYY Type $X,XXX.XX
    var match = note.match(/^([A-Z]+)\s+(\d{1,2}\/\d{1,2}\/\d{4})\s+(Call|Put)\s+\$?([\d,]+\.?\d*)/i);
    if (!match) return null;

    return {
        symbol: match[1],
        expiration: match[2],
        type: match[3].toLowerCase(),
        strike: parseFloat(match[4].replace(',', ''))
    };
}

// Get all options transactions from master transactions
function getOptionsTransactions() {
    if (typeof masterTransactions === 'undefined') return [];

    return masterTransactions.filter(function (t) {
        return t.action === 'OptionBuy' || t.action === 'OptionSell';
    }).map(function (t) {
        var contract = parseOptionContract(t.note);
        return {
            date: t.date,
            account: t.account,
            symbol: t.symbol,
            action: t.action,
            quantity: t.quantity,
            price: t.price,
            amount: t.amount,
            note: t.note,
            contract: contract
        };
    });
}

// Match buy/sell transactions to create trades
function matchOptionsTrades(transactions) {
    var trades = [];
    var openPositions = {}; // Key: contractId, Value: { buys: [], quantity: 0 }

    // Sort by date
    var sorted = transactions.slice().sort(function (a, b) {
        return new Date(a.date) - new Date(b.date);
    });

    sorted.forEach(function (t) {
        if (!t.contract) return;

        var contractId = t.symbol + '_' + t.contract.expiration + '_' +
            t.contract.type + '_' + t.contract.strike;

        if (t.action === 'OptionBuy') {
            // Opening a long position or closing a short
            if (!openPositions[contractId]) {
                openPositions[contractId] = {
                    buys: [],
                    sells: [],
                    netQuantity: 0,
                    contract: t.contract,
                    symbol: t.symbol
                };
            }
            openPositions[contractId].buys.push(t);
            openPositions[contractId].netQuantity += t.quantity;
        } else if (t.action === 'OptionSell') {
            // Closing a long position or opening a short
            if (!openPositions[contractId]) {
                openPositions[contractId] = {
                    buys: [],
                    sells: [],
                    netQuantity: 0,
                    contract: t.contract,
                    symbol: t.symbol
                };
            }
            openPositions[contractId].sells.push(t);
            openPositions[contractId].netQuantity -= t.quantity;
        }
    });

    // Process positions into trades
    var today = new Date();

    Object.keys(openPositions).forEach(function (contractId) {
        var pos = openPositions[contractId];
        if (!pos.contract) return;

        var expDate = new Date(pos.contract.expiration);
        var isExpired = expDate < today;

        var totalBuyAmount = pos.buys.reduce(function (sum, b) { return sum + b.amount; }, 0);
        var totalSellAmount = pos.sells.reduce(function (sum, s) { return sum + s.amount; }, 0);
        var totalBuyQty = pos.buys.reduce(function (sum, b) { return sum + b.quantity; }, 0);
        var totalSellQty = pos.sells.reduce(function (sum, s) { return sum + s.quantity; }, 0);

        var openDate = pos.buys.length > 0 ? pos.buys[0].date : (pos.sells.length > 0 ? pos.sells[0].date : null);
        var closeDate = null;

        // Determine if closed
        var isClosed = Math.abs(pos.netQuantity) < 0.001 || isExpired;
        if (isClosed && pos.sells.length > 0 && pos.buys.length > 0) {
            closeDate = pos.sells[pos.sells.length - 1].date > pos.buys[pos.buys.length - 1].date
                ? pos.sells[pos.sells.length - 1].date
                : pos.buys[pos.buys.length - 1].date;
        } else if (isExpired) {
            closeDate = pos.contract.expiration;
        }

        // Calculate P&L: For long positions, P&L = sell - buy
        // For short positions (sold first), P&L = sell - buy (still works)
        var pnl = totalSellAmount - totalBuyAmount;

        trades.push({
            contractId: contractId,
            symbol: pos.symbol,
            contract: pos.contract,
            openDate: openDate,
            closeDate: closeDate,
            isClosed: isClosed,
            isExpired: isExpired,
            netQuantity: pos.netQuantity,
            totalBuyQty: totalBuyQty,
            totalSellQty: totalSellQty,
            totalBuyAmount: totalBuyAmount,
            totalSellAmount: totalSellAmount,
            pnl: pnl,
            buys: pos.buys,
            sells: pos.sells
        });
    });

    return trades;
}

// Create Options Section
function createOptionsSection() {
    var transactions = getOptionsTransactions();
    if (transactions.length === 0) {
        var container = document.getElementById('optionsSummary');
        if (container) {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">No options transactions found.</p>';
        }
        return;
    }

    var trades = matchOptionsTrades(transactions);

    createOptionsSummary(trades, transactions);
    createOpenOptionsTable(trades);
    createClosedOptionsTable(trades);
    createOptionsPremiumSection(transactions);
    createOptionsPLChart(trades);
    createOptionsMonthlyChart(trades);
}

// Create Options Summary Cards
function createOptionsSummary(trades, transactions) {
    var container = document.getElementById('optionsSummary');
    if (!container) return;

    var totalTrades = trades.length;
    var openTrades = trades.filter(function (t) { return !t.isClosed; }).length;
    var closedTrades = trades.filter(function (t) { return t.isClosed; }).length;

    var totalPnL = trades.reduce(function (sum, t) { return sum + t.pnl; }, 0);
    var realizedPnL = trades.filter(function (t) { return t.isClosed; })
        .reduce(function (sum, t) { return sum + t.pnl; }, 0);

    var winningTrades = trades.filter(function (t) { return t.isClosed && t.pnl > 0; }).length;
    var losingTrades = trades.filter(function (t) { return t.isClosed && t.pnl < 0; }).length;
    var winRate = closedTrades > 0 ? ((winningTrades / closedTrades) * 100).toFixed(1) : 0;

    // Calculate total premium collected and paid
    var premiumCollected = transactions.filter(function (t) { return t.action === 'OptionSell'; })
        .reduce(function (sum, t) { return sum + t.amount; }, 0);
    var premiumPaid = transactions.filter(function (t) { return t.action === 'OptionBuy'; })
        .reduce(function (sum, t) { return sum + t.amount; }, 0);

    var html = '';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Total Trades</div>';
    html += '<div class="options-stat-value">' + totalTrades + '</div>';
    html += '</div>';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Open Positions</div>';
    html += '<div class="options-stat-value">' + openTrades + '</div>';
    html += '</div>';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Closed Trades</div>';
    html += '<div class="options-stat-value">' + closedTrades + '</div>';
    html += '</div>';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Win Rate</div>';
    html += '<div class="options-stat-value">' + winRate + '%</div>';
    html += '</div>';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Realized P&L</div>';
    html += '<div class="options-stat-value ' + (realizedPnL >= 0 ? 'positive' : 'negative') + '">' +
        formatCurrency(realizedPnL) + '</div>';
    html += '</div>';

    html += '<div class="options-stat">';
    html += '<div class="options-stat-label">Net Premium</div>';
    html += '<div class="options-stat-value ' + ((premiumCollected - premiumPaid) >= 0 ? 'positive' : 'negative') + '">' +
        formatCurrency(premiumCollected - premiumPaid) + '</div>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Open Options Table
function createOpenOptionsTable(trades) {
    var tbody = document.getElementById('openOptionsBody');
    if (!tbody) return;

    var openTrades = trades.filter(function (t) { return !t.isClosed; });

    if (openTrades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-secondary);">No open positions</td></tr>';
        return;
    }

    // Sort by expiration
    openTrades.sort(function (a, b) {
        if (!a.contract || !b.contract) return 0;
        return new Date(a.contract.expiration) - new Date(b.contract.expiration);
    });

    var html = '';
    openTrades.forEach(function (trade) {
        if (!trade.contract) return;

        var costBasis = trade.totalBuyAmount - trade.totalSellAmount;
        var currentValue = 0; // Would need real-time option prices
        var unrealizedPnL = trade.pnl;

        html += '<tr>';
        html += '<td><strong>' + trade.symbol + '</strong></td>';
        html += '<td><span class="option-type ' + trade.contract.type + '">' +
            trade.contract.type.toUpperCase() + '</span></td>';
        html += '<td>' + formatCurrency(trade.contract.strike) + '</td>';
        html += '<td>' + formatDate(trade.contract.expiration) + '</td>';
        html += '<td style="text-align: right;">' + Math.abs(trade.netQuantity).toFixed(0) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(Math.abs(costBasis)) + '</td>';
        html += '<td style="text-align: right; color: var(--text-secondary);">—</td>';
        html += '<td style="text-align: right;" class="' + (unrealizedPnL >= 0 ? 'positive' : 'negative') + '">' +
            formatCurrency(unrealizedPnL) + '</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

// Store closed options data for sorting
var closedOptionsData = [];

// Create Closed Options Table
function createClosedOptionsTable(trades) {
    var tbody = document.getElementById('closedOptionsBody');
    if (!tbody) return;

    var closedTrades = trades.filter(function (t) { return t.isClosed; });

    if (closedTrades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-secondary);">No closed positions</td></tr>';
        return;
    }

    // Transform trades into sortable data
    closedOptionsData = closedTrades.filter(function (t) { return t.contract; }).map(function (trade) {
        return {
            openDate: trade.openDate,
            closeDate: trade.closeDate || (trade.isExpired ? 'Expired' : ''),
            symbol: trade.symbol,
            type: trade.contract.type,
            strike: trade.contract.strike,
            contracts: Math.max(trade.totalBuyQty, trade.totalSellQty),
            premiumPaid: trade.totalBuyAmount,
            premiumReceived: trade.totalSellAmount,
            pnl: trade.pnl,
            isExpired: trade.isExpired
        };
    });

    // Initial sort by close date descending
    renderClosedOptionsTable('closeDate', 'desc');

    // Add sort handlers
    initClosedOptionsSorting();
}

function renderClosedOptionsTable(sortKey, sortDir) {
    var tbody = document.getElementById('closedOptionsBody');
    if (!tbody) return;

    var data = closedOptionsData.slice();

    data.sort(function (a, b) {
        var aVal = a[sortKey];
        var bVal = b[sortKey];

        // Handle dates
        if (sortKey === 'openDate' || sortKey === 'closeDate') {
            aVal = aVal && aVal !== 'Expired' ? new Date(aVal).getTime() : 0;
            bVal = bVal && bVal !== 'Expired' ? new Date(bVal).getTime() : 0;
        }
        // Handle strings
        else if (typeof aVal === 'string') {
            aVal = aVal.toLowerCase();
            bVal = bVal.toLowerCase();
        }

        if (aVal < bVal) return sortDir === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortDir === 'asc' ? 1 : -1;
        return 0;
    });

    var html = '';
    data.forEach(function (row) {
        html += '<tr>';
        html += '<td>' + formatDate(row.openDate) + '</td>';
        html += '<td>' + (row.closeDate && row.closeDate !== 'Expired' ? formatDate(row.closeDate) : (row.isExpired ? 'Expired' : '—')) + '</td>';
        html += '<td><strong>' + row.symbol + '</strong></td>';
        html += '<td><span class="option-type ' + row.type + '">' + row.type.toUpperCase() + '</span></td>';
        html += '<td style="text-align: right;">' + formatCurrency(row.strike) + '</td>';
        html += '<td style="text-align: right;">' + row.contracts + '</td>';
        html += '<td style="text-align: right; color: var(--accent-red);">' + formatCurrency(row.premiumPaid) + '</td>';
        html += '<td style="text-align: right; color: var(--accent-green);">' + formatCurrency(row.premiumReceived) + '</td>';
        html += '<td style="text-align: right;" class="' + (row.pnl >= 0 ? 'positive' : 'negative') + '">' +
            formatCurrency(row.pnl) + '</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

function initClosedOptionsSorting() {
    var table = document.getElementById('closedOptionsTable');
    if (!table) return;

    var headers = table.querySelectorAll('th.sortable');
    var currentSort = { key: 'closeDate', dir: 'desc' };

    headers.forEach(function (header) {
        header.addEventListener('click', function () {
            var sortKey = header.dataset.sort;
            var sortDir = 'asc';

            if (currentSort.key === sortKey) {
                sortDir = currentSort.dir === 'asc' ? 'desc' : 'asc';
            }

            currentSort = { key: sortKey, dir: sortDir };

            // Update header classes
            headers.forEach(function (h) {
                h.classList.remove('sort-asc', 'sort-desc');
            });
            header.classList.add('sort-' + sortDir);

            renderClosedOptionsTable(sortKey, sortDir);
        });
    });
}

// Create Premium Collected/Paid Section
function createOptionsPremiumSection(transactions) {
    var container = document.getElementById('optionsPremium');
    if (!container) return;

    var premiumCollected = transactions.filter(function (t) { return t.action === 'OptionSell'; })
        .reduce(function (sum, t) { return sum + t.amount; }, 0);
    var premiumPaid = transactions.filter(function (t) { return t.action === 'OptionBuy'; })
        .reduce(function (sum, t) { return sum + t.amount; }, 0);
    var netPremium = premiumCollected - premiumPaid;

    // Count by type
    var callsCollected = transactions.filter(function (t) {
        return t.action === 'OptionSell' && t.contract && t.contract.type === 'call';
    }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var putsCollected = transactions.filter(function (t) {
        return t.action === 'OptionSell' && t.contract && t.contract.type === 'put';
    }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var callsPaid = transactions.filter(function (t) {
        return t.action === 'OptionBuy' && t.contract && t.contract.type === 'call';
    }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var putsPaid = transactions.filter(function (t) {
        return t.action === 'OptionBuy' && t.contract && t.contract.type === 'put';
    }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var html = '';

    html += '<div class="premium-row">';
    html += '<span class="premium-label">Premium Collected (Sold)</span>';
    html += '<span class="premium-value collected">+' + formatCurrency(premiumCollected) + '</span>';
    html += '</div>';

    html += '<div class="premium-row">';
    html += '<span class="premium-label">Premium Paid (Bought)</span>';
    html += '<span class="premium-value paid">-' + formatCurrency(premiumPaid) + '</span>';
    html += '</div>';

    html += '<div class="premium-row" style="font-size: 12px;">';
    html += '<span class="premium-label">Calls: +' + formatCurrency(callsCollected) + ' / -' + formatCurrency(callsPaid) + '</span>';
    html += '<span class="premium-label">Puts: +' + formatCurrency(putsCollected) + ' / -' + formatCurrency(putsPaid) + '</span>';
    html += '</div>';

    html += '<div class="premium-net">';
    html += '<span class="premium-label">Net Premium</span>';
    html += '<span class="premium-value ' + (netPremium >= 0 ? 'collected' : 'paid') + '">' +
        (netPremium >= 0 ? '+' : '') + formatCurrency(netPremium) + '</span>';
    html += '</div>';

    container.innerHTML = html;
}

// Create Options P&L Chart by Symbol
function createOptionsPLChart(trades) {
    var ctx = document.getElementById('optionsPLChart');
    if (!ctx) return;

    // Group P&L by symbol
    var pnlBySymbol = {};
    trades.forEach(function (t) {
        if (!pnlBySymbol[t.symbol]) {
            pnlBySymbol[t.symbol] = 0;
        }
        pnlBySymbol[t.symbol] += t.pnl;
    });

    // Sort by absolute P&L
    var symbols = Object.keys(pnlBySymbol).sort(function (a, b) {
        return Math.abs(pnlBySymbol[b]) - Math.abs(pnlBySymbol[a]);
    }).slice(0, 10);

    var values = symbols.map(function (s) { return pnlBySymbol[s]; });
    var colors = values.map(function (v) { return v >= 0 ? '#4ade80' : '#f87171'; });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: symbols,
            datasets: [{
                label: 'P&L',
                data: values,
                backgroundColor: colors,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#888',
                        callback: function (val) { return formatCurrency(val); }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                },
                y: {
                    ticks: { color: '#888' },
                    grid: { display: false }
                }
            }
        }
    });
}

// Create Monthly Options P&L Chart
function createOptionsMonthlyChart(trades) {
    var ctx = document.getElementById('optionsMonthlyChart');
    if (!ctx) return;

    // Helper to parse date and get YYYY-MM
    function getYearMonth(dateStr) {
        if (!dateStr) return null;
        var d = new Date(dateStr);
        if (isNaN(d.getTime())) return null;
        var year = d.getFullYear();
        var month = String(d.getMonth() + 1).padStart(2, '0');
        return year + '-' + month;
    }

    // Group P&L by month for closed trades
    var pnlByMonth = {};
    trades.filter(function (t) { return t.isClosed && t.closeDate; }).forEach(function (t) {
        var month = getYearMonth(t.closeDate);
        if (!month) return;
        if (!pnlByMonth[month]) {
            pnlByMonth[month] = 0;
        }
        pnlByMonth[month] += t.pnl;
    });

    var months = Object.keys(pnlByMonth).sort();
    var values = months.map(function (m) { return pnlByMonth[m]; });
    var colors = values.map(function (v) { return v >= 0 ? '#4ade80' : '#f87171'; });

    // Calculate cumulative P&L
    var cumulative = [];
    var runningTotal = 0;
    values.forEach(function (v) {
        runningTotal += v;
        cumulative.push(runningTotal);
    });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: months.map(function (m) {
                var parts = m.split('-');
                var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                var monthIdx = parseInt(parts[1], 10) - 1;
                return monthNames[monthIdx] + ' ' + parts[0].substring(2);
            }),
            datasets: [
                {
                    label: 'Monthly P&L',
                    data: values,
                    backgroundColor: colors,
                    borderRadius: 4,
                    order: 2
                },
                {
                    label: 'Cumulative P&L',
                    data: cumulative,
                    type: 'line',
                    borderColor: '#60a5fa',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#60a5fa',
                    tension: 0.3,
                    order: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#888' }
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
                        callback: function (val) { return formatCurrency(val); }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// ============================================
// RECENT ACTIVITY WIDGET
// ============================================

function createRecentActivity() {
    var container = document.getElementById('recentActivity');
    if (!container) return;

    // Get recent transactions
    var transactions = getMasterTransactions().slice(0, 100); // Get most recent 100

    if (transactions.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No recent activity</p>';
        return;
    }

    // Filter to interesting transactions (not transfers or minor items)
    var interestingActions = ['Buy', 'Sell', 'Dividend', 'Staking', 'OptionBuy', 'OptionSell', 'Interest'];
    var filtered = transactions.filter(function (t) {
        return interestingActions.indexOf(t.action) !== -1 && Math.abs(t.amount) >= 1;
    }).slice(0, 10);

    if (filtered.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No recent activity</p>';
        return;
    }

    var html = '';
    filtered.forEach(function (t) {
        var iconClass = 'transfer';
        var icon = '↔';
        var amountClass = '';

        if (t.action === 'Buy') {
            iconClass = 'buy';
            icon = '↓';
            amountClass = 'negative';
        } else if (t.action === 'Sell') {
            iconClass = 'sell';
            icon = '↑';
            amountClass = 'positive';
        } else if (t.action === 'Dividend') {
            iconClass = 'dividend';
            icon = '💰';
            amountClass = 'positive';
        } else if (t.action === 'Interest') {
            iconClass = 'dividend';
            icon = '💵';
            amountClass = 'positive';
        } else if (t.action === 'Staking') {
            iconClass = 'staking';
            icon = '⚡';
            amountClass = 'positive';
        } else if (t.action === 'OptionBuy' || t.action === 'OptionSell') {
            iconClass = 'option';
            icon = '📊';
            amountClass = t.action === 'OptionSell' ? 'positive' : 'negative';
        }

        var title = t.symbol ? t.symbol : t.action;
        if (t.action === 'OptionBuy' || t.action === 'OptionSell') {
            title = (t.action === 'OptionBuy' ? 'Bought' : 'Sold') + ' ' + t.symbol + ' Option';
        } else if (t.action === 'Buy' || t.action === 'Sell') {
            title = t.action + ' ' + t.symbol;
        }

        html += '<div class="activity-item">';
        html += '<div class="activity-icon ' + iconClass + '">' + icon + '</div>';
        html += '<div class="activity-details">';
        html += '<div class="activity-title">' + title + '</div>';
        html += '<div class="activity-subtitle">' + t.account + '</div>';
        html += '</div>';
        html += '<div class="activity-amount">';
        html += '<div class="activity-amount-value ' + amountClass + '">' +
            (amountClass === 'positive' ? '+' : '') + formatCurrency(t.amount) + '</div>';
        html += '<div class="activity-date">' + formatDate(t.date) + '</div>';
        html += '</div>';
        html += '</div>';
    });

    container.innerHTML = html;
}

// ============================================
// TOP MOVERS WIDGET
// ============================================

function createTopMovers() {
    var container = document.getElementById('topMovers');
    if (!container) return;

    // Get holdings with 30-day change
    var holdings = [];
    if (typeof holdingsDetail !== 'undefined') {
        holdings = holdingsDetail.filter(function (h) {
            return h.change_30d !== null && h.change_30d !== undefined && h.current_value > 100;
        });
    }

    if (holdings.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No data available</p>';
        return;
    }

    // Sort by 30-day change
    var sorted = holdings.slice().sort(function (a, b) {
        return b.change_30d - a.change_30d;
    });

    var gainers = sorted.slice(0, 5);
    var losers = sorted.slice(-5).reverse();

    // Find max absolute change for scaling
    var maxChange = Math.max(
        Math.abs(gainers[0] ? gainers[0].change_30d : 0),
        Math.abs(losers[0] ? losers[0].change_30d : 0)
    );
    if (maxChange < 1) maxChange = 1;

    var html = '';

    // Gainers
    html += '<div class="movers-section">';
    html += '<div class="movers-header">🟢 Top Gainers</div>';
    gainers.forEach(function (h) {
        if (h.change_30d <= 0) return;
        var barWidth = Math.min((Math.abs(h.change_30d) / maxChange) * 100, 100);
        html += '<div class="mover-item">';
        html += '<div class="mover-symbol">' + h.symbol + '</div>';
        html += '<div class="mover-bar-container">';
        html += '<div class="mover-bar positive" style="width: ' + barWidth + '%;"></div>';
        html += '</div>';
        html += '<div class="mover-change positive">+' + h.change_30d.toFixed(1) + '%</div>';
        html += '</div>';
    });
    html += '</div>';

    // Losers
    html += '<div class="movers-section">';
    html += '<div class="movers-header">🔴 Top Losers</div>';
    losers.forEach(function (h) {
        if (h.change_30d >= 0) return;
        var barWidth = Math.min((Math.abs(h.change_30d) / maxChange) * 100, 100);
        html += '<div class="mover-item">';
        html += '<div class="mover-symbol">' + h.symbol + '</div>';
        html += '<div class="mover-bar-container">';
        html += '<div class="mover-bar negative" style="width: ' + barWidth + '%;"></div>';
        html += '</div>';
        html += '<div class="mover-change negative">' + h.change_30d.toFixed(1) + '%</div>';
        html += '</div>';
    });
    html += '</div>';

    container.innerHTML = html;
}

// ============================================
// CRYPTO PAGE
// ============================================

function getCryptoSymbols() {
    // Common crypto symbols that might appear in the data
    return ['BTC-USD', 'ETH-USD', 'LTC-USD', 'DOGE-USD', 'SOL-USD', 'ADA-USD', 'XRP-USD',
        'AVAX-USD', 'DOT-USD', 'MATIC-USD', 'LINK-USD', 'ATOM-USD', 'UNI-USD',
        'CBETH', 'WBTC', 'stETH'];
}

function isCryptoSymbol(symbol) {
    if (!symbol) return false;
    var cryptoSymbols = getCryptoSymbols();
    // Check exact match or if it ends with -USD (crypto pair)
    return cryptoSymbols.indexOf(symbol) !== -1 ||
        symbol.indexOf('-USD') !== -1 ||
        symbol.indexOf('BTC') !== -1 ||
        symbol.indexOf('ETH') !== -1;
}

function isCryptoAccount(account) {
    if (!account) return false;
    var cryptoAccounts = ['Coinbase', 'Coinbase Pro', 'GDAX', 'Kraken', 'Binance', 'Gemini'];
    return cryptoAccounts.some(function (ca) {
        return account.toLowerCase().indexOf(ca.toLowerCase()) !== -1;
    });
}

function createCryptoSection() {
    createCryptoSummary();
    createCryptoAllocationChart();
    createCryptoVsTraditionalChart();
    createStakingRewardsChart();
    createCryptoHoldingsTable();
    createStakingIncomeSection();
    createCryptoTxSummary();
    createCryptoTransactionsTable();
}

function createCryptoSummary() {
    var container = document.getElementById('cryptoSummary');
    if (!container) return;

    // Get crypto holdings
    var cryptoHoldings = [];
    if (typeof holdingsDetail !== 'undefined') {
        cryptoHoldings = holdingsDetail.filter(function (h) {
            return isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency';
        });
    }

    var totalValue = cryptoHoldings.reduce(function (sum, h) { return sum + (h.current_value || 0); }, 0);
    var totalCost = cryptoHoldings.reduce(function (sum, h) { return sum + (h.cost_basis || 0); }, 0);
    var totalGain = totalValue - totalCost;
    var gainPct = totalCost > 0 ? ((totalGain / totalCost) * 100) : 0;

    // Get staking income
    var stakingIncome = getMasterTransactions()
        .filter(function (t) { return t.action === 'Staking'; })
        .reduce(function (sum, t) { return sum + (t.amount || 0); }, 0);

    // Get portfolio percentage
    var portfolioTotal = portfolioData ? portfolioData.TotalValue : 0;
    var cryptoPct = portfolioTotal > 0 ? ((totalValue / portfolioTotal) * 100) : 0;

    var html = '';

    html += '<div class="crypto-stat">';
    html += '<div class="crypto-stat-icon">💎</div>';
    html += '<div class="crypto-stat-label">Total Crypto Value</div>';
    html += '<div class="crypto-stat-value">' + formatCurrency(totalValue) + '</div>';
    html += '<div class="crypto-stat-subvalue">' + cryptoPct.toFixed(1) + '% of portfolio</div>';
    html += '</div>';

    html += '<div class="crypto-stat">';
    html += '<div class="crypto-stat-icon">📈</div>';
    html += '<div class="crypto-stat-label">Total Gain/Loss</div>';
    html += '<div class="crypto-stat-value ' + (totalGain >= 0 ? 'positive' : 'negative') + '">' +
        formatCurrency(totalGain) + '</div>';
    html += '<div class="crypto-stat-subvalue">' + (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%</div>';
    html += '</div>';

    html += '<div class="crypto-stat">';
    html += '<div class="crypto-stat-icon">⚡</div>';
    html += '<div class="crypto-stat-label">Total Staking Rewards</div>';
    html += '<div class="crypto-stat-value positive">' + formatCurrency(stakingIncome) + '</div>';
    html += '<div class="crypto-stat-subvalue">All-time earned</div>';
    html += '</div>';

    html += '<div class="crypto-stat">';
    html += '<div class="crypto-stat-icon">🪙</div>';
    html += '<div class="crypto-stat-label">Cost Basis</div>';
    html += '<div class="crypto-stat-value">' + formatCurrency(totalCost) + '</div>';
    html += '<div class="crypto-stat-subvalue">Total invested</div>';
    html += '</div>';

    container.innerHTML = html;
}

function createCryptoAllocationChart() {
    var ctx = document.getElementById('cryptoAllocationChart');
    if (!ctx) return;

    var cryptoHoldings = [];
    if (typeof holdingsDetail !== 'undefined') {
        cryptoHoldings = holdingsDetail.filter(function (h) {
            return (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency')
                && h.current_value > 0;
        });
    }

    if (cryptoHoldings.length === 0) {
        ctx.parentElement.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 40px;">No crypto holdings</p>';
        return;
    }

    // Group by symbol
    var bySymbol = {};
    cryptoHoldings.forEach(function (h) {
        var sym = h.symbol.replace('-USD', '');
        if (!bySymbol[sym]) bySymbol[sym] = 0;
        bySymbol[sym] += h.current_value;
    });

    var sorted = Object.entries(bySymbol).sort(function (a, b) { return b[1] - a[1]; });
    var labels = sorted.map(function (e) { return e[0]; });
    var values = sorted.map(function (e) { return e[1]; });

    var colors = ['#f7931a', '#627eea', '#345d9d', '#26a17b', '#e84142', '#2775ca', '#8247e5', '#00d395'];

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors.slice(0, labels.length),
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#888', padding: 12 }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            var pct = ((context.raw / total) * 100).toFixed(1);
                            return context.label + ': ' + formatCurrency(context.raw) + ' (' + pct + '%)';
                        }
                    }
                }
            }
        }
    });
}

function createCryptoVsTraditionalChart() {
    var ctx = document.getElementById('cryptoVsTraditionalChart');
    if (!ctx) return;

    var cryptoValue = 0;
    var traditionalValue = 0;

    if (typeof holdingsDetail !== 'undefined') {
        holdingsDetail.forEach(function (h) {
            if (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency') {
                cryptoValue += h.current_value || 0;
            } else {
                traditionalValue += h.current_value || 0;
            }
        });
    }

    // Add cash balances to traditional
    if (typeof cashBalances !== 'undefined') {
        traditionalValue += cashBalances.reduce(function (sum, c) { return sum + (c.balance || 0); }, 0);
    }

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Crypto', 'Traditional'],
            datasets: [{
                data: [cryptoValue, traditionalValue],
                backgroundColor: ['#f7931a', '#60a5fa'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#888', padding: 12 }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            var pct = ((context.raw / total) * 100).toFixed(1);
                            return context.label + ': ' + formatCurrency(context.raw) + ' (' + pct + '%)';
                        }
                    }
                }
            }
        }
    });
}

function createStakingRewardsChart() {
    var ctx = document.getElementById('stakingRewardsChart');
    if (!ctx) return;

    var stakingByMonth = {};
    getMasterTransactions()
        .filter(function (t) { return t.action === 'Staking'; })
        .forEach(function (t) {
            var d = new Date(t.date);
            if (isNaN(d.getTime())) return;
            var month = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
            if (!stakingByMonth[month]) stakingByMonth[month] = 0;
            stakingByMonth[month] += t.amount || 0;
        });

    var months = Object.keys(stakingByMonth).sort();
    var values = months.map(function (m) { return stakingByMonth[m]; });

    // Calculate cumulative
    var cumulative = [];
    var running = 0;
    values.forEach(function (v) {
        running += v;
        cumulative.push(running);
    });

    if (months.length === 0) {
        ctx.parentElement.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 40px;">No staking rewards</p>';
        return;
    }

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: months.map(function (m) {
                var parts = m.split('-');
                var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                return monthNames[parseInt(parts[1], 10) - 1] + ' ' + parts[0].substring(2);
            }),
            datasets: [
                {
                    label: 'Monthly Rewards',
                    data: values,
                    backgroundColor: '#fbbf24',
                    borderRadius: 4,
                    order: 2
                },
                {
                    label: 'Cumulative',
                    data: cumulative,
                    type: 'line',
                    borderColor: '#22c55e',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 2,
                    tension: 0.3,
                    order: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, labels: { color: '#888' } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ': ' + formatCurrency(context.raw);
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888' }, grid: { display: false } },
                y: {
                    ticks: {
                        color: '#888',
                        callback: function (val) { return formatCurrency(val); }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

function createCryptoHoldingsTable() {
    var tbody = document.getElementById('cryptoHoldingsBody');
    if (!tbody) return;

    var cryptoHoldings = [];
    if (typeof holdingsDetail !== 'undefined') {
        cryptoHoldings = holdingsDetail.filter(function (h) {
            return (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency')
                && h.current_value > 0;
        });
    }

    if (cryptoHoldings.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-secondary);">No crypto holdings</td></tr>';
        return;
    }

    // Sort by value
    cryptoHoldings.sort(function (a, b) { return b.current_value - a.current_value; });

    var html = '';
    cryptoHoldings.forEach(function (h) {
        var gain = (h.current_value || 0) - (h.cost_basis || 0);
        var gainPct = h.cost_basis > 0 ? ((gain / h.cost_basis) * 100) : 0;

        html += '<tr>';
        html += '<td><strong>' + h.symbol.replace('-USD', '') + '</strong></td>';
        html += '<td style="text-align: right;">' + formatNumber(h.quantity, 6) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(h.current_price) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(h.current_value) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(h.cost_basis) + '</td>';
        html += '<td style="text-align: right;" class="' + (gain >= 0 ? 'positive' : 'negative') + '">' +
            formatCurrency(gain) + '</td>';
        html += '<td style="text-align: right;" class="' + (gainPct >= 0 ? 'positive' : 'negative') + '">' +
            (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

function createStakingIncomeSection() {
    var container = document.getElementById('stakingIncome');
    if (!container) return;

    var stakingByYear = {};
    getMasterTransactions()
        .filter(function (t) { return t.action === 'Staking'; })
        .forEach(function (t) {
            var d = new Date(t.date);
            if (isNaN(d.getTime())) return;
            var year = d.getFullYear();
            if (!stakingByYear[year]) stakingByYear[year] = 0;
            stakingByYear[year] += t.amount || 0;
        });

    var years = Object.keys(stakingByYear).sort().reverse();

    if (years.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No staking income</p>';
        return;
    }

    var html = '';
    years.forEach(function (year) {
        html += '<div class="staking-year">';
        html += '<span class="staking-year-label">' + year + '</span>';
        html += '<span class="staking-year-value">+' + formatCurrency(stakingByYear[year]) + '</span>';
        html += '</div>';
    });

    container.innerHTML = html;
}

function createCryptoTxSummary() {
    var container = document.getElementById('cryptoTxSummary');
    if (!container) return;

    var stats = { buys: 0, sells: 0, buyCount: 0, sellCount: 0, staking: 0, stakingCount: 0 };

    getMasterTransactions().forEach(function (t) {
        if (!isCryptoSymbol(t.symbol) && !isCryptoAccount(t.account)) return;

        if (t.action === 'Buy') {
            stats.buys += t.amount || 0;
            stats.buyCount++;
        } else if (t.action === 'Sell') {
            stats.sells += t.amount || 0;
            stats.sellCount++;
        } else if (t.action === 'Staking') {
            stats.staking += t.amount || 0;
            stats.stakingCount++;
        }
    });

    var html = '';

    html += '<div class="crypto-tx-row">';
    html += '<span class="crypto-tx-label">Total Buys (' + stats.buyCount + ' transactions)</span>';
    html += '<span class="crypto-tx-value buys">' + formatCurrency(stats.buys) + '</span>';
    html += '</div>';

    html += '<div class="crypto-tx-row">';
    html += '<span class="crypto-tx-label">Total Sells (' + stats.sellCount + ' transactions)</span>';
    html += '<span class="crypto-tx-value sells">' + formatCurrency(stats.sells) + '</span>';
    html += '</div>';

    html += '<div class="crypto-tx-row">';
    html += '<span class="crypto-tx-label">Net Investment</span>';
    html += '<span class="crypto-tx-value">' + formatCurrency(stats.buys - stats.sells) + '</span>';
    html += '</div>';

    html += '<div class="crypto-tx-row">';
    html += '<span class="crypto-tx-label">Staking Rewards (' + stats.stakingCount + ')</span>';
    html += '<span class="crypto-tx-value" style="color: var(--accent-green);">+' + formatCurrency(stats.staking) + '</span>';
    html += '</div>';

    container.innerHTML = html;
}

function createCryptoTransactionsTable() {
    var tbody = document.getElementById('cryptoTransactionsBody');
    if (!tbody) return;

    var cryptoTx = getMasterTransactions().filter(function (t) {
        return (isCryptoSymbol(t.symbol) || isCryptoAccount(t.account)) &&
            ['Buy', 'Sell', 'Staking', 'Transfer'].indexOf(t.action) !== -1;
    }).slice(0, 50);

    if (cryptoTx.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary);">No crypto transactions</td></tr>';
        return;
    }

    var html = '';
    cryptoTx.forEach(function (t) {
        var actionClass = '';
        if (t.action === 'Buy') actionClass = 'negative';
        else if (t.action === 'Sell' || t.action === 'Staking') actionClass = 'positive';

        html += '<tr>';
        html += '<td>' + formatDate(t.date) + '</td>';
        html += '<td><strong>' + (t.symbol || '').replace('-USD', '') + '</strong></td>';
        html += '<td>' + t.action + '</td>';
        html += '<td style="text-align: right;">' + (t.quantity ? formatNumber(t.quantity, 6) : '—') + '</td>';
        html += '<td style="text-align: right;">' + (t.price ? formatCurrency(t.price) : '—') + '</td>';
        html += '<td style="text-align: right;" class="' + actionClass + '">' + formatCurrency(t.amount) + '</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

// Helper for formatting numbers with decimals
function formatNumber(num, decimals) {
    if (num === null || num === undefined) return '—';
    return num.toLocaleString('en-US', { minimumFractionDigits: decimals || 2, maximumFractionDigits: decimals || 2 });
}

// =============================================
// TRANSACTIONS PAGE
// =============================================

var txState = {
    allTransactions: [],
    filteredTransactions: [],
    currentPage: 1,
    pageSize: 50,
    sortColumn: 'date',
    sortDirection: 'desc',
    volumeChart: null,
    typeChart: null
};

function createTransactionsSection() {
    // Get all transactions
    txState.allTransactions = getMasterTransactions().slice().sort(function (a, b) {
        return new Date(b.date) - new Date(a.date);
    });
    txState.filteredTransactions = txState.allTransactions.slice();

    // Populate filter dropdowns
    populateTransactionFilters();

    // Set up filter event listeners
    setupTransactionFilterListeners();

    // Create summary cards
    createTxSummaryCards();

    // Create charts
    createTxVolumeChart();
    createTxTypeChart();

    // Create transaction table
    renderTransactionTable();
}

function populateTransactionFilters() {
    var actionSelect = document.getElementById('txFilterAction');
    var accountSelect = document.getElementById('txFilterAccount');
    var symbolSelect = document.getElementById('txFilterSymbol');

    if (!actionSelect || !accountSelect || !symbolSelect) return;

    // Get unique values
    var actions = {};
    var accounts = {};
    var symbols = {};

    txState.allTransactions.forEach(function (t) {
        if (t.action) actions[t.action] = true;
        if (t.account) accounts[t.account] = true;
        if (t.symbol) symbols[t.symbol] = true;
    });

    // Populate action dropdown
    Object.keys(actions).sort().forEach(function (action) {
        var opt = document.createElement('option');
        opt.value = action;
        opt.textContent = action;
        actionSelect.appendChild(opt);
    });

    // Populate account dropdown
    Object.keys(accounts).sort().forEach(function (account) {
        var opt = document.createElement('option');
        opt.value = account;
        opt.textContent = account;
        accountSelect.appendChild(opt);
    });

    // Populate symbol dropdown
    Object.keys(symbols).sort().forEach(function (symbol) {
        var opt = document.createElement('option');
        opt.value = symbol;
        opt.textContent = symbol;
        symbolSelect.appendChild(opt);
    });

    // Set date range defaults
    var dateFrom = document.getElementById('txFilterDateFrom');
    var dateTo = document.getElementById('txFilterDateTo');

    if (txState.allTransactions.length > 0) {
        var dates = txState.allTransactions.map(function (t) {
            return new Date(t.date).getTime();
        }).filter(function (d) { return !isNaN(d); });

        if (dates.length > 0) {
            var minDate = new Date(Math.min.apply(null, dates));
            var maxDate = new Date(Math.max.apply(null, dates));
            dateFrom.min = minDate.toISOString().split('T')[0];
            dateTo.max = maxDate.toISOString().split('T')[0];
        }
    }
}

function setupTransactionFilterListeners() {
    var actionSelect = document.getElementById('txFilterAction');
    var accountSelect = document.getElementById('txFilterAccount');
    var symbolSelect = document.getElementById('txFilterSymbol');
    var dateFrom = document.getElementById('txFilterDateFrom');
    var dateTo = document.getElementById('txFilterDateTo');
    var searchInput = document.getElementById('txSearch');
    var clearBtn = document.getElementById('txClearFilters');

    function applyFilters() {
        var action = actionSelect ? actionSelect.value : '';
        var account = accountSelect ? accountSelect.value : '';
        var symbol = symbolSelect ? symbolSelect.value : '';
        var fromDate = dateFrom ? dateFrom.value : '';
        var toDate = dateTo ? dateTo.value : '';
        var search = searchInput ? searchInput.value.toLowerCase().trim() : '';

        txState.filteredTransactions = txState.allTransactions.filter(function (t) {
            // Action filter
            if (action && t.action !== action) return false;

            // Account filter
            if (account && t.account !== account) return false;

            // Symbol filter
            if (symbol && t.symbol !== symbol) return false;

            // Date range filter
            if (fromDate) {
                var txDate = new Date(t.date);
                var filterFrom = new Date(fromDate);
                if (txDate < filterFrom) return false;
            }
            if (toDate) {
                var txDate = new Date(t.date);
                var filterTo = new Date(toDate);
                filterTo.setHours(23, 59, 59, 999);
                if (txDate > filterTo) return false;
            }

            // Search filter
            if (search) {
                var searchStr = [
                    t.action,
                    t.symbol,
                    t.account,
                    formatCurrency(t.amount)
                ].join(' ').toLowerCase();
                if (searchStr.indexOf(search) === -1) return false;
            }

            return true;
        });

        txState.currentPage = 1;
        createTxSummaryCards();
        createTxVolumeChart();
        createTxTypeChart();
        renderTransactionTable();
    }

    if (actionSelect) actionSelect.addEventListener('change', applyFilters);
    if (accountSelect) accountSelect.addEventListener('change', applyFilters);
    if (symbolSelect) symbolSelect.addEventListener('change', applyFilters);
    if (dateFrom) dateFrom.addEventListener('change', applyFilters);
    if (dateTo) dateTo.addEventListener('change', applyFilters);
    if (searchInput) {
        var searchTimeout;
        searchInput.addEventListener('input', function () {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(applyFilters, 300);
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            if (actionSelect) actionSelect.value = '';
            if (accountSelect) accountSelect.value = '';
            if (symbolSelect) symbolSelect.value = '';
            if (dateFrom) dateFrom.value = '';
            if (dateTo) dateTo.value = '';
            if (searchInput) searchInput.value = '';
            applyFilters();
        });
    }
}

function createTxSummaryCards() {
    var container = document.getElementById('txSummaryCards');
    if (!container) return;

    var transactions = txState.filteredTransactions;

    // Calculate stats
    var stats = {
        total: transactions.length,
        buys: 0,
        buyCount: 0,
        sells: 0,
        sellCount: 0,
        dividends: 0,
        dividendCount: 0,
        transfers: 0,
        transferCount: 0
    };

    transactions.forEach(function (t) {
        var amt = t.amount || 0;
        switch (t.action) {
            case 'Buy':
                stats.buys += amt;
                stats.buyCount++;
                break;
            case 'Sell':
                stats.sells += amt;
                stats.sellCount++;
                break;
            case 'Dividend':
                stats.dividends += amt;
                stats.dividendCount++;
                break;
            case 'Transfer':
            case 'Transfer In':
            case 'Transfer Out':
                stats.transfers += amt;
                stats.transferCount++;
                break;
        }
    });

    var html = '';

    // Total Transactions
    html += '<div class="tx-summary-card">';
    html += '<div class="tx-summary-card-icon">📊</div>';
    html += '<div class="tx-summary-card-label">Total Transactions</div>';
    html += '<div class="tx-summary-card-value">' + stats.total.toLocaleString() + '</div>';
    html += '</div>';

    // Total Buys
    html += '<div class="tx-summary-card">';
    html += '<div class="tx-summary-card-icon">🛒</div>';
    html += '<div class="tx-summary-card-label">Total Buys</div>';
    html += '<div class="tx-summary-card-value buys">' + formatCurrency(stats.buys) + '</div>';
    html += '<div class="tx-summary-card-subvalue">' + stats.buyCount + ' transactions</div>';
    html += '</div>';

    // Total Sells
    html += '<div class="tx-summary-card">';
    html += '<div class="tx-summary-card-icon">💰</div>';
    html += '<div class="tx-summary-card-label">Total Sells</div>';
    html += '<div class="tx-summary-card-value sells">' + formatCurrency(stats.sells) + '</div>';
    html += '<div class="tx-summary-card-subvalue">' + stats.sellCount + ' transactions</div>';
    html += '</div>';

    // Dividends
    html += '<div class="tx-summary-card">';
    html += '<div class="tx-summary-card-icon">💵</div>';
    html += '<div class="tx-summary-card-label">Dividends Received</div>';
    html += '<div class="tx-summary-card-value dividends">' + formatCurrency(stats.dividends) + '</div>';
    html += '<div class="tx-summary-card-subvalue">' + stats.dividendCount + ' payments</div>';
    html += '</div>';

    // Net Flow
    var netFlow = stats.sells - stats.buys + stats.dividends;
    html += '<div class="tx-summary-card">';
    html += '<div class="tx-summary-card-icon">📈</div>';
    html += '<div class="tx-summary-card-label">Net Cash Flow</div>';
    html += '<div class="tx-summary-card-value ' + (netFlow >= 0 ? 'buys' : 'sells') + '">' + formatCurrency(netFlow) + '</div>';
    html += '</div>';

    container.innerHTML = html;
}

function createTxVolumeChart() {
    var canvas = document.getElementById('txVolumeChart');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');

    // Destroy existing chart
    if (txState.volumeChart) {
        txState.volumeChart.destroy();
    }

    // Group transactions by month
    var monthlyData = {};
    txState.filteredTransactions.forEach(function (t) {
        var d = new Date(t.date);
        if (isNaN(d.getTime())) return;
        var key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
        if (!monthlyData[key]) {
            monthlyData[key] = { buys: 0, sells: 0, dividends: 0, count: 0 };
        }
        monthlyData[key].count++;
        if (t.action === 'Buy') monthlyData[key].buys += t.amount || 0;
        if (t.action === 'Sell') monthlyData[key].sells += t.amount || 0;
        if (t.action === 'Dividend') monthlyData[key].dividends += t.amount || 0;
    });

    var months = Object.keys(monthlyData).sort();

    if (months.length === 0) {
        ctx.font = '14px Inter, sans-serif';
        ctx.fillStyle = '#888';
        ctx.textAlign = 'center';
        ctx.fillText('No transaction data', canvas.width / 2, canvas.height / 2);
        return;
    }

    var labels = months.map(function (m) {
        var parts = m.split('-');
        var date = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    });

    txState.volumeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Buys',
                    data: months.map(function (m) { return monthlyData[m].buys; }),
                    backgroundColor: 'rgba(74, 222, 128, 0.7)',
                    borderRadius: 4
                },
                {
                    label: 'Sells',
                    data: months.map(function (m) { return monthlyData[m].sells; }),
                    backgroundColor: 'rgba(248, 113, 113, 0.7)',
                    borderRadius: 4
                },
                {
                    label: 'Dividends',
                    data: months.map(function (m) { return monthlyData[m].dividends; }),
                    backgroundColor: 'rgba(96, 165, 250, 0.7)',
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#ccc' }
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
                    stacked: false,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#888' }
                },
                y: {
                    stacked: false,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#888',
                        callback: function (val) { return formatCurrency(val); }
                    }
                }
            }
        }
    });
}

function createTxTypeChart() {
    var canvas = document.getElementById('txTypeChart');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');

    // Destroy existing chart
    if (txState.typeChart) {
        txState.typeChart.destroy();
    }

    // Count by action type
    var actionCounts = {};
    txState.filteredTransactions.forEach(function (t) {
        var action = t.action || 'Other';
        if (!actionCounts[action]) actionCounts[action] = 0;
        actionCounts[action]++;
    });

    var labels = Object.keys(actionCounts).sort(function (a, b) {
        return actionCounts[b] - actionCounts[a];
    });

    if (labels.length === 0) {
        ctx.font = '14px Inter, sans-serif';
        ctx.fillStyle = '#888';
        ctx.textAlign = 'center';
        ctx.fillText('No transaction data', canvas.width / 2, canvas.height / 2);
        return;
    }

    var colors = {
        'Buy': 'rgba(74, 222, 128, 0.8)',
        'Sell': 'rgba(248, 113, 113, 0.8)',
        'Dividend': 'rgba(96, 165, 250, 0.8)',
        'Transfer': 'rgba(168, 85, 247, 0.8)',
        'Transfer In': 'rgba(168, 85, 247, 0.8)',
        'Transfer Out': 'rgba(139, 92, 246, 0.8)',
        'Staking': 'rgba(251, 191, 36, 0.8)',
        'Interest': 'rgba(45, 212, 191, 0.8)',
        'Fee': 'rgba(156, 163, 175, 0.8)',
        'Other': 'rgba(107, 114, 128, 0.8)'
    };

    var defaultColors = [
        'rgba(236, 72, 153, 0.8)',
        'rgba(34, 211, 238, 0.8)',
        'rgba(250, 204, 21, 0.8)',
        'rgba(163, 230, 53, 0.8)',
        'rgba(244, 114, 182, 0.8)'
    ];

    var colorIndex = 0;
    var bgColors = labels.map(function (label) {
        if (colors[label]) return colors[label];
        return defaultColors[colorIndex++ % defaultColors.length];
    });

    txState.typeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: labels.map(function (l) { return actionCounts[l]; }),
                backgroundColor: bgColors,
                borderWidth: 0
            }]
        },
        options: getDonutChartOptions('Activity by Type', function (context) {
            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
            var pct = ((context.raw / total) * 100).toFixed(1);
            return context.label + ': ' + context.raw.toLocaleString() + ' (' + pct + '%)';
        })
    });
}

function renderTransactionTable() {
    var tbody = document.getElementById('txTableBody');
    var countEl = document.getElementById('txCount');
    var paginationEl = document.getElementById('txPagination');

    if (!tbody) return;

    var transactions = txState.filteredTransactions.slice();

    // Sort
    transactions.sort(function (a, b) {
        var aVal, bVal;
        switch (txState.sortColumn) {
            case 'date':
                aVal = new Date(a.date).getTime() || 0;
                bVal = new Date(b.date).getTime() || 0;
                break;
            case 'action':
                aVal = (a.action || '').toLowerCase();
                bVal = (b.action || '').toLowerCase();
                break;
            case 'symbol':
                aVal = (a.symbol || '').toLowerCase();
                bVal = (b.symbol || '').toLowerCase();
                break;
            case 'quantity':
                aVal = a.quantity || 0;
                bVal = b.quantity || 0;
                break;
            case 'price':
                aVal = a.price || 0;
                bVal = b.price || 0;
                break;
            case 'amount':
                aVal = a.amount || 0;
                bVal = b.amount || 0;
                break;
            case 'account':
                aVal = (a.account || '').toLowerCase();
                bVal = (b.account || '').toLowerCase();
                break;
            default:
                aVal = 0;
                bVal = 0;
        }

        if (aVal < bVal) return txState.sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return txState.sortDirection === 'asc' ? 1 : -1;
        return 0;
    });

    // Pagination
    var totalPages = Math.ceil(transactions.length / txState.pageSize);
    var startIdx = (txState.currentPage - 1) * txState.pageSize;
    var endIdx = startIdx + txState.pageSize;
    var pageTransactions = transactions.slice(startIdx, endIdx);

    // Update count
    if (countEl) {
        countEl.textContent = '(' + transactions.length.toLocaleString() + ' transactions)';
    }

    // Render table
    if (pageTransactions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 40px;">No transactions match filters</td></tr>';
    } else {
        var html = '';
        pageTransactions.forEach(function (t) {
            var actionBadgeClass = getActionBadgeClass(t.action);
            var amountClass = '';
            if (t.action === 'Sell' || t.action === 'Dividend' || t.action === 'Staking' || t.action === 'Interest') {
                amountClass = 'positive';
            } else if (t.action === 'Buy' || t.action === 'Fee') {
                amountClass = 'negative';
            }

            html += '<tr>';
            html += '<td>' + formatDate(t.date) + '</td>';
            html += '<td><span class="action-badge ' + actionBadgeClass + '">' + (t.action || '—') + '</span></td>';
            html += '<td><strong>' + (t.symbol || '—') + '</strong></td>';
            html += '<td style="text-align: right;">' + (t.quantity ? formatNumber(t.quantity, 4) : '—') + '</td>';
            html += '<td style="text-align: right;">' + (t.price ? formatCurrency(t.price) : '—') + '</td>';
            html += '<td style="text-align: right;" class="' + amountClass + '">' + formatCurrency(t.amount) + '</td>';
            html += '<td>' + (t.account || '—') + '</td>';
            html += '</tr>';
        });
        tbody.innerHTML = html;
    }

    // Render pagination
    if (paginationEl && totalPages > 1) {
        var pagHtml = '';

        pagHtml += '<button ' + (txState.currentPage === 1 ? 'disabled' : '') + ' data-page="' + (txState.currentPage - 1) + '">← Prev</button>';

        // Page numbers
        var startPage = Math.max(1, txState.currentPage - 2);
        var endPage = Math.min(totalPages, txState.currentPage + 2);

        if (startPage > 1) {
            pagHtml += '<button data-page="1">1</button>';
            if (startPage > 2) pagHtml += '<span class="page-info">...</span>';
        }

        for (var i = startPage; i <= endPage; i++) {
            pagHtml += '<button ' + (i === txState.currentPage ? 'class="active"' : '') + ' data-page="' + i + '">' + i + '</button>';
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) pagHtml += '<span class="page-info">...</span>';
            pagHtml += '<button data-page="' + totalPages + '">' + totalPages + '</button>';
        }

        pagHtml += '<button ' + (txState.currentPage === totalPages ? 'disabled' : '') + ' data-page="' + (txState.currentPage + 1) + '">Next →</button>';
        pagHtml += '<span class="page-info">Page ' + txState.currentPage + ' of ' + totalPages + '</span>';

        paginationEl.innerHTML = pagHtml;

        // Add click listeners
        paginationEl.querySelectorAll('button[data-page]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var page = parseInt(this.getAttribute('data-page'));
                if (!isNaN(page) && page >= 1 && page <= totalPages) {
                    txState.currentPage = page;
                    renderTransactionTable();
                }
            });
        });
    } else if (paginationEl) {
        paginationEl.innerHTML = '';
    }

    // Setup table header sorting
    setupTxTableSorting();
}

function setupTxTableSorting() {
    var table = document.getElementById('txTable');
    if (!table) return;

    var headers = table.querySelectorAll('th.sortable');
    headers.forEach(function (th) {
        // Remove old listener by cloning
        var newTh = th.cloneNode(true);
        th.parentNode.replaceChild(newTh, th);

        newTh.addEventListener('click', function () {
            var col = this.getAttribute('data-sort');
            if (txState.sortColumn === col) {
                txState.sortDirection = txState.sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                txState.sortColumn = col;
                txState.sortDirection = 'asc';
            }

            // Update sort icons
            table.querySelectorAll('th.sortable').forEach(function (h) {
                h.classList.remove('sort-asc', 'sort-desc');
            });
            this.classList.add('sort-' + txState.sortDirection);

            txState.currentPage = 1;
            renderTransactionTable();
        });

        // Set initial sort indicator
        if (newTh.getAttribute('data-sort') === txState.sortColumn) {
            newTh.classList.add('sort-' + txState.sortDirection);
        }
    });
}

function getActionBadgeClass(action) {
    var actionMap = {
        'Buy': 'buy',
        'Sell': 'sell',
        'Dividend': 'dividend',
        'Transfer': 'transfer',
        'Transfer In': 'transfer',
        'Transfer Out': 'transfer',
        'Staking': 'staking',
        'Interest': 'interest',
        'Fee': 'fee'
    };
    return actionMap[action] || '';
}

// Run when DOM is ready
document.addEventListener('DOMContentLoaded', initDashboard);