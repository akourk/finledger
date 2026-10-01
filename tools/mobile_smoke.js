// Phone navigation and viewport checks use only the verified fictional demo.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {selectSection} = require('./mobile_navigation');

const SECTIONS = ['overview', 'holdings', 'performance', 'transactions', 'options',
  'retirement', 'planning', 'income', 'tax', 'crypto'];
const PRIMARY = ['overview', 'holdings', 'performance'];
const VIEWPORTS = [
  {width:320, height:740}, {width:390, height:844}, {width:430, height:932},
  {width:720, height:1000}, {width:721, height:1000}, {width:720, height:390}, {width:956, height:440}, {width:874, height:402},
];

async function noOverflow(surface, state) {
  assert.equal(await surface.evaluate(() =>
    document.documentElement.scrollWidth <= innerWidth + 1), true, state + ' horizontal overflow');
}

async function checkFontFallback(surface, state) {
  // Linux can reach the dashboard's generic font fallback. Its wider glyphs
  // must stay within table scroll regions at the desktop/mobile boundary.
  const style = await surface.addStyleTag({content:'body { font-family: monospace; }'});
  try {
    for (const section of SECTIONS) {
      await selectSection(surface, section);
      await noOverflow(surface, state + ' monospace ' + section);
    }
  } finally {
    await style.evaluate(element => element.remove());
  }
}

async function currentSection(surface, name, state) {
  const result = await surface.evaluate(() => ({
    panels: [...document.querySelectorAll('.tab-panel.active')].map(el => el.id),
    desktop: [...document.querySelectorAll('#tabnav [aria-selected="true"]')].map(el => el.dataset.tab),
    mobile: [...document.querySelectorAll('#mobile-navigation [aria-current="page"]')]
      .map(el => el.dataset.mobileTab || 'more'),
    heading: document.getElementById('mobile-section-heading').textContent,
    sourceLabel: document.querySelector('.tab-btn.active').textContent.trim(),
    moreLabel: document.getElementById('mobile-more-label').textContent,
    sheetOpen: document.getElementById('mobile-sections').open,
    failed: !!document.querySelector('[data-render-error]'),
  }));
  assert.deepEqual(result.panels, ['tab-' + name], state + ' one active panel');
  assert.deepEqual(result.desktop, [name], state + ' desktop selection stays synchronized');
  assert.deepEqual(result.mobile, [PRIMARY.includes(name) ? name : 'more'], state + ' mobile selection');
  assert.equal(result.heading, result.sourceLabel, state + ' section heading');
  assert.equal(result.moreLabel, PRIMARY.includes(name) ? 'More' : result.sourceLabel, state + ' More label');
  assert.equal(result.sheetOpen, false, state + ' sheet closes after choosing a section');
  assert.equal(result.failed, false, state + ' section renders');
}

async function targetSizes(surface, state) {
  const sizes = await surface.$$eval('#mobile-navigation button', buttons => buttons.map(button => {
    const box = button.getBoundingClientRect();
    return {width:box.width, height:box.height};
  }));
  assert.equal(sizes.length, 4, state + ' four bottom destinations');
  assert.ok(sizes.every(box => box.width >= 44 && box.height >= 44), state + ' bottom targets at least 44px');
  const geometry = await surface.$eval('#mobile-navigation', element => {
    const box = element.getBoundingClientRect();
    return {bottom:box.bottom, viewport:innerHeight};
  });
  assert.ok(Math.abs(geometry.bottom - geometry.viewport) <= 1, state + ' bottom nav stays within viewport');
}

