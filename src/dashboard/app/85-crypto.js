// =========================================================================
// Crypto tab — per-coin holdings, income, activity timeline, conversions
// =========================================================================

function isCryptoSymbol(sym) {
  return !!sym && sym.endsWith('-USD');
}

function renderCrypto() {
  const root = document.getElementById('cryptoContent');
  if (!root) return;

  // Prefer pre-computed analytics
  const cr = ANALYTICS.crypto || {};
  const perCoin = cr.per_coin || null;
  const preRecent = cr.recent_activity || null;
  const preConvs = cr.conversions || null;
  const stats = cr.stats || null;

  // Crypto txns + holdings (used for fallback aggregation and by
  // the rest of the renderer)
  const cryptoTxns = txns.filter(t => isCryptoSymbol(t.symbol));
  const cryptoHoldings = holdingsByAsset.filter(h => isCryptoSymbol(h.symbol));

  // byCoin comes straight from the precomputed analytics (per_coin is a
  // 1:1 structural match — see analytics/crypto.py).  The old in-JS
  // re-aggregation was a dead fallback for pre-analytics exports and a
  // recompute-divergence hazard; removed.
  const byCoin = {};
  for (const c of (perCoin || [])) byCoin[c.symbol] = { ...c };

  const coinRows = Object.values(byCoin)
    .sort((a, b) => (b.value || 0) - (a.value || 0))
    .map(c => {
      const valStr = c.value != null ? fmtMoney(c.value) : '—';
      const basisStr = c.basis != null ? fmtMoney(c.basis) : '—';
      const urStr = c.unrealized != null
        ? `<span class="${c.unrealized >= 0 ? 'positive' : 'negative'}">${fmtSigned(c.unrealized)}</span>`
        : '—';
      const rCls = c.realized > 0 ? 'positive' : (c.realized < 0 ? 'negative' : '');
      return `<tr>
        <td><b>${symLabel(c.symbol)}</b></td>
        <td class="num">${c.quantity != null ? c.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 }) : '—'}</td>
        <td class="num">${c.price != null ? fmtMoney(c.price, 4) : '—'}</td>
        <td class="num">${valStr}</td>
        <td class="num">${basisStr}</td>
        <td class="num">${urStr}</td>
        <td class="num"><span class="${rCls}">${fmtSigned(c.realized)}</span></td>
        <td class="num">${fmtMoney(c.income)}</td>
        <td class="num">${c.txn_count}</td>
      </tr>`;
    }).join('');

  // Aggregates — analytics provides these pre-computed
  const totalValue = stats ? stats.total_value : cryptoHoldings.reduce((s, h) => s + (h.value || 0), 0);
  const totalBasis = stats ? stats.total_basis : cryptoHoldings.reduce((s, h) => s + (h.cost_basis || 0), 0);
  const totalUnrealized = stats ? stats.total_unrealized : (totalValue - totalBasis);
  const totalRealized = stats ? stats.total_realized
    : cryptoTxns.reduce((s, t) => s + (t.realized_gain || 0), 0);
  const totalIncome = stats ? stats.total_income
    : cryptoTxns.filter(t => t.action === 'Reward' || t.action === 'Interest')
      .reduce((s, t) => s + (t.amount || 0), 0);
  const txnCount = stats ? stats.txn_count : cryptoTxns.length;
  const coinCount = stats ? stats.coins_ever : Object.keys(byCoin).length;

  const statCards = [
    { label: 'Crypto Value', value: fmtMoney(totalValue), cls: 'positive' },
    { label: 'Crypto Basis', value: fmtMoney(totalBasis) },
    {
      label: 'Unrealized', value: fmtSigned(totalUnrealized),
      cls: totalUnrealized >= 0 ? 'positive' : 'negative'
    },
    {
      label: 'Realized (all-time)', value: fmtSigned(totalRealized),
      cls: totalRealized >= 0 ? 'positive' : 'negative'
    },
    { label: 'Staking / Reward Income', value: fmtMoney(totalIncome) },
    { label: 'Coins Held / Ever', value: `${cryptoHoldings.length} / ${coinCount}` },
    { label: 'Transactions', value: txnCount.toString() },
  ];
  const statsHtml = _renderStatCards(statCards);

  // Recent activity (last 30 crypto txns)
  const recent = preRecent || [...cryptoTxns]
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 30);
  const recentRows = recent.map(t => {
    const actionColor = ACTION_COLORS[t.action] || '';
    const actionSpan = actionColor
      ? `<span style="color:${actionColor}">${t.action || ''}</span>`
      : (t.action || '');
    const rg = t.realized_gain;
    const rgStr = typeof rg === 'number'
      ? `<span class="${rg >= 0 ? 'positive' : 'negative'}">${fmtSigned(rg)}</span>`
      : '';
    return `<tr>
      <td>${t.date || ''}</td>
      <td><b>${symLabel(t.symbol)}</b></td>
      <td>${actionSpan}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
      <td class="num">${fmtMoney(t.price, 4)}</td>
      <td class="num">${fmtMoney(t.amount)}</td>
      <td class="num">${rgStr}</td>
    </tr>`;
  }).join('');

  // Conversions: actions containing "Convert" / "Wrap" / "Unwrap"
  const convActions = new Set(['Convert In', 'Convert Out', 'Wrap Asset In', 'Wrap Asset Out', 'Unwrap In', 'Unwrap Out']);
  // For display, use raw_action if we still have it; otherwise the normalized.
  const conversions = preConvs || cryptoTxns.filter(t =>
    convActions.has(t.action) || convActions.has(t.raw_action || '') || /Convert|Wrap|Unwrap/i.test(t.raw_action || ''));
  const convRows = conversions
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 30)
    .map(t => `<tr>
      <td>${t.date || ''}</td>
      <td><b>${symLabel(t.symbol)}</b></td>
      <td>${t.action}</td>
      <td class="num">${(t.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
      <td>${(t.description || '').slice(0, 80)}</td>
    </tr>`).join('');

  root.innerHTML = `
    ${statsHtml}

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Per-Coin</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Coin</th>
          <th class="num">Quantity</th>
          <th class="num">Price</th>
          <th class="num">Value</th>
          <th class="num">Basis</th>
          <th class="num">Unrealized</th>
          <th class="num">Realized</th>
          <th class="num">Income</th>
          <th class="num">Txns</th>
        </tr></thead>
        <tbody>${coinRows || '<tr><td colspan="9" style="color:var(--text-dim);padding:12px;">No crypto activity.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Recent Crypto Activity</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Date</th><th>Coin</th><th>Action</th>
          <th class="num">Quantity</th><th class="num">Price</th><th class="num">Amount</th><th class="num">Realized</th>
        </tr></thead>
        <tbody>${recentRows || '<tr><td colspan="7" style="color:var(--text-dim);padding:12px;">—</td></tr>'}</tbody>
      </table>
    </div>

    <div class="section-header" style="margin-top:24px;"><h2><span style="color:var(--accent);">Conversions / Wraps</span></h2></div>
    <div class="panel">
      <table class="mini-table">
        <thead><tr>
          <th>Date</th><th>Coin</th><th>Action</th><th class="num">Quantity</th><th>Note</th>
        </tr></thead>
        <tbody>${convRows || '<tr><td colspan="5" style="color:var(--text-dim);padding:12px;">No conversion/wrap events recorded.</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

registerTabRenderer('crypto', renderCrypto);

