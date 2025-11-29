"""
Holdings Calculator Module
=========================
Calculates current holdings quantities and values from transactions.
"""

import logging
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from ..utils.prices import get_price_from_yfinance

logger = logging.getLogger(__name__)


def get_signed_quantity(row: pd.Series) -> float:
    """
    Calculate signed quantity based on action type.
    Positive = shares acquired, Negative = shares disposed.

    Args:
        row: Transaction row from DataFrame

    Returns:
        Signed quantity (positive for acquisitions, negative for disposals)
    """
    action = row["Action"]
    qty = row["Quantity"]
    amount = row["Amount"]
    source = row["Source"]

    if action == "Buy":
        return abs(qty)
    elif action in ["StockSplit", "ReverseStockSplit"]:
        # Stock split events record the shares received/lost from the split
        # But since we already adjust historical quantities for splits in adjust_for_splits(),
        # counting these again would be double-counting. Skip them.
        return 0
    elif action == "SpinOff":
        # SpinOff gives new shares of a different company
        return abs(qty)
    elif action == "Sell":
        return -abs(qty)
    elif action in ["OptionBuy", "OptionSell", "OptionsExpiration"]:
        # Options are derivative contracts, not shares of the underlying stock
        # They should not affect the share count of the underlying symbol
        # Options positions would need to be tracked separately with their own symbols
        # (e.g., "AAPL 8/18/2023 Call $190.00" as recorded in the Note field)
        return 0
    elif action == "Transfer":
        # Source-specific transfer handling
        if source == "Robinhood":
            # Robinhood: Amount is positive for deposits, negative for withdrawals
            # The clean_currency function already converts parentheses to negative
            if amount >= 0:
                return abs(qty)
            else:
                return -abs(qty)
        elif source == "Coinbase":
            # Coinbase: Quantity already has correct sign (negative for outgoing)
            # Internal Pro/Exchange transfers are now filtered out at parse time
            return qty  # Already signed correctly
        elif source == "Coinbase Pro":
            # Coinbase Pro: Quantity already has correct sign (negative for outgoing)
            return qty  # Already signed correctly
        elif source == "Schwab":
            # Schwab: Check if this is a "Security Transfer" from a merged account
            # These should be skipped since the original buys are already counted
            # after account merging (e.g., USAA Victory Capital -> Schwab Roth)
            # Security transfers have a symbol and quantity (unlike cash transfers)
            symbol = row["Symbol"]
            if symbol and symbol != "" and qty != 0:
                # This is a security transfer (shares coming in)
                # Skip it since the original buys are counted after account merge
                return 0
            # Other transfers (MoneyLink, etc.) are cash movements, not holdings
            if amount >= 0:
                return abs(qty)
            else:
                return -abs(qty)
        elif source == "Voya Atos 401K":
            # Voya: Amount sign is reliable (positive = receiving, negative = sending)
            if amount >= 0:
                return abs(qty)
            else:
                return -abs(qty)
        else:
            # Default: use amount sign
            if amount >= 0:
                return abs(qty)
            else:
                return -abs(qty)
    elif action == "Staking":
        # Crypto staking rewards add to holdings
        return abs(qty)
    elif action in ["Dividend", "Interest"]:
        # Dividends/interest can be:
        # 1. Paid in cash (qty = 0) - no change to holdings
        # 2. Auto-reinvested with separate Buy transaction - no change (Buy handles it)
        # 3. Auto-reinvested without separate Buy transaction - adds shares
        #
        # Sources with separate Buy transactions for reinvested dividends:
        # - USAA Victory Capital: has "REINVEST LT CAP GAIN DIST" Buy rows
        # - Schwab: has "Reinvest Dividend" Buy rows
        #
        # Sources where dividend quantity IS the reinvestment (no separate Buy):
        # - Voya Atos 401K: dividend row itself has the reinvested shares
        if qty > 0 and source == "Voya Atos 401K":
            return abs(qty)
        return 0
    elif action == "MergerOut":
        # Merger: qty > 0 means shares received (stock-for-stock merger)
        # qty = 0 means shares removed (cash buyout)
        if qty > 0:
            return abs(qty)  # Receiving shares in new company
        else:
            return 0  # Just removing old shares (handled separately)
    elif action == "MergerCash":
        # Cash received from merger - doesn't affect share count
        # (The MergerOut with qty=0 handles the share removal)
        return 0
    elif action == "CashInLieu":
        # Cash payment for fractional shares - doesn't affect share count
        return 0
    else:
        # For other actions, assume no quantity change
        return 0


