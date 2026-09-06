// Accessibility gate for the generated dashboard.
//
// WHY THIS EXISTS
// ---------------
// The dashboard is one self-contained HTML file whose entire body is
// built by JavaScript at view time, so nothing about it can be checked
// by looking at the file.  A `role="tab"` in the template is only worth
// something if `activateTab` keeps `aria-selected` in step with it, and
// a colour token is only worth something at the pairing it actually
// renders at.  Both are runtime facts.
//
// `tests/test_accessibility.py` pins the attributes that ARE static —
// it is the fast, dependency-free half.  This is the other half: a real
// browser, real computed styles, and axe-core.
//
// STATES, NOT PAGES
// -----------------
// Ten tabs render lazily, and several carry sub-views behind a toggle
// (Performance's Returns/Risk, Holdings' Table/Board) plus collapsed
// <details> panels.  A scan that only visits the ten default states
// never sees the drawdown chart, the monthly P&L heatmap, the daily P&L
// bars, or the board — roughly a third of the rendered surface, and
// disproportionately the chart-shaped third where the problems live.
// So each entry below is a STATE: a tab plus whatever it takes to get
// the rest of that tab on screen.
//
// Usage:  node tools/a11y_check.js <dashboard.html>
// Exits non-zero on any violation of impact `serious` or `critical`.

'use strict';

const { pathToFileURL } = require('url');
const puppeteer = require('puppeteer');
const { AxePuppeteer } = require('@axe-core/puppeteer');

// Fail on these; `minor` / `moderate` are reported but do not gate, so
// the build stays honest about what it is actually promising.
const GATING = new Set(['serious', 'critical']);

const STATES = [
  { tab: 'overview', name: 'overview' },
  { tab: 'holdings', name: 'holdings (table)' },
  {
    tab: 'holdings', name: 'holdings (board)',
    setup: () => { if (typeof setBoardLayout === 'function') setBoardLayout('board'); },
    teardown: () => { if (typeof setBoardLayout === 'function') setBoardLayout('table'); },
  },
  { tab: 'transactions', name: 'transactions' },
  { tab: 'options', name: 'options' },
  { tab: 'retirement', name: 'retirement' },
  { tab: 'planning', name: 'planning' },
  { tab: 'income', name: 'income' },
  { tab: 'tax', name: 'tax' },
  { tab: 'crypto', name: 'crypto' },
  { tab: 'performance', name: 'performance (returns)' },
  {
    tab: 'performance', name: 'performance (risk)',
    setup: () => { if (typeof setPerfView === 'function') setPerfView('risk'); },
    teardown: () => { if (typeof setPerfView === 'function') setPerfView('returns'); },
  },
];

// Collapsed content is `display:none`, which axe correctly skips — so
// open everything before scanning or the panels inside never get looked
// at.  Runs per state because most <details> do not exist until their
// tab has rendered.
function openAllDetails() {
  document.querySelectorAll('details').forEach(d => { d.open = true; });
}

async function main() {
  const file = process.argv[2];
  if (!file) {
    console.error('usage: node tools/a11y_check.js <dashboard.html>');
    return 2;
  }

  const browser = await puppeteer.launch({
    headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  // A renderer that throws leaves its panel empty, and an empty panel
  // has no violations — a silent pass.  Treat any page error as a
  // failure of this check.
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') pageErrors.push(m.text()); });

  await page.goto(pathToFileURL(file).href, { waitUntil: 'load' });

  const byRule = new Map();
  let scanned = 0;

  for (const state of STATES) {
    await page.evaluate(t => { activateTab(t); }, state.tab);
    if (state.setup) await page.evaluate(state.setup);
    await page.evaluate(openAllDetails);
    // Several charts measure their container in a queueMicrotask and
    // re-render; give the layout a beat to settle before measuring it.
    await new Promise(r => setTimeout(r, 200));

    const results = await new AxePuppeteer(page).analyze();
    scanned++;

    for (const v of results.violations) {
      if (!GATING.has(v.impact)) continue;
      if (!byRule.has(v.id)) {
        byRule.set(v.id, {
          id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl,
          states: new Set(), nodes: 0, samples: [],
        });
      }
      const e = byRule.get(v.id);
      e.states.add(state.name);
      e.nodes += v.nodes.length;
      for (const n of v.nodes) {
        if (e.samples.length >= 5) break;
        e.samples.push(`${n.target.join(' ')} — ${n.failureSummary.split('\n')[1] || ''}`.trim());
      }
    }

    if (state.teardown) await page.evaluate(state.teardown);
  }

  await browser.close();

  console.log(`axe-core: scanned ${scanned} dashboard state(s) in ${file}`);

  if (pageErrors.length) {
    console.log('');
    console.log('Uncaught page errors (a panel that fails to render scans clean):');
    pageErrors.forEach(e => console.log('  ' + e));
  }

  if (byRule.size) {
    for (const e of [...byRule.values()].sort((a, b) => b.nodes - a.nodes)) {
      console.log('');
      console.log(`[${e.impact}] ${e.id} — ${e.help}`);
      console.log(`  ${e.nodes} node(s) across: ${[...e.states].join(', ')}`);
      console.log(`  ${e.helpUrl}`);
      e.samples.forEach(s => console.log(`    - ${s}`));
    }
    console.log('');
    console.log(`FAIL: ${byRule.size} rule(s) violated at serious/critical severity.`);
    return 1;
  }

  if (pageErrors.length) {
    console.log('');
    console.log('FAIL: the dashboard threw while rendering.');
    return 1;
  }

  console.log('PASS: no serious or critical accessibility violations.');
  return 0;
}

main().then(code => process.exit(code)).catch(err => {
  console.error(err);
  process.exit(2);
});