async function checkSheet(surface, state, axeSource) {
  await surface.click('#mobile-more');
  assert.equal(await surface.$eval('#mobile-sections', element => element.open), true);
  assert.equal(await surface.$eval('#mobile-more', element => element.getAttribute('aria-expanded')), 'true');
  assert.equal(await surface.evaluate(() => {
    document.getElementById('mobile-more').focus();
    return document.getElementById('mobile-sections').contains(document.activeElement);
  }), true, state + ' modal prevents background control focus');
  const buttons = await surface.$$eval('#mobile-section-list button', elements => elements.map(element => {
    const box = element.getBoundingClientRect();
    return {name:element.dataset.mobileTab, width:box.width, height:box.height};
  }));
  assert.deepEqual(buttons.map(button => button.name), SECTIONS, state + ' all source sections in order');
  assert.ok(buttons.every(button => button.width >= 44 && button.height >= 44), state + ' sheet targets at least 44px');
  const closeSize = await surface.$eval('#mobile-sections-close', element => {
    const box = element.getBoundingClientRect();
    return {width:box.width, height:box.height};
  });
  assert.ok(closeSize.width >= 44 && closeSize.height >= 44, state + ' Close target at least 44px');
  for (let step = 0; step < buttons.length + 8; step++) {
    await surface.page().keyboard.press('Tab');
    const focus = await surface.evaluate(() => ({
      inside:document.getElementById('mobile-sections').contains(document.activeElement),
      documentBoundary:document.activeElement === document.body && document.body.tabIndex < 0
        && !document.body.hasAttribute('tabindex') && document.getElementById('mobile-sections').matches(':modal'),
      id:document.activeElement.id,
    }));
    // Native iframe dialogs make their own document inert. Tab may also visit
    // the enclosing viewer toolbar; it does not promise an application-wide
    // focus trap. Chrome exposes nonfocusable BODY at traversal boundaries.
    assert.ok(focus.inside || focus.documentBoundary, state + ' modal focus escaped to ' + focus.id);
    if (focus.documentBoundary && surface.parentFrame()) {
      const parentFocus = await surface.page().evaluate(() => {
        const active = document.activeElement;
        return active === document.body && document.body.tabIndex < 0
          || active.matches('#snapshot-open, #snapshot-reset, #snapshot-help summary, .skip-link[href="#snapshot-host"], #snapshot-host iframe:not(.snapshot-pending)');
      });
      assert.equal(parentFocus, true, state + ' traversal reaches only intentional parent viewer controls');
    }
  }
  if (axeSource) {
    await surface.evaluate(axeSource);
    const violations = await surface.evaluate(async () => (await axe.run(document.getElementById('mobile-sections')))
      .violations.filter(item => ['serious', 'critical'].includes(item.impact)).map(item => item.id));
    assert.deepEqual(violations, [], state + ' sheet accessibility');
  }
  // Give this document the key after any legitimate parent-toolbar traversal.
  await surface.focus('#mobile-sections-close');
  await surface.page().keyboard.press('Escape');
  await surface.waitForFunction(() => !document.getElementById('mobile-sections').open
    && document.getElementById('mobile-more').getAttribute('aria-expanded') === 'false');
  assert.equal(await surface.evaluate(() => document.activeElement.id), 'mobile-more', state + ' Escape restores More focus');
  assert.equal(await surface.$eval('#mobile-more', element => element.getAttribute('aria-expanded')), 'false');
  await surface.click('#mobile-more');
  await surface.click('#mobile-sections-close');
  await surface.waitForFunction(() => !document.getElementById('mobile-sections').open
    && document.getElementById('mobile-more').getAttribute('aria-expanded') === 'false');
  assert.equal(await surface.evaluate(() => document.activeElement.id), 'mobile-more', state + ' Close restores More focus');
}

async function deepScrollSwitch(surface, state) {
  await selectSection(surface, 'transactions');
  await surface.evaluate(() => window.scrollTo(0, document.scrollingElement.scrollHeight));
  assert.ok(await surface.evaluate(() => window.scrollY) > 100, state + ' test starts deep in the ledger');
  await selectSection(surface, 'performance');
  const position = await surface.$eval('#mobile-section-heading', element => element.getBoundingClientRect().top);
  assert.ok(position >= 0 && position <= 32, state + ' switching reveals the new section heading');
  assert.equal(await surface.evaluate(() => document.activeElement.id), 'tab-performance', state + ' selection focuses panel');
}

