/**
 * Main Dashboard Entry Point
 * Initializes all dashboard modules
 */

// Tab Navigation
function initTabs() {
    var tabButtons = document.querySelectorAll('.tab-btn');
    var tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var tabId = button.getAttribute('data-tab');

            tabButtons.forEach(function (btn) { btn.classList.remove('active'); });
            tabContents.forEach(function (content) { content.classList.remove('active'); });

            button.classList.add('active');
            var targetTab = document.getElementById(tabId);
            if (targetTab) targetTab.classList.add('active');

            // Lazy-load tab content
            loadTabContent(tabId);

            // Trigger chart resize for proper rendering
            window.dispatchEvent(new Event('resize'));
        });
    });
}

// Lazy Load Tab Content
var loadedTabs = {};

function loadTabContent(tabId) {
    if (loadedTabs[tabId]) return;

    switch (tabId) {
        case 'overview':
            break;
        case 'accounts':
            initAccountCards();
            break;
        case 'holdings':
            createAllHoldingsTable();
            initHoldingsSearch();
            setTimeout(function () {
                createHoldingsTreemap();
                createHoldingsHeatmap();
            }, 100);
            break;
        case 'performance':
            if (typeof initPerformance === 'function') {
                initPerformance();
            }
            break;
        case 'income':
            createIncomeProjection();
            createIncomeSection();
            createDividendCalendar();
            break;
        case 'history':
            createHistoricalChart();
            createGrowthComparisonChart();
            createValueVsInvestedChart();
            createMonthlyReturnsChart();
            break;
        case 'tax':
            createTaxSection();
            break;
        case 'retirement':
            createRetirementSection();
            break;
        case 'options':
            createOptionsSection();
            break;
        case 'crypto':
            createCryptoSection();
            break;
        case 'transactions':
            createTransactionsSection();
            break;
        case 'corporate-actions':
            createCorporateActionsSection();
            break;
    }

    loadedTabs[tabId] = true;
}

// Initialize Dashboard
function initDashboard() {
    // Initialize refresh indicator
    initRefreshIndicator();

    // Check if data is loaded
    if (typeof portfolioData === 'undefined') {
        var errorMsg = document.getElementById('errorMessage');
        if (errorMsg) errorMsg.style.display = 'block';
        var skeleton = document.getElementById('loadingSkeleton');
        if (skeleton) skeleton.classList.add('hidden');
        return;
    }

    // Hide loading skeleton and show dashboard content
    var skeleton = document.getElementById('loadingSkeleton');
    if (skeleton) skeleton.classList.add('hidden');
    var content = document.getElementById('dashboardContent');
    if (content) content.style.display = 'block';

    // Update timestamp
    var timestamp = document.getElementById('timestamp');
    if (timestamp && portfolioData.GeneratedAt) {
        timestamp.textContent = 'Generated: ' + new Date(portfolioData.GeneratedAt).toLocaleString();
    }

    // Initialize tabs
    initTabs();

    // Create overview sections
    createPortfolioHero();
    createSummaryCards();
    createAllocationChart();
    createSectorAllocationChart();
    createReturnsChart();
    createPortfolioGrowthChart();
    createTopHoldingsTable();
    createRecentActivity();
    createTopMovers();

    // Mark overview as loaded
    loadedTabs['overview'] = true;
}

// Start on DOM ready
document.addEventListener('DOMContentLoaded', initDashboard);
