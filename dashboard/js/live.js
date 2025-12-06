/**
 * Live Updates Module
 * Handles real-time updates when running in server mode
 */

// Server mode detection
var isServerMode = window.location.protocol === 'http:' || window.location.protocol === 'https:';

// EventSource for Server-Sent Events
var eventSource = null;

// Activity log entries (max 50)
var activityLog = [];
var MAX_LOG_ENTRIES = 50;

// Auto-refresh state
var autoRefreshEnabled = false;
var autoRefreshInterval = null;
var AUTO_REFRESH_SECONDS = 60; // Default refresh interval
var pauseWhenMarketClosed = true; // Default: pause when closed

// Portfolio value history for chart (stored in memory for session)
var portfolioValueHistory = [];
var MAX_VALUE_HISTORY = 100;

// Market status cache
var marketStatusCache = null;
var marketStatusLastCheck = null;
var MARKET_STATUS_CACHE_MS = 60000; // Cache for 1 minute

// Custom watchlist (persisted to localStorage)
var customWatchlist = [];
var WATCHLIST_STORAGE_KEY = 'portfolioLiveWatchlist';

// Session statistics
var sessionStats = {
    startTime: new Date(),
    refreshCount: 0,
    highValue: null,
    lowValue: null,
    firstValue: null
}

    ;

// S&P 500 comparison
var spyData = null;

/**
 * Initialize the Live tab functionality
 */
function initLive() {
    if (!isServerMode) {
        return;
    }

    // Show the Live tab button
    var liveTabBtn = document.getElementById('liveTabBtn');

    if (liveTabBtn) {
        liveTabBtn.style.display = 'inline-flex';
    }

    // Load custom watchlist from localStorage
    loadCustomWatchlist();

    // Initialize session stats display
    initSessionStats();

    // Set up event listeners
    setupLiveEventListeners();

    // Connect to SSE
    connectToEventSource();

    // Initial market status check
    refreshMarketStatus();

    // Start session duration timer
    startSessionDurationTimer();

    // Load initial data after a short delay (even if market is closed)
    setTimeout(function () {
        loadInitialData();
    }

        , 500);
}

/**
 * Load initial data when the Live tab is first viewed
 * This runs regardless of market status to populate the UI with current values
 */
function loadInitialData() {
    addActivityLogEntry('system', 'Loading initial data...');

    // Load sector breakdown from static data (works offline/after hours)
    refreshLiveSectorChart();

    // Initialize portfolio chart with static data as starting point
    initPortfolioChartWithStaticData();

    // Load portfolio value and day gain (requires API)
    refreshLivePortfolioValue();
    refreshDayGain();

    // Load S&P 500 comparison
    refreshSPYComparison();

    // Load custom watchlist prices (if any symbols)
    if (customWatchlist.length > 0) {
        refreshCustomWatchlist();
    }

    // Load top holdings watchlist
    refreshAllWatchlistPrices();
}

/**
 * Initialize portfolio chart with static data as starting point
 */
function initPortfolioChartWithStaticData() {
    // Use holdings data to get initial portfolio value
    var holdings = null;
    if (typeof holdingsDetailData !== 'undefined' && Array.isArray(holdingsDetailData) && holdingsDetailData.length > 0) {
        holdings = holdingsDetailData;
    } else if (typeof holdingsData !== 'undefined' && Array.isArray(holdingsData) && holdingsData.length > 0) {
        holdings = holdingsData;
    }

    if (holdings) {
        var totalValue = 0;
        holdings.forEach(function (h) {
            totalValue += (h.CurrentValue || h.MarketValue || 0);
        });

        // Add cash balances if available
        if (typeof cashBalancesData !== 'undefined' && Array.isArray(cashBalancesData)) {
            cashBalancesData.forEach(function (c) {
                totalValue += (c.CurrentBalance || c.Balance || 0);
            });
        }

        if (totalValue > 0 && portfolioValueHistory.length === 0) {
            // Add as first data point
            addToPortfolioHistory(totalValue);
            addActivityLogEntry('info', 'Loaded initial portfolio value: $' + formatNumber(totalValue));
        }
    } else {
        // Data not loaded yet - show helpful message
        var container = document.getElementById('portfolioValueChart');
        if (container) {
            container.innerHTML = '<p style="text-align: center; color: #888; padding-top: 60px;">Loading portfolio data...</p>';
        }
    }
}

/**
 * Set up event listeners for the Live tab
 */
function setupLiveEventListeners() {
    // Price check button
    var priceCheckBtn = document.getElementById('priceCheckBtn');
    var priceCheckInput = document.getElementById('priceCheckSymbol');

    if (priceCheckBtn) {
        priceCheckBtn.addEventListener('click', function () {
            var symbol = priceCheckInput ? priceCheckInput.value.trim().toUpperCase() : '';

            if (symbol) {
                checkLivePrice(symbol);
            }
        }

        );
    }

    if (priceCheckInput) {
        priceCheckInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                var symbol = priceCheckInput.value.trim().toUpperCase();

                if (symbol) {
                    checkLivePrice(symbol);
                }
            }
        }

        );
    }

    // Add to watchlist from price check
    var addToWatchlistBtn = document.getElementById('addToWatchlistBtn');

    if (addToWatchlistBtn) {
        addToWatchlistBtn.addEventListener('click', function () {
            var symbol = priceCheckInput ? priceCheckInput.value.trim().toUpperCase() : '';

            if (symbol) {
                addToCustomWatchlist(symbol);
                addToWatchlistBtn.style.display = 'none';
            }
        }

        );
    }

    // Refresh live value button
    var refreshLiveValueBtn = document.getElementById('refreshLiveValueBtn');

    if (refreshLiveValueBtn) {
        refreshLiveValueBtn.addEventListener('click', function () {
            refreshLivePortfolioValue();
            refreshDayGain();
            refreshSPYComparison();
            refreshLiveSectorChart();
        }

        );
    }

    // Refresh all prices button
    var refreshAllPricesBtn = document.getElementById('refreshAllPricesBtn');

    if (refreshAllPricesBtn) {
        refreshAllPricesBtn.addEventListener('click', refreshAllWatchlistPrices);
    }

    // Auto-refresh toggle
    var autoRefreshToggle = document.getElementById('autoRefreshToggle');

    if (autoRefreshToggle) {
        autoRefreshToggle.addEventListener('change', function () {
            toggleAutoRefresh(this.checked);
        }

        );
    }

    // Pause when market closed toggle
    var pauseWhenClosedToggle = document.getElementById('pauseWhenClosedToggle');

    if (pauseWhenClosedToggle) {
        pauseWhenClosedToggle.addEventListener('change', function () {
            pauseWhenMarketClosed = this.checked;
            addActivityLogEntry('system', 'Pause when market closed: ' + (pauseWhenMarketClosed ? 'On' : 'Off'));
        }

        );
    }

    // Refresh interval selector
    var intervalSelect = document.getElementById('refreshIntervalSelect');

    if (intervalSelect) {
        intervalSelect.addEventListener('change', function () {
            AUTO_REFRESH_SECONDS = parseInt(this.value, 10);

            if (autoRefreshEnabled) {
                // Restart auto-refresh with new interval
                toggleAutoRefresh(false);
                toggleAutoRefresh(true);
            }

            addActivityLogEntry('system', 'Refresh interval changed to ' + AUTO_REFRESH_SECONDS + 's');
        }

        );
    }

    // Custom watchlist controls
    var watchlistAddBtn = document.getElementById('watchlistAddBtn');
    var watchlistAddInput = document.getElementById('watchlistAddSymbol');
    var watchlistRefreshBtn = document.getElementById('watchlistRefreshBtn');
    var watchlistClearBtn = document.getElementById('watchlistClearBtn');

    if (watchlistAddBtn && watchlistAddInput) {
        watchlistAddBtn.addEventListener('click', function () {
            var symbol = watchlistAddInput.value.trim().toUpperCase();

            if (symbol) {
                addToCustomWatchlist(symbol);
                watchlistAddInput.value = '';
            }
        }

        );

        watchlistAddInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                var symbol = watchlistAddInput.value.trim().toUpperCase();

                if (symbol) {
                    addToCustomWatchlist(symbol);
                    watchlistAddInput.value = '';
                }
            }
        }

        );
    }

    if (watchlistRefreshBtn) {
        watchlistRefreshBtn.addEventListener('click', refreshCustomWatchlist);
    }

    if (watchlistClearBtn) {
        watchlistClearBtn.addEventListener('click', clearCustomWatchlist);
    }
}

