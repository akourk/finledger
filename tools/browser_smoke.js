// User-level regression checks against the exact fictional Pages artifact.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const puppeteer = require('puppeteer');

async function checkAllocationGeometry(page) {
  // DOM/string probes cannot tell whether SVG arcs actually fill an annulus.
  // Exercise the shipped renderer with independent fictional positions, then
  // restore the demo's inputs; no fixture is written into the public artifact.
  const results = await page.evaluate(() => {
    const savedPositions = holdingsByAccount.slice();
    const savedDate = asOfDate;
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:-1000px;width:200px';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const legend = document.createElement('div');
    host.append(svg, legend);
    document.body.append(host);
    const results = [];
    try {
      asOfDate = LATEST_DATE;
      const unsafeLabel = 'Example <img src=x onerror="alert(1)"> & "quoted"';
      const cases = [
        ['single', [[unsafeLabel, 125]]],
        ['almost full, both arcs collapse', [['Main', 999999], ['Minor', 1]]],
        ['almost full, inner arc collapses', [['Main', 999986], ['Minor', 14]]],
        ['ordinary slices', [['Main', 75], ['Minor', 25]]],
        ['empty', []],
        ['zero', [['Main', 0]]],
      ];
      for (const field of ['account_group', 'account_type', 'sector']) {
        for (const [name, entries] of cases) {
          holdingsByAccount.splice(0, holdingsByAccount.length,
            ...entries.map(([label, value]) => ({[field]: label, value})));
          _renderOneAllocationDonut(svg, legend, field, Object.create(null));
          const paths = [...svg.querySelectorAll('path.alloc-slice')];
          const filled = (x, y) => paths.some(path => path.isPointInFill(new DOMPoint(x, y)));
          const positive = entries.some(([, value]) => value > 0);
          const ringVisible = [45, 135, 225, 315].every(degrees => {
            const radians = degrees * Math.PI / 180;
            return filled(100 + 65 * Math.cos(radians), 100 + 65 * Math.sin(radians));
          });
          results.push({name: `${field}: ${name}`, positive,
            pathCount: paths.length,
            ringVisible,
            holeEmpty: !filled(100, 100) && !filled(145, 100),
            outsideEmpty: !filled(185, 100),
            emptyTotal: svg.textContent === 'Total$0' && legend.textContent === '',
            escaped: name !== 'single' || (
              svg.querySelector('title')?.textContent === unsafeLabel + ': $125.00 (100.0%)' &&
              legend.querySelector('.alloc-name')?.textContent === unsafeLabel &&
              !host.querySelector('img, script, [onerror]')),
            ordinarySplit: name !== 'ordinary slices' || (
              paths[0].isPointInFill(new DOMPoint(145, 145)) &&
              !paths[0].isPointInFill(new DOMPoint(55, 55)) &&
              paths[1].isPointInFill(new DOMPoint(55, 55))),
          });
        }
      }
      return results;
    } finally {
      holdingsByAccount.splice(0, holdingsByAccount.length, ...savedPositions);
      asOfDate = savedDate;
      host.remove();
    }
  });
  for (const result of results) {
    assert.equal(result.ringVisible, result.positive, result.name + ': visible ring');
    assert.equal(result.holeEmpty, true, result.name + ': empty center');
    assert.equal(result.outsideEmpty, true, result.name + ': bounded ring');
    assert.equal(result.escaped, true, result.name + ': imported label remains text');
    assert.equal(result.ordinarySplit, true, result.name + ': proportional slices');
    if (!result.positive) {
      assert.equal(result.pathCount, 0, result.name + ': no empty slices');
      assert.equal(result.emptyTotal, true, result.name + ': zero total without legend');
    }
  }
}

