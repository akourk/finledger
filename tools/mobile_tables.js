// Behavioral checks for compact records against an independently fictional demo.
// Usage: node tools/mobile_tables.js /path/to/fictional-demo/index.html
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {AxePuppeteer} = require('@axe-core/puppeteer');
const {selectSection} = require('./mobile_navigation');

async function input(page, selector, value) {
  await page.$eval(selector, (element, text) => {
    element.value = text;
    element.dispatchEvent(new Event('input', {bubbles: true}));
  }, value);
}

async function checkGeometry(page, container) {
  const result = await page.$eval(container, element => {
    const records = [...element.querySelectorAll(':scope > details')];
    return {
      pageFits: document.documentElement.scrollWidth <= innerWidth + 1,
      recordsFit: records.every(record => record.scrollWidth <= record.clientWidth + 1),
      targets: records.every(record => record.querySelector('summary').getBoundingClientRect().height >= 44),
      visible: element.getBoundingClientRect().width > 0,
    };
  });
  assert.deepEqual(result, {pageFits:true, recordsFit:true, targets:true, visible:true});
}

async function sameRows(page, compact, desktop) {
  const result = await page.evaluate((mobile, table) => {
    const keys = selector => [...document.querySelectorAll(selector)]
      .map(record => record.dataset.recordKey);
    return {mobile:keys(mobile + ' > [data-record-key]'), table:keys(table + ' > [data-record-key]')};
  }, compact, desktop);
  assert.deepEqual(result.mobile, result.table, 'compact order and scope match the desktop table');
}

async function checkHoldings(page, viewport) {
  await selectSection(page, 'holdings');
  await sameRows(page, '#byAssetCompact', '#byAssetTbody');
  await checkGeometry(page, '#byAssetCompact');
  assert.equal(await page.$eval('#byAssetTbody', el => el.closest('.table-wrap').getBoundingClientRect().width), 0);
  const row = await page.evaluate(() => {
    const lp = document.querySelector('#byAssetCompact .compact-lots');
    const record = lp.closest('.compact-record');
    return {key:record.dataset.recordKey, lotKey:lp.dataset.lotkey};
  });
  const summary = '#byAssetCompact > .compact-record > summary';
  await page.$$eval(summary, (items, key) => items.find(el => el.parentElement.dataset.recordKey === key).focus(), row.key);
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact > details')]
    .find(el => el.dataset.recordKey === key).open, row.key), true);
  const fields = await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact > details')]
    .find(el => el.dataset.recordKey === key).querySelector('dl').textContent, row.key);
  assert.match(fields, /Quantity/);
  assert.match(fields, /Cost basis/);
  await page.$$eval('#byAssetCompact .compact-lots > summary', (items, key) =>
    items.find(el => el.parentElement.dataset.lotkey === key).focus(), row.lotKey);
  await page.keyboard.press('Space');
  assert.equal(await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact .compact-lots')]
    .find(el => el.dataset.lotkey === key).open, row.lotKey), true);
  await checkGeometry(page, '#byAssetCompact');

  await input(page, '#byAssetSearch', 'fictional-no-matching-position');
  assert.match(await page.$eval('#byAssetCompact', el => el.textContent), /No positions match/);
  assert.equal(await page.$eval('#byAssetTotalValue', el => el.textContent), '$0.00');
  await input(page, '#byAssetSearch', '');
  assert.equal(await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact > details')]
    .find(el => el.dataset.recordKey === key).open, row.key), true, 'position disclosure survives a filter round trip');
  assert.equal(await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact .compact-lots')]
    .find(el => el.dataset.lotkey === key).open, row.lotKey), true, 'lot disclosure survives a filter round trip');

  await page.select('#byAssetCompact [data-compact-sort]', 'symbol');
  await sameRows(page, '#byAssetCompact', '#byAssetTbody');
  assert.equal(await page.evaluate(() => document.activeElement.matches('#byAssetCompact [data-compact-sort]')), true);
  await page.click('#byAssetCompact [data-compact-direction]');
  await sameRows(page, '#byAssetCompact', '#byAssetTbody');
  assert.equal(await page.evaluate(() => document.activeElement.matches('#byAssetCompact [data-compact-direction]')), true);

  await page.$$eval(summary, (items, key) => items.find(el => el.parentElement.dataset.recordKey === key).focus(), row.key);
  await page.setViewport({width:1440, height:1000});
  await page.waitForFunction(() => document.querySelector('#byAssetCompact').children.length === 0);
  assert.equal(await page.evaluate(key => document.activeElement.matches('button.lot-disclosure')
    && document.activeElement.dataset.lotkey === key && document.activeElement.getAttribute('aria-expanded') === 'true', row.lotKey), true,
  'rotation retains open lots and moves focus to the desktop control');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-expanded')), 'false');
  await page.setViewport(viewport);
  await page.waitForSelector('#byAssetCompact > details');
  assert.equal(await page.evaluate(key => document.activeElement.parentElement.dataset.recordKey === key, row.key), true);
  assert.equal(await page.evaluate(key => [...document.querySelectorAll('#byAssetCompact .compact-lots')]
    .find(el => el.dataset.lotkey === key).open, row.lotKey), false, 'desktop lot closure carries back to compact');

  await page.click('#holdingsFilters > summary');
  const group = await page.$eval('#byAssetAccountGroupFilter', el => el.options[1].value);
  await page.select('#byAssetAccountGroupFilter', group);
  await sameRows(page, '#byAssetCompact', '#byAssetTbody');
  assert.equal(await page.$$eval('#byAssetCompact > details', (items, value) =>
    items.every(el => JSON.parse(el.dataset.recordKey)[0] === value), group), true);
  await page.select('#byAssetAccountGroupFilter', '');
  const dates = await page.$eval('#asOfPickerHoldings', el => [...el.options].map(option => option.value));
  await page.select('#asOfPickerHoldings', dates.at(-1));
  assert.equal(await page.$$eval('#byAssetCompact .compact-lots', items => items.length), 0, 'historical positions cannot expose current lots');
  await sameRows(page, '#byAssetCompact', '#byAssetTbody');
  await page.select('#asOfPickerHoldings', dates[0]);

  const escaped = await page.evaluate(() => {
    const marker = 'Example <img src=x onerror="alert(1)"> & "quoted"';
    const record = {account_group:marker, account_type:'Taxable', symbol:marker, sector:marker,
      quantity:2, price:25, value:50, cost_basis:40, unrealized_gain:10};
    holdingsByAccount.push(record);
    byAssetSearch = marker.toLowerCase();
    renderByAssetTable();
    const container = document.getElementById('byAssetCompact');
    const valid = container.textContent.includes(marker) && !container.querySelector('img, script, [onerror]');
    holdingsByAccount.pop();
    byAssetSearch = '';
    renderByAssetTable();
    return valid;
  });
  assert.equal(escaped, true, 'imported position fields remain text');
}

