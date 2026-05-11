"""Robinhood corporate-action handling regression tests.

Each test sets up a small synthetic Robinhood CSV exercising one
corporate-action pattern we've encountered, runs the parser, and
asserts the canonical txn output is what we expect.  These exist to
prevent regressions when the parser is refactored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import write_robinhood_csv


def _parse(path: Path) -> list[dict]:
    from src.parsers import parse_robinhood
    return parse_robinhood(path)


def _balance(txns: list[dict], symbol: str) -> float:
    """Walk balance for a single symbol, applying the same SUB/NEUTRAL
    rules main.py uses."""
    SUB = {"Sell", "Withdrawal", "Transfer Out", "Distribution",
           "Fee", "Tax", "Option Sell", "Option Expire", "Option Exercise",
           "Contribution Reversal"}
    NEU = {"Neutral"}
    bal = 0.0
    for t in txns:
        if t["symbol"] != symbol:
            continue
        action = t.get("action", "")
        qty = float(t.get("quantity", 0) or 0)
        if action in NEU:
            continue
        if action in SUB:
            bal -= qty
        else:
            bal += qty
    return bal


# ---------------------------------------------------------------------------
# Cash-only mergers (TWTR, TPTX, AKRO, VRNA, RNAM pattern)
# ---------------------------------------------------------------------------

class TestCashMerger:
    def test_mrgs_plus_mrgc_emits_single_sell(self, isolated_workdir):
        """A MRGS surrender paired with an MRGC cash receipt collapses
        to one Sell at the implied per-share price.  Balance zeros."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/1/2024", "Trans Code": "Buy",
             "Instrument": "TWTR", "Description": "Twitter",
             "Quantity": "2", "Price": "$50.00", "Amount": "($100.00)"},
            {"Activity Date": "10/31/2024", "Trans Code": "MRGS",
             "Instrument": "TWTR", "Description": "Twitter",
             "Quantity": "2S"},
            {"Activity Date": "10/31/2024", "Trans Code": "MRGC",
             "Instrument": "TWTR",
             "Description": "Cash received thru Merger 2 shares at $54.2",
             "Amount": "$120.00"},
        ])
        txns = _parse(csv)
        sells = [t for t in txns if t["symbol"] == "TWTR" and t["action"] == "Sell"]
        assert len(sells) == 1, f"expected 1 Sell, got {len(sells)}"
        assert sells[0]["quantity"] == 2.0
        assert sells[0]["amount"] == 120.00
        assert sells[0]["price"] == pytest.approx(54.20)
        assert _balance(txns, "TWTR") == pytest.approx(0.0)

    def test_mrgs_without_mrgc_emits_zero_proceeds_sell(self, isolated_workdir):
        """A MRGS surrender with no paired MRGC (stock-for-stock merger
        partner side) emits a Sell at $0 proceeds.  Balance still
        decrements correctly."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/1/2022", "Trans Code": "Buy",
             "Instrument": "XLNX", "Description": "Xilinx",
             "Quantity": "1", "Price": "$200.00", "Amount": "($200.00)"},
            {"Activity Date": "2/15/2022", "Trans Code": "MRGS",
             "Instrument": "XLNX", "Description": "Xilinx", "Quantity": "1S"},
        ])
        txns = _parse(csv)
        assert _balance(txns, "XLNX") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Stock-for-stock merger receive (XLNX → AMD)
# ---------------------------------------------------------------------------

class TestStockMerger:
    def test_mrgs_receive_adds_shares(self, isolated_workdir):
        """MRGS with a no-S quantity = shares received in stock-for-stock
        merger.  Adds to the new symbol's balance at $0 cost basis."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/1/2022", "Trans Code": "Buy",
             "Instrument": "XLNX", "Description": "Xilinx",
             "Quantity": "1", "Price": "$200.00", "Amount": "($200.00)"},
            {"Activity Date": "2/15/2022", "Trans Code": "MRGS",
             "Instrument": "XLNX", "Description": "Xilinx", "Quantity": "1S"},
            {"Activity Date": "2/15/2022", "Trans Code": "MRGS",
             "Instrument": "AMD", "Description": "AMD", "Quantity": "1"},
        ])
        txns = _parse(csv)
        assert _balance(txns, "XLNX") == pytest.approx(0.0)
        assert _balance(txns, "AMD") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CIL — three flavors (held, never-held, merger-fractional)
# ---------------------------------------------------------------------------

