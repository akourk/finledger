"""Data-health checks — pipeline integrity diagnostics for the dashboard.

Different from ``alerts.py`` (which surfaces *actionable financial*
signals — concentration risk, harvest candidates, etc.).  Data-health
checks answer "is the underlying data sound?": rollup math consistent,
no negative basis, no future-dated rows, no stale price coverage on
held symbols, no orphaned cache meta.

Severity tiers:
- ``high``  : math is broken / data lost (bug, must investigate)
- ``warn``  : coverage / classification gaps that distort displayed
              figures (worth fixing but dashboard still works)
- ``info``  : FYI items, often parser / broker quirks the user can't
              do much about (e.g. older Coinbase Buys with no
              bank-account note)

Output: list of ``{kind, severity, category, message, details, count}``
dicts.  Rendered as a collapsible panel on the Overview tab.
"""

from __future__ import annotations

from .. import clock
from collections import defaultdict
from datetime import datetime
import math
from pathlib import Path

from ..basis import txn_external_cash_flow


def _check_unpriced_account_transfers(txns: list[dict]) -> list[dict]:
    unpriced = [t for t in txns if t.get("account_transfer")
                and t["account_transfer"].get("flow") is None]
    if not unpriced:
        return []
    return [{
        "kind": "unpriced_account_transfer",
        "severity": "warn",
        "category": "Coverage",
        "message": f"{len(unpriced)} paired transfer leg(s) have no market value "
                   "— account returns cannot neutralize these movements. "
                   "Check price coverage on the transfer dates.",
        "details": [f"{t.get('date', '')} {t.get('account_group', '')} "
                    f"{t.get('action', '')} {t.get('symbol', '')}"
                    for t in unpriced[:5]],
        "count": len(unpriced),
    }]


def _check_transfer_share_units(txns: list[dict]) -> list[dict]:
    """Matching raw quantities do not reconcile a split between posting dates.

    Check both endpoints separately from transit marks: a split effective on
    arrival has no post-split day in the half-open transit interval. Earlier
    supported marks and the source/destination quantities remain unchanged.
    """
    from .. import valuation
    from ..return_flows import eligible_account_transfer_pairs

    incompatible = []
    for tout, tin in eligible_account_transfer_pairs(txns):
        factors = [valuation.split_factor_since(t["symbol"], t["date"])
                   for t in (tout, tin)]
        if not all(math.isfinite(f) and f > 0 and f == factors[0] for f in factors):
            incompatible.append((tout, tin))
    if not incompatible:
        return []
    return [{
        "kind": "unsupported_transfer_share_units",
        "severity": "warn",
        "category": "Coverage",
        "message": f"{len(incompatible)} paired transfer(s) span incompatible "
                   "split-adjusted share units. Matching raw quantities cannot "
                   "reconcile these movements; portfolio valuations and returns "
                   "may be distorted. Check both transfer legs and corporate actions.",
        "details": [f"{tout['date']} → {tin['date']} {tout['symbol']} "
                    f"{tout['account_group']} → {tin['account_group']}"
                    for tout, tin in incompatible[:5]],
        "count": len(incompatible),
    }]


def _check_future_dated(txns: list[dict]) -> list[dict]:
    today = clock.now(fallback=datetime.now).date().isoformat()
    future = [t for t in txns if t.get("date", "") > today]
    if not future:
        return []
    samples = [f"{t['date']} {t.get('account_group', '')} {t.get('action', '')} "
               f"{t.get('symbol', '')}" for t in future[:5]]
    return [{
        "kind": "future_dated_txns",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(future)} transaction(s) dated in the future "
                   "— parser bug or timezone issue.",
        "details": samples,
        "count": len(future),
    }]


def _check_unpriced_transfer_transit(history: list[dict],
                                    transit_issues: list[dict] | None = None) -> list[dict]:
    # Daily coverage and snapshot coverage can describe the same failed mark.
    missing_by_key = {}
    for h in [*history, *(transit_issues or [])]:
        for p in h.get("in_transit", ()):
            if p.get("value") is None:
                day = h.get("date", "")
                key = (day, *(p.get(k) for k in ("source_group", "destination_group",
                       "start_date", "end_date", "symbol", "quantity")))
                missing_by_key[key] = (day, p)
    missing = list(missing_by_key.values())
    if not missing:
        return []
    return [{
        "kind": "unpriced_transfer_transit",
        "severity": "warn",
        "category": "Coverage",
        "message": f"{len(missing)} historical in-transit position(s) have no "
                   "supported valuation. Portfolio returns and balance declines "
                   "may be distorted; check transfer-date prices and share units.",
        "details": [f"{day} {p.get('symbol', '')} "
                    f"{p.get('source_group', '')} → {p.get('destination_group', '')}: "
                    f"{p.get('valuation_issue', 'unpriced')}"
                    for day, p in missing[:5]],
        "count": len(missing),
    }]


def _check_negative_cost_basis(holdings_by_account: list[dict]) -> list[dict]:
    neg = [h for h in holdings_by_account
           if (h.get("cost_basis") or 0) < -0.01]
    if not neg:
        return []
    samples = [f"{h.get('account_group', '')} {h.get('symbol', '')} "
               f"cost_basis=${h.get('cost_basis', 0):,.2f}" for h in neg[:5]]
    return [{
        "kind": "negative_cost_basis",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(neg)} holding(s) with negative cost basis — "
                   "basis walker error (more sold than bought).",
        "details": samples,
        "count": len(neg),
    }]