/**
 * Toggle auto-refresh on/off
 */
function toggleAutoRefresh(enabled) {
    autoRefreshEnabled = enabled;
    var statusSpan = document.getElementById('autoRefreshStatus');
    var countdownSpan = document.getElementById('autoRefreshCountdown');

    if (enabled) {

        // Check if market is closed and pause is enabled
        if (pauseWhenMarketClosed && marketStatusCache && marketStatusCache.market_status === 'closed') {
            addActivityLogEntry('system', 'Auto-refresh paused - market is closed');
            if (statusSpan) statusSpan.textContent = 'Paused (Market Closed)';
            if (countdownSpan) countdownSpan.textContent = '';
            // Still set the flag so it will auto-resume when market opens
            return;
        }

        addActivityLogEntry('system', 'Auto-refresh enabled (every ' + AUTO_REFRESH_SECONDS + 's)');
        if (statusSpan) statusSpan.textContent = 'On';

        // Do an immediate refresh
        performFullRefresh();

        // Start countdown and interval
        startAutoRefreshCountdown();

        autoRefreshInterval = setInterval(function () {

            // Check market status before each refresh
            if (pauseWhenMarketClosed && marketStatusCache && marketStatusCache.market_status === 'closed') {
                addActivityLogEntry('system', 'Skipping refresh - market closed');
                if (statusSpan) statusSpan.textContent = 'Paused (Market Closed)';
                if (countdownSpan) countdownSpan.textContent = 'Will resume when market opens';
                return;
            }

            performFullRefresh();
            startAutoRefreshCountdown();
        }

            , AUTO_REFRESH_SECONDS * 1000);
    }

    else {
        addActivityLogEntry('system', 'Auto-refresh disabled');
        if (statusSpan) statusSpan.textContent = 'Off';
        if (countdownSpan) countdownSpan.textContent = '';

        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }

        // Clear any existing countdown
        if (window.autoRefreshCountdownInterval) {
            clearInterval(window.autoRefreshCountdownInterval);
            window.autoRefreshCountdownInterval = null;
        }
    }
}

/**
 * Perform a full refresh of all live data
 */
function performFullRefresh() {
    sessionStats.refreshCount++;
    updateSessionStatsDisplay();

    refreshMarketStatus();
    refreshLivePortfolioValue();
    refreshDayGain();
    refreshSPYComparison();
    refreshAllWatchlistPrices();
    refreshCustomWatchlist();
    refreshLiveSectorChart();
}

/**
 * Start countdown display for next auto-refresh
 */
function startAutoRefreshCountdown() {
    var countdownSpan = document.getElementById('autoRefreshCountdown');
    if (!countdownSpan) return;

    var secondsLeft = AUTO_REFRESH_SECONDS;

    // Clear any existing countdown
    if (window.autoRefreshCountdownInterval) {
        clearInterval(window.autoRefreshCountdownInterval);
    }

    // Update immediately
    countdownSpan.textContent = 'Next refresh in ' + secondsLeft + 's';

    // Update every second
    window.autoRefreshCountdownInterval = setInterval(function () {
        secondsLeft--;

        if (secondsLeft <= 0) {
            countdownSpan.textContent = 'Refreshing...';
            clearInterval(window.autoRefreshCountdownInterval);
        }

        else {
            countdownSpan.textContent = 'Next refresh in ' + secondsLeft + 's';
        }
    }

        , 1000);
}

/**
 * Connect to Server-Sent Events endpoint
 */
function connectToEventSource() {
    if (!isServerMode) return;

    var statusIndicator = document.getElementById('liveStatusIndicator');
    var connectionStatus = document.getElementById('liveConnectionStatus');

    try {
        eventSource = new EventSource('/api/events');

        eventSource.onopen = function () {
            if (statusIndicator) statusIndicator.className = 'live-status-indicator connected';

            if (connectionStatus) {
                connectionStatus.innerHTML = '<p class="status-connected">✓ Connected to server. Real-time updates enabled.</p>';
            }

            addActivityLogEntry('system', 'Connected to live updates server');
        }

            ;

        eventSource.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);
                handleServerEvent(data);
            }

            catch (e) {
                console.error('Error parsing SSE message:', e);
            }
        }

            ;

        eventSource.onerror = function () {
            if (statusIndicator) statusIndicator.className = 'live-status-indicator disconnected';

            if (connectionStatus) {
                connectionStatus.innerHTML = '<p class="status-disconnected">✗ Disconnected from server. Attempting to reconnect...</p>';
            }

            addActivityLogEntry('error', 'Lost connection to server');
        }

            ;
    }

    catch (e) {
        console.error('Error connecting to SSE:', e);

        if (connectionStatus) {
            connectionStatus.innerHTML = '<p class="status-error">✗ Could not connect to server events.</p>';
        }
    }
}

/**
 * Handle incoming server events
 */
