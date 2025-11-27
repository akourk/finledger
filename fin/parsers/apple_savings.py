"""
Apple Savings Parser
====================
Parser for Apple Savings transaction files.
"""

import pathlib
import pandas as pd
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, standardize_action


def parse_apple_savings(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Apple Savings transaction files.
    Format: Account, Date, Currency, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note
    Date format: YYYY-MM-DD (already normalized)
    """
    df = pd.read_csv(file_path)
    
    result = create_empty_dataframe()
    result["Date"] = df["Date"].apply(parse_date)
    result["Account"] = df.get("Account", "Apple Savings").fillna("Apple Savings")
    result["Symbol"] = df["Symbol"].fillna("")
    result["Action"] = df["Action"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["Quantity"], errors='coerce').fillna(0)
    result["Price"] = pd.to_numeric(df["unitPrice"], errors='coerce').fillna(0)
    result["Fee"] = pd.to_numeric(df["Fee"], errors='coerce').fillna(0)
    result["Amount"] = pd.to_numeric(df["Subtotal"], errors='coerce').fillna(0)
    result["Currency"] = df.get("Currency", "USD").fillna("USD")
    result["Note"] = df["Note"].fillna("")
    result["Source"] = "Apple Savings"
    
    return result
