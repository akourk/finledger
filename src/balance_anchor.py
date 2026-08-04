"""True up hand-maintained CASH accounts to a known statement balance.

Some accounts are maintained by hand rather than by CSV export — the
user types in transfers and monthly interest.  That works until money
arrives through a channel they never log.  The motivating case: Apple
Card **Daily Cash** lands directly in Apple Savings in small irregular
amounts, so fin's balance drifts low by roughly $50/month forever.

A ``Balance Anchor`` metadata row states the real balance on a date::

    Balance Anchor,2026-08-04,12345.67,Apple Savings,Apple Card Daily Cash

``apply_balance_anchors`` walks fin's own cash balance to that date and
synthesizes ONE ``Cash Back`` transaction for the difference.  Anchors
are processed chronologically and each sees the rows synthesized by
earlier anchors, so every anchor books only the drift accumulated since
the previous one — re-anchoring monthly gives a reasonable month-by-month
cash-back series rather than one lump.

**Restricted to cash accounts on purpose.**  For a cash account the
balance is exact arithmetic, so a delta unambiguously means missing
transactions and plugging it is safe.  For an account holding
securities, a delta could equally be a pricing error, a missing split,
or a basis bug — plugging that would paper over exactly the kind of bug
this codebase works hard to surface.  Anchors on non-cash accounts are
skipped with a warning.

A negative delta (fin's balance is HIGHER than the statement) is also
skipped: that direction means fin has transactions the account doesn't,
which is a data bug to investigate, not drift to absorb.
"""

from __future__ import annotations

from . import config
from .actions import NEUTRAL_ACTIONS, SUBTRACT_ACTIONS
from .config import CASH_SYMBOLS

# Only these account types have a fully-tracked USD balance (see the
# "USD balance tracking is deliberately skipped" invariant in
# CLAUDE.md).  Anchoring anything else would be plugging a number fin
# never claimed to track.
_ANCHORABLE_TYPES = frozenset({"Savings"})

# Below this, the drift is rounding noise rather than real money.
MIN_ANCHOR_DELTA = 0.01


def cash_balance_at(txns: list[dict], group: str, date: str) -> float:
    """fin's computed USD balance for ``group`` at end-of-day ``date``."""
    bal = 0.0
    for t in txns:
        if t.get("account_group") != group:
            continue
        if t.get("symbol") not in CASH_SYMBOLS:
            continue
        d = t.get("date", "")
        if not d or d > date:
            continue
        action = t.get("action", "")
        if action in NEUTRAL_ACTIONS:
            continue
        qty = float(t.get("quantity", 0) or 0)
        if action in SUBTRACT_ACTIONS:
            bal -= qty
        else:
            bal += qty
    return bal


def apply_balance_anchors(txns: list[dict], anchors: list[dict],
                          verbose: bool = True) -> list[dict]:
    """Return ``txns`` plus a ``Cash Back`` row per anchor that drifts.

    Must run AFTER action normalization and account tagging — the walk
    matches on canonical action names and ``account_group``.
    """
    if not anchors:
        return txns

    out: list[dict] = list(txns)
    booked = 0
    total = 0.0
    for anchor in sorted(anchors, key=lambda a: a.get("date", "")):
        group = anchor.get("account_group", "")
        date = anchor.get("date", "")
        target = float(anchor.get("amount", 0) or 0)
        if not group or not date:
            continue

        # Read through the module rather than a from-import: metadata.py
        # mutates ACCOUNT_TYPES at runtime, and binding the dict object
        # at import time goes stale whenever the module graph is
        # rebuilt (which the test conftest does per-test).
        acct_type = config.ACCOUNT_TYPES.get(group)
        if acct_type not in _ANCHORABLE_TYPES:
            if verbose:
                print(f"  Balance Anchor skipped for {group}: only "
                      f"{'/'.join(sorted(_ANCHORABLE_TYPES))} accounts have a "
                      f"fully-tracked cash balance (got {acct_type or 'unmapped'})")
            continue

        # Include rows synthesized by earlier anchors so each books
        # only the drift since the previous anchor.
        computed = cash_balance_at(out, group, date)
        delta = round(target - computed, 2)
        if abs(delta) < MIN_ANCHOR_DELTA:
            continue
        if delta < 0:
            if verbose:
                print(f"  Balance Anchor skipped for {group} @ {date}: fin's "
                      f"balance is ${-delta:,.2f} ABOVE the stated balance — "
                      f"that's a data bug to investigate, not drift to absorb")
            continue

        out.append({
            "date": date,
            "account": group,
            "account_group": group,
            "account_type": acct_type,
            "symbol": "USD",
            "action": "Cash Back",
            "raw_action": "Balance Anchor true-up",
            "quantity": delta,
            "price": 1.0,
            "fees": 0.0,
            "amount": delta,
            "description": (anchor.get("note")
                            or "Untracked deposits (Balance Anchor true-up)"),
            "source": "auto-balance-anchor",
        })
        booked += 1
        total += delta

    if booked and verbose:
        print(f"  Balance Anchor: booked {booked} Cash Back row(s) "
              f"totalling ${total:,.2f} of untracked deposits")
    return out


def apr_at(savings_apr: list[dict], group: str, date: str) -> float | None:
    """Latest declared APR for ``group`` at or before ``date``.

    Undated rows apply from the beginning of time, so a single
    ``Savings APR`` row with no date is a valid "this is the rate"
    declaration.
    """
    best = None
    best_date = ""
    for row in savings_apr:
        if row.get("account_group") != group:
            continue
        d = row.get("date", "") or ""
        if d and date and d > date:
            continue
        if best is None or d >= best_date:
            best, best_date = float(row.get("rate", 0) or 0), d
    return best