def _check_snapshot_rollup(history: list[dict]) -> list[dict]:
    """Snapshot total should equal Σ by_account_group within $1."""
    bad = []
    for h in history:
        by_grp = h.get("by_account_group", {}) or {}
        grp_sum = sum(v for v in by_grp.values()
                      if isinstance(v, (int, float)))
        diff = abs((h.get("total") or 0) - grp_sum)
        if diff > 1.0:
            bad.append((h["date"], h.get("total", 0), grp_sum, diff))
    if not bad:
        return []
    bad.sort(key=lambda r: -r[3])
    samples = [f"{d}: total=${t:,.2f} but sum=${gs:,.2f} (diff ${diff:,.2f})"
               for d, t, gs, diff in bad[:5]]
    return [{
        "kind": "snapshot_rollup_mismatch",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(bad)} history snapshot(s) where total ≠ Σ "
                   "by_account_group — history walker arithmetic error.",
        "details": samples,
        "count": len(bad),
    }]


def _check_lots_holdings_basis_parity(analytics: dict,
                                      holdings_by_account: list[dict]) -> list[dict]:
    """Every exported lot-inventory position's cost basis (visible lots
    + folded micro lots) must equal the Holdings row it anchors to —
    ties ``analytics.lots`` to the basis walker and catches dust-folding
    or grouping bugs in the per-lot export."""
    lots = (analytics.get("lots") or {}).get("positions") or []
    if not lots:
        return []
    holding_basis = {(h.get("account_group", ""), h.get("symbol", "")):
                     h.get("cost_basis")
                     for h in holdings_by_account}
    bad = []
    for p in lots:
        key = (p.get("account_group", ""), p.get("symbol", ""))
        hb = holding_basis.get(key)
        if not isinstance(hb, (int, float)):
            continue
        diff = abs(float(p.get("cost_basis", 0) or 0) - hb)
        if diff > 0.05:
            bad.append((key, p.get("cost_basis"), hb, diff))
    if not bad:
        return []
    bad.sort(key=lambda r: -r[3])
    samples = [f"{a} {s}: lots=${lb:,.2f} vs holdings=${hb:,.2f}"
               for (a, s), lb, hb, _d in bad[:5]]
    return [{
        "kind": "lots_holdings_basis_parity",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(bad)} position(s) where the exported lot "
                   "inventory's cost basis disagrees with the Holdings "
                   "table — per-lot export drifted from the basis walker.",
        "details": samples,
        "count": len(bad),
    }]


def _check_open_options_past_expiration(analytics: dict) -> list[dict]:
    today = clock.now(fallback=datetime.now).date().isoformat()
    opts = (analytics.get("options") or {}).get("open_contracts", []) or []
    expired = [o for o in opts
               if (o.get("expiry") or "") and o.get("expiry") < today]
    if not expired:
        return []
    samples = [f"{o.get('symbol', '')} expiry={o.get('expiry')}"
               for o in expired[:5]]
    return [{
        "kind": "expired_options_still_open",
        "severity": "warn",
        "category": "Integrity",
        "message": f"{len(expired)} option contract(s) past expiration "
                   "still showing as open — missing OEXP/OEXCS row in CSV.",
        "details": samples,
        "count": len(expired),
    }]


def _check_realized_gain_reconciliation(txns: list[dict],
                                        analytics: dict) -> list[dict]:
    """Sum of txn-level realized_gain should match analytics.tax sum."""
    txn_sum = sum((t.get("realized_gain") or 0) for t in txns)
    ry = (analytics.get("tax") or {}).get("realized_by_year", []) or []
    ay = sum((r.get("st", 0) or 0) + (r.get("lt", 0) or 0)
             for r in ry if isinstance(r, dict))
    diff = txn_sum - ay
    # The by-year rows are each rounded to cents, so summing N of them
    # drifts from the unrounded txn total by up to ~$0.005 × N — pure
    # rounding, not a real inconsistency.  Tolerate a penny per year row
    # (floor $0.01) so a clean portfolio doesn't perpetually warn.
    tolerance = max(0.01, 0.01 * len(ry))
    if abs(diff) <= tolerance:
        return []
    return [{
        "kind": "realized_gain_drift",
        "severity": "warn",
        "category": "Integrity",
        "message": (f"Realized gain reconciliation drift: txn-level sum "
                    f"${txn_sum:,.2f} vs analytics by-year sum ${ay:,.2f} "
                    f"(diff ${diff:,.2f})."),
        "count": 1,
    }]