async function checkTransactions(page, viewport) {
  await selectSection(page, 'transactions');
  await sameRows(page, '#transactionsCompact', '#tbody');
  await checkGeometry(page, '#transactionsCompact');
  assert.equal(await page.$eval('#tbody', el => el.closest('.table-wrap').getBoundingClientRect().width), 0);
  await page.focus('#transactionsCompact > details > summary');
  await page.keyboard.press('Enter');
  const key = await page.$eval('#transactionsCompact > details', el => el.dataset.recordKey);
  assert.match(await page.$eval('#transactionsCompact > details > dl', el => el.textContent), /Account/);
  assert.match(await page.$eval('#transactionsCompact > details > dl', el => el.textContent), /Description/);
  await input(page, '#search', 'fictional-no-matching-transaction');
  assert.match(await page.$eval('#transactionsCompact', el => el.textContent), /No transactions match/);
  await input(page, '#search', '');
  assert.equal(await page.evaluate(value => [...document.querySelectorAll('#transactionsCompact > details')]
    .find(el => el.dataset.recordKey === value).open, key), true);
  await page.select('#transactionsCompact [data-compact-sort]', 'amount');
  await sameRows(page, '#transactionsCompact', '#tbody');
  await page.click('#transactionsCompact [data-compact-direction]');
  await sameRows(page, '#transactionsCompact', '#tbody');
  assert.equal(await page.evaluate(() => document.activeElement.matches('#transactionsCompact [data-compact-direction]')), true);
  const date = await page.evaluate(() => txns[Math.floor(txns.length / 2)].date);
  await page.click('#transactionFilters > summary');
  await input(page, '#dateFrom', date);
  await input(page, '#dateTo', date);
  await sameRows(page, '#transactionsCompact', '#tbody');
  assert.equal(await page.$$eval('#transactionsCompact > details > summary', (items, value) =>
    items.length > 0 && items.every(el => el.textContent.includes(value)), date), true, 'date endpoints are inclusive');
  await page.click('#resetTransactionFilters');
  await sameRows(page, '#transactionsCompact', '#tbody');

  await page.focus('#transactionsCompact > details > summary');
  await page.setViewport({width:1440, height:1000});
  await page.waitForFunction(() => document.querySelector('#transactionsCompact').children.length === 0);
  assert.equal(await page.evaluate(() => document.activeElement.matches('#headerRow th')), true,
    `${viewport.width}x${viewport.height}: rotation restores focus to a visible transaction header`);
  await page.setViewport(viewport);
  await page.waitForSelector('#transactionsCompact > details');
  assert.equal(await page.evaluate(() => document.activeElement.matches('#transactionsCompact [data-compact-sort]')), true);

  // A previous row focus must not be reused after the reader moves elsewhere.
  await page.focus('#search');
  await page.setViewport({width:1440, height:1000});
  await page.waitForFunction(() => document.querySelector('#transactionsCompact').children.length === 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'search',
    'rotation does not steal focus from transaction search');
  await page.setViewport(viewport);
  await page.waitForSelector('#transactionsCompact > details');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'search',
    'returning to compact retains unrelated input focus');

  await page.focus('#transactionsCompact > details > summary');
  await page.evaluate(() => document.activeElement.blur());
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true);
  await page.setViewport({width:1440, height:1000});
  await page.waitForFunction(() => document.querySelector('#transactionsCompact').children.length === 0);
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true,
    'rotation respects an intentional blur to the document');
  await page.setViewport(viewport);
  await page.waitForSelector('#transactionsCompact > details');
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true,
    'returning to compact does not reuse intentionally released row focus');

  const escaped = await page.evaluate(() => {
    const marker = 'Fictional <svg onload="alert(1)"> & "quoted"';
    const record = {date:'2024-01-02', action:marker, symbol:marker, account_group:marker,
      quantity:1, price:25, amount:25, description:marker};
    txns.push(record);
    searchInput.value = marker;
    renderTable();
    const container = document.getElementById('transactionsCompact');
    const valid = container.textContent.includes(marker) && !container.querySelector('svg, script, [onload]');
    txns.pop();
    searchInput.value = '';
    renderTable();
    return valid;
  });
  assert.equal(escaped, true, 'transaction summary and hidden detail fields remain escaped');
  await checkGeometry(page, '#transactionsCompact');
}

