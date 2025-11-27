"""
Schwab Parser
=============
Parser for Schwab transaction files (Rollover IRA, Roth Contributory IRA).
"""

import pathlib
import pandas as pd
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, clean_currency, standardize_action


def parse_schwab(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Schwab transaction files (Rollover IRA, Roth Contributory IRA).
    Format: Date, Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount
    Date format: MM/DD/YYYY (may have "as of" suffix)
    """
    df = pd.read_csv(file_path)
    
    # Determine account name from filename
    if "rollover" in file_path.name.lower():
        account = "Schwab Rollover IRA"
    elif "roth" in file_path.name.lower():
        account = "Schwab Roth Contributory IRA"
    else:
        account = "Schwab"
    
    result = create_empty_dataframe()
    result["Date"] = df["Date"].apply(parse_date)
    result["Account"] = account
    result["Symbol"] = df["Symbol"].fillna("")
    result["Action"] = df["Action"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["Quantity"], errors='coerce').fillna(0)
    result["Price"] = df["Price"].apply(clean_currency)
    result["Fee"] = df["Fees & Comm"].apply(clean_currency)
    result["Amount"] = df["Amount"].apply(clean_currency)
    result["Currency"] = "USD"
    result["Note"] = df["Description"].fillna("")
    result["Source"] = "Schwab"
    
    return result
