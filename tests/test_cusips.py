"""Tests for the CUSIP identity-resolution layer."""

from __future__ import annotations


class TestExtraction:
    def test_extracts_basic_cusip(self):
        from src.cusips import extract_cusip
        assert extract_cusip("Avidity Biosciences\nCUSIP: 05370A108") == "05370A108"

    def test_handles_inline_cusip(self):
        from src.cusips import extract_cusip
        assert extract_cusip("Twitter CUSIP: 90184L102") == "90184L102"

    def test_no_match(self):
        from src.cusips import extract_cusip
        assert extract_cusip("Just a description") is None
        assert extract_cusip("") is None
        assert extract_cusip(None) is None

    def test_uppercases(self):
        from src.cusips import extract_cusip
        assert extract_cusip("CUSIP: 05370a108") == "05370A108"


class TestRenameDetection:
    def test_cusip_with_two_tickers_flags_rename(self):
        """Same CUSIP under two different tickers → ticker rename."""
        from src.cusips import detect_collisions
        txns = [
            {"date": "2024-10-17", "symbol": "RNA",  "cusip": "05370A108"},
            {"date": "2025-12-04", "symbol": "RNAM", "cusip": "05370A108"},
        ]
        report = detect_collisions(txns)
        cands = report["rename_candidates"]
        assert len(cands) == 1
        assert cands[0]["cusip"] == "05370A108"
        assert set(cands[0]["tickers"]) == {"RNA", "RNAM"}
        # RNAM is the more recent → should be canonical
        assert cands[0]["canonical"] == "RNAM"

    def test_no_collision_for_distinct_cusips(self):
        from src.cusips import detect_collisions
        txns = [
            {"date": "2024-01-01", "symbol": "AAPL", "cusip": "037833100"},
            {"date": "2024-02-01", "symbol": "MSFT", "cusip": "594918104"},
        ]
        assert detect_collisions(txns)["rename_candidates"] == []


class TestReuseDetection:
    def test_ticker_with_two_cusips_flags_reuse(self):
        """Same ticker pointing to two different CUSIPs (Atrium taking
        over the RNA ticker after Avidity exited) flags as reuse."""
        from src.cusips import detect_collisions
        txns = [
            {"date": "2024-10-17", "symbol": "RNA", "cusip": "05370A108"},   # Avidity
            {"date": "2026-02-27", "symbol": "RNA", "cusip": "04965N104"},   # Atrium
        ]
        report = detect_collisions(txns)
        # ticker reuse appears in `reuse_candidates`
        assert any(r["ticker"] == "RNA" for r in report["reuse_candidates"])


class TestSuggestRules:
    def test_round_trip_to_rename_rule(self):
        from src.cusips import detect_collisions, suggest_rename_rules
        txns = [
            {"date": "2024-10-17", "symbol": "RNA",  "cusip": "05370A108"},
            {"date": "2025-12-04", "symbol": "RNAM", "cusip": "05370A108"},
        ]
        report = detect_collisions(txns)
        rules = suggest_rename_rules(report)
        assert len(rules) == 1
        rule = rules[0]
        assert rule["from"] == "RNA"
        assert rule["to"] == "RNAM"
        assert "before_date" in rule
        assert "auto-suggested" in rule["note"]
