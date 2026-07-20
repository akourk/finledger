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


def _pair_wraps(txns: list[dict]) -> dict:
    """Group wrap / unwrap legs into basis-carrying conversions.

    Wrapping (e.g. ETH→CBETH) and unwrapping move the same underlying
    asset between two symbols WITHOUT a taxable disposal — basis carries.
    Each ``(account_group, date, kind)`` group bundles its ``wrap_out``
    legs (the source symbol consumed) and ``wrap_in`` legs (the
    destination symbol received).  ``kind`` is ``wrap`` (Wrap Asset*) or
    ``unwrap`` (Unwrap*) so a same-day wrap and unwrap on the same account
    don't get cross-wired.  The walker processes each group atomically the
    first time it meets any leg, so same-day leg ordering is irrelevant.

    Returns ``{group_key: {"out": [...], "in": [...], "src": sym,
    "dst": sym, "q_out": float, "q_in": float}}``.
    """
    groups: dict[tuple, dict] = {}
    for t in txns:
        effect = _basis_effect(t)
        if effect not in ("wrap_out", "wrap_in"):
            continue
        action = t.get("action", "") or ""
        kind = "unwrap" if "Unwrap" in action else "wrap"
        key = (t.get("account_group", "") or "", t.get("date", "") or "", kind)
        g = groups.setdefault(key, {"out": [], "in": [], "src": "", "dst": "",
                                    "q_out": 0.0, "q_in": 0.0})
        qty = float(t.get("quantity", 0) or 0)
        if effect == "wrap_out":
            g["out"].append(t); g["q_out"] += qty
            g["src"] = t.get("symbol", "") or ""
        else:
            g["in"].append(t); g["q_in"] += qty
            g["dst"] = t.get("symbol", "") or ""
    return groups


def _rescale_lots(carried: list[dict], target_qty: float) -> list[dict]:
    """Rebase a carried lot list onto `target_qty` units, preserving total
    basis and each lot's acquired date.  Used to move basis across a
    wrap's quantity change (e.g. 10 ETH → 9.39 CBETH)."""
    src_qty = sum(l["qty"] for l in carried)
    if src_qty <= 0 or target_qty <= 0:
        return []
    ratio = target_qty / src_qty
    return [{
        "date": l["date"],
        "qty": l["qty"] * ratio,
        "basis_per_share": l["basis_per_share"] / ratio,
        "origin": l.get("origin", "reconstructed"),
    } for l in carried]


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
            "origin": lot.get("origin", "reconstructed"),
        })
        lot["qty"] -= take
        remaining -= take
        if lot["qty"] <= 1e-12:
            consumed_fully.add(idx)

    # Remove emptied lots (iterate in reverse to preserve remaining indices)
    for idx in sorted(consumed_fully, reverse=True):
        lots.pop(idx)

    return basis_removed, carried


def _free_after_reserve(lots: list[dict],
                        reserved: dict[str, float]) -> dict[str, float]:
    """Per-acquired-date surplus above the reserved budget — what an
    undirected consume may take without touching lots the broker's
    report disposes later."""
    pool: dict[str, float] = {}
    for l in lots:
        d = (l.get("date") or "")[:10]
        pool[d] = pool.get(d, 0.0) + l["qty"]
    return {d: q - min(q, float(reserved.get(d, 0) or 0))
            for d, q in pool.items()}


