"""
Price and Split Utilities Module
================================
Functions for fetching prices from yfinance, handling stock splits,
and getting sector information.
"""

import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional

from fin.config import DELISTED_SYMBOLS, SYMBOL_MAP, PRICE_CONVERSION_MAP
from fin.utils.cache import save_sector_cache


def get_price_from_yfinance(ticker: str, date_str: str, cache: dict) -> tuple[Optional[float], bool]:
    """
    Get the closing price for a ticker on a specific date.
    Uses cache first, then fetches from yfinance if not cached.
    Handles weekends/holidays by looking at previous trading days.
    
    Returns: (price, was_fetched) where was_fetched indicates if a new fetch was made
    """
    # Skip delisted symbols - they no longer trade
    if ticker in DELISTED_SYMBOLS:
        return None, False  # No fetch needed, symbol is delisted
    
    # Check cache first
    if ticker in cache and date_str in cache[ticker]:
        return cache[ticker][date_str], False  # From cache, no fetch needed
    
    # Parse the date
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    # Fetch data for a range around the target date (to handle weekends/holidays)
    start_date = target_date - timedelta(days=10)
    end_date = target_date + timedelta(days=1)
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(start=start_date.strftime("%Y-%m-%d"), 
                            end=end_date.strftime("%Y-%m-%d"))
        
        if hist.empty:
            print(f"Warning: No price data found for {ticker} around {date_str}")
            return None, True
        
        # Find the closest trading day on or before target date
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        valid_dates = hist.index[hist.index <= target_date]
        
        if len(valid_dates) == 0:
            # Use earliest available if target is before all data
            closest_date = hist.index[0]
        else:
            closest_date = valid_dates[-1]
        
        price = float(hist.loc[closest_date, "Close"])
        
        # Cache the result
        if ticker not in cache:
            cache[ticker] = {}
        cache[ticker][date_str] = price
        
        return price, True  # New fetch was made
        
    except Exception as e:
        print(f"Warning: Error fetching price for {ticker} on {date_str}: {e}")
        return None, True


def get_price_changes(symbols: list, price_cache: dict) -> dict:
    """
    Calculate 7-day and 30-day price changes for a list of symbols.
    
    Returns: {symbol: {"change_7d": pct, "change_30d": pct, "price_7d": price, "price_30d": price}}
    """
    today = datetime.now()
    date_7d = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    date_30d = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    
    results = {}
    
    print(f"Fetching historical prices for {len(symbols)} symbols (7d/30d changes)...")
    
    for symbol in symbols:
        # Get current price
        current_price, _ = get_price_from_yfinance(symbol, today_str, price_cache)
        
        if current_price is None or current_price == 0:
            results[symbol] = {
                "change_7d": None,
                "change_30d": None,
                "price_7d": None,
                "price_30d": None
            }
            continue
        
        # Get 7-day ago price
        price_7d, _ = get_price_from_yfinance(symbol, date_7d, price_cache)
        change_7d = None
        if price_7d is not None and price_7d > 0:
            change_7d = ((current_price - price_7d) / price_7d) * 100
        
        # Get 30-day ago price
        price_30d, _ = get_price_from_yfinance(symbol, date_30d, price_cache)
        change_30d = None
        if price_30d is not None and price_30d > 0:
            change_30d = ((current_price - price_30d) / price_30d) * 100
        
        results[symbol] = {
            "change_7d": round(change_7d, 2) if change_7d is not None else None,
            "change_30d": round(change_30d, 2) if change_30d is not None else None,
            "price_7d": round(price_7d, 4) if price_7d is not None else None,
            "price_30d": round(price_30d, 4) if price_30d is not None else None
        }
    
    return results


