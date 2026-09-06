// User-level regression checks against the exact fictional Pages artifact.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const puppeteer = require('puppeteer');

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
    assert.deepEqual(errors, []);
    console.log('PASS: 10 tabs, per-key input, historical gains, Python/JS monthly risk parity, keyboard disclosures, CSV export, deep link, mobile layout, no console/network errors.');
  } finally {
    await browser.close();
    await fs.rm(downloads, {recursive: true, force: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
