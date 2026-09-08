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
    await page.click('a[href="#about-project"]');
    assert.equal(await page.$eval('#about-project', el => el.open), true);
    await page.click('#about-project summary');
    await page.focus('#tabbtn-overview');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.$eval('#tabbtn-holdings', el => el.getAttribute('aria-selected')), 'true');

    const tabs = ['overview', 'holdings', 'transactions', 'options', 'retirement', 'planning', 'income', 'tax', 'crypto', 'performance'];
    for (const tab of tabs) {
      await page.click('#tabbtn-' + tab);
      assert.equal(await page.$eval('#tab-' + tab, el => el.classList.contains('active')), true, tab);
      assert.ok(await page.$eval('#tab-' + tab, el => el.innerText.trim().length > 80), tab + ' must render content');
      assert.equal(await page.$$eval('[data-render-error]', els => els.length), 0, tab + ' render error');
    }

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

    await page.goto(pathToFileURL(file).href + '#tax', {waitUntil: 'load'});
    assert.equal(await page.$eval('#tabbtn-tax', el => el.getAttribute('aria-selected')), 'true');
    await page.setViewport({width: 375, height: 812});
    for (const tab of tabs) {
      await page.click('#tabbtn-' + tab);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 1, `${tab} overflows mobile viewport by ${overflow}px`);
    }
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
    console.log('PASS: 10 tabs, allocation ring geometry, per-key input, historical gains, Python/JS monthly risk parity, keyboard disclosures, CSV export, deep link, mobile Returns/Risk layout and table scrolling, no console/network errors.');
  } finally {
    await browser.close();
    await fs.rm(downloads, {recursive: true, force: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
