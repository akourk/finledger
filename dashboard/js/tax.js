/**
 * Tax Module
 * Tax loss harvesting, realized gains, holding period analysis
 */

// Create tax section with loss harvesting opportunities
function createTaxSection() {
    var holdings = getHoldingsDetail();
    var realizedGains = typeof realizedGainsData !== 'undefined' ? realizedGainsData : [];

    createTaxLossHarvestingTable(holdings);
    createRealizedGainsTable(realizedGains);
    createGainsBreakdownChart(holdings);
    createHoldingPeriodChart(holdings);
}

function createTaxLossHarvestingTable(holdings) {
    var tbody = document.getElementById('taxLossBody');
    if (!tbody) return;

    var losses = holdings.filter(function (h) {
        return h.unrealized_gain < -10;
    }).sort(function (a, b) {
        return a.unrealized_gain - b.unrealized_gain;
    });

    if (losses.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #888;">No tax loss harvesting opportunities (no positions with losses > $10)</td></tr>';
        return;
    }

    var html = '';
    var totalLoss = 0;
    var today = new Date();

    losses.forEach(function (h) {
        totalLoss += h.unrealized_gain;

        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        var holdingDays = buyDate ? Math.floor((today - buyDate) / (1000 * 60 * 60 * 24)) : 0;
        var holdingPeriod = holdingDays > 365 ? 'Long-term' : 'Short-term';

        var washSaleEnd = new Date(today);
        washSaleEnd.setDate(washSaleEnd.getDate() + 30);

        html += '<tr>';
        html += '<td><span class="symbol-badge">' + h.symbol + '</span></td>';
        html += '<td>' + h.account + '</td>';
        html += '<td class="number">' + formatNumber(h.quantity, 4) + '</td>';
        html += '<td class="number">' + formatCurrency(h.cost_basis) + '</td>';
        html += '<td class="number">' + formatCurrency(h.current_value) + '</td>';
        html += '<td class="number negative">' + formatCurrency(h.unrealized_gain) + '</td>';
        html += '<td>' + holdingPeriod + '</td>';
        html += '<td>' + washSaleEnd.toLocaleDateString() + '</td>';
        html += '</tr>';
    });

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

    var currentYear = new Date().getFullYear().toString();
    var thisYearGains = realizedGains.filter(function (g) {
        return g.Date && g.Date.startsWith(currentYear);
    }).sort(function (a, b) {
        return new Date(b.Date) - new Date(a.Date);
    });

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
    });

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
    var shortTermGain = 0, shortTermLoss = 0, longTermGain = 0, longTermLoss = 0;

    holdings.forEach(function (h) {
        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        var isLongTerm = buyDate && (today - buyDate) / (1000 * 60 * 60 * 24) > 365;
        var gain = h.unrealized_gain || 0;

        if (isLongTerm) {
            if (gain >= 0) longTermGain += gain;
            else longTermLoss += Math.abs(gain);
        } else {
            if (gain >= 0) shortTermGain += gain;
            else shortTermLoss += Math.abs(gain);
        }
    });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Short-Term', 'Long-Term'],
            datasets: [
                { label: 'Gains', data: [shortTermGain, longTermGain], backgroundColor: '#4ade80', borderRadius: 4 },
                { label: 'Losses', data: [-shortTermLoss, -longTermLoss], backgroundColor: '#f87171', borderRadius: 4 }
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
                            return context.dataset.label + ': ' + formatCurrency(Math.abs(context.raw));
                        }
                    }
                }
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

function createHoldingPeriodChart(holdings) {
    var ctx = document.getElementById('holdingPeriodChart');
    if (!ctx) return;

    var today = new Date();
    var periods = {
        '< 1 month': { value: 0, gain: 0 },
        '1-3 months': { value: 0, gain: 0 },
        '3-6 months': { value: 0, gain: 0 },
        '6-12 months': { value: 0, gain: 0 },
        '1-2 years': { value: 0, gain: 0 },
        '2+ years': { value: 0, gain: 0 }
    };

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
    });

    var labels = Object.keys(periods);
    var gains = labels.map(function (k) { return periods[k].gain; });
    var colors = gains.map(function (g) { return g >= 0 ? '#4ade80' : '#f87171'; });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{ label: 'Unrealized Gain/Loss', data: gains, backgroundColor: colors, borderRadius: 4 }]
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
                            return (val >= 0 ? 'Gain: +' : 'Loss: ') + formatCurrency(Math.abs(val));
                        },
                        afterLabel: function (context) {
                            var key = context.label;
                            return 'Value: ' + formatCurrency(periods[key].value);
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#888', maxRotation: 45 }, grid: { display: false } },
                y: {
                    ticks: { color: '#888', callback: function (val) { return formatCurrency(val); } },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}
