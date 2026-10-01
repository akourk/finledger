/* Public local-file controller. Payloads never leave this tab or enter storage. */
(() => {
  'use strict';
  const config = JSON.parse(document.getElementById('snapshot-config').textContent);
  const renderer = document.getElementById('finledger-renderer').textContent.trimStart();
  const openButton = document.getElementById('snapshot-open');
  const help = document.getElementById('snapshot-help');
  const skip = document.querySelector('.skip-link');
  const input = document.getElementById('snapshot-file');
  const reset = document.getElementById('snapshot-reset');
  const host = document.getElementById('snapshot-host');
  const status = document.getElementById('snapshot-status');
  const error = document.getElementById('snapshot-error');
  const controls = document.getElementById('snapshot-controls');
  const demo = [document.getElementById('demo-intro'), document.getElementById('portfolioContext'), document.getElementById('tabnav'), document.getElementById('main-content'), document.getElementById('mobile-navigation'), document.getElementById('mobile-sections')].filter(Boolean);
  let generation = 0;
  let pending = null;
  let current = null;
  let pendingTimer = null;
  const fail = () => { throw new Error('Invalid viewer snapshot'); };
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !value.startsWith('0000') && !Number.isNaN(Date.parse(value + 'T00:00:00Z')) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  const safeJSON = value => JSON.stringify(value).replace(/[<>&\u2028\u2029]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  function validate(snapshot) {
    if (!object(snapshot) || snapshot.format !== 'finledger-viewer' || snapshot.version !== 1 || !date(snapshot.as_of) || !object(snapshot.data)) fail();
    const data = snapshot.data;
    for (const key of ['transactions', 'holdings', 'holdings_by_account', 'history']) {
      if (!Array.isArray(data[key]) || !data[key].every(object)) fail();
    }
    for (const key of ['analytics', 'basis_totals', 'basis_methods', 'cash_summary', 'retirement_meta', 'sector_of', 'display_of', 'action_catalog', 'tax_tables']) if (!object(data[key])) fail();
    const stack = [[snapshot, 0]];
    while (stack.length) {
      const [value, depth] = stack.pop();
      if (depth > 64) fail();
      if (typeof value === 'number' && !Number.isFinite(value)) fail();
      if (typeof value === 'string' && /[<>]/.test(value)) fail();
      if (value && typeof value === 'object') for (const [key, child] of Object.entries(value)) {
        if (['__proto__', 'constructor', 'prototype'].includes(key) || /[<>]/.test(key)) fail();

        stack.push([child, depth + 1]);
      }
    }
    if ('format' in data || 'files' in data || data.as_of !== snapshot.as_of) fail();
    for (const key of ['as_of', 'snapshot_date']) if (key in data && !date(data[key])) fail();
    if ('generated' in data && (typeof data.generated !== 'string' || !date(data.generated.slice(0,10)))) fail();
    if ('count' in data && (!Number.isInteger(data.count) || data.count !== data.transactions.length)) fail();
    const numeric = ['quantity','price','fees','amount','balance','value','cost_basis','realized_gain','cash_flow','total','total_cost_basis','priced_pct','net_contributed','unrealized_gain','benchmark_spy','benchmark_spy_price'];
    const nullable = new Set(['price','value','balance','cost_basis','realized_gain','unrealized_gain','benchmark_spy','benchmark_spy_price']);
    const number = (value, nullOK = false) => { if (!(nullOK && value === null) && (typeof value !== 'number' || !Number.isFinite(value))) fail(); };
    function record(row) {
      if (!object(row)) fail();
      for (const key of ['symbol','account','account_group','account_type','action','raw_action','description','source','sector','basis_effect']) if (key in row && typeof row[key] !== 'string') fail();
      for (const key of numeric) if (key in row) number(row[key], nullable.has(key));
      for (const key of ['by_account_group','by_account_type','by_sector','cost_basis_by_group','cost_basis_by_type']) if (key in row) {
        if (!object(row[key])) fail();
        Object.values(row[key]).forEach(value => number(value));
      }
      for (const key of ['positions','in_transit']) if (key in row) { if (!Array.isArray(row[key])) fail(); row[key].forEach(record); }
      if ('valuation_precision' in row) record(row.valuation_precision);
    }
    for (const key of ['transactions','holdings','holdings_by_account','history']) data[key].forEach(record);
    if ('actions' in data.action_catalog) {
      if (!Array.isArray(data.action_catalog.actions)) fail();
      for (const action of data.action_catalog.actions) if (!object(action) || typeof action.name !== 'string' || typeof action.color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(action.color)) fail();
    }
    for (const row of [...data.transactions, ...data.history]) if (!date(row.date)) fail();
    return snapshot;
  }
  function discardPending() { clearTimeout(pendingTimer); pendingTimer = null; if (pending) { pending.remove(); pending = null; } }
  function showError() {
    error.textContent = 'Could not open this file. Choose a supported FinLedger viewer JSON snapshot (version 1, up to 25 MiB), with valid dates, numbers and plain-text labels. Your previous view is unchanged.';
    error.hidden = false;
  }
  function frameDocument(snapshot) {
    const nonceBytes = crypto.getRandomValues(new Uint8Array(16));
    const nonce = Array.from(nonceBytes, b => b.toString(16).padStart(2, '0')).join('');
    const policy = `default-src 'none'; script-src 'nonce-${nonce}'; script-src-attr 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
    // JSON.parse avoids JS object-literal __proto__ semantics. No file text is code.
    let program = renderer.slice(0, config.start) + 'JSON.parse(' + safeJSON(JSON.stringify(snapshot.data)) + ')' + renderer.slice(config.end);
    // Opaque srcdoc documents cannot update History URLs. Tabs still work locally.
    program = program.replace("window.history.replaceState(null, '', '#' + name);", '/* Local viewer does not write a URL. */');
    const bootstrap = `document.addEventListener('click',e=>{const a=e.target.closest('a');if(a&&!a.download&&!a.getAttribute('href')?.startsWith('#'))e.preventDefault();},true);document.addEventListener('submit',e=>e.preventDefault(),true);`;
    const ready = `\nwindow.addEventListener('message',e=>{if(e.source===parent&&e.data?.type==='finledger-viewer-visible')window.dispatchEvent(new Event('resize'));});parent.postMessage({type:'finledger-viewer-ready'},'*');`;
    return config.template.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="' + policy + '">')
      .replace('__VIEWER_SCRIPT__', () => '<script nonce="' + nonce + '">' + bootstrap + program + ready + '<\/script>');
  }
  async function open(file) {
    const ticket = ++generation;
    discardPending();
    error.hidden = true;
    if (!file || file.size > 25 * 1024 * 1024) { showError(); return; }
    try {
      const text = await file.text();
      if (ticket !== generation) return;
      const snapshot = validate(JSON.parse(text));
      const frame = document.createElement('iframe');
      frame.title = 'Your local FinLedger snapshot';
      frame.setAttribute('sandbox', 'allow-scripts allow-downloads');
      frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.className = 'snapshot-pending';
      frame.dataset.asOf = snapshot.as_of;
      pending = frame;
      frame.srcdoc = frameDocument(snapshot);
      host.append(frame);
      pendingTimer = setTimeout(() => { if (pending === frame && ticket === generation) { discardPending(); showError(); } }, 5000);
    } catch (_) { if (ticket === generation) { discardPending(); showError(); } }
    finally { if (ticket === generation) input.value = ''; }
  }
  window.addEventListener('message', event => {
    if (!pending || event.source !== pending.contentWindow || event.data?.type !== 'finledger-viewer-ready') return;
    if (current) current.remove();
    clearTimeout(pendingTimer); pendingTimer = null;
    current = pending; pending = null;
    current.className = '';
    document.body.classList.add('viewer-active');
    help.open = false;
    openButton.textContent = 'Change file';
    if (skip) { skip.href = '#snapshot-host'; skip.textContent = 'Skip to snapshot dashboard'; }
    current.contentWindow.postMessage({type:'finledger-viewer-visible'}, '*');
    demo.forEach(element => {
      if (element.open && typeof element.close === 'function') element.close();
      element.classList.add('viewer-demo-hidden');
    });
    reset.hidden = false;
    status.textContent = 'Loaded locally · As of ' + current.dataset.asOf;
  });
  openButton.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files.length) open(input.files[0]); });
  reset.addEventListener('click', () => {
    ++generation; discardPending();
    if (current) current.remove(); current = null;
    document.body.classList.remove('viewer-active');
    help.open = false; openButton.textContent = 'Open snapshot';
    if (skip) { skip.href = '#main-content'; skip.textContent = 'Skip to dashboard content'; }
    input.value = ''; reset.hidden = true; error.hidden = true;
    demo.forEach(element => { element.classList.remove('viewer-demo-hidden'); });
    status.textContent = 'Showing the fictional demo.';
    openButton.focus();
  });
  controls.addEventListener('dragover', event => { event.preventDefault(); controls.classList.add('dragging'); });
  controls.addEventListener('dragleave', () => controls.classList.remove('dragging'));
  controls.addEventListener('drop', event => {
    event.preventDefault(); controls.classList.remove('dragging');
    if (event.dataTransfer.files.length !== 1) { ++generation; discardPending(); showError(); return; }
    open(event.dataTransfer.files[0]);
  });
})();