async function main() {
  const file = path.resolve(process.argv[2] || '_site/index.html');
  const browser = await puppeteer.launch({headless:true,
    executablePath:process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args:['--no-sandbox', '--disable-setuid-sandbox']});
  try {
    for (const viewport of [{width:320,height:740}, {width:390,height:844}, {width:956,height:440}]) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      await page.setRequestInterception(true);
      page.on('request', request => {
        if (/^https?:/.test(request.url())) { errors.push('Unexpected network request'); return request.abort(); }
        request.continue();
      });
      await page.setViewport(viewport);
      await page.goto(pathToFileURL(file).href, {waitUntil:'load'});
      assert.equal(await page.evaluate(() => DATA.demo?.synthetic === true), true,
        'input must be the fictional demo');
      await checkHoldings(page, viewport);
      if (viewport.width === 390) {
        const result = await new AxePuppeteer(page).include('#byAssetCompact').analyze();
        assert.deepEqual(result.violations.filter(item => ['serious', 'critical'].includes(item.impact))
          .map(item => item.id), [], 'compact holdings accessibility');
      }
      await checkTransactions(page, viewport);
      if (viewport.width === 390) {
        const result = await new AxePuppeteer(page).include('#transactionsCompact').analyze();
        assert.deepEqual(result.violations.filter(item => ['serious', 'critical'].includes(item.impact))
          .map(item => item.id), [], 'compact transactions accessibility');
      }
      assert.deepEqual(await page.$$eval('[id]', elements => {
        const seen = new Set();
        return elements.filter(el => { const duplicate = seen.has(el.id); seen.add(el.id); return duplicate; }).map(el => el.id);
      }), [], 'no duplicate IDs');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: compact Holdings and Transactions at 320/390 portrait and 956x440 landscape; keyboard disclosures, lots, filters, sorting, date scope, escaping, empty states, desktop rotation, focus, and row parity. Actual iPhone Safari was not tested.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
