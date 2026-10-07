// Secondary-page presentation checks use the dedicated fictional demo only.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {selectSection} = require('./mobile_navigation');

const DISCLOSURES = {
  retirement: ['retirementContributionHistory'],
  planning: ['planningScenarioMethod', 'planningFireCrossings', 'planningAnnualHistory'],
  income: ['incomeCashFlowDetails', 'incomePaycheckDetails', 'incomeBudgetDetails',
    'incomeForecastPositions', 'incomeAnnualSummary', 'incomeSources'],
  tax: ['taxIncomeBuildUp', 'taxRealizedYears', 'taxRealizedAssets'],
};

async function noOverflow(page, label) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    true, label + ': page stays within viewport');
}

async function editNumber(page, selector, value) {
  await page.focus(selector);
  await page.$eval(selector, element => element.select());
  for (const digit of value) {
    await page.keyboard.type(digit);
    assert.equal(await page.$eval(selector, element => document.activeElement === element),
      true, selector + ': focus survives each input event');
  }
  assert.equal(await page.$eval(selector, element => element.value), value,
    selector + ': complete typed value survives rerender');
}

async function checkSections(page, viewport) {
  for (const [section, ids] of Object.entries(DISCLOSURES)) {
    await selectSection(page, section);
    const panel = '#tab-' + section;
    const primarySelector = panel + ' .stat-card .value, ' + panel + ' .ds-value';
    assert.equal(await page.$(panel + ' [data-render-error]'), null, section + ': renderer succeeds');
    const primaryFigures = await page.$$eval(primarySelector, elements => elements.map(element => element.textContent));
    assert.ok(primaryFigures.length, section + ': main figures remain available');
    assert.equal(await page.$$eval(primarySelector, elements => elements.every(element => element.checkVisibility())),
      true, section + ': main figures are visible with details closed');
    for (const id of ids) {
      assert.equal(await page.$eval('#' + id, element => element.open), false,
        id + ': supporting details start collapsed');
      const text = await page.$eval('#' + id, element => element.textContent);
      await page.focus('#' + id + ' > summary');
      await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#' + id, element => element.open), true,
        id + ': native keyboard reveal');
      assert.equal(await page.$eval('#' + id, element => element.textContent), text,
        id + ': reveal preserves figures and text');
      assert.equal(await page.$eval('#' + id, element =>
        [...element.children].some(child => child.tagName !== 'SUMMARY'
          && child.checkVisibility() && child.textContent.trim())), true,
      id + ': expanded content is available');
    }
    await noOverflow(page, viewport + ' ' + section + ' expanded');
    await selectSection(page, 'overview');
    await selectSection(page, section);
    assert.deepEqual(await page.$$eval(primarySelector, elements => elements.map(element => element.textContent)),
      primaryFigures, section + ': disclosure/navigation changes preserve the main figures');
    for (const id of ids) {
      assert.equal(await page.$eval('#' + id, element => element.open), true,
        id + ': navigation preserves reader choice');
    }
    // Closing one section must not close its siblings or reopen on rerender.
    await page.focus('#' + ids[0] + ' > summary');
    await page.keyboard.press('Space');
    await selectSection(page, 'overview');
    await selectSection(page, section);
    assert.equal(await page.$eval('#' + ids[0], element => element.open), false,
      ids[0] + ': closed state also survives rerender');
    for (const id of ids.slice(1)) {
      assert.equal(await page.$eval('#' + id, element => element.open), true,
        id + ': sibling state stays independent');
    }
    await noOverflow(page, viewport + ' ' + section);
  }
}

async function checkInputs(page) {
  await selectSection(page, 'planning');
  await page.click('#planningScenarioMethod > summary');
  const projectionBefore = await page.$eval('#planningContent table', element => element.textContent);
  await editNumber(page, '#retAnnualContrib', '12345');
  await editNumber(page, '#retProjectionAge', '72');
  assert.notEqual(await page.$eval('#planningContent table', element => element.textContent), projectionBefore,
    'scenario inputs still update projection results');
  assert.equal(await page.$eval('#planningScenarioMethod', element => element.open), true,
    'projection changes retain disclosure state');
  await page.click('#mc-retirement');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'mc-retirement',
    'scenario button retains focus');
  assert.equal(await page.$eval('#planningScenarioMethod', element => element.open), true);
  await page.click('#mc-all-accounts');
  assert.equal(await page.$eval('#planningFireCrossings', element => element.open), true,
    'FIRE detail choice survives a scenario where that section is absent');
  await noOverflow(page, 'planning after inputs');

  await selectSection(page, 'tax');
  assert.equal(await page.$eval('#taxShortRate', element => element.closest('details')), null,
    'selected rates stay visible outside supporting detail');
  // Keep the pointer target clear of fixed mobile navigation across font metrics.
  await page.$eval('#taxIncomeBuildUp > summary', element =>
    element.scrollIntoView({block: 'center', behavior: 'instant'}));
  await page.click('#taxIncomeBuildUp > summary');
  assert.equal(await page.$eval('#taxIncomeBuildUp', element => element.open), true,
    'pointer reveal opens detail before editing rates');
  await editNumber(page, '#taxShortRate', '27');
  await editNumber(page, '#taxLongRate', '18');
  assert.equal(await page.$eval('#taxIncomeBuildUp', element => element.open), true,
    'rate changes retain detail state');
  await page.click('#tax-year-all');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'tax-year-all');
  assert.equal(await page.$eval('#taxIncomeBuildUp', element => element.open), true,
    'year changes retain detail state');
  assert.match(await page.$eval('#taxIncomeBuildUp summary', element => element.textContent), /\d{4}/,
    'collapsed rate build-up names its assumptions year');
  const latestYear = await page.evaluate(() => String(snapshotYear()));
  await page.click('#tax-year-' + latestYear);
  await page.click('#taxResetRates');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'taxResetRates',
    'reset rates retains focus');
  assert.equal(await page.$eval('#taxIncomeBuildUp', element => element.open), true);
  const visibleTaxText = await page.$eval('#taxContent', root => {
    const copy = root.cloneNode(true);
    copy.querySelectorAll('details').forEach(element => element.remove());
    return copy.textContent;
  });
  for (const warning of ['Realized gains are YTD only', 'Latest taxable holdings',
    'Wash Risk', 'Heuristic check', 'CSV export · all recorded years']) {
    assert.ok(visibleTaxText.includes(warning), warning + ': essential scope/caveat stays outside details');
  }
  await noOverflow(page, 'tax after inputs');
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
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      await page.setViewport(viewport);
      await page.goto(url, {waitUntil: 'load'});
      assert.equal(await page.evaluate(() => DATA.demo?.synthetic === true
        && !!document.getElementById('demo-intro')), true, 'input must be the fictional public demo');
      await checkSections(page, viewport.width);
      await checkInputs(page);
    }
    assert.deepEqual(failures, [], 'no script, console or network failures');
    console.log('PASS: Secondary disclosures, visible scope and warnings, keyboard reveal, independent state across navigation and input, projection updates, input focus, desktop/mobile overflow.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
