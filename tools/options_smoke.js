// Options interaction checks use only the dedicated fictional demo.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {selectSection} = require('./mobile_navigation');

async function noOverflow(page, label) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    true, label + ': the page fits its viewport');
}

async function activateKeyboard(page, selector) {
  await page.$eval(selector, element => element.scrollIntoView({block: 'center', behavior: 'instant'}));
  await page.focus(selector);
  await page.keyboard.press('Enter');
}

async function checkScope(page) {
  const result = await page.evaluate(() => {
    const opt = ANALYTICS.options;
    const range = _optWindowRange();
    const chosen = row => (!_optAccountFilter || row.account_group === _optAccountFilter)
      && (!range || row.close_date >= range[0] && row.close_date <= range[1]);
    const positions = opt.completed_positions.filter(chosen);
    const closes = opt.closed_trades.filter(chosen);
    const opened = opt.open_contracts.filter(row => !_optAccountFilter || row.account_group === _optAccountFilter);
    const cards = Object.fromEntries([...document.querySelectorAll('#optionsContent .stat-card')]
      .map(card => [card.querySelector('.label')?.textContent.trim(), card.querySelector('.value')?.textContent.trim()]));
    const realized = closes.some(row => row.realized == null) ? null : closes.reduce((sum, row) => sum + row.realized, 0);
    const openTable = [...document.querySelectorAll('#optionsContent table')]
      .find(table => table.caption?.textContent === 'Open option positions');
    return {cards, positions: positions.length, openPositions: opened.length,
      openContracts: _optQty(opened.reduce((sum, row) => sum + row.qty, 0)),
      expectedRealized: _optSigned(realized), completed: fmtSigned(positions.reduce((sum, row) => sum + row.realized, 0)),
      selectedAccount: _optAccountFilter,
      renderedOpenAccounts: [...openTable.tBodies[0].rows].filter(row => row.cells.length > 1)
        .map(row => row.cells[0].textContent.trim()),
      expectedOpenAccounts: opened.map(row => row.account_group)};
  });
  assert.equal(result.cards['Completed Positions'], String(result.positions));
  assert.equal(result.cards['Open Positions'], String(result.openPositions));
  assert.equal(result.cards['Open Contracts'], result.openContracts);
  assert.equal(result.cards['Realized P&L'], result.expectedRealized);
  assert.equal(result.cards['Completed Position P&L'], result.completed);
  assert.deepEqual(result.renderedOpenAccounts.sort(), result.expectedOpenAccounts.sort());
  return result;
}

async function checkOptions(page, viewport) {
  await selectSection(page, 'options');
  assert.equal(await page.$('#tab-options [data-render-error]'), null, 'Options renders successfully');
  const lifetime = await checkScope(page);
  const source = await page.evaluate(() => ({trades: ANALYTICS.options.stats.trades,
    openContracts: _optQty(ANALYTICS.options.stats.open_contract_count),
    winRate: _optPct(ANALYTICS.options.stats.win_rate)}));
  assert.equal(lifetime.positions, source.trades);
  assert.equal(lifetime.cards['Open Contracts'], source.openContracts);
  assert.equal(lifetime.cards['Position Win Rate'], source.winRate);
  assert.ok(lifetime.positions > 0, 'the fictional demo exercises completed positions');

  const disclosures = ['options-metric-guide', 'options-contract-stats', 'options-closing-executions'];
  for (const id of disclosures) {
    assert.equal(await page.$eval('#' + id, element => element.open), false);
    await activateKeyboard(page, '#' + id + ' > summary');
    assert.equal(await page.$eval('#' + id, element => element.open), true,
      id + ': keyboard reveals definitions and detail');
  }
  await noOverflow(page, viewport + ' with details open');

  await activateKeyboard(page, '#opt-window-ytd');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'opt-window-ytd',
    'window filter keeps keyboard focus after rerender');
  const year = await checkScope(page);
  assert.equal(year.openContracts, lifetime.openContracts,
    'current open quantity does not change with the outcome date window');
  for (const id of disclosures) {
    assert.equal(await page.$eval('#' + id, element => element.open), true,
      id + ': window changes retain reader choice');
  }

  const accountButton = '#opt-account-0';
  assert.ok(await page.$(accountButton), 'the demo exposes an account filter');
  await activateKeyboard(page, accountButton);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'opt-account-0',
    'account filter keeps keyboard focus after rerender');
  assert.equal(await page.$eval(accountButton, element => element.getAttribute('aria-pressed')), 'true');
  await checkScope(page);
  for (const id of disclosures) {
    assert.equal(await page.$eval('#' + id, element => element.open), true,
      id + ': account changes retain reader choice');
  }
  await activateKeyboard(page, '#options-contract-stats > summary');
  await activateKeyboard(page, '#opt-window-lifetime');
  assert.equal(await page.$eval('#options-contract-stats', element => element.open), false,
    'closed contract details stay closed after rerender');
  await activateKeyboard(page, '#opt-account-all');
  const restored = await checkScope(page);
  assert.deepEqual(restored.cards, lifetime.cards,
    'restoring lifetime and all accounts restores the original figures');
  assert.equal(await page.evaluate(() => /\b(?:NaN|undefined|Infinity)\b/.test(
    document.querySelector('#optionsContent').textContent)), false, 'undefined figures are not printed');
  await noOverflow(page, viewport + ' after filter changes');
}

(async () => {
  const browser = await puppeteer.launch({headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox']});
  try {
    const page = await browser.newPage();
    const failures = [];
    page.on('pageerror', () => failures.push('Script error'));
    page.on('console', message => { if (message.type() === 'error') failures.push('Console error'); });
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (/^https?:/.test(request.url())) {
        failures.push('Unexpected network request');
        return request.abort();
      }
      return request.continue();
    });
    const url = pathToFileURL(path.resolve(process.argv[2] || '_site/index.html')).href;
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844, isMobile: true, hasTouch: true}]) {
      await page.setViewport(viewport);
      await page.goto(url, {waitUntil: 'load'});
      assert.equal(await page.evaluate(() => DATA.demo?.synthetic === true), true,
        'Options smoke requires the dedicated fictional demo');
      await checkOptions(page, `${viewport.width}px`);
    }
    assert.deepEqual(failures, []);
    console.log('PASS: Options position/contract quantities, exported lifetime figures, date/account filters, keyboard focus, disclosure state, mobile overflow, and no script/network errors.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
