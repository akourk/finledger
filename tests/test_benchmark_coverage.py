"""Benchmark tickers must keep fetching even when briefly held.

``compute_position_endings`` clamps the price-fetch range of any symbol
whose final balance is zero — right for a delisted holding nobody owns
anymore, wrong for a BENCHMARK, whose series has to run through today
regardless of what the user holds.

Real failure mode: a benchmark ticker was briefly held and then sold.
That made it a "closed position", clamped its fetch range to the sell
date, and silently froze the benchmark overlay at zero for every
snapshot after it — with `failure_count: 0`, so nothing flagged it.
The same trap applies to SPY, which backs the headline "Vs SPY" figure
on every performance view.

Detection tip: a cached symbol with a stale `covered_end` but
`failure_count: 0` was never REQUESTED — it didn't fail.
"""

from __future__ import annotations

from src.config import BENCHMARK_SYMBOLS
from src.pipeline_stages import compute_position_endings


def _txn(date, symbol, action, qty, amount=0.0, price=0.0):
    return {
        "date": date, "account": "Robinhood", "account_group": "Robinhood",
        "account_type": "Taxable", "symbol": symbol, "action": action,
        "raw_action": action, "quantity": qty, "price": price,
        "fees": 0.0, "amount": amount, "description": "", "source": "rh.csv",
    }


def test_benchmark_symbols_are_defined():
    assert "SPY" in BENCHMARK_SYMBOLS
    assert "VXUS" in BENCHMARK_SYMBOLS
    assert "BND" in BENCHMARK_SYMBOLS


def test_held_then_sold_benchmark_is_a_closed_position():
    """Baseline: the clamp genuinely fires on a bought-then-sold
    benchmark.  If this stops being true the exemption below is
    vacuous and the test would silently stop protecting anything."""
    txns = [
        _txn("2024-01-15", "VXUS", "Buy", 10.0, amount=600.0, price=60.0),
        _txn("2024-02-15", "VXUS", "Sell", 10.0, amount=620.0, price=62.0),
    ]
    closed, _trivial = compute_position_endings(txns)
    assert closed.get("VXUS") == "2024-02-15"


def test_main_exempts_benchmarks_from_the_fetch_clamp():
    """main.py must drop benchmarks from the clamp map before passing
    it to ensure_coverage.  Asserted against the real source so a
    refactor that loses the exemption fails here."""
    import inspect
    import src.main

    src_text = inspect.getsource(src.main)
    assert "closed_position_ends.pop(" in src_text, (
        "main.py no longer exempts benchmark symbols from the "
        "closed-position fetch clamp — a bought-then-sold benchmark "
        "will freeze its overlay at zero"
    )
    assert "trivial -= set(_BENCHMARK_SYMBOLS)" in src_text, (
        "main.py no longer exempts benchmark symbols from the "
        "trivial-symbol filter"
    )


def test_benchmarks_survive_both_filters():
    """A fractional benchmark position that closes would otherwise trip
    BOTH the closed-position clamp and the trivial-symbol filter."""
    txns = [
        _txn("2024-01-15", "SPY", "Buy", 0.5, amount=300.0, price=600.0),
        _txn("2024-02-15", "SPY", "Sell", 0.5, amount=310.0, price=620.0),
    ]
    closed, trivial = compute_position_endings(txns)
    # Both filters catch it...
    assert "SPY" in closed or "SPY" in trivial
    # ...so main.py's exemption is what keeps the benchmark alive.
    for b in BENCHMARK_SYMBOLS:
        closed.pop(b, None)
    trivial -= set(BENCHMARK_SYMBOLS)
    assert "SPY" not in closed and "SPY" not in trivial
