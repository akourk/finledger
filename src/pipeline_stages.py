"""Composable pipeline stages — pure(-ish) functions that turn raw txns
into the structures the dashboard consumes.

Each stage takes explicit inputs and returns explicit outputs (with the
documented exception of ``walk_balances`` and a couple of others that
annotate txns in place — see each docstring).  Both the full pipeline
(``main.main``) and the fast-refresh path (``main._refresh_prices_only``)
compose these stages.  Pulling them out of ``main()`` was driven by
a real bug: when the refresh path was first written it manually
re-implemented the USD-non-Savings skip rule, the dust filter, and the
cash-fold step, and silently disagreed with main() on all three.  With
the logic centralized here, both code paths share one implementation.

What's NOT in here:
- CSV scan / parse / dedupe / normalize — that's parser concerns.
- Cost basis walking — already pure functions in ``basis.py``.
- Sector / price fetching — those have their own modules and side
  effects (caches, network).
- History / analytics / dashboard — those modules are already cleanly
  separated.

What IS in here: the holdings-shaped logic that previously lived as
inline code blocks in ``main()`` and was begging to be reused.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .actions import NEUTRAL_ACTIONS, SUBTRACT_ACTIONS
from .config import ACCOUNT_TYPES, CASH_SYMBOLS


# ---------------------------------------------------------------------------
# Stage 1: dust filter
# ---------------------------------------------------------------------------

def is_dust(qty: float, price: float) -> bool:
    """Whether a (qty, price) pair represents a position too small to display.

    Two regimes:

    1. **With a price**: dust if the dollar value rounds below a penny,
       OR (defensively) if it's a small NEGATIVE fractional position
       under $200 — those are corporate-action artifacts (SPR surrender
       without a paired receive, CIL on a ticker we don't have full
       history for).  A real held position is always positive and
       ≥1 share for stocks the cache can price.

    2. **Without a price**: dust if quantity is below 1e-6, OR if it's
       negative — broker CSVs leave residuals like 7.7e-9 from
       precision mismatches between paired Wrap/Sell rows, and orphan
       option-exercise rows can push contract counts negative
       indefinitely.

    Pulled out of ``main()`` so the full pipeline and ``--refresh-prices``
    apply the same filter.
    """
    if price > 0:
        if qty < 0 and abs(qty) < 1.0 and abs(qty * price) < 200:
            return True
        return abs(qty * price) < 0.01
    if qty < 0:
        return True
    return abs(qty) < 1e-6


# ---------------------------------------------------------------------------
# Stage 2: balance walker
# ---------------------------------------------------------------------------

def walk_balances(txns: list[dict]) -> tuple[list[dict], dict[tuple[str, str], float]]:
    """Sort txns chronologically, walk running balances per
    (account_group, symbol).  Annotates each txn with ``balance`` and
    ``value`` fields IN PLACE.

    Returns ``(sorted_txns, balances)`` — the sorted txn list (same
    objects, new order) and the final balance dict.

    **Invariant**: USD balances in non-Savings accounts are
    deliberately not tracked.  Sell proceeds + transfer outs from
    taxable / retirement accounts are routinely under-reported in
    broker CSVs, so a USD running balance there would silently go
    very negative.  Such txns get ``balance=None`` / ``value=None``.

    This was previously inline in ``main()``; the bug that pushed the
    extraction out: ``--refresh-prices`` re-implemented the walk and
    forgot the USD skip, surfacing -$107k phantom Coinbase USD in the
    changes panel.
    """
    # Sort: date, account, symbol, then increases before decreases on
    # the same day so balances don't go negative mid-day (a Transfer
    # In must post before its paired Transfer Out, etc).  Neutral
    # actions sort between adds and subs.
    sorted_txns = sorted(txns, key=lambda t: (
        t.get("date", ""),
        t.get("account_group", ""),
        t.get("symbol", ""),
        2 if t.get("action") in SUBTRACT_ACTIONS
        else (1 if t.get("action") in NEUTRAL_ACTIONS else 0),
    ))
    balances: dict[tuple[str, str], float] = {}
    for txn in sorted_txns:
        sym = txn.get("symbol", "")
        acct_type = txn.get("account_type", "")
        skip_balance = sym in CASH_SYMBOLS and acct_type != "Savings"
        key = (txn.get("account_group", ""), sym)
        qty = float(txn.get("quantity", 0) or 0)
        price = float(txn.get("price", 0) or 0)
        action = txn.get("action")
        if action in NEUTRAL_ACTIONS:
            qty = 0.0
        elif action in SUBTRACT_ACTIONS:
            qty = -qty
        if skip_balance:
            txn["balance"] = None
            txn["value"] = None
        else:
            prev = balances.get(key, 0.0)
            new_bal = prev + qty
            balances[key] = new_bal
            txn["balance"] = round(new_bal, 8)
            if action in NEUTRAL_ACTIONS:
                txn["value"] = None
            else:
                txn["value"] = (round(abs(float(txn.get("quantity", 0))) * price, 2)
                                if price else None)
    return sorted_txns, balances


# ---------------------------------------------------------------------------
# Stage 3: closed-position bookkeeping
# ---------------------------------------------------------------------------

def compute_position_endings(txns: list[dict]) -> tuple[dict[str, str], set[str]]:
    """Derive per-symbol bookkeeping needed for price-fetch clamping.

    Returns ``(closed_position_ends, trivial_symbols)``:

    - ``closed_position_ends``: ``{symbol: ISO-date}`` for symbols whose
      final balance is 0 — fed to ``ensure_coverage`` so we don't ask
      yfinance for today's price on a position the user no longer holds.

    - ``trivial_symbols``: symbols whose max historical qty was < 1 share
      AND whose max historical *dollar* value was immaterial AND final
      balance is dust-filtered.  These are corp-action artifacts (ENVXW
      spinoff warrants, fractional CIL bits) that never produce material
      snapshot value — skip the fetch entirely.  The dollar guard matters
      for high-priced assets: a closed 0.8 BTC position is well under
      1 "share" but was worth thousands while held, and skipping its
      price fetch would leave those snapshots valued at stale txn prices.
    """
    _TRIVIAL_MAX_VALUE = 100.0   # any position ever worth ≥ this fetches

    max_abs_bal: dict[str, float] = defaultdict(float)
    max_pos_value: dict[str, float] = defaultdict(float)
    final_bal:   dict[str, float] = defaultdict(float)
    last_nonzero_date: dict[str, str] = {}
    for t in sorted(txns, key=lambda x: x.get("date", "")):
        sym = t.get("symbol", "")
        if not sym or sym == "USD":
            continue
        a = t.get("action", "")
        if a in NEUTRAL_ACTIONS:
            continue
        q = float(t.get("quantity", 0) or 0)
        prev_bal = final_bal[sym]
        final_bal[sym] += (-q if a in SUBTRACT_ACTIONS else q)
        if abs(final_bal[sym]) > max_abs_bal[sym]:
            max_abs_bal[sym] = abs(final_bal[sym])
        px = float(t.get("price", 0) or 0)
        if px > 0:
            # Position value at this txn's observed price — cheap
            # materiality proxy without a price-cache lookup.
            val = max(abs(final_bal[sym]), abs(prev_bal)) * px
            if val > max_pos_value[sym]:
                max_pos_value[sym] = val
        # A txn "touches" a live position if the balance was nonzero on
        # EITHER side of it — including the closing sell that takes it
        # to zero.  Checking only the post-txn balance clamped the fetch
        # range at the txn *before* the disposal, leaving the final
        # holding stretch (last add → close) without price coverage.
        if (abs(final_bal[sym]) > 1e-6 or abs(prev_bal) > 1e-6) and t.get("date"):
            last_nonzero_date[sym] = t["date"]

    closed_position_ends: dict[str, str] = {}
    for sym, bal in final_bal.items():
        if abs(bal) > 1e-6:
            continue
        last = last_nonzero_date.get(sym)
        if last:
            closed_position_ends[sym] = last

    trivial = {sym for sym in final_bal
               if sym and sym != "USD"
               and max_abs_bal.get(sym, 0) < 1.0
               and max_pos_value.get(sym, 0) < _TRIVIAL_MAX_VALUE
               and abs(final_bal.get(sym, 0)) < max(1e-4,
                                                    max_abs_bal.get(sym, 0) * 0.01)}
    return closed_position_ends, trivial


# ---------------------------------------------------------------------------
# Stage 4: cash principal (Apple Savings basis = deposits − withdrawals)
# ---------------------------------------------------------------------------

def compute_cash_principal(txns: Iterable[dict]) -> dict[tuple[str, str], float]:
    """Per-(account, USD) net principal for Savings-type accounts.

    For HYSA-style accounts, basis = principal (deposits − withdrawals).
    Interest accrued shows up as unrealized gain rather than getting
    absorbed into basis — otherwise a HYSA always reads "gain: 0"
    which buries the entire return of the account.

    Returns ``{(account_group, "USD"): principal_dollars}``.
    """
    out: dict[tuple[str, str], float] = defaultdict(float)
    for t in txns:
        sym = t.get("symbol", "")
        if sym not in CASH_SYMBOLS:
            continue
        acct = t.get("account_group", "")
        if ACCOUNT_TYPES.get(acct) != "Savings":
            continue
        action = t.get("action", "")
        amt = float(t.get("amount", 0) or 0)
        if action == "Deposit":
            out[(acct, sym)] += amt
        elif action == "Withdrawal":
            out[(acct, sym)] -= amt
        # Interest (and any future non-principal cash event) deliberately
        # excluded — it's the gain itself, not principal.
    return dict(out)


# ---------------------------------------------------------------------------
# Stage 5: holdings rebuild
# ---------------------------------------------------------------------------

def build_holdings(
    balances: dict[tuple[str, str], float],
    last_prices: dict[str, float],
    fifo_basis_by_key: dict[tuple[str, str], float],
    cash_principal_by_key: dict[tuple[str, str], float],
) -> tuple[list[dict], list[dict]]:
    """From the balance walker output + fresh prices + basis, produce:

    - ``holdings_by_account``: one row per (account_group, symbol) with
      non-zero, non-dust balance.
    - ``holdings``: aggregated across accounts (one row per symbol).

    Cost basis source:
    - Savings USD → cash principal (deposits − withdrawals)
    - Other USD → None (not in lot queue)
    - Non-cash → from FIFO walker
    """
    holdings_by_account: list[dict] = []
    for (acct, sym), qty in sorted(balances.items()):
        price = last_prices.get(sym, 0.0)
        if is_dust(qty, price):
            continue
        value = round(qty * price, 2) if price else None
        if sym in CASH_SYMBOLS and ACCOUNT_TYPES.get(acct) == "Savings":
            cost_basis = round(cash_principal_by_key.get((acct, sym), 0.0), 2)
        else:
            # FIFO walker has an entry only when basis was tracked for
            # that (acct, sym).  USD in non-Savings → no entry → None.
            if (acct, sym) in fifo_basis_by_key:
                cost_basis = round(fifo_basis_by_key[(acct, sym)], 2)
            else:
                cost_basis = None
        unrealized = (round(value - cost_basis, 2)
                      if (value is not None and cost_basis is not None) else None)
        holdings_by_account.append({
            "account_group": acct,
            "account_type": ACCOUNT_TYPES.get(acct, "Taxable"),
            "symbol": sym,
            "quantity": round(qty, 8),
            "price": round(price, 2),
            "value": value,
            "cost_basis": cost_basis,
            "unrealized_gain": unrealized,
        })

    asset_totals: dict[str, float] = defaultdict(float)
    asset_basis:  dict[str, float] = defaultdict(float)
    asset_has_basis: dict[str, bool] = defaultdict(bool)
    for h in holdings_by_account:
        asset_totals[h["symbol"]] += h["quantity"]
        if h.get("cost_basis") is not None:
            asset_basis[h["symbol"]] += h["cost_basis"]
            asset_has_basis[h["symbol"]] = True

    holdings: list[dict] = []
    for sym, qty in sorted(asset_totals.items()):
        price = last_prices.get(sym, 0.0)
        if is_dust(qty, price):
            continue
        cost_basis = round(asset_basis[sym], 2) if asset_has_basis[sym] else None
        value = round(qty * price, 2) if price else None
        unrealized = (round(value - cost_basis, 2)
                      if (value is not None and cost_basis is not None) else None)
        holdings.append({
            "symbol": sym,
            "quantity": round(qty, 8),
            "price": round(price, 2),
            "value": value,
            "cost_basis": cost_basis,
            "unrealized_gain": unrealized,
        })

    return holdings, holdings_by_account


# ---------------------------------------------------------------------------
# Stage 6: cash fold into basis methods
# ---------------------------------------------------------------------------

def fold_cash_into_basis_methods(
    basis_methods: dict,
    holdings_by_account: list[dict],
) -> None:
    """Fold Apple Savings cash into per-method basis_methods totals.

    The basis walker intentionally skips USD (no lots), but the
    dashboard's headline "Cost Basis / Value / Unrealized P&L" cards
    expect cash to be included so the HYSA's interest return shows up.
    Mutates ``basis_methods`` in place.

    Identical math runs on every method (FIFO/LIFO/HIFO/Average) since
    cash isn't lot-method-specific.

    Was previously a 40-line inline block in ``main()``; pulled here so
    ``--refresh-prices`` can reuse it without re-typing the math.
    """
    cash_rows = [h for h in holdings_by_account
                 if h.get("symbol") in CASH_SYMBOLS
                 and isinstance(h.get("value"), (int, float))]
    if not cash_rows:
        return

    cash_value_by_type: dict[str, float] = {}
    cash_basis_by_type: dict[str, float] = {}
    cash_value_total = 0.0
    cash_basis_total = 0.0
    for h in cash_rows:
        t = h.get("account_type", "Savings")
        v = h["value"]
        b = h.get("cost_basis")
        if b is None:
            b = v   # non-Savings cash falls back to face value
        cash_value_by_type[t] = cash_value_by_type.get(t, 0.0) + v
        cash_basis_by_type[t] = cash_basis_by_type.get(t, 0.0) + b
        cash_value_total += v
        cash_basis_total += b
    cash_gain_total = cash_value_total - cash_basis_total

    for m in basis_methods:
        t = basis_methods[m]["totals"]
        t["cost_basis"]      = round(t["cost_basis"]      + cash_basis_total, 2)
        t["value"]           = round(t["value"]           + cash_value_total, 2)
        t["unrealized_gain"] = round(t["unrealized_gain"] + cash_gain_total, 2)
        tt_map = basis_methods[m]["totals_by_type"]
        for type_key, val in cash_value_by_type.items():
            basis = cash_basis_by_type[type_key]
            tt = tt_map.setdefault(type_key, {
                "cost_basis": 0.0, "value": 0.0,
                "unrealized_gain": 0.0, "unpriced_count": 0,
            })
            tt["cost_basis"]      = round(tt["cost_basis"]      + basis, 2)
            tt["value"]           = round(tt["value"]           + val, 2)
            tt["unrealized_gain"] = round(tt["unrealized_gain"] + (val - basis), 2)


# ---------------------------------------------------------------------------
# Stage 7: build basis-methods totals from rows + cash fold
# ---------------------------------------------------------------------------

def build_basis_methods_totals(
    basis_methods: dict,
    holdings_by_account: list[dict],
) -> None:
    """Recompute ``basis_methods[m]['totals']`` and ``totals_by_type``
    from each method's per-holding rows, then fold in cash.

    Used by the refresh-prices path: rows are loaded from the prior
    JSON and re-priced with fresh ``last_prices``, so totals must be
    rebuilt rather than reused (the prior totals already had cash
    folded in once — re-adding would double-count).

    Mutates ``basis_methods`` in place.
    """
    for m, block in basis_methods.items():
        rows = block.get("holdings", [])
        realized_gain = block.get("totals", {}).get("realized_gain", 0.0)

        totals_by_type: dict[str, dict] = {}
        for r in rows:
            t = totals_by_type.setdefault(r.get("account_type", "Taxable"), {
                "cost_basis": 0.0, "value": 0.0, "unrealized_gain": 0.0,
                "unpriced_count": 0,
            })
            t["cost_basis"] += r.get("cost_basis", 0)
            if r.get("value") is None:
                t["unpriced_count"] += 1
            else:
                t["value"] += r["value"]
                if r.get("unrealized_gain") is not None:
                    t["unrealized_gain"] += r["unrealized_gain"]
        block["totals"] = {
            "cost_basis":      round(sum(r.get("cost_basis", 0) for r in rows), 2),
            "value":           round(sum((r.get("value") or 0) for r in rows), 2),
            "unrealized_gain": round(sum((r.get("unrealized_gain") or 0) for r in rows), 2),
            "realized_gain":   realized_gain,
            "unpriced_count":  sum(1 for r in rows if r.get("value") is None),
        }
        block["totals_by_type"] = {
            k: {
                "cost_basis":      round(t["cost_basis"], 2),
                "value":           round(t["value"], 2),
                "unrealized_gain": round(t["unrealized_gain"], 2),
                "unpriced_count":  t["unpriced_count"],
            } for k, t in totals_by_type.items()
        }

    fold_cash_into_basis_methods(basis_methods, holdings_by_account)
