"""Reconstructed broker-cash bridges for history snapshots.

fin deliberately does NOT track USD balances for non-Savings accounts
(see CLAUDE.md's "USD balance tracking is deliberately skipped"
invariant).  For most brokers that is the right call: their exports
underreport cash — sell proceeds frequently never appear as a row — so
a naive running cash balance would be worse than no balance at all.

For a broker whose export is *provably complete*, the opposite is true.
Every deposit, withdrawal, trade leg, distribution and fee is present,
so the implicit cash balance can be reconstructed exactly — and NOT
bridging it actively corrupts the numbers.  A sale converts tracked
position value into untracked cash, so the snapshot series shows a
phantom drop with no offsetting external flow, which Modified-Dietz TWR
reads as a market loss.  The damage compounds: every profitable round
trip books a phantom loss roughly equal to the gain, and chain-linking
turns that into a large negative lifetime return for an account that
actually made money.

Coinbase was bridged first (its natural-flow walker ends at ~$0, which
matched the user's real wallet).  This module generalizes that
mechanism so a validated group is one registry entry rather than a
second copy of the walk.

**Adding a group here is a claim about that broker's data
completeness.**  Validate before adding:

1. Walk ``usd_series`` and confirm the balance never goes materially
   negative (a persistent negative means inflow rows are missing).
2. Confirm the final balance matches the broker's reported cash — or
   that ``tracked_value + final_balance`` matches the broker's total
   account value.  A ``Reconcile Balance`` row in ``metadata.csv``
   makes this an ongoing check rather than a one-off.

Get this wrong and you inflate portfolio value with cash that isn't
there, which is worse than the understatement it fixes.
"""

from __future__ import annotations

from .config import CASH_SYMBOLS

# Account groups whose cash balance is reconstructed and added to
# history snapshots.  See the module docstring before extending.
BRIDGED_GROUPS: tuple[str, ...] = ("Coinbase", "Robinhood")

# Sub-dollar bridge balances are floating-point drift accumulated over
# thousands of rows, not real cash.  Looser than main.py's $0.01 dust
# rule for exactly that reason.
BRIDGE_MIN = 1.0


# ---------------------------------------------------------------------------
# Robinhood
# ---------------------------------------------------------------------------
# Robinhood's activity CSV is complete: it records every ACH leg, every
# trade, every distribution and every fee against the brokerage cash
# balance.  Actions below are the canonical (post-normalize) names.

_RH_CASH_IN = frozenset({
    "Deposit",            # ACH in (parser splits ACH by sign)
    "Sell",               # proceeds land in cash
    "Option Sell",
    "Option Exercise",    # cash-settled index options — OCC cash component
    "Dividend",
    "Interest",
    "Lending",            # share-lending income
    "Return of Capital",
    "Event Contract Transfer",      # inbound leg from the event-contracts entity
})

_RH_CASH_OUT = frozenset({
    "Buy",
    "Option Buy",
    "Withdrawal",
    "Fee",
    "Tax",                          # dividend withholding
    "Return of Capital Reversal",   # negative-amount ROC (parser-split)
    "Event Contract Transfer Out",  # outbound leg (parser-split)
})

# These actions credit cash ONLY when booked against the USD symbol.
# The same action also appears as a SHARE credit — a free-share promo
# (Reward/REC), an Apex→RHS share migration (Conversion) — where
# ``amount`` is share value, not cash.
_RH_CASH_IN_USD_ONLY = frozenset({"Reward", "Conversion"})


def _robinhood_usd_effect(t: dict) -> float:
    """Per-txn brokerage-cash effect for a Robinhood row."""
    action = t.get("action", "")
    amt = float(t.get("amount", 0) or 0)
    if not amt:
        return 0.0
    if action in _RH_CASH_IN:
        return +amt
    if action in _RH_CASH_OUT:
        return -amt
    if action in _RH_CASH_IN_USD_ONLY:
        sym = t.get("symbol", "") or ""
        return +amt if (sym in CASH_SYMBOLS or not sym) else 0.0
    # Everything else moves shares only: Option Expire, Split, Spinoff,
    # Merger, Neutral, and share-symbol Reward / Conversion.
    return 0.0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def usd_effect(t: dict) -> float:
    """Per-txn implicit cash effect, dispatched by account group.

    Returns 0.0 for any group that isn't bridged, so callers can walk
    the whole ledger without pre-filtering.
    """
    group = t.get("account_group", "")
    if group == "Coinbase":
        from .coinbase_reconcile import usd_effect as _cb_usd_effect
        return _cb_usd_effect(t)
    if group == "Robinhood":
        return _robinhood_usd_effect(t)
    return 0.0


