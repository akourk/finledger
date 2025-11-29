/**
 * Widgets Module
 * Recent activity, top movers, corporate actions
 */

// Create Recent Activity Widget
function createRecentActivity() {
    var container = document.getElementById('recentActivity');
    if (!container) return;

    var transactions = getMasterTransactions().slice(0, 100);
    if (transactions.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No recent activity</p>';
        return;
    }

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
        var iconClass = 'transfer', icon = '↔', amountClass = '';

        if (t.action === 'Buy') { iconClass = 'buy'; icon = '↓'; amountClass = 'negative'; }
        else if (t.action === 'Sell') { iconClass = 'sell'; icon = '↑'; amountClass = 'positive'; }
        else if (t.action === 'Dividend') { iconClass = 'dividend'; icon = '💰'; amountClass = 'positive'; }
        else if (t.action === 'Interest') { iconClass = 'dividend'; icon = '💵'; amountClass = 'positive'; }
        else if (t.action === 'Staking') { iconClass = 'staking'; icon = '⚡'; amountClass = 'positive'; }
        else if (t.action === 'OptionBuy' || t.action === 'OptionSell') {
            iconClass = 'option'; icon = '📊';
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
        html += '<div class="activity-amount-value ' + amountClass + '">' + (amountClass === 'positive' ? '+' : '') + formatCurrency(t.amount) + '</div>';
        html += '<div class="activity-date">' + formatDate(t.date) + '</div>';
        html += '</div></div>';
    });

    container.innerHTML = html;
}

// Create Top Movers Widget
function createTopMovers() {
    var container = document.getElementById('topMovers');
    if (!container) return;

    var holdings = getHoldingsDetail().filter(function (h) {
        return h.change_30d !== null && h.change_30d !== undefined && h.current_value > 100;
    });

    if (holdings.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No data available</p>';
        return;
    }

    var sorted = holdings.slice().sort(function (a, b) { return b.change_30d - a.change_30d; });
    var gainers = sorted.slice(0, 5);
    var losers = sorted.slice(-5).reverse();

    var maxChange = Math.max(
        Math.abs(gainers[0] ? gainers[0].change_30d : 0),
        Math.abs(losers[0] ? losers[0].change_30d : 0)
    );
    if (maxChange < 1) maxChange = 1;

    var html = '';

    html += '<div class="movers-section"><div class="movers-header">🟢 Top Gainers</div>';
    gainers.forEach(function (h) {
        if (h.change_30d <= 0) return;
        var barWidth = Math.min((Math.abs(h.change_30d) / maxChange) * 100, 100);
        html += '<div class="mover-item"><div class="mover-symbol">' + h.symbol + '</div>';
        html += '<div class="mover-bar-container"><div class="mover-bar positive" style="width: ' + barWidth + '%;"></div></div>';
        html += '<div class="mover-change positive">+' + h.change_30d.toFixed(1) + '%</div></div>';
    });
    html += '</div>';

    html += '<div class="movers-section"><div class="movers-header">🔴 Top Losers</div>';
    losers.forEach(function (h) {
        if (h.change_30d >= 0) return;
        var barWidth = Math.min((Math.abs(h.change_30d) / maxChange) * 100, 100);
        html += '<div class="mover-item"><div class="mover-symbol">' + h.symbol + '</div>';
        html += '<div class="mover-bar-container"><div class="mover-bar negative" style="width: ' + barWidth + '%;"></div></div>';
        html += '<div class="mover-change negative">' + h.change_30d.toFixed(1) + '%</div></div>';
    });
    html += '</div>';

    container.innerHTML = html;
}

// Create Corporate Actions Widget (simple table for overview)
function createCorporateActionsWidget() {
    var container = document.getElementById('corporateActionsBody');
    if (!container) return;

    var transactions = getMasterTransactions();
    var corpActions = transactions.filter(function (t) {
        return t.action === 'Split' || t.action === 'Merger' || t.action === 'SpinOff' ||
            t.action === 'RightsIssue' || t.action === 'StockDividend';
    }).slice(0, 20);

    if (corpActions.length === 0) {
        container.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-secondary);">No corporate actions recorded</td></tr>';
        return;
    }

    var html = '';
    corpActions.forEach(function (t) {
        html += '<tr>';
        html += '<td>' + formatDate(t.date) + '</td>';
        html += '<td><span class="symbol-badge">' + t.symbol + '</span></td>';
        html += '<td>' + t.action + '</td>';
        html += '<td>' + t.account + '</td>';
        html += '<td>' + (t.note || '—') + '</td>';
        html += '</tr>';
    });

    container.innerHTML = html;
}

