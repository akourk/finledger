// Refresh portfolio screenshots only from a verified fictional demo artifact.
'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function main() {
  const root = path.resolve(__dirname, '..');
  const file = path.resolve(process.argv[2] || '_site/index.html');
  const output = path.resolve(process.argv[3] || path.join(root, 'docs/img'));
  const manifest = JSON.parse(await fs.readFile(path.join(path.dirname(file), 'provenance.json'), 'utf8'));
  const bytes = await fs.readFile(file);
  if (manifest.synthetic !== true || manifest.files['index.html'] !== sha(bytes)
      || manifest.source_sha256 !== sha(await fs.readFile(path.join(root, 'tools/build_sample_snapshot.py')))) {
    throw new Error('Screenshots require a verified current fictional demo');
  }
  const browser = await puppeteer.launch({headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox']});
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 1100, deviceScaleFactor: 1});
  await page.setRequestInterception(true);
  page.on('request', request => /^https?:/.test(request.url()) ? request.abort() : request.continue());
  try {
    await page.goto(pathToFileURL(file).href, {waitUntil: 'load'});
    if (!await page.evaluate(() => DATA.demo?.synthetic && document.getElementById('demo-intro'))) {
      throw new Error('Missing fictional demo designation');
    }
    await fs.mkdir(output, {recursive: true});
    const files = {};
    for (const [tab, name] of [['overview', 'dashboard'], ['holdings', 'holdings'], ['performance', 'performance'], ['tax', 'tax']]) {
      await page.click('#tabbtn-' + tab);
      if (tab === 'holdings') {
        const disclosure = await page.$('button.lot-disclosure');
        if (disclosure) await disclosure.click();
      }
      if (tab === 'tax') await page.click('#tax-year-all');
      await page.evaluate(() => window.scrollTo(0, 0));
      await new Promise(resolve => setTimeout(resolve, 250));
      const target = path.join(output, name + '.png');
      await page.screenshot({path: target});
      files[name + '.png'] = sha(await fs.readFile(target));
    }
    await fs.writeFile(path.join(output, 'provenance.json'), JSON.stringify({
      schema: 1, synthetic: true, as_of: manifest.as_of,
      source: manifest.source, source_sha256: manifest.source_sha256,
      snapshot_sha256: manifest.snapshot_sha256, prices_sha256: manifest.prices_sha256,
      artifact_sha256: manifest.files['index.html'], visually_reviewed: false,
      tool: 'tools/capture_demo_screenshots.js', viewport: {width: 1440, height: 1100}, files,
    }, null, 2) + '\n');
    console.log('Captured 4 fictional demo screenshots with provenance.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