function handleServerEvent(data) {
    switch (data.type) {
        case 'connected':
            addActivityLogEntry('system', 'Event stream connected');
            break;
        case 'heartbeat':
            // Silent heartbeat, just keep connection alive
            break;
        case 'refresh_started':
            addActivityLogEntry('refresh', 'Data refresh started...');
            break;
        case 'refresh_completed':
            addActivityLogEntry('success', 'Data refresh completed successfully');

            if (data.data && data.data.output_lines) {
                data.data.output_lines.slice(-3).forEach(function (line) {
                    if (line.trim()) {
                        addActivityLogEntry('info', line.trim());
                    }
                }

                );
            }

            break;
        case 'refresh_error': addActivityLogEntry('error', 'Refresh failed: ' + (data.data ? data.data.message : 'Unknown error'));
            break;
        default: addActivityLogEntry('info', 'Server event: ' + data.type);
    }
}

/**
 * Add entry to activity log
 */
function addActivityLogEntry(type, message) {
    var timestamp = new Date().toLocaleTimeString();

    var entry = {
        type: type,
        message: message,
        timestamp: timestamp
    }

        ;

    activityLog.unshift(entry);

    if (activityLog.length > MAX_LOG_ENTRIES) {
        activityLog.pop();
    }

    renderActivityLog();
}

/**
 * Render the activity log
 */
function renderActivityLog() {
    var container = document.getElementById('activityLogEntries');
    if (!container) return;

    if (activityLog.length === 0) {
        container.innerHTML = '<p class="activity-empty">No activity yet. Events will appear here when you refresh data or check prices.</p>';
        return;
    }

    var html = activityLog.map(function (entry) {
        var iconClass = 'activity-icon-' + entry.type;
        var icon = '●';

        switch (entry.type) {
            case 'success': icon = '✓'; break;
            case 'error': icon = '✗'; break;
            case 'refresh': icon = '↻'; break;
            case 'price': icon = '$'; break;
            case 'system': icon = '⚙'; break;
            default: icon = 'ℹ';
        }

        return '<div class="activity-entry activity-' + entry.type + '">' + '<span class="activity-icon ' + iconClass + '">' + icon + '</span>' + '<span class="activity-time">' + entry.timestamp + '</span>' + '<span class="activity-message">' + entry.message + '</span>' + '</div>';
    }

    ).join('');

    container.innerHTML = html;
}

/**
 * Refresh market status
 */
function refreshMarketStatus() {
    var now = Date.now();

    if (marketStatusCache && marketStatusLastCheck && (now - marketStatusLastCheck) < MARKET_STATUS_CACHE_MS) {
        // Use cached value
        updateMarketStatusDisplay(marketStatusCache);
        return;
    }

    fetch('/api/live/market-status').then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            marketStatusCache = data;
            marketStatusLastCheck = Date.now();
            updateMarketStatusDisplay(data);
        }
    }

    ).catch(function (error) {
        console.error('Error fetching market status:', error);
    }

    );
}

/**
 * Update market status display
 */
function updateMarketStatusDisplay(data) {
    var indicator = document.getElementById('marketStatusIndicator');
    var text = document.getElementById('marketStatusText');
    var info = document.getElementById('marketStatusInfo');
    var time = document.getElementById('marketTime');

    if (indicator) {
        indicator.className = 'market-status-indicator market-' + data.market_status;
    }

    if (text) {
        text.textContent = data.status_text;
        text.className = 'market-status-text market-' + data.market_status;
    }

    if (info) {
        info.textContent = data.next_open;
    }

    if (time) {
        time.textContent = data.current_time;
    }
}

/**
 * Refresh day's gain/loss
 */
function refreshDayGain() {
    var valueElem = document.getElementById('dayGainValue');
    var percentElem = document.getElementById('dayGainPercent');

    if (valueElem) valueElem.textContent = 'Loading...';

    fetch('/api/live/day-gain').then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            var changeClass = data.day_change >= 0 ? 'positive' : 'negative';
            var sign = data.day_change >= 0 ? '+' : '';

            if (valueElem) {
                valueElem.textContent = sign + '$' + formatNumber(Math.abs(data.day_change));
                valueElem.className = 'day-gain-value ' + changeClass;
            }

            if (percentElem) {
                percentElem.textContent = '(' + sign + data.day_change_pct.toFixed(2) + '%)';
                percentElem.className = 'day-gain-percent ' + changeClass;
            }

            // Update top movers
            updateTopMovers(data.top_movers);

            // Add to value history for chart
            addToPortfolioHistory(data.current_value);
        }
    }

    ).catch(function (error) {
        if (valueElem) valueElem.textContent = 'Error';
        console.error('Error fetching day gain:', error);
    }

    );
}

/**
 * Update top movers display
 */
function updateTopMovers(movers) {
    var container = document.getElementById('topMoversContainer');
    if (!container || !movers || movers.length === 0) return;

    var html = '<div class="top-movers-list">';

    movers.forEach(function (m) {
        var changeClass = m.change >= 0 ? 'positive' : 'negative';
        var sign = m.change >= 0 ? '+' : '';
        html += '<div class="mover-item">' + '<span class="mover-symbol">' + m.symbol + '</span>' + '<span class="mover-price">$' + m.price.toFixed(2) + '</span>' + '<span class="mover-change ' + changeClass + '">' + sign + m.change_pct.toFixed(2) + '%</span>' + '<span class="mover-value ' + changeClass + '">' + sign + '$' + formatNumber(Math.abs(m.value_change)) + '</span>' + '</div>';
    }

    );
    html += '</div>';
    container.innerHTML = html;
}

/**
 * Add portfolio value to history for chart
 */
function addToPortfolioHistory(value) {
    var now = new Date();

    portfolioValueHistory.push({
        time: now.toLocaleTimeString(),
        value: value,
        timestamp: now.getTime()
    }

    );

    if (portfolioValueHistory.length > MAX_VALUE_HISTORY) {
        portfolioValueHistory.shift();
    }

    // Update session stats
    updateSessionStats(value);

    // Update S&P comparison (if we have the data)
    updateSPYDisplay();

    renderPortfolioValueChart();
}

/**
 * Render portfolio value chart
 */
