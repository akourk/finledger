"""Build a synthetic but realistic multi-broker portfolio for end-to-end
pipeline testing.

Covers the edge cases that have bitten us in production:

- Robinhood stock buys + options (BTO / OEXP / OEXCS+OCC pairing)
- Cash-only merger (MRGS + MRGC → realized Sell)
- Stock-for-stock merger (MRGS XLNX → MRGS AMD + CIL fractional)
- Retroactive ticker rename applied via ticker_renames.json
- CUSIP embedded in Robinhood descriptions (for collision detection)
- Voya 401K with CONTRIBUTION REVERSAL (negative-units rollback)
- Apple Savings interest income

The test harness invokes this function, writes the CSVs and config
files to a tmp dir, runs the full pipeline, and asserts the output
against known-good values.  When you legitimately change the
algorithm, update the assertions (snapshot semantics without the full
file-compare cost — targeted pinning of specific figures).
"""

from __future__ import annotations

import json
from pathlib import Path


def build(tmp: Path) -> None:
    """Write a complete synthetic portfolio into ``tmp``.

    Creates ``tmp/data/*.csv`` with one file per broker, and
    ``tmp/cache/ticker_renames.json`` for the rename-layer test.
    """
    from tests.conftest import write_robinhood_csv, write_voya_csv

    # Robinhood — stock buys, a cash merger, and a stock-for-stock
    # merger with CIL.  CUSIPs embedded so the collision detector
    # has something to work with.
    write_robinhood_csv(tmp / "data" / "robinhood-1.csv", [
        # Plain equity positions
        {"Activity Date": "1/15/2023", "Trans Code": "Buy",
         "Instrument": "AAPL",
         "Description": "Apple Inc\nCUSIP: 037833100",
         "Quantity": "10", "Price": "$150.00", "Amount": "($1500.00)"},
        {"Activity Date": "6/1/2023", "Trans Code": "Buy",
         "Instrument": "AAPL",
         "Description": "Apple Inc\nCUSIP: 037833100",
         "Quantity": "5",  "Price": "$180.00", "Amount": "($900.00)"},
        {"Activity Date": "11/1/2023", "Trans Code": "Sell",
         "Instrument": "AAPL",
         "Description": "Apple Inc\nCUSIP: 037833100",
         "Quantity": "3",  "Price": "$200.00", "Amount": "$600.00"},

        # Cash-only merger (TWTR-style)
        {"Activity Date": "1/1/2024", "Trans Code": "Buy",
         "Instrument": "TWTR",
         "Description": "Twitter Inc\nCUSIP: 90184L102",
         "Quantity": "2",  "Price": "$50.00", "Amount": "($100.00)"},
        {"Activity Date": "10/31/2024", "Trans Code": "MRGS",
         "Instrument": "TWTR",
         "Description": "Twitter Inc\nCUSIP: 90184L102",
         "Quantity": "2S"},
        {"Activity Date": "10/31/2024", "Trans Code": "MRGC",
         "Instrument": "TWTR",
         "Description": "Cash received thru Merger 2 shares at $60.0",
         "Amount": "$120.00"},

        # Stock-for-stock merger with CIL (XLNX→AMD style)
        {"Activity Date": "5/1/2024", "Trans Code": "Buy",
         "Instrument": "XLNX",
         "Description": "Xilinx\nCUSIP: 983919101",
         "Quantity": "1",  "Price": "$200.00", "Amount": "($200.00)"},
        {"Activity Date": "10/15/2024", "Trans Code": "MRGS",
         "Instrument": "XLNX",
         "Description": "Xilinx\nCUSIP: 983919101",
         "Quantity": "1S"},
        {"Activity Date": "10/15/2024", "Trans Code": "MRGS",
         "Instrument": "AMD",
         "Description": "AMD\nCUSIP: 007903107",
         "Quantity": "1"},
        {"Activity Date": "10/22/2024", "Trans Code": "CIL",
         "Instrument": "AMD",
         "Description": "CIL on 0.723 @ $150.00 - AMD",
         "Amount": "$108.45"},

        # Stock-lending income (SLIP)
        {"Activity Date": "12/1/2024", "Trans Code": "SLIP",
         "Instrument": "AAPL",
         "Description": "Stock Lending",
         "Amount": "$0.05"},

        # Option: BTO + OEXP expiring worthless
        {"Activity Date": "1/1/2024", "Trans Code": "BTO",
         "Instrument": "AAPL",
         "Description": "AAPL 3/15/2024 Call $200.00",
         "Quantity": "1",  "Price": "$5.00", "Amount": "($500.00)"},
        {"Activity Date": "3/15/2024", "Trans Code": "OEXP",
         "Instrument": "AAPL",
         "Description": "Option Expiration for AAPL 3/15/2024 Call $200.00"},

        # ACH deposit (funding the account)
        {"Activity Date": "1/1/2023", "Trans Code": "ACH",
         "Instrument": "",
         "Description": "ACH Deposit",
         "Amount": "$5000.00"},
    ])

    # Voya 401K — biweekly contributions to two funds, plus the
    # negative-CONTRIBUTION error reversal pattern.
    write_voya_csv(tmp / "data" / "voya-401k-1.csv", [
        {"Activity Date": "2023-01-15", "Activity": "CONTRIBUTION",
         "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
         "# of Units": "1.0", "Unit Price": "75.00", "Amount": "75.00"},
        {"Activity Date": "2023-02-01", "Activity": "CONTRIBUTION",
         "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
         "# of Units": "1.0", "Unit Price": "75.00", "Amount": "75.00"},
        # Error reversal — employer claws back one contribution
        {"Activity Date": "2023-02-15", "Activity": "CONTRIBUTION",
         "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
         "# of Units": "-1.0", "Unit Price": "75.00", "Amount": "-75.00"},
        # More regular contributions after
        {"Activity Date": "2023-03-01", "Activity": "CONTRIBUTION",
         "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
         "# of Units": "1.0", "Unit Price": "75.00", "Amount": "75.00"},
        # Dividend (auto-reinvest)
        {"Activity Date": "2023-03-31", "Activity": "DIVIDEND",
         "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
         "# of Units": "0.01", "Unit Price": "76.00", "Amount": "0.76"},
    ])

    # Ticker rename: suppose OLDSYM was renamed to NEWSYM before
    # 2024-06-01.  Add a rule so the fixture's parser folds OLDSYM
    # txns into NEWSYM before dedup.
    renames = {
        "_comment": "test fixture — treat OLDSYM as NEWSYM before 2024-06-01",
        "Robinhood": [
            {"from": "OLDSYM", "to": "NEWSYM", "before_date": "2024-06-01"},
        ],
    }
    (tmp / "cache" / "ticker_renames.json").write_text(json.dumps(renames))

    # Metadata file — Account Group / Account Type rows.  Required
    # because src/config.py's ACCOUNT_GROUPS / ACCOUNT_TYPES are empty
    # by default (the real mappings live in data/metadata.csv).  Tests
    # that assert specific account_group values (e.g. "Rollover IRA"
    # for Voya 401K) depend on these rows.
    with open(tmp / "data" / "metadata.csv", "w",
              newline="", encoding="utf-8") as f:
        f.write("Type,Date,Amount,Symbol,Note\n")
        f.write("Account Group,,,Robinhood,Robinhood\n")
        f.write("Account Group,,,Voya 401K,Rollover IRA\n")
        f.write("Account Type,,,Robinhood,Taxable\n")
        f.write("Account Type,,,Rollover IRA,Retirement\n")
