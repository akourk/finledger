"""Coinbase-specific reconciliation and helpers.

Coinbase exports have several quirks that don't apply to other brokers
and that need post-parse reconciliation:

- Bank-funded Buys lack explicit Deposit rows (the ACH event happens
  off-Coinbase).  ``reconcile_external_funding`` synthesizes Deposit
  rows for genuine deficits using the cumulative-min approach.

- Coinbase Pro's GDAX export tags intra-Coinbase transfers identically
  to bank deposits.  ``reconcile_intra_transfers`` re-tags the Pro
  legs as Transfer In/Out when a same-date, same-amount counter-leg
  exists on the Coinbase regular side.

- Coinbase's USD wallet movements aren't reported as separate USD
  rows on the regular-side parser.  ``usd_effect`` / ``usd_series``
  expose the implicit per-row + per-date USD balance for the
  history-snapshot bridge.

All Coinbase-specific logic lives here so ``main.py`` stays a generic
orchestrator.  See CLAUDE.md "USD balance tracking" carve-out for the
full data-completeness rationale.
"""

from __future__ import annotations

from .config import ACCOUNT_TYPES


_ASSET_SWAP_RAW_ACTIONS = frozenset({
    "Convert In", "Convert Out",
    "Wrap Asset In", "Wrap Asset Out",
    "Unwrap In", "Unwrap Out",
    "Convert Out Neutral",
    "Retail Eth2 Deprecation",
})


def is_pro_source(t: dict) -> bool:
    src = (t.get("source") or "").lower()
    return "coinbase-pro" in src or "gdax" in src


def usd_effect(t: dict) -> float:
    """Per-txn implicit USD wallet effect for a Coinbase row.

    Single source of truth used by both the external-funding
    reconciliation (deficit synth) and the history-snapshot bridge
    (USD positions during sell→buy gaps).  Returns 0 for non-Coinbase
    txns.
    """
    if t.get("account_group") != "Coinbase":
        return 0.0
    action = t.get("action", "")
    raw_action = t.get("raw_action", "") or action
    sym = t.get("symbol", "") or ""
    amt = float(t.get("amount", 0) or 0)
    desc = (t.get("description") or "").lower()

    if sym == "USD":
        if action in ("Trade Settle In", "Deposit"):
            return +amt
        if action in ("Trade Settle Out", "Withdrawal"):
            return -amt
        if action == "Fee":
            return -amt   # Pro USD trading fees
        return 0.0   # Transfer In/Out USD: intra-Coinbase, neutral

    # Crypto txns — implicit USD movement on Coinbase regular ONLY
    # for actual Buy/Sell trades.
    if is_pro_source(t):
        return 0.0   # Pro: USD leg handled separately
    if raw_action in _ASSET_SWAP_RAW_ACTIONS:
        return 0.0   # Wrap / Convert: no USD changes hands
    if action == "Buy":
        if "using usd wallet" in desc:
            return -amt   # spending intra-Coinbase USD
        if "using" in desc and "bought" in desc:
            return 0.0    # bank-funded → already external in
        return -amt
    if action == "Sell":
        return +amt
    return 0.0


def usd_series(txns: list[dict]) -> list[tuple[str, float]]:
    """Implicit Coinbase USD-wallet running balance over time.

    Returns ``[(date, running_usd_balance_at_eod), ...]`` sorted by
    date.  Multiple events on the same date collapse to the latest
    end-of-day balance.

    After the GDAX-deprecation marker fix, the final running balance
    ≈ $0 — matching the user's actual Coinbase USD wallet.  Snapshot
    consumers add this to ``by_account_group['Coinbase']`` to bridge
    the gap between Sells and the next Buy / Withdrawal.
    """
    chrono: list[tuple[str, str, int, float]] = []
    for t in txns:
        if t.get("account_group") != "Coinbase":
            continue
        eff = usd_effect(t)
        chrono.append((t.get("date", ""), t.get("source", ""), id(t), eff))
    # Inflows before outflows within a date — same ordering rule as
    # reconcile_external_funding.
    chrono.sort(key=lambda x: (x[0], 0 if x[3] > 0 else (1 if x[3] < 0 else 2),
                                x[1], x[2]))

    eod: dict[str, float] = {}
    running = 0.0
    for date, _src, _id, eff in chrono:
        running += eff
        if date:
            eod[date] = running
    return sorted(eod.items())