function renderPortfolioValueChart() {
    var container = document.getElementById('portfolioValueChart');

    if (!container) return;

    if (portfolioValueHistory.length < 2) {
        var message = portfolioValueHistory.length === 1
            ? '<p style="text-align: center; color: #888; padding-top: 60px;">Current value: <strong style="color: #4CAF50;">$' + formatNumber(portfolioValueHistory[0].value) + '</strong></p><p style="text-align: center; color: #666; font-size: 12px;">Chart will show movement after next refresh</p>'
            : '<p style="text-align: center; color: #888; padding-top: 80px;">Portfolio value will be charted after data loads...</p>';
        container.innerHTML = message;
        return;
    }

    var times = portfolioValueHistory.map(function (p) {
        return p.time;
    }

    );

    var values = portfolioValueHistory.map(function (p) {
        return p.value;
    }

    );

    var minValue = Math.min.apply(null, values);
    var maxValue = Math.max.apply(null, values);
    var range = maxValue - minValue;
    var padding = range * 0.1 || 100;

    var option = {
        tooltip: {

            trigger: 'axis',
            formatter: function (params) {
                var point = params[0];
                return point.axisValue + '<br/>Portfolio: $' + formatNumber(point.value);
            }
        }

        ,
        grid: {
            top: 30,
            left: 60,
            right: 20,
            bottom: 40
        }

        ,
        xAxis: {

            type: 'category',
            data: times,
            axisLabel: {
                color: '#888'
            }
        }

        ,
        yAxis: {

            type: 'value',
            min: Math.floor(minValue - padding),
            max: Math.ceil(maxValue + padding),
            axisLabel: {

                color: '#888',
                formatter: function (value) {
                    return '$' + formatNumber(value);
                }
            }

            ,
            splitLine: {
                lineStyle: {
                    color: 'rgba(255,255,255,0.1)'
                }
            }
        }

        ,
        series: [{

            data: values,
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: {
                color: '#4CAF50', width: 2
            }

            ,
            itemStyle: {
                color: '#4CAF50'
            }

            ,
            areaStyle: {
                color: {

                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 0,
                    y2: 1,
                    colorStops: [{
                        offset: 0, color: 'rgba(76, 175, 80, 0.4)'
                    }

                        ,
                    {
                        offset: 1, color: 'rgba(76, 175, 80, 0.05)'
                    }

                    ]
                }
            }
        }

        ]
    }

        ;

    var chart = echarts.getInstanceByDom(container);

    if (!chart) {
        chart = echarts.init(container);
    }

    chart.setOption(option);
}

/**
 * Fetch intraday data for sparkline
 */
function fetchIntradayData(symbol, callback) {
    fetch('/api/live/intraday/' + encodeURIComponent(symbol)).then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        callback(data.status === 'success' ? data : null);
    }

    ).catch(function () {
        callback(null);
    }

    );
}

/**
 * Create a sparkline chart
 */
function createSparkline(containerId, prices, prevClose) {
    var container = document.getElementById(containerId);
    if (!container || !prices || prices.length === 0) return;

    var values = prices.map(function (p) {
        return p.price;
    }

    );
    var lastPrice = values[values.length - 1];
    var isUp = lastPrice >= (prevClose || values[0]);
    var color = isUp ? '#4CAF50' : '#f44336';

    var option = {
        grid: {
            top: 2, bottom: 2, left: 2, right: 2
        }

        ,
        xAxis: {
            type: 'category', show: false
        }

        ,
        yAxis: {
            type: 'value', show: false
        }

        ,
        series: [{

            data: values,
            type: 'line',
            smooth: true,
            symbol: 'none',
            lineStyle: {
                color: color, width: 1.5
            }

            ,
            areaStyle: {
                color: {

                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 0,
                    y2: 1,
                    colorStops: [{
                        offset: 0, color: color.replace(')', ', 0.3)').replace('rgb', 'rgba')
                    }

                        ,
                    {
                        offset: 1, color: 'rgba(0,0,0,0)'
                    }

                    ]
                }
            }
        }

        ]
    }

        ;

    var chart = echarts.getInstanceByDom(container);

    if (!chart) {
        chart = echarts.init(container);
    }

    chart.setOption(option);
}

/**
 * Check live price for a symbol
 */
function checkLivePrice(symbol) {
    var resultDiv = document.getElementById('priceCheckResult');
    var addBtn = document.getElementById('addToWatchlistBtn');
    if (!resultDiv) return;

    resultDiv.innerHTML = '<p class="loading">Fetching price for ' + symbol + '...</p>';
    if (addBtn) addBtn.style.display = 'none';
    addActivityLogEntry('price', 'Checking price for ' + symbol);

    fetch('/api/live/price/' + encodeURIComponent(symbol)).then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            var changeClass = data.change >= 0 ? 'positive' : 'negative';
            var changeSign = data.change >= 0 ? '+' : '';
            resultDiv.innerHTML = '<div class="price-result">' + '<div class="price-symbol">' + data.symbol + '</div>' + '<div class="price-value">$' + data.price.toFixed(2) + '</div>' + '<div class="price-change ' + changeClass + '">' + changeSign + (data.change ? data.change.toFixed(2) : '0.00') + ' (' + changeSign + (data.change_pct ? data.change_pct.toFixed(2) : '0.00') + '%)' + '</div>' + '</div>';
            addActivityLogEntry('success', symbol + ': $' + data.price.toFixed(2) + ' (' + changeSign + (data.change_pct ? data.change_pct.toFixed(2) : '0') + '%)');

            // Show "Add to Watchlist" button if not already in watchlist
            if (addBtn && customWatchlist.indexOf(symbol) === -1) {
                addBtn.style.display = 'inline-block';
            }
        }

        else {
            resultDiv.innerHTML = '<p class="error">Could not fetch price: ' + (data.message || 'Unknown error') + '</p>';
            addActivityLogEntry('error', 'Failed to get price for ' + symbol);
        }
    }

    ).catch(function (error) {
        resultDiv.innerHTML = '<p class="error">Error: ' + error.message + '</p>';
        addActivityLogEntry('error', 'Network error checking ' + symbol);
    }

    );
}

/**
 * Refresh live portfolio value
 */
function refreshLivePortfolioValue() {
    var valueDisplay = document.getElementById('livePortfolioValue');
    if (!valueDisplay) return;

    var valueSpan = valueDisplay.querySelector('.live-value');
    var timestampSpan = valueDisplay.querySelector('.live-timestamp');

    if (valueSpan) valueSpan.textContent = 'Loading...';
    addActivityLogEntry('refresh', 'Refreshing portfolio value...');

    fetch('/api/live/portfolio-value').then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            if (valueSpan) valueSpan.textContent = '$' + formatNumber(data.total_value);
            if (timestampSpan) timestampSpan.textContent = 'Last updated: ' + new Date().toLocaleTimeString();

            // Build detailed log message
            var logMsg = 'Portfolio value: $' + formatNumber(data.total_value) + ' (' + data.holdings_count + ' holdings';

            if (data.cash_total && data.cash_total > 0) {
                logMsg += ' + $' + formatNumber(data.cash_total) + ' cash';
            }

            logMsg += ')';
            addActivityLogEntry('success', logMsg);

            // Show cash breakdown if available
            if (data.cash_accounts && data.cash_accounts.length > 0) {
                var cashDetails = valueDisplay.querySelector('.live-cash-details');

                if (!cashDetails) {
                    cashDetails = document.createElement('div');
                    cashDetails.className = 'live-cash-details';
                    timestampSpan.parentNode.insertBefore(cashDetails, timestampSpan);
                }

                var cashHtml = '<div class="cash-breakdown">';

                data.cash_accounts.forEach(function (acc) {
                    cashHtml += '<span class="cash-item">' + acc.account + ': $' + formatNumber(acc.balance) + '</span>';
                }

                );
                cashHtml += '</div>';
                cashDetails.innerHTML = cashHtml;
            }
        }

        else {
            if (valueSpan) valueSpan.textContent = 'Error';
            addActivityLogEntry('error', 'Failed to get portfolio value');
        }
    }

    ).catch(function (error) {
        if (valueSpan) valueSpan.textContent = 'Error';
        addActivityLogEntry('error', 'Network error: ' + error.message);
    }

    );
}