def calculate_holdings(
    df: pd.DataFrame,
    price_cache: Dict[str, Dict[str, float]],
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> pd.DataFrame:
    """
    Calculate current holdings for each account and asset combination.

    Holdings are calculated by summing up quantity changes from all transactions:
    - Buy/Contribution: +quantity
    - Sell: -quantity
    - Transfer: +/- based on source-specific logic and context
    - Dividend/Interest/Staking: +quantity (if reinvested, otherwise 0)
    - StockSplit: The split adjustment already modified historical quantities

    Args:
        df: DataFrame with transaction data
        price_cache: Nested dict of {symbol: {date: price}}
        unavailable_ticker_cache: Optional cache of tickers without historical data

    Returns:
        DataFrame with columns: Account, Symbol, Quantity, CurrentPrice, Value, Currency
    """
    if df.empty:
        logger.warning("Empty DataFrame passed to calculate_holdings")
        return pd.DataFrame(
            columns=["Account", "Symbol", "Quantity", "CurrentPrice", "Value", "Currency"]
        )

    logger.info("Calculating current holdings")

    # Create a copy to work with
    df = df.copy()

    try:
        df["SignedQty"] = df.apply(get_signed_quantity, axis=1)
    except Exception as e:
        logger.exception("Error calculating signed quantities")
        raise ValueError(f"Failed to calculate signed quantities: {e}")

    # Group by Account and Symbol, sum quantities
    holdings = df.groupby(["Account", "Symbol", "Currency"]).agg({"SignedQty": "sum"}).reset_index()

    holdings = holdings.rename(columns={"SignedQty": "Quantity"})

    # Filter out zero or negligible holdings (less than 0.0001)
    holdings = holdings[holdings["Quantity"].abs() > 0.0001]

    # Filter out cash/empty symbols
    holdings = holdings[holdings["Symbol"] != ""]
    holdings = holdings[holdings["Symbol"] != "USD"]

    # Get current prices for each symbol
    print("\nFetching current prices for holdings...")
    holdings["CurrentPrice"] = 0.0
    holdings["Value"] = 0.0

    unique_symbols = holdings["Symbol"].unique()
    current_prices = {}

    for symbol in unique_symbols:
        try:
            # Use today's date for current price
            today = datetime.now().strftime("%Y-%m-%d")
            price, _ = get_price_from_yfinance(
                symbol, today, price_cache, unavailable_ticker_cache, split_cache
            )
            if price is not None:
                current_prices[symbol] = price
        except Exception as e:
            print(f"  Warning: Could not get price for {symbol}: {e}")

    # Apply prices and calculate values
    for idx in holdings.index:
        symbol = holdings.loc[idx, "Symbol"]
        qty = holdings.loc[idx, "Quantity"]

        if symbol in current_prices:
            price = current_prices[symbol]
            holdings.loc[idx, "CurrentPrice"] = round(price, 4)
            holdings.loc[idx, "Value"] = round(qty * price, 2)

    # Sort by value descending
    holdings = holdings.sort_values("Value", ascending=False).reset_index(drop=True)

    # Round quantity for display
    holdings["Quantity"] = holdings["Quantity"].round(6)

    return holdings


def calculate_holdings_quantities_only(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate current holdings quantities only (no price lookups).
    Used for historical holdings where we'll look up historical prices separately.
    """
    if df.empty:
        return pd.DataFrame(columns=["Account", "Symbol", "Quantity", "Currency"])

    df = df.copy()

    df["SignedQty"] = df.apply(get_signed_quantity, axis=1)

    holdings = df.groupby(["Account", "Symbol", "Currency"]).agg({"SignedQty": "sum"}).reset_index()

    holdings = holdings.rename(columns={"SignedQty": "Quantity"})
    holdings = holdings[holdings["Quantity"].abs() > 0.0001]
    holdings = holdings[holdings["Symbol"] != ""]
    holdings = holdings[holdings["Symbol"] != "USD"]
    holdings["Quantity"] = holdings["Quantity"].round(6)
    holdings["Price"] = 0.0
    holdings["Value"] = 0.0

    return holdings


def add_running_balances(df: pd.DataFrame, holdings_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add running balance columns to master transactions DataFrame.

    For each transaction, calculates:
    - RunningBalance: The quantity held AFTER this transaction
    - RunningValue: The value at that time (balance × price at transaction)

    Calculations are done per (Account, Symbol) combination.
    Transactions are processed chronologically (oldest first) to accumulate balances.

    Uses the same get_signed_quantity logic as calculate_holdings for consistency.
    """
    if df.empty:
        return df

    df = df.copy()

    # Get current holdings for final balance reference
    current_holdings = {}
    if not holdings_df.empty:
        for _, row in holdings_df.iterrows():
            key = (row["Account"], row["Symbol"])
            current_holdings[key] = row["Quantity"]

    # Define get_signed_quantity for running balance (same as in calculate_holdings)
    def get_signed_qty_for_balance(row):
        action = row["Action"]
        qty = row["Quantity"]
        amount = row["Amount"]
        source = row["Source"]
        symbol = row["Symbol"]

        # For cash accounts (USD symbol)
        if symbol == "USD":
            if action == "Buy":
                return abs(qty) if qty != 0 else abs(amount)
            elif action == "Sell":
                return -(abs(qty) if qty != 0 else abs(amount))
            elif action == "Interest":
                return abs(qty) if qty != 0 else abs(amount)
            elif action == "Transfer":
                if amount >= 0:
                    return abs(qty) if qty != 0 else abs(amount)
                else:
                    return -(abs(qty) if qty != 0 else abs(amount))
            return 0

        if action == "Buy":
            return abs(qty)
        elif action in ["StockSplit", "ReverseStockSplit"]:
            return 0  # Already adjusted in historical quantities
        elif action == "SpinOff":
            return abs(qty)
        elif action == "Sell":
            return -abs(qty)
        elif action in ["OptionBuy", "OptionSell", "OptionsExpiration"]:
            return 0  # Options don't affect share count
        elif action == "Transfer":
            if source == "Robinhood":
                if amount >= 0:
                    return abs(qty)
                else:
                    return -abs(qty)
            elif source in ["Coinbase", "Coinbase Pro"]:
                return qty  # Already signed
            elif source == "Schwab":
                if symbol and symbol != "" and qty != 0:
                    return 0  # Security transfers skipped (original buys counted)
                if amount >= 0:
                    return abs(qty)
                else:
                    return -abs(qty)
            elif source == "Voya Atos 401K":
                if amount >= 0:
                    return abs(qty)
                else:
                    return -abs(qty)
            else:
                return qty
        elif action == "Staking":
            return abs(qty)
        elif action in ["Dividend", "Interest", "StockLending"]:
            return 0  # Cash income, not share changes
        elif action == "MergerOut":
            if qty == 0:
                return 0  # Removal record
            else:
                return abs(qty)  # New shares received
        elif action == "MergerCash":
            return 0  # Cash payment
        elif action in ["CashInLieu", "Liquidation"]:
            return 0  # Cash, not shares
        elif action == "Receive":
            return abs(qty)
        else:
            return 0

    # Sort chronologically (oldest first) for running balance calculation
    df = df.sort_values(["Account", "Symbol", "Date"], ascending=[True, True, True]).reset_index(
        drop=True
    )

    # Calculate running balance for each (Account, Symbol) group
    df["RunningBalance"] = 0.0
    df["RunningValue"] = 0.0

    # Group by Account and Symbol
    for (account, symbol), group in df.groupby(["Account", "Symbol"]):
        balance = 0.0

        for idx in group.index:
            row = df.loc[idx]
            signed_qty = get_signed_qty_for_balance(row)
            balance += signed_qty

            # Round to avoid floating point issues
            balance = round(balance, 6)

            df.loc[idx, "RunningBalance"] = balance

            # Calculate value at transaction time
            price = row["Price"]
            if price > 0:
                df.loc[idx, "RunningValue"] = round(balance * price, 2)
            else:
                # No price available, leave as 0
                df.loc[idx, "RunningValue"] = 0.0

    # Sort back to newest first (original order for display)
    df = df.sort_values("Date", ascending=False).reset_index(drop=True)

    return df
