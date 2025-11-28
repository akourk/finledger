/**
 * Corporate Actions Module
 * Handles display of stock splits, mergers, spinoffs, and liquidations
 */

function createCorporateActionsSection() {
    var actions = getCorporateActionsData();

    if (actions.length === 0) {
        showCorporateActionsEmpty();
        return;
    }

    createCorporateActionsSummary(actions);
    createCorporateActionsTimelineChart(actions);
    createCorporateActionsTypeChart(actions);
    createCostBasisImpact(actions);
    createStockSplitsTable(actions);
    createSpinoffsMergersTable(actions);
    createAllCorporateActionsTable(actions);
    initCorporateActionsFilters(actions);
}

function showCorporateActionsEmpty() {
    var container = document.getElementById('corporateActionsSummary');
    if (!container) return;

    container.innerHTML = '<div class="corp-actions-empty">' +
        '<div class="corp-actions-empty-icon">📊</div>' +
        '<h3>No Corporate Actions Found</h3>' +
        '<p>Stock splits, mergers, and spinoffs will appear here when detected.</p>' +
        '</div>';
}

function createCorporateActionsSummary(actions) {
    var container = document.getElementById('corporateActionsSummary');
    if (!container) return;

    var counts = {
        splits: 0, reverseSplits: 0, mergers: 0, spinoffs: 0, liquidations: 0, total: actions.length
    };

    actions.forEach(function (action) {
        var type = action.type.toLowerCase();
        if (type === 'split') counts.splits++;
        else if (type === 'reverse split') counts.reverseSplits++;
        else if (type.includes('merger') || type.includes('acquisition')) counts.mergers++;
        else if (type.includes('spinoff') || type.includes('spin-off')) counts.spinoffs++;
        else if (type.includes('liquidation')) counts.liquidations++;
    });

    container.innerHTML =
        '<div class="summary-cards">' +
        '<div class="card"><div class="card-label">Total Actions</div><div class="card-value">' + counts.total + '</div></div>' +
        '<div class="card"><div class="card-label">Stock Splits</div><div class="card-value" style="color: var(--accent-blue);">' + counts.splits + '</div></div>' +
        '<div class="card"><div class="card-label">Reverse Splits</div><div class="card-value" style="color: var(--accent-muted);">' + counts.reverseSplits + '</div></div>' +
        '<div class="card"><div class="card-label">Mergers</div><div class="card-value" style="color: #a855f7;">' + counts.mergers + '</div></div>' +
        '<div class="card"><div class="card-label">Spinoffs</div><div class="card-value" style="color: #f59e0b;">' + counts.spinoffs + '</div></div>' +
        '<div class="card"><div class="card-label">Liquidations</div><div class="card-value" style="color: var(--accent-red);">' + counts.liquidations + '</div></div>' +
        '</div>';
}

function createCorporateActionsTimelineChart(actions) {
    var canvas = document.getElementById('corporateActionsTimelineChart');
    if (!canvas) return;

    var yearCounts = {};
    actions.forEach(function (action) {
        var year = action.date.substring(0, 4);
        yearCounts[year] = (yearCounts[year] || 0) + 1;
    });

    var years = Object.keys(yearCounts).sort();
    var counts = years.map(function (y) { return yearCounts[y]; });

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: years,
            datasets: [{ label: 'Corporate Actions', data: counts, backgroundColor: '#60a5fa', borderRadius: 4 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888' } },
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888', stepSize: 1 } }
            }
        }
    });
}

function createCorporateActionsTypeChart(actions) {
    var canvas = document.getElementById('corporateActionsTypeChart');
    if (!canvas) return;

    var typeCounts = {};
    actions.forEach(function (action) { typeCounts[action.type] = (typeCounts[action.type] || 0) + 1; });

    var labels = Object.keys(typeCounts);
    var data = Object.values(typeCounts);
    var colors = labels.map(function (label) {
        var t = label.toLowerCase();
        if (t === 'split') return '#60a5fa';
        if (t === 'reverse split') return '#6b7280';
        if (t.includes('merger')) return '#a855f7';
        if (t.includes('spinoff')) return '#f59e0b';
        if (t.includes('liquidation')) return '#f87171';
        return '#888888';
    });

    new Chart(canvas, {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: data, backgroundColor: colors, borderColor: '#252525', borderWidth: 2 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'right', labels: { color: '#888', font: { size: 11 }, padding: 12 } } }
        }
    });
}

function createCostBasisImpact(actions) {
    var container = document.getElementById('costBasisImpact');
    if (!container) return;

    var totalCash = 0, cashActions = 0;
    actions.forEach(function (action) {
        if (action.cash && action.cash > 0) { totalCash += action.cash; cashActions++; }
    });

    container.innerHTML =
        '<div class="cost-basis-stats">' +
        '<div class="stat-row"><span class="stat-label">Cash Received (Mergers/Liquidations)</span><span class="stat-value positive">' + formatCurrency(totalCash) + '</span></div>' +
        '<div class="stat-row"><span class="stat-label">Cash Transactions</span><span class="stat-value">' + cashActions + '</span></div>' +
        '<div class="stat-note"><p>Stock splits adjust cost basis per share but don\'t create taxable events.</p><p>Mergers and spinoffs may have tax implications - consult your tax advisor.</p></div>' +
        '</div>';
}

