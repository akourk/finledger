/**
 * Transactions Module
 * Transaction history, filtering, and analysis
 */

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
    txState.allTransactions = getMasterTransactions().slice().sort(function (a, b) {
        return new Date(b.date) - new Date(a.date);
    });
    txState.filteredTransactions = txState.allTransactions.slice();

    populateTransactionFilters();
    setupTransactionFilterListeners();
    createTxSummaryCards();
    createTxVolumeChart();
    createTxTypeChart();
    renderTransactionTable();
}

function populateTransactionFilters() {
    var actionSelect = document.getElementById('txFilterAction');
    var accountSelect = document.getElementById('txFilterAccount');
    var symbolSelect = document.getElementById('txFilterSymbol');
    if (!actionSelect || !accountSelect || !symbolSelect) return;

    var actions = {}, accounts = {}, symbols = {};
    txState.allTransactions.forEach(function (t) {
        if (t.action) actions[t.action] = true;
        if (t.account) accounts[t.account] = true;
        if (t.symbol) symbols[t.symbol] = true;
    });

    Object.keys(actions).sort().forEach(function (action) {
        var opt = document.createElement('option');
        opt.value = action;
        opt.textContent = action;
        actionSelect.appendChild(opt);
    });

    Object.keys(accounts).sort().forEach(function (account) {
        var opt = document.createElement('option');
        opt.value = account;
        opt.textContent = account;
        accountSelect.appendChild(opt);
    });

    Object.keys(symbols).sort().forEach(function (symbol) {
        var opt = document.createElement('option');
        opt.value = symbol;
        opt.textContent = symbol;
        symbolSelect.appendChild(opt);
    });

    var dateFrom = document.getElementById('txFilterDateFrom');
    var dateTo = document.getElementById('txFilterDateTo');
    if (txState.allTransactions.length > 0) {
        var dates = txState.allTransactions.map(function (t) { return new Date(t.date).getTime(); }).filter(function (d) { return !isNaN(d); });
        if (dates.length > 0) {
            dateFrom.min = new Date(Math.min.apply(null, dates)).toISOString().split('T')[0];
            dateTo.max = new Date(Math.max.apply(null, dates)).toISOString().split('T')[0];
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
            if (action && t.action !== action) return false;
            if (account && t.account !== account) return false;
            if (symbol && t.symbol !== symbol) return false;

            if (fromDate) {
                var txDate = new Date(t.date);
                if (txDate < new Date(fromDate)) return false;
            }
            if (toDate) {
                var txDate = new Date(t.date);
                var filterTo = new Date(toDate);
                filterTo.setHours(23, 59, 59, 999);
                if (txDate > filterTo) return false;
            }

            if (search) {
                var searchStr = [t.action, t.symbol, t.account, formatCurrency(t.amount)].join(' ').toLowerCase();
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
    var stats = { total: transactions.length, buys: 0, buyCount: 0, sells: 0, sellCount: 0, dividends: 0, dividendCount: 0 };

    transactions.forEach(function (t) {
        var amt = t.amount || 0;
        switch (t.action) {
            case 'Buy': stats.buys += amt; stats.buyCount++; break;
            case 'Sell': stats.sells += amt; stats.sellCount++; break;
            case 'Dividend': stats.dividends += amt; stats.dividendCount++; break;
        }
    });

    var netFlow = stats.sells - stats.buys + stats.dividends;

    var html = '';
    html += '<div class="tx-summary-card"><div class="tx-summary-card-icon">📊</div><div class="tx-summary-card-label">Total Transactions</div><div class="tx-summary-card-value">' + stats.total.toLocaleString() + '</div></div>';
    html += '<div class="tx-summary-card"><div class="tx-summary-card-icon">🛒</div><div class="tx-summary-card-label">Total Buys</div><div class="tx-summary-card-value buys">' + formatCurrency(stats.buys) + '</div><div class="tx-summary-card-subvalue">' + stats.buyCount + ' transactions</div></div>';
    html += '<div class="tx-summary-card"><div class="tx-summary-card-icon">💰</div><div class="tx-summary-card-label">Total Sells</div><div class="tx-summary-card-value sells">' + formatCurrency(stats.sells) + '</div><div class="tx-summary-card-subvalue">' + stats.sellCount + ' transactions</div></div>';
    html += '<div class="tx-summary-card"><div class="tx-summary-card-icon">💵</div><div class="tx-summary-card-label">Dividends Received</div><div class="tx-summary-card-value dividends">' + formatCurrency(stats.dividends) + '</div><div class="tx-summary-card-subvalue">' + stats.dividendCount + ' payments</div></div>';
    html += '<div class="tx-summary-card"><div class="tx-summary-card-icon">📈</div><div class="tx-summary-card-label">Net Cash Flow</div><div class="tx-summary-card-value ' + (netFlow >= 0 ? 'buys' : 'sells') + '">' + formatCurrency(netFlow) + '</div></div>';

    container.innerHTML = html;
}

function createTxVolumeChart() {
    var canvas = document.getElementById('txVolumeChart');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    if (txState.volumeChart) txState.volumeChart.destroy();

    var monthlyData = {};
    txState.filteredTransactions.forEach(function (t) {
        var d = new Date(t.date);
        if (isNaN(d.getTime())) return;
        var key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
        if (!monthlyData[key]) monthlyData[key] = { buys: 0, sells: 0, dividends: 0, count: 0 };
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
        return new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    });

    txState.volumeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                { label: 'Buys', data: months.map(function (m) { return monthlyData[m].buys; }), backgroundColor: 'rgba(74, 222, 128, 0.7)', borderRadius: 4 },
                { label: 'Sells', data: months.map(function (m) { return monthlyData[m].sells; }), backgroundColor: 'rgba(248, 113, 113, 0.7)', borderRadius: 4 },
                { label: 'Dividends', data: months.map(function (m) { return monthlyData[m].dividends; }), backgroundColor: 'rgba(96, 165, 250, 0.7)', borderRadius: 4 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { position: 'top', labels: { color: '#ccc' } }, tooltip: { callbacks: { label: function (context) { return context.dataset.label + ': ' + formatCurrency(context.raw); } } } },
            scales: {
                x: { stacked: false, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888' } },
                y: { stacked: false, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } } }
            }
        }
    });
}