def _check_held_symbol_price_health(holdings_by_account: list[dict],
                                    cache_dir: Path) -> list[dict]:
    """Held symbols whose price-cache meta has failures or stale coverage."""
    import json
    meta_path = cache_dir / "price_cache_meta.json"
    if not meta_path.exists():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sym_meta = meta.get("symbols", {})
    held = {h.get("symbol", "") for h in holdings_by_account
            if (h.get("quantity", 0) or 0) > 0}

    # Check 1: held + has failure_count > 0 + no proxy = real fetch problem
    proxy_path = cache_dir / "symbol_proxy_map.json"
    proxied: set[str] = set()
    if proxy_path.exists():
        try:
            pm = json.loads(proxy_path.read_text(encoding="utf-8"))
            proxied = {k for k, v in pm.items()
                       if isinstance(v, dict) and "proxy" in v}
        except (OSError, ValueError):
            pass

    # Skip symbols the classifier marks as no-fetch — corp-action
    # stubs (CVRs, warrants), multi-word fund display names, etc.
    # These never reach yfinance, so any failure_count on them is
    # stale meta from before the classifier was extended.
    from ..prices import _classify_no_fetch
    failing = []
    orphan_meta = []
    for sym in sorted(held):
        if _classify_no_fetch(sym):
            continue
        m = sym_meta.get(sym, {})
        fc = m.get("failure_count", 0) or 0
        is_tomb = bool(m.get("tombstone"))
        if (fc > 0 or is_tomb) and sym not in proxied:
            failing.append((sym, fc, is_tomb,
                            (m.get("last_error") or "")[:60]))
        elif (fc > 0 or is_tomb) and sym in proxied:
            orphan_meta.append(sym)

    today = clock.now(fallback=datetime.now).date()
    stale = []
    for sym in sorted(held):
        if sym in proxied:
            # Stale proxy entries are caught by checking the proxy
            continue
        m = sym_meta.get(sym, {})
        ce = m.get("covered_end")
        if not ce:
            continue
        try:
            gap = (today - datetime.strptime(ce, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if gap > 7:
            stale.append((sym, ce, gap))

    out = []
    if failing:
        details = [f"{s} (failures={fc}{', tombstoned' if t else ''}: "
                   f"{err})" for s, fc, t, err in failing[:8]]
        out.append({
            "kind": "price_fetch_failing",
            "severity": "warn",
            "category": "Coverage",
            "message": (f"{len(failing)} held symbol(s) failing price "
                        "fetches — may be delisted or have ticker-format "
                        "issues yfinance can't resolve."),
            "details": details,
            "count": len(failing),
        })
    if stale:
        details = [f"{s}: last covered {ce}, {g} days stale"
                   for s, ce, g in sorted(stale, key=lambda x: -x[2])[:8]]
        out.append({
            "kind": "stale_price_coverage",
            "severity": "info",
            "category": "Coverage",
            "message": (f"{len(stale)} held symbol(s) with price coverage "
                        "more than 7 days stale — re-run pipeline to "
                        "refresh, or check for fetch failures."),
            "details": details,
            "count": len(stale),
        })
    if orphan_meta:
        out.append({
            "kind": "orphan_meta_with_proxy",
            "severity": "info",
            "category": "Coverage",
            "message": (f"{len(orphan_meta)} cache meta entr(y/ies) with "
                        "stale failure_count for symbols that now have a "
                        "working proxy — safe to delete those entries."),
            "details": orphan_meta[:8],
            "count": len(orphan_meta),
        })
    return out


def _check_per_account_negative_contributions(txns: list[dict],
                                               holdings_by_account: list[dict]) -> list[dict]:
    """Per-account net_contributed sharply negative — could be either:
       (a) the user genuinely withdrew more than they put in because
           the position appreciated and they cashed out gains
           (totally valid; common with crypto), or
       (b) the broker CSV is missing the bank-funding leg of Buys, so
           external money in is invisible while withdrawals are
           tracked (older Coinbase exports do this).

    We can distinguish (a) from (b): if the account's current value
    plus net withdrawals nets POSITIVE, the user made money on the
    position and case (a) applies — the negative net_contributed is
    correct and not a data-quality issue.
    """
    by_grp_flow: dict[str, float] = defaultdict(float)
    for t in txns:
        by_grp_flow[t.get("account_group", "?")] += txn_external_cash_flow(t)

    cur_value_by_grp: dict[str, float] = defaultdict(float)
    for h in holdings_by_account:
        v = h.get("value")
        if isinstance(v, (int, float)):
            cur_value_by_grp[h.get("account_group", "")] += v

    suspect = []
    explained = []
    for g, flow in by_grp_flow.items():
        if flow >= -1000:
            continue
        # Implied total P&L = current value − net contributed.
        # If positive, the negative net_contributed reflects gains
        # withdrawn, not missing data.
        cv = cur_value_by_grp.get(g, 0.0)
        implied_pnl = cv - flow   # cv − (negative) = cv + |flow|
        if cv > 0 and implied_pnl > 0:
            explained.append((g, flow, cv, implied_pnl))
        else:
            suspect.append((g, flow))

    out = []
    if suspect:
        suspect.sort(key=lambda x: x[1])
        samples = [f"{g}: net_contributed=${v:,.2f}"
                   for g, v in suspect[:5]]
        out.append({
            "kind": "missing_funding_leg",
            "severity": "info",
            "category": "Reconciliation",
            "message": ("Account(s) with deeply negative net_contributed "
                        "AND no remaining holdings to explain it — likely "
                        "the broker CSV missed the bank-funding leg of "
                        "Buys.  Older Coinbase exports do this (no "
                        "\"using bank account ...\" notes pre-2018)."),
            "details": samples,
            "count": len(suspect),
        })
    if explained:
        explained.sort(key=lambda x: x[1])
        samples = [f"{g}: contributed ${v:,.0f}, holdings now ${cv:,.0f} "
                   f"(net P&L: ${pnl:+,.0f}) — gains withdrawn"
                   for g, v, cv, pnl in explained[:5]]
        out.append({
            "kind": "gains_withdrawn",
            "severity": "info",
            "category": "Reconciliation",
            "message": (f"{len(explained)} account(s) show negative "
                        "net_contributed because the user withdrew more "
                        "than they put in — but current holdings + "
                        "withdrawals exceeds contributions, so the math "
                        "is consistent with a profitable position whose "
                        "gains were partially cashed out.  Not a data "
                        "issue."),
            "details": samples,
            "count": len(explained),
        })
    return out


def _check_priced_coverage(history: list[dict]) -> list[dict]:
    """Snapshots with priced_pct < 95% = some held positions had no
    price for that date.  Indicates data gaps."""
    weak = [h for h in history if (h.get("priced_pct", 1.0) or 1.0) < 0.95]
    if not weak:
        return []
    samples = [f"{h['date']}: priced_pct={h.get('priced_pct', 1):.2%} "
               f"total=${h.get('total', 0):,.0f}" for h in weak[:5]]
    return [{
        "kind": "snapshot_coverage_gap",
        "severity": "info",
        "category": "Coverage",
        "message": (f"{len(weak)} history snapshot(s) where some held "
                    "positions had no price — value figure may be slightly "
                    "approximated for those dates."),
        "details": samples,
        "count": len(weak),
    }]


def _check_orphan_zero_qty_basis(holdings_by_account: list[dict]) -> list[dict]:
    """Holdings with zero quantity but non-zero cost basis = the basis
    walker didn't fully wind down a closed position."""
    bad = [h for h in holdings_by_account
           if abs(h.get("quantity", 0) or 0) < 1e-9
           and abs(h.get("cost_basis", 0) or 0) > 0.01]
    if not bad:
        return []
    samples = [f"{h.get('account_group', '')} {h.get('symbol', '')} "
               f"qty=0 but cb=${h.get('cost_basis', 0):,.2f}"
               for h in bad[:5]]
    return [{
        "kind": "zero_qty_with_basis",
        "severity": "warn",
        "category": "Integrity",
        "message": f"{len(bad)} closed position(s) with non-zero cost "
                   "basis — basis walker leftover; total realized may be "
                   "off by the residual amount.",
        "details": samples,
        "count": len(bad),
    }]


def _check_value_qty_price_consistency(holdings_by_account: list[dict]) -> list[dict]:
    """For priced holdings, value should equal qty × price within tolerance.

    The stored ``price`` field is rounded to 2 decimals for display,
    but ``value`` is computed from the unrounded price.  So per-share
    rounding error of up to $0.005 propagates to ``qty * 0.005`` of
    total drift — that's not a bug, just display rounding.  Tolerance
    needs to absorb that PLUS a small absolute floor; real value
    mismatches would be way larger.

    Option rows value at qty × price × 100 (quantity is CONTRACTS,
    price the per-share premium — config.contract_multiplier), so the
    expectation is scaled the same way the holdings builder scales it.

    NEGATIVE quantities are checked too.  This gated on ``qty > 0``,
    which silently exempted every short/negative row — and those are
    reachable: an orphan OEXP / OEXCS whose opening BTO predates the
    CSV window pushes a contract balance below zero.  ``is_dust`` drops
    only the UNPRICED ones, so a priced negative position reached the
    holdings table with nothing checking its valuation.  Exempting the
    one class of row most likely to be mis-signed is how a check ends
    up passing over the bug it exists to find (see docs/AUDIT.md F-035, same
    shape).  The tolerance takes ``abs(expected)`` so a negative
    expectation can't produce a negative relative bound.
    """
    from ..config import contract_multiplier
    bad = []
    for h in holdings_by_account:
        qty = h.get("quantity", 0) or 0
        val = h.get("value", 0) or 0
        pr = h.get("price", 0) or 0
        if qty != 0 and pr > 0:
            mult = contract_multiplier(h.get("symbol", ""))
            expected = qty * pr * mult
            # 0.5¢/share rounding × qty (×100 for contracts) + 1‰
            # relative + 10¢ floor.
            tolerance = max(0.10, abs(expected) * 0.001, abs(qty) * 0.006 * mult)
            if abs(val - expected) > tolerance:
                bad.append((h.get("account_group", ""), h.get("symbol", ""),
                            qty, pr, val, expected))
    if not bad:
        return []
    samples = [f"{g} {s}: qty {q} × price {p} = expected {exp:,.2f} "
               f"but value={v:,.2f}" for g, s, q, p, v, exp in bad[:5]]
    return [{
        "kind": "value_qty_price_mismatch",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(bad)} holding(s) where value ≠ qty × price.",
        "details": samples,
        "count": len(bad),
    }]


def _check_lot_queue_parity(txns: list[dict],
                            holdings_by_account: list[dict]) -> list[dict]:
    """For every non-USD (account_group, symbol), the basis walker's
    running cost basis should match the holdings-table cost_basis.
    Detects drift between the basis walker and the balance walker —
    which would mean the dashboard's per-account "Unrealized P&L"
    disagrees with the lot-method comparison table."""
    from ..basis import derive_basis_by_key_from_txns
    walked_basis = derive_basis_by_key_from_txns(txns)

    # Each held key's cost_basis from holdings_by_account should match
    # the walked sum (within rounding).
    drift = []
    for h in holdings_by_account:
        sym = h.get("symbol", "")
        if not sym or sym == "USD":
            # Deliberate, unlike the identical-looking skip that used to
            # sit in _check_history_holdings_basis_parity (F-035): this
            # check compares against derive_basis_by_key_from_txns, a
            # reconstruction from per-txn basis_effect annotations, and
            # every cash row is classified "ignore" — there is no cash
            # figure on the other side to compare to.  Savings cash
            # basis is covered by the history/holdings parity check,
            # where both sides genuinely compute it.
            continue
        if abs(h.get("quantity", 0) or 0) < 1e-9:
            continue
        held_cb = h.get("cost_basis")
        if held_cb is None:
            continue   # no FIFO basis tracked (e.g. no Buy ever); skip
        key = (h.get("account_group", ""), sym)
        expected = walked_basis.get(key, 0.0)
        # The tolerance is not slack — `derive_basis_by_key_from_txns`
        # sums per-txn annotations that are each rounded to cents, so on
        # a position built from many fills it lands a cent or two from
        # the walker's full-precision total.  Don't tighten this to 0;
        # fix the annotations' precision first if you need to.
        if abs(held_cb - expected) > 0.05:
            drift.append((key, held_cb, expected, held_cb - expected))

    if not drift:
        return []
    samples = [f"{a}/{s}: holdings cb=${hcb:,.2f} but walked={exp:,.2f} "
               f"(drift ${d:,.2f})"
               for (a, s), hcb, exp, d in drift[:5]]
    return [{
        "kind": "lot_queue_parity_drift",
        "severity": "high",
        "category": "Integrity",
        "message": (f"{len(drift)} (account, symbol) where the FIFO "
                    "walker's cost-basis sum disagrees with the holdings "
                    "table — the lot-method comparison and the headline "
                    "Unrealized P&L will report different numbers."),
        "details": samples,
        "count": len(drift),
    }]


def _check_history_holdings_basis_parity(history: list[dict],
                                         holdings_by_account: list[dict]) -> list[dict]:
    """The latest history snapshot's per-position cost basis must match
    the holdings table's cost basis for every (account, symbol) present
    in both — cash included.

    history.compute_history has its own inline lot walker (it needs lot
    state at every sample date), and basis-rule changes in basis.py have
    twice landed without the matching history.py change (wrap
    basis-carrying, FMV transfer-ins) — silently desyncing the Overview
    chart's Cost Basis line and the as-of-date holdings view from the
    Holdings table.  This check pins the two walkers together.

    CASH USED TO BE EXEMPT and should never have been.  The exemption
    was written when the snapshot walker booked cash at face value and
    the holdings table booked it at principal, so a HYSA's every
    accrued dollar of interest was a disagreement — the skip made the
    check pass by declining to look.  Both walkers now use principal
    (``pipeline_stages.cash_principal_effect``); iterating holdings
    means the bridged groups' synthetic snapshot USD rows, which have
    no holdings counterpart and are genuinely face-value, are still
    skipped by the `key not in snap_basis` guard below."""
    if not history:
        return []
    latest = history[-1]
    snap_basis: dict[tuple[str, str], float] = {}
    for p in latest.get("positions", []) or []:
        sym = p.get("symbol", "")
        if not sym:
            continue
        key = (p.get("account_group", ""), sym)
        snap_basis[key] = snap_basis.get(key, 0.0) + float(p.get("cost_basis") or 0)

    drift = []
    for h in holdings_by_account:
        sym = h.get("symbol", "")
        if not sym:
            continue
        cb = h.get("cost_basis")
        if cb is None:
            continue
        key = (h.get("account_group", ""), sym)
        if key not in snap_basis:
            continue   # dust/pricing edge — covered by other checks
        if abs(cb - snap_basis[key]) > 0.05:
            drift.append((key, cb, snap_basis[key], cb - snap_basis[key]))

    if not drift:
        return []
    drift.sort(key=lambda r: -abs(r[3]))
    samples = [f"{a}/{s}: holdings cb=${hcb:,.2f} but latest snapshot "
               f"cb=${scb:,.2f} (drift ${d:,.2f})"
               for (a, s), hcb, scb, d in drift[:5]]
    return [{
        "kind": "history_holdings_basis_parity",
        "severity": "high",
        "category": "Integrity",
        "message": (f"{len(drift)} (account, symbol) where the latest "
                    "history snapshot's cost basis disagrees with the "
                    "holdings table — history.py's lot walker has drifted "
                    "from basis.py (check wrap/transfer/override rules)."),
        "details": samples,
        "count": len(drift),
    }]


def _check_net_contributed_monotonicity(history: list[dict]) -> list[dict]:
    """Per-snapshot ``net_contributed`` should change in plausible
    increments month-over-month — large unexpected jumps signal a
    parser bug (e.g. an unseen Coinbase synthetic deposit that
    suddenly recategorized a chunk of cash flow).

    Threshold: any single-month delta exceeding the larger of $50k or
    20% of all-time peak portfolio value is suspect.  Real life: even
    aggressive savers contribute < $50k/mo to investment accounts.
    Tax-deferred custodial rollovers (which can be $XXk in one event)
    are skipped from net_contributed by design — see the Roth/Rollover
    Distribution carve-out in ``basis.txn_external_cash_flow``.
    """
    if len(history) < 2:
        return []
    from ..return_flows import scope_snapshot_value
    atl_peak = max(scope_snapshot_value(h) for h in history) or 0
    threshold = max(50_000.0, atl_peak * 0.20)

    suspect = []
    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]
        d = (float(curr.get("net_contributed") or 0)
             - float(prev.get("net_contributed") or 0))
        if abs(d) > threshold:
            suspect.append((curr.get("date", ""), d,
                            float(prev.get("net_contributed") or 0),
                            float(curr.get("net_contributed") or 0)))
    if not suspect:
        return []
    samples = [f"{dt}: net_contributed jumped ${d:+,.0f} "
               f"(${prev:,.0f} → ${cur:,.0f})"
               for dt, d, prev, cur in suspect[:5]]
    return [{
        "kind": "net_contributed_jump",
        "severity": "warn",
        "category": "Reconciliation",
        "message": (f"{len(suspect)} month(s) where net_contributed "
                    f"changed by more than ${threshold/1000:.0f}k — "
                    "could be a parser bug or a major life event "
                    "(home sale, inheritance) — worth eyeballing."),
        "details": samples,
        "count": len(suspect),
    }]


