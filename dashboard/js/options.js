/**
 * Options Module
 * Options trading analysis, P&L tracking
 */

// Parse option contract details from note field
function parseOptionContract(note) {
    if (!note) return null;
    var match = note.match(/^([A-Z]+)\s+(\d{1,2}\/\d{1,2}\/\d{4})\s+(Call|Put)\s+\$?([\d,]+\.?\d*)/i);
    if (!match) return null;
    return {
        symbol: match[1],
        expiration: match[2],
        type: match[3].toLowerCase(),
        strike: parseFloat(match[4].replace(',', ''))
    };
}

// Get all options transactions
function getOptionsTransactions() {
    var transactions = getMasterTransactions();
    if (!transactions || transactions.length === 0) return [];
    return transactions.filter(function (t) {
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
    var openPositions = {};

    var sorted = transactions.slice().sort(function (a, b) {
        return new Date(a.date) - new Date(b.date);
    });

    sorted.forEach(function (t) {
        if (!t.contract) return;

        var contractId = t.symbol + '_' + t.contract.expiration + '_' + t.contract.type + '_' + t.contract.strike;

        if (!openPositions[contractId]) {
            openPositions[contractId] = { buys: [], sells: [], netQuantity: 0, contract: t.contract, symbol: t.symbol };
        }

        if (t.action === 'OptionBuy') {
            openPositions[contractId].buys.push(t);
            openPositions[contractId].netQuantity += t.quantity;
        } else if (t.action === 'OptionSell') {
            openPositions[contractId].sells.push(t);
            openPositions[contractId].netQuantity -= t.quantity;
        }
    });

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

        var isClosed = Math.abs(pos.netQuantity) < 0.001 || isExpired;
        if (isClosed && pos.sells.length > 0 && pos.buys.length > 0) {
            closeDate = pos.sells[pos.sells.length - 1].date > pos.buys[pos.buys.length - 1].date
                ? pos.sells[pos.sells.length - 1].date : pos.buys[pos.buys.length - 1].date;
        } else if (isExpired) {
            closeDate = pos.contract.expiration;
        }

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
            pnl: totalSellAmount - totalBuyAmount,
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
        if (container) container.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">No options transactions found.</p>';
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
    var realizedPnL = trades.filter(function (t) { return t.isClosed; }).reduce(function (sum, t) { return sum + t.pnl; }, 0);
    var winningTrades = trades.filter(function (t) { return t.isClosed && t.pnl > 0; }).length;
    var winRate = closedTrades > 0 ? ((winningTrades / closedTrades) * 100).toFixed(1) : 0;

    var premiumCollected = transactions.filter(function (t) { return t.action === 'OptionSell'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var premiumPaid = transactions.filter(function (t) { return t.action === 'OptionBuy'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var html = '';
    html += '<div class="options-stat"><div class="options-stat-label">Total Trades</div><div class="options-stat-value">' + totalTrades + '</div></div>';
    html += '<div class="options-stat"><div class="options-stat-label">Open Positions</div><div class="options-stat-value">' + openTrades + '</div></div>';
    html += '<div class="options-stat"><div class="options-stat-label">Closed Trades</div><div class="options-stat-value">' + closedTrades + '</div></div>';
    html += '<div class="options-stat"><div class="options-stat-label">Win Rate</div><div class="options-stat-value">' + winRate + '%</div></div>';
    html += '<div class="options-stat"><div class="options-stat-label">Realized P&L</div><div class="options-stat-value ' + (realizedPnL >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(realizedPnL) + '</div></div>';
    html += '<div class="options-stat"><div class="options-stat-label">Net Premium</div><div class="options-stat-value ' + ((premiumCollected - premiumPaid) >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(premiumCollected - premiumPaid) + '</div></div>';

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

    openTrades.sort(function (a, b) {
        if (!a.contract || !b.contract) return 0;
        return new Date(a.contract.expiration) - new Date(b.contract.expiration);
    });

    var html = '';
    openTrades.forEach(function (trade) {
        if (!trade.contract) return;
        var costBasis = trade.totalBuyAmount - trade.totalSellAmount;

        html += '<tr>';
        html += '<td><strong>' + trade.symbol + '</strong></td>';
        html += '<td><span class="option-type ' + trade.contract.type + '">' + trade.contract.type.toUpperCase() + '</span></td>';
        html += '<td>' + formatCurrency(trade.contract.strike) + '</td>';
        html += '<td>' + formatDate(trade.contract.expiration) + '</td>';
        html += '<td style="text-align: right;">' + Math.abs(trade.netQuantity).toFixed(0) + '</td>';
        html += '<td style="text-align: right;">' + formatCurrency(Math.abs(costBasis)) + '</td>';
        html += '<td style="text-align: right; color: var(--text-secondary);">—</td>';
        html += '<td style="text-align: right;" class="' + (trade.pnl >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(trade.pnl) + '</td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;
}

// Closed options data for sorting
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

    renderClosedOptionsTable('closeDate', 'desc');
    initClosedOptionsSorting();
}

function renderClosedOptionsTable(sortKey, sortDir) {
    var tbody = document.getElementById('closedOptionsBody');
    if (!tbody) return;

    var data = closedOptionsData.slice();
    data.sort(function (a, b) {
        var aVal = a[sortKey];
        var bVal = b[sortKey];

        if (sortKey === 'openDate' || sortKey === 'closeDate') {
            aVal = aVal && aVal !== 'Expired' ? new Date(aVal).getTime() : 0;
            bVal = bVal && bVal !== 'Expired' ? new Date(bVal).getTime() : 0;
        } else if (typeof aVal === 'string') {
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
        html += '<td style="text-align: right;" class="' + (row.pnl >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(row.pnl) + '</td>';
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
            var sortDir = currentSort.key === sortKey ? (currentSort.dir === 'asc' ? 'desc' : 'asc') : 'asc';
            currentSort = { key: sortKey, dir: sortDir };

            headers.forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });
            header.classList.add('sort-' + sortDir);
            renderClosedOptionsTable(sortKey, sortDir);
        });
    });
}

// Create Premium Section
function createOptionsPremiumSection(transactions) {
    var container = document.getElementById('optionsPremium');
    if (!container) return;

    var premiumCollected = transactions.filter(function (t) { return t.action === 'OptionSell'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var premiumPaid = transactions.filter(function (t) { return t.action === 'OptionBuy'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var netPremium = premiumCollected - premiumPaid;

    var callsCollected = transactions.filter(function (t) { return t.action === 'OptionSell' && t.contract && t.contract.type === 'call'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var putsCollected = transactions.filter(function (t) { return t.action === 'OptionSell' && t.contract && t.contract.type === 'put'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var callsPaid = transactions.filter(function (t) { return t.action === 'OptionBuy' && t.contract && t.contract.type === 'call'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);
    var putsPaid = transactions.filter(function (t) { return t.action === 'OptionBuy' && t.contract && t.contract.type === 'put'; }).reduce(function (sum, t) { return sum + t.amount; }, 0);

    var html = '';
    html += '<div class="premium-row"><span class="premium-label">Premium Collected (Sold)</span><span class="premium-value collected">+' + formatCurrency(premiumCollected) + '</span></div>';
    html += '<div class="premium-row"><span class="premium-label">Premium Paid (Bought)</span><span class="premium-value paid">-' + formatCurrency(premiumPaid) + '</span></div>';
    html += '<div class="premium-row" style="font-size: 12px;"><span class="premium-label">Calls: +' + formatCurrency(callsCollected) + ' / -' + formatCurrency(callsPaid) + '</span>';
    html += '<span class="premium-label">Puts: +' + formatCurrency(putsCollected) + ' / -' + formatCurrency(putsPaid) + '</span></div>';
    html += '<div class="premium-net"><span class="premium-label">Net Premium</span>';
    html += '<span class="premium-value ' + (netPremium >= 0 ? 'collected' : 'paid') + '">' + (netPremium >= 0 ? '+' : '') + formatCurrency(netPremium) + '</span></div>';

    container.innerHTML = html;
}

// Create Options P&L Chart by Symbol
function createOptionsPLChart(trades) {
    var ctx = document.getElementById('optionsPLChart');
    if (!ctx) return;

    var pnlBySymbol = {};
    trades.forEach(function (t) {
        if (!pnlBySymbol[t.symbol]) pnlBySymbol[t.symbol] = 0;
        pnlBySymbol[t.symbol] += t.pnl;
    });

    var symbols = Object.keys(pnlBySymbol).sort(function (a, b) {
        return Math.abs(pnlBySymbol[b]) - Math.abs(pnlBySymbol[a]);
    }).slice(0, 10);

    var values = symbols.map(function (s) { return pnlBySymbol[s]; });
    var colors = values.map(function (v) { return v >= 0 ? '#4ade80' : '#f87171'; });

    new Chart(ctx, {
        type: 'bar',
        data: { labels: symbols, datasets: [{ label: 'P&L', data: values, backgroundColor: colors, borderRadius: 4 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (context) { return formatCurrency(context.raw); } } } },
            scales: {
                x: { ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#888' }, grid: { display: false } }
            }
        }
    });
}

// Create Monthly Options P&L Chart
function createOptionsMonthlyChart(trades) {
    var ctx = document.getElementById('optionsMonthlyChart');
    if (!ctx) return;

    function getYearMonth(dateStr) {
        if (!dateStr) return null;
        var d = new Date(dateStr);
        if (isNaN(d.getTime())) return null;
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    }

    var pnlByMonth = {};
    trades.filter(function (t) { return t.isClosed && t.closeDate; }).forEach(function (t) {
        var month = getYearMonth(t.closeDate);
        if (!month) return;
        if (!pnlByMonth[month]) pnlByMonth[month] = 0;
        pnlByMonth[month] += t.pnl;
    });

    var months = Object.keys(pnlByMonth).sort();
    var values = months.map(function (m) { return pnlByMonth[m]; });
    var colors = values.map(function (v) { return v >= 0 ? '#4ade80' : '#f87171'; });

    var cumulative = [];
    var runningTotal = 0;
    values.forEach(function (v) { runningTotal += v; cumulative.push(runningTotal); });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: months.map(function (m) {
                var parts = m.split('-');
                var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                return monthNames[parseInt(parts[1], 10) - 1] + ' ' + parts[0].substring(2);
            }),
            datasets: [
                { label: 'Monthly P&L', data: values, backgroundColor: colors, borderRadius: 4, order: 2 },
                { label: 'Cumulative P&L', data: cumulative, type: 'line', borderColor: '#60a5fa', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#60a5fa', tension: 0.3, order: 1 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true, labels: { color: '#888' } }, tooltip: { callbacks: { label: function (context) { return context.dataset.label + ': ' + formatCurrency(context.raw); } } } },
            scales: {
                x: { ticks: { color: '#888' }, grid: { display: false } },
                y: { ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } }, grid: { color: 'rgba(255,255,255,0.05)' } }
            }
        }
    });
}
