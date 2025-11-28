/**
 * Crypto Module
 * Cryptocurrency analysis, staking rewards
 */

function getCryptoSymbols() {
    return ['BTC-USD', 'ETH-USD', 'LTC-USD', 'DOGE-USD', 'SOL-USD', 'ADA-USD', 'XRP-USD',
        'AVAX-USD', 'DOT-USD', 'MATIC-USD', 'LINK-USD', 'ATOM-USD', 'UNI-USD', 'CBETH', 'WBTC', 'stETH'];
}

function isCryptoSymbol(symbol) {
    if (!symbol) return false;
    var cryptoSymbols = getCryptoSymbols();
    return cryptoSymbols.indexOf(symbol) !== -1 || symbol.indexOf('-USD') !== -1 ||
        symbol.indexOf('BTC') !== -1 || symbol.indexOf('ETH') !== -1;
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

    var allHoldings = getHoldingsDetail();
    var cryptoHoldings = allHoldings.filter(function (h) {
        return isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency';
    });

    var totalValue = cryptoHoldings.reduce(function (sum, h) { return sum + (h.current_value || 0); }, 0);
    var totalCost = cryptoHoldings.reduce(function (sum, h) { return sum + (h.cost_basis || 0); }, 0);
    var totalGain = totalValue - totalCost;
    var gainPct = totalCost > 0 ? ((totalGain / totalCost) * 100) : 0;

    var stakingIncome = getMasterTransactions()
        .filter(function (t) { return t.action === 'Staking'; })
        .reduce(function (sum, t) { return sum + (t.amount || 0); }, 0);

    // Calculate crypto realized gains
    var realizedGains = typeof realizedGainsData !== 'undefined' ? realizedGainsData : [];
    var cryptoRealizedGain = realizedGains
        .filter(function (g) {
            return isCryptoSymbol(g.Symbol) || isCryptoAccount(g.Account);
        })
        .reduce(function (sum, g) { return sum + (g.RealizedGain || 0); }, 0);

    var portfolioTotal = typeof portfolioData !== 'undefined' ? portfolioData.TotalValue : 0;
    var cryptoPct = portfolioTotal > 0 ? ((totalValue / portfolioTotal) * 100) : 0;

    var html = '';
    html += '<div class="crypto-stat"><div class="crypto-stat-icon">💎</div>';
    html += '<div class="crypto-stat-label">Total Crypto Value</div>';
    html += '<div class="crypto-stat-value">' + formatCurrency(totalValue) + '</div>';
    html += '<div class="crypto-stat-subvalue">' + cryptoPct.toFixed(1) + '% of portfolio</div></div>';

    html += '<div class="crypto-stat"><div class="crypto-stat-icon">📈</div>';
    html += '<div class="crypto-stat-label">Unrealized Gain/Loss</div>';
    html += '<div class="crypto-stat-value ' + (totalGain >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(totalGain) + '</div>';
    html += '<div class="crypto-stat-subvalue">' + (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%</div></div>';

    html += '<div class="crypto-stat"><div class="crypto-stat-icon">💰</div>';
    html += '<div class="crypto-stat-label">Realized Gains</div>';
    html += '<div class="crypto-stat-value ' + (cryptoRealizedGain >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(cryptoRealizedGain) + '</div>';
    html += '<div class="crypto-stat-subvalue">All-time closed positions</div></div>';

    html += '<div class="crypto-stat"><div class="crypto-stat-icon">⚡</div>';
    html += '<div class="crypto-stat-label">Staking Rewards</div>';
    html += '<div class="crypto-stat-value positive">' + formatCurrency(stakingIncome) + '</div>';
    html += '<div class="crypto-stat-subvalue">All-time earned</div></div>';

    html += '<div class="crypto-stat"><div class="crypto-stat-icon">🪙</div>';
    html += '<div class="crypto-stat-label">Cost Basis</div>';
    html += '<div class="crypto-stat-value">' + formatCurrency(totalCost) + '</div>';
    html += '<div class="crypto-stat-subvalue">Total invested</div></div>';

    container.innerHTML = html;
}

function createCryptoAllocationChart() {
    var ctx = document.getElementById('cryptoAllocationChart');
    if (!ctx) return;

    var allHoldings = getHoldingsDetail();
    var cryptoHoldings = allHoldings.filter(function (h) {
        return (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency') && h.current_value > 0;
    });

    if (cryptoHoldings.length === 0) {
        ctx.parentElement.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 40px;">No crypto holdings</p>';
        return;
    }

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
        data: { labels: labels, datasets: [{ data: values, backgroundColor: colors.slice(0, labels.length), borderWidth: 0 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#888', padding: 12 } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            return context.label + ': ' + formatCurrency(context.raw) + ' (' + ((context.raw / total) * 100).toFixed(1) + '%)';
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

    var cryptoValue = 0, traditionalValue = 0;
    var allHoldings = getHoldingsDetail();

    allHoldings.forEach(function (h) {
        if (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency') {
            cryptoValue += h.current_value || 0;
        } else {
            traditionalValue += h.current_value || 0;
        }
    });

    var cashBalances = getCashBalances();
    traditionalValue += cashBalances.reduce(function (sum, c) { return sum + (c.balance || 0); }, 0);

    new Chart(ctx, {
        type: 'doughnut',
        data: { labels: ['Crypto', 'Traditional'], datasets: [{ data: [cryptoValue, traditionalValue], backgroundColor: ['#f7931a', '#60a5fa'], borderWidth: 0 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#888', padding: 12 } },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                            return context.label + ': ' + formatCurrency(context.raw) + ' (' + ((context.raw / total) * 100).toFixed(1) + '%)';
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

    var cumulative = [];
    var running = 0;
    values.forEach(function (v) { running += v; cumulative.push(running); });

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
                { label: 'Monthly Rewards', data: values, backgroundColor: '#fbbf24', borderRadius: 4, order: 2 },
                { label: 'Cumulative', data: cumulative, type: 'line', borderColor: '#22c55e', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 2, tension: 0.3, order: 1 }
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

function createCryptoHoldingsTable() {
    var tbody = document.getElementById('cryptoHoldingsBody');
    if (!tbody) return;

    var allHoldings = getHoldingsDetail();
    var cryptoHoldings = allHoldings.filter(function (h) {
        return (isCryptoSymbol(h.symbol) || isCryptoAccount(h.account) || h.sector === 'Cryptocurrency') && h.current_value > 0;
    });

    if (cryptoHoldings.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-secondary);">No crypto holdings</td></tr>';
        return;
    }

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
        html += '<td style="text-align: right;" class="' + (gain >= 0 ? 'positive' : 'negative') + '">' + formatCurrency(gain) + '</td>';
        html += '<td style="text-align: right;" class="' + (gainPct >= 0 ? 'positive' : 'negative') + '">' + (gainPct >= 0 ? '+' : '') + gainPct.toFixed(1) + '%</td>';
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
        html += '<div class="staking-year"><span class="staking-year-label">' + year + '</span>';
        html += '<span class="staking-year-value">+' + formatCurrency(stakingByYear[year]) + '</span></div>';
    });

    container.innerHTML = html;
}

function createCryptoTxSummary() {
    var container = document.getElementById('cryptoTxSummary');
    if (!container) return;

    var stats = { buys: 0, sells: 0, buyCount: 0, sellCount: 0, staking: 0, stakingCount: 0 };

    getMasterTransactions().forEach(function (t) {
        if (!isCryptoSymbol(t.symbol) && !isCryptoAccount(t.account)) return;

        if (t.action === 'Buy') { stats.buys += Math.abs(t.amount) || 0; stats.buyCount++; }
        else if (t.action === 'Sell') { stats.sells += Math.abs(t.amount) || 0; stats.sellCount++; }
        else if (t.action === 'Staking') { stats.staking += t.amount || 0; stats.stakingCount++; }
    });

    var html = '';
    html += '<div class="crypto-tx-row"><span class="crypto-tx-label">Total Buys (' + stats.buyCount + ' transactions)</span>';
    html += '<span class="crypto-tx-value buys">' + formatCurrency(stats.buys) + '</span></div>';
    html += '<div class="crypto-tx-row"><span class="crypto-tx-label">Total Sells (' + stats.sellCount + ' transactions)</span>';
    html += '<span class="crypto-tx-value sells">' + formatCurrency(stats.sells) + '</span></div>';
    html += '<div class="crypto-tx-row"><span class="crypto-tx-label">Net Investment</span>';
    html += '<span class="crypto-tx-value">' + formatCurrency(stats.buys - stats.sells) + '</span></div>';
    html += '<div class="crypto-tx-row"><span class="crypto-tx-label">Staking Rewards (' + stats.stakingCount + ')</span>';
    html += '<span class="crypto-tx-value" style="color: var(--accent-green);">+' + formatCurrency(stats.staking) + '</span></div>';

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
        var actionClass = t.action === 'Buy' ? 'negative' : (t.action === 'Sell' || t.action === 'Staking') ? 'positive' : '';

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
