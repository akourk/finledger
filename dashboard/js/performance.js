/**
 * Performance Module
 * Multi-period returns analysis for assets, accounts, sectors, and portfolio
 */

// Performance period labels (matching Python PERFORMANCE_PERIODS)
var PERIOD_LABELS = {
    '1D': '1 Day',
    '1W': '1 Week',
    '2W': '2 Weeks',
    '1M': '1 Month',
    '3M': '3 Months',
    '6M': '6 Months',
    '1Y': '1 Year',
    '2Y': '2 Years',
    '5Y': '5 Years'
};

var PERIOD_ORDER = ['1D', '1W', '2W', '1M', '3M', '6M', '1Y', '2Y', '5Y'];

// Get performance data
function getPerformanceData() {
    if (typeof performanceData !== 'undefined') {
        return performanceData;
    }
    return { portfolio: {}, accounts: [], sectors: [], assets: [], periods: [] };
}

// Format return with color
function formatReturnCell(value) {
    if (value === null || value === undefined || isNaN(value)) {
        return '<span class="muted">—</span>';
    }
    var cls = value >= 0 ? 'positive' : 'negative';
    var sign = value >= 0 ? '+' : '';
    return '<span class="' + cls + '">' + sign + value.toFixed(2) + '%</span>';
}

// Get heatmap color for return value
function getReturnColor(value, maxAbs) {
    if (value === null || value === undefined || isNaN(value)) {
        return 'rgba(128, 128, 128, 0.3)';
    }

    maxAbs = maxAbs || 50;
    var normalized = Math.max(-1, Math.min(1, value / maxAbs));

    if (normalized >= 0) {
        // Green gradient
        var intensity = normalized;
        return 'rgba(34, 197, 94, ' + (0.2 + intensity * 0.8) + ')';
    } else {
        // Red gradient
        var intensity = -normalized;
        return 'rgba(239, 68, 68, ' + (0.2 + intensity * 0.8) + ')';
    }
}

// Create portfolio performance summary cards
function createPortfolioPerformanceCards() {
    var container = document.getElementById('portfolioPerformance');
    if (!container) return;

    var data = getPerformanceData();
    var portfolio = data.portfolio || {};

    if (!portfolio.TotalValue) {
        container.innerHTML = '<p class="muted">No performance data available</p>';
        return;
    }

    var html = '<div class="portfolio-summary-hero">';
    html += '<div class="hero-main">';
    html += '<div class="hero-label">Portfolio Value</div>';
    html += '<div class="hero-value">' + formatCurrency(portfolio.TotalValue) + '</div>';
    html += '</div>';
    html += '<div class="hero-return">';
    html += '<div class="hero-label">Total Return</div>';
    html += '<div class="hero-value ' + (portfolio.TotalReturn >= 0 ? 'positive' : 'negative') + '">';
    html += (portfolio.TotalReturn >= 0 ? '+' : '') + (portfolio.TotalReturn || 0).toFixed(2) + '%';
    html += '</div>';
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;
}