def _check_twr_sanity_bounds(analytics: dict) -> list[dict]:
    """Per-year TWR figures above ±500% are almost always
    denominator-explosion artifacts on a tiny early-portfolio base.

    Note: ``twr_pct`` is stored in PERCENT (e.g. 24.47 means 24.47%,
    not 2447%).  Bug fixed 2026-04-27 — earlier version was treating
    ``twr_pct`` as a fraction and flagging every >5% return as
    suspect.  Crypto + concentrated bets can plausibly produce
    +200% or -90% annual returns, so the ±500% threshold is the
    line where we're confident it's a small-base artifact, not a
    real market move."""
    perf = analytics.get("performance_by_filter", {}) or {}
    suspect = []
    for filt_name, info in perf.items():
        for r in info.get("annual", []) or []:
            twr = r.get("twr_pct")
            if twr is None:
                continue
            try:
                v = float(twr)
            except (TypeError, ValueError):
                continue
            if abs(v) > 500.0:   # ±500% (twr_pct is in percent units)
                suspect.append((filt_name, r.get("year"), v))
    if not suspect:
        return []
    samples = [f"{f}/{y}: TWR = {v:+.0f}%" for f, y, v in suspect[:5]]
    return [{
        "kind": "implausible_twr",
        "severity": "warn",
        "category": "Integrity",
        "message": (f"{len(suspect)} (filter, year) pair(s) where "
                    "annual TWR exceeded ±500% — likely a "
                    "small-base denominator artifact in early-portfolio "
                    "history."),
        "details": samples,
        "count": len(suspect),
    }]


