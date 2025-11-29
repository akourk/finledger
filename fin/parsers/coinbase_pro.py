"""
Coinbase Pro / GDAX Parser
==========================
Parser for Coinbase Pro / GDAX transaction files.
"""

import pathlib

import pandas as pd

from fin.config import SYMBOL_MAP, UNIFIED_COLUMNS
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date


def parse_coinbase_pro(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Coinbase Pro / GDAX transaction files.
    Format: portfolio, type, time, amount, balance, amount/balance unit, transfer id, trade id, order id

    Transaction types:
    - deposit: funds/crypto coming in (positive amount)
    - withdrawal: funds/crypto going out (negative amount)
    - match: trade execution (positive = bought asset, negative = sold asset)
    - fee: trading fees (negative amount, always in USD)

    Date format: ISO 8601 (2017-12-12T03:19:50.252Z)
    """
    df = pd.read_csv(file_path)

    if df.empty:
        return create_empty_dataframe()

    results = []

    for _, row in df.iterrows():
        tx_type = row["type"]
        amount = float(row["amount"])
        unit = row["amount/balance unit"]
        time_str = row["time"]
        trade_id = row.get("trade id", "")

        # Parse date from ISO format
        date_str = parse_date(time_str.split("T")[0] if "T" in str(time_str) else time_str)

        # Skip deposits and withdrawals - these are internal transfers between Coinbase and GDAX/Pro
        if tx_type in ["deposit", "withdrawal"]:
            continue
        elif tx_type == "match":
            # Match = trade execution
            if amount > 0:
                action = "Buy"
                quantity = abs(amount)
                tx_amount = abs(amount)
            else:
                action = "Sell"
                quantity = amount  # Keep negative for proper tracking
                tx_amount = abs(amount)
            note = f"Trade ID: {trade_id}" if trade_id else ""
        elif tx_type == "fee":
            action = "Fee"
            quantity = 0
            tx_amount = abs(amount)
            note = "Trading fee"
        else:
            # Unknown type, skip
            continue

        # Map crypto symbols to yfinance format
        symbol = unit
        if symbol in SYMBOL_MAP:
            symbol = SYMBOL_MAP[symbol]
        elif symbol not in ["USD"] and not symbol.endswith("-USD"):
            # Add -USD suffix for crypto if not already present
            if symbol in ["ETH", "BTC", "LTC", "BCH", "ZEC", "ZRX"]:
                symbol = f"{symbol}-USD"

        results.append(
            {
                "Date": date_str,
                "Account": "Coinbase",  # Consolidate with regular Coinbase
                "Symbol": symbol,
                "Action": action,
                "Quantity": quantity,
                "Price": 0,  # Price not directly available in this format
                "Fee": 0,  # Fees are separate rows
                "Amount": tx_amount,
                "Currency": "USD",
                "Note": note,
                "Source": "Coinbase Pro",
            }
        )

    if not results:
        return create_empty_dataframe()

    result = pd.DataFrame(results, columns=UNIFIED_COLUMNS)
    return result