def get_split_multiplier(ticker: str, transaction_date_str: str, split_cache: dict) -> float:
    """
    Calculate the cumulative split multiplier for a ticker from a transaction date to today.
    
    For example, if NVDA had a 4:1 split on 2021-07-20 and a 10:1 split on 2024-06-10,
    a transaction from 2020-09-09 would have a multiplier of 40 (4 * 10).
    A transaction from 2022-01-01 would have a multiplier of 10 (only the 2024 split applies).
    
    Returns the multiplier to apply to quantity (and inverse to apply to price).
    """
    # Check if we have cached splits for this ticker
    if ticker not in split_cache:
        # Fetch splits from yfinance
        try:
            stock = yf.Ticker(ticker)
            splits = stock.splits
            
            if splits is None or splits.empty:
                split_cache[ticker] = {}
            else:
                # Convert to dict with date string keys
                split_cache[ticker] = {
                    date.strftime("%Y-%m-%d"): float(ratio) 
                    for date, ratio in splits.items()
                }
        except Exception as e:
            print(f"Warning: Error fetching splits for {ticker}: {e}")
            split_cache[ticker] = {}
    
    # Calculate cumulative multiplier for splits after the transaction date
    splits_dict = split_cache.get(ticker, {})
    if not splits_dict:
        return 1.0
    
    transaction_date = datetime.strptime(transaction_date_str, "%Y-%m-%d")
    multiplier = 1.0
    
    for split_date_str, ratio in splits_dict.items():
        split_date = datetime.strptime(split_date_str, "%Y-%m-%d")
        if split_date > transaction_date:
            multiplier *= ratio
    
    return multiplier


def adjust_for_splits(df: pd.DataFrame, split_cache: dict) -> pd.DataFrame:
    """
    Adjust historical quantities and prices for stock splits that occurred after each transaction.
    
    For Buy/Sell transactions:
    - Quantity is multiplied by the cumulative split ratio
    - Price is divided by the cumulative split ratio
    - Amount stays the same (quantity * price = constant)
    
    For StockSplit/ReverseStockSplit transactions:
    - These record the split event itself, so we don't adjust them
    """
    if df.empty:
        return df
    
    df = df.copy()
    
    # Actions that should have quantity/price adjusted for splits
    # Note: Options (OptionBuy, OptionSell) are excluded - they have their own strike/expiry
    adjustable_actions = ["Buy", "Sell", "Dividend", "Transfer"]
    
    # Get unique symbols that need split checking (exclude crypto, cash, etc.)
    # Filter to only include symbols that look like stock tickers
    mask = (
        df["Action"].isin(adjustable_actions) & 
        (df["Quantity"] != 0) &
        (df["Symbol"].str.len() <= 5) &  # Most stock tickers are 1-5 chars
        (df["Symbol"].str.match(r'^[A-Z]+$', na=False))  # Only letters
    )
    
    symbols_to_check = df.loc[mask, "Symbol"].unique()
    
    if len(symbols_to_check) == 0:
        return df
    
    print(f"Checking {len(symbols_to_check)} symbols for stock splits...")
    
    adjusted_count = 0
    for symbol in symbols_to_check:
        symbol_mask = mask & (df["Symbol"] == symbol)
        
        for idx in df[symbol_mask].index:
            date_str = df.loc[idx, "Date"]
            if not date_str:
                continue
                
            multiplier = get_split_multiplier(symbol, date_str, split_cache)
            
            if multiplier != 1.0:
                original_qty = df.loc[idx, "Quantity"]
                original_price = df.loc[idx, "Price"]
                
                df.loc[idx, "Quantity"] = round(original_qty * multiplier, 6)
                if original_price > 0:
                    df.loc[idx, "Price"] = round(original_price / multiplier, 6)
                
                # Add note about split adjustment
                split_note = f"Split adjusted {multiplier:.0f}:1"
                existing_note = df.loc[idx, "Note"]
                df.loc[idx, "Note"] = f"{existing_note}; {split_note}" if existing_note else split_note
                adjusted_count += 1
    
    if adjusted_count > 0:
        print(f"  Adjusted {adjusted_count} transactions for stock splits")
    
    return df


