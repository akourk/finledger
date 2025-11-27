"""
Voya Atos 401K Parser
=====================
Parser for Voya Atos 401K transaction files.
"""

import io
import pathlib
import pandas as pd
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, standardize_action


def parse_voya_atos401k(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Voya Atos 401K transaction files.
    Format: 2 text lines + 4 empty quoted lines = 6 lines before header
    Columns: Activity Date, Activity, Fund, Money Source, # of Units, Unit Price, Amount
    Date format: YYYY-MM-DD
    
    Note: Some transactions labeled as CONTRIBUTION have negative units/amounts,
    which represent corrections or reversals. These are converted to Sell actions.
    """
    # Read the file and find where the actual data header starts
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Find the header line (contains "Activity Date")
    header_idx = None
    for i, line in enumerate(lines):
        if "Activity Date" in line:
            header_idx = i
            break
    
    if header_idx is None:
        raise ValueError("Could not find 'Activity Date' header in Voya file")
    
    # Build clean CSV: header + data rows (skip metadata/quoted lines)
    valid_lines = [lines[header_idx]]
    for j in range(header_idx + 1, len(lines)):
        line = lines[j].strip()
        if line and not line.startswith('"'):
            valid_lines.append(lines[j])
    
    df = pd.read_csv(io.StringIO(''.join(valid_lines)))
    
    result = create_empty_dataframe()
    result["Date"] = df["Activity Date"].apply(parse_date)
    result["Account"] = "Voya Atos 401K"
    result["Symbol"] = df["Fund"].fillna("")
    result["Action"] = df["Activity"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["# of Units"], errors='coerce').fillna(0)
    result["Price"] = pd.to_numeric(df["Unit Price"], errors='coerce').fillna(0)
    result["Fee"] = 0.0
    result["Amount"] = pd.to_numeric(df["Amount"], errors='coerce').fillna(0)
    result["Currency"] = "USD"
    result["Note"] = df["Money Source"].fillna("")  # Tax treatment info
    result["Source"] = "Voya Atos 401K"
    
    # Handle negative quantities: if a "Buy" action has negative quantity, it's actually a Sell
    negative_buy_mask = (result["Action"] == "Buy") & (result["Quantity"] < 0)
    if negative_buy_mask.any():
        result.loc[negative_buy_mask, "Action"] = "Sell"
        result.loc[negative_buy_mask, "Quantity"] = result.loc[negative_buy_mask, "Quantity"].abs()
        result.loc[negative_buy_mask, "Amount"] = result.loc[negative_buy_mask, "Amount"].abs()
        # Add note about the correction
        for idx in result[negative_buy_mask].index:
            existing_note = result.loc[idx, "Note"]
            result.loc[idx, "Note"] = f"{existing_note}; Contribution reversal" if existing_note else "Contribution reversal"
    
    # Also handle negative dividends (rare but possible)
    negative_div_mask = (result["Action"] == "Dividend") & (result["Quantity"] < 0)
    if negative_div_mask.any():
        result.loc[negative_div_mask, "Quantity"] = result.loc[negative_div_mask, "Quantity"].abs()
        result.loc[negative_div_mask, "Amount"] = result.loc[negative_div_mask, "Amount"].abs()
        for idx in result[negative_div_mask].index:
            existing_note = result.loc[idx, "Note"]
            result.loc[idx, "Note"] = f"{existing_note}; Dividend adjustment" if existing_note else "Dividend adjustment"
    
    return result