/**
 * Refresh all watchlist prices
 */
function refreshAllWatchlistPrices() {
    var tbody = document.getElementById('liveWatchlistBody');
    var statusSpan = document.getElementById('watchlistStatus');
    var btn = document.getElementById('refreshAllPricesBtn');

    if (!tbody) return;

    // Get top holdings from holdings data (variable is holdingsDetailData)
    var holdings = [];

    if (typeof holdingsDetailData !== 'undefined' && Array.isArray(holdingsDetailData)) {

        // Sort by value and take top 10
        holdings = holdingsDetailData.filter(function (h) {
            return h.Symbol && h.Quantity > 0;
        }

        ).sort(function (a, b) {
            return (b.CurrentValue || b.MarketValue || 0) - (a.CurrentValue || a.MarketValue || 0);
        }

        ).slice(0, 10);
    }

    if (holdings.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center;">No holdings data available</td></tr>';
        return;
    }

    // Disable button during refresh
    if (btn) btn.disabled = true;
    if (statusSpan) statusSpan.textContent = 'Refreshing...';
    addActivityLogEntry('refresh', 'Refreshing prices for top ' + holdings.length + ' holdings...');

    // Only show loading state if table is empty (first load)
    var isFirstLoad = tbody.querySelector('td[colspan]') !== null;

    if (isFirstLoad) {
        tbody.innerHTML = holdings.map(function (h) {
            return '<tr id="holding-row-' + h.Symbol.replace(/[^a-zA-Z0-9]/g, '_') + '">' + '<td>' + h.Symbol + '</td>' + '<td class="holding-price" style="text-align: right;">Loading...</td>' + '<td class="holding-change" style="text-align: right;">--</td>' + '<td class="holding-change-pct" style="text-align: right;">--</td>' + '<td class="holding-sparkline" style="text-align: center;"><div class="sparkline-container" id="sparkline-' + h.Symbol.replace(/[^a-zA-Z0-9]/g, '_') + '"></div></td>' + '<td style="text-align: right;">' + (h.Quantity ? h.Quantity.toFixed(4) : '--') + '</td>' + '<td class="holding-value" style="text-align: right;">--</td>' + '<td class="holding-time" style="color: #888; font-size: 0.85em;">--</td>' + '</tr>';
        }

        ).join('');
    }

    // Fetch prices one at a time to avoid rate limits
    var results = [];

    function fetchNext(index) {
        if (index >= holdings.length) {
            // All done - do a final render to ensure consistency
            renderWatchlistResults(results);
            if (btn) btn.disabled = false;
            if (statusSpan) statusSpan.textContent = 'Updated ' + new Date().toLocaleTimeString();
            addActivityLogEntry('success', 'Refreshed ' + results.length + ' prices');
            return;
        }

        var holding = holdings[index];
        if (statusSpan) statusSpan.textContent = 'Fetching ' + (index + 1) + ' of ' + holdings.length + '...';

        fetch('/api/live/price/' + encodeURIComponent(holding.Symbol)).then(function (response) {
            // Check for rate limiting
            if (response.status === 429) {
                addActivityLogEntry('warning', 'Rate limited - slowing down requests');
                // Longer delay on rate limit
                setTimeout(function () {
                    fetchNext(index);  // Retry same index
                }, 2000);
                return Promise.reject('rate_limited');
            }
            return response.json();
        }

        ).then(function (data) {
            if (data === 'rate_limited') return;

            var priceData = data.status === 'success' ? data : null;

            results.push({
                holding: holding,
                price: priceData
            }

            );
            // Update row immediately as data comes in
            updateHoldingRow(holding, priceData);

            // Longer delay to avoid rate limiting from Yahoo Finance
            setTimeout(function () {
                fetchNext(index + 1);
            }

                , 500);
        }

        ).catch(function (err) {
            if (err === 'rate_limited') return;

            results.push({
                holding: holding, price: null
            }

            );

            setTimeout(function () {
                fetchNext(index + 1);
            }

                , 500);
        }

        );
    }

    fetchNext(0);
}

/**
 * Update a single holding row with new price data (for incremental updates)
 */
function updateHoldingRow(holding, priceData) {
    var rowId = 'holding-row-' + holding.Symbol.replace(/[^a-zA-Z0-9]/g, '_');
    var row = document.getElementById(rowId);

    if (!row) return;

    var priceCell = row.querySelector('.holding-price');
    var changeCell = row.querySelector('.holding-change');
    var changePctCell = row.querySelector('.holding-change-pct');
    var valueCell = row.querySelector('.holding-value');
    var timeCell = row.querySelector('.holding-time');
    var sparklineCell = row.querySelector('.holding-sparkline');

    if (!priceData) {
        if (priceCell) priceCell.innerHTML = '<span style="color: #888;">N/A</span>';
        if (timeCell) timeCell.textContent = 'Error';
        return;
    }

    var changeClass = (priceData.change || 0) >= 0 ? 'positive' : 'negative';
    var changeSign = (priceData.change || 0) >= 0 ? '+' : '';
    var marketValue = priceData.price * holding.Quantity;
    var isMutualFund = priceData.is_mutual_fund || false;
    var priceTypeLabel = isMutualFund ? ' <span class="eod-badge">NAV</span>' : '';

    // Update symbol cell with MF badge if needed (only on first load)
    var symbolCell = row.querySelector('td:first-child');

    if (symbolCell && isMutualFund && !symbolCell.querySelector('.mutual-fund-badge')) {
        symbolCell.innerHTML = '<strong>' + holding.Symbol + '</strong> <span class="mutual-fund-badge">MF</span>';
    }

    if (priceCell) {
        priceCell.innerHTML = '$' + priceData.price.toFixed(2) + priceTypeLabel;
    }

    if (changeCell) {
        changeCell.textContent = changeSign + (priceData.change ? priceData.change.toFixed(2) : '0.00');
        changeCell.className = 'holding-change ' + changeClass;
    }

    if (changePctCell) {
        changePctCell.textContent = changeSign + (priceData.change_pct ? priceData.change_pct.toFixed(2) : '0.00') + '%';
        changePctCell.className = 'holding-change-pct ' + changeClass;
    }

    if (valueCell) {
        valueCell.textContent = '$' + formatNumber(marketValue);
    }

    if (timeCell) {
        timeCell.textContent = new Date().toLocaleTimeString();
    }

    // Update sparkline cell for mutual funds
    if (sparklineCell && isMutualFund) {
        sparklineCell.innerHTML = '<span class="eod-only">EOD Only</span>';
    }

    else if (sparklineCell && !isMutualFund) {
        // Fetch sparkline data
        var sparklineId = 'sparkline-' + holding.Symbol.replace(/[^a-zA-Z0-9]/g, '_');

        if (!sparklineCell.querySelector('.sparkline-container')) {
            sparklineCell.innerHTML = '<div class="sparkline-container" id="' + sparklineId + '"></div>';
        }

        fetchIntradayData(holding.Symbol, function (data) {
            if (data && data.prices) {
                createSparkline(sparklineId, data.prices, data.prev_close);
            }
        }

        );
    }
}

