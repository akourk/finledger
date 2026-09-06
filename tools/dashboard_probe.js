// Render the real dashboard JS under a minimal DOM stub and report the
// figures it actually produces, as structured JSON on stdout.
//
// WHY THIS EXISTS
// ---------------
// `analytics/` guarantees each figure is computed once and correctly.
// It cannot guarantee that two correctly-computed figures placed side by
// side are COMPARABLE — that pairing is invented at the render site, out
// of inputs the compute layer never sees together.  Both bug classes this
// repo learned from user reports rather than from its own suite —
// docs/PLAN-audit.md (11) as-of/date-alignment and (12) unpaired comparison —
// were exactly that shape, and both were invisible to any harness that
// stops at the compute layer.
//
// So this probe deliberately drives `renderPerformance()` and friends,
// not `computeWindowedMetrics()`.  It captures what a reader sees.
//
// The DOM stub is small on purpose: the whole bundle touches under ten
// distinct browser APIs.  Any `getElementById` returns a live stub node,
// so no renderer bails early on a missing target — a renderer that
// silently does not run would otherwise report as clean.
//
// Usage:  node tools/dashboard_probe.js <bundle.js>
//
// <bundle.js> is app JS with __JSON_DATA__ already substituted.  Build it
// with tools/dashboard_probe.py, which goes through the real bundler, so
// the probe always exercises the concatenation order that ships.

'use strict';
const fs = require('fs');

// ---------------------------------------------------------------------
// DOM stub
// ---------------------------------------------------------------------

function makeNode(id) {
  return {
    id: id || '',
    innerHTML: '',
    outerHTML: '',
    textContent: '',
    value: '',
    checked: false,
    dataset: {},
    style: {},
    children: [],
    childNodes: [],
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      toggle(c, on) {
        if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (on) { this._s.add(c); } else { this._s.delete(c); }
      },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { return c; },
    insertBefore(c) { this.children.push(c); return c; },
    remove() {},
    setAttribute(k, v) { this[k] = v; },
    getAttribute(k) { return this[k] === undefined ? null : this[k]; },
    removeAttribute(k) { delete this[k]; },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    focus() {}, blur() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect() {
      return { width: 900, height: 400, top: 0, left: 0, right: 900, bottom: 400 };
    },
  };
}

const NODES = new Map();
function nodeFor(id) {
  if (!NODES.has(id)) NODES.set(id, makeNode(id));
  return NODES.get(id);
}

globalThis.document = {
  getElementById: (id) => nodeFor(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => makeNode('created:' + tag),
  createTextNode: () => makeNode('text'),
  addEventListener: () => {},
  body: makeNode('body'),
  documentElement: makeNode('html'),
};
globalThis.window = globalThis;
globalThis.location = { hash: '', href: 'http://localhost/', search: '' };
globalThis.history = { replaceState: () => {}, pushState: () => {} };
globalThis.requestAnimationFrame = (fn) => { fn(0); return 0; };
globalThis.cancelAnimationFrame = () => {};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.matchMedia = () => ({ matches: false, addEventListener: () => {}, addListener: () => {} });
globalThis.alert = () => {};
globalThis.addEventListener = () => {};
globalThis.localStorage = {
  _m: new Map(),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, String(v)); },
  removeItem(k) { this._m.delete(k); },
};

// Console errors are a finding, not noise — collect them rather than
// print, so stdout stays parseable JSON.
const CONSOLE_ERRORS = [];
console.error = (...a) => { CONSOLE_ERRORS.push(a.map(String).join(' ')); };
console.warn = () => {};
console.log = () => {};

// ---------------------------------------------------------------------
// Extraction — pull the figures a reader sees back out of the HTML
// ---------------------------------------------------------------------

function stripTags(s) {
  return String(s)
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
}

const CARD_RE = /<div class="stat-card([^"]*)"([^>]*)>\s*<div class="label">([\s\S]*?)<\/div>\s*<div class="value">([\s\S]*?)<\/div>/g;

function parseCards(html) {
  const out = [];
  let m;
  CARD_RE.lastIndex = 0;
  while ((m = CARD_RE.exec(html)) !== null) {
    const attrs = m[2] || '';
    const titleMatch = /title="([\s\S]*?)"/.exec(attrs);
    out.push({
      cls: m[1].trim(),
      label: stripTags(m[3]),
      value: stripTags(m[4]),
      title: titleMatch ? stripTags(titleMatch[1]) : '',
    });
  }
  return out;
}

function parseTables(html) {
  const tables = [];
  const tblRe = /<table[\s\S]*?<\/table>/g;
  let t;
  while ((t = tblRe.exec(html)) !== null) {
    const chunk = t[0];
    const headers = [...chunk.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map(h => stripTags(h[1]));
    const bodyMatch = /<tbody[^>]*>([\s\S]*?)<\/tbody>/.exec(chunk);
    const body = bodyMatch ? bodyMatch[1] : chunk;
    const rows = [...body.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(r =>
      [...r[1].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/g)].map(c => stripTags(c[1]))
    ).filter(r => r.length);
    if (headers.length || rows.length) tables.push({ headers, rows });
  }
  // Renderers that inject a tbody FRAGMENT into a table the template
  // owns (Holdings, the transaction ledger) never emit a <table> at all.
  // Without this, those tabs capture as empty and every check over them
  // passes by having nothing to read.  Headers are genuinely unknown for
  // a fragment, so they stay empty rather than being guessed at.
  const consumed = html.replace(/<table[\s\S]*?<\/table>/g, '');
  const loose = [...consumed.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(r =>
    [...r[1].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/g)].map(c => stripTags(c[1]))
  ).filter(r => r.length);
  if (loose.length) tables.push({ headers: [], rows: loose, fragment: true });
  return tables;
}

// Expose the extraction helpers to the driver, which runs inside the
// bundle's own lexical scope (see below).
globalThis.__probe = { NODES, parseCards, parseTables, CONSOLE_ERRORS };

// ---------------------------------------------------------------------
// Load + drive
// ---------------------------------------------------------------------
//
// The driver is CONCATENATED with the bundle and evaluated as one script
// rather than run afterwards.  This is load-bearing, not tidiness:
// `var` and function declarations from an indirect eval land on the
// global object, but `let` / `const` are scoped to that eval call and
// vanish when it returns.  The bundle declares `const DATA`,
// `const holdingsByAccount`, `let performanceAccountFilter` and the rest
// exactly that way, so a driver run in a later call sees the render
// FUNCTIONS but none of the state they read — which fails as
// "performanceAccountFilter is not defined" from inside a function that
// very much exists.  Sharing one evaluation is what puts them in scope.

const path = require('path');
const bundlePath = process.argv[2];
if (!bundlePath) {
  process.stderr.write('usage: node dashboard_probe.js <bundle.js>\n');
  process.exit(2);
}
const bundle = fs.readFileSync(bundlePath, 'utf8');
const driver = fs.readFileSync(path.join(__dirname, 'dashboard_probe_driver.js'), 'utf8');

(0, eval)(bundle + '\n;\n' + driver);

process.stdout.write(JSON.stringify(globalThis.__probe_results));