async function checkDisclosures(surface, state) {
  const keyboard = surface.page().keyboard;
  // Scroll the target into the iframe's usable center before clicking; a
  // browser's minimal scroll can leave it underneath the fixed bottom bar.
  const click = async selector => {
    await surface.$eval(selector, el => el.scrollIntoView({block:'center'}));
    await surface.click(selector);
  };
  await selectSection(surface, 'holdings');
  assert.equal(await surface.$eval('#portfolioContext', el => el.open), false, state + ' section owns the first screen');
  assert.equal(await surface.$eval('#holdingsBreakdown', el => el.open), false);
  assert.ok(await surface.$eval('#byAssetHeader', el => el.getBoundingClientRect().top) < 300,
    state + ' positions appear promptly');
  await click('#holdingsBreakdown > summary');
  await click('#btnBySector');
  await surface.select('#asOfPickerHoldings', '2023-12-31');
  assert.equal(await surface.$eval('#holdingsBreakdown', el => el.open), true, state + ' snapshot change preserves disclosure');
  assert.match(await surface.$eval('#holdingsBreakdownScope', el => el.textContent), /By Sector.*2023-12-31/);
  await surface.select('#asOfPickerHoldings', '2026-06-30');
  await click('#holdingsBreakdown > summary');
  await click('#holdingsFilters > summary');
  const group = await surface.$eval('#byAssetAccountGroupFilter', el => el.options[1].value);
  await surface.select('#byAssetAccountGroupFilter', group);
  await click('#holdingsFilters > summary');
  assert.ok((await surface.$eval('#holdingsFilterSummary', el => el.textContent)).includes(group));
  await click('#holdingsFilters > summary');
  await surface.select('#byAssetAccountGroupFilter', '');
  await click('#holdingsFilters > summary');

  await selectSection(surface, 'performance');
  assert.equal(await surface.$eval('#portfolioContext', el => el.open), false);
  assert.ok(await surface.$eval('.perf-primary-summary', el => el.getBoundingClientRect().bottom)
    < await surface.$eval('#mobile-navigation', el => el.getBoundingClientRect().top),
  state + ' four primary metrics fit above portrait navigation');
  for (const id of ['perfChartOptions', 'perfAnnualReturns', 'perfPositionGains', 'perfViewGuide']) {
    assert.equal(await surface.$eval('#' + id, el => el.open), false);
    await click('#' + id + ' > summary');
    assert.equal(await surface.$eval('#' + id, el => el.open), true);
  }
  await surface.$eval('#perfBenchRebase-on', el => el.scrollIntoView({block:'center'}));
  await surface.focus('#perfBenchRebase-on');
  await keyboard.press('Enter');
  assert.equal(await surface.evaluate(() => document.activeElement.id), 'perfBenchRebase-on', state + ' axis keyboard focus survives render');
  await surface.select('#perfWindowSelect', 'custom');
  for (const id of ['perfChartOptions', 'perfAnnualReturns', 'perfPositionGains', 'perfViewGuide']) {
    assert.equal(await surface.$eval('#' + id, el => el.open), true, state + ' selected window preserves analysis details');
  }
  assert.equal(await surface.$eval('#perfCustomStart', el => el.checkVisibility()), true);
  assert.match(await surface.$eval('#perfChartOptions > summary', el => el.textContent), /Rebased to/);
  await click('#perfWindowButton-mobile-lifetime');
  await click('#perfBenchRebase-auto');
  for (const id of ['perfChartOptions', 'perfAnnualReturns', 'perfPositionGains', 'perfViewGuide']) {
    await click('#' + id + ' > summary');
  }

  await selectSection(surface, 'transactions');
  await click('#transactionFilters > summary');
  await surface.type('#symbolFilter', 'fictional-empty');
  await click('.date-quick[data-range="ytd"]');
  await click('#transactionFilters > summary');
  assert.match(await surface.$eval('#transactionFilterSummary', el => el.textContent), /Symbol: fictional-empty.*2026-01-01/);
  assert.match(await surface.$eval('#countPill', el => el.textContent), /^0 \/ /);
  await click('#transactionFilters > summary');
  await click('#resetTransactionFilters');
  assert.equal(await surface.$eval('#transactionFilterSummary', el => el.textContent), 'All transactions · All dates');
  await click('#transactionFilters > summary');

  await selectSection(surface, 'overview');
  assert.equal(await surface.$eval('#portfolioContext', el => el.open), true, state + ' overview retains its hero');
  const allCards = await surface.$$eval('#stats .stat-card, #snapshotSecondary .stat-card', els => els.length);
  assert.equal(allCards, 6, state + ' all snapshot figures retained');
  assert.equal(await surface.$eval('#snapshotBreakdown', el => el.open), false);
  await click('#snapshotBreakdown > summary');
  await surface.select('#asOfPickerOverview', '2023-12-31');
  assert.equal(await surface.$eval('#snapshotBreakdown', el => el.open), true);
  assert.equal(await surface.$eval('#snapshotBreakdownDate', el => el.textContent), '2023-12-31');
  await surface.select('#asOfPickerOverview', '2026-06-30');
  await click('#snapshotBreakdown > summary');
  await noOverflow(surface, state + ' disclosures');
}

