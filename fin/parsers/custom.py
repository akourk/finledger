"""
Custom Input Parser
===================
Parser for custom/pre-normalized input files.
"""

import pathlib
import pandas as pd
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, standardize_action


def parse_custom_input(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse custom input files (pre-normalized format).
    Format: Account, Date, Currency, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note
    Date format: YYYY-MM-DD
    """
    df = pd.read_csv(file_path)
    
    result = create_empty_dataframe()
    result["Date"] = df["Date"].apply(parse_date)
    result["Account"] = df.get("Account", "Custom").fillna("Custom")
    result["Symbol"] = df["Symbol"].fillna("")
    result["Action"] = df["Action"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["Quantity"], errors='coerce').fillna(0)
    result["Price"] = pd.to_numeric(df["unitPrice"], errors='coerce').fillna(0)
    result["Fee"] = pd.to_numeric(df["Fee"], errors='coerce').fillna(0)
    result["Amount"] = pd.to_numeric(df["Subtotal"], errors='coerce').fillna(0)
    result["Currency"] = df.get("Currency", "USD").fillna("USD")
    result["Note"] = df["Note"].fillna("")
    result["Source"] = "Custom Input"
    
    return result