/**
 * Render watchlist results with sparklines (full re-render, used for initial load)
 */
function renderWatchlistResults(results) {
    var tbody = document.getElementById('liveWatchlistBody');
    if (!tbody) return;

    var html = results.map(function (r, index) {
        var h = r.holding;
        var p = r.price;
        var rowId = 'holding-row-' + h.Symbol.replace(/[^a-zA-Z0-9]/g, '_');
        var sparklineId = 'sparkline-' + h.Symbol.replace(/[^a-zA-Z0-9]/g, '_');

        if (!p) {
            return '<tr id="' + rowId + '">' + '<td>' + h.Symbol + '</td>' + '<td class="holding-price" style="text-align: right; color: #888;">N/A</td>' + '<td class="holding-change" style="text-align: right;">--</td>' + '<td class="holding-change-pct" style="text-align: right;">--</td>' + '<td class="holding-sparkline" style="text-align: center;"><div class="sparkline-container" id="' + sparklineId + '"></div></td>' + '<td style="text-align: right;">' + (h.Quantity ? h.Quantity.toFixed(4) : '--') + '</td>' + '<td class="holding-value" style="text-align: right;">--</td>' + '<td class="holding-time" style="color: #888;">Error</td>' + '</tr>';
        }

        var changeClass = (p.change || 0) >= 0 ? 'positive' : 'negative';
        var changeSign = (p.change || 0) >= 0 ? '+' : '';
        var marketValue = p.price * h.Quantity;

        // Check if mutual fund (EOD only pricing)
        var isMutualFund = p.is_mutual_fund || false;
        var priceTypeLabel = isMutualFund ? '<span class="eod-badge">NAV</span>' : '';
        var symbolDisplay = '<strong>' + h.Symbol + '</strong>' + (isMutualFund ? ' <span class="mutual-fund-badge">MF</span>' : '');

        return '<tr id="' + rowId + '">' + '<td>' + symbolDisplay + '</td>' + '<td class="holding-price" style="text-align: right;">$' + p.price.toFixed(2) + priceTypeLabel + '</td>' + '<td class="holding-change" style="text-align: right;" class="' + changeClass + '">' + changeSign + (p.change ? p.change.toFixed(2) : '0.00') + '</td>' + '<td class="holding-change-pct" style="text-align: right;" class="' + changeClass + '">' + changeSign + (p.change_pct ? p.change_pct.toFixed(2) : '0.00') + '%</td>' + '<td class="holding-sparkline" style="text-align: center;">' + (isMutualFund ? '<span class="eod-only">EOD Only</span>' : '<div class="sparkline-container" id="' + sparklineId + '"></div>') + '</td>' + '<td style="text-align: right;">' + h.Quantity.toFixed(4) + '</td>' + '<td class="holding-value" style="text-align: right;">$' + formatNumber(marketValue) + '</td>' + '<td class="holding-time" style="color: #888; font-size: 0.85em;">' + new Date().toLocaleTimeString() + '</td>' + '</tr>';
    }

    ).join('');

    tbody.innerHTML = html;

    // Load sparklines for each holding (with slight delay to ensure DOM is ready)
    // Skip mutual funds as they don't have intraday data
    setTimeout(function () {
        results.forEach(function (r) {
            if (r.price && !r.price.is_mutual_fund) {
                var sparklineId = 'sparkline-' + r.holding.Symbol.replace(/[^a-zA-Z0-9]/g, '_');

                fetchIntradayData(r.holding.Symbol, function (data) {
                    if (data && data.prices) {
                        createSparkline(sparklineId, data.prices, data.prev_close);
                    }
                }

                );
            }
        }

        );
    }

        , 100);
}

/**
 * Create the Live section (called from main.js loadTabContent)
 */
function createLiveSection() {
    if (!isServerMode) {
        var liveTab = document.getElementById('live');

        if (liveTab) {
            liveTab.innerHTML = '<div class="charts-grid"><div class="chart-container full-width"><h3>Live Updates Not Available</h3><p>Live updates are only available when running the dashboard through the server.</p><p>Run <code>python server.py</code> and open <a href="http://localhost:5000">http://localhost:5000</a></p></div></div>';
        }

        return;
    }

    // Connect to event source
    connectToEventSource();

    // Re-render charts now that the tab is visible (ECharts needs visible container)
    // Small delay to ensure tab content is fully rendered
    setTimeout(function () {
        refreshLiveSectorChart();
        initPortfolioChartWithStaticData();
    }, 50);
}

// ============================================
// CUSTOM WATCHLIST FUNCTIONS
// ============================================

/**
 * Load custom watchlist from localStorage
 */
function loadCustomWatchlist() {
    try {
        var stored = localStorage.getItem(WATCHLIST_STORAGE_KEY);

        if (stored) {
            customWatchlist = JSON.parse(stored);
            renderCustomWatchlist();
        }
    }

    catch (e) {
        console.error('Error loading watchlist:', e);
        customWatchlist = [];
    }
}

/**
 * Save custom watchlist to localStorage
 */
function saveCustomWatchlist() {
    try {
        localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(customWatchlist));
    }

    catch (e) {
        console.error('Error saving watchlist:', e);
    }
}

/**
 * Add symbol to custom watchlist
 */