def _check_cash_flow_conservation(txns: list[dict],
                                   cash_summary: dict | None) -> list[dict]:
    """Sum of per-txn ``cash_flow`` should equal
    ``cash_summary.net_contributed`` exactly.  Both come from the same
    ``txn_external_cash_flow`` helper, but the per-txn annotation runs
    on a different code path than the lifetime aggregate.  A drift
    here means one of the two paths missed a carve-out."""
    if not cash_summary:
        return []
    txn_sum = sum(float(t.get("cash_flow") or 0) for t in txns)
    nc = float(cash_summary.get("net_contributed") or 0)
    diff = abs(txn_sum - nc)
    if diff < 0.05:
        return []
    return [{
        "kind": "cash_flow_conservation_drift",
        "severity": "high",
        "category": "Integrity",
        "message": (f"Σ(per-txn cash_flow) ${txn_sum:,.2f} ≠ "
                    f"cash_summary.net_contributed ${nc:,.2f} "
                    f"(diff ${diff:,.2f}).  txn_external_cash_flow "
                    "must be the single source of truth for both."),
        "count": 1,
    }]


def _check_action_catalog_coverage(txns: list[dict]) -> list[dict]:
    """Every action used in txns should be defined in the action
    catalog (src/actions.py).  Unknown actions slip through default
    sets (subtract / neutral / cash) silently and produce wrong
    balances or basis rollups."""
    from ..actions import to_json_dict
    catalog = {a["name"] for a in to_json_dict().get("actions", [])}
    used = {t.get("action", "") for t in txns if t.get("action")}
    missing = sorted(used - catalog)
    if not missing:
        return []
    samples = missing[:8]
    counts = {a: sum(1 for t in txns if t.get("action") == a) for a in samples}
    details = [f"{a} ({counts[a]} txns)" for a in samples]
    return [{
        "kind": "action_not_in_catalog",
        "severity": "warn",
        "category": "Integrity",
        "message": (f"{len(missing)} action name(s) used by parsers "
                    "but missing from src/actions.py — they fall back "
                    "to default classification (likely wrong)."),
        "details": details,
        "count": len(missing),
    }]


