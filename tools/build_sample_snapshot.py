"""Generate ``samples/portfolio.snapshot.json`` — a synthetic, fully
fictional portfolio that exercises every broker parser end-to-end.

Run from the repo root::

    python tools/build_sample_snapshot.py

The output is a plain JSON snapshot (see ``src/snapshot.py``) that any
fresh checkout can drop in with::

    python -m src.main --import-snapshot samples/portfolio.snapshot.json
    python -m src.main

The portfolio belongs to "Sam Sample" — a fictional 35-year-old with
a moderately-diversified mix of taxable equities, crypto, retirement
contributions, and a high-yield savings account.  All values are
round numbers chosen to be obviously synthetic; tickers are common
public market symbols (AAPL, MSFT, VOO, BTC-USD, etc.) so the
dashboard's sector enrichment + price fetching exercise the same
code paths real data would.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.snapshot import export_snapshot  # noqa: E402


# ---------------------------------------------------------------------------
# Per-broker CSV writers
# ---------------------------------------------------------------------------

def _w_robinhood(path: Path, rows: list[dict]) -> None:
    headers = [
        "Activity Date", "Process Date", "Settle Date", "Instrument",
        "Description", "Trans Code", "Quantity", "Price", "Amount",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_schwab(path: Path, rows: list[dict]) -> None:
    headers = ["Date", "Action", "Symbol", "Description",
               "Quantity", "Price", "Fees & Comm", "Amount"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_vanguard(path: Path, rows: list[dict]) -> None:
    headers = ["Date", "Symbol", "Action", "Quantity", "unitPrice",
               "Fee", "Subtotal", "Note", "Account", "Currency"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_voya(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("000000 Sample 401(k) Savings Plan\n")
        f.write("Date Range :\t01/01/2020 - 12/31/2026\n")
        f.write('"\n"\n"\n"\n')
        headers = ["Activity Date", "Activity", "Fund", "Money Source",
                   "# of Units", "Unit Price", "Amount"]
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_usaa(path: Path, rows: list[dict]) -> None:
    headers = ["Account", "Currency", "Date", "Symbol", "Action",
               "Quantity", "unitPrice", "Fee", "Subtotal", "Note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_apple_savings(path: Path, rows: list[dict]) -> None:
    """Apple Savings uses the normalized 10-column schema (matching
    USAA / Vanguard).  The iCloud download is hand-converted into
    this format before dropping into ``data/``."""
    headers = ["Account", "Date", "Currency", "Symbol", "Action",
               "Quantity", "unitPrice", "Fee", "Subtotal", "Note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_coinbase(path: Path, rows: list[dict]) -> None:
    """Coinbase has a multi-line preamble.  Headers per parser:
    ID, Timestamp, Transaction Type, Asset, Quantity Transacted,
    Price Currency, Price at Transaction, Subtotal,
    Total (inclusive of fees and/or spread), Fees and/or Spread, Notes
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("Transactions\n")
        f.write("User,Sample User,00000000-0000-0000-0000-000000000000\n")
        headers = [
            "ID", "Timestamp", "Transaction Type", "Asset",
            "Quantity Transacted", "Price Currency", "Price at Transaction",
            "Subtotal", "Total (inclusive of fees and/or spread)",
            "Fees and/or Spread", "Notes",
        ]
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_coinbase_pro(path: Path, rows: list[dict]) -> None:
    headers = ["portfolio", "type", "time", "amount", "balance",
               "amount/balance unit", "transfer id", "trade id", "order id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_manual(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("# Manual adjustments — hand-edited corrections "
                "for things broker exports miss.\n")
        f.write("# Lines starting with # are ignored.  Schema:\n")
        f.write("# Account, Date, Type, Symbol, Quantity, Price, "
                "Amount, Description\n")
        headers = ["Account", "Date", "Type", "Symbol",
                   "Quantity", "Price", "Amount", "Description"]
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def _w_metadata(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Type", "Date", "Amount", "Symbol", "Note"])
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Synthetic portfolio fixtures
# ---------------------------------------------------------------------------

def _build(tmp: Path) -> None:
    """Write a complete fictional portfolio into ``tmp``."""
    tmp.mkdir(parents=True, exist_ok=True)

    # Robinhood — taxable equities + one option round-trip + ACH funding.
    _w_robinhood(tmp / "robinhood-1.csv", [
        {"Activity Date": "1/15/2022", "Trans Code": "ACH",
         "Description": "ACH Deposit", "Amount": "$5000.00"},
        {"Activity Date": "2/1/2022",  "Trans Code": "Buy",
         "Instrument": "VOO", "Description": "Vanguard S&P 500 ETF",
         "Quantity": "10", "Price": "$400.00", "Amount": "($4000.00)"},
        {"Activity Date": "5/12/2022", "Trans Code": "Buy",
         "Instrument": "AAPL", "Description": "Apple Inc",
         "Quantity": "5", "Price": "$150.00", "Amount": "($750.00)"},
        {"Activity Date": "7/15/2023", "Trans Code": "Buy",
         "Instrument": "MSFT", "Description": "Microsoft Corp",
         "Quantity": "3", "Price": "$330.00", "Amount": "($990.00)"},
        {"Activity Date": "9/20/2023", "Trans Code": "CDIV",
         "Instrument": "VOO", "Description": "VOO Dividend",
         "Amount": "$15.00"},
        {"Activity Date": "1/8/2024",  "Trans Code": "Sell",
         "Instrument": "AAPL", "Description": "Apple Inc",
         "Quantity": "2", "Price": "$185.00", "Amount": "$370.00"},
        # Option round-trip — buy a call, expires worthless
        {"Activity Date": "3/1/2024",  "Trans Code": "BTO",
         "Instrument": "AAPL",
         "Description": "AAPL 6/21/2024 Call $200.00",
         "Quantity": "1", "Price": "$3.50", "Amount": "($350.00)"},
        {"Activity Date": "6/21/2024", "Trans Code": "OEXP",
         "Instrument": "AAPL",
         "Description": "Option Expiration for AAPL 6/21/2024 Call $200.00",
         "Quantity": "1"},
        {"Activity Date": "11/8/2024", "Trans Code": "Buy",
         "Instrument": "VTI", "Description": "Vanguard Total Stock Market",
         "Quantity": "8", "Price": "$280.00", "Amount": "($2240.00)"},
        {"Activity Date": "3/14/2025", "Trans Code": "Buy",
         "Instrument": "BND", "Description": "Vanguard Total Bond Market",
         "Quantity": "20", "Price": "$72.50", "Amount": "($1450.00)"},
    ])

    # Coinbase — BTC + ETH purchases, one dividend, one staking income.
    _w_coinbase(tmp / "coinbase-1.csv", [
        {"ID": "tx-1", "Timestamp": "2021-04-15 14:30:00 UTC",
         "Transaction Type": "Buy", "Asset": "BTC",
         "Quantity Transacted": "0.05", "Price Currency": "USD",
         "Price at Transaction": "$60000.00",
         "Subtotal": "$3000.00", "Total (inclusive of fees and/or spread)": "$3030.00",
         "Fees and/or Spread": "$30.00", "Notes": ""},
        {"ID": "tx-2", "Timestamp": "2022-06-12 09:00:00 UTC",
         "Transaction Type": "Buy", "Asset": "ETH",
         "Quantity Transacted": "1.0", "Price Currency": "USD",
         "Price at Transaction": "$1200.00",
         "Subtotal": "$1200.00", "Total (inclusive of fees and/or spread)": "$1212.00",
         "Fees and/or Spread": "$12.00", "Notes": ""},
        {"ID": "tx-3", "Timestamp": "2024-02-20 16:45:00 UTC",
         "Transaction Type": "Buy", "Asset": "BTC",
         "Quantity Transacted": "0.02", "Price Currency": "USD",
         "Price at Transaction": "$50000.00",
         "Subtotal": "$1000.00", "Total (inclusive of fees and/or spread)": "$1010.00",
         "Fees and/or Spread": "$10.00", "Notes": ""},
        {"ID": "tx-4", "Timestamp": "2025-03-10 11:00:00 UTC",
         "Transaction Type": "Staking Income", "Asset": "ETH",
         "Quantity Transacted": "0.005", "Price Currency": "USD",
         "Price at Transaction": "$3500.00",
         "Subtotal": "$17.50", "Total (inclusive of fees and/or spread)": "$17.50",
         "Fees and/or Spread": "$0.00", "Notes": ""},
    ])

    # Coinbase Pro / GDAX — a small trade pair (deposit + match buy + match
    # sell) showing the trade_settle leg pairing.
    _w_coinbase_pro(tmp / "coinbase-pro-gdax-1.csv", [
        {"portfolio": "default", "type": "deposit",
         "time": "2023-01-15T12:00:00.000Z",
         "amount": "500.00", "balance": "500.00",
         "amount/balance unit": "USD",
         "transfer id": "tr-1"},
        {"portfolio": "default", "type": "match",
         "time": "2023-01-16T10:30:00.000Z",
         "amount": "0.01", "balance": "0.01",
         "amount/balance unit": "BTC",
         "trade id": "tt-1", "order id": "or-1"},
        {"portfolio": "default", "type": "match",
         "time": "2023-01-16T10:30:00.000Z",
         "amount": "-300.00", "balance": "200.00",
         "amount/balance unit": "USD",
         "trade id": "tt-1", "order id": "or-1"},
    ])

    # Schwab Roth IRA — a couple of mutual fund buys + reinvest.
    _w_schwab(tmp / "schwab-roth-ira-1.csv", [
        {"Date": "03/15/2023", "Action": "Buy", "Symbol": "FXAIX",
         "Description": "FIDELITY 500 INDEX",
         "Quantity": "20", "Price": "$150.00", "Fees & Comm": "",
         "Amount": "-$3000.00"},
        {"Date": "03/15/2024", "Action": "Buy", "Symbol": "FXAIX",
         "Description": "FIDELITY 500 INDEX",
         "Quantity": "18", "Price": "$170.00", "Fees & Comm": "",
         "Amount": "-$3060.00"},
        {"Date": "12/20/2024", "Action": "Reinvest Shares", "Symbol": "FXAIX",
         "Description": "FIDELITY 500 INDEX",
         "Quantity": "0.5", "Price": "$180.00", "Fees & Comm": "",
         "Amount": "-$90.00"},
        {"Date": "03/15/2025", "Action": "Buy", "Symbol": "FXAIX",
         "Description": "FIDELITY 500 INDEX",
         "Quantity": "16", "Price": "$190.00", "Fees & Comm": "",
         "Amount": "-$3040.00"},
    ])

    # Vanguard 401K — a few biweekly contributions to one fund.
    _w_vanguard(tmp / "vanguard-401k.csv", [
        {"Date": "2023-06-30", "Symbol": "VFIAX", "Action": "Buy",
         "Quantity": "10", "unitPrice": "200.00", "Fee": "0",
         "Subtotal": "2000.00", "Note": "EMPLOYEE PRE-TAX BASIC",
         "Account": "Vanguard 401K", "Currency": "USD"},
        {"Date": "2023-06-30", "Symbol": "VFIAX", "Action": "Buy",
         "Quantity": "5", "unitPrice": "200.00", "Fee": "0",
         "Subtotal": "1000.00", "Note": "EMPLOYER MATCH",
         "Account": "Vanguard 401K", "Currency": "USD"},
        {"Date": "2024-01-15", "Symbol": "VFIAX", "Action": "Buy",
         "Quantity": "8", "unitPrice": "230.00", "Fee": "0",
         "Subtotal": "1840.00", "Note": "EMPLOYEE PRE-TAX BASIC",
         "Account": "Vanguard 401K", "Currency": "USD"},
        {"Date": "2025-01-15", "Symbol": "VFIAX", "Action": "Buy",
         "Quantity": "8", "unitPrice": "260.00", "Fee": "0",
         "Subtotal": "2080.00", "Note": "EMPLOYEE PRE-TAX BASIC",
         "Account": "Vanguard 401K", "Currency": "USD"},
    ])

    # Voya 401K — biweekly contributions to a target-date fund.
    _w_voya(tmp / "voya-401k-1.csv", [
        {"Activity Date": "2022-01-14", "Activity": "CONTRIBUTION",
         "Fund": "VANG TARGET 2055", "Money Source": "Before-Tax",
         "# of Units": "5.0", "Unit Price": "100.00", "Amount": "500.00"},
        {"Activity Date": "2022-07-15", "Activity": "CONTRIBUTION",
         "Fund": "VANG TARGET 2055", "Money Source": "Before-Tax",
         "# of Units": "4.5", "Unit Price": "110.00", "Amount": "495.00"},
        {"Activity Date": "2023-01-13", "Activity": "DIVIDEND",
         "Fund": "VANG TARGET 2055", "Money Source": "Before-Tax",
         "# of Units": "0.1", "Unit Price": "115.00", "Amount": "11.50"},
    ])

    # USAA Roth IRA — a single contribution.
    _w_usaa(tmp / "usaa-roth-ira.csv", [
        {"Account": "USAA Victory Capital Roth IRA", "Currency": "USD",
         "Date": "15/04/2022", "Symbol": "USSPX", "Action": "Buy",
         "Quantity": "30", "unitPrice": "100.00", "Fee": "0",
         "Subtotal": "3000.00",
         "Note": "PRIOR YEAR CONTRIBUTION"},
        {"Account": "USAA Victory Capital Roth IRA", "Currency": "USD",
         "Date": "15/04/2023", "Symbol": "USSPX", "Action": "Buy",
         "Quantity": "26", "unitPrice": "115.00", "Fee": "0",
         "Subtotal": "2990.00",
         "Note": "PRIOR YEAR CONTRIBUTION"},
    ])

    # Apple Savings — initial deposit + interest credits.  Schema
    # mirrors USAA / Vanguard: Action=Buy on USD = cash inflow,
    # Action=Dividend = interest income.
    _w_apple_savings(tmp / "apple-savings.csv", [
        {"Account": "Apple Savings", "Date": "2023-04-17", "Currency": "USD",
         "Symbol": "USD", "Action": "Buy",
         "Quantity": "5000.00", "unitPrice": "1.00", "Fee": "0.00",
         "Subtotal": "5000.00", "Note": "Initial deposit"},
        {"Account": "Apple Savings", "Date": "2023-05-31", "Currency": "USD",
         "Symbol": "USD", "Action": "Dividend",
         "Quantity": "18.75", "unitPrice": "1.00", "Fee": "0.00",
         "Subtotal": "18.75", "Note": "Monthly interest"},
        {"Account": "Apple Savings", "Date": "2023-06-30", "Currency": "USD",
         "Symbol": "USD", "Action": "Dividend",
         "Quantity": "18.82", "unitPrice": "1.00", "Fee": "0.00",
         "Subtotal": "18.82", "Note": "Monthly interest"},
        {"Account": "Apple Savings", "Date": "2024-01-15", "Currency": "USD",
         "Symbol": "USD", "Action": "Buy",
         "Quantity": "2000.00", "unitPrice": "1.00", "Fee": "0.00",
         "Subtotal": "2000.00", "Note": "Transfer in"},
    ])

    # Manual adjustments — illustrative no-op (CSV with a sample
    # comment-only entry so the parser is exercised but no txns added).
    _w_manual(tmp / "manual-adjustments.csv", [])

    # Metadata file: birthday, salary, bonuses, expenses, targets, and
    # an example Account Group / Account Type override (no-op for the
    # built-in defaults — present to demonstrate the mechanism).
    _w_metadata(tmp / "metadata.csv", [
        ["Personal Info", "1990-06-15", "0", "USD", "Birthday"],
        ["Salary History", "2018-01-01", "50000", "USD", "First job"],
        ["Salary History", "2020-06-01", "60000", "USD", "Promotion"],
        ["Salary History", "2022-09-01", "70000", "USD", "New role"],
        ["Salary History", "2024-04-01", "80000", "USD", "Annual raise"],
        ["Bonus History", "2023-12-15", "3000", "USD", "Year-end bonus"],
        ["Bonus History", "2024-12-15", "4000", "USD", "Year-end bonus"],
        ["Annual Expenses", "2025-01-01", "40000", "USD",
         "Estimated annual living expenses"],
        # Tax / projection settings — drive federal bracket math,
        # Roth phaseout, and the Monte Carlo / scenario projection
        # horizon.  State is currently a display label only.
        ["Filing Status",  "", "",  "",   "Single"],
        ["State",          "", "",  "",   "CA"],
        ["Retirement Age", "", "67", "",  "Target retirement age"],
        ["Target", "2025", "100000", "USD", "End-of-year goal"],
        ["Target", "2026", "150000", "USD", "End-of-year goal"],
        ["Target", "2030", "500000", "USD", "Mid-career goal"],
        # Account Group / Account Type mappings.  These are no longer
        # baked into src/config.py — every install configures them
        # here, in the user's own metadata.csv, so they stay
        # personal/local.
        ["Account Group", "", "", "Robinhood",                   "Robinhood"],
        ["Account Group", "", "", "Coinbase",                    "Coinbase"],
        ["Account Group", "", "", "Coinbase Pro",                "Coinbase"],
        ["Account Group", "", "", "Schwab Roth IRA",             "Roth IRA"],
        ["Account Group", "", "", "Vanguard 401K",               "401K"],
        ["Account Group", "", "", "Voya 401K",                   "Rollover IRA"],
        ["Account Group", "", "", "USAA Roth IRA",                 "Roth IRA"],
        ["Account Group", "", "", "Apple Savings",               "Apple Savings"],
        ["Account Type",  "", "", "Robinhood",     "Taxable"],
        ["Account Type",  "", "", "Coinbase",      "Taxable"],
        ["Account Type",  "", "", "Roth IRA",      "Retirement"],
        ["Account Type",  "", "", "Rollover IRA",  "Retirement"],
        ["Account Type",  "", "", "401K",          "Retirement"],
        ["Account Type",  "", "", "Apple Savings", "Savings"],
    ])


def main() -> None:
    out = ROOT / "samples" / "portfolio.snapshot.json"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "data"
        _build(data_dir)
        bundle = export_snapshot(data_dir, out)
    print(f"Wrote {bundle['file_count']} synthetic file(s) to {out}")
    print(f"Try it:  python -m src.main --import-snapshot {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
