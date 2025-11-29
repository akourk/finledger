"""
Robinhood Parser
================
Parser for Robinhood transaction files.
"""

import pathlib

import pandas as pd

from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import clean_currency, parse_date, standardize_action


def parse_robinhood(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Robinhood transaction files.
    Format: Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code, Quantity, Price, Amount
    Date format: MM/DD/YYYY
    Note: Description field may contain newlines, so we must handle that.
    Note: File may have empty rows and disclaimer text at the end.
    """
    # Read with error handling for malformed lines (Description field contains newlines)
    df = pd.read_csv(file_path, on_bad_lines="warn")

    # Filter out empty rows and disclaimer rows (rows where Activity Date is empty/NaN)
    df = df.dropna(subset=["Activity Date"])
    df = df[df["Activity Date"].astype(str).str.strip() != ""]

    result = create_empty_dataframe()
    result["Date"] = df["Activity Date"].apply(parse_date)
    result["Account"] = "Robinhood"
    result["Symbol"] = df["Instrument"].fillna("")
    result["Action"] = df["Trans Code"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    result["Price"] = df["Price"].apply(clean_currency)
    result["Fee"] = 0.0
    result["Amount"] = df["Amount"].apply(clean_currency)
    result["Currency"] = "USD"
    result["Note"] = df["Description"].fillna("").astype(str).str.replace("\n", " ", regex=False)
    result["Source"] = "Robinhood"

    return result