def _check_snapshot_date_monotonicity(history: list[dict]) -> list[dict]:
    """``history`` must be strictly ascending by date.  Out-of-order
    snapshots break TWR's chain-link calculation (it walks index-by-
    index assuming time order)."""
    bad = []
    for i in range(1, len(history)):
        prev = history[i - 1].get("date", "")
        curr = history[i].get("date", "")
        if prev and curr and prev >= curr:
            bad.append((i, prev, curr))
    if not bad:
        return []
    samples = [f"index {i}: {p} → {c}" for i, p, c in bad[:5]]
    return [{
        "kind": "snapshot_dates_not_ascending",
        "severity": "high",
        "category": "Integrity",
        "message": (f"{len(bad)} pair(s) of consecutive history "
                    "snapshots not in ascending date order."),
        "details": samples,
        "count": len(bad),
    }]


def _check_holding_days_non_negative(txns: list[dict]) -> list[dict]:
    """Sells are annotated with ``holding_days`` (days from earliest
    matched lot to sale date).  Negative values mean the basis walker
    matched a future-acquired lot to a past sale — pairing bug."""
    bad = []
    for t in txns:
        hd = t.get("holding_days")
        if isinstance(hd, (int, float)) and hd < 0:
            bad.append(t)
    if not bad:
        return []
    samples = [f"{t.get('date')} {t.get('symbol')} {t.get('action')} "
               f"holding_days={t.get('holding_days')}" for t in bad[:5]]
    return [{
        "kind": "negative_holding_days",
        "severity": "high",
        "category": "Integrity",
        "message": f"{len(bad)} sell(s) with negative holding_days — "
                   "basis walker matched a sale to a lot acquired AFTER it.",
        "details": samples,
        "count": len(bad),
    }]


def _check_parser_dropped_rows(parse_report: list[dict] | None) -> list[dict]:
    """Rows a parser dropped, and files that parsed to zero.

    F-017 tier 3. Every parser discards an unparseable row with a bare
    ``continue`` — correct for one odd row, and catastrophic when a
    broker changes its date format, because the whole file goes to zero
    and that account simply vanishes from the portfolio.

    Tiers 1 and 2 detect it and print to the console. That is the one
    place a daily run's output is least likely to be read, and nothing
    downstream reliably catches the miss: What's-Changed reports it only
    after the fact, and the empty-run guard fires only when EVERY file
    is empty, so one missing broker sails through.

    Severity splits on what the evidence supports:

    * **high** — a recognised file with data rows that parsed to ZERO.
      An entire account is missing. There is no benign reading.
    * **warn** — some rows dropped from a file that otherwise parsed.
      One malformed row in an export is ordinary; a large count is the
      same format change caught earlier, so the count is in the message.
    """
    if not parse_report:
        return []

    issues: list[dict] = []

    empty = [r for r in parse_report if r.get("empty_with_data")]
    if empty:
        issues.append({
            "kind": "parser_produced_no_rows",
            "severity": "high",
            "category": "Integrity",
            "message": (
                f"{len(empty)} broker file(s) were recognised and contain "
                "data rows but parsed to ZERO transactions — those accounts "
                "are MISSING from this portfolio entirely. The export "
                "format has almost certainly changed; compare the file's "
                "header and date format against its parser."),
            "details": [f"{r['file']} — detected as '{r['broker']}', "
                        f"0 transactions parsed" for r in empty],
            "count": len(empty),
        })

    dropped = [r for r in parse_report
               if r.get("dropped") and not r.get("empty_with_data")]
    if dropped:
        total = sum(int(r.get("dropped") or 0) for r in dropped)
        issues.append({
            "kind": "parser_dropped_rows",
            "severity": "warn",
            "category": "Integrity",
            "message": (
                f"{total} row(s) across {len(dropped)} file(s) were dropped "
                "because their date could not be parsed. Those transactions "
                "are absent from every figure on this dashboard. A handful "
                "is usually one malformed export row; a large count means "
                "the broker's format changed."),
            "details": [f"{r['file']} ({r['broker']}) — {r['dropped']} "
                        f"dropped, {r['parsed']} parsed" for r in dropped],
            "count": total,
        })

    return issues