async function checkHistoryControls(page) {
  await page.click('#tabbtn-overview');
  await page.select('#asOfPickerOverview', '2023-12-31');
  const snapshot = await page.$eval('#stats', el => el.textContent);
  const latest = await page.$eval('#historyLatestValue', el => el.textContent);
  const fullScope = await page.$eval('#historyScope', el => el.textContent);
  const balance = await page.$eval('#chartSvg', el => el.innerHTML);
  await page.focus('#histQuickView-composition');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'histQuickView-composition');
  assert.equal(await page.$eval('#histQuickView-composition', el => el.getAttribute('aria-pressed')), 'true');
  assert.notEqual(await page.$eval('#chartSvg', el => el.innerHTML), balance, 'account mix must redraw the chart');
  await page.focus('#histQuickView-balance');
  await page.keyboard.press('Space');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'histQuickView-balance');
  assert.equal(await page.$eval('#histQuickView-balance', el => el.getAttribute('aria-pressed')), 'true');
  await page.focus('#histQuickRange-ytd');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'histQuickRange-ytd');
  const range = await page.evaluate(() => ({dates: filteredHistory().map(h => h.date),
    scope: document.getElementById('historyScope').textContent}));
  assert.ok(range.dates.length > 1 && range.dates.every(date => date.startsWith('2026-')));
  assert.notEqual(range.scope, fullScope);
  assert.equal(range.scope, range.dates[0] + ' – ' + range.dates.at(-1));
  assert.notEqual(await page.$eval('#chartSvg', el => el.innerHTML), balance, 'YTD must redraw the chart observations');
  assert.equal(await page.$eval('#asOfPickerOverview', el => el.value), '2023-12-31');
  assert.equal(await page.$eval('#stats', el => el.textContent), snapshot, 'chart range must preserve the snapshot cards');
  assert.equal(await page.$eval('#historyLatestValue', el => el.textContent), latest);
  await page.focus('#historyQuickRangeSelect');
  await page.select('#historyQuickRangeSelect', 'custom');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'historyQuickRangeSelect');
  assert.equal(await page.$eval('#histControlsWrap', el => el.open), true);
  assert.equal(await page.$eval('#historyCustomFrom', el => el.value), range.dates[0], 'first Custom range starts at the visible range');
  assert.equal(await page.$eval('#historyCustomTo', el => el.value), range.dates.at(-1));
  for (const id of ['historyCustomFrom', 'historyCustomTo']) {
    assert.equal(await page.$eval('#' + id, el => el.checkVisibility()), true, id + ' must be visible');
  }
  await page.focus('#historyGroup-account-all');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'historyGroup-account-all');
  await page.focus('#historyOverlay-basis');
  await page.keyboard.press('Space');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'historyOverlay-basis');
  await page.click('#histQuickView-balance');
  await page.click('#histQuickRange-lifetime');
  await page.click('#histControlsWrap > summary');
  await page.select('#asOfPickerOverview', '2026-06-30');
}