async function privateViewport(page, frame, state) {
  const geometry = await page.evaluate(() => {
    const frame = document.querySelector('#snapshot-host iframe:not(.snapshot-pending)');
    const box = frame.getBoundingClientRect();
    return {hostParent:frame.parentElement.parentElement.tagName, active:document.body.classList.contains('viewer-active'),
      outerHeight:document.scrollingElement.scrollHeight, height:innerHeight, width:innerWidth,
      frame:{left:box.left, right:box.right, top:box.top, bottom:box.bottom},
      navigation:document.getElementById('mobile-navigation').checkVisibility(),
      dialog:document.getElementById('mobile-sections').open};
  });
  assert.equal(geometry.hostParent, 'BODY', state + ' viewer host is a body flex child');
  assert.equal(geometry.active, true, state + ' viewer viewport mode');
  assert.equal(geometry.navigation, false, state + ' parent nav hidden');
  assert.equal(geometry.dialog, false, state + ' parent sheet closed');
  assert.ok(geometry.outerHeight <= geometry.height + 1, state + ' no outer page scrolling');
  assert.ok(Math.abs(geometry.frame.left) <= 1 && Math.abs(geometry.frame.right - geometry.width) <= 1,
    state + ' iframe spans viewport width');
  assert.ok(Math.abs(geometry.frame.bottom - geometry.height) <= 1, state + ' iframe fills remaining viewport');
  assert.ok(geometry.frame.top >= 44 && geometry.frame.top < geometry.height / 2,
    state + ' compact loader leaves room for dashboard');
  const controls = await page.$$eval('#snapshot-open, #snapshot-reset, #snapshot-help summary', elements => elements.map(element => {
    const box = element.getBoundingClientRect();
    return {width:box.width, height:box.height};
  }));
  assert.ok(controls.every(box => box.width >= 44 && box.height >= 44), state + ' loader targets at least 44px');
  await noOverflow(page, state + ' parent');
  await noOverflow(frame, state + ' child');
}

