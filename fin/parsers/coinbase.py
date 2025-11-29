"""
Coinbase Parser
===============
Parser for Coinbase transaction files.
"""

import pathlib
import re

import pandas as pd

from fin.config import UNIFIED_COLUMNS
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import clean_currency, parse_date, standardize_action


def parse_coinbase(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Coinbase transaction files.
    Format: Has 3 metadata lines, then header on line 4
    Columns: ID, Timestamp, Transaction Type, Asset, Quantity Transacted, Price Currency,
             Price at Transaction, Subtotal, Total (inclusive of fees), Fees, Notes
    Date format: YYYY-MM-DD HH:MM:SS UTC

    Special handling:
    1. ETH→ETH2 conversions: Generate the "to" side (+ETH2) since both map to ETH-USD
    2. Internal Pro/Exchange transfers: Skip these since the actual trades happen on the Pro side
    """
    # Skip first 3 metadata lines
    df = pd.read_csv(file_path, skiprows=3)

    # Transaction types that represent internal transfers within Coinbase ecosystem
    # These should be skipped because they don't represent actual net holdings changes:
    INTERNAL_TRANSFER_TYPES = {
        "pro withdrawal",  # ETH coming from Pro (already bought on Pro)
        "pro deposit",  # ETH going to Pro (will be sold on Pro)
        "exchange withdrawal",  # Same as Pro Withdrawal (older format)
        "exchange deposit",  # Same as Pro Deposit (older format)
        "vault withdrawal",  # Internal Coinbase transfer from Vault
        "retail staking transfer",  # Internal ETH→ETH2 staking move
        "retail unstaking transfer",  # Internal ETH2→ETH unstaking move
        "retail eth2 deprecation",  # ETH2→ETH conversion when ETH2 deprecated
        "transfer",  # Generic internal transfers
    }

    # Filter out internal Pro/Exchange transfers BEFORE creating result DataFrame
    df["tx_type_lower"] = df["Transaction Type"].fillna("").str.lower().str.strip()
    df_filtered = df[~df["tx_type_lower"].isin(INTERNAL_TRANSFER_TYPES)].copy()

    result = create_empty_dataframe()
    result["Date"] = df_filtered["Timestamp"].apply(parse_date)
    result["Account"] = "Coinbase"
    result["Symbol"] = df_filtered["Asset"].fillna("")
    result["Action"] = df_filtered["Transaction Type"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df_filtered["Quantity Transacted"], errors="coerce").fillna(
        0
    )
    result["Price"] = df_filtered["Price at Transaction"].apply(clean_currency)
    result["Fee"] = df_filtered["Fees and/or Spread"].apply(clean_currency)
    result["Amount"] = df_filtered["Total (inclusive of fees and/or spread)"].apply(clean_currency)
    result["Currency"] = df_filtered["Price Currency"].fillna("USD")
    result["Note"] = df_filtered["Notes"].fillna("")
    result["Source"] = "Coinbase"

    # Generate the "to" side for conversions where Coinbase only shows the "from" side
    convert_rows = []
    convert_pattern = re.compile(
        r"Converted\s+([\d.]+)\s+(\w+)\s+to\s+([\d.]+)\s+(\w+)", re.IGNORECASE
    )

    for idx, row in df_filtered.iterrows():
        tx_type = str(row["Transaction Type"]).lower() if pd.notna(row["Transaction Type"]) else ""
        notes = str(row["Notes"]) if pd.notna(row["Notes"]) else ""
        asset = str(row["Asset"]).upper() if pd.notna(row["Asset"]) else ""

        if tx_type == "convert" and notes:
            match = convert_pattern.search(notes)
            if match:
                from_qty = float(match.group(1))
                from_symbol = match.group(2).upper()
                to_qty = float(match.group(3))
                to_symbol = match.group(4).upper()

                # Generate the "to" side for ETH→ETH2 conversions
                if from_symbol == "ETH" and to_symbol == "ETH2":
                    convert_rows.append(
                        {
                            "Date": parse_date(row["Timestamp"]),
                            "Account": "Coinbase",
                            "Symbol": to_symbol,
                            "Action": "Transfer",
                            "Quantity": to_qty,
                            "Price": clean_currency(row["Price at Transaction"]),
                            "Fee": 0.0,
                            "Amount": abs(
                                clean_currency(row["Total (inclusive of fees and/or spread)"])
                            ),
                            "Currency": (
                                row["Price Currency"] if pd.notna(row["Price Currency"]) else "USD"
                            ),
                            "Note": f"Received from conversion: {notes}",
                            "Source": "Coinbase",
                        }
                    )

                # Generate the "to" side for OTHER→ETH conversions
                elif to_symbol == "ETH" and from_symbol != "ETH":
                    convert_rows.append(
                        {
                            "Date": parse_date(row["Timestamp"]),
                            "Account": "Coinbase",
                            "Symbol": to_symbol,
                            "Action": "Transfer",
                            "Quantity": to_qty,
                            "Price": clean_currency(row["Price at Transaction"]),
                            "Fee": 0.0,
                            "Amount": abs(
                                clean_currency(row["Total (inclusive of fees and/or spread)"])
                            ),
                            "Currency": (
                                row["Price Currency"] if pd.notna(row["Price Currency"]) else "USD"
                            ),
                            "Note": f"Received from conversion: {notes}",
                            "Source": "Coinbase",
                        }
                    )

    # Append the generated "to" side transactions
    if convert_rows:
        convert_df = pd.DataFrame(convert_rows, columns=UNIFIED_COLUMNS)
        result = pd.concat([result, convert_df], ignore_index=True)

    return result