function addToCustomWatchlist(symbol) {
    symbol = symbol.toUpperCase().trim();
    if (!symbol) return;

    // Check if already in watchlist
    if (customWatchlist.indexOf(symbol) !== -1) {
        addActivityLogEntry('info', symbol + ' is already in your watchlist');
        return;
    }

    customWatchlist.push(symbol);
    saveCustomWatchlist();
    addActivityLogEntry('success', 'Added ' + symbol + ' to watchlist');
    renderCustomWatchlist();

    // Fetch price for the new symbol
    fetchWatchlistSymbolPrice(symbol);
}

/**
 * Remove symbol from custom watchlist
 */
function removeFromCustomWatchlist(symbol) {
    var index = customWatchlist.indexOf(symbol);

    if (index !== -1) {
        customWatchlist.splice(index, 1);
        saveCustomWatchlist();
        addActivityLogEntry('info', 'Removed ' + symbol + ' from watchlist');
        renderCustomWatchlist();
    }
}

/**
 * Clear entire custom watchlist
 */
function clearCustomWatchlist() {
    if (customWatchlist.length === 0) return;

    if (confirm('Are you sure you want to clear your entire watchlist?')) {
        customWatchlist = [];
        saveCustomWatchlist();
        addActivityLogEntry('info', 'Watchlist cleared');
        renderCustomWatchlist();
    }
}

/**
 * Render custom watchlist table
 */
function renderCustomWatchlist() {
    var tbody = document.getElementById('customWatchlistBody');
    var countSpan = document.getElementById('customWatchlistCount');

    if (countSpan) {
        countSpan.textContent = '(' + customWatchlist.length + ')';
    }

    if (!tbody) return;

    if (customWatchlist.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No symbols in watchlist. Add symbols above.</td></tr>';
        return;
    }

    var html = customWatchlist.map(function (symbol) {
        var sparklineId = 'custom-sparkline-' + symbol.replace(/[^a-zA-Z0-9]/g, '_');
        return '<tr id="watchlist-row-' + symbol + '">' + '<td><strong>' + symbol + '</strong></td>' + '<td class="watchlist-price" style="text-align: right;">--</td>' + '<td class="watchlist-change" style="text-align: right;">--</td>' + '<td class="watchlist-change-pct" style="text-align: right;">--</td>' + '<td style="text-align: center;"><div class="sparkline-container" id="' + sparklineId + '"></div></td>' + '<td style="text-align: center;">' + '<button class="btn btn-sm btn-danger" onclick="removeFromCustomWatchlist(\'' + symbol + '\')">✕</button>' + '</td>' + '</tr>';
    }

    ).join('');

    tbody.innerHTML = html;
}

/**
 * Refresh all symbols in custom watchlist
 */
function refreshCustomWatchlist() {
    if (customWatchlist.length === 0) return;

    addActivityLogEntry('refresh', 'Refreshing custom watchlist (' + customWatchlist.length + ' symbols)...');

    customWatchlist.forEach(function (symbol, index) {

        // Stagger requests to avoid rate limiting
        setTimeout(function () {
            fetchWatchlistSymbolPrice(symbol);
        }

            , index * 250);
    }

    );
}

/**
 * Fetch price for a single watchlist symbol
 */
function fetchWatchlistSymbolPrice(symbol) {
    fetch('/api/live/price/' + encodeURIComponent(symbol)).then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            updateWatchlistRow(symbol, data);

            // Also fetch sparkline data
            fetchIntradayData(symbol, function (intradayData) {
                if (intradayData && intradayData.prices) {
                    var sparklineId = 'custom-sparkline-' + symbol.replace(/[^a-zA-Z0-9]/g, '_');
                    createSparkline(sparklineId, intradayData.prices, intradayData.prev_close);
                }
            }

            );
        }
    }

    ).catch(function (error) {
        console.error('Error fetching price for ' + symbol + ':', error);
    }

    );
}

/**
 * Update a single watchlist row with price data
 */
function updateWatchlistRow(symbol, data) {
    var row = document.getElementById('watchlist-row-' + symbol);
    if (!row) return;

    var priceCell = row.querySelector('.watchlist-price');
    var changeCell = row.querySelector('.watchlist-change');
    var changePctCell = row.querySelector('.watchlist-change-pct');

    if (priceCell) {
        priceCell.textContent = '$' + data.price.toFixed(2);
    }

    if (changeCell && data.change !== null) {
        var changeClass = data.change >= 0 ? 'positive' : 'negative';
        var sign = data.change >= 0 ? '+' : '';
        changeCell.textContent = sign + data.change.toFixed(2);
        changeCell.className = 'watchlist-change ' + changeClass;
    }

    if (changePctCell && data.change_pct !== null) {
        var pctClass = data.change_pct >= 0 ? 'positive' : 'negative';
        var pctSign = data.change_pct >= 0 ? '+' : '';
        changePctCell.textContent = pctSign + data.change_pct.toFixed(2) + '%';
        changePctCell.className = 'watchlist-change-pct ' + pctClass;
    }
}

// ============================================
// S&P 500 COMPARISON FUNCTIONS
// ============================================

/**
 * Refresh S&P 500 comparison data
 */
function refreshSPYComparison() {
    fetch('/api/live/price/SPY').then(function (response) {
        return response.json();
    }

    ).then(function (data) {
        if (data.status === 'success') {
            spyData = data;
            updateSPYDisplay();
        }
    }

    ).catch(function (error) {
        console.error('Error fetching SPY:', error);
    }

    );
}

/**
 * Update SPY comparison display
 */
function updateSPYDisplay() {
    var spyChangeElem = document.getElementById('spyChange');
    var vsMarketElem = document.getElementById('vsMarketDiff');

    if (!spyData) return;

    if (spyChangeElem) {
        var spyClass = spyData.change_pct >= 0 ? 'positive' : 'negative';
        var spySign = spyData.change_pct >= 0 ? '+' : '';
        spyChangeElem.textContent = 'S&P 500: ' + spySign + spyData.change_pct.toFixed(2) + '%';
        spyChangeElem.className = 'comparison-value ' + spyClass;
    }

    // Calculate vs market difference
    var dayGainPctElem = document.getElementById('dayGainPercent');

    if (vsMarketElem && dayGainPctElem) {
        var portfolioPctText = dayGainPctElem.textContent;
        var portfolioPct = parseFloat(portfolioPctText.replace(/[^0-9.-]/g, ''));

        if (!isNaN(portfolioPct) && spyData.change_pct !== null) {
            var diff = portfolioPct - spyData.change_pct;
            var diffClass = diff >= 0 ? 'positive' : 'negative';
            var diffSign = diff >= 0 ? '+' : '';
            var diffText = diff >= 0 ? 'Outperforming' : 'Underperforming';
            vsMarketElem.textContent = diffText + ' by ' + Math.abs(diff).toFixed(2) + '%';
            vsMarketElem.className = 'comparison-diff ' + diffClass;
        }
    }
}