async function checkHoldingsLayouts(page) {
  await page.click('#tabbtn-holdings');
  // Choose a fixture account sharing a symbol, so account membership and
  // account-specific amounts demonstrably differ without recreating any math.
  const fixture = await page.evaluate(() => {
    const shared = PPNL.by_symbol.find(row => row.accounts.length > 1 && row.value > 0);
    const account = PPNL.by_account.find(row => row.symbol === shared.symbol && row.value > 0 && row.value < shared.value);
    return {group: account.account_group, symbol: shared.symbol,
      symbolValue: fmtMoney(shared.value), accountValue: fmtMoney(account.value)};
  });
  await page.select('#byAssetAccountGroupFilter', fixture.group);
  const tableTotal = await page.$eval('#byAssetTotalValue', el => el.textContent);
  const tableRows = await page.$eval('#byAssetTbody', el => el.textContent);
  await page.click('#btnBoardBoard');
  const boardTotal = await page.$eval('#byAssetTotalValue', el => el.textContent);
  const paneTotal = '#boardPanes [data-pane="0"] .board-pane-total[title="Total market value of the rows shown"]';
  assert.equal(boardTotal, await page.$eval(paneTotal, el => el.textContent));
  assert.notEqual(boardTotal, tableTotal, 'Board heading must leave the Table account subtotal behind');
  assert.notEqual(await page.$eval('#boardPanes [data-pane="0"] tbody', el => el.textContent), tableRows);
  await page.select('#boardAccountFilter', fixture.group);
  assert.match(await page.$eval('#boardScopeContext', el => el.textContent), /all accounts holding those symbols/);
  const boardRow = async () => page.$$eval('#boardPanes [data-pane="0"] tbody tr',
    (rows, symbol) => rows.find(row => row.cells[0].textContent.trim() === symbol)?.textContent, fixture.symbol);
  assert.ok((await boardRow())?.includes(fixture.symbolValue), 'By Symbol keeps the exported all-account value');
  await page.click('button[onclick="setBoardGroupBy(\'account\')"]');
  assert.match(await page.$eval('#boardScopeContext', el => el.textContent), /amounts follow the selected account/);
  assert.ok((await boardRow())?.includes(fixture.accountValue), 'By Account displays the exported account value');
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), await page.$eval(paneTotal, el => el.textContent));
  await page.type('#boardSearch', 'fictional-no-matching-position');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'boardSearch');
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), '$0.00');
  assert.ok(await page.$('#boardPanes .board-empty'));
  await page.select('#asOfPickerHoldings', '2023-12-31');
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), '', 'historical Board notice must clear the latest total');
  assert.ok(await page.$('#boardBody .board-note'));
  await page.click('#boardBody .board-note button');
  await page.click('#boardSearch');
  await page.keyboard.press('Home');
  await page.keyboard.down('Shift');
  await page.keyboard.press('End');
  await page.keyboard.up('Shift');
  await page.keyboard.press('Backspace');
  await page.select('#boardAccountFilter', '');
  await page.click('button[onclick="setBoardGroupBy(\'symbol\')"]');
  await page.click('#btnBoardTable');
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), tableTotal, 'returning to Table restores its independent filter');
  await page.select('#byAssetAccountGroupFilter', '');
  await page.type('#byAssetSearch', 'fictional-no-matching-position');
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), '$0.00');
  await page.click('#byAssetSearch');
  await page.keyboard.press('Home');
  await page.keyboard.down('Shift');
  await page.keyboard.press('End');
  await page.keyboard.up('Shift');
  await page.keyboard.press('Backspace');
}

async function checkStickyIdentity(page, selector) {
  const initial = await page.$eval(selector, el => {
    el.scrollLeft = 0;
    const cell = el.querySelector('tbody tr td:not([colspan])');
    return {overflow: el.scrollWidth > el.clientWidth + 1,
      cue: getComputedStyle(el, '::before').content,
      annotated: el.dataset.overflowX, sticky: getComputedStyle(cell).position,
      x: cell.getBoundingClientRect().x};
  });
  assert.equal(initial.overflow, true, selector + ' should need horizontal scrolling at phone width');
  assert.equal(initial.annotated, 'true');
  assert.match(initial.cue, /Scroll horizontally/);
  assert.equal(initial.sticky, 'sticky');
  await page.focus(selector);
  await page.keyboard.press('ArrowRight');
  await page.waitForFunction(selector => document.querySelector(selector).scrollLeft > 20, {}, selector);
  const after = await page.$eval(selector, el => el.querySelector('tbody tr td:not([colspan])').getBoundingClientRect().x);
  assert.ok(Math.abs(after - initial.x) <= 1, selector + ' must keep the position identity visible while scrolling');
}

