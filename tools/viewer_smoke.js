// Verify only independently fictional inputs against the exact public artifact.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('puppeteer');
const {selectSection} = require('./mobile_navigation');

async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'finledger-viewer-check-'));
  const browser = await puppeteer.launch({headless:true,
    executablePath:process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args:['--no-sandbox','--disable-setuid-sandbox']});
  const page = await browser.newPage();
  const errors = [], requests = [];
  const axeSource = await fs.readFile(require.resolve('axe-core/axe.min.js'), 'utf8');
  async function checkPrivateAccessibility(frame, state) {
    if (!await frame.evaluate(() => typeof window.axe !== 'undefined')) await frame.evaluate(axeSource);
    await frame.evaluate(() => document.querySelectorAll('details').forEach(element => {element.open=true;}));
    await new Promise(resolve => setTimeout(resolve,200));
    const violations = await frame.evaluate(async () => (await axe.run(document)).violations
      .filter(item => ['serious','critical'].includes(item.impact)).map(item => ({id:item.id, targets:item.nodes.map(node=>node.target)})));
    assert.deepEqual(violations, [], state+' private accessibility');
  }
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (/^https?:/.test(request.url())) { requests.push('Unexpected network request'); return request.abort(); }
    return request.continue();
  });
  const choose = async (snapshot, name='fictional.json') => {
    const file = path.join(directory,name);
    await fs.writeFile(file, typeof snapshot === 'string' ? snapshot : JSON.stringify(snapshot));
    await (await page.$('#snapshot-file')).uploadFile(file);
  };
  const active = () => page.frames().find(frame => frame.parentFrame());
  const loaded = () => page.waitForFunction(() => document.getElementById('snapshot-status').textContent.startsWith('Loaded locally'));
  const rejected = () => page.waitForFunction(() => !document.getElementById('snapshot-error').hidden);
  try {
    await page.setViewport({width:1440,height:1000});
    await page.goto(pathToFileURL(path.resolve(process.argv[2] || '_site/index.html')).href,{waitUntil:'load'});
    const snapshot = await page.evaluate(() => ({format:'finledger-viewer',version:1,as_of:'2026-06-30',data:{...DATA,as_of:'2026-06-30'}}));
    const demoSummary = await page.$eval('#stats', element => element.textContent);
    await choose(snapshot); await loaded();
    let frame = active();
    assert.equal(await frame.$eval('#stats', element => element.textContent), demoSummary, 'same exported values yield same summary');
    assert.equal(await frame.$('#demo-intro'),null);
    assert.equal(await frame.$('#topBarRefresh'),null);
    assert.equal(await page.$eval('#snapshot-host iframe', element => element.getAttribute('sandbox')), 'allow-scripts allow-downloads');
    assert.equal(await frame.evaluate(() => {try {localStorage.setItem('probe','fictional');return false;} catch (_) {return true;}}),true,'storage denied');
    assert.equal(await frame.evaluate(() => {try {return !!parent.document;} catch (_) {return false;}}),false,'parent DOM denied');
    for (const tab of ['overview','holdings','performance','transactions','options','retirement','planning','income','tax','crypto']) {
      await selectSection(frame, tab);
      assert.equal(await frame.$eval('#tab-'+tab, element => element.classList.contains('active')),true,tab);
      assert.equal(await frame.$('[data-render-error]'),null,tab+' renders');
      if (['overview','holdings','tax'].includes(tab)) await checkPrivateAccessibility(frame,'desktop '+tab);
    }
    await selectSection(frame, 'tax');
    const download = await browser.target().createCDPSession();
    await download.send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:directory,eventsEnabled:true});
    const taxButton = await frame.$('button[onclick*="export"],button[onclick*="download"]');
    assert.ok(taxButton,'tax CSV download control');
    const downloaded = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Tax CSV download did not complete')), 5000);
      download.on('Browser.downloadProgress', event => { if (event.state === 'completed') { clearTimeout(timer); resolve(); } });
    });
    await taxButton.click(); await downloaded;
    assert.ok((await fs.readdir(directory)).some(name => name.endsWith('.csv')), 'CSV created inside sandbox');
    assert.equal(await page.$eval('#demo-intro', element => element.checkVisibility()), false);
    assert.equal(await page.$eval('.top-bar', element => element.checkVisibility()), false);
    const original = frame;
    for (const mutate of [s=>s.version=2,s=>s.data.history[0].date='2026-02-30',s=>s.data.history[0].date='<img src=x>',s=>s.data.analytics.reconciliation.summary.off='<svg onload=alert(1)>',s=>s.data.action_catalog.actions[0].color='red" onpointerover="window.injected=true',s=>s.data.transactions[0].amount='123']) {
      const bad=structuredClone(snapshot);mutate(bad);await choose(bad);await rejected();assert.equal(active(),original,'invalid file preserves current view');
    }
    await choose('Date,Amount\n2026-01-01,3\n','fictional.csv');await rejected();
    await choose({format:'finledger-snapshot',files:{}});await rejected();
    await page.evaluate(() => {
      const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(25*1024*1024+1)],'oversize.json'));
      const input=document.getElementById('snapshot-file');input.files=transfer.files;input.dispatchEvent(new Event('change'));
    });await rejected();assert.equal(active(),original);
    const second=structuredClone(snapshot);
    second.data.transactions[0].description='Fictional O\'Brien "quoted" & Ω';
    second.data.as_of=second.as_of='2026-07-01';
    await page.evaluate(s=>{const transfer=new DataTransfer();transfer.items.add(new File([JSON.stringify(s)],'second.json'));document.getElementById('snapshot-controls').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));},second);
    await page.waitForFunction(()=>document.getElementById('snapshot-status').textContent.includes('2026-07-01'));
    frame=active();assert.notEqual(frame,original);assert.equal(page.frames().length,2);
    await selectSection(frame, 'transactions');
    assert.equal(await frame.evaluate(()=>DATA.transactions[0].description),'Fictional O\'Brien "quoted" & Ω');
    await page.setViewport({width:375,height:812});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'outer mobile viewport');
    assert.equal(await frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'private mobile viewport');
    await checkPrivateAccessibility(frame,'mobile transactions');
    await selectSection(frame, 'performance');
    await frame.evaluate(()=>setPerfView('risk'));
    await checkPrivateAccessibility(frame,'mobile performance risk');
    // Delay a file read, reset, then resolve it: private state must stay destroyed.
    await page.evaluate(s=>{window.finishViewerRead=null;const file=new File(['{}'],'late.json');file.text=()=>new Promise(resolve=>{window.finishViewerRead=()=>resolve(JSON.stringify(s));});const transfer=new DataTransfer();transfer.items.add(file);const input=document.getElementById('snapshot-file');input.files=transfer.files;input.dispatchEvent(new Event('change'));},snapshot);
    await page.click('#snapshot-reset');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'snapshot-open');
    await page.evaluate(()=>{window.finishViewerRead();delete window.finishViewerRead;});
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.equal(page.frames().length,1);
    assert.equal(await page.$eval('#stats', element => element.textContent),demoSummary);
    // Two asynchronous reads: the later selection wins even if the first resolves last.
    await page.evaluate(s=>{window.viewerReads=[];for (const day of ['2026-07-02','2026-07-03']) {const value=structuredClone(s);value.as_of=value.data.as_of=day;const file=new File(['{}'],'delayed.json');file.text=()=>new Promise(resolve=>window.viewerReads.push(()=>resolve(JSON.stringify(value))));const transfer=new DataTransfer();transfer.items.add(file);const input=document.getElementById('snapshot-file');input.files=transfer.files;input.dispatchEvent(new Event('change'));}},snapshot);
    await page.evaluate(()=>window.viewerReads[1]());
    await page.waitForFunction(()=>document.getElementById('snapshot-status').textContent.includes('2026-07-03'));
    await page.evaluate(()=>{window.viewerReads[0]();delete window.viewerReads;});
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.equal(await page.$eval('#snapshot-status',el=>el.textContent.includes('2026-07-03')),true);
    // A failed trusted renderer must time out, preserving the established view.
    await page.evaluate(()=>{window.originalViewerCreate=document.createElement.bind(document);document.createElement=function(tag,...args){const el=window.originalViewerCreate(tag,...args);if(tag==='iframe')Object.defineProperty(el,'srcdoc',{set(){}});return el;};});
    await choose(snapshot);await rejected();
    assert.equal(await page.$eval('#snapshot-status',el=>el.textContent.includes('2026-07-03')),true);
    await page.evaluate(()=>{document.createElement=window.originalViewerCreate;delete window.originalViewerCreate;});
    await choose(snapshot);await page.waitForFunction(()=>document.getElementById('snapshot-status').textContent.includes('2026-06-30'));await page.reload({waitUntil:'load'});
    assert.equal(page.frames().length,1);assert.equal(await page.$eval('#snapshot-status', element=>element.textContent),'Showing the fictional demo.');
    assert.deepEqual(requests,[],'zero network requests');assert.deepEqual(errors,[],'no renderer/CSP errors');
    console.log('viewer_smoke: all 10 tabs, summary parity, isolation, picker/drop, reset/reload, mobile, private accessibility and hostile inputs passed');
  } finally { await browser.close();await fs.rm(directory,{recursive:true,force:true}); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
