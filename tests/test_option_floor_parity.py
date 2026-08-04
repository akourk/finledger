"""The option intrinsic floor must be applied by EVERY pricing path.

yfinance can't quote option contracts, so every valuation site falls
back to the last-traded premium and then floors it at intrinsic
(``prices.option_intrinsic``).  ``history.py``'s two walkers did this;
the header 1-day change, the daily-P&L window, and the TWR boundary
valuation did not.

That asymmetry is not a rounding difference — it silently fabricates
return.  A deep-ITM contract was marked at intrinsic by the snapshot
walker but pinned at its purchase premium by the 1-day reprice, so the
ENTIRE intrinsic-over-cost gain reprinted as "today's move" every
single day the contract stayed open, and vanished the day it was
closed.

These tests pin the parity rather than any particular figure.
"""

from __future__ import annotations

import inspect

import pytest


def test_every_txn_price_fallback_applies_the_floor():
    """Static guard: any site that falls back to a last-traded premium
    must also consult ``option_intrinsic``.

    Cheap to keep honest and it catches the failure mode that actually
    happened — a NEW valuation path copied from an existing one that
    predates the floor.
    """
    import src.history
    import src.analytics.header
    import src.analytics.daily_pnl
    import src.analytics._shared

    for mod in (src.history, src.analytics.header,
                src.analytics.daily_pnl, src.analytics._shared):
        src_text = inspect.getsource(mod)
        if "last_txn_price" not in src_text:
            continue
        assert "option_intrinsic" in src_text, (
            f"{mod.__name__} falls back to last_txn_price without applying "
            "the option intrinsic floor — see tests/test_option_floor_parity.py"
        )


def test_intrinsic_floor_only_raises(stub_prices):
    """The floor must never LOWER a mark: a contract trading above
    intrinsic (time value) keeps its traded premium."""
    from src.prices import option_intrinsic

    stub_prices.set("ACME", {"2026-08-03": 220.0})
    from src.prices import ensure_coverage
    ensure_coverage({"ACME"}, "2026-08-03", "2026-08-03")

    sym = "ACME 1/15/2028 Call $100.00"
    iv = option_intrinsic(sym, "2026-08-03")
    assert iv == pytest.approx(120.0)

    # Mirrors the fallback branch shared by all four pricing paths.
    def floored(traded_premium):
        fb = traded_premium
        if iv is not None and iv > (fb or 0):
            fb = iv
        return fb

    assert floored(50.0) == pytest.approx(120.0)   # stale premium → floored up
    assert floored(135.0) == pytest.approx(135.0)  # time value preserved


def test_header_and_snapshot_agree_on_an_open_option(stub_prices):
    """End-to-end parity: with an open deep-ITM contract, the header's
    yesterday-reprice and the snapshot walker must price it by the same
    rule, so the 1-day change reflects the underlying's actual move
    rather than the gap between two pricing conventions."""
    from src.history import compute_history
    from src.analytics.header import compute_header_summary

    # Underlying flat across both days → a correct 1-day change is ~0.
    stub_prices.set("ACME", {"2026-08-03": 220.0, "2026-08-04": 220.0})
    from src.prices import ensure_coverage
    ensure_coverage({"ACME"}, "2026-08-03", "2026-08-04")

    sym = "ACME 1/15/2028 Call $100.00"
    txns = [{
        "date": "2026-06-30", "account": "Robinhood",
        "account_group": "Robinhood", "account_type": "Taxable",
        "symbol": sym, "action": "Option Buy", "raw_action": "BTO",
        "quantity": 1.0, "price": 50.0, "fees": 0.0, "amount": 5000.0,
        "description": "", "source": "rh.csv",
    }]

    history = compute_history(txns, {sym: "Options"})
    assert history, "expected at least one snapshot"
    summary = compute_header_summary(txns, history, [], {"net_contributed": 5000.0})

    # The contract is worth intrinsic ($120 × 100) on both days, so the
    # day-over-day change must be ~$0 — NOT the $7,000 gap between
    # intrinsic and the $50 purchase premium.
    assert summary["change_1d"] == pytest.approx(0.0, abs=1.0)
