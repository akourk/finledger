/**
 * Holdings Module
 * Account cards, holdings tables, treemap, heatmap
 */

// Holdings table sorting state
var holdingsSortColumn = 'current_value';
var holdingsSortDirection = 'desc';

// Format percent change with color class
function formatPriceChange(value) {
    if (value === null || value === undefined || isNaN(value)) return '<span class="muted">—</span>';
    var cls = value >= 0 ? 'positive' : 'negative';
    return '<span class="' + cls + '">' + formatPercent(value) + '</span>';
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
    var holdingsByAccount = {};
    holdings.forEach(function (h) {
        if (!holdingsByAccount[h.account]) {
            holdingsByAccount[h.account] = [];
        }
        holdingsByAccount[h.account].push(h);
    });

    // Create a map of cash-only accounts
    var cashAccountMap = {};
    cashAccounts.forEach(function (c) {
        cashAccountMap[c.account] = c;
    });

    var html = '';

    for (var i = 0; i < accounts.length; i++) {
        var account = accounts[i];
        var acctHoldings = holdingsByAccount[account.account] || [];
        var gainClass = account.unrealized_gain >= 0 ? 'positive' : 'negative';

        // For cash-only accounts, create synthetic USD holding
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
            }];
        }

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
                html += '<tr class="transaction-detail-row" id="' + rowId + '" style="display:none;"><td colspan="5"><div class="mini-transactions"></div></td></tr>';
            }
            html += '</tbody></table>';
        }

        html += '</div></div>';
    }

    container.innerHTML = html;

    // Set up click handlers for account headers (expansion)
    container.querySelectorAll('.account-header').forEach(function (header) {
        header.addEventListener('click', function () {
            header.parentElement.classList.toggle('expanded');
        });
    });

    initHoldingRowClicks(container);
}

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
        html += '<tr class="transaction-detail-row" id="' + rowId + '" style="display:none;"><td colspan="10"><div class="mini-transactions"></div></td></tr>';
    }
    tbody.innerHTML = html;
    initHoldingRowClicks(tbody);
}

// Sort holdings data
function sortHoldingsData(data, column, direction) {
    return data.slice().sort(function (a, b) {
        var aVal = a[column];
        var bVal = b[column];

        if (aVal === null || aVal === undefined) aVal = direction === 'asc' ? Infinity : -Infinity;
        if (bVal === null || bVal === undefined) bVal = direction === 'asc' ? Infinity : -Infinity;

        if (column === 'symbol' || column === 'account') {
            aVal = (aVal || '').toString().toLowerCase();
            bVal = (bVal || '').toString().toLowerCase();
            return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }

        return direction === 'asc' ? aVal - bVal : bVal - aVal;
    });
}