// Create performance heatmap for assets
function createAssetPerformanceHeatmap() {
    var container = document.getElementById('assetPerformanceHeatmap');
    if (!container) return;

    var data = getPerformanceData();
    var assets = data.assets || [];

    if (assets.length === 0) {
        container.innerHTML = '<p class="muted">No asset performance data available</p>';
        return;
    }

    // Sort by current value and take top 25
    var topAssets = assets.slice().sort(function (a, b) {
        return (b.CurrentValue || 0) - (a.CurrentValue || 0);
    }).slice(0, 25);

    // Prepare data for ECharts heatmap
    var symbols = topAssets.map(function (a) { return a.Symbol; }).reverse();
    var xLabels = PERIOD_ORDER.map(function (p) { return PERIOD_LABELS[p]; });

    var heatmapData = [];
    topAssets.forEach(function (asset, idx) {
        var yIdx = topAssets.length - 1 - idx;
        PERIOD_ORDER.forEach(function (period, xIdx) {
            var value = asset['Return_' + period];
            heatmapData.push([xIdx, yIdx, value !== null && value !== undefined ? value : null]);
        });
    });

    // Dispose existing chart
    var existingChart = echarts.getInstanceByDom(container);
    if (existingChart) existingChart.dispose();

    var chart = echarts.init(container);

    var option = {
        tooltip: {
            position: 'top',
            formatter: function (params) {
                var symbol = symbols[params.value[1]];
                var period = xLabels[params.value[0]];
                var value = params.value[2];
                if (value === null) return symbol + ' - ' + period + ': N/A';
                return '<strong>' + symbol + '</strong><br/>' + period + ': ' +
                    (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
            }
        },
        grid: {
            left: 80,
            right: 100,
            top: 30,
            bottom: 60
        },
        xAxis: {
            type: 'category',
            data: xLabels,
            splitArea: { show: true },
            axisLabel: { color: '#888', rotate: 45 },
            axisLine: { lineStyle: { color: '#444' } }
        },
        yAxis: {
            type: 'category',
            data: symbols,
            splitArea: { show: true },
            axisLabel: { color: '#888' },
            axisLine: { lineStyle: { color: '#444' } }
        },
        visualMap: {
            min: -50,
            max: 50,
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
            name: 'Returns',
            type: 'heatmap',
            data: heatmapData,
            label: {
                show: true,
                formatter: function (params) {
                    var val = params.value[2];
                    if (val === null) return '';
                    return (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
                },
                color: '#fff',
                fontSize: 10
            }
        }]
    };

    chart.setOption(option);
    window.addEventListener('resize', function () { chart.resize(); });
}

// Create account performance table
function createAccountPerformanceTable() {
    var container = document.getElementById('accountPerformanceBody');
    if (!container) return;

    var data = getPerformanceData();
    var accounts = data.accounts || [];
    var portfolio = data.portfolio || {};

    if (accounts.length === 0) {
        container.innerHTML = '<tr><td colspan="12" class="muted">No account data</td></tr>';
        return;
    }

    var html = '';

    // Portfolio total row first (highlighted)
    html += '<tr class="portfolio-total-row">';
    html += '<td><strong>Portfolio Total</strong></td>';
    html += '<td class="number"><strong>' + formatCurrency(portfolio.TotalValue) + '</strong></td>';
    html += '<td class="number"><strong>' + formatReturnCell(portfolio.TotalReturn) + '</strong></td>';
    PERIOD_ORDER.forEach(function (period) {
        html += '<td class="number"><strong>' + formatReturnCell(portfolio['Return_' + period]) + '</strong></td>';
    });
    html += '</tr>';

    // Individual accounts
    accounts.forEach(function (account) {
        html += '<tr>';
        html += '<td>' + account.Account + '</td>';
        html += '<td class="number">' + formatCurrency(account.CurrentValue) + '</td>';
        html += '<td class="number">' + formatReturnCell(account.TotalReturn) + '</td>';

        PERIOD_ORDER.forEach(function (period) {
            html += '<td class="number">' + formatReturnCell(account['Return_' + period]) + '</td>';
        });

        html += '</tr>';
    });

    container.innerHTML = html;
}

// Create sector performance table
function createSectorPerformanceTable() {
    var container = document.getElementById('sectorPerformanceBody');
    if (!container) return;

    var data = getPerformanceData();
    var sectors = data.sectors || [];

    if (sectors.length === 0) {
        container.innerHTML = '<tr><td colspan="12" class="muted">No sector data</td></tr>';
        return;
    }

    var html = '';
    sectors.forEach(function (sector) {
        html += '<tr>';
        html += '<td>' + sector.Sector + '</td>';
        html += '<td class="number">' + formatCurrency(sector.CurrentValue) + '</td>';
        html += '<td class="number">' + formatReturnCell(sector.TotalReturn) + '</td>';

        PERIOD_ORDER.forEach(function (period) {
            html += '<td class="number">' + formatReturnCell(sector['Return_' + period]) + '</td>';
        });

        html += '</tr>';
    });

    container.innerHTML = html;
}

// Create top/bottom performers comparison
function createPerformersComparison() {
    createTopPerformers();
    createBottomPerformers();
}

// Create top performers card
function createTopPerformers() {
    var container = document.getElementById('topPerformers');
    if (!container) return;

    var data = getPerformanceData();
    var assets = data.assets || [];

    if (assets.length === 0) {
        container.innerHTML = '<p class="muted">No data</p>';
        return;
    }

    var html = '<div class="performers-grid">';

    ['1M', '3M', '1Y'].forEach(function (period) {
        var key = 'Return_' + period;
        var validAssets = assets.filter(function (a) {
            return a[key] !== null && a[key] !== undefined && a.CurrentValue > 100;
        });

        if (validAssets.length < 3) return;

        var sorted = validAssets.slice().sort(function (a, b) {
            return b[key] - a[key];
        });

        var topN = sorted.slice(0, 8);

        html += '<div class="performers-column">';
        html += '<div class="period-header">' + PERIOD_LABELS[period] + '</div>';
        topN.forEach(function (a, idx) {
            html += '<div class="performer-row">';
            html += '<span class="symbol-badge">' + a.Symbol + '</span>';
            html += '<span class="positive">+' + a[key].toFixed(1) + '%</span>';
            html += '</div>';
        });
        html += '</div>';
    });

    html += '</div>';
    container.innerHTML = html;
}

// Create bottom performers card
function createBottomPerformers() {
    var container = document.getElementById('bottomPerformers');
    if (!container) return;

    var data = getPerformanceData();
    var assets = data.assets || [];

    if (assets.length === 0) {
        container.innerHTML = '<p class="muted">No data</p>';
        return;
    }

    var html = '<div class="performers-grid">';

    ['1M', '3M', '1Y'].forEach(function (period) {
        var key = 'Return_' + period;
        var validAssets = assets.filter(function (a) {
            return a[key] !== null && a[key] !== undefined && a.CurrentValue > 100;
        });

        if (validAssets.length < 3) return;

        var sorted = validAssets.slice().sort(function (a, b) {
            return a[key] - b[key];
        });

        var bottomN = sorted.slice(0, 8);

        html += '<div class="performers-column">';
        html += '<div class="period-header">' + PERIOD_LABELS[period] + '</div>';
        bottomN.forEach(function (a, idx) {
            html += '<div class="performer-row">';
            html += '<span class="symbol-badge">' + a.Symbol + '</span>';
            var val = a[key];
            html += '<span class="' + (val >= 0 ? 'positive' : 'negative') + '">' + (val >= 0 ? '+' : '') + val.toFixed(1) + '%</span>';
            html += '</div>';
        });
        html += '</div>';
    });

    html += '</div>';
    container.innerHTML = html;
}

// Create period comparison bar chart
function createPeriodComparisonChart() {
    var container = document.getElementById('periodComparisonChart');
    if (!container) return;

    var data = getPerformanceData();
    var portfolio = data.portfolio || {};

    var labels = [];
    var values = [];
    var colors = [];

    PERIOD_ORDER.forEach(function (period) {
        var key = 'Return_' + period;
        var value = portfolio[key];
        if (value !== null && value !== undefined) {
            labels.push(PERIOD_LABELS[period]);
            values.push(value);
            colors.push(value >= 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)');
        }
    });

    if (values.length === 0) return;

    var ctx = container.getContext('2d');

    // Destroy existing chart
    if (container._chart) {
        container._chart.destroy();
    }

    container._chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Portfolio Return',
                data: values,
                backgroundColor: colors,
                borderColor: colors.map(function (c) { return c.replace('0.8', '1'); }),
                borderWidth: 1
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
                            var value = context.parsed.y;
                            return (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.1)' },
                    ticks: {
                        color: '#888',
                        callback: function (value) { return value + '%'; }
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#888' }
                }
            }
        }
    });
}

