"""Cost-basis and realized / unrealized gain tracking.

Supports four lot-selection methods: FIFO, LIFO, HIFO, and weighted
average cost.  FIFO is the default and the only one annotated back onto
the transaction list; the others are walked independently to produce
per-method summaries for the dashboard's lot-method comparison section.

Per-transaction annotations (FIFO only, mutated in place):
    cost_basis       — dollars added (buys) or removed (sells).  None for
                       transactions with no basis impact (Deposits,
                       Dividends, etc.).
    realized_gain    — proceeds minus cost_basis_removed (sells only).
    basis_effect     — add / remove / transfer_out / transfer_in /
                       transfer_in_unpaired / split / zero_basis /
                       ignore / unknown.

Per-holding annotations (merged by main.py):
    cost_basis       — sum of remaining lots' basis for that
                       (account_group, symbol).
    unrealized_gain  — current_value - cost_basis (None when price unknown).

Key invariants
--------------
- A same-day Transfer Out is processed before its paired Transfer In
  under basis.py's own sort, regardless of main.py's sort.  This lets the
  transfer pool have content to hand off to the inbound leg.
- Same-(account_group, symbol) transfers (Voya 401K → Schwab Rollover IRA,
  Coinbase ↔ Coinbase Pro) don't need pairing — the FIFO queue is keyed
  on account_group, so basis sails through.  Cross-group transfers
  (USAA Roth → Schwab Roth) DO need pairing.  The USAA reconciler
  synthesizes same-date same-qty legs so those pair trivially.
- Dollars-for-basis: `amount` when >0 (includes fees), else `qty × price`.
  Matches conventional tax-lot basis.
"""

from collections import defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Action → basis effect classification
# ---------------------------------------------------------------------------
# The classification table lives in src/actions.py — single source of
# truth for every action's (balance, basis, cash_flow) effects.  This
# module re-exports BASIS_EFFECTS for backward compatibility with
# callers that imported it from here.

from .actions import (
    BASIS_EFFECTS, CASH_ADD_ACTIONS, CASH_SUB_ACTIONS, INCOME_ACTIONS,
)

# Income actions used by compute_cash_summary's "income" rollup.
# Derived from the action catalog's `income` field (single source of
# truth) — see src/actions.py.
_INCOME_ACTIONS       = INCOME_ACTIONS

# Account groups whose Distribution rows are typically custodial
# rollovers (paired with a Transfer In on the destination side) rather
# than real external withdrawals.  Used by ``txn_external_cash_flow``
# below — keeps net_contributed / SPY benchmark / FIRE math from
# treating intra-IRA fund swaps as money out of the user's pocket.
_ROLLOVER_GROUPS = frozenset({"Roth IRA", "Rollover IRA"})