async function checkMobilePerformance(page) {
  await page.click('#tabbtn-performance');
  const account = await page.$eval('#perfAccountSelect', el => [...el.options].find(option => option.value && !option.value.startsWith('__')).value);
  await page.focus('#perfAccountSelect');
  await page.select('#perfAccountSelect', account);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'perfAccountSelect');
  assert.equal(await page.$eval('#perfSelectedHeading', el => el.textContent), account);
  const scope = await page.$eval('#perfScopeContext', el => el.textContent);
  const exportedValue = await page.evaluate(account => fmtMoney(history.at(-1).by_account_group[account]), account);
  assert.equal(await page.$eval('.perf-primary-summary .stat-card .value', el => el.textContent), exportedValue,
    'the selected account must own the primary value, using its exported snapshot total');
  assert.equal(await page.$$eval('.perf-primary-summary .stat-card', els => els.length), 4);
  assert.equal(await page.$eval('#perfLifetimeReference', el => el.open), false);
  assert.equal(await page.$eval('#perfLifetimeReference > summary', el => el.checkVisibility()), true);
  assert.equal(await page.$eval('.perf-mobile-account', el => el.checkVisibility()), true);
  assert.equal(await page.$eval('.perf-mobile-ranges', el => el.checkVisibility()), true);
  assert.equal(await page.$$eval('#perfControls .desktop-only', els => els.every(el => !el.checkVisibility())), true);
  const ytd = '.perf-mobile-ranges [data-perf-window="ytd"]';
  await page.focus(ytd);
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('data-perf-window')), 'ytd', 'keyboard range selection must preserve focus');
  assert.equal(await page.$eval(ytd, el => el.getAttribute('aria-pressed')), 'true');
  assert.notEqual(await page.$eval('#perfScopeContext', el => el.textContent), scope);
  assert.match(await page.$eval('#perfScopeContext', el => el.textContent), /2026-06-30/);
  assert.equal(await page.$eval('#perfSelectedHeading', el => el.textContent), account);
  await page.focus('#perfWindowSelect');
  const ytdScope = await page.$eval('#perfScopeContext', el => el.textContent);
  await page.select('#perfWindowSelect', '30day');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'perfWindowSelect');
  assert.notEqual(await page.$eval('#perfScopeContext', el => el.textContent), ytdScope,
    'a range chosen from the menu must change the displayed observation span');
  await page.select('#perfWindowSelect', 'custom');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'perfWindowSelect');
  for (const id of ['perfCustomStart', 'perfCustomEnd']) {
    assert.equal(await page.$eval('#' + id, el => el.checkVisibility()), true, id + ' must open from More ranges');
  }
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth <= 1), 'custom Performance controls must fit the mobile page');
  await page.focus('#perfCustomStart');
  await page.$eval('#perfCustomStart', el => { el.value = '2023-12-31'; el.dispatchEvent(new Event('change', {bubbles: true})); });
  assert.equal(await page.evaluate(() => document.activeElement.id), 'perfCustomStart');
  await page.focus('#perfCustomEnd');
  await page.$eval('#perfCustomEnd', el => { el.value = '2025-12-31'; el.dispatchEvent(new Event('change', {bubbles: true})); });
  assert.equal(await page.evaluate(() => document.activeElement.id), 'perfCustomEnd');
  assert.match(await page.$eval('#perfScopeContext', el => el.textContent), /2023-12-31 → 2025-12-31/);
  const historicalValue = await page.evaluate(account => fmtMoney(history.find(h => h.date === '2025-12-31').by_account_group[account]), account);
  assert.equal(await page.$eval('.perf-primary-summary .stat-card .value', el => el.textContent), historicalValue,
    'custom window value must use the selected account at the exported end snapshot');
  await page.click('.perf-mobile-ranges [data-perf-window="lifetime"]');
  await page.select('#perfAccountSelect', '');
}