def reconcile_external_funding(txns: list[dict]) -> list[dict]:
    """Synthesize Deposit USD rows for implicit bank funding on Coinbase.

    Coinbase regular's CSV doesn't emit a Deposit row when you Buy
    crypto with linked-bank funding — the bank ACH event happens
    off-Coinbase.  Older exports (pre-2018) also lack the
    ``"using bank account ..."`` Notes marker that newer rows carry.
    Without these, every bank-funded Buy silently undercounts
    ``net_contributed``: the user paid real money from their bank,
    but only the Buy (an asset swap) is recorded.

    Walks Coinbase txns chronologically tracking the implicit USD
    balance.  Synthesizes a Deposit ONLY when running hits a NEW
    cumulative low — the deepest point of net cash needed from
    external sources.  Transient dips covered by later Sells / Trade
    Settle Ins are NOT synthesized (recycled cash, not new external
    funding).

    Pinned by ``tests/test_coinbase_funding_heuristic.py``.

    Must run AFTER action normalization + intra-Coinbase reconcile,
    because we match on the canonical action names produced by those
    earlier steps.
    """
    chrono = []
    for t in txns:
        if t.get("account_group") != "Coinbase":
            continue
        effect = usd_effect(t)
        chrono.append((t.get("date", ""), t.get("source", ""), id(t), t, effect))

    # Date ASC, inflows-before-outflows within a date, then source/id
    # for deterministic tie-breaking.  Required because brokers often
    # list the outflow before the offsetting inflows on the same day.
    chrono.sort(key=lambda x: (x[0], 0 if x[4] > 0 else (1 if x[4] < 0 else 2),
                                x[1], x[2]))

    out_extra: list[dict] = []
    running_usd = 0.0
    cumulative_min = 0.0
    for date, _src, _id, t, effect in chrono:
        running_usd += effect
        if running_usd < cumulative_min - 0.01:
            new_deficit = round(cumulative_min - running_usd, 2)
            cumulative_min = running_usd
            out_extra.append({
                "date": date,
                "account": "Coinbase",
                "account_group": "Coinbase",
                "account_type": ACCOUNT_TYPES.get("Coinbase", "Taxable"),
                "symbol": "USD",
                "action": "Deposit",
                "raw_action": "Synthesized Deposit (implicit bank funding)",
                "quantity": new_deficit,
                "price": 1.0,
                "fees": 0.0,
                "amount": new_deficit,
                "description": (f"Auto-synthesized: implicit bank funding "
                                f"for {t.get('action','')} "
                                f"{t.get('symbol','')[:30]}"),
                "source": "auto-reconcile-coinbase-funding",
            })

    if out_extra:
        total = sum(r["amount"] for r in out_extra)
        print(f"  Synthesized {len(out_extra)} Coinbase Deposit row(s) "
              f"for ${total:,.2f} of implicit bank funding "
              f"(cumulative min: ${cumulative_min:,.2f})")
    return txns + out_extra


def reconcile_intra_transfers(txns: list[dict]) -> list[dict]:
    """Re-tag Coinbase regular ↔ Coinbase Pro intra-wallet transfers.

    Both wallets share account_group "Coinbase".  When the user moves
    money between them, it shows up as a paired Transfer Out (regular
    side) and Deposit / Withdrawal (Pro side) — but the Pro-side rows
    look identical to a real external bank deposit, so they end up
    inflating ``net_contributed``, the SPY benchmark series, and FIRE
    figures with phantom external money.

    Pairs Pro non-match Deposits / Withdrawals with same-date,
    same-amount regular-side Transfer Out / In rows, and re-tags the
    Pro side as Transfer In / Out.  Both legs land on
    ``account_group=Coinbase`` so the basis walker treats the pair as
    an intra-group no-op (no balance / no basis movement).

    Real bank → Pro deposits with no matching regular-side counterpart
    are left alone (still tagged Deposit), preserving accuracy for
    users who fund Pro directly.
    """
    pro_dep_idxs: dict[tuple[str, float], list[int]] = {}
    pro_wd_idxs:  dict[tuple[str, float], list[int]] = {}
    for i, t in enumerate(txns):
        if t.get("account_group") != "Coinbase":
            continue
        if not is_pro_source(t):
            continue
        action = t.get("action", "")
        date = t.get("date", "")
        amount = round(float(t.get("amount", 0) or 0), 2)
        if not date or amount <= 0:
            continue
        key = (date, amount)
        if action == "Deposit":
            pro_dep_idxs.setdefault(key, []).append(i)
        elif action == "Withdrawal":
            pro_wd_idxs.setdefault(key, []).append(i)

    if not pro_dep_idxs and not pro_wd_idxs:
        return txns

    used_reg: set[int] = set()
    for i, t in enumerate(txns):
        if t.get("account_group") != "Coinbase":
            continue
        if is_pro_source(t):
            continue   # only match against the regular side
        date = t.get("date", "")
        amount = round(float(t.get("amount", 0) or 0), 2)
        action = t.get("action", "")
        if not date or amount <= 0:
            continue
        key = (date, amount)
        if action == "Transfer Out" and pro_dep_idxs.get(key):
            pro_idx = pro_dep_idxs[key].pop(0)
            txns[pro_idx]["action"] = "Transfer In"
            txns[pro_idx]["description"] = (
                txns[pro_idx].get("description", "")
                + " [auto-reconciled: paired with Coinbase regular Transfer Out]"
            ).strip()
            used_reg.add(i)
        elif action == "Transfer In" and pro_wd_idxs.get(key):
            pro_idx = pro_wd_idxs[key].pop(0)
            txns[pro_idx]["action"] = "Transfer Out"
            txns[pro_idx]["description"] = (
                txns[pro_idx].get("description", "")
                + " [auto-reconciled: paired with Coinbase regular Transfer In]"
            ).strip()
            used_reg.add(i)

    return txns