def txn_external_cash_flow(t: dict) -> float:
    """Signed external cash-flow amount for a single transaction.

    Single source of truth for "did this txn move money in or out of the
    user's pocket".  Returns +amount for inflows (Deposits,
    Contributions, USAA-marked contribution Buys), -amount for outflows
    (Withdrawals, Distributions, Contribution Reversals), and 0 for
    everything else (Buys, Sells, Transfer In/Out, Trade Settle,
    Reinvest, Dividend, etc. — those are intra-account or income, not
    external).

    Carve-outs:

    1. ``Distribution`` rows on Roth/Rollover IRA accounts are treated
       as 0 because they're virtually always custodial rollovers
       (matched by a Transfer In on the destination side) or
       intra-account fund consolidations (proceeds re-purchased within
       the same account).  Real Roth withdrawals before age 59½ would
       also fall into this bucket; the heuristic prefers under-counting
       reality over over-counting non-events.

    2. USAA-style ``Buy`` rows on Roth IRA whose description carries
       ``PRIOR YEAR CONTRIBUTION`` or ``CURRENT YEAR CONTRIBUTION``
       markers count as +amount.  USAA Victory Capital exports
       represent contributions as Buys with these descriptive
       annotations rather than a separate Deposit / Contribution
       line, so without this carve-out cash_summary would silently
       under-count Roth IRA contributions by the user's full USAA
       history.

    Used by:
    - ``basis.compute_cash_summary`` (cash_summary.net_contributed)
    - ``history._compute_net_contributed_series`` (snapshot series)
    - ``history._compute_benchmark_series`` (SPY / BND / VXUS / 60-40)
    - ``analytics._shared.net_cash_flow`` (per-period TWR cash flow)
    """
    a = t.get("action", "")
    amt = float(t.get("amount", 0) or 0)
    if amt <= 0:
        return 0.0
    if a in CASH_ADD_ACTIONS:
        return amt
    if a in CASH_SUB_ACTIONS:
        if a == "Distribution" and t.get("account_group") in _ROLLOVER_GROUPS:
            return 0.0
        return -amt
    # USAA-style contribution marker on a Buy row
    if a == "Buy" and t.get("account_group") == "Roth IRA":
        desc = (t.get("description") or "").upper()
        if "PRIOR YEAR CONTRIBUTION" in desc or "CURRENT YEAR CONTRIBUTION" in desc:
            return amt
    # Coinbase regular bank-funded Buy.  Coinbase records the funding
    # source in the Notes field.  Two formats observed:
    #   • Old (~2017-2024): "...using bank account <BANK NAME> ****<digits>"
    #   • New (2025+):      "...using <BANK NAME> ****<digits>"  (no "bank account" phrase)
    # The "USD Wallet" case is INTRA-Coinbase (cash already sitting in
    # the user's USD wallet) and must NOT count as external funding.
    # Without this carve-out, Coinbase's apparent net_contributed
    # silently misses ~$10k+ of bank-funded Buys recorded in the new
    # CSV format — discovered while tracking a Coinbase USD discrepancy
    # caused by GDAX deprecation changes.
    if a == "Buy" and t.get("account_group") == "Coinbase":
        desc = (t.get("description") or "").lower()
        if "using usd wallet" in desc:
            return 0.0   # internal wallet — not external funding
        if "using" in desc and "bought" in desc:
            # Heuristic: any "Bought ... using <X>" where X is not the
            # USD Wallet means external funding (linked bank, credit
            # card, etc.).  Catches both old and new bank-marker formats.
            return amt
    return 0.0

