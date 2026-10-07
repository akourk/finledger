// Exercise real pointer/keyboard input against the independently fictional demo.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {selectSection} = require('./mobile_navigation');

const charts = {
  history: {svg: '#chartSvg', cap: '#chartCapture', tip: '#chartTooltip'},
  performance: {svg: '#benchmarkCompareSvg', cap: '#benchmarkCompareSvg_cap', tip: '#benchmarkCompareSvg_tip'},
};
const settle = () => new Promise(resolve => setTimeout(resolve, 100));
async function placeChart(surface, chart) {
  await surface.$eval(chart.svg, svg => window.scrollTo({
    top: scrollY + svg.getBoundingClientRect().top - 70, behavior: 'instant',
  }));
  await settle();
  return surface.$eval(chart.cap, el => {
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, y: r.top + 20, width: r.width};
  });
}
async function readout(surface, chart) {
  return surface.$eval(chart.tip, el => {
    const r = el.getBoundingClientRect();
    return {visible: el.checkVisibility(), pinned: el.classList.contains('chart-tooltip-pinned'),
      date: el.querySelector('.tt-date')?.textContent, value: el.querySelector('.tt-row > span:last-child')?.textContent,
      fits: r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight,
      scrollable: el.scrollHeight <= el.clientHeight || getComputedStyle(el).overflowY === 'auto'};
  });
}
async function clear(surface, chart) {
  await surface.$eval(chart.svg, svg => svg.focus());
  await surface.evaluate(selector => document.querySelector(selector).dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})), chart.svg);
  await surface.$eval(chart.svg, svg => svg.blur());
}
async function touch(client, type, x, y) {
  await client.send('Input.dispatchTouchEvent', {type, touchPoints: type === 'touchEnd' || type === 'touchCancel' ? [] : [{x, y, id: 1}]});
  await settle();
}
async function checkTouch(page, client, chart, state) {
  await clear(page, chart);
  let cap = await placeChart(page, chart);
  const start = cap.left + 2, end = cap.right - 2;
  await touch(client, 'touchStart', start, cap.y);
  await touch(client, 'touchEnd');
  const tapped = await readout(page, chart);
  assert.ok(tapped.visible && tapped.pinned && tapped.fits, state + ': tap pins a visible readout');
  assert.match(tapped.date, /^\d{4}-\d{2}-\d{2}/);
  assert.match(tapped.value, /\$/);
  await clear(page, chart);
  cap = await placeChart(page, chart);
  await touch(client, 'touchStart', start, cap.y);
  for (let step = 1; step <= 4; step++) await touch(client, 'touchMove', start + (end - start) * step / 4, cap.y);
  await touch(client, 'touchEnd');
  const dragged = await readout(page, chart);
  assert.ok(dragged.visible && dragged.pinned && dragged.fits && dragged.scrollable, state + ': drag retains a bounded readout');
  assert.notEqual(dragged.date, tapped.date, state + ': horizontal drag changes the inspected date');
  // Cancellation must not leave a stale panel over the page or an active drag.
  await clear(page, chart);
  await touch(client, 'touchStart', start, cap.y);
  await touch(client, 'touchCancel');
  assert.equal((await readout(page, chart)).visible, false, state + ': cancellation clears the readout');
  cap = await placeChart(page, chart);
  const beforeScroll = await page.evaluate(() => scrollY);
  await touch(client, 'touchStart', cap.left + cap.width / 2, cap.y + 60);
  for (let step = 1; step <= 4; step++) await touch(client, 'touchMove', cap.left + cap.width / 2, cap.y + 60 - 25 * step);
  await touch(client, 'touchEnd');
  assert.ok(await page.evaluate(before => scrollY > before + 20, beforeScroll), state + ': vertical chart swipe scrolls the page');
  assert.equal((await readout(page, chart)).visible, false, state + ': scrolling dismisses inspection');
  cap = await placeChart(page, chart);
  await touch(client, 'touchStart', cap.right - 2, cap.y);
  await touch(client, 'touchEnd');
  await page.click(chart.tip + ' .chart-inspection-clear');
  assert.equal((await readout(page, chart)).visible, false, state + ': Clear inspection works by touch');
  assert.equal(await page.evaluate(() => document.activeElement.id), chart.svg.slice(1), state + ': clear returns focus to the chart');
}
async function checkKeyboard(page, surface, chart, state) {
  await surface.$eval(chart.svg, svg => svg.focus());
  await page.keyboard.press('Home');
  const first = await readout(surface, chart);
  assert.ok(first.visible && first.pinned, state + ': keyboard inspection opens');
  assert.equal(await surface.$eval(chart.svg, el => el.getAttribute('aria-valuenow')), '1');
  await page.keyboard.press('ArrowRight');
  assert.equal(await surface.$eval(chart.svg, el => el.getAttribute('aria-valuenow')), '2');
  await page.keyboard.press('End');
  const last = await readout(surface, chart);
  assert.notEqual(last.date, first.date, state + ': keyboard reaches both ends');
  assert.ok(await surface.$eval(chart.svg, el => el.getAttribute('aria-valuetext').includes(el._chartInspection.date)), state + ': accessible value includes the date');
  await page.keyboard.press('Escape');
  assert.equal((await readout(surface, chart)).visible, false, state + ': Escape clears inspection');
  await page.keyboard.press('Tab');
  assert.notEqual(await surface.evaluate(() => document.activeElement.id), chart.svg.slice(1), state + ': keyboard focus can leave the chart');
}
async function main() {
  const browser = await puppeteer.launch({headless: true, executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox']});
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (/^https?:/.test(request.url())) { errors.push('Unexpected network request'); return request.abort(); }
    return request.continue();
  });
  const client = await page.createCDPSession();
  const url = pathToFileURL(path.resolve(process.argv[2] || '_site/index.html')).href;
  try {
    for (const viewport of [{width: 320, height: 740}, {width: 390, height: 844}, {width: 956, height: 440}]) {
      await page.setViewport({...viewport, isMobile: true, hasTouch: true});
      await page.goto(url, {waitUntil: 'load'});
      assert.equal(await page.evaluate(() => DATA.demo.synthetic), true, 'only fictional demo inputs');
      const state = `${viewport.width}x${viewport.height}`;
      await checkTouch(page, client, charts.history, state + ' History');
      await page.evaluate(() => setHistoryChartMode('composition'));
      await checkTouch(page, client, charts.history, state + ' account mix');
      await checkKeyboard(page, page, charts.history, state + ' History');
      await selectSection(page, 'performance');
      const menu = await page.$eval('#perfWindowSelect', el => {
        const r = el.getBoundingClientRect();
        return {width: r.width, right: r.right, viewport: innerWidth};
      });
      assert.ok(menu.width >= 125 && menu.right <= menu.viewport, state + ': More ranges is readable and within the viewport');
      await page.focus('#perfWindowSelect');
      await page.select('#perfWindowSelect', '6mo');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'perfWindowSelect', state + ': range selection retains focus');
      await checkTouch(page, client, charts.performance, state + ' Performance');
      await checkKeyboard(page, page, charts.performance, state + ' Performance');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), state + ': no horizontal page overflow');
    }
    await page.setViewport({width: 1440, height: 1000, isMobile: false, hasTouch: false});
    await page.goto(url, {waitUntil: 'load'});
    for (const [name, chart] of Object.entries(charts)) {
      await selectSection(page, name === 'history' ? 'overview' : name);
      const cap = await placeChart(page, chart);
      await page.mouse.move(cap.left + 2, cap.y);
      const first = await readout(page, chart);
      await page.mouse.move(cap.right - 2, cap.y);
      const last = await readout(page, chart);
      assert.ok(first.visible && last.visible && !last.pinned && last.fits, name + ': desktop hover remains transient and bounded');
      assert.notEqual(first.date, last.date, name + ': hover maps dates across the plot');
      await page.mouse.move(0, 0);
      assert.equal((await readout(page, chart)).visible, false, name + ': mouse leave clears hover');
      await checkKeyboard(page, page, chart, 'desktop ' + name);
    }
    await page.$eval(charts.performance.svg, svg => svg.focus());
    await page.keyboard.press('Home');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.className), 'chart-inspection-clear');
    await page.setViewport({width: 1360, height: 1000, isMobile: false, hasTouch: false});
    await settle();
    assert.equal(await page.evaluate(() => document.activeElement.className), 'chart-inspection-clear', 'Performance resize preserves Clear inspection focus');
    await page.keyboard.press('Enter');
    await selectSection(page, 'overview');
    await page.$eval(charts.history.svg, svg => svg.focus());
    await page.keyboard.press('Home');
    const beforeResize = await readout(page, charts.history);
    await page.setViewport({width: 1280, height: 900, isMobile: false, hasTouch: false});
    await settle();
    assert.equal((await readout(page, charts.history)).date, beforeResize.date, 'resize keeps the inspected observation');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'chartSvg', 'resize preserves keyboard focus');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.className), 'chart-inspection-clear', 'Tab reaches Clear inspection');
    await page.setViewport({width: 1200, height: 900, isMobile: false, hasTouch: false});
    await settle();
    assert.equal(await page.evaluate(() => document.activeElement.className), 'chart-inspection-clear', 'resize preserves Clear inspection focus');
    await page.evaluate(() => setHistoryRange('ytd'));
    assert.equal((await readout(page, charts.history)).visible, false, 'a range excluding the inspected date clears its readout');
    await page.evaluate(() => {
      const svg = document.getElementById('chartSvg');
      svg.dispatchEvent(new KeyboardEvent('keydown', {key: 'End'}));
      _renderComposition(svg, document.getElementById('chartTooltip'), document.getElementById('chartLegend'), []);
    });
    assert.equal((await readout(page, charts.history)).visible, false, 'empty chart clears its readout');
    assert.ok(await page.$eval(charts.history.svg, svg => !svg.hasAttribute('tabindex') && !svg.onkeydown && !svg.onfocus), 'empty chart removes stale inspection handlers');
    await page.evaluate(() => renderHistory());
    // Load a fictional viewing envelope only in memory, then exercise its real
    // sandboxed iframe. No snapshot is written into the repository or artifact.
    await page.evaluate(() => {
      const snapshot = {format: 'finledger-viewer', version: 1, as_of: '2026-06-30', data: {...DATA, as_of: '2026-06-30'}};
      const transfer = new DataTransfer();
      transfer.items.add(new File([JSON.stringify(snapshot)], 'fictional-chart-check.json', {type: 'application/json'}));
      const input = document.getElementById('snapshot-file');
      input.files = transfer.files;
      input.dispatchEvent(new Event('change'));
    });
    await page.waitForFunction(() => document.getElementById('snapshot-status').textContent.startsWith('Local snapshot'));
    const frame = page.frames().find(frame => frame.parentFrame());
    await checkKeyboard(page, frame, charts.history, 'loaded iframe History');
    await selectSection(frame, 'performance');
    await checkKeyboard(page, frame, charts.performance, 'loaded iframe Performance');
    assert.deepEqual(errors, []);
    console.log('PASS: History, account mix, and Performance tap/drag/cancel/scroll at 320, 390 and short landscape; readable ranges; desktop hover; keyboard and loaded iframe inspection.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