function createStockSplitsTable(actions) {
    var tbody = document.getElementById('stockSplitsBody');
    if (!tbody) return;

    var splits = actions.filter(function (a) {
        var t = a.type.toLowerCase();
        return t === 'split' || t === 'reverse split';
    }).sort(function (a, b) { return new Date(b.date) - new Date(a.date); });

    if (splits.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No stock splits recorded</td></tr>';
        return;
    }

    var html = '';
    splits.forEach(function (split) {
        var typeClass = split.type.toLowerCase() === 'split' ? 'split' : 'reverse-split';
        html += '<tr><td>' + formatDate(split.date) + '</td><td><strong>' + split.symbol + '</strong></td>';
        html += '<td><span class="corp-type-badge ' + typeClass + '">' + split.type + '</span></td>';
        html += '<td>' + split.account + '</td><td>' + (split.details || '-') + '</td></tr>';
    });
    tbody.innerHTML = html;
}

function createSpinoffsMergersTable(actions) {
    var tbody = document.getElementById('spinoffsMergersBody');
    if (!tbody) return;

    var filtered = actions.filter(function (a) {
        var t = a.type.toLowerCase();
        return t.includes('spinoff') || t.includes('merger') || t.includes('liquidation');
    }).sort(function (a, b) { return new Date(b.date) - new Date(a.date); });

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No spinoffs or mergers recorded</td></tr>';
        return;
    }

    var html = '';
    filtered.forEach(function (action) {
        var typeClass = getCorpActionTypeClass(action.type);
        html += '<tr><td>' + formatDate(action.date) + '</td><td><strong>' + action.symbol + '</strong></td>';
        html += '<td><span class="corp-type-badge ' + typeClass + '">' + action.type + '</span></td>';
        html += '<td>' + action.account + '</td>';
        html += '<td style="text-align: right;">' + (action.shares ? formatNumber(action.shares, 4) : '-') + '</td>';
        html += '<td style="text-align: right;">' + (action.cash ? formatCurrency(action.cash) : '-') + '</td>';
        html += '<td>' + (action.cusip || '-') + '</td><td>' + (action.details || '-') + '</td></tr>';
    });
    tbody.innerHTML = html;
}

function createAllCorporateActionsTable(actions) {
    window.allCorporateActions = actions;
    renderCorporateActionsTable(actions);
}

function renderCorporateActionsTable(actions) {
    var tbody = document.getElementById('allCorporateActionsBody');
    if (!tbody) return;

    var sorted = actions.slice().sort(function (a, b) { return new Date(b.date) - new Date(a.date); });

    if (sorted.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No matching corporate actions</td></tr>';
        return;
    }

    var html = '';
    sorted.forEach(function (action) {
        var typeClass = getCorpActionTypeClass(action.type);
        html += '<tr><td>' + formatDate(action.date) + '</td>';
        html += '<td><span class="corp-type-badge ' + typeClass + '">' + action.type + '</span></td>';
        html += '<td><strong>' + action.symbol + '</strong></td><td>' + action.account + '</td>';
        html += '<td style="text-align: right;">' + (action.shares ? formatNumber(action.shares, 4) : '-') + '</td>';
        html += '<td style="text-align: right;">' + (action.cash ? formatCurrency(action.cash) : '-') + '</td>';
        html += '<td>' + (action.cusip || '-') + '</td><td>' + (action.details || '-') + '</td></tr>';
    });
    tbody.innerHTML = html;
}

function getCorpActionTypeClass(type) {
    var t = type.toLowerCase();
    if (t === 'split') return 'split';
    if (t === 'reverse split') return 'reverse-split';
    if (t.includes('merger')) return 'merger';
    if (t.includes('spinoff')) return 'spinoff';
    if (t.includes('liquidation')) return 'liquidation';
    return '';
}

function initCorporateActionsFilters(actions) {
    var filterType = document.getElementById('caFilterType');
    var filterYear = document.getElementById('caFilterYear');
    var searchBox = document.getElementById('caSearch');

    if (filterYear) {
        var years = {};
        actions.forEach(function (a) { years[a.date.substring(0, 4)] = true; });
        Object.keys(years).sort().reverse().forEach(function (year) {
            var option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            filterYear.appendChild(option);
        });
    }

    function applyFilters() {
        var typeFilter = filterType ? filterType.value : '';
        var yearFilter = filterYear ? filterYear.value : '';
        var searchTerm = searchBox ? searchBox.value.toLowerCase() : '';

        var filtered = window.allCorporateActions.filter(function (action) {
            if (typeFilter && !action.type.toLowerCase().includes(typeFilter.toLowerCase())) return false;
            if (yearFilter && !action.date.startsWith(yearFilter)) return false;
            if (searchTerm && !action.symbol.toLowerCase().includes(searchTerm)) return false;
            return true;
        });
        renderCorporateActionsTable(filtered);
    }

    if (filterType) filterType.addEventListener('change', applyFilters);
    if (filterYear) filterYear.addEventListener('change', applyFilters);
    if (searchBox) searchBox.addEventListener('input', applyFilters);
}
