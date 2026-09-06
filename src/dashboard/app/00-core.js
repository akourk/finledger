const DATA = __JSON_DATA__;

const txns = DATA.transactions || [];
const holdingsByAsset = DATA.holdings || [];
const holdingsByAccount = DATA.holdings_by_account || [];
const history = DATA.history || [];
// The lot-method COMPARISON table: four pure single-method what-if
// walks.  Never publish a figure from this — see `basisTotals`.
const basisMethods = DATA.basis_methods || {};
// The portfolio's REAL totals, from the annotated basis walk (the
// per-account lot methods the brokers actually use).  This is what a
// headline figure reads.  Reading `basisMethods.fifo` instead is
// docs/AUDIT.md F-033: with one account on HIFO it put the Overview's
// Realized roughly 19% away from the same quantity on Performance.
const basisTotals = DATA.basis_totals || {};
const cashSummary = DATA.cash_summary || {};
// Pre-computed analytics from src/analytics.py.  Single source of truth
// for rollover bridges, retirement contributions by year, and
// per-account annual returns + TWR summaries.  The dashboard should
// PREFER reading from here rather than recomputing — avoids drift
// between views that display the same metric.
const ANALYTICS = DATA.analytics || {};
const ANALYTICS_PERF = ANALYTICS.performance_by_filter || {};

// Rollover bridges now come from Python analytics (src/analytics.py).
// The dashboard-side helper is just a shim that applies the bridge
// to a snapshot date for a given account filter.
const _ROLLOVER_BRIDGES = ANALYTICS.rollover_bridges || [];

// User-maintained metadata from data/metadata.csv (birthday,
// salary history, bonus history, annual expenses, year-end targets).
// Hoisted to the top because renderAnnualBreakdown (Overview tab,
// rendered immediately on load) reads RETIREMENT_META.targets and
// RETIREMENT_META.birthday — the original declaration further down
// the file produced a temporal-dead-zone error that blocked every
// tab from loading.
const RETIREMENT_META = DATA.retirement_meta || {};

// --- Symbol → sector lookup (covers every symbol ever seen, not just
// current holdings).  Populated by main.py from sectors.get_sector().
const SECTOR_OF = DATA.sector_of || {};
// --- Symbol → short display name lookup.  One entry per proxy-mapped
// symbol (multi-word fund names, ticker-format aliases); the display
// defaults to the proxy ticker unless the proxy map entry has a
// `display` override.  Unmapped symbols render as-is.
const DISPLAY_OF = DATA.display_of || {};

// HTML-escape helper for the few places we inject raw symbols into
// attribute values (title="..." for tooltip).
function _htmlEsc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Render a symbol as compact HTML: proxy-mapped symbols show their
// short display ("VFIAX" rather than "Vanguard Employee Benefit Index
// Fund"), long unmapped symbols (option contracts) get CSS truncation
// via the `.sym-cell` class.  In both cases the raw symbol lives in a
// `title` attribute so hovering reveals the full name.  Callers
// wrap with <b>…</b> themselves if bold is wanted (existing convention).
function symLabel(sym) {
  if (!sym) return '';
  const display = DISPLAY_OF[sym] || sym;
  const needsTip = display !== sym || sym.length > 24;
  const tip = needsTip ? ` title="${_htmlEsc(sym)}"` : '';
  return `<span class="sym-cell"${tip}>${_htmlEsc(display)}</span>`;
}
// --- Account group → account type map.  Derived client-side from
// txns + current holdings (every row carries both fields) — covers
// every historical account group.
const ACCOUNT_TYPE_OF = (() => {
  const m = {};
  for (const t of txns) {
    if (t.account_group && t.account_type) m[t.account_group] = t.account_type;
  }
  for (const h of holdingsByAccount) {
    if (h.account_group && h.account_type) m[h.account_group] = h.account_type;
  }
  return m;
})();