def convert_price_based_symbols(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    """
    Convert symbols that require price-based quantity recalculation.
    For symbols in PRICE_CONVERSION_MAP, recalculates quantity = amount / target_price.
    """
    if df.empty:
        return df
    
    df = df.copy()
    
    for source_symbol, target_ticker in PRICE_CONVERSION_MAP.items():
        mask = df["Symbol"] == source_symbol
        if not mask.any():
            continue
        
        print(f"Converting {mask.sum()} transactions from '{source_symbol}' to '{target_ticker}'...")
        
        for idx in df[mask].index:
            date_str = df.loc[idx, "Date"]
            amount = df.loc[idx, "Amount"]
            
            if pd.isna(amount) or amount == 0:
                # Can't convert without an amount
                df.loc[idx, "Symbol"] = target_ticker
                continue
            
            price, _ = get_price_from_yfinance(target_ticker, date_str, cache)
            
            if price is not None and price > 0:
                new_quantity = abs(float(amount)) / price
                df.loc[idx, "Quantity"] = round(new_quantity, 6)
                df.loc[idx, "Price"] = round(price, 4)
                df.loc[idx, "Symbol"] = target_ticker
                df.loc[idx, "Note"] = f"{df.loc[idx, 'Note']}; Converted from {source_symbol}" if df.loc[idx, 'Note'] else f"Converted from {source_symbol}"
            else:
                # Couldn't get price, just update symbol
                df.loc[idx, "Symbol"] = target_ticker
                df.loc[idx, "Note"] = f"{df.loc[idx, 'Note']}; Price lookup failed" if df.loc[idx, 'Note'] else "Price lookup failed"
    
    return df


def get_sector_info(symbol: str, sector_cache: dict) -> tuple[str, bool]:
    """
    Get sector information for a symbol from yfinance.
    Uses cache first, then fetches if not cached.
    
    Returns: (sector, was_fetched)
    """
    # Check cache first
    if symbol in sector_cache:
        return sector_cache[symbol], False
    
    # Handle known special cases
    if symbol.endswith("-USD"):
        # Cryptocurrency
        sector_cache[symbol] = "Cryptocurrency"
        return "Cryptocurrency", True
    
    if symbol == "USD":
        sector_cache[symbol] = "Cash"
        return "Cash", True
    
    # Try to fetch from yfinance
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        quote_type = info.get("quoteType", "")
        sector = info.get("sector")
        
        if sector:
            sector_cache[symbol] = sector
            return sector, True
        elif quote_type == "MUTUALFUND":
            sector_cache[symbol] = "Mutual Funds"
            return "Mutual Funds", True
        elif quote_type == "ETF":
            # Try to get category for ETFs
            category = info.get("category", "")
            if "bond" in category.lower():
                sector_cache[symbol] = "Bonds"
            elif "real estate" in category.lower():
                sector_cache[symbol] = "Real Estate"
            else:
                sector_cache[symbol] = "ETFs"
            return sector_cache[symbol], True
        elif quote_type == "CRYPTOCURRENCY":
            sector_cache[symbol] = "Cryptocurrency"
            return "Cryptocurrency", True
        else:
            sector_cache[symbol] = "Other"
            return "Other", True
    except Exception as e:
        print(f"Warning: Could not get sector for {symbol}: {e}")
        sector_cache[symbol] = "Unknown"
        return "Unknown", True


def get_sectors_for_holdings(holdings_df: pd.DataFrame, sector_cache: dict) -> dict:
    """
    Get sector information for all holdings.
    Returns dict mapping symbol -> sector.
    """
    if holdings_df.empty:
        return {}
    
    symbols = holdings_df["Symbol"].unique()
    result = {}
    fetched_count = 0
    
    print(f"Getting sector information for {len(symbols)} symbols...")
    
    for symbol in symbols:
        sector, was_fetched = get_sector_info(symbol, sector_cache)
        result[symbol] = sector
        if was_fetched:
            fetched_count += 1
    
    if fetched_count > 0:
        print(f"Fetched sector info for {fetched_count} new symbols")
        save_sector_cache(sector_cache)
    
    return result
