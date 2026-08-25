"""A synthetic portfolio whose accounts START AT DIFFERENT TIMES.

`build_synthetic_portfolio` is pinned by exact-figure assertions in
`test_pipeline_snapshot.py`, so it cannot absorb new transactions.  This
one exists separately because the dashboard-consistency checks need
properties that fixture does not have, and both properties are
load-bearing rather than incidental:

1. **Staggered account starts.**  The pairing bug F-031 broke is
   invisible when every filter's natural window equals the portfolio's.
   The 401K here opens two and a half years after the taxable account.
2. **Activity that reaches the present.**  Trailing windows (1y, 6mo,
   3mo, 30day) collapse to a flat zero on a portfolio whose data stops
   years ago, which would leave most of the (filter x window) matrix
   asserting nothing.

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
    ])

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

    with open(tmp / "data" / "metadata.csv", "w",
              newline="", encoding="utf-8") as f:
        f.write("Type,Date,Amount,Symbol,Note\n")
        f.write("Account Group,,,Robinhood,Robinhood\n")
        f.write("Account Group,,,Voya 401K,Rollover IRA\n")
        f.write("Account Type,,,Robinhood,Taxable\n")
        f.write("Account Type,,,Rollover IRA,Retirement\n")


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
