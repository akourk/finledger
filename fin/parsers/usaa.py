"""
USAA Victory Capital Parser
===========================
Parser for USAA Victory Capital transaction files.
"""

import pathlib

import pandas as pd

from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, standardize_action


def parse_usaa_victory_capital(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse USAA Victory Capital transaction files.
    Format: Account, Currency, Date, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note
    Date format: DD/MM/YYYY (European format!)
    """
    df = pd.read_csv(file_path)

    # Filter out empty rows (rows where Date is empty/NaN)
    df = df.dropna(subset=["Date"])
    df = df[df["Date"].astype(str).str.strip() != ""]

    result = create_empty_dataframe()
    result["Date"] = df["Date"].apply(lambda x: parse_date(x, format_hint="european"))
    result["Account"] = df.get("Account", "USAA Victory Capital").fillna("USAA Victory Capital")
    result["Symbol"] = df["Symbol"].fillna("")
    result["Action"] = df["Action"].apply(standardize_action)
    # Handle quantity with possible trailing spaces
    result["Quantity"] = df["Quantity"].apply(lambda x: float(str(x).strip()) if pd.notna(x) else 0)
    result["Price"] = pd.to_numeric(df["unitPrice"], errors="coerce").fillna(0)
    result["Fee"] = pd.to_numeric(df["Fee"], errors="coerce").fillna(0)
    result["Amount"] = pd.to_numeric(df["Subtotal"], errors="coerce").fillna(0)
    result["Currency"] = df.get("Currency", "USD").fillna("USD")
    result["Note"] = df["Note"].fillna("").str.strip()
    result["Source"] = "USAA Victory Capital"

    return result