def _check_unbridged_retirement_distribution(txns: list[dict],
                                             analytics: dict) -> list[dict]:
    """A Roth/Rollover IRA ``Distribution`` with no covering rollover
    bridge means the in-flight window renders as a phantom dip-to-zero
    on the history chart and pollutes TWR / drawdown / monthly P&L.

    The bridge matcher (``detect_rollover_bridges``) pairs Distribution
    events with Transfer In(s) within 90 days at ±5%; when it can't
    find a match (amount drift beyond tolerance, arrival > 90 days out,
    a Transfer In row the parser didn't produce), the failure is
    silent — this check makes it loud, with the event details so the
    user can see exactly which rollover needs attention."""
    bridges = analytics.get("rollover_bridges") or []

    # Rebuild the distribution events the matcher looks at.
    dist_by_key: dict[tuple[str, str], float] = defaultdict(float)
    for t in txns:
        if t.get("action") != "Distribution":
            continue
        g = t.get("account_group", "")
        if g not in ("Rollover IRA", "Roth IRA"):
            continue
        d = t.get("date", "")
        if d:
            dist_by_key[(g, d)] += float(t.get("amount", 0) or 0)

    unbridged = []
    for (g, d), amt in dist_by_key.items():
        if amt < 1000:
            continue   # small distributions don't move the charts
        covered = any(b.get("group") == g
                      and b.get("start_date", "") <= d < b.get("end_date", "")
                      for b in bridges)
        if not covered:
            unbridged.append((g, d, amt))

    if not unbridged:
        return []
    unbridged.sort(key=lambda x: -x[2])
    samples = [f"{g}: ${a:,.0f} distributed {d} — no matching Transfer In "
               f"within 90 days / ±5%" for g, d, a in unbridged[:5]]
    return [{
        "kind": "unbridged_retirement_distribution",
        "severity": "warn",
        "category": "Reconciliation",
        "message": (f"{len(unbridged)} retirement Distribution event(s) "
                    "with no rollover bridge — the in-flight window shows "
                    "as a phantom dip on the history chart and distorts "
                    "drawdown / monthly P&L until the matching Transfer "
                    "In is found (check the destination account's CSV "
                    "covers the arrival, and that amounts agree within 5%)."),
        "details": samples,
        "count": len(unbridged),
    }]


def _check_broker_cash_balance(txns: list[dict]) -> list[dict]:
    """Cash may legitimately remain in a brokerage wallet.

    Check impossible negative reconstructed cash before valuation clamps it
    to zero. Positive balances are reconciled against the user's declared
    statement values by the reconciliation panel; no personal wallet's
    historical zero balance is a universal target.
    """
    from ..cash_bridge import all_series
    bad = [(group, day, value)
           for group, series in all_series(txns).items()
           for day, value in series if value < -0.01]
    if not bad:
        return []
    return [{
        "kind": "negative_broker_cash",
        "severity": "warn",
        "category": "Reconciliation",
        "message": "Reconstructed broker cash is negative; check missing funding or incorrectly classified cash movements.",
        "details": [f"{group} on {day}: ${value:,.2f}"
                    for group, day, value in sorted(bad, key=lambda item: item[2])[:5]],
        "count": len(bad),
    }]


def _check_held_symbol_sector_coverage(holdings_by_account: list[dict]) -> list[dict]:
    """Held positions worth > $1k with sector='Other' likely have
    classifiable sectors that just haven't been resolved.  Not a bug,
    but worth surfacing so the user knows which symbols would benefit
    from a manual entry in ``cache/sector_cache.json``."""
    bad = []
    for h in holdings_by_account:
        sector = h.get("sector") or ""
        val = abs(h.get("value") or 0)
        if sector == "Other" and val > 1000:
            bad.append((h.get("symbol", ""), val))
    if not bad:
        return []
    bad.sort(key=lambda x: -x[1])
    samples = [f"{s}: ${v:,.2f}" for s, v in bad[:8]]
    return [{
        "kind": "held_unclassified_sector",
        "severity": "info",
        "category": "Coverage",
        "message": (f"{len(bad)} held position(s) worth > $1k "
                    "classified as sector='Other' — adding a manual "
                    "sector mapping would improve allocation breakdowns."),
        "details": samples,
        "count": len(bad),
    }]