// Create full asset performance table
function createAssetPerformanceTable() {
    var container = document.getElementById('assetPerformanceBody');
    if (!container) return;

    var data = getPerformanceData();
    var assets = data.assets || [];

    if (assets.length === 0) {
        container.innerHTML = '<tr><td colspan="13" class="muted">No asset data</td></tr>';
        return;
    }

    // Sort by value by default
    var sorted = assets.slice().sort(function (a, b) {
        return (b.CurrentValue || 0) - (a.CurrentValue || 0);
    });

    var html = '';
    sorted.forEach(function (asset) {
        html += '<tr>';
        html += '<td><span class="symbol-badge">' + asset.Symbol + '</span></td>';
        html += '<td>' + (asset.Account || '') + '</td>';
        html += '<td class="number">' + formatCurrency(asset.CurrentValue) + '</td>';
        html += '<td class="number">' + formatReturnCell(asset.TotalReturn) + '</td>';

        PERIOD_ORDER.forEach(function (period) {
            html += '<td class="number">' + formatReturnCell(asset['Return_' + period]) + '</td>';
        });

        html += '</tr>';
    });

    container.innerHTML = html;
}

// Search/filter functionality
function initPerformanceSearch() {
    var searchInput = document.getElementById('performanceSearch');
    if (!searchInput) return;

    searchInput.addEventListener('input', function () {
        var query = this.value.toLowerCase().trim();
        var rows = document.querySelectorAll('#assetPerformanceBody tr');

        rows.forEach(function (row) {
            var symbol = row.cells[0] ? row.cells[0].textContent.toLowerCase() : '';
            var account = row.cells[1] ? row.cells[1].textContent.toLowerCase() : '';

            if (query === '' || symbol.indexOf(query) !== -1 || account.indexOf(query) !== -1) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    });
}

// Initialize performance tab
function initPerformance() {
    createPortfolioPerformanceCards();
    createPeriodComparisonChart();
    createPerformersComparison();
    createBenchmarkComparison();
    createBenchmarkChart();
    createROIChart();
    createValueVsCostBasisChart();
    createRiskMetrics();
    createBenchmarkReturnsTable();
    createAccountPerformanceTable();
    createSectorPerformanceTable();
    createAssetPerformanceHeatmap();
    createAssetPerformanceTable();
    initPerformanceSearch();
}

// Get benchmark data
function getBenchmarkData() {
    if (typeof benchmarkData !== 'undefined') {
        return benchmarkData;
    }
    return {};
}

// Create benchmark comparison cards
function createBenchmarkComparison() {
    var container = document.getElementById('benchmarkComparison');
    if (!container) return;

    var data = getBenchmarkData();

    if (!data.benchmark_symbol) {
        container.innerHTML = '<p class="muted">No benchmark data available. Run the portfolio aggregator to generate.</p>';
        return;
    }

    var html = '<div class="benchmark-cards">';

    // CAGR card (new)
    var cagr = data.cagr;
    var cagrClass = cagr >= 0 ? 'positive' : 'negative';
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">CAGR</div>';
    html += '<div class="benchmark-value ' + cagrClass + '">' + (cagr !== null ? (cagr >= 0 ? '+' : '') + cagr.toFixed(2) + '%' : '—') + '</div>';
    html += '<div class="benchmark-desc">Compound Annual Growth Rate</div>';
    html += '</div>';

    // Current ROI card (new)
    var roi = data.current_roi;
    var roiClass = roi >= 0 ? 'positive' : 'negative';
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">Total ROI</div>';
    html += '<div class="benchmark-value ' + roiClass + '">' + (roi !== null ? (roi >= 0 ? '+' : '') + roi.toFixed(2) + '%' : '—') + '</div>';
    html += '<div class="benchmark-desc">Return on current cost basis</div>';
    html += '</div>';

    // Alpha card
    var alpha = data.alpha;
    var alphaClass = alpha >= 0 ? 'positive' : 'negative';
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">Alpha (Annual)</div>';
    html += '<div class="benchmark-value ' + alphaClass + '">' + (alpha !== null ? (alpha >= 0 ? '+' : '') + alpha.toFixed(2) + '%' : '—') + '</div>';
    html += '<div class="benchmark-desc">Excess return vs ' + data.benchmark_name + '</div>';
    html += '</div>';

    // Beta card
    var beta = data.beta;
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">Beta</div>';
    html += '<div class="benchmark-value">' + (beta !== null ? beta.toFixed(2) : '—') + '</div>';
    html += '<div class="benchmark-desc">' + (beta > 1 ? 'More volatile than market' : beta < 1 ? 'Less volatile than market' : 'Market-like volatility') + '</div>';
    html += '</div>';

    // Sharpe Ratio card
    var sharpe = data.sharpe_ratio;
    var sharpeClass = sharpe >= 1 ? 'positive' : sharpe >= 0.5 ? '' : 'negative';
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">Sharpe Ratio</div>';
    html += '<div class="benchmark-value ' + sharpeClass + '">' + (sharpe !== null ? sharpe.toFixed(2) : '—') + '</div>';
    html += '<div class="benchmark-desc">' + (sharpe >= 1 ? 'Excellent risk-adjusted' : sharpe >= 0.5 ? 'Good risk-adjusted' : 'Below average') + '</div>';
    html += '</div>';

    // R-Squared card
    var rSquared = data.r_squared;
    html += '<div class="benchmark-card">';
    html += '<div class="benchmark-label">R-Squared</div>';
    html += '<div class="benchmark-value">' + (rSquared !== null ? rSquared.toFixed(1) + '%' : '—') + '</div>';
    html += '<div class="benchmark-desc">Correlation with ' + data.benchmark_name + '</div>';
    html += '</div>';

    html += '</div>';

    container.innerHTML = html;
}

// Create benchmark vs portfolio chart
function createBenchmarkChart() {
    var ctx = document.getElementById('benchmarkChart');
    if (!ctx) return;

    var data = getBenchmarkData();
    var chartData = data.chart_data || [];

    if (chartData.length === 0) {
        ctx.parentElement.innerHTML = '<p class="muted" style="text-align: center; padding: 50px;">No historical comparison data available</p>';
        return;
    }

    var labels = chartData.map(function (d) { return d.date; });
    var portfolioData = chartData.map(function (d) { return d.portfolio; });
    var benchmarkData = chartData.map(function (d) { return d.benchmark; });

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Portfolio',
                    data: portfolioData,
                    borderColor: '#4ade80',
                    backgroundColor: 'rgba(74, 222, 128, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2
                },
                {
                    label: data.benchmark_name || 'S&P 500',
                    data: benchmarkData,
                    borderColor: '#60a5fa',
                    backgroundColor: 'rgba(96, 165, 250, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2
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
                            var value = context.raw;
                            var returnPct = ((value - 100) / 100 * 100).toFixed(1);
                            return context.dataset.label + ': ' + value.toFixed(0) + ' (' + (returnPct >= 0 ? '+' : '') + returnPct + '%)';
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#888', maxTicksLimit: 12 },
                    grid: { display: false }
                },
                y: {
                    type: 'logarithmic',
                    ticks: {
                        color: '#888',
                        callback: function (value) {
                            // Show clean values on log scale
                            if (value === 100 || value === 200 || value === 500 ||
                                value === 1000 || value === 2000 || value === 5000 ||
                                value === 10000 || value === 20000) {
                                return value;
                            }
                            return '';
                        }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    min: 50
                }
            }
        }
    });
}

