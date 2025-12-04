/**
 * Tax Module
 * Tax loss harvesting, realized gains, holding period analysis
 */

// Store for year filter state
var selectedTaxYear = new Date().getFullYear().toString();

// Create tax section with loss harvesting opportunities
function createTaxSection() {
    var holdings = getHoldingsDetail();
    var realizedGains = typeof realizedGainsData !== 'undefined' ? realizedGainsData : [];

    createTaxSummaryCards(holdings, realizedGains);
    createWashSaleWarnings(holdings, realizedGains);
    createTaxLossHarvestingTable(holdings, realizedGains);
    initRealizedGainsYearFilter(realizedGains);
    createRealizedGainsTable(realizedGains, selectedTaxYear);
    createGainsBreakdownChart(holdings);
    createHoldingPeriodChart(holdings);
}

// 1. YTD Tax Summary Cards
function createTaxSummaryCards(holdings, realizedGains) {
    var container = document.getElementById('taxSummaryCards');
    if (!container) return;

    var currentYear = new Date().getFullYear().toString();
    var today = new Date();

    // Calculate realized gains for current year
    var ytdGains = realizedGains.filter(function (g) {
        return g.Date && g.Date.startsWith(currentYear);
    });

    var totalRealizedGain = 0;
    var shortTermGain = 0;
    var longTermGain = 0;

    ytdGains.forEach(function (g) {
        var gain = g.RealizedGain || 0;
        totalRealizedGain += gain;

        // Determine if short-term or long-term based on HoldingPeriod field or date comparison
        if (g.HoldingPeriod === 'Long-term') {
            longTermGain += gain;
        } else {
            shortTermGain += gain;
        }
    });

    // Calculate harvestable losses (unrealized losses)
    var harvestableLosses = 0;
    holdings.forEach(function (h) {
        if (h.unrealized_gain < 0) {
            harvestableLosses += h.unrealized_gain;
        }
    });

    // Estimate tax impact (rough calculation)
    // Short-term: assume 32% marginal rate, Long-term: 15%
    var shortTermTax = shortTermGain > 0 ? shortTermGain * 0.32 : shortTermGain * 0.32;
    var longTermTax = longTermGain > 0 ? longTermGain * 0.15 : longTermGain * 0.15;
    var estimatedTax = shortTermTax + longTermTax;

    // Potential tax savings from harvesting
    var potentialSavings = Math.abs(harvestableLosses) * 0.25; // Blended rate estimate

    var html = '';

    // Card 1: Total YTD Realized
    var totalClass = totalRealizedGain >= 0 ? 'positive' : 'negative';
    var totalIcon = totalRealizedGain >= 0 ? '📈' : '📉';
    html += '<div class="tax-summary-card">';
    html += '<div class="tax-card-icon">' + totalIcon + '</div>';
    html += '<div class="tax-card-label">YTD Realized Gain/Loss</div>';
    html += '<div class="tax-card-value ' + totalClass + '">' + formatCurrency(totalRealizedGain) + '</div>';
    html += '<div class="tax-card-subvalue">' + ytdGains.length + ' transactions</div>';
    html += '</div>';

    // Card 2: Short-Term
    var stClass = shortTermGain >= 0 ? 'positive' : 'negative';
    html += '<div class="tax-summary-card">';
    html += '<div class="tax-card-icon">⚡</div>';
    html += '<div class="tax-card-label">Short-Term (&lt;1 yr)</div>';
    html += '<div class="tax-card-value ' + stClass + '">' + formatCurrency(shortTermGain) + '</div>';
    html += '<div class="tax-card-subvalue">~32% tax rate</div>';
    html += '</div>';

    // Card 3: Long-Term
    var ltClass = longTermGain >= 0 ? 'positive' : 'negative';
    html += '<div class="tax-summary-card">';
    html += '<div class="tax-card-icon">🏦</div>';
    html += '<div class="tax-card-label">Long-Term (&gt;1 yr)</div>';
    html += '<div class="tax-card-value ' + ltClass + '">' + formatCurrency(longTermGain) + '</div>';
    html += '<div class="tax-card-subvalue">~15% tax rate</div>';
    html += '</div>';

    // Card 4: Estimated Tax
    var taxClass = estimatedTax >= 0 ? 'negative' : 'positive'; // Owing tax is "negative" for user
    var taxIcon = estimatedTax >= 0 ? '💸' : '💰';
    html += '<div class="tax-summary-card">';
    html += '<div class="tax-card-icon">' + taxIcon + '</div>';
    html += '<div class="tax-card-label">Est. Tax Liability</div>';
    html += '<div class="tax-card-value ' + taxClass + '">' + formatCurrency(Math.abs(estimatedTax)) + '</div>';
    html += '<div class="tax-card-subvalue">' + (estimatedTax >= 0 ? 'Owed' : 'Refund potential') + '</div>';
    html += '</div>';

    // Card 5: Harvestable Losses
    html += '<div class="tax-summary-card">';
    html += '<div class="tax-card-icon">🌾</div>';
    html += '<div class="tax-card-label">Harvestable Losses</div>';
    html += '<div class="tax-card-value negative">' + formatCurrency(harvestableLosses) + '</div>';
    html += '<div class="tax-card-subvalue">~' + formatCurrency(potentialSavings) + ' potential savings</div>';
    html += '</div>';

    container.innerHTML = html;
}

