"""What a position is worth on a given date — in one place.

Five call sites used to answer this question independently:
``history``'s snapshot walker, ``history.compute_daily_totals``,
``analytics/_shared._value_at_date``, ``analytics/daily_pnl`` and
``analytics/header``.  They agreed on the shape and drifted on the
details, which is the failure mode this module exists to end.  The
drift found when they were finally read side by side:

* ``_value_at_date``, ``daily_pnl`` and ``header`` omit
  ``contract_multiplier`` on the cache-priced branch while applying it on
  the transaction-price branch.  Latent rather than live — option
  symbols are multi-word, so ``_classify_no_fetch`` never fetches them
  and the cache branch cannot currently fire for a contract — but it is
  a 100x error waiting for the day one gets a price.
* ``history``'s inline dust rule is documented as mirroring
  ``pipeline_stages.is_dust`` "so history positions exactly match the
  holdings table".  It does not: ``is_dust`` also drops small NEGATIVE
  fractional priced positions (unpaired corporate-action surrenders),
  and the inline copy keeps them.
* The near-zero cutoff before valuing anything is ``1e-12`` in one
  place, ``1e-9`` in two others and absent in the rest.

Nothing here is new arithmetic.  It is the union of what those five
sites already did, with the disagreements resolved in favour of the
documented intent.

**The ladder**, in order:

1. **Cash** — price 1.0, quantity unchanged.
2. **Cache hit** — yfinance's ``Close``, which is always split-adjusted
   regardless of ``auto_adjust``.  The stored series is therefore in
   TODAY's share basis, so an as-of-date quantity is restated forward by
   ``split_factor_since`` before multiplying.  See CLAUDE.md: never
   multiply a historical balance by a historical cache price without it.
3. **Cache miss** — the most recent transaction price at or before the
   date.  Transaction prices are as-of-trade, so the quantity is NOT
   restated.  Floored at the option contract's intrinsic value, because
   yfinance cannot quote contracts and a last-traded premium goes stale
   between trades; without the floor a deep-ITM contract sits flat while
   the underlying moves.
4. **Nothing** — the position is unpriceable and contributes no value.
   Callers report this through ``priced_pct`` rather than substituting a
   number.

``restate_qty`` selects between the two quantity bases.  It is ``True``
for a balance walked to a past date (history, TWR boundaries) and
``False`` when the quantity already came from today's positions
(``daily_pnl``, ``header``) — restating there would double-adjust every
date before a recent split.
"""

from __future__ import annotations

from typing import NamedTuple

from .config import CASH_SYMBOLS, contract_multiplier
from .prices import get_price, option_intrinsic, split_factor_since

# Below this, a quantity is float noise rather than a position.  The old
# sites used 1e-12, 1e-9 and nothing; 1e-9 is the middle and is well
# under the 1e-6 the dust filter already treats as absent.
QTY_EPSILON = 1e-9


class Mark(NamedTuple):
    """A position's resolved price and value on one date.

    ``qty`` is the quantity in the same share basis as ``price``, which
    is what makes ``value`` a straight product — a caller that needs to
    display the pair gets a consistent one.  ``source`` names which rung
    of the ladder answered, for diagnostics and for callers that report
    price coverage.
    """

    price: float | None
    qty: float
    value: float | None
    source: str          # cash | cache | txn | intrinsic | none

    @property
    def priced(self) -> bool:
        return self.value is not None



def mark(symbol: str, qty: float, on_date: str,
         last_txn_price: dict[str, float] | None = None,
         *, restate_qty: bool = True,
         price_cache: dict[str, float | None] | None = None) -> Mark:
    """Value ``qty`` of ``symbol`` on ``on_date``.

    ``price_cache`` is an optional per-date memo for ``get_price``.  A
    day walker asks for the same symbol once per account group holding
    it; passing a dict scoped to one date collapses those to one lookup.
    It must not outlive the date, or a stale price leaks forward.
    """
    if symbol in CASH_SYMBOLS:
        return Mark(1.0, qty, qty, "cash")

    if price_cache is not None and symbol in price_cache:
        cache_px = price_cache[symbol]
    else:
        cache_px = get_price(symbol, on_date)
        if price_cache is not None:
            price_cache[symbol] = cache_px

    if cache_px is not None:
        adj_qty = qty * split_factor_since(symbol, on_date) if restate_qty else qty
        return Mark(cache_px, adj_qty,
                    adj_qty * cache_px * contract_multiplier(symbol), "cache")

    # Transaction-price fallback, floored at intrinsic.  Kept in this
    # order so a contract whose last trade predates a big move in the
    # underlying still tracks it.
    price = (last_txn_price or {}).get(symbol)
    source = "txn"
    iv = option_intrinsic(symbol, on_date)
    if iv is not None and iv > (price or 0):
        price, source = iv, "intrinsic"

    if price is None or price <= 0:
        return Mark(price, qty, None, "none")
    return Mark(price, qty, qty * price * contract_multiplier(symbol), source)


def is_dust(qty: float, price: float) -> bool:
    """Whether a (qty, price) pair is too small to be a real position.

    Two regimes:

    1. **With a price**: dust if the dollar value rounds below a penny,
       OR (defensively) if it's a small NEGATIVE fractional position
       under $200 — those are corporate-action artifacts (SPR surrender
       without a paired receive, CIL on a ticker we don't have full
       history for).  A real held position is always positive and
       >=1 share for stocks the cache can price.

    2. **Without a price**: dust if quantity is below 1e-6, OR if it's
       negative — broker CSVs leave residuals like 7.7e-9 from
       precision mismatches between paired Wrap/Sell rows, and orphan
       option-exercise rows can push contract counts negative
       indefinitely.

    Lives here rather than in ``pipeline_stages`` because the history
    walkers need it too, and their inline copy had already drifted from
    it.  ``pipeline_stages`` re-exports the name.
    """
    if price > 0:
        if qty < 0 and abs(qty) < 1.0 and abs(qty * price) < 200:
            return True
        return abs(qty * price) < 0.01
    if qty < 0:
        return True
    return abs(qty) < 1e-6


def mark_is_dust(m: Mark, qty: float) -> bool:
    """``is_dust`` against a ``Mark``.

    Takes the RAW quantity, not the mark's restated one: the dust
    thresholds are about the position as held, and a pre-split balance
    scaled forward would clear a share-count threshold it should not.
    Price may be None, which ``is_dust`` reads as the unpriced regime.
    """
    return is_dust(qty, m.price or 0.0)
