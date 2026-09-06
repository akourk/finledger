"""A synthetic portfolio whose accounts START AT DIFFERENT TIMES.

`build_synthetic_portfolio` is pinned by exact-figure assertions in
`test_pipeline_snapshot.py`, so it cannot absorb new transactions.  This
one exists separately because the dashboard-consistency checks need
properties that fixture does not have, every one of them load-bearing
rather than incidental:

1. **Staggered account starts.**  The pairing bug F-031 broke is
   invisible when every filter's natural window equals the portfolio's.
   The 401K here opens two and a half years after the taxable account.
2. **Activity that reaches the present.**  Trailing windows (1y, 6mo,
   3mo, 30day) collapse to a flat zero on a portfolio whose data stops
   years ago, which would leave most of the (filter x window) matrix
   asserting nothing.
3. **A lot-relief method that is not the default, with two lots to apply
   it to.**  The dashboard has two sources for realized gain — the
   annotated walk (real per-account methods) and the pure-method
   comparison table.  They are identical unless some account overrides
   the default AND has more than one lot to choose between, so without
   both halves a test distinguishing the two sources passes whichever
   one the code happens to read.  It did: F-033 survived its own
   regression test until this fixture grew a `Lot Method` row and a
   second AAPL lot.

Everything is fictional and deliberately round.  The figures are never
asserted directly — the tests check relations between them — so round
numbers cost nothing and keep real-looking amounts out of a public repo.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

# Anchors.  TAXABLE_START is early; RETIREMENT_START is deliberately much
# later.  Both are fixed dates rather than offsets from today so a run in
# 2030 still produces the same shape.
TAXABLE_START = date(2022, 1, 3)
RETIREMENT_START = date(2024, 7, 1)
PRICE_START = date(2021, 12, 1)


def _d(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def _dividend_rows() -> list[dict]:
    """Quarterly dividends from the first full year to the present.

    The Income tab's headline stat cards are summed IN JAVASCRIPT from
    the annual table's rows, so the relation between them only has teeth
    when that table spans several years with non-zero amounts.  One
    dividend, or none, and the sum is trivially right however the code
    behaves.
    """
    rows = []
    d = date(2022, 3, 15)
    while d <= date.today():
        rows.append({
            "Activity Date": _d(d), "Trans Code": "CDIV",
            "Instrument": "AAPL", "Description": "AAPL Cash Div",
            "Amount": "$25.00",
        })
        m = d.month + 3
        d = date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 15)
    return rows


def _write_apple_savings(path: Path) -> None:
    """Apple Savings HYSA: one deposit, then monthly interest.

    Written here rather than via `tests.conftest.write_apple_savings_csv`
    — that writer emits a different Apple export's columns, which this
    parser does not read.
    """
    import csv
    headers = ["Account", "Date", "Currency", "Symbol", "Action",
               "Quantity", "unitPrice", "Fee", "Subtotal", "Note"]
    rows = [{
        "Account": "Apple Savings", "Date": "2023-01-10", "Currency": "USD",
        "Symbol": "USD", "Action": "Buy", "Quantity": "5000",
        "unitPrice": "1", "Fee": "0", "Subtotal": "5000", "Note": "Opening deposit",
    }]
    d = date(2023, 2, 28)
    while d <= date.today():
        rows.append({
            "Account": "Apple Savings", "Date": d.strftime("%Y-%m-%d"),
            "Currency": "USD", "Symbol": "USD", "Action": "Interest",
            "Quantity": "0", "unitPrice": "0", "Fee": "0",
            "Subtotal": "18.00", "Note": "Interest paid",
        })
        m = d.month + 1
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        # Land on the 28th every month — no month-length special cases.
        d = date(y, m, 28)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def build(tmp: Path) -> None:
    """Write the portfolio into ``tmp/data``."""
    from tests.conftest import write_robinhood_csv, write_voya_csv

    # --- Taxable: funded 2022, one buy, one partial sell, still held ---
    write_robinhood_csv(tmp / "data" / "robinhood-1.csv", [
        {"Activity Date": _d(TAXABLE_START), "Trans Code": "ACH",
         "Instrument": "", "Description": "ACH Deposit",
         "Amount": "$10000.00"},
        {"Activity Date": _d(TAXABLE_START + timedelta(days=2)),
         "Trans Code": "Buy", "Instrument": "AAPL",
         "Description": "AAPL", "Quantity": "50",
         "Price": "$150.00", "Amount": "($7500.00)"},
        # A SECOND lot at a clearly different price, before the sell —
        # see property 3.  One lot makes every relief method agree.
        {"Activity Date": "05/10/2023", "Trans Code": "Buy",
         "Instrument": "AAPL", "Description": "AAPL", "Quantity": "20",
         "Price": "$190.00", "Amount": "($3800.00)"},
        # A partial exit, so realized and unrealized are both non-zero.
        {"Activity Date": "03/14/2025", "Trans Code": "Sell",
         "Instrument": "AAPL", "Description": "AAPL", "Quantity": "20",
         "Price": "$220.00", "Amount": "$4400.00"},
        # A later top-up, so the trailing windows carry a cash flow.
        {"Activity Date": "02/02/2026", "Trans Code": "ACH",
         "Instrument": "", "Description": "ACH Deposit",
         "Amount": "$2000.00"},
        {"Activity Date": "02/04/2026", "Trans Code": "Buy",
         "Instrument": "AAPL", "Description": "AAPL", "Quantity": "8",
         "Price": "$250.00", "Amount": "($2000.00)"},
        # A SHORT-term round trip.  Every other disposal here is held over
        # a year, and an ST/LT relation cannot catch a misclassification
        # while one side is structurally zero — property 3's shape again.
        # Under HIFO this relieves the $230 lot it just created.
        {"Activity Date": "09/02/2025", "Trans Code": "Buy",
         "Instrument": "AAPL", "Description": "AAPL", "Quantity": "10",
         "Price": "$230.00", "Amount": "($2300.00)"},
        {"Activity Date": "11/17/2025", "Trans Code": "Sell",
         "Instrument": "AAPL", "Description": "AAPL", "Quantity": "10",
         "Price": "$245.00", "Amount": "$2450.00"},
    ] + _dividend_rows())

    # --- Retirement: opens mid-2024, contributes to the present -------
    # Unit price climbs so the position appreciates even though the fund
    # name is multi-word (not price-fetchable — valued from txn history).
    rows = []
    d = RETIREMENT_START
    unit = 100.0
    while d <= date(2026, 8, 1):
        rows.append({
            "Activity Date": d.strftime("%Y-%m-%d"),
            "Activity": "CONTRIBUTION",
            "Fund": "VANG WELLINGTON ADM",
            "Money Source": "Before-Tax",
            "# of Units": "5.0",
            "Unit Price": f"{unit:.2f}",
            "Amount": f"{unit * 5:.2f}",
        })
        # Advance a month; nudge the unit price up ~1%/month.
        d = date(d.year + (d.month // 12), (d.month % 12) + 1, 1)
        unit *= 1.01
    write_voya_csv(tmp / "data" / "voya-401k-1.csv", rows)

    # --- Savings: a third account group, a third account TYPE, and the
    # only source of Interest income.  Without it the Income tab's
    # Interest / Rewards / Lending columns are all structurally zero and
    # the relation between those columns and their stat cards holds no
    # matter what the code does — it would only ever exercise Dividends.
    _write_apple_savings(tmp / "data" / "apple-savings.csv")

    with open(tmp / "data" / "metadata.csv", "w",
              newline="", encoding="utf-8") as f:
        f.write("Type,Date,Amount,Symbol,Note\n")
        f.write("Account Group,,,Robinhood,Robinhood\n")
        f.write("Account Group,,,Voya 401K,Rollover IRA\n")
        f.write("Account Type,,,Robinhood,Taxable\n")
        f.write("Account Type,,,Rollover IRA,Retirement\n")
        f.write("Account Group,,,Apple Savings,Apple Savings\n")
        f.write("Account Type,,,Apple Savings,Savings\n")
        # A per-account lot-relief override, so the ANNOTATED walk and the
        # pure-FIFO comparison table produce different realized gains.
        # The dashboard has two sources for "Realized" and they are only
        # distinguishable when some account is not on the default.
        f.write("Lot Method,,,Robinhood,HIFO\n")
        # Tax-tab inputs.  Without a filing status and a salary the
        # bracket-fill panel has no income to place, so every relation
        # over it would hold on a row of zeros.
        f.write("Filing Status,,,,Single\n")
        f.write("Salary History,2022-01-01,90000,,Base\n")
        f.write("Salary History,2025-01-01,110000,,Raise\n")
        f.write("State,,,,WA\n")
        # Drives the Income tab's expense-coverage card.
        f.write("Annual Expenses,2024-01-01,48000,,Estimated\n")


def dense_prices(symbol_curves: dict[str, tuple[float, float]],
                 end: date) -> dict[str, dict[str, float]]:
    """Daily price series from ``PRICE_START`` to ``end``.

    ``symbol_curves`` maps symbol -> (start_price, annual_growth).  A
    sparse series is not good enough here: `get_price` walks back only
    seven days, so a snapshot cadence of every two weeks against a
    handful of hand-picked dates leaves most snapshots unpriced — and an
    unpriced benchmark renders as an em-dash, which turns every
    comparison assertion vacuous without failing anything.
    """
    out: dict[str, dict[str, float]] = {}
    span_days = (end - PRICE_START).days
    for sym, (p0, growth) in symbol_curves.items():
        series: dict[str, float] = {}
        for i in range(span_days + 1):
            day = PRICE_START + timedelta(days=i)
            yrs = i / 365.25
            series[day.strftime("%Y-%m-%d")] = round(p0 * (1 + growth) ** yrs, 4)
        out[sym] = series
    return out
