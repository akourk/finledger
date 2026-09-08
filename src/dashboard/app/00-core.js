const DATA = __JSON_DATA__;

const txns = DATA.transactions || [];

// Python decides portfolio cash flow and marks verified account transfers.
// An in-kind transfer is capital crossing a selected account's boundary only
// when its counterpart is outside that selection. Total keeps its original
// external-flow accounting; older exports without the annotation still work.
function _txnCashFlowForGroups(t, filterSet) {
  if (filterSet && !filterSet.has(t.account_group)) return 0;
  const external = Number.isFinite(t.cash_flow) ? t.cash_flow : 0;
  const transfer = t.account_transfer;
  if (!filterSet || !transfer || external !== 0
    || typeof transfer.counterparty_group !== 'string'
    || !transfer.counterparty_group
    || filterSet.has(transfer.counterparty_group)
    || !Number.isFinite(transfer.flow)) return external;
  return external + transfer.flow;
}

const holdingsByAsset = DATA.holdings || [];
const holdingsByAccount = DATA.holdings_by_account || [];
const history = DATA.history || [];

// A generated dashboard is a dated artifact: visiting it later must not
// silently move tax years, age projections or trailing activity windows.
const SNAPSHOT_DATE = String(DATA.as_of || DATA.snapshot_date
  || (history.length && history[history.length - 1].date)
  || DATA.generated || '1970-01-01').slice(0, 10);
function snapshotDate() { return new Date(SNAPSHOT_DATE + 'T12:00:00'); }
function snapshotYear() { return Number(SNAPSHOT_DATE.slice(0, 4)); }
function calendarIso(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')].join('-');
}

// Ledger dates are calendar days, not instants in the visitor's timezone.
// UTC accessors avoid DST changing a cutoff by a day. Month/year shifts
// clamp to the destination month's last day (Feb 29 -> Feb 28).
function shiftCalendarIso(iso, { days = 0, months = 0, years = 0 } = {}) {
  const date = new Date(iso + 'T00:00:00Z');
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCFullYear(date.getUTCFullYear() + years, date.getUTCMonth() + months);
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, lastDay) + days);
  return date.toISOString().slice(0, 10);
}

// Match Python's calendar-age rule, including March 1 as the anniversary
// of a leap-day birthday in a non-leap year.
function calendarAge(birthday, asOf = SNAPSHOT_DATE) {
  if (!birthday) return null;
  return Number(asOf.slice(0, 4)) - Number(birthday.slice(0, 4))
    - (asOf.slice(5, 10) < birthday.slice(5, 10) ? 1 : 0);
}

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
const ANALYTICS_PERF = Object.assign(Object.create(null), ANALYTICS.performance_by_filter || {});

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
const SECTOR_OF = Object.assign(Object.create(null), DATA.sector_of || {});
// --- Symbol → short display name lookup.  One entry per proxy-mapped
// symbol (multi-word fund names, ticker-format aliases); the display
// defaults to the proxy ticker unless the proxy map entry has a
// `display` override.  Unmapped symbols render as-is.
const DISPLAY_OF = Object.assign(Object.create(null), DATA.display_of || {});

// HTML-escape helper for the few places we inject raw symbols into
// attribute values (title="..." for tooltip).
function _htmlEsc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
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
  const m = Object.create(null);
  for (const t of txns) {
    if (t.account_group && t.account_type) m[t.account_group] = t.account_type;
  }
  for (const h of holdingsByAccount) {
    if (h.account_group && h.account_type) m[h.account_group] = h.account_type;
  }
  return m;
})();


// Serialize an argument as JavaScript, then escape the whole handler for
// its quoted HTML attribute at the call site. HTML and JS are different
// contexts: escaping apostrophes alone does not protect either boundary.
function _jsString(value) { return JSON.stringify(String(value)); }
function fieldLabel(field) {
  const labels = {
    account_group: 'Account', account_type: 'Account type', account: 'Source account',
    cost_basis: 'Cost basis', unrealized_gain: 'Unrealized gain', realized_gain: 'Realized gain',
    total_return: 'Total gain', pct_return: 'Gain / open basis %', cash_flow: 'Cash flow',
    source_file: 'Source file', source: 'Source', source_row: 'Source row',
    acquisition_date: 'Acquired', holding_period: 'Holding period',
  };
  return (Object.hasOwn(labels, field) ? labels[field] : '') || String(field).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}

// Preserve the actual editing control when a derived-results renderer
// rebuilds its panel. Keeping the node preserves raw multi-digit input,
// selection and composition state, and lets invalid intermediate input
// remain editable without replacing it with the last accepted number.
function renderKeepingFocus(render) {
  const active = document.activeElement;
  const id = active && active.id;
  const editing = id && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
  const start = editing ? active.selectionStart : null;
  const end = editing ? active.selectionEnd : null;
  render();
  if (!id) return;
  const replacement = document.getElementById(id);
  if (!replacement || replacement === active) return;
  if (editing) {
    replacement.replaceWith(active);
    active.focus({ preventScroll: true });
    if (start != null && typeof active.setSelectionRange === 'function') active.setSelectionRange(start, end);
  } else {
    replacement.focus({ preventScroll: true });
  }
}

// Compare imported data values without interpolating them into selectors.
function dataControl(root, attribute, value) {
  return [...root.querySelectorAll('[data-' + attribute + ']')]
    .find(element => element.getAttribute('data-' + attribute) === value);
}
