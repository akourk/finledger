"""
Formatting Utilities Module
===========================
Functions for cleaning, parsing, and standardizing data values.
"""

from datetime import datetime

import pandas as pd

from fin.config import ACTION_MAP


def clean_currency(value) -> float:
    """Convert currency string to float (handles $, commas, negative signs, parentheses)."""
    if pd.isna(value) or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # Remove $, commas
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if cleaned == "" or cleaned == "-":
        return 0.0
    # Handle parentheses for negative numbers: (123.45) -> -123.45
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned)


def standardize_action(action: str) -> str:
    """Map various action names to standardized action types."""
    if pd.isna(action):
        return "Unknown"
    action_lower = action.lower().strip()

    # Check for exact matches first
    if action_lower in ACTION_MAP:
        return ACTION_MAP[action_lower]

    # Check for partial matches
    for key, value in ACTION_MAP.items():
        if key in action_lower:
            return value

    return action.strip().title()


def parse_date(date_str: str, format_hint: str = "auto") -> str:
    """Parse various date formats and return YYYY-MM-DD string."""
    if pd.isna(date_str):
        return ""

    date_str = str(date_str).strip()

    # Handle "as of" dates (Schwab)
    if " as of " in date_str:
        date_str = date_str.split(" as of ")[0]

    # Handle UTC timestamps (Coinbase)
    if " UTC" in date_str:
        date_str = date_str.replace(" UTC", "")

    # Try different formats
    formats = [
        "%Y-%m-%d",  # 2023-01-15
        "%Y-%m-%d %H:%M:%S",  # 2023-01-15 10:30:00
        "%m/%d/%Y",  # 01/15/2023
        "%d/%m/%Y",  # 15/01/2023 (European)
    ]

    # If format hint is European (DD/MM/YYYY)
    if format_hint == "european":
        formats = ["%d/%m/%Y"] + formats

    for fmt in formats:
        try:
            dt = datetime.strptime(
                date_str.split()[0] if " " in date_str and fmt == "%Y-%m-%d" else date_str, fmt
            )
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Last resort - try pandas
    try:
        return pd.to_datetime(date_str).strftime("%Y-%m-%d")
    except:
        return date_str