async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'finledger-mobile-check-'));
  let browser;
  try {
    browser = await puppeteer.launch({headless:true,
      executablePath:process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
      args:['--no-sandbox', '--disable-setuid-sandbox']});
    const page = await browser.newPage();
    // Use Frame surfaces for both documents so sheet keyboard helpers agree.
    const surface = page.mainFrame();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (/^https?:/.test(request.url())) { requests.push('Unexpected network request'); return request.abort(); }
      return request.continue();
    });
    const axeSource = await fs.readFile(require.resolve('axe-core/axe.min.js'), 'utf8');
    const url = pathToFileURL(path.resolve(process.argv[2] || '_site/index.html')).href;
    await page.setViewport(VIEWPORTS[0]);
    await page.goto(url, {waitUntil:'load'});
    assert.equal(await page.evaluate(() => DATA.demo?.synthetic === true && !!document.getElementById('demo-intro')), true,
      'input must be the fictional public demo');
    const snapshot = await page.evaluate(() => ({format:'finledger-viewer', version:1,
      as_of:SNAPSHOT_DATE, data:{...DATA, as_of:SNAPSHOT_DATE}}));

    for (const viewport of VIEWPORTS) {
      await page.setViewport(viewport);
      const state = 'demo ' + viewport.width + 'x' + viewport.height;
      const mobile = await surface.evaluate(() => matchMedia(COMPACT_NAV_QUERY).matches);
      assert.equal(await page.$eval('#mobile-navigation', element => element.checkVisibility()), mobile, state + ' nav breakpoint');
      assert.equal(await page.$eval('#tabnav', element => element.checkVisibility()), !mobile, state + ' desktop nav breakpoint');
      for (const section of SECTIONS) {
        await selectSection(surface, section);
        await currentSection(surface, section, state + ' ' + section);
        await noOverflow(surface, state + ' ' + section);
      }
      if (mobile) {
        await targetSizes(surface, state);
        await checkSheet(surface, state, viewport.width === 320 ? axeSource : null);
        await deepScrollSwitch(surface, state);
      }
    }
    await page.setViewport({width:390, height:844});
    await checkDisclosures(surface, 'demo');
    await page.setViewport({width:721, height:1000});
    await checkFontFallback(surface, 'demo 721x1000');
    await page.setViewport({width:720, height:390});
    await surface.click('#mobile-more');
    await page.setViewport({width:956, height:440});
    assert.equal(await surface.$eval('#mobile-sections', element => element.open), true, 'landscape stays compact with More open');
    await page.setViewport({width:1200, height:700});
    await surface.waitForFunction(() => !document.getElementById('mobile-sections').open
      && document.getElementById('mobile-more').getAttribute('aria-expanded') === 'false');
    assert.equal(await surface.$eval('#mobile-more', element => element.getAttribute('aria-expanded')), 'false', 'desktop resize closes sheet');
    await page.setViewport({width:390, height:844});
    for (const section of SECTIONS) {
      await page.goto(url + '#' + section, {waitUntil:'load'});
      await page.reload({waitUntil:'load'});
      await currentSection(surface, section, 'initial deep link ' + section);
    }
    // The initial router and hashchange path must agree.
    await page.evaluate(() => {location.hash = 'holdings';});
    await surface.waitForFunction(() => document.getElementById('tab-holdings').classList.contains('active'));
    await currentSection(surface, 'holdings', 'hashchange');

    async function choose(value) {
      await page.evaluate(() => document.querySelectorAll('#snapshot-host iframe').forEach(frame => {frame.dataset.mobileSmokePrevious = 'true';}));
      const file = path.join(directory, 'fictional-mobile.json');
      await fs.writeFile(file, JSON.stringify(value));
      await (await page.$('#snapshot-file')).uploadFile(file);
      await page.waitForFunction(() => {
        const frame = document.querySelector('#snapshot-host iframe:not(.snapshot-pending)');
        return frame && !frame.dataset.mobileSmokePrevious && document.body.classList.contains('viewer-active');
      });
      return page.frames().find(frame => frame.parentFrame());
    }
    await surface.click('#mobile-more');
    let frame = await choose(snapshot);
    for (const viewport of VIEWPORTS) {
      await page.setViewport(viewport);
      const state = 'private ' + viewport.width + 'x' + viewport.height;
      await privateViewport(page, frame, state);
      if ([320, 390, 430].includes(viewport.width)) {
        await page.click('#snapshot-help summary');
        const help = await page.$eval('.snapshot-help-content', element => {
          const box = element.getBoundingClientRect();
          return {left:box.left, right:box.right, bottom:box.bottom, width:innerWidth, height:innerHeight};
        });
        assert.ok(help.left >= 0 && help.right <= help.width && help.bottom <= help.height,
          state + ' loaded Help popover fits viewport');
        await page.click('#snapshot-help summary');
      }
      for (const section of SECTIONS) {
        await selectSection(frame, section);
        await currentSection(frame, section, state + ' ' + section);
        await noOverflow(frame, state + ' ' + section);
      }
      if (await frame.evaluate(() => matchMedia(COMPACT_NAV_QUERY).matches)) {
        await targetSizes(frame, state);
        await checkSheet(frame, state, viewport.width === 390 ? axeSource : null);
        await deepScrollSwitch(frame, state);
      }
    }
    await page.setViewport({width:390, height:844});
    await checkDisclosures(frame, 'private');
    await page.setViewport({width:721, height:1000});
    await checkFontFallback(frame, 'private 721x1000');
    await page.setViewport({width:390, height:844});
    await frame.click('#mobile-more');
    await page.setViewport({width:721, height:844});
    await frame.waitForFunction(() => !document.getElementById('mobile-sections').open
      && document.getElementById('mobile-more').getAttribute('aria-expanded') === 'false');
    await page.setViewport({width:390, height:844});
    const empty = structuredClone(snapshot);
    empty.data.analytics.options = {stats:{trades:0, open_count:0}};
    empty.data.analytics.crypto = {stats:{txn_count:0}};
    frame = await choose(empty);
    await frame.click('#mobile-more');
    const available = await frame.$$eval('#mobile-section-list button', elements => elements.map(element => element.dataset.mobileTab));
    assert.deepEqual(available, SECTIONS.filter(section => !['options', 'crypto'].includes(section)), 'empty optional sections omitted');
    await page.keyboard.press('Escape');
    await page.click('#snapshot-reset');
    assert.equal(page.frames().length, 1, 'reset destroys private iframe');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'snapshot-open', 'reset restores loader focus');
    assert.equal(await page.$eval('#mobile-navigation', element => element.checkVisibility()), true, 'reset restores public nav');
    assert.equal(await page.evaluate(() => document.body.classList.contains('viewer-active')), false, 'reset restores ordinary document flow');
    await selectSection(surface, 'overview');
    await currentSection(surface, 'overview', 'reset demo');
    assert.deepEqual(requests, [], 'zero network requests');
    assert.deepEqual(errors, [], 'no renderer or CSP errors');
    console.log('mobile_smoke: viewport boundaries/landscape, all routes/deep links, More focus/accessibility, touch targets, private viewport/empty tabs and reset passed');
  } finally {
    if (browser) await browser.close();
    await fs.rm(directory, {recursive:true, force:true});
  }
}
main().catch(error => {
  console.error(error);
  if (process.env.GITHUB_ACTIONS === 'true') {
    // Keep the failed behavioral assertion visible in public check annotations.
    const message = String(error.message).replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A');
    console.error('::error title=Mobile browser check::' + message);
  }
  process.exitCode = 1;
});
