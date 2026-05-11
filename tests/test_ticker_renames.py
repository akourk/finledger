"""Ticker rename layer tests — ensures retroactive ticker relabeling
(e.g. RNA→RNAM) collapses to one canonical symbol pre-dedup."""

from __future__ import annotations

import json

import pytest

from .conftest import write_robinhood_csv


class TestTickerRenameLayer:
    def test_rename_applies_with_date_boundary(self, isolated_workdir):
        """The rename map's `before_date` correctly windows the rewrite —
        RNA before 2026-02-27 → RNAM, RNA on/after 2026-02-27 stays
        RNA (different entity, Atrium Therapeutics)."""
        # Drop a rename rule into the cache directory
        rename_path = isolated_workdir / "cache" / "ticker_renames.json"
        rename_path.write_text(json.dumps({
            "Robinhood": [
                {"from": "RNA", "to": "RNAM",
                 "before_date": "2026-02-27",
                 "note": "test rule"},
            ],
        }))

        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "10/17/2024", "Trans Code": "Buy",
             "Instrument": "RNA", "Description": "Avidity Biosciences",
             "Quantity": "10", "Price": "$49.50", "Amount": "($495.00)"},
            {"Activity Date": "12/4/2025", "Trans Code": "SLIP",
             "Instrument": "RNA", "Description": "Stock Lending",
             "Amount": "$0.01"},
            {"Activity Date": "2/27/2026", "Trans Code": "SOFF",
             "Instrument": "RNA", "Description": "Atrium Therapeutics",
             "Quantity": "1"},
            {"Activity Date": "4/8/2026", "Trans Code": "SLIP",
             "Instrument": "RNA", "Description": "Stock Lending",
             "Amount": "$0.01"},
        ])

        from src.parsers import parse_robinhood
        txns = parse_robinhood(csv)
        # All pre-2026-02-27 RNA events should be RNAM
        # All on-or-after 2026-02-27 RNA events stay RNA
        for t in txns:
            if t["date"] < "2026-02-27":
                assert t["symbol"] == "RNAM", \
                    f"pre-cutoff: {t['date']} {t['action']} should be RNAM, got {t['symbol']}"
            else:
                assert t["symbol"] == "RNA", \
                    f"post-cutoff: {t['date']} {t['action']} should be RNA, got {t['symbol']}"

    def test_no_rename_file_passes_through(self, isolated_workdir):
        """When no ticker_renames.json exists, symbols pass through
        unchanged."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/1/2024", "Trans Code": "Buy",
             "Instrument": "FOO", "Description": "Foo Corp",
             "Quantity": "1", "Price": "$10.00", "Amount": "($10.00)"},
        ])
        from src.parsers import parse_robinhood
        txns = parse_robinhood(csv)
        assert all(t["symbol"] == "FOO" for t in txns)


class TestDedupeWithRename:
    def test_same_event_different_tickers_dedupes(self, isolated_workdir):
        """The same physical SLIP event in two CSV exports — one under
        the OLD ticker (RNA) and one under the NEW (RNAM) — should
        collapse to a single txn after dedup, given the rename rule
        normalizes both to RNAM."""
        rename_path = isolated_workdir / "cache" / "ticker_renames.json"
        rename_path.write_text(json.dumps({
            "Robinhood": [
                {"from": "RNA", "to": "RNAM", "before_date": "2026-02-27"},
            ],
        }))

        csv_old = isolated_workdir / "data" / "robinhood-1.csv"
        csv_new = isolated_workdir / "data" / "robinhood-2.csv"
        write_robinhood_csv(csv_old, [
            {"Activity Date": "12/4/2025", "Trans Code": "SLIP",
             "Instrument": "RNA", "Description": "Stock Lending",
             "Amount": "$0.01"},
        ])
        write_robinhood_csv(csv_new, [
            {"Activity Date": "12/4/2025", "Trans Code": "SLIP",
             "Instrument": "RNAM", "Description": "Stock Lending",
             "Amount": "$0.01"},
        ])

        from src.parsers import parse_robinhood
        from src.export import deduplicate
        all_txns = parse_robinhood(csv_old) + parse_robinhood(csv_new)
        deduped, removed = deduplicate(all_txns)
        # Both rows should hash identically after rename → 1 kept, 1 removed
        slip_rows = [t for t in deduped
                     if t["symbol"] == "RNAM" and "Stock Lending" in (t.get("description") or "")]
        assert len(slip_rows) == 1
        assert removed == 1