def _consume_lots_capped(lots: list[dict], qty_to_remove: float, method: str,
                         free: dict[str, float]) -> tuple[float, list[dict]]:
    """``_consume_lots`` with a per-acquired-date consumption cap.

    ``free`` (mutated as units are taken) is the per-date surplus from
    ``_free_after_reserve``; lots on dates with no surplus are skipped.
    May consume less than asked — the caller decides whether to dip
    into reserved lots for the shortfall."""
    if qty_to_remove <= 0 or not lots:
        return 0.0, []
    if method == "fifo":
        order = list(range(len(lots)))
    elif method == "lifo":
        order = list(range(len(lots)))[::-1]
    elif method == "hifo":
        order = sorted(range(len(lots)),
                       key=lambda i: -lots[i]["basis_per_share"])
    else:
        raise ValueError(f"_consume_lots_capped does not support method={method!r}")

    remaining = qty_to_remove
    basis_removed = 0.0
    carried: list[dict] = []
    consumed_fully: set[int] = set()
    for idx in order:
        if remaining <= 1e-12:
            break
        lot = lots[idx]
        d = (lot.get("date") or "")[:10]
        take = min(lot["qty"], remaining, free.get(d, 0.0))
        if take <= 1e-12:
            continue
        basis_removed += take * lot["basis_per_share"]
        carried.append({
            "date": lot["date"],
            "qty": take,
            "basis_per_share": lot["basis_per_share"],
            "origin": lot.get("origin", "reconstructed"),
        })
        lot["qty"] -= take
        remaining -= take
        free[d] = free.get(d, 0.0) - take
        if lot["qty"] <= 1e-12:
            consumed_fully.add(idx)
    for idx in sorted(consumed_fully, reverse=True):
        lots.pop(idx)
    return basis_removed, carried


def _consume_lots_reserving(lots: list[dict], qty_to_remove: float,
                            method: str, reserved: dict[str, float] | None
                            ) -> tuple[float, list[dict]]:
    """Consume in ``method`` order, but prefer lots the broker's report
    does NOT dispose later (see ``broker_lots.reserved_future_demand``);
    reserved lots are touched only for the shortfall."""
    if qty_to_remove <= 0 or not lots:
        return 0.0, []
    if not reserved:
        return _consume_lots(lots, qty_to_remove, method)
    free = _free_after_reserve(lots, reserved)
    basis_removed, carried = _consume_lots_capped(
        lots, qty_to_remove, method, free)
    got = sum(c["qty"] for c in carried)
    if qty_to_remove - got > 1e-12:
        b2, c2 = _consume_lots(lots, qty_to_remove - got, method)
        basis_removed += b2
        carried.extend(c2)
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


