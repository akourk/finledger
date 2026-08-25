// Matrix walk for tools/dashboard_probe.js.
//
// This file is concatenated onto the end of the dashboard bundle and
// evaluated with it as ONE script, so the bundle's top-level `const` /
// `let` declarations (`DATA`, `holdingsByAccount`, `TAB_RENDERERS`,
// `performanceAccountFilter`, `PERF_TWR_PRESETS`, …) are in scope here.
// See the loader comment in dashboard_probe.js for why running it as a
// separate eval does not work.
//
// Reached only through the probe.  `__probe` carries the extraction
// helpers; `__probe_results` is what the probe serialises to stdout.

(function () {
  const { NODES, parseCards, parseTables, CONSOLE_ERRORS } = globalThis.__probe;
  const results = { console_errors: [], performance: [], tabs: {} };

  const clearNodes = () => { for (const [, n] of NODES) n.innerHTML = ''; };

  // Renderers write into inner ids, not the tab panel itself.  Joining
  // every stub node's innerHTML avoids tracking which id each one picked
  // — and keeps working when one is renamed.
  const snapshotHtml = () => {
    let html = '';
    for (const [, n] of NODES) if (n.innerHTML) html += '\n' + n.innerHTML;
    return html;
  };

  // Invoke a tab's registered renderer DIRECTLY rather than going through
  // activateTab.  activateTab renders only on first activation, and the
  // bundle renders Overview during load — so an activateTab('overview')
  // here is a no-op that captures an empty snapshot and reports clean.
  // That is the vacuous-harness shape this file exists to avoid.
  const renderTab = (name) => {
    const fn = (typeof TAB_RENDERERS !== 'undefined') ? TAB_RENDERERS[name] : null;
    if (typeof fn !== 'function') return false;
    fn();
    return true;
  };

  // --- Performance: the (filter x window) matrix ---------------------
  const filters = [null, '__investments__', '__retirement__', '__taxable__'];
  try {
    const groups = [...new Set(
      holdingsByAccount.map(h => h.account_group).filter(Boolean))].sort();
    filters.push(...groups);
  } catch (e) {
    results.console_errors.push('could not enumerate account groups: ' + (e && e.message));
  }

  const windows = (typeof PERF_TWR_PRESETS !== 'undefined')
    ? PERF_TWR_PRESETS.filter(w => w !== 'custom')
    : ['lifetime'];

  for (const f of filters) {
    for (const w of windows) {
      clearNodes();
      try {
        performanceAccountFilter = f;
        performanceWindow = w;
        renderPerformance();
      } catch (e) {
        results.console_errors.push(
          'renderPerformance(' + f + ', ' + w + '): ' + (e && e.message));
        continue;
      }
      const html = snapshotHtml();
      results.performance.push({
        filter: f === null ? 'Total' : f,
        window: w,
        cards: parseCards(html),
        tables: parseTables(html),
      });
    }
  }

  // --- Every tab once, for the figures that are not window-dependent --
  //
  // Not every tab is driven by the lazy router.  Overview's stat cards,
  // the holdings tables and the transaction ledger are populated by
  // global functions that run once at load and write into containers the
  // TEMPLATE owns — `registerTabRenderer` never sees them.  Listing them
  // explicitly is the honest option; the alternative (capture whatever
  // the router happens to produce) reports an empty Overview as clean.
  const EXTRA_RENDERERS = {
    overview: ['renderStats', 'renderTopBarSummary', 'renderHistory'],
    holdings: ['renderHoldings'],
    transactions: ['renderHeader', 'renderTable'],
  };
  const TABS = ['overview', 'holdings', 'transactions', 'options', 'retirement',
    'planning', 'income', 'tax', 'crypto', 'performance'];

  for (const name of TABS) {
    clearNodes();
    let ran = false;
    for (const fname of (EXTRA_RENDERERS[name] || [])) {
      const fn = globalThis[fname];
      if (typeof fn !== 'function') {
        results.console_errors.push(
          'expected global renderer is missing: ' + fname + ' (tab ' + name + ')');
        continue;
      }
      try { fn(); ran = true; } catch (e) {
        results.console_errors.push(fname + ': ' + (e && e.message));
      }
    }
    try {
      if (renderTab(name)) ran = true;
    } catch (e) {
      results.console_errors.push('render ' + name + ': ' + (e && e.message));
    }
    if (!ran) {
      // A tab reachable through neither route is a gap in this map, not
      // a clean result — say so rather than omitting it silently.
      results.console_errors.push('no way to render tab: ' + name);
      continue;
    }
    const html = snapshotHtml();
    results.tabs[name] = { cards: parseCards(html), tables: parseTables(html) };
  }

  results.console_errors.push(...CONSOLE_ERRORS);
  globalThis.__probe_results = results;
})();