// ============================================
// SESSION STATISTICS FUNCTIONS
// ============================================

/**
 * Initialize session statistics
 */
function initSessionStats() {
    sessionStats.startTime = new Date();
    sessionStats.refreshCount = 0;
    sessionStats.highValue = null;
    sessionStats.lowValue = null;
    sessionStats.firstValue = null;

    var startTimeElem = document.getElementById('sessionStartTime');

    if (startTimeElem) {
        startTimeElem.textContent = sessionStats.startTime.toLocaleTimeString();
    }
}

/**
 * Start session duration timer
 */
function startSessionDurationTimer() {
    setInterval(function () {
        var durationElem = document.getElementById('sessionDuration');
        if (!durationElem) return;

        var now = new Date();
        var diff = now - sessionStats.startTime;
        var hours = Math.floor(diff / 3600000);
        var minutes = Math.floor((diff % 3600000) / 60000);
        var seconds = Math.floor((diff % 60000) / 1000);

        var durationStr = '';
        if (hours > 0) durationStr += hours + 'h ';
        durationStr += minutes + 'm ' + seconds + 's';

        durationElem.textContent = durationStr;
    }

        , 1000);
}

/**
 * Update session statistics with new portfolio value
 */
function updateSessionStats(value) {
    if (sessionStats.firstValue === null) {
        sessionStats.firstValue = value;
    }

    if (sessionStats.highValue === null || value > sessionStats.highValue) {
        sessionStats.highValue = value;
    }

    if (sessionStats.lowValue === null || value < sessionStats.lowValue) {
        sessionStats.lowValue = value;
    }

    updateSessionStatsDisplay();
}

/**
 * Update session statistics display
 */
function updateSessionStatsDisplay() {
    var refreshCountElem = document.getElementById('totalRefreshes');
    var sessionHighElem = document.getElementById('sessionHigh');
    var sessionLowElem = document.getElementById('sessionLow');
    var sessionChangeElem = document.getElementById('sessionChange');

    if (refreshCountElem) {
        refreshCountElem.textContent = sessionStats.refreshCount;
    }

    if (sessionHighElem && sessionStats.highValue !== null) {
        sessionHighElem.textContent = '$' + formatNumber(sessionStats.highValue);
    }

    if (sessionLowElem && sessionStats.lowValue !== null) {
        sessionLowElem.textContent = '$' + formatNumber(sessionStats.lowValue);
    }

    if (sessionChangeElem && sessionStats.firstValue !== null && portfolioValueHistory.length > 0) {
        var currentValue = portfolioValueHistory[portfolioValueHistory.length - 1].value;
        var change = currentValue - sessionStats.firstValue;
        var changePct = (change / sessionStats.firstValue) * 100;
        var changeClass = change >= 0 ? 'positive' : 'negative';
        var sign = change >= 0 ? '+' : '';

        sessionChangeElem.textContent = sign + '$' + formatNumber(Math.abs(change)) + ' (' + sign + changePct.toFixed(2) + '%)';
        sessionChangeElem.className = 'stat-value ' + changeClass;
    }
}

// ============================================
// LIVE SECTOR BREAKDOWN CHART
// ============================================

/**
 * Refresh live sector breakdown chart
 */
function refreshLiveSectorChart() {
    var container = document.getElementById('liveSectorChart');
    if (!container) return;

    // Get holdings data - try multiple sources
    var holdings = null;
    if (typeof holdingsDetailData !== 'undefined' && Array.isArray(holdingsDetailData) && holdingsDetailData.length > 0) {
        holdings = holdingsDetailData;
    } else if (typeof holdingsData !== 'undefined' && Array.isArray(holdingsData) && holdingsData.length > 0) {
        holdings = holdingsData;
    }

    if (!holdings) {
        container.innerHTML = '<p style="text-align: center; color: #888; padding-top: 80px;">Holdings data not loaded yet.<br><small>Visit the Holdings tab first, or refresh.</small></p>';
        return;
    }

    // Calculate sector totals
    var sectorTotals = {}

        ;
    var totalValue = 0;

    holdings.forEach(function (holding) {
        var sector = holding.Sector || 'Unknown';
        var value = holding.CurrentValue || holding.MarketValue || 0;

        if (value > 0) {
            if (!sectorTotals[sector]) {
                sectorTotals[sector] = 0;
            }

            sectorTotals[sector] += value;
            totalValue += value;
        }
    }

    );

    // Convert to array and sort by value
    var sectorData = Object.keys(sectorTotals).map(function (sector) {
        return {
            name: sector,
            value: Math.round(sectorTotals[sector] * 100) / 100
        }

            ;
    }

    ).sort(function (a, b) {
        return b.value - a.value;
    }

    );

    if (sectorData.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #888; padding-top: 80px;">No sector data available</p>';
        return;
    }

    // Color palette for sectors
    var colors = ['#4CAF50',
        '#2196F3',
        '#FF9800',
        '#E91E63',
        '#9C27B0',
        '#00BCD4',
        '#FFC107',
        '#8BC34A',
        '#FF5722',
        '#607D8B',
        '#3F51B5',
        '#795548'];

    var option = {
        tooltip: {

            trigger: 'item',
            formatter: function (params) {
                return params.name + '<br/>$' + formatNumber(params.value) + ' (' + params.percent.toFixed(1) + '%)';
            }
        }

        ,
        series: [{

            type: 'pie',
            radius: ['40%',
                '70%'],
            center: ['50%',
                '50%'],
            avoidLabelOverlap: true,
            itemStyle: {
                borderRadius: 4,
                borderColor: '#1e1e1e',
                borderWidth: 2
            }

            ,
            label: {
                show: true,
                formatter: '{b}: {d}%',
                color: '#e0e0e0',
                fontSize: 11
            }

            ,
            labelLine: {

                show: true,
                lineStyle: {
                    color: '#888'
                }
            }

            ,
            data: sectorData.map(function (s, i) {
                return {

                    name: s.name,
                    value: s.value,
                    itemStyle: {
                        color: colors[i % colors.length]
                    }
                }

                    ;
            }

            )
        }

        ]
    }

        ;

    var chart = echarts.getInstanceByDom(container);

    if (chart) {
        // Dispose existing chart to ensure clean render
        chart.dispose();
    }
    chart = echarts.init(container);

    chart.setOption(option);
}

// Initialize on DOM ready if in server mode
document.addEventListener('DOMContentLoaded', function () {
    // Small delay to let other scripts initialize first
    setTimeout(initLive, 100);
}

);