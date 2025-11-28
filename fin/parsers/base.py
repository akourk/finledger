"""
Parser Base Module
==================
Common functions used by all parsers.
"""

import logging
import pandas as pd
from typing import Tuple, List
from fin.config import UNIFIED_COLUMNS, SYMBOL_MAP, ACCOUNT_MERGE_MAP

logger = logging.getLogger(__name__)


def validate_dataframe(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate that DataFrame conforms to unified schema.
    
    Args:
        df: DataFrame to validate
    
    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []
    
    if df.empty:
        return True, []
    
    # Check required columns
    missing_cols = set(UNIFIED_COLUMNS) - set(df.columns)
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
    
    # Check date format
    if 'Date' in df.columns:
        try:
            pd.to_datetime(df['Date'])
        except Exception as e:
            errors.append(f"Invalid date format in Date column: {e}")
    
    # Check numeric columns
    numeric_cols = ['Quantity', 'Price', 'Fee', 'Amount']
    for col in numeric_cols:
        if col in df.columns:
            try:
                pd.to_numeric(df[col], errors='coerce')
            except Exception as e:
                errors.append(f"{col} must be numeric: {e}")
    
    # Check for required non-null values
    if 'Date' in df.columns and df['Date'].isna().all():
        errors.append("Date column cannot be all null")
    
    if 'Account' in df.columns and df['Account'].isna().all():
        errors.append("Account column cannot be all null")
    
    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning(f"DataFrame validation failed: {errors}")
    
    return is_valid, errors


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
    
    Args:
        df: DataFrame with transaction data
    
    Returns:
        DataFrame with normalized amounts
    """
    if df.empty:
        logger.debug("Empty DataFrame passed to normalize_amounts")
        return df
    
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
    
    Args:
        df: DataFrame with transaction data
    
    Returns:
        DataFrame with standardized symbols
    """
    if df.empty:
        return df
    
    df = df.copy()
    df["Symbol"] = df["Symbol"].replace(SYMBOL_MAP)
    logger.debug(f"Standardized {len(SYMBOL_MAP)} symbol mappings")
    return df


def merge_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge accounts that have been transferred/rolled over into other accounts.
    Uses ACCOUNT_MERGE_MAP to rename old account names to their new merged account.
    
    Args:
        df: DataFrame with transaction data
    
    Returns:
        DataFrame with merged account names
    """
    if df.empty:
        return df
    
    df = df.copy()
    merged_count = df["Account"].isin(ACCOUNT_MERGE_MAP.keys()).sum()
    df["Account"] = df["Account"].replace(ACCOUNT_MERGE_MAP)
    
    if merged_count > 0:
        logger.info(f"Merged {merged_count} transactions from rolled-over accounts")
    
    return df