def usd_series(txns: list[dict], group: str) -> list[tuple[str, float]]:
    """Implicit cash running balance for ``group`` over time.

    Returns ``[(date, running_balance_at_eod), ...]`` sorted by date;
    multiple events on one date collapse to that date's closing balance.

    Inflows are applied before outflows within a date.  Brokers often
    list a day's buys ahead of the deposit that funded them, and
    without this the intraday ordering produces large phantom negative
    excursions (they clamp to 0 at read time, silently understating
    the bridge on exactly the days it matters most).
    """
    chrono: list[tuple[str, int, str, int, float]] = []
    for t in txns:
        if t.get("account_group") != group:
            continue
        eff = usd_effect(t)
        order = 0 if eff > 0 else (1 if eff < 0 else 2)
        chrono.append((t.get("date", ""), order, t.get("source", ""),
                       id(t), eff))
    chrono.sort(key=lambda x: x[:4])

    eod: dict[str, float] = {}
    running = 0.0
    for date, _order, _src, _id, eff in chrono:
        running += eff
        if date:
            eod[date] = running
    return sorted(eod.items())


def all_series(txns: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """``{account_group: [(date, balance), ...]}`` for every bridged group.

    Groups with no activity in ``txns`` are omitted.
    """
    out: dict[str, list[tuple[str, float]]] = {}
    for group in BRIDGED_GROUPS:
        series = usd_series(txns, group)
        if series:
            out[group] = series
    return out


def synthesize_external_funding(txns: list[dict], group: str) -> list[dict]:
    """Synthesize Deposit rows for external funding the export omits.

    Broker activity exports are often truncated: Robinhood's begins at
    the first trade, so an account can show a year of buying before its
    first ACH row.  The reconstructed cash balance then runs negative,
    ``balance_at`` clamps it to 0, and the bridge silently contributes
    nothing across exactly the early period where the account is small
    and every percentage swing is amplified.

    Walks chronologically and synthesizes a Deposit whenever the
    running balance sets a NEW cumulative low — the deepest point of
    net cash the account must have received from outside.  Transient
    dips already covered by later inflows are recycled cash, not new
    funding, and are left alone.

    Returns ``txns`` plus the synthesized rows.  Must run AFTER action
    normalization: classification matches canonical action names.

    This mirrors ``coinbase_reconcile.reconcile_external_funding``,
    which solves the same problem for Coinbase.
    """
    from .config import ACCOUNT_TYPES

    chrono = []
    for t in txns:
        if t.get("account_group") != group:
            continue
        eff = usd_effect(t)
        order = 0 if eff > 0 else (1 if eff < 0 else 2)
        chrono.append((t.get("date", ""), order, t.get("source", ""),
                       id(t), t, eff))
    chrono.sort(key=lambda x: x[:4])

    extra: list[dict] = []
    running = 0.0
    cumulative_min = 0.0
    for date, _order, _src, _id, t, eff in chrono:
        running += eff
        if running < cumulative_min - 0.01:
            deficit = round(cumulative_min - running, 2)
            cumulative_min = running
            extra.append({
                "date": date,
                "account": group,
                "account_group": group,
                "account_type": ACCOUNT_TYPES.get(group, "Taxable"),
                "symbol": "USD",
                "action": "Deposit",
                "raw_action": "Synthesized Deposit (implicit external funding)",
                "quantity": deficit,
                "price": 1.0,
                "fees": 0.0,
                "amount": deficit,
                "description": (f"Auto-synthesized: implicit external funding "
                                f"for {t.get('action', '')} "
                                f"{(t.get('symbol') or '')[:30]}"),
                "source": f"auto-reconcile-{group.lower().replace(' ', '-')}-funding",
            })

    if extra:
        total = sum(r["amount"] for r in extra)
        print(f"  Synthesized {len(extra)} {group} Deposit row(s) "
              f"for ${total:,.2f} of implicit external funding "
              f"(cumulative min: ${cumulative_min:,.2f})")
    return txns + extra


def balance_at(series: list[tuple[str, float]], date: str) -> float:
    """Most recent end-of-day balance at or before ``date``, clamped at 0.

    Negative excursions are transient artifacts (same-day ordering the
    date-only ledger can't resolve, sub-dollar rounding drift), never
    real — a brokerage cash balance cannot go negative without margin,
    which fin does not model.  Clamping is the conservative choice: it
    understates the bridge rather than inventing value.
    """
    bal = 0.0
    for d, b in series:
        if d <= date:
            bal = b
        else:
            break
    return max(bal, 0.0)