// Update sort header indicators
function updateSortIndicators(column, direction) {
    var headers = document.querySelectorAll('#allHoldingsTable th.sortable');
    headers.forEach(function (th) {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.getAttribute('data-sort') === column) {
            th.classList.add(direction === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });
}

// Initialize sortable table headers
function initSortableTable() {
    var headers = document.querySelectorAll('#allHoldingsTable th.sortable');

    headers.forEach(function (th) {
        th.addEventListener('click', function () {
            var column = th.getAttribute('data-sort');

            if (column === holdingsSortColumn) {
                holdingsSortDirection = holdingsSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                holdingsSortColumn = column;
                holdingsSortDirection = (column === 'symbol' || column === 'account') ? 'asc' : 'desc';
            }

            var data = getHoldingsDetail();
            var sorted = sortHoldingsData(data, holdingsSortColumn, holdingsSortDirection);
            renderHoldingsTableRows(sorted);
            updateSortIndicators(holdingsSortColumn, holdingsSortDirection);
        });
    });

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

// Create holdings treemap visualization using ECharts
function createHoldingsTreemap() {
    var container = document.getElementById('holdingsTreemap');
    if (!container) return;

    var holdings = getHoldingsDetail();
    if (holdings.length === 0) return;

    // Aggregate by sector, then by symbol
    var bySector = {};
    holdings.forEach(function (h) {
        var sector = h.sector || 'Other';
        if (!bySector[sector]) {
            bySector[sector] = { name: sector, children: {}, value: 0 };
        }
        if (!bySector[sector].children[h.symbol]) {
            bySector[sector].children[h.symbol] = {
                name: h.symbol,
                value: 0,
                cost_basis: 0,
                gain: 0,
                gain_pct: 0
            };
        }
        bySector[sector].children[h.symbol].value += h.current_value || 0;
        bySector[sector].children[h.symbol].cost_basis += h.cost_basis || 0;
        bySector[sector].children[h.symbol].gain += h.unrealized_gain || 0;
        bySector[sector].value += h.current_value || 0;
    });

    // Build treemap data structure
    var treemapData = Object.values(bySector).map(function (sector) {
        var children = Object.values(sector.children).map(function (s) {
            s.gain_pct = s.cost_basis > 0 ? (s.gain / s.cost_basis * 100) : 0;
            return s;
        }).filter(function (s) {
            return s.value > 0;
        });
        return {
            name: sector.name,
            value: sector.value,
            children: children
        };
    }).filter(function (s) {
        return s.value > 0;
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    // Dispose existing chart if any
    var existingChart = echarts.getInstanceByDom(container);
    if (existingChart) existingChart.dispose();

    var chart = echarts.init(container);

    var option = {
        tooltip: {
            formatter: function (info) {
                if (info.data.children) {
                    return '<strong>' + info.name + '</strong><br/>Value: ' + formatCurrency(info.value);
                }
                var gainPct = info.data.gain_pct || 0;
                var gain = info.data.gain || 0;
                return '<strong>' + info.name + '</strong><br/>' +
                    'Value: ' + formatCurrency(info.value) + '<br/>' +
                    'Gain/Loss: ' + formatCurrency(gain) + ' (' + (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%)';
            }
        },
        series: [{
            type: 'treemap',
            width: '100%',
            height: '100%',
            roam: false,
            nodeClick: 'zoomToNode',
            breadcrumb: {
                show: true,
                itemStyle: { color: '#333', borderColor: '#555' },
                textStyle: { color: '#ccc' }
            },
            label: {
                show: true,
                formatter: function (params) {
                    if (params.data.children) return params.name;
                    var gainPct = params.data.gain_pct || 0;
                    return params.name + '\n' + (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%';
                },
                color: '#fff',
                fontSize: 12
            },
            upperLabel: {
                show: true,
                height: 24,
                color: '#fff',
                backgroundColor: 'rgba(0,0,0,0.3)'
            },
            itemStyle: {
                borderColor: '#1a1a2e',
                borderWidth: 2,
                gapWidth: 2
            },
            levels: [
                {
                    itemStyle: {
                        borderColor: '#333',
                        borderWidth: 3,
                        gapWidth: 3
                    }
                },
                {
                    colorSaturation: [0.35, 0.5],
                    itemStyle: {
                        borderColorSaturation: 0.6,
                        gapWidth: 1
                    }
                }
            ],
            data: treemapData
        }]
    };

    // Apply colors based on gain_pct
    function applyColors(data) {
        data.forEach(function (item) {
            if (item.children) {
                applyColors(item.children);
            } else {
                var pct = item.gain_pct || 0;
                if (pct >= 50) item.itemStyle = { color: '#16a34a' };
                else if (pct >= 20) item.itemStyle = { color: '#22c55e' };
                else if (pct >= 5) item.itemStyle = { color: '#4ade80' };
                else if (pct >= 0) item.itemStyle = { color: '#86efac' };
                else if (pct >= -5) item.itemStyle = { color: '#fca5a5' };
                else if (pct >= -20) item.itemStyle = { color: '#f87171' };
                else item.itemStyle = { color: '#dc2626' };
            }
        });
    }
    applyColors(treemapData);

    chart.setOption(option);

    // Handle resize
    window.addEventListener('resize', function () {
        chart.resize();
    });
}

// Helper function to get heatmap color based on percentage (kept for backward compatibility)
function getHeatmapColor(pct, scale) {
    scale = scale || 10;
    var normalized = Math.max(-1, Math.min(1, pct / scale));

    if (normalized >= 0) {
        var intensity = normalized;
        var r = Math.round(134 - intensity * 112);
        var g = Math.round(239 - intensity * 74);
        var b = Math.round(172 - intensity * 128);
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    } else {
        var intensity = -normalized;
        var r = Math.round(252 - intensity * 32);
        var g = Math.round(165 - intensity * 127);
        var b = Math.round(165 - intensity * 127);
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }
}

// Create holdings heatmap visualization using ECharts
function createHoldingsHeatmap() {
    var container = document.getElementById('holdingsHeatmap');
    if (!container) return;

    var holdings = getHoldingsDetail();
    if (holdings.length === 0) return;

    // Aggregate by symbol
    var bySymbol = {};
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
            };
        }
        bySymbol[h.symbol].value += h.current_value || 0;
        bySymbol[h.symbol].cost_basis += h.cost_basis || 0;
        bySymbol[h.symbol].gain += h.unrealized_gain || 0;
    });

    var symbols = Object.values(bySymbol).map(function (s) {
        s.gain_pct = s.cost_basis > 0 ? (s.gain / s.cost_basis * 100) : 0;
        return s;
    }).filter(function (s) {
        return s.value > 0;
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    var topSymbols = symbols.slice(0, 20);
    var yLabels = topSymbols.map(function (s) { return s.symbol; }).reverse();
    var xLabels = ['7D Change', '30D Change', 'Total Return'];

    // Build heatmap data: [x, y, value]
    var heatmapData = [];
    topSymbols.forEach(function (s, idx) {
        var yIdx = topSymbols.length - 1 - idx; // reverse order
        heatmapData.push([0, yIdx, s.change_7d]);
        heatmapData.push([1, yIdx, s.change_30d]);
        heatmapData.push([2, yIdx, s.gain_pct]);
    });

    // Dispose existing chart if any
    var existingChart = echarts.getInstanceByDom(container);
    if (existingChart) existingChart.dispose();

    var chart = echarts.init(container);

    var option = {
        tooltip: {
            position: 'top',
            formatter: function (params) {
                var symbol = yLabels[params.value[1]];
                var metric = xLabels[params.value[0]];
                var value = params.value[2];
                return '<strong>' + symbol + '</strong><br/>' + metric + ': ' + (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
            }
        },
        grid: {
            left: 80,
            right: 60,
            top: 30,
            bottom: 50
        },
        xAxis: {
            type: 'category',
            data: xLabels,
            splitArea: { show: true },
            axisLabel: { color: '#888' },
            axisLine: { lineStyle: { color: '#444' } }
        },
        yAxis: {
            type: 'category',
            data: yLabels,
            splitArea: { show: true },
            axisLabel: { color: '#888' },
            axisLine: { lineStyle: { color: '#444' } }
        },
        visualMap: {
            min: -30,
            max: 30,
            calculable: true,
            orient: 'vertical',
            right: 0,
            top: 'center',
            inRange: {
                color: ['#dc2626', '#f87171', '#fca5a5', '#d1d5db', '#86efac', '#22c55e', '#16a34a']
            },
            textStyle: { color: '#888' }
        },
        series: [{
            name: 'Performance',
            type: 'heatmap',
            data: heatmapData,
            label: {
                show: true,
                formatter: function (params) {
                    var val = params.value[2];
                    return (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
                },
                color: '#fff',
                fontSize: 11
            },
            emphasis: {
                itemStyle: {
                    shadowBlur: 10,
                    shadowColor: 'rgba(0, 0, 0, 0.5)'
                }
            }
        }]
    };

    chart.setOption(option);

    // Handle resize
    window.addEventListener('resize', function () {
        chart.resize();
    });
}

// Initialize holdings search
function initHoldingsSearch() {
    var searchInput = document.getElementById('holdingsSearch');
    if (!searchInput) return;

    searchInput.addEventListener('input', function () {
        var query = this.value.toLowerCase().trim();
        var data = getHoldingsDetail();

        if (query === '') {
            var sorted = sortHoldingsData(data, holdingsSortColumn, holdingsSortDirection);
            renderHoldingsTableRows(sorted);
            return;
        }

        var filtered = data.filter(function (h) {
            return (h.symbol && h.symbol.toLowerCase().indexOf(query) !== -1) ||
                (h.account && h.account.toLowerCase().indexOf(query) !== -1) ||
                (h.sector && h.sector.toLowerCase().indexOf(query) !== -1);
        });

        var sorted = sortHoldingsData(filtered, holdingsSortColumn, holdingsSortDirection);
        renderHoldingsTableRows(sorted);
    });
}

// Account card expansion - main entry point for Accounts tab
function initAccountCards() {
    createAccountCards();
}

// Holding row click to show transactions
function initHoldingRowClicks(container) {
    var rows = container.querySelectorAll('.holding-row.clickable');

    rows.forEach(function (row) {
        row.addEventListener('click', function (e) {
            e.stopPropagation();

            var symbol = row.getAttribute('data-symbol');
            var account = row.getAttribute('data-account');
            var rowId = row.getAttribute('data-row-id');
            var detailRow = document.getElementById(rowId);

            if (!detailRow) return;

            var isVisible = detailRow.style.display !== 'none';

            if (isVisible) {
                detailRow.style.display = 'none';
                row.classList.remove('expanded');
            } else {
                document.querySelectorAll('.transaction-detail-row').forEach(function (r) {
                    r.style.display = 'none';
                });
                document.querySelectorAll('.holding-row.expanded').forEach(function (r) {
                    r.classList.remove('expanded');
                });

                detailRow.style.display = 'table-row';
                row.classList.add('expanded');

                var txContainer = detailRow.querySelector('.mini-transactions');
                if (txContainer && !txContainer.hasAttribute('data-loaded')) {
                    loadTransactionsForHolding(symbol, account, txContainer);
                    txContainer.setAttribute('data-loaded', 'true');
                }
            }
        });
    });
}

// Load transactions for a holding detail row
function loadTransactionsForHolding(symbol, account, container) {
    var transactions = getTransactionsForHolding(symbol, account);

    if (transactions.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">No transactions</p>';
        return;
    }

    var html = '<table class="mini-tx-table"><thead><tr>';
    html += '<th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Amount</th>';
    html += '</tr></thead><tbody>';

    transactions.slice(0, 15).forEach(function (t) {
        html += '<tr>';
        html += '<td>' + formatDate(t.date) + '</td>';
        html += '<td><span class="action-badge ' + getActionBadgeClass(t.action) + '">' + t.action + '</span></td>';
        html += '<td>' + formatNumber(t.quantity, 4) + '</td>';
        html += '<td>' + formatCurrency(t.price) + '</td>';
        html += '<td>' + formatCurrency(t.amount) + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table>';

    if (transactions.length > 15) {
        html += '<p style="text-align: center; color: var(--text-secondary); margin-top: 10px;">Showing 15 of ' + transactions.length + ' transactions</p>';
    }

    container.innerHTML = html;
}

// Donut chart color palette
var DONUT_COLORS = [
    '#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
    '#14b8a6', '#eab308', '#a855f7', '#0ea5e9', '#10b981'
];

// Create sector breakdown donut chart
function createSectorDonutChart() {
    var container = document.getElementById('sectorDonutChart');
    if (!container) return;

    var holdings = getHoldingsDetail();
    var cashBalances = getCashBalances();

    if (holdings.length === 0 && cashBalances.length === 0) return;

    // Aggregate by sector
    var bySector = {};
    holdings.forEach(function (h) {
        var sector = h.sector || 'Other';
        if (!bySector[sector]) {
            bySector[sector] = 0;
        }
        bySector[sector] += h.current_value || 0;
    });

    // Add cash accounts as "Cash" sector
    cashBalances.forEach(function (c) {
        if (!bySector['Cash']) {
            bySector['Cash'] = 0;
        }
        bySector['Cash'] += c.balance || 0;
    });

    // Convert to array and sort by value
    var data = Object.keys(bySector).map(function (sector) {
        return { name: sector, value: bySector[sector] };
    }).filter(function (d) {
        return d.value > 0;
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    createDonutChart(container, data, 'Sector Allocation');
}

// Create account breakdown donut chart
function createAccountDonutChart() {
    var container = document.getElementById('accountDonutChart');
    if (!container) return;

    var holdings = getHoldingsDetail();
    var cashBalances = getCashBalances();

    if (holdings.length === 0 && cashBalances.length === 0) return;

    // Aggregate by account
    var byAccount = {};
    holdings.forEach(function (h) {
        var account = h.account || 'Unknown';
        if (!byAccount[account]) {
            byAccount[account] = 0;
        }
        byAccount[account] += h.current_value || 0;
    });

    // Add cash accounts
    cashBalances.forEach(function (c) {
        var account = c.account || 'Unknown';
        if (!byAccount[account]) {
            byAccount[account] = 0;
        }
        byAccount[account] += c.balance || 0;
    });

    // Convert to array and sort by value
    var data = Object.keys(byAccount).map(function (account) {
        return { name: account, value: byAccount[account] };
    }).filter(function (d) {
        return d.value > 0;
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    createDonutChart(container, data, 'Account Allocation');
}

// Create top holdings donut chart
function createTopHoldingsDonutChart() {
    var container = document.getElementById('topHoldingsDonutChart');
    if (!container) return;

    var holdings = getHoldingsDetail();
    var cashBalances = getCashBalances();

    if (holdings.length === 0 && cashBalances.length === 0) return;

    // Aggregate by symbol (combine same symbol across accounts)
    var bySymbol = {};
    holdings.forEach(function (h) {
        var symbol = h.symbol || 'Unknown';
        if (!bySymbol[symbol]) {
            bySymbol[symbol] = 0;
        }
        bySymbol[symbol] += h.current_value || 0;
    });

    // Add cash as USD
    var totalCash = 0;
    cashBalances.forEach(function (c) {
        totalCash += c.balance || 0;
    });
    if (totalCash > 0) {
        bySymbol['USD (Cash)'] = totalCash;
    }

    // Convert to array and sort by value
    var allHoldings = Object.keys(bySymbol).map(function (symbol) {
        return { name: symbol, value: bySymbol[symbol] };
    }).filter(function (d) {
        return d.value > 0;
    }).sort(function (a, b) {
        return b.value - a.value;
    });

    // Take top 10 and group rest as "Other"
    var top10 = allHoldings.slice(0, 10);
    var otherTotal = allHoldings.slice(10).reduce(function (sum, h) {
        return sum + h.value;
    }, 0);

    if (otherTotal > 0) {
        top10.push({ name: 'Other (' + (allHoldings.length - 10) + ')', value: otherTotal });
    }

    createDonutChart(container, top10, 'Top Holdings');
}

// Generic donut chart creator using ECharts
function createDonutChart(container, data, title) {
    // Dispose existing chart if any
    var existingChart = echarts.getInstanceByDom(container);
    if (existingChart) existingChart.dispose();

    var chart = echarts.init(container);

    // Calculate total for percentage
    var total = data.reduce(function (sum, d) { return sum + d.value; }, 0);

    var option = {
        tooltip: {
            trigger: 'item',
            formatter: function (params) {
                var pct = ((params.value / total) * 100).toFixed(1);
                return '<strong>' + params.name + '</strong><br/>' +
                    formatCurrency(params.value) + ' (' + pct + '%)';
            },
            backgroundColor: 'rgba(20, 20, 35, 0.95)',
            borderColor: '#333',
            textStyle: { color: '#fff' }
        },
        series: [{
            type: 'pie',
            radius: ['40%', '75%'],
            center: ['50%', '42%'],
            avoidLabelOverlap: true,
            itemStyle: {
                borderRadius: 4,
                borderColor: '#1a1a2e',
                borderWidth: 2
            },
            label: {
                show: true,
                position: 'outside',
                formatter: function (params) {
                    var pct = ((params.value / total) * 100).toFixed(1);
                    if (pct < 3) return '';  // Hide labels for very small slices
                    return params.name + '\n' + pct + '%';
                },
                color: '#ccc',
                fontSize: 10,
                lineHeight: 14
            },
            labelLine: {
                show: true,
                length: 10,
                length2: 8,
                lineStyle: {
                    color: '#555'
                }
            },
            emphasis: {
                label: {
                    show: true,
                    fontSize: 11,
                    fontWeight: 'bold',
                    color: '#fff'
                },
                itemStyle: {
                    shadowBlur: 10,
                    shadowOffsetX: 0,
                    shadowColor: 'rgba(0, 0, 0, 0.5)'
                }
            },
            data: data.map(function (d, i) {
                return {
                    name: d.name,
                    value: d.value,
                    itemStyle: { color: DONUT_COLORS[i % DONUT_COLORS.length] }
                };
            })
        }],
        graphic: [{
            type: 'text',
            left: 'center',
            top: '37%',
            style: {
                text: formatCurrency(total),
                fontSize: 14,
                fontWeight: 'bold',
                fill: '#fff',
                textAlign: 'center'
            }
        }, {
            type: 'text',
            left: 'center',
            top: '45%',
            style: {
                text: 'Total',
                fontSize: 10,
                fill: '#888',
                textAlign: 'center'
            }
        }]
    };

    chart.setOption(option);
    window.addEventListener('resize', function () { chart.resize(); });
}

// Initialize all donut charts
function createHoldingsDonutCharts() {
    createSectorDonutChart();
    createAccountDonutChart();
    createTopHoldingsDonutChart();
}


