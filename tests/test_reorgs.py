"""Unit tests for src/reorgs.py — pure-function corp-action helpers."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Quantity parsing
# ---------------------------------------------------------------------------

class TestQtyMarker:
    def test_no_suffix_is_received(self):
        from src.reorgs import parse_qty_with_surrender_marker
        assert parse_qty_with_surrender_marker("1") == (1.0, False)
        assert parse_qty_with_surrender_marker("0.6000") == (0.6000, False)

    def test_s_suffix_is_surrender(self):
        from src.reorgs import parse_qty_with_surrender_marker
        assert parse_qty_with_surrender_marker("1S") == (1.0, True)
        assert parse_qty_with_surrender_marker("10S") == (10.0, True)

    def test_empty_returns_zero_received(self):
        from src.reorgs import parse_qty_with_surrender_marker
        assert parse_qty_with_surrender_marker("") == (0.0, False)
        assert parse_qty_with_surrender_marker(None) == (0.0, False)

    def test_unparseable_returns_zero_with_marker_preserved(self):
        from src.reorgs import parse_qty_with_surrender_marker
        # "junkS" → can't parse the body, but we still know it's a surrender
        assert parse_qty_with_surrender_marker("junkS") == (0.0, True)


# ---------------------------------------------------------------------------
# Description parsers
# ---------------------------------------------------------------------------

class TestCILParser:
    def test_basic(self):
        from src.reorgs import parse_cil_description
        assert parse_cil_description("CIL on 0.723 @ $100.00 - AMD") == (0.723, 100.00)

    def test_with_thousand_separator(self):
        from src.reorgs import parse_cil_description
        assert parse_cil_description("CIL on 0.5 @ $1,234.56 - FOO") == (0.5, 1234.56)

    def test_no_match_returns_zeros(self):
        from src.reorgs import parse_cil_description
        assert parse_cil_description("not a CIL row") == (0.0, 0.0)


class TestLIQParser:
    def test_basic(self):
        from src.reorgs import parse_liq_description
        assert parse_liq_description(
            "Cash Liquidation 11 shares at 0.01500000"
        ) == (11.0, 0.01500000)

    def test_singular_share(self):
        from src.reorgs import parse_liq_description
        assert parse_liq_description("Cash Liquidation 1 share at 5.00") == (1.0, 5.00)

    def test_no_match_returns_zeros(self):
        from src.reorgs import parse_liq_description
        assert parse_liq_description("Stock Lending") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Pool indexers
# ---------------------------------------------------------------------------

def _row(code: str, sym: str, qty: str = "", amount: str = ""):
    return {"Trans Code": code, "Instrument": sym,
            "Quantity": qty, "Amount": amount}


class TestPoolBuilders:
    def test_mrgc_pool_keys_by_date_and_symbol(self):
        from src.reorgs import build_mrgc_pool
        rows = [
            ("2024-01-01", _row("MRGC", "TWTR", amount="$120.00")),
            ("2024-01-01", _row("MRGS", "TWTR", qty="2S")),
            ("2024-02-01", _row("MRGC", "TPTX", amount="$76.00")),
        ]
        pool = build_mrgc_pool(rows)
        assert ("2024-01-01", "TWTR") in pool
        assert ("2024-02-01", "TPTX") in pool
        # MRGS rows are not in the MRGC pool
        assert all(r.get("Trans Code") == "MRGC"
                   for rs in pool.values() for r in rs)

    def test_mrgs_receive_dates_filter_to_no_S(self):
        """Only no-S rows count as receives."""
        from src.reorgs import build_mrgs_receive_dates
        rows = [
            ("2022-02-15", _row("MRGS", "XLNX", qty="1S")),    # surrender
            ("2022-02-15", _row("MRGS", "AMD",  qty="1")),     # receive
        ]
        out = build_mrgs_receive_dates(rows)
        assert "AMD" in out
        assert "XLNX" not in out

    def test_held_at_some_point_includes_buys_and_receives(self):
        from src.reorgs import build_held_at_some_point
        rows = [
            ("2024-01-01", _row("Buy",  "AAPL", qty="10")),
            ("2024-02-01", _row("MRGS", "AMD",  qty="1")),    # receive
            ("2024-03-01", _row("MRGS", "XLNX", qty="1S")),   # surrender — NOT held
            ("2024-04-01", _row("CIL",  "OPENW", amount="$1.62")),  # standalone CIL
        ]
        held = build_held_at_some_point(rows)
        assert "AAPL" in held
        assert "AMD" in held
        assert "XLNX" not in held
        assert "OPENW" not in held


# ---------------------------------------------------------------------------
# CIL classification
# ---------------------------------------------------------------------------

class TestMergerFractionalDetection:
    def test_within_30_days_after_merger_receive(self):
        from src.reorgs import is_cil_from_recent_merger
        receive_dates = {"AMD": ["2022-02-15"]}
        # 7 days after — within window
        assert is_cil_from_recent_merger("AMD", "2022-02-22", receive_dates)
        # 60 days after — outside
        assert not is_cil_from_recent_merger("AMD", "2022-04-15", receive_dates)
        # Before the merger receive — invalid (negative delta)
        assert not is_cil_from_recent_merger("AMD", "2022-02-01", receive_dates)

    def test_no_receive_for_symbol_returns_false(self):
        from src.reorgs import is_cil_from_recent_merger
        assert not is_cil_from_recent_merger("FOO", "2022-02-22", {})


# ---------------------------------------------------------------------------
# Option-description heuristic
# ---------------------------------------------------------------------------

class TestOptionDescription:
    def test_call_and_put_match(self):
        from src.reorgs import is_option_description
        assert is_option_description("AAPL 3/15/2024 Call $200.00")
        assert is_option_description("META 12/18/2026 Put $800.00")

    def test_stock_descriptions_dont_match(self):
        from src.reorgs import is_option_description
        assert not is_option_description("Apple Inc")
        assert not is_option_description("Avidity Biosciences")
        assert not is_option_description("")