def _check_per_position_basis_sanity(holdings_by_account: list[dict]) -> list[dict]:
    """Per-holding cost-basis sanity: a position's basis-per-share
    should be in the same order of magnitude as its current price.
    A 100x mismatch usually means a split adjustment got applied
    on one side but not the other (basis kept at pre-split price
    while quantity was post-split, or vice versa).

    Filters out small positions (<$1k cost basis) — penny stocks and
    spin-off stubs legitimately drop 100x+ and would dominate the
    output without telling us anything actionable.  At >$1k of basis,
    a 100x mismatch is almost always a split bug worth investigating.
    """
    from ..config import contract_multiplier
    bad = []
    for h in holdings_by_account:
        qty = h.get("quantity", 0) or 0
        cb = h.get("cost_basis") or 0
        pr = h.get("price") or 0
        if abs(qty) < 1e-6 or pr <= 0 or cb <= 0:
            continue
        if cb < 1000:
            continue   # tiny positions: bankruptcy / penny-stock crashes are real
        # Option rows: quantity is CONTRACTS and price is the per-share
        # premium, so basis-per-unit must be normalized by the ×100
        # contract multiplier before comparing — otherwise every option
        # holding false-positives at exactly 100x.
        bps = cb / (qty * contract_multiplier(h.get("symbol", "")))
        ratio = max(bps, pr) / min(bps, pr)
        if ratio > 100:
            bad.append((h.get("account_group", ""), h.get("symbol", ""),
                        bps, pr, ratio))
    if not bad:
        return []
    bad.sort(key=lambda x: -x[4])
    samples = [f"{g}/{s}: basis ${bps:.2f}/share vs price ${pr:.2f} ({r:.0f}x)"
               for g, s, bps, pr, r in bad[:5]]
    return [{
        "kind": "basis_price_magnitude_mismatch",
        "severity": "warn",
        "category": "Integrity",
        "message": (f"{len(bad)} holding(s) (>$1k basis) where "
                    "basis-per-share and current price differ by >100x "
                    "— likely a missed stock-split adjustment."),
        "details": samples,
        "count": len(bad),
    }]


_SEVERITY_RANK = {"high": 0, "warn": 1, "info": 2}


class InvariantViolation(AssertionError):
    """Raised by ``check_invariants`` when a high-severity data-health
    check fails.  Subclasses ``AssertionError`` so it shows up as a
    proper test failure rather than an unexpected exception."""


def check_invariants(txns: list[dict],
                     holdings_by_account: list[dict],
                     history: list[dict],
                     analytics: dict,
                     cache_dir,
                     *,
                     cash_summary: dict | None = None,
                     severities: tuple[str, ...] = ("high",)) -> None:
    """Run data-health checks and raise ``InvariantViolation`` if any
    fail at the specified severities.

    Default: only ``high`` severities trigger failure (math is broken,
    data lost — these MUST never happen).  ``warn`` items remain
    diagnostic (surfaced in the dashboard panel but don't crash).

    Used in two places:
    - Tests, via the ``conftest.py`` fixture that enables assertions
      during the synthetic pipeline run.  Catches refactor bugs at the
      point where they're introduced, before they ship.
    - Production runs when ``FIN_ASSERT_INVARIANTS`` env var is set.
      Off by default — minor coverage gaps shouldn't crash the user's
      daily pipeline run.
    """
    issues = compute_data_health(
        txns, holdings_by_account, history, analytics, cache_dir,
        cash_summary=cash_summary,
    )
    violations = [i for i in issues if i["severity"] in severities]
    if violations:
        lines = [f"  [{v['severity']}] {v['kind']}: {v['message']}"
                 for v in violations]
        for v in violations:
            for d in (v.get("details") or [])[:3]:
                lines.append(f"      - {d}")
        raise InvariantViolation(
            f"{len(violations)} pipeline invariant(s) violated:\n"
            + "\n".join(lines)
        )


def compute_data_health(txns: list[dict],
                        holdings_by_account: list[dict],
                        history: list[dict],
                        analytics: dict,
                        cache_dir: Path,
                        *,
                        cash_summary: dict | None = None,
                        parse_report: list[dict] | None = None,
                        transit_issues: list[dict] | None = None) -> list[dict]:
    """Run all data-health checks and return a list sorted by severity.

    ``parse_report`` carries per-file parse findings that cannot be
    reconstructed from ``txns`` — a file that parsed to zero leaves no
    transactions behind to notice its absence.  Defaults to the last
    ``parse_all_files`` run, and can be passed explicitly for tests and
    for any caller that parses out of band.
    """
    if parse_report is None:
        from ..parsers import parse_report as _last_parse_report
        parse_report = _last_parse_report()

    issues: list[dict] = []
    issues.extend(_check_parser_dropped_rows(parse_report))
    issues.extend(_check_future_dated(txns))
    issues.extend(_check_unpriced_account_transfers(txns))
    issues.extend(_check_transfer_share_units(txns))
    issues.extend(_check_unpriced_transfer_transit(history, transit_issues))
    issues.extend(_check_negative_cost_basis(holdings_by_account))
    issues.extend(_check_value_qty_price_consistency(holdings_by_account))
    issues.extend(_check_orphan_zero_qty_basis(holdings_by_account))
    issues.extend(_check_snapshot_rollup(history))
    issues.extend(_check_snapshot_date_monotonicity(history))
    issues.extend(_check_realized_gain_reconciliation(txns, analytics))
    issues.extend(_check_cash_flow_conservation(txns, cash_summary))
    issues.extend(_check_action_catalog_coverage(txns))
    issues.extend(_check_holding_days_non_negative(txns))
    issues.extend(_check_per_position_basis_sanity(holdings_by_account))
    issues.extend(_check_lots_holdings_basis_parity(analytics, holdings_by_account))
    issues.extend(_check_open_options_past_expiration(analytics))
    issues.extend(_check_held_symbol_price_health(holdings_by_account, cache_dir))
    issues.extend(_check_priced_coverage(history))
    issues.extend(_check_per_account_negative_contributions(txns, holdings_by_account))
    issues.extend(_check_lot_queue_parity(txns, holdings_by_account))
    issues.extend(_check_history_holdings_basis_parity(history, holdings_by_account))
    issues.extend(_check_net_contributed_monotonicity(history))
    issues.extend(_check_twr_sanity_bounds(analytics))
    issues.extend(_check_unbridged_retirement_distribution(txns, analytics))
    issues.extend(_check_broker_cash_balance(txns))
    issues.extend(_check_held_symbol_sector_coverage(holdings_by_account))
    issues.sort(key=lambda r: (_SEVERITY_RANK.get(r["severity"], 9),
                               r.get("category", ""), r.get("kind", "")))
    return issues