function createTxTypeChart() {
    var canvas = document.getElementById('txTypeChart');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    if (txState.typeChart) txState.typeChart.destroy();

    var actionCounts = {};
    txState.filteredTransactions.forEach(function (t) {
        var action = t.action || 'Other';
        if (!actionCounts[action]) actionCounts[action] = 0;
        actionCounts[action]++;
    });

    var labels = Object.keys(actionCounts).sort(function (a, b) { return actionCounts[b] - actionCounts[a]; });
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
        'Staking': 'rgba(251, 191, 36, 0.8)',
        'Interest': 'rgba(45, 212, 191, 0.8)',
        'Fee': 'rgba(156, 163, 175, 0.8)'
    };
    var defaultColors = ['rgba(236, 72, 153, 0.8)', 'rgba(34, 211, 238, 0.8)', 'rgba(250, 204, 21, 0.8)', 'rgba(163, 230, 53, 0.8)'];

    var colorIndex = 0;
    var bgColors = labels.map(function (label) {
        if (colors[label]) return colors[label];
        return defaultColors[colorIndex++ % defaultColors.length];
    });

    txState.typeChart = new Chart(ctx, {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: labels.map(function (l) { return actionCounts[l]; }), backgroundColor: bgColors, borderWidth: 0 }] },
        options: getDonutChartOptions('Activity by Type', function (context) {
            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
            return context.label + ': ' + context.raw.toLocaleString() + ' (' + ((context.raw / total) * 100).toFixed(1) + '%)';
        })
    });
}

function renderTransactionTable() {
    var tbody = document.getElementById('txTableBody');
    var countEl = document.getElementById('txCount');
    var paginationEl = document.getElementById('txPagination');
    if (!tbody) return;

    var transactions = txState.filteredTransactions.slice();

    transactions.sort(function (a, b) {
        var aVal, bVal;
        switch (txState.sortColumn) {
            case 'date': aVal = new Date(a.date).getTime() || 0; bVal = new Date(b.date).getTime() || 0; break;
            case 'action': aVal = (a.action || '').toLowerCase(); bVal = (b.action || '').toLowerCase(); break;
            case 'symbol': aVal = (a.symbol || '').toLowerCase(); bVal = (b.symbol || '').toLowerCase(); break;
            case 'quantity': aVal = a.quantity || 0; bVal = b.quantity || 0; break;
            case 'price': aVal = a.price || 0; bVal = b.price || 0; break;
            case 'amount': aVal = a.amount || 0; bVal = b.amount || 0; break;
            case 'account': aVal = (a.account || '').toLowerCase(); bVal = (b.account || '').toLowerCase(); break;
            default: aVal = 0; bVal = 0;
        }
        if (aVal < bVal) return txState.sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return txState.sortDirection === 'asc' ? 1 : -1;
        return 0;
    });

    var totalPages = Math.ceil(transactions.length / txState.pageSize);
    var startIdx = (txState.currentPage - 1) * txState.pageSize;
    var pageTransactions = transactions.slice(startIdx, startIdx + txState.pageSize);

    if (countEl) countEl.textContent = '(' + transactions.length.toLocaleString() + ' transactions)';

    if (pageTransactions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 40px;">No transactions match filters</td></tr>';
    } else {
        var html = '';
        pageTransactions.forEach(function (t) {
            var actionBadgeClass = getActionBadgeClass(t.action);
            var amountClass = '';
            if (t.action === 'Sell' || t.action === 'Dividend' || t.action === 'Staking' || t.action === 'Interest') amountClass = 'positive';
            else if (t.action === 'Buy' || t.action === 'Fee') amountClass = 'negative';

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

    if (paginationEl && totalPages > 1) {
        var pagHtml = '';
        pagHtml += '<button ' + (txState.currentPage === 1 ? 'disabled' : '') + ' data-page="' + (txState.currentPage - 1) + '">← Prev</button>';

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

    setupTxTableSorting();
}

function setupTxTableSorting() {
    var table = document.getElementById('txTable');
    if (!table) return;

    var headers = table.querySelectorAll('th.sortable');
    headers.forEach(function (th) {
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

            table.querySelectorAll('th.sortable').forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });
            this.classList.add('sort-' + txState.sortDirection);
            txState.currentPage = 1;
            renderTransactionTable();
        });

        if (newTh.getAttribute('data-sort') === txState.sortColumn) {
            newTh.classList.add('sort-' + txState.sortDirection);
        }
    });
}

function getActionBadgeClass(action) {
    var actionMap = { 'Buy': 'buy', 'Sell': 'sell', 'Dividend': 'dividend', 'Transfer': 'transfer', 'Staking': 'staking', 'Interest': 'interest', 'Fee': 'fee' };
    return actionMap[action] || '';
}