// Create risk metrics display
function createRiskMetrics() {
    var container = document.getElementById('riskMetrics');
    if (!container) return;

    var data = getBenchmarkData();

    if (!data.benchmark_symbol) {
        container.innerHTML = '<p class="muted">No risk data available</p>';
        return;
    }

    var html = '<div class="risk-metrics-list">';

    // Volatility
    html += '<div class="risk-row">';
    html += '<span class="risk-label">Annualized Volatility</span>';
    html += '<span class="risk-value">' + (data.volatility !== null ? data.volatility.toFixed(1) + '%' : '—') + '</span>';
    html += '</div>';

    // Max Drawdown
    var maxDD = data.max_drawdown;
    html += '<div class="risk-row">';
    html += '<span class="risk-label">Maximum Drawdown</span>';
    html += '<span class="risk-value negative">' + (maxDD !== null ? maxDD.toFixed(1) + '%' : '—') + '</span>';
    html += '</div>';

    if (data.drawdown_peak && data.drawdown_trough) {
        html += '<div class="risk-row subdued">';
        html += '<span class="risk-label">Drawdown Period</span>';
        html += '<span class="risk-value">' + data.drawdown_peak + ' to ' + data.drawdown_trough + '</span>';
        html += '</div>';
    }

    // Tracking Error
    html += '<div class="risk-row">';
    html += '<span class="risk-label">Tracking Error</span>';
    html += '<span class="risk-value">' + (data.tracking_error !== null ? data.tracking_error.toFixed(1) + '%' : '—') + '</span>';
    html += '</div>';

    // Best/Worst Day
    html += '<div class="risk-row">';
    html += '<span class="risk-label">Best Day</span>';
    html += '<span class="risk-value positive">' + (data.best_day !== null ? '+' + data.best_day.toFixed(2) + '%' : '—') + '</span>';
    html += '</div>';

    html += '<div class="risk-row">';
    html += '<span class="risk-label">Worst Day</span>';
    html += '<span class="risk-value negative">' + (data.worst_day !== null ? data.worst_day.toFixed(2) + '%' : '—') + '</span>';
    html += '</div>';

    // Win Rate
    if (data.positive_days !== undefined && data.total_days) {
        var winRate = (data.positive_days / data.total_days * 100).toFixed(1);
        html += '<div class="risk-row">';
        html += '<span class="risk-label">Positive Days</span>';
        html += '<span class="risk-value">' + data.positive_days + ' / ' + data.total_days + ' (' + winRate + '%)</span>';
        html += '</div>';
    }

    html += '</div>';

    container.innerHTML = html;
}

