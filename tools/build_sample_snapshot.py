"""Generate ``samples/portfolio.snapshot.json`` — a synthetic, fully
fictional portfolio that exercises every broker parser end-to-end.

Run from the repo root::

    python tools/build_sample_snapshot.py

The output is a plain JSON snapshot (see ``src/snapshot.py``). To
render the public example without touching personal data or fetching prices::

    python -m tools.build_demo

The portfolio belongs to "Sam Sample", a fictional 36-year-old as of
June 30, 2026, with taxable equities, crypto, retirement contributions,
and savings accounts. Every figure and personal detail is invented.
Familiar public ticker names label the examples; the isolated demo builder
uses illustrative synthetic price curves from ``samples/prices.fixture.json``.

"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AS_OF_DATE = date(2026, 6, 30)
SAMPLE_TIMESTAMP = AS_OF_DATE.isoformat() + "T00:00:00+00:00"


# ---------------------------------------------------------------------------
# The public sample is a fixed, fictional snapshot. Advancing its date is an
# explicit fixture edit; rebuilding never incorporates the current date,
# market feeds, local portfolio files, or user-specific price caches.
# ---------------------------------------------------------------------------

def _recent(days_ago: int) -> date:
    return AS_OF_DATE - timedelta(days=days_ago)


def _iso(d: date) -> str:
    return d.isoformat()


def _mdy(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


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


def _w_sfcu(path: Path, rows: list[dict]) -> None:
    """State Farm FCU exports a generic ``ExportedTransactions.csv``
    from its online-banking platform — no account column, rows
    newest-first, and a running ``Balance`` the parser checks itself
    against.  ``Transaction Type`` (Credit/Debit) carries direction."""
    headers = ["Transaction ID", "Posting Date", "Effective Date",
               "Transaction Type", "Posting Status", "Amount",
               "Check Number", "Reference Number", "Description",
               "Transaction Category", "Type", "Balance", "Memo",
               "Extended Description"]
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
            if r.get("Transaction Type") == "Buy" and not r.get("Notes"):
                r = {**r, "Notes": "Bought fictional asset using bank account DEMO"}
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
        {"Activity Date": "5/1/2021", "Trans Code": "ACH",
         "Description": "Fictional opening funding", "Amount": "$10000.00"},
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
        # --- Option EXERCISE: OEXCS paired with its OCC cash leg -------
        # Robinhood writes an exercise as TWO rows: the contract
        # disposal (OEXCS, quantity "1S", no amount) and the cash payout
        # (OCC, "Option Maturity: Cash Component", qty 0, amount).  The
        # parser pairs them on (date, underlying) because the OCC
        # description is generic, and rolls the cash into the OEXCS row.
        # Neither code appeared in the sample before, so the pairing was
        # unreachable end to end.
        {"Activity Date": "9/3/2024", "Trans Code": "BTO",
         "Instrument": "MSFT",
         "Description": "MSFT 1/17/2025 Call $400.00",
         "Quantity": "1", "Price": "$15.00", "Amount": "($1500.00)"},
        {"Activity Date": "1/17/2025", "Trans Code": "OEXCS",
         "Instrument": "MSFT",
         "Description": "MSFT 1/17/2025 Call $400.00",
         "Quantity": "1S", "Amount": ""},
        {"Activity Date": "1/17/2025", "Trans Code": "OCC",
         "Instrument": "MSFT",
         "Description": "Option Maturity: Cash Component",
         "Quantity": "0", "Amount": "$2903.00"},

        # --- An OPEN, deep-ITM contract held across every snapshot -----
        # This is what unlocks the intrinsic floor.  yfinance cannot
        # price option contracts, so an open one is marked at its last
        # traded premium — which goes stale the moment the underlying
        # moves.  `prices.option_intrinsic` FLOORS that mark at intrinsic
        # value from the underlying's cached close, so a deep-ITM
        # contract tracks its underlying instead of sitting frozen at the
        # purchase price.
        #
        # CLAUDE.md records that this floor shipped MISSING at three of
        # its six call sites, reprinting the whole intrinsic-over-cost
        # gap as a phantom daily move.  The sample's only other contract
        # expires, so no valuation ever saw a live premium.
        #
        # Dates are relative to the fixed snapshot: bought 60 days
        # ago, expiring ~400 days out, therefore always open.
        {"Activity Date": _mdy(_recent(60)), "Trans Code": "BTO",
         "Instrument": "AAPL",
         "Description": f"AAPL {_mdy(_recent(-400))} Call $250.00",
         "Quantity": "1", "Price": "$20.00", "Amount": "($2000.00)"},

        # --- A cash merger: the position is acquired for cash ----------
        # Robinhood writes this as TWO rows: MRGS with an "{N}S" suffix
        # (shares SURRENDERED) carrying no money, and MRGC carrying the
        # cash.  `reorgs.py` pools them on (date, symbol) and the parser
        # emits ONE Sell at the MRGC price, so the position closes with
        # proper proceeds and a proper realized gain instead of
        # vanishing.  Without the pooling the shares disappear and the
        # cash arrives unattached.
        #
        # reorgs.py exists for exactly this family and was exercised only
        # by its own unit tests — no end-to-end path reached it.
        {"Activity Date": "1/14/2022", "Trans Code": "Buy",
         "Instrument": "TWTR", "Description": "Twitter Inc",
         "Quantity": "20", "Price": "$40.00", "Amount": "($800.00)"},
        {"Activity Date": "10/27/2022", "Trans Code": "MRGS",
         "Instrument": "TWTR", "Description": "Twitter Inc",
         "Quantity": "20S"},
        {"Activity Date": "10/27/2022", "Trans Code": "MRGC",
         "Instrument": "TWTR",
         "Description": "Cash received thru Merger 20 shares at $54.20",
         "Amount": "$1084.00"},

        # --- A stock-for-stock merger ----------------------------------
        # The other half of the "S" convention, and the reason it has to
        # be here: with only a surrender in the sample, nothing
        # distinguishes reading the suffix correctly from assuming every
        # MRGS is a surrender.  Both fixtures are needed before the flag
        # means anything.
        #
        # The target surrenders (MRGS "{N}S", no MRGC -> Sell at $0,
        # realizing the full basis as a loss) and the acquirer's shares
        # arrive (MRGS with a plain quantity -> Buy at $0).  The parser
        # documents this as balance-accurate but not tax-accurate: the
        # $0-basis receive carries the loss forward as unrealized gain,
        # so the eventual sale nets out to the true economic result.
        {"Activity Date": "5/10/2021", "Trans Code": "Buy",
         "Instrument": "XLNX", "Description": "Xilinx Inc",
         "Quantity": "10", "Price": "$150.00", "Amount": "($1500.00)"},
        {"Activity Date": "2/14/2022", "Trans Code": "MRGS",
         "Instrument": "XLNX", "Description": "Xilinx Inc",
         "Quantity": "10S"},
        {"Activity Date": "2/14/2022", "Trans Code": "MRGS",
         "Instrument": "AMD", "Description": "Advanced Micro Devices",
         "Quantity": "17"},
        {"Activity Date": "3/15/2023", "Trans Code": "Sell",
         "Instrument": "AMD", "Description": "Advanced Micro Devices",
         "Quantity": "17", "Price": "$110.00", "Amount": "$1870.00"},

        # --- Cash in lieu of a fractional share ------------------------
        # CIL pays out a fraction the user cannot hold.  The description
        # carries the fraction and price ("CIL on {qty} @ {price} -
        # {symbol}"), which reorgs.parse_cil_description reads to emit a
        # Sell of exactly that fraction — so the remaining position is
        # reduced by the fraction rather than left untouched with
        # unexplained cash.
        {"Activity Date": "6/13/2025", "Trans Code": "CIL",
         "Instrument": "VTI",
         "Description": "CIL on 0.25 @ $290.00 - VTI",
         "Amount": "$72.50"},

        {"Activity Date": "11/8/2024", "Trans Code": "Buy",
         "Instrument": "VTI", "Description": "Vanguard Total Stock Market",
         "Quantity": "8", "Price": "$280.00", "Amount": "($2240.00)"},
        {"Activity Date": "3/14/2025", "Trans Code": "Buy",
         "Instrument": "BND", "Description": "Vanguard Total Bond Market",
         "Quantity": "20", "Price": "$72.50", "Amount": "($1450.00)"},
        # Recent activity relative to the fixed sample date.
        {"Activity Date": _mdy(_recent(40)), "Trans Code": "CDIV",
         "Instrument": "VOO", "Description": "VOO Dividend",
         "Amount": "$18.00"},
        {"Activity Date": _mdy(_recent(12)), "Trans Code": "Buy",
         "Instrument": "VOO", "Description": "Vanguard S&P 500 ETF",
         "Quantity": "2", "Price": "$560.00", "Amount": "($1120.00)"},
    ])

    # Coinbase — BTC + ETH purchases, one dividend, one staking income.
    _w_coinbase(tmp / "coinbase-1.csv", [
        {"ID": "tx-cash-withdrawal", "Timestamp": "2026-06-25 10:00:00 UTC",
         "Transaction Type": "Withdrawal", "Asset": "USD",
         "Quantity Transacted": "2830", "Price Currency": "USD",
         "Price at Transaction": "$1.00", "Subtotal": "$2830.00",
         "Total (inclusive of fees and/or spread)": "$2830.00",
         "Fees and/or Spread": "$0.00", "Notes": "Fictional proceeds returned to bank"},
        {"ID": "tx-opening-pro-funding", "Timestamp": "2023-01-14 10:00:00 UTC",
         "Transaction Type": "Deposit", "Asset": "USD",
         "Quantity Transacted": "500", "Price Currency": "USD",
         "Price at Transaction": "$1.00", "Subtotal": "$500.00",
         "Total (inclusive of fees and/or spread)": "$500.00",
         "Fees and/or Spread": "$0.00", "Notes": "Fictional bank funding for Pro transfer"},
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

        # --- Wrap / unwrap (ETH <-> CBETH) --------------------------------
        # Basis-CARRYING, not a taxable disposal: the pair moves the same
        # underlying, so the walker consumes the ETH lots with NO realized
        # gain and carries their basis to CBETH, rescaled to the new
        # quantity.  CLAUDE.md calls this out as the rule that has broken
        # most often, and mapping it to Buy/Sell (the old behaviour)
        # wrongly realized the whole gain at every wrap.
        #
        # The parser splits `Wrap Asset` by the sign of Quantity
        # Transacted, so the OUT leg must carry a negative quantity.
        # CBETH trades at a premium to ETH, hence fewer units in.
        {"ID": "tx-5", "Timestamp": "2024-05-01 10:00:00 UTC",
         "Transaction Type": "Wrap Asset", "Asset": "ETH",
         "Quantity Transacted": "-0.5", "Price Currency": "USD",
         "Price at Transaction": "$3000.00",
         "Subtotal": "$1500.00", "Total (inclusive of fees and/or spread)": "$1500.00",
         "Fees and/or Spread": "$0.00", "Notes": "Wrapped 0.5 ETH to CBETH"},
        {"ID": "tx-6", "Timestamp": "2024-05-01 10:00:00 UTC",
         "Transaction Type": "Wrap Asset", "Asset": "CBETH",
         "Quantity Transacted": "0.46", "Price Currency": "USD",
         "Price at Transaction": "$3200.00",
         "Subtotal": "$1500.00", "Total (inclusive of fees and/or spread)": "$1500.00",
         "Fees and/or Spread": "$0.00", "Notes": "Wrapped 0.5 ETH to CBETH"},
        # Selling the wrapped asset is where the carried basis finally
        # realizes.  Without this leg the wrap's basis handling is
        # unobservable — a wrap alone preserves total basis by
        # construction, so nothing distinguishes carrying it from
        # rebuilding it (the same trap that made the split-parity test
        # vacuous; see docs/AUDIT.md).
        {"ID": "tx-7", "Timestamp": "2025-08-15 13:00:00 UTC",
         "Transaction Type": "Sell", "Asset": "CBETH",
         "Quantity Transacted": "0.46", "Price Currency": "USD",
         "Price at Transaction": "$4000.00",
         "Subtotal": "$1840.00", "Total (inclusive of fees and/or spread)": "$1830.00",
         "Fees and/or Spread": "$10.00", "Notes": ""},

        # --- Two ADA lots at very different prices, then a partial sale --
        # This is what makes the `Lot Method` metadata row observable.
        # The LATER lot is the EXPENSIVE one, so FIFO and HIFO relieve
        # different lots and the realized gain differs sharply:
        #   FIFO -> relieves the 2023 lot  (cheap basis, big gain)
        #   HIFO -> relieves the 2024 lot  (dear basis, small gain)
        # With both lots at the same price the two methods agree and the
        # metadata row would be untestable.
        {"ID": "tx-8", "Timestamp": "2023-02-10 10:00:00 UTC",
         "Transaction Type": "Buy", "Asset": "ADA",
         "Quantity Transacted": "1000", "Price Currency": "USD",
         "Price at Transaction": "$0.25",
         "Subtotal": "$250.00", "Total (inclusive of fees and/or spread)": "$250.00",
         "Fees and/or Spread": "$0.00", "Notes": ""},
        {"ID": "tx-9", "Timestamp": "2024-11-20 10:00:00 UTC",
         "Transaction Type": "Buy", "Asset": "ADA",
         "Quantity Transacted": "1000", "Price Currency": "USD",
         "Price at Transaction": "$1.00",
         "Subtotal": "$1000.00", "Total (inclusive of fees and/or spread)": "$1000.00",
         "Fees and/or Spread": "$0.00", "Notes": ""},
        {"ID": "tx-10", "Timestamp": "2025-09-05 10:00:00 UTC",
         "Transaction Type": "Sell", "Asset": "ADA",
         "Quantity Transacted": "1000", "Price Currency": "USD",
         "Price at Transaction": "$0.80",
         "Subtotal": "$800.00", "Total (inclusive of fees and/or spread)": "$800.00",
         "Fees and/or Spread": "$0.00", "Notes": ""},

        # --- An off-platform receive with user-supplied basis -----------
        # A `Receive` is crypto arriving from a wallet fin cannot see, so
        # it crosses fin's MEASUREMENT BOUNDARY (contribution at FMV) and
        # fin has no way to reconstruct what it cost.  That is exactly
        # the case the `Cost Basis` metadata row exists for — see the
        # paired row in the metadata block below.
        #
        # Without the override this lot would take FMV-at-transfer basis,
        # which is only an estimate; with it, the broker's
        # customer-provided figure wins.
        # --- The regular-side counter-leg of the Pro deposit -----------
        # Both wallets share account_group "Coinbase", so moving cash
        # between them is a no-op — but the GDAX export does not tag it,
        # and the Pro-side `deposit` row looks exactly like a real bank
        # deposit.  `_reconcile_coinbase_intra_transfers` pairs them on
        # (date, amount) and re-tags the Pro leg as a Transfer In.
        #
        # Without the pairing, every regular->Pro shuffle inflates
        # "external money in" by the transferred amount, which corrupts
        # net_contributed and therefore TWR, the SPY benchmark, the
        # savings rate and FIRE.  The sample had the Pro leg but no
        # counterpart, so the pairing never fired.
        #
        # "Pro Deposit" = deposited INTO Pro, i.e. it LEAVES the regular
        # wallet -> Transfer Out.  Date and amount match the Pro-side
        # deposit below exactly, which is what the matcher keys on.
        {"ID": "tx-12", "Timestamp": "2023-01-15 11:00:00 UTC",
         "Transaction Type": "Pro Deposit", "Asset": "USD",
         "Quantity Transacted": "500", "Price Currency": "USD",
         "Price at Transaction": "$1.00",
         "Subtotal": "$500.00", "Total (inclusive of fees and/or spread)": "$500.00",
         "Fees and/or Spread": "$0.00", "Notes": "Transferred to Coinbase Pro"},

        {"ID": "tx-11", "Timestamp": "2023-06-01 08:00:00 UTC",
         "Transaction Type": "Receive", "Asset": "MATIC",
         "Quantity Transacted": "2000", "Price Currency": "USD",
         "Price at Transaction": "$0.90",
         "Subtotal": "$1800.00", "Total (inclusive of fees and/or spread)": "$1800.00",
         "Fees and/or Spread": "$0.00", "Notes": "Received from external wallet"},
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

        # --- A broker stock-split row --------------------------------------
        # `Stock Split` normalizes to the canonical `Split` action, whose
        # basis effect rescales lot quantities and per-share basis while
        # preserving TOTAL basis.  It reaches
        # `basis._apply_split_to_lots` and history's inline copy of the
        # same arithmetic — two verbatim implementations that, before
        # this row existed, NEITHER of which was executed by anything
        # (F-002 / F-015).  No sample symbol split after the sample's
        # start date, so this path was unreachable end to end.
        #
        # Bought below, split 2:1 the following year, so the split spans
        # several snapshot dates.
        {"Date": "01/10/2023", "Action": "Buy", "Symbol": "SMH",
         "Description": "VANECK SEMICONDUCTOR ETF",
         "Quantity": "10", "Price": "$120.00", "Fees & Comm": "",
         "Amount": "-$1200.00"},
        {"Date": "05/05/2023", "Action": "Stock Split", "Symbol": "SMH",
         "Description": "VANECK SEMICONDUCTOR ETF 2 FOR 1 SPLIT",
         "Quantity": "10", "Price": "", "Fees & Comm": "",
         "Amount": ""},
        # A partial sale AFTER the split, and it is load-bearing.
        #
        # A split preserves TOTAL basis and the balance walker adds the
        # new shares regardless, so quantity and total basis are
        # identical whether or not the lot-level rescale ran.  Only
        # CONSUMING lots exposes it: rescaled, these 5 shares relieve
        # 5 x $60; unrescaled they would relieve 5 x $120 and leave the
        # position with half the basis it should have.
        #
        # Same trap that made the first draft of
        # tests/test_split_walker_parity.py vacuous.
        {"Date": "06/10/2024", "Action": "Sell", "Symbol": "SMH",
         "Description": "VANECK SEMICONDUCTOR ETF",
         "Quantity": "5", "Price": "$220.00", "Fees & Comm": "",
         "Amount": "$1100.00"},
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
        # Recent contribution (relative to the fixed sample date).
        {"Date": _iso(_recent(14)), "Symbol": "VFIAX", "Action": "Buy",
         "Quantity": "7", "unitPrice": "290.00", "Fee": "0",
         "Subtotal": "2030.00", "Note": "EMPLOYEE PRE-TAX BASIC",
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

        # --- A custodian rollover, in flight for a month ---------------
        # The plan is liquidated (WITHDRAWAL -> Distribution) and the
        # proceeds land at the new record-keeper a month later
        # (TRANSFER with positive units -> Transfer In).  Same
        # account_group on both legs, which is what
        # detect_rollover_bridges matches on.
        #
        # In between, the money is invisible: main.py deliberately skips
        # USD balances outside Savings, so the account reads ZERO for a
        # month and the charts show a dip to nothing followed by a full
        # recovery.  That is not a drawdown — the money never left the
        # portfolio, it was between custodians — and the bridge exists to
        # add it back to the effective balance for exactly that window.
        #
        # Consumers: TWR, annual returns, XIRR, the JS history chart,
        # drawdown and monthly P&L.  None of them had an end-to-end path
        # to this before, and CLAUDE.md names an unbridged distribution
        # as the first thing to look for when the history chart dips to
        # $0 around a transfer.
        #
        # 32 days apart (inside the 90-day window) and equal amounts
        # (inside the 5% tolerance), with two semimonthly snapshots
        # falling inside the gap.
        {"Activity Date": "2025-04-10", "Activity": "WITHDRAWAL",
         "Fund": "VANG TARGET 2055", "Money Source": "Before-Tax",
         "# of Units": "9.6", "Unit Price": "125.00", "Amount": "1200.00"},
        {"Activity Date": "2025-05-12", "Activity": "TRANSFER",
         "Fund": "VANG TARGET 2055", "Money Source": "Before-Tax",
         "# of Units": "9.6", "Unit Price": "125.00", "Amount": "1200.00"},
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
        # Recent interest credit (relative to the fixed sample date).
        {"Account": "Apple Savings", "Date": _iso(_recent(20)),
         "Currency": "USD", "Symbol": "USD", "Action": "Dividend",
         "Quantity": "19.10", "unitPrice": "1.00", "Fee": "0.00",
         "Subtotal": "19.10", "Note": "Monthly interest"},
    ])

    # State Farm FCU share savings — credit-union "dividends" (which
    # are economically interest, reported on a 1099-INT), an ACH
    # deposit, and a fee debit.  Newest-first with a running balance,
    # exactly as the platform exports it.
    _w_sfcu(tmp / "ExportedTransactions.csv", [
        {"Transaction ID": "20260731 000004", "Posting Date": _mdy(_recent(8)),
         "Transaction Type": "Credit", "Posting Status": "Posted",
         "Amount": "10.20000", "Description": "Dividend Deposit",
         "Type": "Dividends", "Balance": "2985.20000"},
        {"Transaction ID": "20260731 000003", "Posting Date": _mdy(_recent(20)),
         "Transaction Type": "Debit", "Posting Status": "Posted",
         "Amount": "25.00000", "Description": "Returned item charge",
         "Type": "NSF Fee", "Balance": "2975.00000"},
        {"Transaction ID": "20260731 000002", "Posting Date": _mdy(_recent(40)),
         "Transaction Type": "Credit", "Posting Status": "Posted",
         "Amount": "1000.00000", "Description": "ACH Deposit PAYROLL",
         "Type": "ACH", "Balance": "3000.00000"},
        {"Transaction ID": "20260731 000001", "Posting Date": _mdy(_recent(70)),
         "Transaction Type": "Credit", "Posting Status": "Posted",
         "Amount": "2000.00000", "Description": "ACH Deposit OPENING",
         "Type": "ACH", "Balance": "2000.00000"},
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
        # Budget — recurring living expenses.  Symbol = category,
        # Amount = cost per period, Note = label with optional cadence
        # suffix (@monthly default / @yearly / @quarterly / @weekly).
        # Drives the Income tab's Budget section (analytics/budget.py).
        ["Budget", "2024-01-01", "1800",  "Housing",       "Rent"],
        ["Budget", "",           "500",   "Food",          "Groceries"],
        ["Budget", "",           "90",    "Utilities",     "Electricity"],
        ["Budget", "",           "60",    "Utilities",     "Internet"],
        ["Budget", "",           "540",   "Insurance",     "Car insurance @6mo"],
        ["Budget", "",           "15.99", "Subscriptions", "Netflix"],
        ["Budget", "",           "11.99", "Subscriptions", "Spotify"],
        ["Budget", "",           "2.99",  "Subscriptions", "iCloud"],
        # Paycheck Deduction — per-paycheck payroll lines (Symbol = kind:
        # Pre-Tax / Tax / Post-Tax; negative Amount = a credit).  Figures
        # are Sam's fictional biweekly stub at the 80k salary, and they are
        # derived rather than invented so the panel adds up: gross is
        # 80000 / 26 = 3076.92, the pre-tax medical lines net to 90.00, and
        # the payroll taxes are that reduced base times their statutory
        # rates — OASDI 6.2%, Medicare 1.45%, CA SDI 1.1%.  The state line
        # must match the `State` row below; it used to name Washington
        # programs while the profile said California.  401(k) deferrals are
        # NOT listed — derived from the contribution transactions.  Drives
        # the Income tab's Paycheck panel + the pre-tax AGI adjustment on
        # the Tax tab.
        ["Paycheck Deduction", "", "95.00",  "Pre-Tax",  "Medical/PPO Before-Tax"],
        ["Paycheck Deduction", "", "-5.00",  "Pre-Tax",  "Wellness Incentive"],
        ["Paycheck Deduction", "", "185.19", "Tax",      "OASDI (Social Security)"],
        ["Paycheck Deduction", "", "43.31",  "Tax",      "Medicare"],
        ["Paycheck Deduction", "", "32.86",  "Tax",      "CA SDI"],
        ["Paycheck Deduction", "", "8.00",   "Post-Tax", "Long-Term Disability"],
        # Voluntary extra federal withholding — reduces take-home but
        # prepays the year-end bill; the Tax tab credits it against the
        # estimated tax on realized gains.
        ["Paycheck Deduction", "", "25.00",  "Withholding", "Extra Federal Withholding"],
        ["Pay Frequency",      "", "26",     "",         "Biweekly"],
        # Tax Return — figures from the FILED prior-year 1040.  Total Tax
        # (line 24) + AGI (line 11) drive the Tax tab's safe-harbor check.
        ["Tax Return", "2025", "6800",  "Total Tax",   "1040 line 24"],
        ["Tax Return", "2025", "82000", "AGI",         "1040 line 11"],
        ["Tax Return", "2025", "7200",  "Withholding", "1040 line 25d"],
        # Tax / projection settings — drive federal bracket math,
        # Roth phaseout, and the Monte Carlo / scenario projection
        # horizon.  State is currently a display label only.
        ["Filing Status",  "", "",      "", "Single"],
        ["State",          "", "",      "", "CA"],
        ["State Tax Rate", "", "0.093", "", "CA marginal rate"],
        ["Retirement Age", "", "67",    "", "Target retirement age"],
        ["Target", "2025", "100000", "USD", "End-of-year goal"],
        ["Target", "2026", "150000", "USD", "End-of-year goal"],
        ["Target", "2030", "500000", "USD", "Mid-career goal"],
        # Target Allocation — Symbol = sector bucket, Amount = target %.
        # Drives the Holdings tab's Target vs Actual / rebalancing-drift
        # view (analytics/rebalancing.py).
        ["Target Allocation", "", "45", "Mutual Funds",   "Index-fund core"],
        ["Target Allocation", "", "30", "ETFs",           "Broad-market ETFs"],
        ["Target Allocation", "", "10", "Cryptocurrency", "Speculative sleeve"],
        ["Target Allocation", "", "10", "Cash",           "Dry powder"],
        ["Target Allocation", "",  "5", "Technology",     "Single-stock tilt"],
        # Fixed fictional statement targets, independently pinned to the
        # illustrative price fixture. Do not derive these from the pipeline
        # at build time: a valuation regression must break reconciliation.
        # The Roth row deliberately demonstrates one auditable timing
        # exception, with a stated delta that re-flags if arithmetic changes.
        ["Reconcile Balance", "2025-12-31", "24639.85", "Roth IRA",
         "Fictional statement uses an earlier close [expected 142.60]"],
        ["Reconcile Balance", "2025-12-31", "13005.18", "Coinbase",
         "Fictional year-end statement including USD cash"],
        ["Reconcile Balance", "2025-12-31", "9167.94", "401K",
         "Fictional VFIAX year-end statement"],
        ["Reconcile Realized", "2024",       "-280",   "Robinhood",     "1099-B realized gains"],
        ["Reconcile Income",   "2023",       "37.57",  "Apple Savings", "1099-INT interest"],
        # Account Group / Account Type mappings.  These are no longer
        # baked into src/config.py — every install configures them
        # here, in the user's own metadata.csv, so they stay
        # personal/local.
        ["Account Group", "", "", "Robinhood",                   "Robinhood"],
        # --- Balance anchor for a hand-maintained CASH account ----------
        # Apple Card Daily Cash lands in Apple Savings in small irregular
        # amounts that never reach the hand-kept CSV, so fin's balance
        # drifts LOW forever.  The anchor states the real statement
        # balance; balance_anchor.py walks fin's own USD balance to that
        # date and synthesizes ONE `Cash Back` row for the difference.
        #
        # Deliberately restricted to Savings accounts: for cash a delta
        # is exact arithmetic and unambiguously means missing
        # transactions, whereas on a securities account it could equally
        # be a pricing error, a missing split or a basis bug — and
        # plugging that would paper over exactly what this codebase
        # exists to surface.  A NEGATIVE delta is refused for the same
        # reason: fin holding MORE than the statement is a bug to
        # investigate, not a gap to fill.
        #
        # fin's own walk reaches ~7,056.67 by this date; the statement
        # says 7,150.00, so ~93 of Daily Cash is booked.
        ["Balance Anchor", _iso(_recent(10)), "7150.00", "Apple Savings",
         "Apple Card Daily Cash"],
        # CLAUDE.md: pair an anchor with a Reconcile Balance at the SAME
        # date so the panel stays honest — the anchor makes fin agree, and
        # the reconcile row is what proves it still does.
        ["Reconcile Balance", _iso(_recent(10)), "7150.00", "Apple Savings",
         "Apple Savings app total"],

        # Lot-relief method for one account.  Coinbase really does
        # default to HIFO, and this overrides fin's FIFO default for
        # Coinbase ONLY — the other accounts stay FIFO, which is what
        # makes the row's effect attributable.  Changes realized gain and
        # holding period, never balances.
        ["Lot Method", "", "", "Coinbase", "HIFO"],

        # User-supplied basis for the off-platform MATIC receive above.
        # Symbol = account_group, Date = acquired, Amount = TOTAL basis,
        # Note = "<qty> <asset>".  fin matches this to one lot-creating
        # txn by (account, symbol, date +-2d, qty) and stamps
        # basis_override, which the walker honours instead of the
        # FMV-at-transfer estimate.
        ["Cost Basis", "2023-06-01", "1400", "Coinbase", "2000 MATIC"],

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
        ["Account Group", "", "", "State Farm FCU Savings",
         "State Farm FCU Savings"],
        # Load-bearing: USD balances are tracked only for Savings-type
        # accounts (see pipeline_stages.walk_balances).  Without this row
        # the group defaults to Taxable and the cash vanishes.
        ["Account Type",  "", "", "State Farm FCU Savings", "Savings"],
        # Savings APR — effective-dated rate driving the Income tab's
        # 12-month forecast (rate x current cash balance).  Forecast
        # only; real Interest txns remain the authority for history.
        ["Savings APR", "2026-01-01", "0.04", "State Farm FCU Savings",
         "Declared APY - add a new dated row when it changes"],
    ])


def build_snapshot(out: Path) -> dict:
    """Build exclusively from the literal fictional rows above, byte for byte."""
    with tempfile.TemporaryDirectory(prefix="finledger-sample-") as td:
        data_dir = Path(td) / "data"
        _build(data_dir)
        # Path ordering folds case on Windows; snapshot order must be the same
        # as the case-sensitive filename ordering used on every other platform.
        files = {p.name: p.read_text(encoding="utf-8")
                 for p in sorted(data_dir.glob("*.csv"), key=lambda p: p.name)}
    bundle = {"version": 1, "exported_at": SAMPLE_TIMESTAMP,
              "file_count": len(files), "files": files}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8", newline="\n")
    return bundle


def main() -> None:
    out = ROOT / "samples" / "portfolio.snapshot.json"
    bundle = build_snapshot(out)
    print(f"Wrote {bundle['file_count']} fictional files; as of {AS_OF_DATE}")
    print("Build the isolated offline demo: python -m tools.build_demo")


if __name__ == "__main__":
    main()