async function main() {
  const file = path.resolve(process.argv[2] || '_site/index.html');
  const browser = await puppeteer.launch({headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox']});
  const downloads = await fs.mkdtemp(path.join(os.tmpdir(), 'finledger-demo-downloads-'));
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (/^https?:/.test(request.url())) { errors.push('Unexpected network request'); return request.abort(); }
    return request.continue();
  });
  try {
    await page.setViewport({width: 1440, height: 1000});
    await page.goto(pathToFileURL(file).href, {waitUntil: 'load'});
    assert.equal(await page.$eval('#demo-intro', el => el.textContent.includes('Fictional portfolio')), true);
    assert.equal(await page.evaluate(() => DATA.demo.synthetic), true);
    assert.equal(await page.evaluate(() => SNAPSHOT_DATE), '2026-06-30');
    await checkAllocationGeometry(page);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.className), 'skip-link');
    await page.click('#about-project summary');
    assert.equal(await page.$eval('#about-project', el => el.open), true);
    await page.click('#about-project summary');
    assert.deepEqual(await page.$$eval('#tabnav [role="tab"]', els => els.slice(0, 3).map(el => el.dataset.tab)),
      ['overview', 'holdings', 'performance']);
    await page.focus('#tabbtn-overview');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.$eval('#tabbtn-holdings', el => el.getAttribute('aria-selected')), 'true');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'tabbtn-holdings');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.$eval('#tabbtn-performance', el => el.getAttribute('aria-selected')), 'true');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'tabbtn-performance');

    const tabs = ['overview', 'holdings', 'transactions', 'options', 'retirement', 'planning', 'income', 'tax', 'crypto', 'performance'];
    for (const tab of tabs) {
      await page.click('#tabbtn-' + tab);
      assert.equal(await page.$eval('#tab-' + tab, el => el.classList.contains('active')), true, tab);
      assert.ok(await page.$eval('#tab-' + tab, el => el.innerText.trim().length > 80), tab + ' must render content');
      assert.equal(await page.$$eval('[data-render-error]', els => els.length), 0, tab + ' render error');
    }
    await checkHistoryControls(page);
    await checkHoldingsLayouts(page);

    async function typeEach(selector, value) {
      await page.click(selector);
      const existingLength = await page.$eval(selector, el => el.value.length);
      // Arrow keys + backspace behave consistently in numeric inputs on
      // macOS and Linux headless Chrome (platform select-all differs).
      for (let n = 0; n < existingLength + 1; n++) await page.keyboard.press('ArrowRight');
      for (let n = 0; n < existingLength + 1; n++) await page.keyboard.press('Backspace');
      for (const key of value) {
        await page.keyboard.type(key);
        assert.equal(await page.evaluate(() => document.activeElement.id), selector.slice(1), selector + ' lost focus');
      }
      assert.equal(await page.$eval(selector, el => el.value), value, selector + ' changed typed text');
    }
    await page.click('#tabbtn-planning');
    await typeEach('#retAnnualContrib', '12345');
    await typeEach('#retProjectionAge', '68');
    assert.equal(await page.evaluate(() => retirementAnnualContrib), 12345);
    await page.click('#tabbtn-tax');
    await typeEach('#taxShortRate', '25.5');
    await typeEach('#taxLongRate', '15.5');
    assert.equal(await page.evaluate(() => taxShortRate), 0.255);
    assert.equal(await page.evaluate(() => taxLongRate), 0.155);
    const taxDisclosure = await page.$('button[data-tax-disclosure]');
    assert.ok(taxDisclosure, 'sample must expose a tax disclosure');
    await taxDisclosure.focus();
    await page.keyboard.press('Space');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-expanded')), 'true');

    // A real browser download catches URL/blob/anchor regressions.
    await page.click('#tax-year-all');
    const session = await page.createCDPSession();
    await session.send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: downloads});
    const exportButton = await page.$('button[onclick="downloadForm8949()"]');
    assert.ok(exportButton, 'Form 8949 export must exist for sample disposals');
    await exportButton.click();
    const csvPath = path.join(downloads, 'form_8949_realized_gains.csv');
    for (let n = 0; n < 50; n++) {
      if (await fs.stat(csvPath).catch(() => false)) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    const csv = await fs.readFile(csvPath, 'utf8');
    assert.ok(csv.split('\n').length > 3, 'CSV must contain actual sample disposals');
    assert.ok(csv.includes('AAPL') || csv.includes('ADA'), 'CSV contains known fictional transactions');

    await page.click('#tabbtn-holdings');
    await page.select('#asOfPickerHoldings', '2023-12-31');
    assert.equal(await page.evaluate(() => {
      const table = document.querySelector('#byAssetBody table');
      const row = [...table.querySelectorAll('tbody tr')].find(tr => tr.textContent.includes('SMH'));
      const heads = [...table.querySelectorAll('thead th')];
      const index = heads.findIndex(th => /realized/i.test(th.textContent) && !/unrealized/i.test(th.textContent));
      return row && index >= 0 ? row.cells[index].textContent.trim() : null;
    }), '$0.00', 'historical holdings must exclude the later $800 realized gain');
    await page.select('#asOfPickerHoldings', '2026-06-30');
    await page.waitForSelector('button.lot-disclosure');
    const lot = await page.$('button.lot-disclosure');
    await lot.focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-expanded')), 'true');
    await page.keyboard.press('Space');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-expanded')), 'false');

    const risk = await page.evaluate(() => ({js: computeWindowedMetrics(null, 'lifetime'), py: ANALYTICS.monthly_pnl}));
    assert.ok(risk.py, 'Python monthly risk summary must be exported');
    assert.equal(risk.js.nMonths, risk.py.n_months_total);
    assert.equal(risk.js.nInRatio, risk.py.n_months_in_ratio);
    assert.equal(risk.js.sharpe, risk.py.sharpe);
    assert.equal(risk.js.sortino, risk.py.sortino);

    await page.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await page.goto(pathToFileURL(file).href + '#tax', {waitUntil: 'load'});
    assert.equal(await page.$eval('#tabbtn-tax', el => el.getAttribute('aria-selected')), 'true');
    assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
    for (const id of ['tax-gain-definitions', 'tax-bracket-definitions']) {
      await page.focus('#' + id + ' > summary');
      await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#' + id, el => el.open), true, id + ' must be keyboard accessible');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth <= 1), id + ' must fit the mobile page when open');
      await page.keyboard.press('Space');
      assert.equal(await page.$eval('#' + id, el => el.open), false);
    }
    for (const tab of tabs) {
      await page.click('#tabbtn-' + tab);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 1, `${tab} overflows mobile viewport by ${overflow}px`);
    }
    await page.click('#tabbtn-holdings');
    await checkStickyIdentity(page, '#byAssetBody .sticky-identity');
    await page.click('#btnBoardBoard');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth <= 1), 'Board must fit the mobile page');
    await checkStickyIdentity(page, '#boardPanes [data-pane="0"] .board-scroll');
    await page.click('#btnBoardTable');
    await checkMobilePerformance(page);
    await page.click('#perfViewBtnRisk');
    const riskOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(riskOverflow <= 1, `performance risk view overflows mobile viewport by ${riskOverflow}px`);
    const monthlyScroll = await page.$('[aria-label="Monthly returns table"]');
    assert.ok(monthlyScroll, 'monthly returns must have a scrollable region');
    await monthlyScroll.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('[aria-label="Monthly returns table"]').scrollLeft > 0);
    await page.click('#perfViewBtnReturns');
    assert.deepEqual(errors, []);
    console.log('PASS: 10 tabs and keyboard navigation, allocation ring geometry, History scope controls, independent Table/Board filters and totals, per-key input, historical gains, Python/JS monthly risk parity, keyboard disclosures, CSV export, deep link, touch layout and sticky identities, mobile Performance scopes/Returns/Risk, no console/network errors.');
  } finally {
    await browser.close();
    await fs.rm(downloads, {recursive: true, force: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