// 2. Wash Sale Warnings
function createWashSaleWarnings(holdings, realizedGains) {
    var container = document.getElementById('washSaleWarnings');
    if (!container) return;

    var today = new Date();
    var thirtyDaysAgo = new Date(today);
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    // Find recently sold symbols (within last 30 days)
    var recentSales = {};
    realizedGains.forEach(function (g) {
        if (!g.Date || !g.Symbol) return;
        var saleDate = new Date(g.Date);
        if (saleDate >= thirtyDaysAgo && saleDate <= today) {
            if (!recentSales[g.Symbol]) {
                recentSales[g.Symbol] = [];
            }
            recentSales[g.Symbol].push({
                date: g.Date,
                quantity: g.Quantity,
                gain: g.RealizedGain,
                saleDate: saleDate
            });
        }
    });

    // Check if any current holdings match recently sold symbols (wash sale risk)
    var warnings = [];
    holdings.forEach(function (h) {
        var symbol = h.symbol;
        if (recentSales[symbol]) {
            recentSales[symbol].forEach(function (sale) {
                // If the sale was at a loss, it's a wash sale risk
                if (sale.gain < 0) {
                    var washSaleEndDate = new Date(sale.saleDate);
                    washSaleEndDate.setDate(washSaleEndDate.getDate() + 30);

                    warnings.push({
                        symbol: symbol,
                        account: h.account,
                        saleDate: sale.date,
                        loss: sale.gain,
                        washSaleUntil: washSaleEndDate,
                        daysRemaining: Math.ceil((washSaleEndDate - today) / (1000 * 60 * 60 * 24)),
                        currentHolding: h.quantity
                    });
                }
            });
        }
    });

    if (warnings.length === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';
    var html = '<div class="wash-sale-alert">';
    html += '<div class="wash-sale-header">';
    html += '<span class="wash-sale-icon">⚠️</span>';
    html += '<span class="wash-sale-title">Wash Sale Warning</span>';
    html += '</div>';
    html += '<div class="wash-sale-content">';
    html += '<p>The following positions may trigger wash sale rules if sold at a loss:</p>';
    html += '<ul class="wash-sale-list">';

    warnings.forEach(function (w) {
        html += '<li>';
        html += '<strong>' + w.symbol + '</strong> (' + w.account + '): ';
        html += 'Sold on ' + w.saleDate + ' with ' + formatCurrency(w.loss) + ' loss. ';
        html += 'Wash sale period ends <strong>' + w.washSaleUntil.toLocaleDateString() + '</strong> ';
        html += '(' + w.daysRemaining + ' days remaining). ';
        html += 'Currently holding ' + formatNumber(w.currentHolding, 4) + ' shares.';
        html += '</li>';
    });

    html += '</ul>';
    html += '<p class="wash-sale-note">Buying or selling this security within 30 days before or after a loss sale may disallow the loss deduction.</p>';
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;
}

// 3. Year Filter for Realized Gains
function initRealizedGainsYearFilter(realizedGains) {
    var select = document.getElementById('realizedGainsYearFilter');
    if (!select) return;

    // Get unique years from realized gains
    var years = {};
    realizedGains.forEach(function (g) {
        if (g.Date) {
            var year = g.Date.substring(0, 4);
            years[year] = true;
        }
    });

    var yearList = Object.keys(years).sort().reverse();
    var currentYear = new Date().getFullYear().toString();

    var html = '';
    yearList.forEach(function (year) {
        var selected = year === currentYear ? ' selected' : '';
        html += '<option value="' + year + '"' + selected + '>' + year + '</option>';
    });

    // Add "All Years" option
    html += '<option value="all">All Years</option>';

    select.innerHTML = html;

    // Add event listener
    select.addEventListener('change', function () {
        selectedTaxYear = this.value;
        createRealizedGainsTable(realizedGains, selectedTaxYear);
    });
}

function createTaxLossHarvestingTable(holdings, realizedGains) {
    var tbody = document.getElementById('taxLossBody');
    if (!tbody) return;

    // Build a map of recently sold symbols (for wash sale detection)
    var today = new Date();
    var thirtyDaysAgo = new Date(today);
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    var recentLossSales = {};
    realizedGains.forEach(function (g) {
        if (!g.Date || !g.Symbol) return;
        var saleDate = new Date(g.Date);
        if (saleDate >= thirtyDaysAgo && g.RealizedGain < 0) {
            recentLossSales[g.Symbol] = saleDate;
        }
    });

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

    losses.forEach(function (h) {
        totalLoss += h.unrealized_gain;

        var buyDate = h.first_buy_date ? new Date(h.first_buy_date) : null;
        var holdingDays = buyDate ? Math.floor((today - buyDate) / (1000 * 60 * 60 * 24)) : 0;
        var holdingPeriod = holdingDays > 365 ? 'Long-term' : 'Short-term';

        // Wash sale end date: 30 days from TODAY if sold now
        var washSaleEnd = new Date(today);
        washSaleEnd.setDate(washSaleEnd.getDate() + 30);

        // Check if this symbol was recently sold at a loss (wash sale warning)
        var hasWashSaleRisk = recentLossSales[h.symbol] !== undefined;
        var rowClass = hasWashSaleRisk ? ' class="wash-sale-risk-row"' : '';
        var washSaleNote = '';
        if (hasWashSaleRisk) {
            var previousSaleDate = recentLossSales[h.symbol];
            var previousWashEnd = new Date(previousSaleDate);
            previousWashEnd.setDate(previousWashEnd.getDate() + 30);
            washSaleNote = '<span class="wash-sale-badge" title="Recently sold at loss on ' + previousSaleDate.toLocaleDateString() + '">⚠️ Active</span>';
        }

        html += '<tr' + rowClass + '>';
        html += '<td><span class="symbol-badge">' + h.symbol + '</span>' + washSaleNote + '</td>';
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

function createRealizedGainsTable(realizedGains, year) {
    var tbody = document.getElementById('realizedGainsBody');
    if (!tbody) return;

    var filteredGains;
    if (year === 'all') {
        filteredGains = realizedGains.slice();
    } else {
        filteredGains = realizedGains.filter(function (g) {
            return g.Date && g.Date.startsWith(year);
        });
    }

    filteredGains.sort(function (a, b) {
        return new Date(b.Date) - new Date(a.Date);
    });

    if (filteredGains.length === 0) {
        var yearLabel = year === 'all' ? '' : ' in ' + year;
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #888;">No realized gains/losses' + yearLabel + '</td></tr>';
        return;
    }

    var html = '';
    var totalGain = 0;
    var shortTermTotal = 0;
    var longTermTotal = 0;

    filteredGains.forEach(function (g) {
        var gain = g.RealizedGain || 0;
        totalGain += gain;
        var gainClass = gain >= 0 ? 'positive' : 'negative';
        var term = g.HoldingPeriod === 'Long-term' ? 'Long' : 'Short';

        if (g.HoldingPeriod === 'Long-term') {
            longTermTotal += gain;
        } else {
            shortTermTotal += gain;
        }

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

    // Summary rows
    var yearLabel = year === 'all' ? 'All-Time' : year;
    var totalClass = totalGain >= 0 ? 'positive' : 'negative';

    // Short-term subtotal
    var stClass = shortTermTotal >= 0 ? 'positive' : 'negative';
    html += '<tr class="subtotal-row" style="background: rgba(255, 255, 255, 0.03);">';
    html += '<td colspan="5"><strong>Short-Term Total</strong></td>';
    html += '<td class="number ' + stClass + '"><strong>' + formatCurrency(shortTermTotal) + '</strong></td>';
    html += '<td></td>';
    html += '</tr>';

    // Long-term subtotal
    var ltClass = longTermTotal >= 0 ? 'positive' : 'negative';
    html += '<tr class="subtotal-row" style="background: rgba(255, 255, 255, 0.03);">';
    html += '<td colspan="5"><strong>Long-Term Total</strong></td>';
    html += '<td class="number ' + ltClass + '"><strong>' + formatCurrency(longTermTotal) + '</strong></td>';
    html += '<td></td>';
    html += '</tr>';

    // Grand total
    html += '<tr class="total-row" style="background: rgba(255, 255, 255, 0.05); font-weight: bold;">';
    html += '<td colspan="5"><strong>' + yearLabel + ' Total Realized Gains/Losses</strong></td>';
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
