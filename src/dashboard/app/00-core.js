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

// Snapshot totals and positions describe posted custody. Verified in-kind
// transfers retain market exposure between posting dates, but belong to a
// selected scope only when that scope contains both ends of the transfer.
function _transitAmountForGroups(h, filterSet, field = 'value') {
  let total = 0;
  for (const p of h?.in_transit || []) {
    if (filterSet && (!filterSet.has(p.source_group) || !filterSet.has(p.destination_group))) continue;
    if (Number.isFinite(p[field])) total += p[field];
  }
  return total;
}

function _hasTransitForGroups(h, filterSet) {
  return (h?.in_transit || []).some(p => !filterSet
    || (filterSet.has(p.source_group) && filterSet.has(p.destination_group)));
}

// Match Python round(value, 2), including exact binary halfway values.
// Apply this only after combining full-precision posted and transit marks;
// rounding the two halves independently can manufacture a cent of return.
function _roundSnapshotCents(value) {
  if (!Number.isFinite(value) || value === 0 || Math.abs(value) >= 1e15) return value;
  const bytes = new DataView(new ArrayBuffer(8));
  bytes.setFloat64(0, Math.abs(value));
  const bits = bytes.getBigUint64(0);
  const exponent = Number((bits >> 52n) & 0x7ffn);
  const fraction = bits & ((1n << 52n) - 1n);
  const significand = exponent ? (1n << 52n) + fraction : fraction;
  const shift = (exponent || 1) - 1023 - 52;
  const numerator = significand * 100n;
  let cents;
  if (shift >= 0) cents = numerator << BigInt(shift);
  else {
    const denominator = 1n << BigInt(-shift);
    cents = numerator / denominator;
    const twiceRemainder = (numerator % denominator) * 2n;
    if (twiceRemainder > denominator || (twiceRemainder === denominator && cents % 2n)) cents++;
  }
  // Parse the exact decimal once: converting a large integer to Number
  // before division by 100 would introduce a second binary rounding.
  const rounded = Number(`${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`);
  return value < 0 ? -rounded : rounded;
}

function _snapshotValueForGroups(h, filterSet = null) {
  if (!h) return 0;
  const includeTransit = _hasTransitForGroups(h, filterSet);
  const source = includeTransit && h.valuation_precision ? h.valuation_precision : h;
  const posted = filterSet
    ? [...filterSet].reduce((sum, g) => sum + (source.by_account_group?.[g] || 0), 0)
    : (source.total || 0);
  return includeTransit ? _roundSnapshotCents(posted + _transitAmountForGroups(h, filterSet)) : posted;
}

function _snapshotBasisForGroups(h, filterSet = null) {
  if (!h) return 0;
  const includeTransit = _hasTransitForGroups(h, filterSet);
  const source = includeTransit && h.valuation_precision ? h.valuation_precision : h;
  let posted;
  if (!filterSet && Number.isFinite(source.total_cost_basis)) posted = source.total_cost_basis;
  else if (filterSet && source.cost_basis_by_group) {
    posted = [...filterSet].reduce((sum, g) => sum + (source.cost_basis_by_group[g] || 0), 0);
  } else {
    posted = (h.positions || []).reduce((sum, p) => sum
      + ((!filterSet || filterSet.has(p.account_group)) && Number.isFinite(p.cost_basis) ? p.cost_basis : 0), 0);
  }
  return includeTransit ? _roundSnapshotCents(posted + _transitAmountForGroups(h, filterSet, 'cost_basis')) : posted;
}

function _snapshotTypeGroups(type) {
  return new Set(Object.keys(ACCOUNT_TYPE_OF).filter(g => ACCOUNT_TYPE_OF[g] === type));
}

function _snapshotTypeAmount(h, type, field = 'value') {
  const groups = _snapshotTypeGroups(type);
  const includeTransit = _hasTransitForGroups(h, groups);
  const key = field === 'cost_basis' ? 'cost_basis_by_type' : 'by_account_type';
  const posted = ((includeTransit && h.valuation_precision?.[key]) || h[key] || {})[type] || 0;
  return includeTransit ? _roundSnapshotCents(posted + _transitAmountForGroups(h, groups, field)) : posted;
}

// This key stays inside display maps: it never enters account metadata,
// account filters, transactions, or performance-group membership.
const _TRANSIT_BUCKET = '\u0000in_transit';
function _snapshotBreakdown(h, field) {
  const out = Object.assign(Object.create(null), h?.valuation_precision?.[field] || h?.[field] || {});
  for (const p of h?.in_transit || []) {
    if (!Number.isFinite(p.value)) continue;
    const key = field === 'by_sector' ? (SECTOR_OF[p.symbol] || 'Other') : _TRANSIT_BUCKET;
    out[key] = (out[key] || 0) + p.value;
  }
  return out;
}
function _breakdownLabel(key) { return key === _TRANSIT_BUCKET ? 'In transit' : key; }

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