# Supported lot-selection methods
METHODS = ("fifo", "lifo", "hifo", "avg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basis_effect(txn: dict) -> str:
    """Classify a txn for basis-walker purposes.

    Symbol-aware: USD (cash) and empty-symbol rows are always "ignore",
    even if the action name would otherwise suggest a share movement —
    they're cash flows that belong in the cash summary, not the lot
    queue.  For non-USD symbols we look up the action directly.
    """
    sym = (txn.get("symbol", "") or "").strip()
    if not sym or sym == "USD":
        return "ignore"
    return BASIS_EFFECTS.get(txn.get("action", ""), "unknown")


def _basis_dollars(txn: dict) -> float:
    """Dollars to attribute to a lot: prefer `amount` (broker-reported,
    includes fees/spread), fall back to qty × price."""
    amount = float(txn.get("amount", 0) or 0)
    if amount > 0:
        return amount
    qty = float(txn.get("quantity", 0) or 0)
    price = float(txn.get("price", 0) or 0)
    return qty * price


def _safe_date(s: str):
    """Parse an ISO ``YYYY-MM-DD`` date, returning None on anything
    unparseable (empty string, ``VARIOUS``, malformed)."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _sort_key(t: dict) -> tuple:
    """Sort txns for basis processing — mirrors main.py's balance-walk sort.

    Within a `(date, account_group, symbol)` group: adds before subtracts,
    so same-day Buy-then-Sell consumes the just-bought lot (not an
    earlier one).  Cross-key same-day pairing (Transfer In that needs a
    paired Transfer Out) is handled by pre-pairing below — this sort
    only has to keep lot mutations within a key in the right order.
    """
    effect = _basis_effect(t)
    is_subtract = effect in ("remove", "transfer_out")
    return (
        t.get("date", ""),
        t.get("account_group", ""),
        t.get("symbol", ""),
        2 if is_subtract else 1,
    )


# ---------------------------------------------------------------------------
# Transfer pre-pairing
# ---------------------------------------------------------------------------

def _pair_transfers(txns: list[dict]) -> dict:
    """Match Transfer Out txns with Transfer In txns.

    Returns a dict with three keys:
      "tin_to_tout":  id(TIN) -> TOUT txn (the Transfer Out that supplies
                      basis for this Transfer In)
      "intra_group":  set of id(txn)s for BOTH legs of any intra-group
                      transfer (same (account_group, symbol) on both sides
                      — these are no-ops for FIFO since they stay in the
                      same lot queue; skipping avoids double-counting)
      "paired_touts": set of id(TOUT) that are paired (so we know which
                      TOUTs to skip vs. treat as orphaned)

    Matching rule: same canonical symbol, TIN date within 0–14 days after
    TOUT date, quantity within 0.1% relative tolerance.  Greedy
    assignment, earliest-TIN-first, closest-TOUT-preferred.
    """
    outs = [t for t in txns if _basis_effect(t) == "transfer_out"]
    ins  = [t for t in txns if _basis_effect(t) == "transfer_in"]
    tin_to_tout: dict[int, dict] = {}
    intra_group: set[int] = set()
    paired_touts: set[int] = set()
    used_out_ids: set[int] = set()

    def _d(iso: str):
        try:
            return datetime.strptime(iso, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    ins_sorted = sorted(ins, key=lambda t: t.get("date", ""))
    for tin in ins_sorted:
        tin_sym = tin.get("symbol", "")
        tin_qty = float(tin.get("quantity", 0) or 0)
        tin_d   = _d(tin.get("date", ""))
        if tin_d is None or tin_qty <= 0:
            continue
        best = None
        best_score = None
        for tout in outs:
            if id(tout) in used_out_ids:
                continue
            if tout.get("symbol", "") != tin_sym:
                continue
            tout_d = _d(tout.get("date", ""))
            if tout_d is None:
                continue
            delta = (tin_d - tout_d).days
            if delta < 0 or delta > 14:
                continue
            qp = float(tout.get("quantity", 0) or 0)
            if qp <= 0:
                continue
            rel = abs(tin_qty - qp) / max(qp, 1e-9)
            if rel > 0.001 and abs(tin_qty - qp) > 1e-6:
                continue
            score = (rel, delta)
            if best_score is None or score < best_score:
                best = tout
                best_score = score
        if best is not None:
            used_out_ids.add(id(best))
            paired_touts.add(id(best))
            tin_to_tout[id(tin)] = best
            # Intra-group check: same (account_group, symbol) on both
            # legs → stays in the same lot queue, so both should be
            # skipped.  (Main.py's balance simply adds then subtracts,
            # netting zero.)
            if (best.get("account_group") == tin.get("account_group")
                    and best.get("symbol") == tin.get("symbol")):
                intra_group.add(id(tin))
                intra_group.add(id(best))

    return {
        "tin_to_tout":  tin_to_tout,
        "intra_group":  intra_group,
        "paired_touts": paired_touts,
    }


# ---------------------------------------------------------------------------
# Lot consumption (per-method)
# ---------------------------------------------------------------------------

def _consume_lots(lots: list[dict], qty_to_remove: float, method: str) -> tuple[float, list[dict]]:
    """Remove `qty_to_remove` shares from `lots` using the given method.

    Mutates `lots` in place.  Returns `(basis_removed, carried)` where
    `carried` is a list of `{date, qty, basis_per_share}` describing the
    specific portions taken — used by transfer_out to ship lots to the
    destination.
    """
    if qty_to_remove <= 0 or not lots:
        return 0.0, []

    # Ordering by method: which lot index to consume first.
    # For avg this function is not called (avg uses a different path).
    if method == "fifo":
        order = list(range(len(lots)))                     # oldest first
    elif method == "lifo":
        order = list(range(len(lots)))[::-1]               # newest first
    elif method == "hifo":
        order = sorted(range(len(lots)),
                       key=lambda i: -lots[i]["basis_per_share"])
    else:
        raise ValueError(f"_consume_lots does not support method={method!r}")

    remaining = qty_to_remove
    basis_removed = 0.0
    carried: list[dict] = []
    consumed_fully: set[int] = set()

    for idx in order:
        if remaining <= 0:
            break
        lot = lots[idx]
        take = min(lot["qty"], remaining)
        if take <= 0:
            continue
        basis_removed += take * lot["basis_per_share"]
        carried.append({
            "date": lot["date"],
            "qty": take,
            "basis_per_share": lot["basis_per_share"],
        })
        lot["qty"] -= take
        remaining -= take
        if lot["qty"] <= 1e-12:
            consumed_fully.add(idx)

    # Remove emptied lots (iterate in reverse to preserve remaining indices)
    for idx in sorted(consumed_fully, reverse=True):
        lots.pop(idx)

    return basis_removed, carried


# ---------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------

def _empty_state(method: str):
    """State container for a walk: lot queues + per-method running totals."""
    if method == "avg":
        # (total_qty, total_basis)
        lots: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    else:
        lots = defaultdict(list)
    return {
        "lots": lots,
        "realized_total": 0.0,
        "transfer_pool": [],  # list of {date, symbol, lots, qty_total}
    }


def _add_to_avg(state, key, qty, basis_dollars):
    t = state["lots"][key]
    t[0] += qty
    t[1] += basis_dollars


def _remove_from_avg(state, key, qty_to_remove):
    t = state["lots"][key]
    total_qty, total_basis = t
    if total_qty <= 0 or qty_to_remove <= 0:
        return 0.0
    take = min(total_qty, qty_to_remove)
    if take >= total_qty - 1e-12:
        basis_removed = total_basis
        t[0] = 0.0
        t[1] = 0.0
    else:
        per_share = total_basis / total_qty
        basis_removed = take * per_share
        t[0] = total_qty - take
        t[1] = total_basis - basis_removed
    return basis_removed


def _apply_split_to_lots(lots: list[dict], old_total_qty: float, added_qty: float):
    """Scale lot quantities by the split ratio; preserve total basis."""
    if old_total_qty <= 0 or added_qty <= 0:
        return
    ratio = (old_total_qty + added_qty) / old_total_qty
    for lot in lots:
        lot["qty"] *= ratio
        lot["basis_per_share"] /= ratio


def _consume_from_key(state: dict, method: str, key: tuple, qty: float) -> tuple[float, list[dict]]:
    """Dispatch lot-consumption to avg or FIFO-family helper.

    Returns `(basis_removed, carried_lots)` — `carried_lots` is useful
    when we need to re-push the same lots into a transfer destination.
    """
    if qty <= 0:
        return 0.0, []
    if method == "avg":
        t_state = state["lots"][key]
        total_qty, total_basis = t_state
        take = min(total_qty, qty)
        if take <= 0 or total_qty <= 0:
            return 0.0, []
        per_share = total_basis / total_qty
        basis_removed = take * per_share
        t_state[0] = total_qty - take
        t_state[1] = total_basis - basis_removed
        return basis_removed, [{"date": "", "qty": take, "basis_per_share": per_share}]
    return _consume_lots(state["lots"][key], qty, method)


def _push_lot(state: dict, method: str, key: tuple, qty: float, basis_dollars: float, date: str) -> None:
    if qty <= 0:
        return
    if method == "avg":
        _add_to_avg(state, key, qty, basis_dollars)
    else:
        state["lots"][key].append({
            "date": date,
            "qty": qty,
            "basis_per_share": basis_dollars / qty,
        })


def _push_carried_lots(state: dict, method: str, key: tuple, carried: list[dict]) -> float:
    total = 0.0
    for lot in carried:
        basis = lot["qty"] * lot["basis_per_share"]
        total += basis
        if method == "avg":
            _add_to_avg(state, key, lot["qty"], basis)
        else:
            state["lots"][key].append({
                "date": lot.get("date", ""),
                "qty":  lot["qty"],
                "basis_per_share": lot["basis_per_share"],
            })
    return total


def _walk(txns: list[dict], method: str, *, annotate: bool,
          account_methods: dict[str, str] | None = None) -> dict:
    """Walk `txns` applying basis rules under `method`.

    If `annotate` is True, mutates each txn with `cost_basis`,
    `realized_gain`, and `basis_effect`.  Returns the final state dict
    (lot queues, realized total).

    `account_methods` optionally overrides the lot-relief method per
    `account_group` (e.g. ``{"Coinbase": "hifo"}`` to match a broker
    that defaults to HIFO).  Only the *consume order* varies per account;
    the lot-queue structure stays uniform, so overrides are restricted to
    the lot-list methods (fifo / lifo / hifo) — an ``avg`` override falls
    back to the base ``method``.  Balances are unaffected (same total qty
    consumed), only which lots' basis is realized.
    """
    state = _empty_state(method)

    def _method_for(acct: str) -> str:
        if not account_methods:
            return method
        m = account_methods.get(acct, method)
        return m if m in ("fifo", "lifo", "hifo") else method

    # Pre-pair transfers across accounts.  Each Transfer In either has a
    # matching Transfer Out (cross-group — basis carries), belongs to an
    # intra-group pair (no-op in the lot queue since both legs share the
    # same (account_group, symbol) key), or is unpaired (zero basis).
    pairings = _pair_transfers(txns)
    tin_to_tout:  dict[int, dict] = pairings["tin_to_tout"]
    intra_group:  set[int]        = pairings["intra_group"]
    paired_touts: set[int]        = pairings["paired_touts"]

    # Lots stashed by a processed Transfer Out, awaiting pickup by its
    # paired Transfer In.
    stashed_tout_lots: dict[int, list[dict]] = {}
    # Transfer Out ids whose basis was already moved eagerly by a same-
    # day Transfer In that walked first — skip them when we reach them.
    tout_handled: set[int] = set()

    txns_sorted = sorted(txns, key=_sort_key)

    for t in txns_sorted:
        effect = _basis_effect(t)
        sym    = t.get("symbol", "") or ""
        acct   = t.get("account_group", "") or ""
        qty    = float(t.get("quantity", 0) or 0)
        key    = (acct, sym)
        cost_basis_value: float | None = None
        realized: float | None = None
        final_effect = effect

        # Intra-group paired transfers net to zero in the same lot queue.
        # Main.py's balance: +qty then -qty → 0 change.  Basis: same lots
        # stay in place.  Skip both legs.
        if id(t) in intra_group:
            final_effect = "intra_group_noop"
            if annotate:
                t["basis_effect"] = final_effect
            continue

        if effect == "add":
            basis = _basis_dollars(t)
            _push_lot(state, method, key, qty, basis, t.get("date", ""))
            if qty > 0:
                cost_basis_value = basis

        elif effect == "zero_basis":
            # Rewards / spinoffs / mergers: FMV-at-receipt if price known,
            # else zero.
            price = float(t.get("price", 0) or 0)
            basis = qty * price if price > 0 else 0.0
            _push_lot(state, method, key, qty, basis, t.get("date", ""))
            if qty > 0:
                cost_basis_value = basis

        elif effect == "remove":
            proceeds = _basis_dollars(t)
            basis_removed, carried = _consume_from_key(state, _method_for(acct), key, qty)
            cost_basis_value = basis_removed
            realized = proceeds - basis_removed
            # Per-lot breakdown of the consumed lots.  Each lot keeps its
            # own acquired date, basis, and proceeds (apportioned by
            # share) and days-held, so the Tax tab can classify realized
            # gain short- vs long-term *lot by lot* — a single sell often
            # straddles the 1-year line, and collapsing it to one
            # weighted-average holding period mis-buckets the whole gain
            # (and fabricates an acquired date that matches no real lot).
            # ``holding_days`` (the weighted average) is still emitted for
            # the transaction-detail display.  FIFO/annotate only.
            if annotate:
                close_d = _safe_date(t.get("date", ""))
                consumed_q = sum(lot["qty"] for lot in carried)
                breakdown: list[dict] = []
                weighted_days = 0.0
                dated_q = 0.0
                for lot in carried:
                    lot_d = _safe_date(lot.get("date", ""))
                    days = (close_d - lot_d).days if (close_d and lot_d) else None
                    if days is not None:
                        weighted_days += days * lot["qty"]
                        dated_q += lot["qty"]
                    breakdown.append({
                        "date_acquired": lot.get("date", "") or "VARIOUS",
                        "qty": lot["qty"],
                        "cost_basis": lot["qty"] * lot["basis_per_share"],
                        "proceeds": (proceeds * lot["qty"] / consumed_q)
                                    if consumed_q > 0 else 0.0,
                        "days": days,
                    })
                if breakdown:
                    t["lot_breakdown"] = breakdown
                if dated_q > 0:
                    t["holding_days"] = round(weighted_days / dated_q, 1)

        elif effect == "transfer_out":
            if id(t) in tout_handled:
                # Already moved eagerly by the paired TIN — skip.
                final_effect = "transfer_out_eager_consumed"
            else:
                basis_removed, carried = _consume_from_key(state, _method_for(acct), key, qty)
                if id(t) in paired_touts:
                    stashed_tout_lots[id(t)] = carried
                # Unpaired: lots are orphaned (transferred to some account
                # not in the dataset).  Basis is lost.
                if carried:
                    cost_basis_value = basis_removed

        elif effect == "transfer_in":
            paired = tin_to_tout.get(id(t))
            if paired is None:
                final_effect = "transfer_in_unpaired"
                _push_lot(state, method, key, qty, 0.0, t.get("date", ""))
                cost_basis_value = 0.0
            elif id(paired) in stashed_tout_lots:
                carried = stashed_tout_lots.pop(id(paired))
                cost_basis_value = _push_carried_lots(state, method, key, carried)
            else:
                # Paired TOUT hasn't walked yet (same-day, alphabetically
                # later account_group).  Eager-consume from source now
                # and mark the TOUT to noop when its turn comes.
                src_key = (paired.get("account_group", ""), paired.get("symbol", ""))
                qty_tout = float(paired.get("quantity", 0) or 0)
                _bsum, carried = _consume_from_key(
                    state, _method_for(src_key[0]), src_key, qty_tout)
                cost_basis_value = _push_carried_lots(state, method, key, carried)
                tout_handled.add(id(paired))

        elif effect == "split":
            if method == "avg":
                state["lots"][key][0] += qty
            else:
                lots = state["lots"][key]
                old_total = sum(lot["qty"] for lot in lots)
                _apply_split_to_lots(lots, old_total, qty)

        # effect == "ignore" or "unknown": leave untouched

        if realized is not None:
            state["realized_total"] += realized

        if annotate:
            if cost_basis_value is not None:
                t["cost_basis"] = round(cost_basis_value, 2)
            if realized is not None:
                t["realized_gain"] = round(realized, 2)
            t["basis_effect"] = final_effect

    state["orphan_stashed_touts"] = len(stashed_tout_lots)
    return state


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def compute_basis_default(txns: list[dict],
                          account_methods: dict[str, str] | None = None) -> dict:
    """Run the annotated walker (FIFO by default), annotating txns in
    place.  Returns the final lot state for merging into holdings.

    `account_methods` overrides the lot-relief method per `account_group`
    (e.g. ``{"Coinbase": "hifo"}``) so realized gains / cost basis match
    what the broker actually used.  Affects which lots' basis is realized
    (and thus realized_gain, holding period, MAGI), never balances.  The
    Lot-Method Comparison table (compute_basis_all_methods) still reports
    pure single-method totals for what-if comparison.
    """
    return _walk(txns, "fifo", annotate=True, account_methods=account_methods)


def compute_basis_all_methods(txns: list[dict]) -> dict[str, dict]:
    """Run each supported method (without re-annotating txns) and return
    `{method: state}` dicts.  Used for the dashboard's lot-method
    comparison section.
    """
    out: dict[str, dict] = {}
    for m in METHODS:
        out[m] = _walk(txns, m, annotate=False)
    return out


def state_to_holdings(state: dict, method: str) -> list[dict]:
    """Collapse final lot state into `[{account_group, symbol, quantity,
    cost_basis}]`.  Used for both default (FIFO) annotation of holdings
    and per-method comparison rows.

    Applies a 1e-6 dust threshold so CSV-precision residuals
    (e.g. 1e-8 share of CBETH left over from a wrap/unwrap pair) don't
    show up as bogus near-zero positions.  Matches main.py's dust filter
    for the case where price is unknown.
    """
    _DUST = 1e-6
    rows: list[dict] = []
    if method == "avg":
        for (acct, sym), (qty, basis) in state["lots"].items():
            if abs(qty) < _DUST:
                continue
            rows.append({
                "account_group": acct,
                "symbol":        sym,
                "quantity":      round(qty, 8),
                "cost_basis":    round(basis, 2),
            })
    else:
        for (acct, sym), lots in state["lots"].items():
            qty   = sum(lot["qty"] for lot in lots)
            basis = sum(lot["qty"] * lot["basis_per_share"] for lot in lots)
            if abs(qty) < _DUST:
                continue
            rows.append({
                "account_group": acct,
                "symbol":        sym,
                "quantity":      round(qty, 8),
                "cost_basis":    round(basis, 2),
            })
    return rows


def derive_basis_by_key_from_txns(txns: list[dict]) -> dict[tuple[str, str], float]:
    """Reconstruct ``{(account_group, symbol): running_cost_basis}``
    from per-txn ``basis_effect`` + ``cost_basis`` annotations.

    Used in two places (so it MUST stay in sync with the basis walker):

    1. ``main._refresh_prices_only`` — to populate ``fifo_basis_by_key``
       from a previously-exported JSON without re-walking every lot.
    2. ``analytics.data_health._check_lot_queue_parity`` — to assert
       the txn-level annotations are internally consistent with the
       holdings table.

    The basis walker writes one of these effect values onto every
    txn it processes:

    - ``add``                  → +cost_basis  (Buy, Reinvest, Convert In, Contribution)
    - ``remove``               → -cost_basis  (Sell, Convert Out, Option Sell, Withdrawal)
    - ``zero_basis``           → +cost_basis  (cb is 0; Reward, Spinoff)
    - ``transfer_in``          → +cost_basis  (paired cross-group, basis carries from source)
    - ``transfer_in_unpaired`` → +cost_basis  (cb is 0; arrival from external wallet)
    - ``transfer_out``         → -cost_basis  (paired cross-group OR unpaired send to external)
    - ``intra_group_noop``     → 0            (paired same-group transfer; both legs cancel)
    - ``split``                → 0            (qty rebalanced, total basis unchanged)
    - ``ignore``               → 0            (USD or empty symbol; not in lot queue)

    A bug pinned by ``test_lot_queue_parity_check_passes_on_clean_data``:
    treating ``transfer_out`` as a no-op leaves Coinbase BTC-USD's basis
    inflated by every external-wallet send, since the basis walker
    correctly drops those lots but the txn-level reconstruction would
    keep them.
    """
    by_key: dict[tuple[str, str], float] = {}
    for t in txns:
        sym = t.get("symbol", "") or ""
        if not sym or sym == "USD":
            continue
        be = t.get("basis_effect")
        cb = t.get("cost_basis")
        if be is None or cb is None:
            continue
        key = (t.get("account_group", ""), sym)
        if be in ("add", "zero_basis", "transfer_in", "transfer_in_unpaired"):
            by_key[key] = by_key.get(key, 0.0) + float(cb)
        elif be in ("remove", "transfer_out"):
            by_key[key] = by_key.get(key, 0.0) - float(cb)
        # intra_group_noop / split / ignore: no contribution
    return by_key


def compute_cash_summary(txns: list[dict]) -> dict:
    """Summarise cash-flow events for portfolio-level performance stats.

    Uses ``txn_external_cash_flow`` — single source of truth that handles
    the full action vocabulary (incl. Contribution Reversal) and the
    Roth/Rollover IRA rollover carve-out consistently with history.py
    and analytics._shared.net_cash_flow.
    """
    contributions = 0.0
    withdrawals   = 0.0
    income        = 0.0
    for t in txns:
        flow = txn_external_cash_flow(t)
        if flow > 0:
            contributions += flow
        elif flow < 0:
            withdrawals += -flow
        elif t.get("action", "") in _INCOME_ACTIONS:
            income += float(t.get("amount", 0) or 0)
    return {
        "contributions":    round(contributions, 2),
        "withdrawals":      round(withdrawals, 2),
        "net_contributed":  round(contributions - withdrawals, 2),
        "income":           round(income, 2),
    }