class TestCashInLieu:
    def test_cil_on_held_symbol_emits_sell(self, isolated_workdir):
        """CIL on a symbol the user has been buying = legitimate
        fractional-share cash-out.  Emits a Sell."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/1/2024", "Trans Code": "Buy",
             "Instrument": "FOO", "Description": "Foo Corp",
             "Quantity": "10", "Price": "$5.00", "Amount": "($50.00)"},
            {"Activity Date": "6/1/2024", "Trans Code": "CIL",
             "Instrument": "FOO",
             "Description": "CIL on 0.5 @ $6.00 - FOO",
             "Amount": "$3.00"},
        ])
        txns = _parse(csv)
        cil_sells = [t for t in txns
                     if t["symbol"] == "FOO" and t["action"] == "Sell"
                     and "Cash in lieu" in (t.get("description") or "")]
        assert len(cil_sells) == 1
        assert cil_sells[0]["quantity"] == pytest.approx(0.5)

    def test_cil_on_unheld_symbol_emits_dividend(self, isolated_workdir):
        """CIL for a spin-off warrant the user never held as shares —
        emits a USD Dividend instead of a phantom-negative Sell."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "11/28/2025", "Trans Code": "CIL",
             "Instrument": "OPENW",
             "Description": "CIL on 0.833 @ $1.95 - OPENW",
             "Amount": "$1.62"},
        ])
        txns = _parse(csv)
        # Should emit a USD Dividend, NOT a Sell on OPENW
        assert _balance(txns, "OPENW") == pytest.approx(0.0)
        usd_divs = [t for t in txns
                    if t["symbol"] == "USD" and t["action"] == "Dividend"]
        assert any(t["amount"] == pytest.approx(1.62) for t in usd_divs)

    def test_cil_after_merger_receive_emits_dividend(self, isolated_workdir):
        """CIL within 30 days of a MRGS-receive on the same symbol is
        the cash-out for the unissued fractional share — those shares
        never hit the user's balance, so emit Dividend, not Sell."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "2/15/2022", "Trans Code": "MRGS",
             "Instrument": "XLNX", "Description": "Xilinx", "Quantity": "1S"},
            {"Activity Date": "2/15/2022", "Trans Code": "MRGS",
             "Instrument": "AMD", "Description": "AMD", "Quantity": "1"},
            {"Activity Date": "2/22/2022", "Trans Code": "CIL",
             "Instrument": "AMD",
             "Description": "CIL on 0.723 @ $100.00 - AMD",
             "Amount": "$84.72"},
        ])
        txns = _parse(csv)
        assert _balance(txns, "AMD") == pytest.approx(1.0), \
            "AMD balance should be exactly 1 (whole share from MRGS)"
        merger_divs = [t for t in txns
                       if t["symbol"] == "USD" and t["action"] == "Dividend"
                       and "Stock-for-stock CIL" in (t.get("description") or "")]
        assert len(merger_divs) == 1
        assert merger_divs[0]["amount"] == pytest.approx(84.72)


# ---------------------------------------------------------------------------
# LIQ — cash liquidation of CVRs
# ---------------------------------------------------------------------------

class TestLiquidation:
    def test_liq_parses_qty_from_description(self, isolated_workdir):
        """LIQ rows have empty Quantity field; parser must extract qty
        from "Cash Liquidation N shares at $X" description so the Sell
        actually removes the shares."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "11/8/2023", "Trans Code": "SOFF",
             "Instrument": "FREQ^", "Description": "Frequency CVR",
             "Quantity": "11"},
            {"Activity Date": "6/4/2024", "Trans Code": "LIQ",
             "Instrument": "FREQ^",
             "Description": "Cash Liquidation 11 shares at 0.01500000",
             "Amount": "$0.21"},
        ])
        txns = _parse(csv)
        sells = [t for t in txns if t["symbol"] == "FREQ^" and t["action"] == "Sell"]
        assert len(sells) == 1
        assert sells[0]["quantity"] == 11.0
        assert _balance(txns, "FREQ^") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Option contract symbols (BTO/STC/OEXP/OEXCS, plus CONV with option desc)
# ---------------------------------------------------------------------------

class TestOptionSymbols:
    def test_bto_uses_contract_symbol(self, isolated_workdir):
        """BTO writes shares to the OPTION-CONTRACT symbol, not the
        underlying.  Otherwise option basis would mix with stock basis."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "1/15/2024", "Trans Code": "BTO",
             "Instrument": "AAPL",
             "Description": "AAPL 3/15/2024 Call $200.00",
             "Quantity": "1", "Price": "$5.00", "Amount": "($500.00)"},
        ])
        txns = _parse(csv)
        assert all(t["symbol"] != "AAPL" for t in txns)
        assert any(" Call " in t["symbol"] for t in txns)

    def test_conv_with_option_description_uses_contract_symbol(self, isolated_workdir):
        """The 2018 Apex→RHS migration emitted CONV rows for option
        contracts — the description has the contract details.  Our
        parser must treat these as options, not adds to the underlying
        stock's balance."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "11/9/2018", "Trans Code": "CONV",
             "Instrument": "AMD",
             "Description": "AMD 11/9/2018 Put $15.50",
             "Quantity": "1"},
            {"Activity Date": "11/9/2018", "Trans Code": "OEXP",
             "Instrument": "AMD",
             "Description": "AMD 11/9/2018 Put $15.50",
             "Quantity": ""},
        ])
        txns = _parse(csv)
        assert _balance(txns, "AMD") == pytest.approx(0.0), \
            "AMD stock should not gain a share from the option CONV"


# ---------------------------------------------------------------------------
# Quantity parsing (option-style "1S" markers)
# ---------------------------------------------------------------------------

class TestQuantityParsing:
    def test_oexcs_parses_1s_quantity(self, isolated_workdir):
        """OEXCS quantity field uses Robinhood's "1S" notation (1
        contract, short-leg marker).  Plain float() fails — the parser
        must strip non-numeric suffixes."""
        csv = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(csv, [
            {"Activity Date": "12/15/2023", "Trans Code": "BTO",
             "Instrument": "AAPL",
             "Description": "AAPL 12/15/2023 Call $200.00",
             "Quantity": "1", "Price": "$5.00", "Amount": "($500.00)"},
            {"Activity Date": "12/15/2023", "Trans Code": "OEXCS",
             "Instrument": "AAPL",
             "Description": "AAPL 12/15/2023 Call $200.00",
             "Quantity": "1S"},
        ])
        txns = _parse(csv)
        oexcs = [t for t in txns if t["action"] == "OEXCS"]
        assert len(oexcs) == 1
        assert oexcs[0]["quantity"] == 1.0