// Create ROI over time chart
function createROIChart() {
    var ctx = document.getElementById('roiChart');
    if (!ctx) return;

    var data = getBenchmarkData();
    var roiHistory = data.roi_history || [];

    if (roiHistory.length === 0) {
        ctx.parentElement.innerHTML = '<p class="muted" style="text-align: center; padding: 50px;">No ROI history available</p>';
        return;
    }

    var labels = roiHistory.map(function (d) { return d.date; });
    var roiData = roiHistory.map(function (d) { return d.roi; });

    // Determine colors based on values
    var pointColors = roiData.map(function (v) {
        return v >= 0 ? 'rgba(74, 222, 128, 1)' : 'rgba(248, 113, 113, 1)';
    });

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Return on Investment',
                data: roiData,
                borderColor: '#4ade80',
                backgroundColor: function(context) {
                    var chart = context.chart;
                    var {ctx, chartArea} = chart;
                    if (!chartArea) return null;
                    var gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                    gradient.addColorStop(0, 'rgba(248, 113, 113, 0.3)');
                    gradient.addColorStop(0.5, 'rgba(128, 128, 128, 0.1)');
                    gradient.addColorStop(1, 'rgba(74, 222, 128, 0.3)');
                    return gradient;
                },
                fill: true,
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 5
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
                            var value = context.raw;
                            return 'ROI: ' + (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
                        }
                    }
                },
                annotation: {
                    annotations: {
                        zeroLine: {
                            type: 'line',
                            yMin: 0,
                            yMax: 0,
                            borderColor: 'rgba(255, 255, 255, 0.3)',
                            borderWidth: 1,
                            borderDash: [5, 5]
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#888', maxTicksLimit: 12 },
                    grid: { display: false }
                },
                y: {
                    ticks: {
                        color: '#888',
                        callback: function (value) { return value + '%'; }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create Value vs Cost Basis chart
function createValueVsCostBasisChart() {
    var ctx = document.getElementById('valueCostBasisChart');
    if (!ctx) return;

    var data = getBenchmarkData();
    var chartData = data.chart_data || [];

    // Filter entries that have costBasis data
    var dataWithCostBasis = chartData.filter(function(d) {
        return d.costBasis !== undefined && d.value !== undefined;
    });

    if (dataWithCostBasis.length === 0) {
        ctx.parentElement.innerHTML = '<p class="muted" style="text-align: center; padding: 50px;">No cost basis history available</p>';
        return;
    }

    var labels = dataWithCostBasis.map(function (d) { return d.date; });
    var valueData = dataWithCostBasis.map(function (d) { return d.value; });
    var costBasisData = dataWithCostBasis.map(function (d) { return d.costBasis; });

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Portfolio Value',
                    data: valueData,
                    borderColor: '#4ade80',
                    backgroundColor: 'rgba(74, 222, 128, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2
                },
                {
                    label: 'Cost Basis',
                    data: costBasisData,
                    borderColor: '#9ca3af',
                    backgroundColor: 'rgba(156, 163, 175, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    borderDash: [5, 5]
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
                        },
                        afterBody: function(tooltipItems) {
                            var idx = tooltipItems[0].dataIndex;
                            var value = valueData[idx];
                            var cost = costBasisData[idx];
                            var gain = value - cost;
                            var gainPct = ((gain / cost) * 100).toFixed(2);
                            return 'Gain: ' + formatCurrency(gain) + ' (' + (gain >= 0 ? '+' : '') + gainPct + '%)';
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#888', maxTicksLimit: 12 },
                    grid: { display: false }
                },
                y: {
                    ticks: {
                        color: '#888',
                        callback: function (value) {
                            if (value >= 1000000) return '$' + (value / 1000000).toFixed(1) + 'M';
                            if (value >= 1000) return '$' + (value / 1000).toFixed(0) + 'K';
                            return '$' + value;
                        }
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

// Create benchmark returns comparison table
function createBenchmarkReturnsTable() {
    var container = document.getElementById('benchmarkReturnsTable');
    if (!container) return;

    var data = getBenchmarkData();

    if (!data.benchmark || !data.portfolio) {
        container.innerHTML = '<p class="muted">No benchmark returns data available</p>';
        return;
    }

    var periods = ['1D', '1W', '2W', '1M', '3M', '6M', '1Y', '2Y', '5Y'];

    var html = '<table class="benchmark-table">';
    html += '<thead><tr>';
    html += '<th>Period</th>';
    html += '<th style="text-align: right;">Portfolio</th>';
    html += '<th style="text-align: right;">' + (data.benchmark_name || 'Benchmark') + '</th>';
    html += '<th style="text-align: right;">Excess</th>';
    html += '</tr></thead>';
    html += '<tbody>';

    periods.forEach(function (period) {
        var portRet = data.portfolio['Return_' + period];
        var benchRet = data.benchmark['Return_' + period];
        var excessRet = data.excess_returns ? data.excess_returns['Excess_' + period] : null;

        html += '<tr>';
        html += '<td>' + period + '</td>';
        html += '<td class="number">' + formatReturnCell(portRet) + '</td>';
        html += '<td class="number">' + formatReturnCell(benchRet) + '</td>';
        html += '<td class="number">' + formatReturnCell(excessRet) + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table>';

    container.innerHTML = html;
}