def _consume_from_key(state: dict, method: str, key: tuple, qty: float,
                      reserved: dict[str, float] | None = None
                      ) -> tuple[float, list[dict]]:
    """Dispatch lot-consumption to avg or FIFO-family helper.

    Returns `(basis_removed, carried_lots)` — `carried_lots` is useful
    when we need to re-push the same lots into a transfer destination.
    ``reserved`` (broker-report future demand) steers the lot-list
    methods away from lots a later report disposal names; avg has no
    discrete lots so it ignores the hint.
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
    return _consume_lots_reserving(state["lots"][key], qty, method, reserved)


def _consume_lots_directed(lots: list[dict], qty_to_remove: float,
                           method: str, hints: list[dict] | None,
                           reserved: dict[str, float] | None = None
                           ) -> tuple[float, list[dict]]:
    """Consume ``qty_to_remove`` following broker-report lot hints.

    For each hint (a lot the broker's gain/loss report says this
    disposal consumed), find the matching pool lot — exact acquired
    date, then acquired date within ±2 days, then per-unit basis within
    2% (wraps rescale per-unit by the conversion ratio, so date is the
    primary key; per-unit is the fallback for date drift).  Any
    remainder (hints exhausted or unmatched) falls back to the normal
    ``method`` order.

    This is purely a consume-ORDER strategy: the basis booked is always
    the actual pool-lot basis, so balance↔basis parity and the
    annotation reconstruction hold exactly as they do for FIFO/HIFO.
    Hints' ``qty_left`` is decremented in place — same-day disposals
    share one hint list and consume it progressively.
    """
    basis_removed = 0.0
    carried: list[dict] = []
    remaining = qty_to_remove

    def _find(hint) -> int | None:
        acq = hint.get("acquired") or ""
        pu = float(hint.get("per_unit") or 0)

        def _best(idxs: list[int]) -> int | None:
            # Among several date-matching lots, prefer the one whose
            # per-unit basis is closest to the hint's — same-date pools
            # routinely hold multiple lots at different per-units (e.g.
            # the 14 ETH2-deprecation rebases all dated one day), and
            # first-match consumed the wrong lot's basis.
            if not idxs:
                return None
            if len(idxs) == 1 or pu <= 0:
                return idxs[0]
            return min(idxs, key=lambda i: abs(
                lots[i].get("basis_per_share", 0.0) - pu))

        exact = [i for i, lot in enumerate(lots)
                 if (lot.get("date") or "")[:10] == acq]
        got = _best(exact)
        if got is not None:
            return got
        if acq:
            from datetime import date as _d, timedelta
            try:
                base = _d.fromisoformat(acq)
                near = {(base + timedelta(days=dd)).isoformat()
                        for dd in (-2, -1, 1, 2)}
                got = _best([i for i, lot in enumerate(lots)
                             if (lot.get("date") or "")[:10] in near])
                if got is not None:
                    return got
            except ValueError:
                pass
        if pu > 0:
            for i, lot in enumerate(lots):
                pps = lot.get("basis_per_share", 0.0)
                if pps > 0 and abs(pps - pu) / pu <= 0.02:
                    return i
        return None

    for h in (hints or []):
        if remaining <= 1e-12:
            break
        want = min(float(h.get("qty_left", 0) or 0), remaining)
        while want > 1e-12:
            idx = _find(h)
            if idx is None:
                break
            lot = lots[idx]
            take = min(want, lot["qty"])
            basis_removed += take * lot["basis_per_share"]
            carried.append({"date": lot.get("date", ""), "qty": take,
                            "basis_per_share": lot["basis_per_share"],
                            "origin": lot.get("origin", "reconstructed")})
            lot["qty"] -= take
            if lot["qty"] <= 1e-12:
                lots.pop(idx)
            want -= take
            remaining -= take
            h["qty_left"] = float(h.get("qty_left", 0) or 0) - take

    if remaining > 1e-12:
        # The hints didn't cover the remainder — fall back to method
        # order, steering clear of lots a FUTURE report disposal names.
        b2, c2 = _consume_lots_reserving(lots, remaining, method, reserved)
        basis_removed += b2
        carried.extend(c2)
    return basis_removed, carried


def _consume_for_rebase(lots: list[dict], qty: float, method: str,
                        txn_date: str, override_total: float,
                        reserved: dict[str, float] | None = None
                        ) -> tuple[float, list[dict]]:
    """Consume ``qty`` for an intra-group transfer REBASE (a wallet move
    whose arrival the broker books at customer-provided basis).

    A move relocates SPECIFIC units, so plain method-order consumption
    picks wrong lots two ways (both observed against the Coinbase
    gain/loss report):

      - the moved units' own lot — whose per-unit matches the arriving
        customer-provided figure — is exactly the one that should leave;
      - HIFO happily ate a lot pushed the same day by ANOTHER rebase
        (the ETH2-deprecation lot), destroying a per-unit flavor the
        broker's inventory keeps and desyncing every later directed
        disposal.

    Preference tiers, each consumed in ``method`` order:
      1. lots whose per-unit ≈ the override per-unit (±0.5%);
      2. lots dated strictly before the move (a move can't relocate
         units created later that day);
      3. anything left.

    ``reserved`` (broker-report future demand) additionally splits
    tiers 2 and 3 into a free pass and a reserved pass — the rebase
    dips into lots a later report disposal names only when nothing
    else covers the quantity.  Tier 1 ignores reservation: those ARE
    the moved units, and the move re-books them (usually verbatim, via
    the wallet-move no-op) rather than destroying them.
    """
    if qty <= 0 or not lots:
        return 0.0, []
    ov_pu = override_total / qty
    def _tier(lot: dict) -> int:
        pu = float(lot.get("basis_per_share", 0) or 0)
        if ov_pu > 0 and abs(pu - ov_pu) <= ov_pu * 0.005:
            return 0
        if (lot.get("date") or "") < (txn_date or ""):
            return 1
        return 2
    tiers: list[list[dict]] = [[], [], []]
    for lot in lots:
        tiers[_tier(lot)].append(lot)
    free = (_free_after_reserve(lots, reserved) if reserved else None)
    if free is None:
        passes = [(tiers[0], None), (tiers[1], None), (tiers[2], None)]
    else:
        passes = [(tiers[0], None), (tiers[1], free), (tiers[2], free),
                  (tiers[1], None), (tiers[2], None)]
    remaining = qty
    basis_removed = 0.0
    carried: list[dict] = []
    for tl, f in passes:
        if remaining <= 1e-12:
            break
        if f is None:
            b, c = _consume_lots(tl, remaining, method)
        else:
            b, c = _consume_lots_capped(tl, remaining, method, f)
        basis_removed += b
        carried.extend(c)
        remaining -= sum(l["qty"] for l in c)
    # The consume helpers mutated the shared lot dicts and dropped
    # emptied ones from each tier list; rebuild the pool in original
    # order from the survivors.
    survivors = {id(l) for tl in tiers for l in tl}
    lots[:] = [l for l in lots if id(l) in survivors]
    return basis_removed, carried


def _rebase_is_move(consumed: float, override_total: float) -> bool:
    """True when a rebase's consumed basis already equals the broker's
    customer-provided figure — i.e. the broker tracked these lots and
    the 'rebase' is really a wallet move.  The walker then keeps the
    consumed lots verbatim (original acquired dates included) so later
    report hints naming those dates still find them; a genuine
    customer-provided receive instead pushes a fresh lot dated at the
    txn (matching how the broker's engine dates it)."""
    return abs(consumed - override_total) <= max(5.0, abs(override_total) * 0.005)


def _push_lot(state: dict, method: str, key: tuple, qty: float,
              basis_dollars: float, date: str,
              origin: str = "reconstructed") -> None:
    """``origin`` is provenance metadata carried on the lot dict —
    "broker" (basis_override / report-stamped), "fmv" (fin estimated
    FMV because the true basis is invisible), or "reconstructed"
    (normal txn-derived basis).  Inert for all basis math; surfaced in
    the Holdings per-lot view."""
    if qty <= 0:
        return
    if method == "avg":
        _add_to_avg(state, key, qty, basis_dollars)
    else:
        state["lots"][key].append({
            "date": date,
            "qty": qty,
            "basis_per_share": basis_dollars / qty,
            "origin": origin,
        })


def _push_txn_lots(state: dict, method: str, key: tuple, t: dict,
                   qty: float, total_basis: float, date: str,
                   origin: str = "reconstructed") -> None:
    """Push the lot(s) created by txn ``t``.

    When the txn carries a ``basis_override_lots`` breakdown (several
    broker-report acquisition rows grouped onto one fin txn — see
    ``broker_lots.stamp_acquisition_basis`` pass 2), push one lot per
    piece so the report's per-unit flavors survive in the pool and
    directed consumption can pick the exact lot a later disposal names.
    A single blended lot loses those flavors — the root cause of the
    Coinbase per-year realized timing drift.  Piece quantities were
    normalized at stamp time to sum exactly to the txn quantity, so
    balance↔lot-queue parity is unaffected; the txn-level ``cost_basis``
    annotation stays the TOTAL, so ``derive_basis_by_key_from_txns``
    needs no change.  Without a breakdown: one lot, as before.
    """
    pieces = t.get("basis_override_lots")
    if pieces and t.get("basis_override") is not None:
        for p in pieces:
            pq = float(p.get("qty", 0) or 0)
            if pq > 0:
                _push_lot(state, method, key, pq,
                          float(p.get("basis", 0) or 0), date,
                          origin="broker")
        return
    # A user / report basis_override means the figure came from the
    # broker, whatever branch pushed it.
    if t.get("basis_override") is not None:
        origin = "broker"
    _push_lot(state, method, key, qty, total_basis, date, origin=origin)


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
                "origin": lot.get("origin", "reconstructed"),
            })
    return total


