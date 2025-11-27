"""
Parser Base Module
==================
Common functions used by all parsers.
"""

import pandas as pd
from fin.config import UNIFIED_COLUMNS, SYMBOL_MAP, ACCOUNT_MERGE_MAP


def create_empty_dataframe() -> pd.DataFrame:
    """Create an empty DataFrame with the unified schema."""
    return pd.DataFrame(columns=UNIFIED_COLUMNS)


def normalize_amounts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize Amount values to be consistently positive for most transaction types.
    
    Convention:
    - Buy/Contribution: Positive (amount invested)
    - Sell: Positive (amount received)  
    - Dividend/Interest/Staking: Positive (income received)
    - Fee/Tax: Positive (amount paid)
    - Transfer: Keep original sign (positive = in, negative = out)
    - Other corporate actions: Absolute value
    """
    df = df.copy()
    
    # Actions that should always have positive amounts
    positive_actions = [
        "Buy", "Sell", "Dividend", "Interest", "Staking", "Fee", "Tax",
        "StockLending", "OptionsExpiration", "StockSplit", "ReverseStockSplit", "SpinOff",
        "Exchange", "MergerCash", "MergerOut", "Liquidation", "CashInLieu",
        "Receive", "Other", "OptionBuy", "OptionSell"
    ]
    
    for action in positive_actions:
        mask = df["Action"] == action
        df.loc[mask, "Amount"] = df.loc[mask, "Amount"].abs()
    
    return df


def standardize_symbols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert fund names to standard ticker symbols.
    """
    df = df.copy()
    df["Symbol"] = df["Symbol"].replace(SYMBOL_MAP)
    return df


def merge_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge accounts that have been transferred/rolled over into other accounts.
    Uses ACCOUNT_MERGE_MAP to rename old account names to their new merged account.
    """
    df = df.copy()
    df["Account"] = df["Account"].replace(ACCOUNT_MERGE_MAP)
    return df