// Refresh Indicator
function initRefreshIndicator() {
    var refreshBtn = document.getElementById('refreshBtn');
    var refreshStatus = document.getElementById('refreshStatus');
    var lastUpdatedText = document.getElementById('lastUpdatedText');

    if (!refreshBtn) return;

    // Check if we're running from server or local file
    var isServerMode = window.location.protocol === 'http:' || window.location.protocol === 'https:';

    // Load initial status
    if (isServerMode) {
        fetchDataStatus();
    } else {
        // Local file mode - use localStorage
        var lastRefresh = localStorage.getItem('dashboardLastRefresh');
        if (lastRefresh) {
            updateRefreshStatus(new Date(parseInt(lastRefresh)));
        } else {
            localStorage.setItem('dashboardLastRefresh', Date.now().toString());
            if (lastUpdatedText) lastUpdatedText.textContent = 'Just updated';
            if (refreshStatus) refreshStatus.classList.add('success');
        }
    }

    refreshBtn.addEventListener('click', function () {
        if (isServerMode) {
            // Server mode - trigger actual data refresh
            triggerDataRefresh();
        } else {
            // Local file mode - just reload
            refreshBtn.classList.add('refreshing');
            if (lastUpdatedText) lastUpdatedText.textContent = 'Refreshing...';
            if (refreshStatus) refreshStatus.classList.remove('success', 'error');

            setTimeout(function () {
                localStorage.setItem('dashboardLastRefresh', Date.now().toString());
                window.location.reload();
            }, 500);
        }
    });

    // Auto-refresh status every 30 seconds if in server mode
    if (isServerMode) {
        setInterval(fetchDataStatus, 30000);
    }
}

function fetchDataStatus() {
    var refreshStatus = document.getElementById('refreshStatus');
    var lastUpdatedText = document.getElementById('lastUpdatedText');
    if (!refreshStatus || !lastUpdatedText) return;

    fetch('/api/status')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            if (data.status === 'success' && data.generated_at) {
                var date = new Date(data.generated_at);
                updateRefreshStatus(date);
            } else {
                lastUpdatedText.textContent = 'Status unknown';
            }
        })
        .catch(function (error) {
            console.error('Error fetching status:', error);
            lastUpdatedText.textContent = 'Status unavailable';
        });
}

function triggerDataRefresh() {
    var refreshBtn = document.getElementById('refreshBtn');
    var refreshStatus = document.getElementById('refreshStatus');
    var lastUpdatedText = document.getElementById('lastUpdatedText');

    refreshBtn.classList.add('refreshing');
    refreshBtn.disabled = true;
    if (lastUpdatedText) lastUpdatedText.textContent = 'Refreshing data...';
    if (refreshStatus) refreshStatus.classList.remove('success', 'error');

    fetch('/api/refresh', {
        method: 'POST'
    })
        .then(function (response) { return response.json(); })
        .then(function (data) {
            if (data.status === 'success') {
                if (refreshStatus) refreshStatus.classList.add('success');
                if (lastUpdatedText) lastUpdatedText.textContent = 'Refresh complete!';

                // Reload the page after a short delay to show new data
                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            } else {
                if (refreshStatus) refreshStatus.classList.add('error');
                if (lastUpdatedText) lastUpdatedText.textContent = 'Refresh failed';
                refreshBtn.classList.remove('refreshing');
                refreshBtn.disabled = false;

                console.error('Refresh error:', data.message);
                alert('Error refreshing data: ' + data.message);
            }
        })
        .catch(function (error) {
            console.error('Refresh request failed:', error);
            if (refreshStatus) refreshStatus.classList.add('error');
            if (lastUpdatedText) lastUpdatedText.textContent = 'Connection error';
            refreshBtn.classList.remove('refreshing');
            refreshBtn.disabled = false;

            alert('Could not connect to server. Make sure server.py is running.');
        });
}

function updateRefreshStatus(date) {
    var refreshStatus = document.getElementById('refreshStatus');
    var lastUpdatedText = document.getElementById('lastUpdatedText');
    if (!refreshStatus || !lastUpdatedText) return;

    var now = new Date();
    var diff = now - date;
    var minutes = Math.floor(diff / 60000);
    var hours = Math.floor(diff / 3600000);
    var days = Math.floor(diff / 86400000);

    var timeAgo;
    if (minutes < 1) timeAgo = 'Just updated';
    else if (minutes < 60) timeAgo = minutes + ' min ago';
    else if (hours < 24) timeAgo = hours + ' hour' + (hours > 1 ? 's' : '') + ' ago';
    else timeAgo = days + ' day' + (days > 1 ? 's' : '') + ' ago';

    lastUpdatedText.textContent = timeAgo;
    refreshStatus.classList.add('success');
}

// Loading States
function showLoadingState() {
    var skeleton = document.getElementById('loadingSkeleton');
    var content = document.getElementById('dashboardContent');
    if (skeleton) skeleton.classList.remove('hidden');
    if (content) content.style.display = 'none';
}

function hideLoadingState() {
    var skeleton = document.getElementById('loadingSkeleton');
    var content = document.getElementById('dashboardContent');
    if (skeleton) skeleton.classList.add('hidden');
    if (content) content.style.display = 'block';
}
