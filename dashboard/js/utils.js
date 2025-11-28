/**
 * Utility Functions
 * Common helpers used across the dashboard
 */

// ==========================================
// Chart Configuration
// ==========================================

const chartColorPalette = [
    '#60a5fa', '#a78bfa', '#f59e0b', '#34d399', '#f472b6',
    '#fbbf24', '#2dd4bf', '#818cf8', '#fb7185', '#4ade80'
];

const accountColors = {
    'default': '#60a5fa'
};

const assetColors = {
    'default': '#60a5fa'
};

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
                    font: { size: 11 },
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

// ==========================================
// Formatters
// ==========================================

function formatCurrency(value) {
    if (value === undefined || value === null || isNaN(value)) return '$0.00';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2
    }).format(value);
}

function formatCompactCurrency(value) {
    if (value >= 1000000) {
        return '$' + (value / 1000000).toFixed(1) + 'M';
    } else if (value >= 1000) {
        return '$' + (value / 1000).toFixed(0) + 'K';
    }
    return formatCurrency(value);
}

function formatPercent(value) {
    if (value === undefined || value === null || isNaN(value)) return '+0.00%';
    return (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
}

function formatNumber(value, decimals) {
    if (value === undefined || value === null || isNaN(value)) return '0';
    if (decimals === undefined) decimals = 4;
    if (Math.abs(value) < 0.0001 && value !== 0) return value.toExponential(2);
    return value.toLocaleString('en-US', {
        maximumFractionDigits: decimals
    });
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

function formatPriceChange(value) {
    if (value === null || value === undefined || isNaN(value)) {
        return '<span class="change neutral">-</span>';
    }
    var className = value >= 0 ? 'positive' : 'negative';
    var sign = value >= 0 ? '+' : '';
    return '<span class="change ' + className + '">' + sign + value.toFixed(2) + '%</span>';
}

// ==========================================
// Color Helpers
// ==========================================

function getAssetColor(symbol, index) {
    return assetColors[symbol] || chartColorPalette[index % chartColorPalette.length];
}

function getAccountColor(account, index) {
    for (var key in accountColors) {
        if (key !== 'default' && account.toLowerCase().includes(key.toLowerCase())) {
            return accountColors[key];
        }
    }
    return chartColorPalette[index % chartColorPalette.length];
}

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
