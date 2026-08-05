"""Typed shapes for the core data structures passed between pipeline
stages.  These exist primarily for IDE support and documentation —
the runtime code uses plain dicts so existing patterns keep working.

Import ``Transaction`` anywhere you want autocomplete on ``t["date"]``,
``t["symbol"]`` etc., or want a compile-time hint that a function
consumes / produces txns.  A ``TypedDict`` doesn't enforce at runtime,
but mypy/pyright will flag misspelled keys (``t["symbl"]``) and
wrong-type assignments during editing and review.

All shapes are defined with ``total=False`` — every field is
technically optional at runtime because:

- Parsers build up txns progressively (some fields fill in during
  later stages like the basis walker annotating ``realized_gain``).
- Broker CSVs sometimes lack fields our schema assumes.

Downstream code should still check ``t.get(key)`` for anything that
might be absent.
"""

from __future__ import annotations

from typing import Optional, TypedDict


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class Transaction(TypedDict, total=False):
    """A single canonical transaction — the common schema every
    broker parser emits.

    **Core fields (always populated by the parsers):**

    - ``date``        ISO date (``YYYY-MM-DD``)
    - ``account``     raw broker account name (parser output)
    - ``symbol``      ticker or asset symbol (post-ticker-rename)
    - ``action``      raw broker action code (pre-normalization)
    - ``quantity``    non-negative — direction encoded in ``action``
    - ``price``       per-share / per-unit price (non-negative)
    - ``fees``        transaction fees (non-negative, usually 0)
    - ``amount``      total dollar amount (non-negative)
    - ``description`` broker memo / note
    - ``source``      CSV filename for traceability

    **Enriched by later pipeline stages:**

    - ``cusip``          extracted from description when present
    - ``account_group``  tagged by main.py via ``ACCOUNT_GROUPS``
    - ``account_type``   tagged by main.py via ``ACCOUNT_TYPES``
    - ``raw_action``     the pre-normalized action string
    - ``balance``        running balance after this txn
    - ``value``          dollar value at this date
    - ``cost_basis``     FIFO basis (annotated by basis walker)
    - ``realized_gain``  realized gain on Sell (basis walker)
    - ``holding_days``   days held (basis walker, for short/long-term)
    - ``basis_effect``   classification used by the walker
    - ``seq``            ingest-order index; the lot walkers' final sort
                         tie-break, so same-day rows are relieved in the
                         same sequence on every pipeline path (see
                         ``pipeline_stages.assign_ingest_seq``)
    """
    # Core (always present after parsing)
    date:        str
    account:     str
    symbol:      str
    action:      str
    quantity:    float
    price:       float
    fees:        float
    amount:      float
    description: str
    source:      str

    # Enriched by later stages
    cusip:          Optional[str]
    account_group:  str
    account_type:   str
    raw_action:     str
    balance:        float
    value:          Optional[float]
    cost_basis:     Optional[float]
    realized_gain:  Optional[float]
    holding_days:   Optional[int]
    basis_effect:   str
    seq:            int


# ---------------------------------------------------------------------------
# Holding (one row of holdings_by_account or holdings)
# ---------------------------------------------------------------------------

class Holding(TypedDict, total=False):
    """A single holding row — one per (account_group, symbol) at
    current time, or per symbol in the aggregated view.
    """
    account_group:   str
    account_type:    str
    symbol:          str
    sector:          str
    quantity:        float
    price:           float
    value:           Optional[float]
    cost_basis:      Optional[float]
    unrealized_gain: Optional[float]


# ---------------------------------------------------------------------------
# History snapshot (one entry in history[])
# ---------------------------------------------------------------------------

class Snapshot(TypedDict, total=False):
    """A single point-in-time portfolio snapshot.

    Emitted by ``src/history.compute_history`` at monthly boundaries
    plus today.  See CLAUDE.md step 14 for the full field list.
    """
    date:                str
    total:               float
    by_account_group:    dict[str, float]
    by_account_type:     dict[str, float]
    by_sector:           dict[str, float]
    total_cost_basis:    float
    cost_basis_by_group: dict[str, float]
    cost_basis_by_type:  dict[str, float]
    priced_pct:          float
    benchmark_spy:       float
    benchmark_spy_price: Optional[float]
    net_contributed:     float
    positions:           list[Holding]


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------

__all__ = ["Transaction", "Holding", "Snapshot"]