def _walk(txns: list[dict], method: str, *, annotate: bool,
          account_methods: dict[str, str] | None = None,
          disposal_lots: dict | None = None) -> dict:
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

    `disposal_lots` directs sell-consumption per the broker's gain/loss
    report (see broker_lots.py) — again a pure consume-order strategy.
    The walk operates on its own copy since consuming mutates hint state.
    """
    state = _empty_state(method)

    from .broker_lots import (build_wrap_demand, copy_disposal_lots,
                              hints_for, reserved_future_demand,
                              take_wrap_demand, wrap_next_dates,
                              wrap_symbol_families)
    disposal_lots = copy_disposal_lots(disposal_lots)
    # Per-walk future-demand pool for wrap direction (independent
    # qty budget from the sale hints above — see broker_lots).
    wrap_demand = build_wrap_demand(disposal_lots)

    def _reserved_for(acct: str, date: str,
                      sym: str) -> dict[str, float] | None:
        """Acquired-date reservation for undirected consumption — the
        lots this account's report disposes after ``date`` in ``sym``'s
        wrap family (see broker_lots.reserved_future_demand)."""
        return reserved_future_demand(disposal_lots, acct, date,
                                      symbols=_sym_families.get(sym, {sym}))

    def _method_for(acct: str) -> str:
        if not account_methods:
            return method
        m = account_methods.get(acct, method)
        return m if m in ("fifo", "lifo", "hifo") else method

    def _ov(t: dict, default: float) -> float:
        """Use the user-supplied cost-basis override on this txn (set by
        cost_basis_overrides.match_and_stamp from a metadata `Cost Basis`
        row) when present, else the computed default.  Only consulted on
        lot-CREATING branches (add / unpaired transfer-in / unpaired
        wrap-in) — the off-platform acquisitions fin can't see the basis
        for."""
        bo = t.get("basis_override")
        return float(bo) if bo is not None else default

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

    # Wrap/unwrap groups (basis-carrying conversions), processed
    # atomically the first time any leg is met.
    wrap_groups = _pair_wraps(txns)
    wrap_until = wrap_next_dates(wrap_groups)
    _sym_families = wrap_symbol_families(wrap_groups)
    wrap_done: set[tuple] = set()
    # Per-leg annotations (effect, cost_basis, realized) computed when a
    # wrap group is processed atomically, applied as each leg is reached.
    wrap_leg_ann: dict[int, tuple] = {}

    # REBASE pairs: an intra-group pair whose Transfer In carries a user
    # `basis_override` (metadata Cost Basis row).  Coinbase's tax engine
    # treats these Pro→regular arrivals as receives with
    # customer-provided basis (GDAX-era lots migrated without basis), so
    # instead of the usual no-op the pair REPLACES the carried basis:
    # consume qty from the (same-key) queue with NO realized gain, then
    # push one lot at the override basis.  Balance is untouched (same
    # key: −qty then +qty); realized is untouched (no gain booked).
    # Processed atomically on whichever leg walks first, and strictly
    # consume-THEN-push — under LIFO/HIFO a push-first order would let
    # the consume eat the fresh override lot itself.
    rebase_pairs: dict[int, tuple[dict, dict]] = {}   # id(either leg) → (tin, tout)
    for _t in txns:
        if (id(_t) in intra_group and _basis_effect(_t) == "transfer_in"
                and _t.get("basis_override") is not None):
            _tout = tin_to_tout.get(id(_t))
            if _tout is not None:
                rebase_pairs[id(_t)] = (_t, _tout)
                rebase_pairs[id(_tout)] = (_t, _tout)
    rebase_done: set[int] = set()
    rebase_ann: dict[int, tuple[str, float]] = {}     # id(leg) → (effect, cost_basis)

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
        # stay in place.  Skip both legs — UNLESS the pair is a rebase
        # (Transfer In stamped with a user basis_override; see above).
        if id(t) in intra_group:
            pair = rebase_pairs.get(id(t))
            if pair is not None:
                tin, tout = pair
                if id(tin) not in rebase_done:
                    rebase_done.add(id(tin))
                    r_qty = float(tin.get("quantity", 0) or 0)
                    r_key = (tin.get("account_group", "") or "",
                             tin.get("symbol", "") or "")
                    new_basis = float(tin["basis_override"])
                    r_m = _method_for(r_key[0])
                    if method == "avg" or r_m == "avg":
                        consumed, carried = _consume_from_key(
                            state, r_m, r_key, r_qty)
                    else:
                        consumed, carried = _consume_for_rebase(
                            state["lots"][r_key], r_qty, r_m,
                            tin.get("date", ""), new_basis,
                            reserved=_reserved_for(r_key[0],
                                                   tin.get("date", ""),
                                                   r_key[1]))
                    if method != "avg" and _rebase_is_move(consumed, new_basis):
                        # Wallet move of broker-tracked lots: keep them
                        # verbatim (dates + per-units) — see _rebase_is_move.
                        pushed = _push_carried_lots(state, method, r_key,
                                                    carried)
                        rebase_ann[id(tin)]  = ("rebase_in",  pushed)
                        rebase_ann[id(tout)] = ("rebase_out", consumed)
                    else:
                        _push_txn_lots(state, method, r_key, tin, r_qty,
                                       new_basis, tin.get("date", ""))
                        rebase_ann[id(tin)]  = ("rebase_in",  new_basis)
                        rebase_ann[id(tout)] = ("rebase_out", consumed)
                final_effect, cb = rebase_ann[id(t)]
                cost_basis_value = cb
                if annotate:
                    t["basis_effect"] = final_effect
                    t["cost_basis"] = round(cb, 2)
                continue
            final_effect = "intra_group_noop"
            if annotate:
                t["basis_effect"] = final_effect
            continue

        if effect == "add":
            basis = _ov(t, _basis_dollars(t))
            _push_txn_lots(state, method, key, t, qty, basis,
                           t.get("date", ""))
            if qty > 0:
                cost_basis_value = basis

        elif effect == "zero_basis":
            # Rewards / spinoffs / mergers: FMV-at-receipt if price known,
            # else zero.  A user Cost Basis override wins (e.g. a free
            # share's grant-FMV basis that exists only on the 1099).
            price = float(t.get("price", 0) or 0)
            basis = _ov(t, qty * price if price > 0 else 0.0)
            _push_txn_lots(state, method, key, t, qty, basis,
                           t.get("date", ""),
                           origin="reconstructed" if price > 0 else "fmv")
            if qty > 0:
                cost_basis_value = basis

        elif effect == "remove":
            proceeds = _basis_dollars(t)
            # Broker-report lot hints (gain/loss report rows for this
            # account/symbol/date) direct WHICH lots the sale consumes;
            # anything unmatched falls back to the account's method.
            # avg has no discrete lots, so hints only apply to the
            # lot-list methods.
            _hints = hints_for(disposal_lots, acct, sym, t.get("date", ""))
            _m = _method_for(acct)
            _rsv = (_reserved_for(acct, t.get("date", ""), sym)
                    if _m != "avg" and method != "avg" else None)
            if _hints and _m != "avg" and method != "avg":
                basis_removed, carried = _consume_lots_directed(
                    state["lots"][key], qty, _m, _hints, reserved=_rsv)
            else:
                basis_removed, carried = _consume_from_key(state, _m, key, qty,
                                                           reserved=_rsv)
            cost_basis_value = basis_removed
            # A fee paid by redeeming shares (e.g. a fund maintenance fee)
            # removes the shares + their basis from the lot queue — so
            # balance↔basis parity holds — but it's an EXPENSE, not a
            # trade, so we don't book the (immaterial, noise) realized
            # gain/loss it would otherwise produce.  basis_effect stays
            # "remove" so derive_basis_by_key_from_txns still drops the
            # basis correctly.
            is_fee = (t.get("action") == "Fee")
            if not is_fee:
                realized = proceeds - basis_removed
            # Per-lot breakdown of the consumed lots.  Each lot keeps its
            # own acquired date, basis, and proceeds (apportioned by
            # share) and days-held, so the Tax tab can classify realized
            # gain short- vs long-term *lot by lot* — a single sell often
            # straddles the 1-year line, and collapsing it to one
            # weighted-average holding period mis-buckets the whole gain
            # (and fabricates an acquired date that matches no real lot).
            # ``holding_days`` (the weighted average) is still emitted for
            # the transaction-detail display.  FIFO/annotate only; skipped
            # for fees (no realized gain to classify).
            if annotate and not is_fee:
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
                # External deposit with no visible origin leg (e.g. crypto
                # received from an off-platform wallet).  We can't recover
                # the true basis, so use FMV at the transfer date
                # (qty × price) when the broker recorded a spot price —
                # the correct basis for assets acquired at market, and a
                # far better estimate than $0 (which would book the entire
                # proceeds as gain on a later sale).  Falls back to $0 only
                # when no price is available.  A user-supplied basis
                # override (off-platform "customer-provided" cost) wins
                # over the FMV guess.
                price = float(t.get("price", 0) or 0)
                basis = _ov(t, qty * price if price > 0 else 0.0)
                _push_txn_lots(state, method, key, t, qty, basis,
                               t.get("date", ""), origin="fmv")
                cost_basis_value = basis
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

        elif effect in ("wrap_out", "wrap_in"):
            # Basis-carrying conversion (e.g. ETH↔CBETH).  Process the whole
            # (account, date, kind) group atomically the first time we meet
            # any leg — consume all source lots (NO realized gain), carry the
            # total basis (rescaled to the destination quantity, dates
            # preserved) to the destination symbol.  Each leg is then
            # annotated with its OWN per-symbol basis delta (out legs
            # subtract, in legs add) so the txn-level reconstruction
            # (derive_basis_by_key_from_txns) and the refresh path stay in
            # sync with the lot queue.
            kind = "unwrap" if "Unwrap" in (t.get("action", "") or "") else "wrap"
            gkey = (acct, t.get("date", "") or "", kind)
            if gkey not in wrap_done:
                wrap_done.add(gkey)
                g = wrap_groups.get(gkey)
                if g and g["out"] and g["in"] and g["q_out"] > 0 and g["q_in"] > 0:
                    # Direct the SOURCE consumption by the destination
                    # symbol's FUTURE report disposals (wrap demand):
                    # the report's later sales of the destination name
                    # the acquired dates Coinbase's engine actually
                    # relieved, and dates survive the wrap — so moving
                    # those exact lots lets each directed sale find
                    # them.  (Was same-day-sell-hints only, which left
                    # any wrap NOT followed by a same-day sale on HIFO
                    # order — the source of multi-$k per-year realized
                    # timing drift vs the broker report.)  The demand
                    # pool's budget is independent of the sales' own
                    # hint budget.
                    _wh = take_wrap_demand(wrap_demand, acct, g["dst"],
                                           t.get("date", ""),
                                           g["q_in"], g["q_out"],
                                           until=wrap_until.get(
                                               (acct, g["dst"],
                                                t.get("date", "") or "")))
                    _wrsv = (_reserved_for(acct, t.get("date", ""), g["src"])
                             if method != "avg" and _method_for(acct) != "avg"
                             else None)
                    if _wh and method != "avg" and _method_for(acct) != "avg":
                        B, carried = _consume_lots_directed(
                            state["lots"][(acct, g["src"])], g["q_out"],
                            _method_for(acct), _wh, reserved=_wrsv)
                    else:
                        B, carried = _consume_from_key(
                            state, _method_for(acct), (acct, g["src"]),
                            g["q_out"], reserved=_wrsv)
                    _push_carried_lots(state, method, (acct, g["dst"]),
                                       _rescale_lots(carried, g["q_in"]))
                    for ol in g["out"]:
                        oq = float(ol.get("quantity", 0) or 0)
                        wrap_leg_ann[id(ol)] = (
                            "wrap_out", B * (oq / g["q_out"]), None)
                    for il in g["in"]:
                        iq = float(il.get("quantity", 0) or 0)
                        wrap_leg_ann[id(il)] = (
                            "wrap_in", B * (iq / g["q_in"]), None)
                elif g:
                    # Lone / malformed group — fall back per leg so basis
                    # isn't silently lost.  A bare wrap_out behaves like a
                    # sale (realize gain); a bare wrap_in like an FMV buy.
                    for ol in g["out"]:
                        oq = float(ol.get("quantity", 0) or 0)
                        proceeds = _basis_dollars(ol)
                        b_rm, _c = _consume_from_key(
                            state, _method_for(acct), (acct, ol.get("symbol", "")), oq)
                        wrap_leg_ann[id(ol)] = (
                            "wrap_out_unpaired", b_rm, proceeds - b_rm)
                    for il in g["in"]:
                        iq = float(il.get("quantity", 0) or 0)
                        px = float(il.get("price", 0) or 0)
                        b = _ov(il, iq * px if px > 0 else 0.0)
                        _push_txn_lots(state, method,
                                       (acct, il.get("symbol", "")),
                                       il, iq, b, il.get("date", ""),
                                       origin="fmv")
                        wrap_leg_ann[id(il)] = ("wrap_in_unpaired", b, None)
            ann = wrap_leg_ann.get(id(t))
            if ann:
                final_effect, cost_basis_value, realized = ann

        elif effect == "split":
            if method == "avg":
                state["lots"][key][0] += qty
            else:
                lots = state["lots"][key]
                old_total = sum(lot["qty"] for lot in lots)
                _apply_split_to_lots(lots, old_total, qty)

        elif (effect == "ignore" and qty > 0 and sym
              and sym not in ("USD",) and t.get("basis_override") is not None):
            # Neutral same-pool conversion carrying a broker-reported
            # basis (Coinbase's ETH2↔ETH deprecation / staking events:
            # the gain/loss report shows them as gain-0 dispositions
            # whose acquired side carries the customer-provided basis).
            # Rebase the pool at zero gain: consume qty, push one lot at
            # the override basis.  Balance untouched (same key, −qty
            # then +qty); no realized gain.  Annotated with the NET
            # basis delta so derive_basis_by_key_from_txns reconstructs
            # the pool change from this single txn.
            consumed, _c = _consume_from_key(
                state, _method_for(acct), key, qty,
                reserved=(_reserved_for(acct, t.get("date", ""), sym)
                          if method != "avg" and _method_for(acct) != "avg"
                          else None))
            new_basis = float(t["basis_override"])
            _push_txn_lots(state, method, key, t, qty, new_basis,
                           t.get("date", ""))
            final_effect = "rebase_neutral"
            cost_basis_value = new_basis - consumed

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
                          account_methods: dict[str, str] | None = None,
                          disposal_lots: dict | None = None) -> dict:
    """Run the annotated walker (FIFO by default), annotating txns in
    place.  Returns the final lot state for merging into holdings.

    `account_methods` overrides the lot-relief method per `account_group`
    (e.g. ``{"Coinbase": "hifo"}``) so realized gains / cost basis match
    what the broker actually used.  Affects which lots' basis is realized
    (and thus realized_gain, holding period, MAGI), never balances.  The
    Lot-Method Comparison table (compute_basis_all_methods) still reports
    pure single-method totals for what-if comparison.

    `disposal_lots` (from ``broker_lots.load_disposal_lots``) directs
    sell-consumption to the specific lots the broker's gain/loss report
    says each disposal consumed — the strongest form of lot relief,
    superseding the method order wherever the report has rows.
    """
    return _walk(txns, "fifo", annotate=True, account_methods=account_methods,
                 disposal_lots=disposal_lots)


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
    - ``rebase_in``            → +cost_basis  (intra-group pair with a user basis
                                               override: the new customer-provided basis)
    - ``rebase_out``           → -cost_basis  (its counter-leg: the carried basis consumed)
    - ``rebase_neutral``       → +cost_basis  (Neutral same-pool conversion rebased at a
                                               broker-reported basis; cb is the NET delta:
                                               override − consumed)
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
        if be in ("add", "zero_basis", "transfer_in", "transfer_in_unpaired",
                  "wrap_in", "wrap_in_unpaired", "rebase_in",
                  "rebase_neutral"):
            by_key[key] = by_key.get(key, 0.0) + float(cb)
        elif be in ("remove", "transfer_out", "wrap_out", "wrap_out_unpaired",
                    "rebase_out"):
            by_key[key] = by_key.get(key, 0.0) - float(cb)
        # intra_group_noop / split / ignore / wrap_carry_noop: no contribution
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
