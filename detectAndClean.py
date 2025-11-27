"""
Transaction Data Aggregator
===========================
Reads transaction history files from multiple financial sources,
normalizes them to a unified format, and outputs a master CSV.

Supported Sources:
- Schwab (Rollover IRA, Roth Contributory IRA)
- Robinhood
- Coinbase
- Vanguard SF 401K
- USAA Victory Capital
- Voya Atos 401K
- Apple Savings
- Custom Input (pre-normalized format)
"""

import io
import json
import numpy as np
import pandas as pd
import pathlib
from datetime import datetime, timedelta
from typing import Optional, Callable
import re
import yfinance as yf


# =============================================================================
# Configuration
# =============================================================================

PATH = pathlib.Path(__file__).resolve().parents[0]
DATA_PATH = PATH.joinpath("data").resolve()
DATA_INPUT_PATH = DATA_PATH.joinpath("input").resolve()
DATA_OUTPUT_PATH = DATA_PATH.joinpath("output").resolve()
DASHBOARD_PATH = PATH.joinpath("dashboard").resolve()
DASHBOARD_DATA_PATH = DASHBOARD_PATH.joinpath("data").resolve()

# Ensure directories exist
DATA_PATH.mkdir(parents=True, exist_ok=True)
DATA_INPUT_PATH.mkdir(parents=True, exist_ok=True)
DATA_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
DASHBOARD_PATH.mkdir(parents=True, exist_ok=True)
DASHBOARD_DATA_PATH.mkdir(parents=True, exist_ok=True)

# Unified output schema - all parsers must output DataFrames with these columns
UNIFIED_COLUMNS = [
    "Date",       # YYYY-MM-DD format
    "Account",    # Source account name
    "Symbol",     # Ticker symbol
    "Action",     # Standardized: Buy, Sell, Dividend, Transfer, Interest, Fee, Staking, Contribution
    "Quantity",   # Number of shares/units
    "Price",      # Unit price
    "Fee",        # Transaction fees
    "Amount",     # Total transaction amount
    "Currency",   # Currency code (USD, etc.)
    "Note",       # Additional details
    "Source",     # Original data source identifier
]

# Action type standardization mapping
ACTION_MAP = {
    # Buy actions (stocks)
    "buy": "Buy",
    "reinvest shares": "Buy",
    "reinvest dividend": "Buy",
    "long term cap gain reinvest": "Buy",
    "short term cap gain reinvest": "Buy",
    "contribution": "Buy",
    # Sell actions (stocks)
    "sell": "Sell",
    # Options actions
    "bto": "OptionBuy",  # Robinhood: Buy to Open (options)
    "stc": "OptionSell",  # Robinhood: Sell to Close (options)
    # Dividend/Income actions
    "dividend": "Dividend",
    "cdiv": "Dividend",
    "cash div": "Dividend",
    "qualified dividend": "Dividend",
    "staking income": "Staking",
    "staking": "Staking",
    "reward income": "Staking",
    # Transfer actions
    "funds received": "Transfer",
    "moneylink transfer": "Transfer",
    "security transfer": "Transfer",
    "wire funds received": "Transfer",
    "ira rollover cont": "Transfer",
    "send": "Transfer",
    "receive": "Transfer",
    "convert": "Transfer",
    "conv": "Transfer",  # Robinhood: Conversion
    "ach": "Transfer",  # Robinhood: ACH transfer
    "deposit": "Transfer",
    "withdrawal": "Transfer",
    "exchange deposit": "Transfer",
    "exchange withdrawal": "Transfer",
    "pro deposit": "Transfer",
    "pro withdrawal": "Transfer",
    "vault withdrawal": "Transfer",
    "wrap asset": "Transfer",
    "unwrap": "Transfer",
    "retail staking transfer": "Transfer",
    "retail unstaking transfer": "Transfer",
    "retail eth2 deprecation": "Transfer",
    # Interest
    "bank interest": "Interest",
    "interest": "Interest",
    "int": "Interest",  # Robinhood: Interest
    "subscription rebates": "Interest",
    # Fees
    "fee": "Fee",
    # Tax
    "dtax": "Tax",  # Robinhood: Dividend tax withholding
    # Stock lending
    "slip": "StockLending",
    # Options
    "oexp": "OptionsExpiration",  # Robinhood: Options expiration
    # Stock splits/corporate actions
    "spl": "StockSplit",
    "stock split": "StockSplit",
    "soff": "SpinOff",
    "spr": "ReverseStockSplit",
    "sxch": "Exchange",
    # Mergers & Acquisitions
    "mrgc": "MergerCash",  # Robinhood: Cash received from merger/acquisition
    "mrgs": "MergerOut",   # Robinhood: Shares removed or received in merger
    "liq": "Liquidation",
    "cil": "CashInLieu",    # Fractional share cash payment
    "rec": "Receive",
}


# =============================================================================
# Utility Functions
# =============================================================================

# Symbol name to ticker mapping (for funds that use full names instead of tickers)
SYMBOL_MAP = {
    "VANG WELLINGTON ADM": "VWENX",
    "VANG INTL GROWTH ADM": "VWILX",
    "VANG INST TR 2055": "VIVLX",
    "VANG US GROWTH ADM": "VWUAX",
    # Crypto symbols that need -USD suffix for yfinance
    "ETH": "ETH-USD",
    "ETH2": "ETH-USD",
    "CBETH": "CBETH-USD",
    "BTC": "BTC-USD",
    "ADA": "ADA-USD",
    "BOBA": "BOBA-USD",
    "OMG": "OMG-USD",
    "MATIC": "MATIC-USD",
    "ZRX": "ZRX-USD",
    "ZEC": "ZEC-USD",
    "REP": "REP-USD",
    "XLM": "XLM-USD",
    "XRP": "XRP-USD",
    # Stock symbols that need adjustment for yfinance
    "BRK.B": "BRK-B",
}

# Symbols that require price-based quantity conversion
# Format: {source_symbol: target_ticker}
# These symbols will have their quantities recalculated as: quantity = amount / target_ticker_price
PRICE_CONVERSION_MAP = {
    "VANG TR II 2055": "VFFVX",  # Vanguard Target Retirement 2055 Trust II → VFFVX equivalent
}

# Account merge mapping (for accounts that were transferred/rolled over)
# Format: {old_account_name: new_account_name}
ACCOUNT_MERGE_MAP = {
    "Voya Atos 401K": "Schwab Rollover IRA",
    "USAA Victory Capital Roth IRA": "Schwab Roth Contributory IRA",
}

# Delisted/acquired symbols - skip price lookups for these
# These companies were acquired, merged, or delisted and no longer trade
DELISTED_SYMBOLS = {
    "XLNX",      # Xilinx - acquired by AMD (Feb 2022)
    "TWTR",      # Twitter - taken private by Elon Musk (Oct 2022)
    "TPTX",      # Turning Point Therapeutics - acquired by Bristol-Myers (Aug 2022)
    "VRNA",      # Verona Pharma - acquired by Merck (Oct 2025)
    "EYEN",      # Eyenovia - delisted
    "FREQ",      # Frequency Therapeutics - delisted
    "FREQ^",     # Frequency Therapeutics preferred - delisted
    "S",         # Sprint - merged with T-Mobile (Apr 2020)
    "GME+",      # GameStop units - converted
}

# Price cache file path
PRICE_CACHE_PATH = DATA_PATH.joinpath("price_cache.json")

# Split cache file path
SPLIT_CACHE_PATH = DATA_PATH.joinpath("split_cache.json")

# Cost basis cache file path
COST_BASIS_CACHE_PATH = DATA_PATH.joinpath("cost_basis_cache.json")

# Historical holdings cache file path
HISTORICAL_HOLDINGS_CACHE_PATH = DATA_PATH.joinpath("historical_holdings_cache.json")

# Sector cache file path
SECTOR_CACHE_PATH = DATA_PATH.joinpath("sector_cache.json")


def load_price_cache() -> dict:
    """Load price cache from disk."""
    if PRICE_CACHE_PATH.exists():
        try:
            with open(PRICE_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_price_cache(cache: dict) -> None:
    """Save price cache to disk."""
    with open(PRICE_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_split_cache() -> dict:
    """Load split cache from disk."""
    if SPLIT_CACHE_PATH.exists():
        try:
            with open(SPLIT_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_split_cache(cache: dict) -> None:
    """Save split cache to disk."""
    with open(SPLIT_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_cost_basis_cache() -> dict:
    """Load cost basis cache from disk."""
    if COST_BASIS_CACHE_PATH.exists():
        try:
            with open(COST_BASIS_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cost_basis_cache(cache: dict) -> None:
    """Save cost basis cache to disk."""
    with open(COST_BASIS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_historical_holdings_cache() -> dict:
    """Load historical holdings cache from disk."""
    if HISTORICAL_HOLDINGS_CACHE_PATH.exists():
        try:
            with open(HISTORICAL_HOLDINGS_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_historical_holdings_cache(cache: dict) -> None:
    """Save historical holdings cache to disk."""
    with open(HISTORICAL_HOLDINGS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_sector_cache() -> dict:
    """Load sector cache from disk."""
    if SECTOR_CACHE_PATH.exists():
        try:
            with open(SECTOR_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_sector_cache(cache: dict) -> None:
    """Save sector cache to disk."""
    with open(SECTOR_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


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
        import yfinance as yf
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
    from datetime import datetime, timedelta
    
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
        "%Y-%m-%d",           # 2023-01-15
        "%Y-%m-%d %H:%M:%S",  # 2023-01-15 10:30:00
        "%m/%d/%Y",           # 01/15/2023
        "%d/%m/%Y",           # 15/01/2023 (European)
    ]
    
    # If format hint is European (DD/MM/YYYY)
    if format_hint == "european":
        formats = ["%d/%m/%Y"] + formats
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.split()[0] if " " in date_str and fmt == "%Y-%m-%d" else date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    # Last resort - try pandas
    try:
        return pd.to_datetime(date_str).strftime("%Y-%m-%d")
    except:
        return date_str


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


# =============================================================================
# Source Detection
# =============================================================================

def detect_source(file_path: pathlib.Path) -> Optional[str]:
    """
    Detect the data source based on filename and file content.
    Returns source identifier string or None if unknown.
    """
    filename = file_path.name.lower()
    
    # Skip retirement data file - it's not a transaction file
    if "retirement-data" in filename or "retirement_data" in filename:
        return None
    
    # Check filename patterns first (faster)
    if "apple-savings" in filename:
        return "apple_savings"
    
    if "usaavictorycapital" in filename or filename == "mutualfund.csv":
        return "usaa_victory_capital"
    
    if "vanguardsf" in filename or filename == "vanguardsf401k.csv":
        return "vanguard_sf401k"
    
    if "schwab" in filename or "rollover_ira" in filename or "roth_contributory_ira" in filename:
        return "schwab"
    
    # Check for Coinbase Pro/GDAX before regular Coinbase (more specific match first)
    if "coinbase-pro" in filename or "gdax" in filename:
        return "coinbase_pro"
    
    if "coinbase" in filename:
        return "coinbase"
    
    if "custominput" in filename:
        return "custom_input"
    
    if "robinhood" in filename:
        return "robinhood"
    
    if "voyaatos401k" in filename:
        return "voya_atos401k"
    
    # Check file content for additional identification
    try:
        content = file_path.read_text(encoding='utf-8')
        
        # Check for Robinhood signature at end of file
        if content[-30:-21] == "Robinhood":
            return "robinhood"
        
        # Check for Voya/Atos 401k header
        if "Atos 401(k)" in content[:50]:
            return "voya_atos401k"
        
        # Check first line for Coinbase metadata
        first_lines = content.split('\n')[:5]
        if any("Transactions" in line for line in first_lines):
            return "coinbase"
            
    except Exception as e:
        print(f"Warning: Could not read file content for detection: {e}")
    
    return None


# =============================================================================
# Parser Functions
# =============================================================================

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


def parse_robinhood(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Robinhood transaction files.
    Format: Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code, Quantity, Price, Amount
    Date format: MM/DD/YYYY
    Note: Description field may contain newlines, so we must handle that.
    Note: File may have empty rows and disclaimer text at the end.
    """
    # Read with error handling for malformed lines (Description field contains newlines)
    df = pd.read_csv(file_path, on_bad_lines='warn')
    
    # Filter out empty rows and disclaimer rows (rows where Activity Date is empty/NaN)
    df = df.dropna(subset=["Activity Date"])
    df = df[df["Activity Date"].astype(str).str.strip() != ""]
    
    result = create_empty_dataframe()
    result["Date"] = df["Activity Date"].apply(parse_date)
    result["Account"] = "Robinhood"
    result["Symbol"] = df["Instrument"].fillna("")
    result["Action"] = df["Trans Code"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df["Quantity"], errors='coerce').fillna(0)
    result["Price"] = df["Price"].apply(clean_currency)
    result["Fee"] = 0.0
    result["Amount"] = df["Amount"].apply(clean_currency)
    result["Currency"] = "USD"
    result["Note"] = df["Description"].fillna("").astype(str).str.replace("\n", " ", regex=False)
    result["Source"] = "Robinhood"
    
    return result


def parse_coinbase(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Coinbase transaction files.
    Format: Has 3 metadata lines, then header on line 4
    Columns: ID, Timestamp, Transaction Type, Asset, Quantity Transacted, Price Currency, 
             Price at Transaction, Subtotal, Total (inclusive of fees), Fees, Notes
    Date format: YYYY-MM-DD HH:MM:SS UTC
    
    Special handling:
    1. ETH→ETH2 conversions: Generate the "to" side (+ETH2) since both map to ETH-USD
    2. Internal Pro/Exchange transfers: Skip these since the actual trades happen on the Pro side
    """
    import re
    
    # Skip first 3 metadata lines
    df = pd.read_csv(file_path, skiprows=3)
    
    # Transaction types that represent internal transfers within Coinbase ecosystem
    # These should be skipped because they don't represent actual net holdings changes:
    # Filter out internal transfers that are paired (net zero within the same mapped symbol)
    # These are movements WITHIN Coinbase ecosystem that don't change actual holdings:
    # - Pro/Exchange transfers: actual trades happen on the Pro side
    # - Vault transfers: internal movement within Coinbase (vault deposit not in export)
    # - Retail staking/unstaking transfers: internal ETH ↔ ETH2 moves (both map to ETH-USD)
    # - Retail Eth2 Deprecation: ETH2→ETH conversion (both map to ETH-USD)
    # - Transfer (generic): internal moves like Pro↔Retail (paired, net zero)
    #
    # NOTE: We do NOT filter Unwrap/Wrap Asset because they convert between DIFFERENT symbols:
    # - Unwrap: CBETH → ETH2 (CBETH-USD to ETH-USD)
    # - Wrap Asset: ETH2 → CBETH (ETH-USD to CBETH-USD)
    # These are real conversions between differently-tracked assets.
    INTERNAL_TRANSFER_TYPES = {
        "pro withdrawal",           # ETH coming from Pro (already bought on Pro)
        "pro deposit",              # ETH going to Pro (will be sold on Pro)
        "exchange withdrawal",      # Same as Pro Withdrawal (older format)
        "exchange deposit",         # Same as Pro Deposit (older format)
        "vault withdrawal",         # Internal Coinbase transfer from Vault (no matching deposit in export)
        "retail staking transfer",  # Internal ETH→ETH2 staking move (has paired reverse)
        "retail unstaking transfer", # Internal ETH2→ETH unstaking move (has paired reverse)
        "retail eth2 deprecation",  # ETH2→ETH conversion when ETH2 deprecated (paired)
        "transfer",                 # Generic internal transfers (e.g., Pro↔Retail, paired)
    }
    
    # Filter out internal Pro/Exchange transfers BEFORE creating result DataFrame
    df["tx_type_lower"] = df["Transaction Type"].fillna("").str.lower().str.strip()
    df_filtered = df[~df["tx_type_lower"].isin(INTERNAL_TRANSFER_TYPES)].copy()
    
    result = create_empty_dataframe()
    result["Date"] = df_filtered["Timestamp"].apply(parse_date)
    result["Account"] = "Coinbase"
    result["Symbol"] = df_filtered["Asset"].fillna("")
    result["Action"] = df_filtered["Transaction Type"].apply(standardize_action)
    result["Quantity"] = pd.to_numeric(df_filtered["Quantity Transacted"], errors='coerce').fillna(0)
    result["Price"] = df_filtered["Price at Transaction"].apply(clean_currency)
    result["Fee"] = df_filtered["Fees and/or Spread"].apply(clean_currency)
    result["Amount"] = df_filtered["Total (inclusive of fees and/or spread)"].apply(clean_currency)
    result["Currency"] = df_filtered["Price Currency"].fillna("USD")
    result["Note"] = df_filtered["Notes"].fillna("")
    result["Source"] = "Coinbase"
    
    # Generate the "to" side for conversions where Coinbase only shows the "from" side
    # This is needed because Coinbase export only shows the asset being converted FROM
    #
    # Cases where we need to generate the "to" side:
    # 1. ETH→ETH2: Both map to ETH-USD, so we need +ETH2 to cancel the -ETH
    # 2. OTHER→ETH: When ADA/XRP/USDC/etc. convert to ETH, the +ETH side is missing
    #    These conversions create real ETH value that needs to be counted
    convert_rows = []
    convert_pattern = re.compile(r'Converted\s+([\d.]+)\s+(\w+)\s+to\s+([\d.]+)\s+(\w+)', re.IGNORECASE)
    
    for idx, row in df_filtered.iterrows():
        tx_type = str(row["Transaction Type"]).lower() if pd.notna(row["Transaction Type"]) else ""
        notes = str(row["Notes"]) if pd.notna(row["Notes"]) else ""
        asset = str(row["Asset"]).upper() if pd.notna(row["Asset"]) else ""
        
        if tx_type == "convert" and notes:
            match = convert_pattern.search(notes)
            if match:
                from_qty = float(match.group(1))
                from_symbol = match.group(2).upper()
                to_qty = float(match.group(3))
                to_symbol = match.group(4).upper()
                
                # Generate the "to" side for ETH→ETH2 conversions
                # Both ETH and ETH2 map to ETH-USD, so we need the +ETH2 side to cancel the -ETH side
                if from_symbol == "ETH" and to_symbol == "ETH2":
                    convert_rows.append({
                        "Date": parse_date(row["Timestamp"]),
                        "Account": "Coinbase",
                        "Symbol": to_symbol,
                        "Action": "Transfer",  # Treat as incoming transfer
                        "Quantity": to_qty,  # Positive - receiving ETH2
                        "Price": clean_currency(row["Price at Transaction"]),
                        "Fee": 0.0,  # Fee is on the "from" side
                        "Amount": abs(clean_currency(row["Total (inclusive of fees and/or spread)"])),
                        "Currency": row["Price Currency"] if pd.notna(row["Price Currency"]) else "USD",
                        "Note": f"Received from conversion: {notes}",
                        "Source": "Coinbase",
                    })
                
                # Generate the "to" side for OTHER→ETH conversions
                # When ADA/XRP/USDC/etc. convert to ETH, the Coinbase export only shows
                # the "from" asset leaving, but doesn't show the +ETH received
                # This creates real ETH value that needs to be counted
                elif to_symbol == "ETH" and from_symbol != "ETH":
                    convert_rows.append({
                        "Date": parse_date(row["Timestamp"]),
                        "Account": "Coinbase",
                        "Symbol": to_symbol,
                        "Action": "Transfer",  # Treat as incoming transfer
                        "Quantity": to_qty,  # Positive - receiving ETH
                        "Price": clean_currency(row["Price at Transaction"]),
                        "Fee": 0.0,  # Fee is on the "from" side
                        "Amount": abs(clean_currency(row["Total (inclusive of fees and/or spread)"])),
                        "Currency": row["Price Currency"] if pd.notna(row["Price Currency"]) else "USD",
                        "Note": f"Received from conversion: {notes}",
                        "Source": "Coinbase",
                    })
    
    # Append the generated "to" side transactions
    if convert_rows:
        convert_df = pd.DataFrame(convert_rows, columns=UNIFIED_COLUMNS)
        result = pd.concat([result, convert_df], ignore_index=True)
    
    return result


def parse_vanguard_sf401k(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Vanguard SF 401K transaction files.
    Format: Date, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note, Account, Currency
    Date format: YYYY-MM-DD (already normalized)
    
    Note: This parser fetches actual prices from yfinance because the input data
    often has missing quantities ('x') and incorrect unit prices.
    Quantity is calculated as: Amount / actual_price
    """
    df = pd.read_csv(file_path)
    
    # Load price cache for lookups
    price_cache = load_price_cache()
    cache_updated = False
    
    results = []
    
    for _, row in df.iterrows():
        date_str = parse_date(row["Date"])
        symbol = row["Symbol"] if pd.notna(row["Symbol"]) else ""
        action = standardize_action(row["Action"]) if pd.notna(row["Action"]) else ""
        amount = pd.to_numeric(row["Subtotal"], errors='coerce')
        if pd.isna(amount):
            amount = 0.0
        fee = pd.to_numeric(row["Fee"], errors='coerce')
        if pd.isna(fee):
            fee = 0.0
        
        # Try to parse quantity - may be 'x' or missing
        qty_raw = row["Quantity"]
        quantity = pd.to_numeric(qty_raw, errors='coerce')
        
        # Try to parse price from file
        price_raw = row["unitPrice"]
        file_price = pd.to_numeric(price_raw, errors='coerce')
        
        # Determine if we need to fetch the actual price
        # Fetch if: quantity is invalid/missing, OR amount > 0 and we can verify
        needs_price_lookup = pd.isna(quantity) or quantity == 0
        
        if needs_price_lookup and symbol and amount > 0:
            # Fetch actual price from yfinance
            actual_price, was_fetched = get_price_from_yfinance(symbol, date_str, price_cache)
            if actual_price and actual_price > 0:
                cache_updated = cache_updated or was_fetched
                price = actual_price
                quantity = amount / price
            else:
                # Fallback to file price if available
                if pd.notna(file_price) and file_price > 0:
                    price = file_price
                    quantity = amount / price
                else:
                    price = 0.0
                    quantity = 0.0
        else:
            # Quantity is valid, but still verify/fetch the correct price
            if symbol and amount > 0:
                actual_price, was_fetched = get_price_from_yfinance(symbol, date_str, price_cache)
                if actual_price and actual_price > 0:
                    cache_updated = cache_updated or was_fetched
                    price = actual_price
                    # Recalculate quantity based on actual price for consistency
                    quantity = amount / price
                elif pd.notna(file_price) and file_price > 0:
                    price = file_price
                    if pd.isna(quantity) or quantity == 0:
                        quantity = amount / price
                else:
                    price = file_price if pd.notna(file_price) else 0.0
                    quantity = quantity if pd.notna(quantity) else 0.0
            else:
                price = file_price if pd.notna(file_price) else 0.0
                quantity = quantity if pd.notna(quantity) else 0.0
        
        results.append({
            "Date": date_str,
            "Account": row.get("Account", "Vanguard SF 401K") if pd.notna(row.get("Account")) else "Vanguard SF 401K",
            "Symbol": symbol,
            "Action": action,
            "Quantity": quantity,
            "Price": price,
            "Fee": fee,
            "Amount": amount,
            "Currency": row.get("Currency", "USD") if pd.notna(row.get("Currency")) else "USD",
            "Note": row["Note"] if pd.notna(row["Note"]) else "",
            "Source": "Vanguard SF 401K",
        })
    
    # Save updated cache
    if cache_updated:
        save_price_cache(price_cache)
    
    if not results:
        return create_empty_dataframe()
    
    return pd.DataFrame(results, columns=UNIFIED_COLUMNS)


def parse_usaa_victory_capital(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse USAA Victory Capital transaction files.
    Format: Account, Currency, Date, Symbol, Action, Quantity, unitPrice, Fee, Subtotal, Note
    Date format: DD/MM/YYYY (European format!)
    """
    df = pd.read_csv(file_path)
    
    # Filter out empty rows (rows where Date is empty/NaN)
    df = df.dropna(subset=["Date"])
    df = df[df["Date"].astype(str).str.strip() != ""]
    
    result = create_empty_dataframe()
    result["Date"] = df["Date"].apply(lambda x: parse_date(x, format_hint="european"))
    result["Account"] = df.get("Account", "USAA Victory Capital").fillna("USAA Victory Capital")
    result["Symbol"] = df["Symbol"].fillna("")
    result["Action"] = df["Action"].apply(standardize_action)
    # Handle quantity with possible trailing spaces
    result["Quantity"] = df["Quantity"].apply(lambda x: float(str(x).strip()) if pd.notna(x) else 0)
    result["Price"] = pd.to_numeric(df["unitPrice"], errors='coerce').fillna(0)
    result["Fee"] = pd.to_numeric(df["Fee"], errors='coerce').fillna(0)
    result["Amount"] = pd.to_numeric(df["Subtotal"], errors='coerce').fillna(0)
    result["Currency"] = df.get("Currency", "USD").fillna("USD")
    result["Note"] = df["Note"].fillna("").str.strip()
    result["Source"] = "USAA Victory Capital"
    
    return result


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
    # This happens with CONTRIBUTION reversals/corrections
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


def parse_coinbase_pro(file_path: pathlib.Path) -> pd.DataFrame:
    """
    Parse Coinbase Pro / GDAX transaction files.
    Format: portfolio, type, time, amount, balance, amount/balance unit, transfer id, trade id, order id
    
    Transaction types:
    - deposit: funds/crypto coming in (positive amount)
    - withdrawal: funds/crypto going out (negative amount)
    - match: trade execution (positive = bought asset, negative = sold asset)
    - fee: trading fees (negative amount, always in USD)
    
    Date format: ISO 8601 (2017-12-12T03:19:50.252Z)
    """
    df = pd.read_csv(file_path)
    
    if df.empty:
        return create_empty_dataframe()
    
    results = []
    
    for _, row in df.iterrows():
        tx_type = row["type"]
        amount = float(row["amount"])
        unit = row["amount/balance unit"]
        time_str = row["time"]
        trade_id = row.get("trade id", "")
        order_id = row.get("order id", "")
        transfer_id = row.get("transfer id", "")
        
        # Parse date from ISO format
        date_str = parse_date(time_str.split("T")[0] if "T" in str(time_str) else time_str)
        
        # Determine action and quantity based on type
        # Skip deposits and withdrawals - these are internal transfers between Coinbase and GDAX/Pro
        # that are already recorded in the main Coinbase export file (as "Deposited X into GDAX" etc.)
        # Only keep match (trade) and fee transactions
        if tx_type in ["deposit", "withdrawal"]:
            continue
        elif tx_type == "match":
            # Match = trade execution
            # Positive amount = we received this asset (bought)
            # Negative amount = we gave this asset (sold)
            if amount > 0:
                action = "Buy"
                quantity = abs(amount)
                tx_amount = abs(amount)
            else:
                action = "Sell"
                quantity = amount  # Keep negative for proper tracking
                tx_amount = abs(amount)
            note = f"Trade ID: {trade_id}" if trade_id else ""
        elif tx_type == "fee":
            action = "Fee"
            quantity = 0
            tx_amount = abs(amount)
            note = f"Trading fee"
        else:
            # Unknown type, skip or log
            continue
        
        # Map crypto symbols to yfinance format
        symbol = unit
        if symbol in SYMBOL_MAP:
            symbol = SYMBOL_MAP[symbol]
        elif symbol not in ["USD"] and not symbol.endswith("-USD"):
            # Add -USD suffix for crypto if not already present
            if symbol in ["ETH", "BTC", "LTC", "BCH", "ZEC", "ZRX"]:
                symbol = f"{symbol}-USD"
        
        results.append({
            "Date": date_str,
            "Account": "Coinbase",  # Consolidate with regular Coinbase
            "Symbol": symbol,
            "Action": action,
            "Quantity": quantity,
            "Price": 0,  # Price not directly available in this format
            "Fee": 0,  # Fees are separate rows
            "Amount": tx_amount,
            "Currency": "USD",
            "Note": note,
            "Source": "Coinbase Pro",
        })
    
    if not results:
        return create_empty_dataframe()
    
    result = pd.DataFrame(results, columns=UNIFIED_COLUMNS)
    return result


# =============================================================================
# Parser Registry
# =============================================================================

# Maps source identifiers to their parser functions
PARSER_REGISTRY: dict[str, Callable[[pathlib.Path], pd.DataFrame]] = {
    "schwab": parse_schwab,
    "robinhood": parse_robinhood,
    "coinbase": parse_coinbase,
    "coinbase_pro": parse_coinbase_pro,
    "vanguard_sf401k": parse_vanguard_sf401k,
    "usaa_victory_capital": parse_usaa_victory_capital,
    "voya_atos401k": parse_voya_atos401k,
    "apple_savings": parse_apple_savings,
    "custom_input": parse_custom_input,
}


# =============================================================================
# Main Processing
# =============================================================================

def process_file(file_path: pathlib.Path) -> Optional[pd.DataFrame]:
    """Process a single input file and return normalized DataFrame."""
    print(f"  Processing: {file_path.name}")
    
    # Detect source
    source = detect_source(file_path)
    if source is None:
        print(f"    ⚠ Unknown source - skipping")
        return None
    
    print(f"    ✓ Detected source: {source}")
    
    # Get parser
    parser = PARSER_REGISTRY.get(source)
    if parser is None:
        print(f"    ⚠ No parser available for source: {source}")
        return None
    
    # Parse file
    try:
        df = parser(file_path)
        # Add source filename for deduplication across files
        df["_SourceFile"] = file_path.name
        print(f"    ✓ Parsed {len(df)} transactions")
        return df
    except Exception as e:
        print(f"    ✗ Error parsing file: {e}")
        return None


def process_all_files() -> pd.DataFrame:
    """Process all CSV files in the input directory."""
    print("\n" + "="*60)
    print("Transaction Data Aggregator")
    print("="*60 + "\n")
    
    all_transactions = []
    files_processed = 0
    files_skipped = 0
    
    # Load caches
    price_cache = load_price_cache()
    split_cache = load_split_cache()
    
    # Process each CSV file
    for file_path in sorted(DATA_INPUT_PATH.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() == ".csv":
            result = process_file(file_path)
            if result is not None and len(result) > 0:
                all_transactions.append(result)
                files_processed += 1
            else:
                files_skipped += 1
    
    # Combine all transactions
    if all_transactions:
        master_df = pd.concat(all_transactions, ignore_index=True)
        
        # Remove duplicate transactions (from overlapping date ranges in input files)
        # Duplicates are identified by matching on key transaction fields
        # We keep duplicates from the same file (legitimate same-day transactions)
        # but remove duplicates that appear in multiple files
        dupe_cols = ["Date", "Account", "Symbol", "Action", "Quantity", "Amount"]
        initial_count = len(master_df)
        
        # Step 1: Within each file, number occurrences of each transaction
        # This preserves legitimate duplicates (e.g., two identical buys on same day)
        master_df["_OccurrenceInFile"] = master_df.groupby(
            ["_SourceFile"] + dupe_cols
        ).cumcount()
        
        # Step 2: Now dedupe across files - keep first occurrence of each 
        # (transaction + occurrence number) combination
        master_df = master_df.drop_duplicates(
            subset=dupe_cols + ["_OccurrenceInFile"], 
            keep="first"
        )
        
        # Remove helper columns
        master_df = master_df.drop(columns=["_SourceFile", "_OccurrenceInFile"])
        
        dupes_removed = initial_count - len(master_df)
        if dupes_removed > 0:
            print(f"Removed {dupes_removed} duplicate transactions (from overlapping files)")
        
        # Normalize amounts to be consistently positive
        master_df = normalize_amounts(master_df)
        
        # Standardize fund names to ticker symbols
        master_df = standardize_symbols(master_df)
        
        # Merge accounts that have been transferred/rolled over
        master_df = merge_accounts(master_df)
        
        # Convert price-based symbols (e.g., VANG TR II 2055 → VFFVX)
        master_df = convert_price_based_symbols(master_df, price_cache)
        
        # Adjust historical transactions for stock splits
        master_df = adjust_for_splits(master_df, split_cache)
        
        # Reload price cache to capture any additions from parsers (like Vanguard)
        # then merge our additions
        final_price_cache = load_price_cache()
        for symbol, dates in price_cache.items():
            if symbol not in final_price_cache:
                final_price_cache[symbol] = {}
            final_price_cache[symbol].update(dates)
        
        # Save updated caches
        save_price_cache(final_price_cache)
        save_split_cache(split_cache)
        
        # Sort by date (newest first)
        master_df = master_df.sort_values("Date", ascending=False).reset_index(drop=True)
        
        print(f"\n" + "-"*60)
        print(f"Summary:")
        print(f"  Files processed: {files_processed}")
        print(f"  Files skipped:   {files_skipped}")
        print(f"  Total transactions: {len(master_df)}")
        print(f"-"*60)
        
        return master_df
    else:
        print("\nNo transactions found!")
        return create_empty_dataframe()


def export_master_csv(df: pd.DataFrame, filename: str = "master_transactions.csv"):
    """Export the master DataFrame to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"\n✓ Exported to: {output_path}")
    return output_path


def calculate_holdings(df: pd.DataFrame, price_cache: dict) -> pd.DataFrame:
    """
    Calculate current holdings for each account and asset combination.
    
    Holdings are calculated by summing up quantity changes from all transactions:
    - Buy/Contribution: +quantity
    - Sell: -quantity  
    - Transfer: +/- based on source-specific logic and context
    - Dividend/Interest/Staking: +quantity (if reinvested, otherwise 0)
    - StockSplit: The split adjustment already modified historical quantities
    
    Returns DataFrame with columns: Account, Symbol, Quantity, CurrentPrice, Value, Currency
    """
    if df.empty:
        return pd.DataFrame(columns=["Account", "Symbol", "Quantity", "CurrentPrice", "Value", "Currency"])
    
    # Create a copy to work with
    df = df.copy()
    
    # Calculate signed quantities based on action type
    def get_signed_quantity(row):
        action = row["Action"]
        qty = row["Quantity"]
        amount = row["Amount"]
        source = row["Source"]
        note = str(row["Note"]).lower() if pd.notna(row["Note"]) else ""
        
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
    
    df["SignedQty"] = df.apply(get_signed_quantity, axis=1)
    
    # Group by Account and Symbol, sum quantities
    holdings = df.groupby(["Account", "Symbol", "Currency"]).agg({
        "SignedQty": "sum"
    }).reset_index()
    
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
            price, _ = get_price_from_yfinance(symbol, today, price_cache)
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
    
    # Define get_signed_quantity (same as in calculate_holdings)
    def get_signed_quantity(row):
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
    df = df.sort_values(["Account", "Symbol", "Date"], ascending=[True, True, True]).reset_index(drop=True)
    
    # Calculate running balance for each (Account, Symbol) group
    df["RunningBalance"] = 0.0
    df["RunningValue"] = 0.0
    
    # Group by Account and Symbol
    for (account, symbol), group in df.groupby(["Account", "Symbol"]):
        balance = 0.0
        
        for idx in group.index:
            row = df.loc[idx]
            signed_qty = get_signed_quantity(row)
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


def add_running_cost_basis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add RunningCostBasis column to transactions using FIFO lot tracking.
    
    For each (Account, Symbol) combination, tracks cost basis as:
    - Buys: Add qty * price to lots
    - Sells: Remove from oldest lots first (FIFO)
    - RunningCostBasis = sum of remaining_cost across all lots for that position
    
    Returns DataFrame with RunningCostBasis column added.
    """
    if df.empty:
        return df
    
    df = df.copy()
    
    # Helper to determine buy/sell effect (similar to calculate_cost_basis)
    def get_transaction_effect(row):
        action = row["Action"]
        qty = row["Quantity"]
        source = row["Source"]
        amount = row["Amount"] if pd.notna(row["Amount"]) else 0
        
        if action == "Buy":
            return "buy", abs(qty)
        elif action == "Sell":
            return "sell", abs(qty)
        elif action == "SpinOff":
            return "buy", abs(qty)  # SpinOff is like receiving free shares
        elif action == "Transfer":
            if source in ["Coinbase", "Coinbase Pro"]:
                if qty >= 0:
                    return "buy", abs(qty)
                else:
                    return "sell", abs(qty)
            elif source == "Robinhood":
                if amount >= 0:
                    return "buy", abs(qty)
                else:
                    return "sell", abs(qty)
            elif source == "Schwab":
                symbol = row["Symbol"]
                if symbol and symbol != "" and qty != 0:
                    return None, 0  # Skip security transfers (already counted)
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            else:
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
        elif action == "Staking":
            return "buy", abs(qty)  # Staking rewards at $0 cost
        elif action in ["Dividend", "Interest"]:
            if qty > 0 and source == "Voya Atos 401K":
                return "buy", abs(qty)
            return None, 0
        elif action == "MergerOut":
            if qty > 0:
                return "merger_receive", abs(qty)
            else:
                return "merger_remove", 0
        elif action == "MergerCash":
            return "merger_cash", 0
        elif action == "CashInLieu":
            return "cash_in_lieu", 0
        else:
            return None, 0
    
    # Create a sort key that puts buys before sells on the same date
    def get_action_order(row):
        action = row["Action"]
        qty = row["Quantity"]
        
        if action in ["Buy", "Staking", "SpinOff"]:
            return 0
        elif action == "Sell":
            return 3
        elif action == "Transfer":
            return 0 if qty >= 0 else 3
        elif action == "MergerOut":
            return 1 if qty == 0 else 2
        elif action in ["MergerCash", "CashInLieu"]:
            return 4
        else:
            return 1
    
    df["ActionOrder"] = df.apply(get_action_order, axis=1)
    # Sort chronologically with action order
    df = df.sort_values(["Account", "Date", "ActionOrder", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])
    
    # Track lots for FIFO processing: {(account, symbol): [{"remaining_qty": x, "remaining_cost": y}, ...]}
    lots = {}
    
    # Track pending mergers
    pending_mergers = {}
    
    # Add column for running cost basis
    df["RunningCostBasis"] = 0.0
    
    # Process each transaction
    for idx, row in df.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]
        date = row["Date"]
        price = row["Price"] if pd.notna(row["Price"]) else 0
        amount = abs(row["Amount"]) if pd.notna(row["Amount"]) else 0
        fee = row["Fee"] if pd.notna(row["Fee"]) else 0
        
        if not symbol or symbol == "" or symbol == "USD":
            continue
        
        key = (account, symbol)
        if key not in lots:
            lots[key] = []
        
        effect, qty = get_transaction_effect(row)
        
        if effect == "buy" and qty > 0:
            # Calculate cost including fees
            if price > 0:
                cost = qty * price + fee
            elif amount > 0:
                cost = amount + fee
            else:
                cost = 0  # Free shares (staking, spinoff)
            
            lots[key].append({
                "remaining_qty": qty,
                "remaining_cost": cost
            })
        
        elif effect == "sell" and qty > 0:
            # FIFO: sell from oldest lots first
            qty_to_sell = qty
            
            while qty_to_sell > 0 and lots[key]:
                lot = lots[key][0]
                
                if lot["remaining_qty"] <= qty_to_sell:
                    # Sell entire lot
                    qty_to_sell -= lot["remaining_qty"]
                    lots[key].pop(0)
                else:
                    # Partial sale from this lot
                    fraction = qty_to_sell / lot["remaining_qty"]
                    cost_from_lot = lot["remaining_cost"] * fraction
                    lot["remaining_qty"] -= qty_to_sell
                    lot["remaining_cost"] -= cost_from_lot
                    qty_to_sell = 0
        
        elif effect == "merger_remove":
            # Save cost basis for transfer
            if lots[key]:
                total_cost = sum(lot["remaining_cost"] for lot in lots[key])
                total_qty = sum(lot["remaining_qty"] for lot in lots[key])
                pending_mergers[key] = {
                    "cost_basis": total_cost,
                    "qty": total_qty,
                    "date": date
                }
                lots[key] = []  # Remove all lots
        
        elif effect == "merger_cash":
            # Cash received from merger - all shares sold
            if key in pending_mergers:
                del pending_mergers[key]
        
        elif effect == "merger_receive":
            # Shares received in stock-for-stock merger
            inherited_cost = 0
            
            # Find matching pending merger by date
            for (acc, old_sym), merger_info in list(pending_mergers.items()):
                if acc == account and merger_info["date"] == date:
                    inherited_cost = merger_info["cost_basis"]
                    del pending_mergers[(acc, old_sym)]
                    break
            
            # Add lot with inherited cost basis
            cost = inherited_cost if inherited_cost > 0 else amount
            lots[key].append({
                "remaining_qty": qty,
                "remaining_cost": cost
            })
        
        elif effect == "cash_in_lieu":
            # Cash for fractional shares - usually after merger_receive
            if key in pending_mergers:
                del pending_mergers[key]
        
        # Calculate running cost basis for this position
        running_cost = sum(lot["remaining_cost"] for lot in lots.get(key, []))
        df.loc[idx, "RunningCostBasis"] = round(running_cost, 2)
    
    # Sort back to newest first for display
    df = df.sort_values("Date", ascending=False).reset_index(drop=True)
    
    return df


def calculate_portfolio_cost_basis_history(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate portfolio-wide cost basis at each transaction date.
    
    Processes all transactions chronologically and after each transaction,
    sums the RunningCostBasis across all (Account, Symbol) positions.
    
    Returns DataFrame with Date and TotalCostBasis columns.
    """
    if df.empty:
        return pd.DataFrame(columns=["Date", "TotalCostBasis"])
    
    df = df.copy()
    
    # Helper functions (same as add_running_cost_basis)
    def get_transaction_effect(row):
        action = row["Action"]
        qty = row["Quantity"]
        source = row["Source"]
        amount = row["Amount"] if pd.notna(row["Amount"]) else 0
        
        if action == "Buy":
            return "buy", abs(qty)
        elif action == "Sell":
            return "sell", abs(qty)
        elif action == "SpinOff":
            return "buy", abs(qty)
        elif action == "Transfer":
            if source in ["Coinbase", "Coinbase Pro"]:
                return ("buy", abs(qty)) if qty >= 0 else ("sell", abs(qty))
            elif source == "Robinhood":
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            elif source == "Schwab":
                symbol = row["Symbol"]
                if symbol and symbol != "" and qty != 0:
                    return None, 0
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            else:
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
        elif action == "Staking":
            return "buy", abs(qty)
        elif action in ["Dividend", "Interest"]:
            if qty > 0 and source == "Voya Atos 401K":
                return "buy", abs(qty)
            return None, 0
        elif action == "MergerOut":
            return ("merger_receive", abs(qty)) if qty > 0 else ("merger_remove", 0)
        elif action == "MergerCash":
            return "merger_cash", 0
        elif action == "CashInLieu":
            return "cash_in_lieu", 0
        else:
            return None, 0
    
    def get_action_order(row):
        action = row["Action"]
        qty = row["Quantity"]
        
        if action in ["Buy", "Staking", "SpinOff"]:
            return 0
        elif action == "Sell":
            return 3
        elif action == "Transfer":
            return 0 if qty >= 0 else 3
        elif action == "MergerOut":
            return 1 if qty == 0 else 2
        elif action in ["MergerCash", "CashInLieu"]:
            return 4
        else:
            return 1
    
    df["ActionOrder"] = df.apply(get_action_order, axis=1)
    df = df.sort_values(["Date", "ActionOrder", "Account", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])
    
    # Track lots for FIFO processing
    lots = {}
    pending_mergers = {}
    
    # Track portfolio cost basis at each date
    cost_basis_history = []
    last_date = None
    
    for idx, row in df.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]
        date = row["Date"]
        price = row["Price"] if pd.notna(row["Price"]) else 0
        amount = abs(row["Amount"]) if pd.notna(row["Amount"]) else 0
        fee = row["Fee"] if pd.notna(row["Fee"]) else 0
        
        if not symbol or symbol == "" or symbol == "USD":
            continue
        
        key = (account, symbol)
        if key not in lots:
            lots[key] = []
        
        effect, qty = get_transaction_effect(row)
        
        if effect == "buy" and qty > 0:
            if price > 0:
                cost = qty * price + fee
            elif amount > 0:
                cost = amount + fee
            else:
                cost = 0
            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})
        
        elif effect == "sell" and qty > 0:
            qty_to_sell = qty
            while qty_to_sell > 0 and lots[key]:
                lot = lots[key][0]
                if lot["remaining_qty"] <= qty_to_sell:
                    qty_to_sell -= lot["remaining_qty"]
                    lots[key].pop(0)
                else:
                    fraction = qty_to_sell / lot["remaining_qty"]
                    lot["remaining_qty"] -= qty_to_sell
                    lot["remaining_cost"] -= lot["remaining_cost"] * fraction
                    qty_to_sell = 0
        
        elif effect == "merger_remove":
            if lots[key]:
                total_cost = sum(l["remaining_cost"] for l in lots[key])
                total_qty = sum(l["remaining_qty"] for l in lots[key])
                pending_mergers[key] = {"cost_basis": total_cost, "qty": total_qty, "date": date}
                lots[key] = []
        
        elif effect == "merger_cash":
            if key in pending_mergers:
                del pending_mergers[key]
        
        elif effect == "merger_receive":
            inherited_cost = 0
            for (acc, old_sym), merger_info in list(pending_mergers.items()):
                if acc == account and merger_info["date"] == date:
                    inherited_cost = merger_info["cost_basis"]
                    del pending_mergers[(acc, old_sym)]
                    break
            cost = inherited_cost if inherited_cost > 0 else amount
            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})
        
        elif effect == "cash_in_lieu":
            if key in pending_mergers:
                del pending_mergers[key]
        
        # Calculate total portfolio cost basis after this transaction
        total_cost_basis = sum(
            sum(lot["remaining_cost"] for lot in position_lots)
            for position_lots in lots.values()
        )
        
        # Record if date changed or it's the last transaction of the day
        # We'll deduplicate to keep only end-of-day values
        if date != last_date:
            if last_date is not None and cost_basis_history:
                # Keep the last entry for the previous date
                pass
            cost_basis_history.append({
                "Date": date,
                "TotalCostBasis": round(total_cost_basis, 2)
            })
            last_date = date
        else:
            # Update the last entry with the new total
            if cost_basis_history:
                cost_basis_history[-1]["TotalCostBasis"] = round(total_cost_basis, 2)
    
    return pd.DataFrame(cost_basis_history)


# =============================================================================
# Cost Basis Calculation (FIFO Method)
# =============================================================================

def calculate_cost_basis(df: pd.DataFrame, price_cache: dict) -> pd.DataFrame:
    """
    Calculate cost basis for all holdings using FIFO (First In, First Out) method.
    
    For each Account/Symbol combination, tracks:
    - Total cost basis (sum of purchase costs)
    - Average cost per share
    - Realized gains/losses from sales
    - Unrealized gains/losses for current holdings
    
    Returns DataFrame with detailed cost basis information per lot.
    """
    if df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    
    # Create a sort key that puts buys before sells on the same date
    # This is important for FIFO - we need to buy before we can sell
    # For mergers: MergerOut (remove shares) must come before MergerOut (receive shares) and MergerCash
    def get_action_order(row):
        action = row["Action"]
        qty = row["Quantity"]
        
        # Buys should come first (0), then sells (3)
        if action in ["Buy", "Staking", "SpinOff"]:
            return 0
        elif action == "Sell":
            return 3
        elif action == "Transfer":
            # For transfers, use quantity sign: positive = buy (0), negative = sell (3)
            return 0 if qty >= 0 else 3
        elif action == "MergerOut":
            # MergerOut with qty=0 removes old shares (must happen first: 1)
            # MergerOut with qty>0 receives new shares (must happen after: 2)
            return 1 if qty == 0 else 2
        elif action in ["MergerCash", "CashInLieu"]:
            # MergerCash/CashInLieu happens after MergerOut to use saved cost basis
            return 4
        else:
            return 1  # Other actions in the middle
    
    df["ActionOrder"] = df.apply(get_action_order, axis=1)
    # Sort order: Account, Date, ActionOrder, Symbol
    # ActionOrder before Symbol ensures all merger removals happen before merger receives across symbols
    df = df.sort_values(["Account", "Date", "ActionOrder", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])
    
    # Track lots for FIFO processing
    # Structure: {(account, symbol): [{"date": date, "qty": qty, "price": price, "cost": cost}, ...]}
    lots = {}
    
    # Track realized gains
    realized_gains = []
    
    # Helper to get signed quantity (same logic as calculate_holdings)
    def get_transaction_effect(row):
        action = row["Action"]
        qty = row["Quantity"]
        source = row["Source"]
        amount = row["Amount"]
        
        if action == "Buy":
            return "buy", abs(qty)
        elif action == "Sell":
            return "sell", abs(qty)
        elif action == "SpinOff":
            return "buy", abs(qty)  # SpinOff is like receiving free shares
        elif action == "Transfer":
            # Transfer handling for cost basis - must match calculate_holdings logic
            # For Coinbase/Coinbase Pro, we use the signed quantity directly:
            # - Positive qty = receiving assets (buy for cost basis)
            # - Negative qty = sending assets (sell for cost basis)
            # This handles: external sends, external receives, wrap/unwrap, conversions
            if source in ["Coinbase", "Coinbase Pro"]:
                if qty >= 0:
                    return "buy", abs(qty)
                else:
                    return "sell", abs(qty)
            elif source == "Robinhood":
                if amount >= 0:
                    return "buy", abs(qty)
                else:
                    return "sell", abs(qty)
            elif source == "Schwab":
                symbol = row["Symbol"]
                if symbol and symbol != "" and qty != 0:
                    return None, 0  # Skip security transfers (already counted)
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            else:
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
        elif action == "Staking":
            return "buy", abs(qty)  # Staking rewards are like receiving at $0 cost
        elif action in ["Dividend", "Interest"]:
            if qty > 0 and source == "Voya Atos 401K":
                return "buy", abs(qty)
            return None, 0
        elif action == "MergerOut":
            # MergerOut with qty > 0: receiving shares (stock-for-stock merger)
            # MergerOut with qty = 0: shares removed (will be paired with MergerCash)
            if qty > 0:
                return "merger_receive", abs(qty)  # Special: inherits cost basis
            else:
                return "merger_remove", 0  # Flag to remove all shares
        elif action == "MergerCash":
            # Cash received from merger - treat as a sale
            return "merger_cash", 0  # Amount is the proceeds
        elif action == "CashInLieu":
            # Cash for fractional shares - a small sale
            return "cash_in_lieu", 0  # Amount is the proceeds
        else:
            return None, 0
    
    # Track pending mergers (cost basis to transfer)
    pending_mergers = {}  # {(account, old_symbol): {"cost_basis": x, "qty": y, "date": d}}
    
    # Process each transaction
    for _, row in df.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]
        date = row["Date"]
        price = row["Price"]
        amount = abs(row["Amount"]) if pd.notna(row["Amount"]) else 0
        fee = row["Fee"] if pd.notna(row["Fee"]) else 0
        
        if not symbol or symbol == "" or symbol == "USD":
            continue
        
        key = (account, symbol)
        if key not in lots:
            lots[key] = []
        
        effect, qty = get_transaction_effect(row)
        
        if effect == "buy" and qty > 0:
            # Calculate cost including fees
            if price > 0:
                cost = qty * price + fee
            elif amount > 0:
                cost = amount + fee
            else:
                cost = 0  # Free shares (staking, spinoff)
            
            lots[key].append({
                "date": date,
                "qty": qty,
                "price": price if price > 0 else (cost / qty if qty > 0 else 0),
                "cost": cost,
                "remaining_qty": qty,
                "remaining_cost": cost
            })
        
        elif effect == "sell" and qty > 0:
            # FIFO: sell from oldest lots first
            qty_to_sell = qty
            proceeds = amount - fee
            cost_basis_sold = 0
            
            while qty_to_sell > 0 and lots[key]:
                lot = lots[key][0]
                
                if lot["remaining_qty"] <= qty_to_sell:
                    # Sell entire lot
                    qty_to_sell -= lot["remaining_qty"]
                    cost_basis_sold += lot["remaining_cost"]
                    lots[key].pop(0)
                else:
                    # Partial sale from this lot
                    fraction = qty_to_sell / lot["remaining_qty"]
                    cost_from_lot = lot["remaining_cost"] * fraction
                    cost_basis_sold += cost_from_lot
                    lot["remaining_qty"] -= qty_to_sell
                    lot["remaining_cost"] -= cost_from_lot
                    qty_to_sell = 0
            
            # Record realized gain/loss
            gain = proceeds - cost_basis_sold
            realized_gains.append({
                "Date": date,
                "Account": account,
                "Symbol": symbol,
                "Quantity": qty,
                "Proceeds": round(proceeds, 2),
                "CostBasis": round(cost_basis_sold, 2),
                "RealizedGain": round(gain, 2)
            })
        
        elif effect == "merger_remove":
            # Shares removed in merger - save cost basis for transfer or cash proceeds
            # This happens when qty=0 on MergerOut (shares being removed)
            if lots[key]:
                total_qty = sum(lot["remaining_qty"] for lot in lots[key])
                total_cost = sum(lot["remaining_cost"] for lot in lots[key])
                pending_mergers[key] = {
                    "cost_basis": total_cost,
                    "qty": total_qty,
                    "date": date
                }
                lots[key] = []  # Remove all lots
        
        elif effect == "merger_cash":
            # Cash received from merger - realize gain/loss
            # The MergerOut (merger_remove) should have already saved the cost basis
            if key in pending_mergers:
                cost_basis = pending_mergers[key]["cost_basis"]
                qty_sold = pending_mergers[key]["qty"]
                del pending_mergers[key]
            else:
                # Fallback: no pending merger info, use 0 cost basis
                cost_basis = 0
                qty_sold = 0
            
            gain = amount - cost_basis
            realized_gains.append({
                "Date": date,
                "Account": account,
                "Symbol": symbol,
                "Quantity": qty_sold,
                "Proceeds": round(amount, 2),
                "CostBasis": round(cost_basis, 2),
                "RealizedGain": round(gain, 2)
            })
        
        elif effect == "merger_receive":
            # Shares received in stock-for-stock merger
            # Look for pending cost basis from the same account on same date
            # (The merger_remove should have happened just before this)
            inherited_cost = 0
            inherited_date = date
            
            # Find matching pending merger by date (same-day assumption)
            for (acc, old_sym), merger_info in list(pending_mergers.items()):
                if acc == account and merger_info["date"] == date:
                    inherited_cost = merger_info["cost_basis"]
                    inherited_date = merger_info["date"]
                    del pending_mergers[(acc, old_sym)]
                    break
            
            # Add lot with inherited cost basis (or amount if no inheritance)
            cost = inherited_cost if inherited_cost > 0 else amount
            lots[key].append({
                "date": inherited_date,
                "qty": qty,
                "price": cost / qty if qty > 0 else 0,
                "cost": cost,
                "remaining_qty": qty,
                "remaining_cost": cost
            })
        
        elif effect == "cash_in_lieu":
            # Cash for fractional shares - small sale with gain/loss
            # Usually paired with a merger, use proportional cost basis
            if key in pending_mergers:
                merger_info = pending_mergers[key]
                # CIL typically represents fractional share proceeds
                # The full shares should have been handled by merger_receive
                cost_basis = 0  # Fractional, minor - treat as full gain
                del pending_mergers[key]
            else:
                cost_basis = 0
            
            gain = amount - cost_basis
            if amount > 0:
                realized_gains.append({
                    "Date": date,
                    "Account": account,
                    "Symbol": symbol,
                    "Quantity": 0,  # Fractional
                    "Proceeds": round(amount, 2),
                    "CostBasis": round(cost_basis, 2),
                    "RealizedGain": round(gain, 2)
                })
    
    # Build current holdings with cost basis
    holdings_with_basis = []
    
    # Get current prices
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Get unique symbols for price change calculation
    unique_symbols = list(set(symbol for (account, symbol) in lots.keys() 
                               if sum(lot["remaining_qty"] for lot in lots[(account, symbol)]) >= 0.0001))
    
    # Fetch 7d and 30d price changes for all symbols
    price_changes = get_price_changes(unique_symbols, price_cache)
    
    for (account, symbol), symbol_lots in lots.items():
        if not symbol_lots:
            continue
        
        total_qty = sum(lot["remaining_qty"] for lot in symbol_lots)
        total_cost = sum(lot["remaining_cost"] for lot in symbol_lots)
        
        if total_qty < 0.0001:
            continue
        
        avg_cost = total_cost / total_qty if total_qty > 0 else 0
        
        # Get current price
        current_price, _ = get_price_from_yfinance(symbol, today, price_cache)
        if current_price is None:
            current_price = 0
        
        current_value = total_qty * current_price
        unrealized_gain = current_value - total_cost
        unrealized_pct = (unrealized_gain / total_cost * 100) if total_cost > 0 else 0
        
        # Get first purchase date
        first_purchase = min(lot["date"] for lot in symbol_lots) if symbol_lots else ""
        
        # Get price changes for this symbol
        symbol_changes = price_changes.get(symbol, {})
        change_7d = symbol_changes.get("change_7d")
        change_30d = symbol_changes.get("change_30d")
        
        holdings_with_basis.append({
            "Account": account,
            "Symbol": symbol,
            "Quantity": round(total_qty, 6),
            "CostBasis": round(total_cost, 2),
            "AvgCostPerShare": round(avg_cost, 4),
            "CurrentPrice": round(current_price, 4),
            "CurrentValue": round(current_value, 2),
            "UnrealizedGain": round(unrealized_gain, 2),
            "UnrealizedGainPct": round(unrealized_pct, 2),
            "Change7D": change_7d,
            "Change30D": change_30d,
            "FirstPurchaseDate": first_purchase,
            "NumLots": len(symbol_lots)
        })
    
    holdings_df = pd.DataFrame(holdings_with_basis)
    if not holdings_df.empty:
        holdings_df = holdings_df.sort_values("CurrentValue", ascending=False).reset_index(drop=True)
    
    # Store realized gains for reporting
    realized_df = pd.DataFrame(realized_gains)
    
    # Build tax lots detail for remaining positions
    tax_lots_detail = []
    today = datetime.now()
    
    for (account, symbol), symbol_lots in lots.items():
        for lot in symbol_lots:
            if lot["remaining_qty"] < 0.0001:
                continue
                
            purchase_date = datetime.strptime(lot["date"], "%Y-%m-%d")
            holding_days = (today - purchase_date).days
            is_long_term = holding_days > 365
            
            # Get current price for unrealized calculation
            current_price, _ = get_price_from_yfinance(symbol, today.strftime("%Y-%m-%d"), price_cache)
            current_price = current_price or 0
            
            current_value = lot["remaining_qty"] * current_price
            unrealized_gain = current_value - lot["remaining_cost"]
            
            tax_lots_detail.append({
                "Account": account,
                "Symbol": symbol,
                "PurchaseDate": lot["date"],
                "Quantity": round(lot["remaining_qty"], 6),
                "CostBasis": round(lot["remaining_cost"], 2),
                "CostPerShare": round(lot["remaining_cost"] / lot["remaining_qty"], 4) if lot["remaining_qty"] > 0 else 0,
                "CurrentPrice": round(current_price, 4),
                "CurrentValue": round(current_value, 2),
                "UnrealizedGain": round(unrealized_gain, 2),
                "HoldingDays": holding_days,
                "HoldingPeriod": "Long-term" if is_long_term else "Short-term"
            })
    
    tax_lots_df = pd.DataFrame(tax_lots_detail)
    if not tax_lots_df.empty:
        tax_lots_df = tax_lots_df.sort_values(["Account", "Symbol", "PurchaseDate"]).reset_index(drop=True)
    
    return holdings_df, realized_df, tax_lots_df


# =============================================================================
# Income Tracking
# =============================================================================

def calculate_income(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all income received: dividends, interest, staking rewards, stock lending.
    
    Returns DataFrame with income by source, symbol, and year.
    """
    if df.empty:
        return pd.DataFrame()
    
    income_actions = ["Dividend", "Interest", "Staking", "StockLending"]
    income_df = df[df["Action"].isin(income_actions)].copy()
    
    if income_df.empty:
        return pd.DataFrame()
    
    # Add year column
    income_df["Year"] = pd.to_datetime(income_df["Date"]).dt.year
    
    # Group by Account, Symbol, Year, and Action type
    income_summary = income_df.groupby(["Account", "Symbol", "Year", "Action"]).agg({
        "Amount": "sum",
        "Quantity": "sum",
        "Date": "count"  # Count of transactions
    }).reset_index()
    
    income_summary = income_summary.rename(columns={
        "Amount": "TotalAmount",
        "Quantity": "TotalQuantity",
        "Date": "NumTransactions"
    })
    
    income_summary["TotalAmount"] = income_summary["TotalAmount"].round(2)
    income_summary["TotalQuantity"] = income_summary["TotalQuantity"].round(6)
    
    return income_summary.sort_values(["Year", "TotalAmount"], ascending=[False, False]).reset_index(drop=True)


def calculate_income_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate total income by year and type for tax reporting purposes.
    """
    if df.empty:
        return pd.DataFrame()
    
    income_actions = ["Dividend", "Interest", "Staking", "StockLending"]
    income_df = df[df["Action"].isin(income_actions)].copy()
    
    if income_df.empty:
        return pd.DataFrame()
    
    income_df["Year"] = pd.to_datetime(income_df["Date"]).dt.year
    
    # Pivot by year and action type
    yearly_income = income_df.groupby(["Year", "Action"])["Amount"].sum().unstack(fill_value=0)
    yearly_income["Total"] = yearly_income.sum(axis=1)
    yearly_income = yearly_income.round(2)
    
    return yearly_income.reset_index()


# =============================================================================
# Cash Balance Tracking (for USD-only accounts like Apple Savings)
# =============================================================================

def calculate_cash_balances(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate cash balances for USD-only accounts (like Apple Savings).
    
    Tracks:
    - Deposits (Buy USD)
    - Withdrawals (Sell USD) 
    - Interest earned
    - Current balance
    - Total interest earned
    - Return on average balance
    
    Returns DataFrame with cash account summary.
    """
    if df.empty:
        return pd.DataFrame()
    
    # Find accounts that only have USD transactions
    # These are cash accounts (like savings accounts)
    account_symbols = df.groupby("Account")["Symbol"].apply(lambda x: set(x.dropna()) - {""})
    cash_accounts = [acc for acc, symbols in account_symbols.items() 
                     if symbols == {"USD"} or symbols == set()]
    
    if not cash_accounts:
        return pd.DataFrame()
    
    cash_df = df[df["Account"].isin(cash_accounts)].copy()
    
    if cash_df.empty:
        return pd.DataFrame()
    
    results = []
    
    for account in cash_accounts:
        account_df = cash_df[cash_df["Account"] == account].copy()
        account_df = account_df.sort_values("Date")
        
        # Calculate totals
        deposits = account_df[account_df["Action"] == "Buy"]["Amount"].sum()
        withdrawals = account_df[account_df["Action"] == "Sell"]["Amount"].abs().sum()
        interest = account_df[account_df["Action"] == "Interest"]["Amount"].sum()
        
        # Net contributions (deposits - withdrawals)
        net_contributions = deposits - withdrawals
        
        # Current balance = net contributions + interest
        current_balance = net_contributions + interest
        
        # Calculate interest by year
        account_df["Year"] = pd.to_datetime(account_df["Date"]).dt.year
        interest_by_year = account_df[account_df["Action"] == "Interest"].groupby("Year")["Amount"].sum()
        
        # Get first and last dates
        first_date = account_df["Date"].min()
        last_date = account_df["Date"].max()
        
        # Count transactions
        num_deposits = len(account_df[account_df["Action"] == "Buy"])
        num_withdrawals = len(account_df[account_df["Action"] == "Sell"])
        num_interest = len(account_df[account_df["Action"] == "Interest"])
        
        # Calculate simple return (interest / net contributions)
        return_pct = (interest / net_contributions * 100) if net_contributions > 0 else 0
        
        result = {
            "Account": account,
            "Currency": "USD",
            "CurrentBalance": round(current_balance, 2),
            "TotalDeposits": round(deposits, 2),
            "TotalWithdrawals": round(withdrawals, 2),
            "NetContributions": round(net_contributions, 2),
            "TotalInterest": round(interest, 2),
            "ReturnPct": round(return_pct, 2),
            "FirstDate": first_date,
            "LastDate": last_date,
            "NumDeposits": num_deposits,
            "NumWithdrawals": num_withdrawals,
            "NumInterestPayments": num_interest
        }
        
        # Add yearly interest breakdown
        for year, amount in interest_by_year.items():
            result[f"Interest_{year}"] = round(amount, 2)
        
        results.append(result)
    
    return pd.DataFrame(results)


def export_cash_balances_csv(df: pd.DataFrame, filename: str = "cash_balances.csv"):
    """Export cash account balances to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported cash balances to: {output_path}")
    return output_path


# =============================================================================
# Historical Holdings Tracking
# =============================================================================

def calculate_net_cash_flows(df: pd.DataFrame, period_start: datetime, period_end: datetime) -> float:
    """
    Calculate net cash flows (deposits - withdrawals) during a period.
    
    Cash flows that represent money added/removed from portfolio:
    - Buy: Money put in to purchase assets
    - Sell: Money taken out from selling assets
    - Transfer in/out: Money movement (USD only)
    
    NOT cash flows (internal portfolio activity):
    - Dividends (portfolio returns, not new money)
    - Interest, Staking rewards (portfolio returns)
    - Stock splits, spinoffs (no cash movement)
    """
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    # Filter to period
    period_df = df[(df["Date"] > period_start) & (df["Date"] <= period_end)]
    
    if period_df.empty:
        return 0.0
    
    net_flow = 0.0
    
    for _, row in period_df.iterrows():
        action = row["Action"]
        amount = abs(row["Amount"])
        symbol = row["Symbol"]
        account = row["Account"]
        
        # For cash accounts like Apple Savings, deposits are cash flows
        if account == "Apple Savings":
            if action == "Buy":
                net_flow += amount
            elif action == "Sell":
                net_flow -= amount
            # Interest is NOT a cash flow (it's return)
            continue
        
        # For Coinbase, only count USD transfers as cash flows
        # Crypto buys within Coinbase are often funded from existing USD balance
        if account == "Coinbase":
            if action == "Transfer" and (symbol == "USD" or symbol == ""):
                if row["Amount"] >= 0:
                    net_flow += amount
                else:
                    net_flow -= amount
            continue
            
        # Buy = money deposited into portfolio
        if action == "Buy":
            net_flow += amount
        
        # Sell = money withdrawn from portfolio
        elif action == "Sell":
            net_flow -= amount
        
        # Transfers - only count if it's a cash transfer (USD)
        elif action == "Transfer":
            if symbol == "USD" or symbol == "":
                # Positive amount = transfer in, negative = transfer out
                if row["Amount"] >= 0:
                    net_flow += amount
                else:
                    net_flow -= amount
    
    return net_flow


def calculate_time_weighted_return(historical_results: list, df: pd.DataFrame) -> list:
    """
    Calculate Time-Weighted Return (TWR) for each period, plus cumulative invested amount.
    
    TWR isolates investment performance from cash flow timing by:
    1. Breaking the period into sub-periods between cash flows
    2. Calculating the return for each sub-period
    3. Geometrically linking (compounding) the returns
    
    Also tracks TotalInvested (cumulative cash flows) for value vs invested comparison.
    
    For monthly snapshots, we calculate:
    - Monthly return adjusted for cash flows during that month
    - Cumulative TWR from the start
    - Cumulative total invested
    """
    if len(historical_results) < 2:
        return historical_results
    
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    cumulative_twr = 1.0  # Start at 1 (100%)
    cumulative_invested = 0.0  # Track total money put in
    
    # Calculate initial invested amount (before first snapshot)
    first_date = pd.to_datetime(historical_results[0]["Date"])
    initial_invested = calculate_cumulative_invested(df, first_date)
    cumulative_invested = initial_invested
    
    for i, result in enumerate(historical_results):
        if i == 0:
            # First period - no prior value, TWR starts at 0%
            result["TWR"] = 0.0
            result["PeriodReturn"] = 0.0
            result["TotalInvested"] = round(cumulative_invested, 2)
            continue
        
        prev_result = historical_results[i - 1]
        
        start_value = prev_result["TotalValue"]
        end_value = result["TotalValue"]
        
        # Get cash flows during this period
        period_start = pd.to_datetime(prev_result["Date"])
        period_end = pd.to_datetime(result["Date"])
        
        net_cash_flow = calculate_net_cash_flows(df, period_start, period_end)
        
        # Update cumulative invested
        cumulative_invested += net_cash_flow
        result["TotalInvested"] = round(cumulative_invested, 2)
        
        # Calculate period return using Modified Dietz method
        # This assumes cash flows occur mid-period on average
        # R = (End - Start - CashFlow) / (Start + CashFlow/2)
        
        if start_value + net_cash_flow / 2 > 0:
            period_return = (end_value - start_value - net_cash_flow) / (start_value + net_cash_flow / 2)
        else:
            period_return = 0.0
        
        # Compound the return
        cumulative_twr *= (1 + period_return)
        
        # Store as percentage
        result["PeriodReturn"] = round(period_return * 100, 2)
        result["TWR"] = round((cumulative_twr - 1) * 100, 2)
    
    return historical_results


def calculate_what_if_sp500(historical_results: list, df: pd.DataFrame, price_cache: dict) -> list:
    """
    Calculate "What If S&P 500" - compare actual portfolio value to what it would be
    if each investment had been in S&P 500 instead, with matching cash flows.
    
    This properly accounts for DCA by:
    1. Tracking cost basis changes (increases = buys, decreases = sells)
    2. When cost basis increases, "buy" equivalent S&P 500 shares
    3. When cost basis decreases, "sell" equivalent dollar amount of S&P 500 shares
    4. At each snapshot, value remaining S&P 500 shares
    
    This answers: "If I made the same investment/withdrawal decisions but with
    S&P 500 instead of individual stocks, what would I have now?"
    """
    if not historical_results or len(historical_results) < 2:
        return historical_results
    
    sp500_symbol = "^GSPC"
    
    # Build list of cost basis changes with dates
    cost_basis_changes = calculate_cost_basis_changes(df, price_cache)
    
    # Track S&P 500 shares over time using FIFO for sells
    sp500_lots = []  # Each lot: {"date": date, "shares": float, "price": float}
    
    # For each snapshot, calculate S&P 500 value
    for result in historical_results:
        snapshot_date = pd.to_datetime(result["Date"])
        sp500_current_price = result.get("SP500")
        
        if not sp500_current_price:
            result["WhatIfSP500"] = None
            continue
        
        # Process all cost basis changes up to this date
        while cost_basis_changes and cost_basis_changes[0]["date"] <= snapshot_date:
            change = cost_basis_changes.pop(0)
            delta = change["delta"]
            change_date = change["date"]
            sp500_price_at_change = change["sp500_price"]
            
            if sp500_price_at_change and sp500_price_at_change > 0:
                if delta > 0:
                    # Cost basis increased = buy S&P 500 shares
                    shares = delta / sp500_price_at_change
                    sp500_lots.append({"shares": shares, "price": sp500_price_at_change})
                elif delta < 0:
                    # Cost basis decreased = sell S&P 500 shares (FIFO)
                    amount_to_sell = abs(delta)
                    while amount_to_sell > 0 and sp500_lots:
                        lot = sp500_lots[0]
                        lot_value = lot["shares"] * sp500_price_at_change
                        if lot_value <= amount_to_sell:
                            # Sell entire lot
                            amount_to_sell -= lot_value
                            sp500_lots.pop(0)
                        else:
                            # Sell partial lot
                            shares_to_sell = amount_to_sell / sp500_price_at_change
                            lot["shares"] -= shares_to_sell
                            amount_to_sell = 0
        
        # Calculate current value of all S&P 500 shares
        total_sp500_value = sum(lot["shares"] * sp500_current_price for lot in sp500_lots)
        result["WhatIfSP500"] = round(total_sp500_value, 2)
    
    return historical_results


def calculate_cost_basis_changes(df: pd.DataFrame, price_cache: dict) -> list:
    """
    Track cost basis changes over time with S&P 500 prices at each change date.
    Returns list of {date, delta, sp500_price} sorted by date.
    """
    if df.empty:
        return []
    
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    sp500_symbol = "^GSPC"
    
    def get_transaction_effect(row):
        action = row["Action"]
        qty = row["Quantity"]
        source = row["Source"]
        amount = row["Amount"] if pd.notna(row["Amount"]) else 0
        
        if action == "Buy":
            return "buy", abs(qty)
        elif action == "Sell":
            return "sell", abs(qty)
        elif action == "SpinOff":
            return "buy", abs(qty)
        elif action == "Transfer":
            if source in ["Coinbase", "Coinbase Pro"]:
                return ("buy", abs(qty)) if qty >= 0 else ("sell", abs(qty))
            elif source == "Robinhood":
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            elif source == "Schwab":
                symbol = row["Symbol"]
                if symbol and symbol != "" and qty != 0:
                    return None, 0
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
            else:
                return ("buy", abs(qty)) if amount >= 0 else ("sell", abs(qty))
        elif action == "Staking":
            return "buy", abs(qty)
        elif action in ["Dividend", "Interest"]:
            if qty > 0 and source == "Voya Atos 401K":
                return "buy", abs(qty)
            return None, 0
        elif action == "MergerOut":
            return ("merger_receive", abs(qty)) if qty > 0 else ("merger_remove", 0)
        elif action == "MergerCash":
            return "merger_cash", 0
        elif action == "CashInLieu":
            return "cash_in_lieu", 0
        else:
            return None, 0
    
    def get_action_order(row):
        action = row["Action"]
        qty = row["Quantity"]
        if action in ["Buy", "Staking", "SpinOff"]:
            return 0
        elif action == "Sell":
            return 3
        elif action == "Transfer":
            return 0 if qty >= 0 else 3
        elif action == "MergerOut":
            return 1 if qty == 0 else 2
        elif action in ["MergerCash", "CashInLieu"]:
            return 4
        else:
            return 1
    
    df["ActionOrder"] = df.apply(get_action_order, axis=1)
    df = df.sort_values(["Date", "ActionOrder", "Account", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])
    
    lots = {}
    pending_mergers = {}
    cost_basis_changes = []
    prev_cost_basis = 0.0
    prev_date = None
    pending_delta = 0.0
    
    for idx, row in df.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]
        date = row["Date"]
        price = row["Price"] if pd.notna(row["Price"]) else 0
        amount = abs(row["Amount"]) if pd.notna(row["Amount"]) else 0
        fee = row["Fee"] if pd.notna(row["Fee"]) else 0
        
        if not symbol or symbol == "" or symbol == "USD":
            continue
        
        key = (account, symbol)
        if key not in lots:
            lots[key] = []
        
        effect, qty = get_transaction_effect(row)
        
        if effect == "buy" and qty > 0:
            if price > 0:
                cost = qty * price + fee
            elif amount > 0:
                cost = amount + fee
            else:
                cost = 0
            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})
        
        elif effect == "sell" and qty > 0:
            qty_to_sell = qty
            while qty_to_sell > 0 and lots.get(key, []):
                lot = lots[key][0]
                if lot["remaining_qty"] <= qty_to_sell:
                    qty_to_sell -= lot["remaining_qty"]
                    lots[key].pop(0)
                else:
                    fraction = qty_to_sell / lot["remaining_qty"]
                    lot["remaining_qty"] -= qty_to_sell
                    lot["remaining_cost"] -= lot["remaining_cost"] * fraction
                    qty_to_sell = 0
        
        elif effect == "merger_remove":
            if lots.get(key):
                total_cost = sum(l["remaining_cost"] for l in lots[key])
                total_qty = sum(l["remaining_qty"] for l in lots[key])
                pending_mergers[key] = {"cost_basis": total_cost, "qty": total_qty, "date": date}
                lots[key] = []
        
        elif effect == "merger_cash":
            if key in pending_mergers:
                del pending_mergers[key]
        
        elif effect == "merger_receive":
            inherited_cost = 0
            for (acc, old_sym), merger_info in list(pending_mergers.items()):
                if acc == account and merger_info["date"] == date:
                    inherited_cost = merger_info["cost_basis"]
                    del pending_mergers[(acc, old_sym)]
                    break
            cost = inherited_cost if inherited_cost > 0 else amount
            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})
        
        elif effect == "cash_in_lieu":
            if key in pending_mergers:
                del pending_mergers[key]
        
        # Calculate new total cost basis
        new_cost_basis = sum(
            sum(lot["remaining_cost"] for lot in position_lots)
            for position_lots in lots.values()
        )
        
        # Accumulate delta for same day, or record if date changed
        if prev_date is not None and date != prev_date and pending_delta != 0:
            date_str = prev_date.strftime("%Y-%m-%d")
            sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache)
            cost_basis_changes.append({
                "date": prev_date,
                "delta": pending_delta,
                "sp500_price": sp500_price
            })
            pending_delta = 0.0
        
        pending_delta += (new_cost_basis - prev_cost_basis)
        prev_cost_basis = new_cost_basis
        prev_date = date
    
    # Record final pending delta
    if prev_date is not None and pending_delta != 0:
        date_str = prev_date.strftime("%Y-%m-%d")
        sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache)
        cost_basis_changes.append({
            "date": prev_date,
            "delta": pending_delta,
            "sp500_price": sp500_price
        })
    
    return cost_basis_changes


def calculate_cumulative_invested(df: pd.DataFrame, up_to_date: datetime) -> float:
    """
    Calculate total cost basis (money invested in current holdings) up to a given date.
    
    This tracks actual money put into positions that are still held, not gross cash flows.
    Uses simplified FIFO to estimate cost basis of remaining positions.
    """
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    # Filter to transactions up to the date
    period_df = df[df["Date"] <= up_to_date].copy()
    
    if period_df.empty:
        return 0.0
    
    # Calculate cost basis using simplified approach:
    # For each position, track buys and sells to get remaining cost basis
    
    total_cost_basis = 0.0
    
    # Group by account and symbol
    positions = {}  # (account, symbol) -> list of (qty, cost_per_share)
    
    for _, row in period_df.sort_values("Date").iterrows():
        action = row["Action"]
        account = row["Account"]
        symbol = row["Symbol"]
        qty = abs(row["Quantity"])
        amount = abs(row["Amount"])
        
        if symbol == "" or symbol == "USD":
            # Cash position - track separately for Apple Savings
            if account == "Apple Savings":
                key = (account, "USD")
                if key not in positions:
                    positions[key] = []
                if action == "Buy":
                    positions[key].append((qty, 1.0))  # USD at $1
                elif action == "Sell":
                    # Remove from position (FIFO)
                    remaining = qty
                    while remaining > 0 and positions[key]:
                        lot_qty, lot_cost = positions[key][0]
                        if lot_qty <= remaining:
                            positions[key].pop(0)
                            remaining -= lot_qty
                        else:
                            positions[key][0] = (lot_qty - remaining, lot_cost)
                            remaining = 0
            continue
        
        key = (account, symbol)
        if key not in positions:
            positions[key] = []
        
        if action == "Buy":
            cost_per_share = amount / qty if qty > 0 else 0
            positions[key].append((qty, cost_per_share))
        
        elif action == "Sell":
            # Remove from position using FIFO
            remaining = qty
            while remaining > 0 and positions[key]:
                lot_qty, lot_cost = positions[key][0]
                if lot_qty <= remaining:
                    positions[key].pop(0)
                    remaining -= lot_qty
                else:
                    positions[key][0] = (lot_qty - remaining, lot_cost)
                    remaining = 0
        
        elif action in ["Staking", "Dividend", "Interest"]:
            # Income that adds to position at zero cost (or current price)
            if qty > 0:
                positions[key].append((qty, 0))  # Zero cost basis for income
        
        elif action == "Transfer":
            # Transfers maintain cost basis (simplified: use amount as proxy)
            if qty > 0 and row["Amount"] >= 0:
                cost_per_share = amount / qty if qty > 0 else 0
                positions[key].append((qty, cost_per_share))
    
    # Sum up remaining cost basis
    for key, lots in positions.items():
        for lot_qty, lot_cost in lots:
            total_cost_basis += lot_qty * lot_cost
    
    return total_cost_basis


def calculate_historical_holdings(df: pd.DataFrame, price_cache: dict, 
                                   snapshot_dates: list = None,
                                   include_cash: pd.DataFrame = None) -> pd.DataFrame:
    """
    Calculate holdings at specific points in time with per-account breakdown.
    Uses historical prices for accurate point-in-time valuations.
    
    Returns DataFrame showing portfolio value over time, with columns for each account.
    """
    if df.empty:
        return pd.DataFrame(), []
    
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    today = datetime.now()
    
    # Default: monthly snapshots (end of each month) from 2019 onwards
    # Earlier data is sparse and takes too long to fetch
    # Prices are cached, so re-runs will be fast for already-fetched dates
    if snapshot_dates is None:
        min_year = max(df["Date"].min().year, 2019)  # Start from 2019 at earliest
        
        snapshot_dates = []
        # Monthly end dates (last day of each month)
        month_ends = [
            (1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30),
            (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31)
        ]
        
        for year in range(min_year, today.year + 1):
            for month, day in month_ends:
                try:
                    # Handle leap years for February
                    if month == 2:
                        if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                            day = 29
                        else:
                            day = 28
                    date = datetime(year, month, day)
                    if date <= today:
                        snapshot_dates.append(date.strftime("%Y-%m-%d"))
                except ValueError:
                    pass  # Skip invalid dates
        
        # Add today as the final snapshot
        snapshot_dates.append(today.strftime("%Y-%m-%d"))
    
    # Filter out future dates and deduplicate
    snapshot_dates = sorted(list(set([
        pd.to_datetime(d) for d in snapshot_dates if pd.to_datetime(d) <= today
    ])))
    
    # Get unique accounts
    accounts = sorted(df["Account"].unique())
    
    results = []
    
    print(f"Calculating historical holdings for {len(snapshot_dates)} dates across {len(accounts)} accounts...")
    
    for snapshot_date in snapshot_dates:
        # Filter transactions up to this date
        filtered_df = df[df["Date"] <= snapshot_date].copy()
        
        if filtered_df.empty:
            continue
        
        # Convert Date back to string for calculate_holdings compatibility
        filtered_df["Date"] = filtered_df["Date"].dt.strftime("%Y-%m-%d")
        
        # Calculate holdings quantities at this point (don't look up prices yet)
        holdings = calculate_holdings_quantities_only(filtered_df)
        
        if holdings.empty:
            continue
        
        # Now fetch historical prices for this snapshot date
        snapshot_date_str = snapshot_date.strftime("%Y-%m-%d")
        
        # Calculate value per account
        account_values = {account: 0.0 for account in accounts}
        total_value = 0
        
        for idx in holdings.index:
            account = holdings.loc[idx, "Account"]
            symbol = holdings.loc[idx, "Symbol"]
            qty = holdings.loc[idx, "Quantity"]
            
            # Get historical price for this date
            price, _ = get_price_from_yfinance(symbol, snapshot_date_str, price_cache)
            
            if price is not None:
                value = qty * price
                holdings.loc[idx, "Price"] = round(price, 4)
                holdings.loc[idx, "Value"] = round(value, 2)
                account_values[account] = account_values.get(account, 0) + value
                total_value += value
            else:
                holdings.loc[idx, "Price"] = 0
                holdings.loc[idx, "Value"] = 0
        
        # Add cash account values if provided
        # Note: Cash accounts like Apple Savings only hold USD, no investments
        # We need to calculate the cash balance at this point in time
        if include_cash is not None and not include_cash.empty:
            for _, cash_row in include_cash.iterrows():
                cash_account = cash_row["Account"]
                if cash_account in accounts:
                    # Get cash transactions up to this date from original df
                    cash_txns = df[(df["Account"] == cash_account) & (df["Date"] <= snapshot_date)]
                    if not cash_txns.empty:
                        # Calculate cash balance properly:
                        # Deposits (Buy): positive
                        # Withdrawals (Sell): negative
                        # Interest: positive
                        deposits = cash_txns[cash_txns["Action"] == "Buy"]["Amount"].sum()
                        withdrawals = cash_txns[cash_txns["Action"] == "Sell"]["Amount"].abs().sum()
                        interest = cash_txns[cash_txns["Action"] == "Interest"]["Amount"].sum()
                        cash_balance = deposits - withdrawals + interest
                        
                        if cash_balance > 0:
                            # Add to existing value (in case account has both cash and investments)
                            account_values[cash_account] = account_values.get(cash_account, 0) + cash_balance
                            total_value += cash_balance
        
        # Add snapshot date
        holdings["SnapshotDate"] = snapshot_date_str
        
        result_row = {
            "Date": snapshot_date_str,
            "TotalValue": round(total_value, 2),
            "NumPositions": len(holdings),
        }
        
        # Add per-account values
        for account in accounts:
            result_row[account] = round(account_values.get(account, 0), 2)
        
        results.append(result_row)
    
    # Fetch S&P 500 prices for all snapshot dates (for benchmark comparison)
    print("Fetching S&P 500 benchmark data...")
    sp500_symbol = "^GSPC"  # S&P 500 index
    for result in results:
        date_str = result["Date"]
        sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache)
        result["SP500"] = round(sp500_price, 2) if sp500_price else None
    
    # Calculate Time-Weighted Return
    print("Calculating time-weighted return...")
    results = calculate_time_weighted_return(results, df)
    
    # Calculate "What If S&P 500" - what portfolio would be worth if all invested in S&P 500
    print("Calculating 'What If S&P 500' comparison...")
    results = calculate_what_if_sp500(results, df, price_cache)
    
    # Create summary DataFrame with account columns
    if results:
        summary_df = pd.DataFrame(results)
    else:
        summary_df = pd.DataFrame()
    
    return summary_df, results


def calculate_holdings_quantities_only(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate current holdings quantities only (no price lookups).
    Used for historical holdings where we'll look up historical prices separately.
    """
    if df.empty:
        return pd.DataFrame(columns=["Account", "Symbol", "Quantity", "Currency"])
    
    df = df.copy()
    
    # Same get_signed_quantity logic as calculate_holdings
    def get_signed_quantity(row):
        action = row["Action"]
        qty = row["Quantity"]
        amount = row["Amount"]
        source = row["Source"]
        
        if action == "Buy":
            return abs(qty)
        elif action in ["StockSplit", "ReverseStockSplit"]:
            return 0
        elif action == "SpinOff":
            return abs(qty)
        elif action == "Sell":
            return -abs(qty)
        elif action in ["OptionBuy", "OptionSell", "OptionsExpiration"]:
            return 0
        elif action == "Transfer":
            if source == "Robinhood":
                if amount >= 0:
                    return abs(qty)
                else:
                    return -abs(qty)
            elif source in ["Coinbase", "Coinbase Pro"]:
                return qty
            elif source == "Schwab":
                symbol = row["Symbol"]
                if symbol and symbol != "" and qty != 0:
                    return 0
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
                if amount >= 0:
                    return abs(qty)
                else:
                    return -abs(qty)
        elif action == "Staking":
            return abs(qty)
        elif action in ["Dividend", "Interest"]:
            if qty > 0 and source == "Voya Atos 401K":
                return abs(qty)
            return 0
        else:
            return 0
    
    df["SignedQty"] = df.apply(get_signed_quantity, axis=1)
    
    holdings = df.groupby(["Account", "Symbol", "Currency"]).agg({
        "SignedQty": "sum"
    }).reset_index()
    
    holdings = holdings.rename(columns={"SignedQty": "Quantity"})
    holdings = holdings[holdings["Quantity"].abs() > 0.0001]
    holdings = holdings[holdings["Symbol"] != ""]
    holdings = holdings[holdings["Symbol"] != "USD"]
    holdings["Quantity"] = holdings["Quantity"].round(6)
    holdings["Price"] = 0.0
    holdings["Value"] = 0.0
    
    return holdings


# =============================================================================
# Account Summary Report
# =============================================================================

def generate_account_summary(holdings_df: pd.DataFrame, income_df: pd.DataFrame,
                              realized_gains_df: pd.DataFrame,
                              cash_balances_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Generate a summary report for each account showing:
    - Total value
    - Total cost basis
    - Unrealized gain/loss
    - Realized gain/loss (YTD and all-time)
    - Income received (YTD and all-time)
    
    Also includes cash-only accounts (like Apple Savings) from cash_balances_df.
    """
    if holdings_df.empty and (cash_balances_df is None or cash_balances_df.empty):
        return pd.DataFrame()
    
    current_year = datetime.now().year
    
    # Aggregate by account
    account_summary = holdings_df.groupby("Account").agg({
        "CurrentValue": "sum",
        "CostBasis": "sum",
        "UnrealizedGain": "sum"
    }).reset_index()
    
    account_summary = account_summary.rename(columns={
        "CurrentValue": "TotalValue",
        "CostBasis": "TotalCostBasis",
        "UnrealizedGain": "TotalUnrealizedGain"
    })
    
    # Calculate unrealized gain percentage
    account_summary["UnrealizedGainPct"] = (
        account_summary["TotalUnrealizedGain"] / account_summary["TotalCostBasis"] * 100
    ).round(2)
    
    # Add realized gains
    if not realized_gains_df.empty:
        realized_gains_df = realized_gains_df.copy()
        realized_gains_df["Year"] = pd.to_datetime(realized_gains_df["Date"]).dt.year
        
        # All-time realized gains
        all_time_realized = realized_gains_df.groupby("Account")["RealizedGain"].sum().reset_index()
        all_time_realized = all_time_realized.rename(columns={"RealizedGain": "AllTimeRealizedGain"})
        
        # YTD realized gains
        ytd_realized = realized_gains_df[realized_gains_df["Year"] == current_year].groupby("Account")["RealizedGain"].sum().reset_index()
        ytd_realized = ytd_realized.rename(columns={"RealizedGain": "YTDRealizedGain"})
        
        account_summary = account_summary.merge(all_time_realized, on="Account", how="left")
        account_summary = account_summary.merge(ytd_realized, on="Account", how="left")
    else:
        account_summary["AllTimeRealizedGain"] = 0
        account_summary["YTDRealizedGain"] = 0
    
    # Add income totals
    if not income_df.empty:
        # All-time income
        all_time_income = income_df.groupby("Account")["TotalAmount"].sum().reset_index()
        all_time_income = all_time_income.rename(columns={"TotalAmount": "AllTimeIncome"})
        
        # YTD income
        ytd_income = income_df[income_df["Year"] == current_year].groupby("Account")["TotalAmount"].sum().reset_index()
        ytd_income = ytd_income.rename(columns={"TotalAmount": "YTDIncome"})
        
        account_summary = account_summary.merge(all_time_income, on="Account", how="left")
        account_summary = account_summary.merge(ytd_income, on="Account", how="left")
    else:
        account_summary["AllTimeIncome"] = 0
        account_summary["YTDIncome"] = 0
    
    # Fill NaN with 0
    account_summary = account_summary.fillna(0)
    
    # Round values
    for col in ["TotalValue", "TotalCostBasis", "TotalUnrealizedGain", "AllTimeRealizedGain", 
                "YTDRealizedGain", "AllTimeIncome", "YTDIncome"]:
        if col in account_summary.columns:
            account_summary[col] = account_summary[col].round(2)
    
    # Add cash-only accounts (like Apple Savings)
    if cash_balances_df is not None and not cash_balances_df.empty:
        current_year = datetime.now().year
        cash_accounts = []
        for _, row in cash_balances_df.iterrows():
            # Calculate YTD interest from the Interest_YYYY columns if available
            ytd_interest_col = f"Interest_{current_year}"
            ytd_interest = row.get(ytd_interest_col, 0) if ytd_interest_col in row.index else 0
            
            cash_accounts.append({
                "Account": row["Account"],
                "TotalValue": round(row["CurrentBalance"], 2),
                "TotalCostBasis": round(row["NetContributions"], 2),
                "TotalUnrealizedGain": round(row["TotalInterest"], 2),  # Interest is the "gain"
                "UnrealizedGainPct": round(row["ReturnPct"], 2),
                "AllTimeRealizedGain": 0.0,
                "YTDRealizedGain": 0.0,
                "AllTimeIncome": round(row["TotalInterest"], 2),  # Interest is income
                "YTDIncome": round(ytd_interest, 2)
            })
        
        if cash_accounts:
            cash_df = pd.DataFrame(cash_accounts)
            account_summary = pd.concat([account_summary, cash_df], ignore_index=True)
    
    # Sort by total value
    account_summary = account_summary.sort_values("TotalValue", ascending=False).reset_index(drop=True)
    
    return account_summary


# =============================================================================
# Portfolio Summary Report
# =============================================================================

def generate_portfolio_summary(holdings_df: pd.DataFrame, account_summary_df: pd.DataFrame,
                                historical_df: pd.DataFrame, income_by_year_df: pd.DataFrame,
                                cash_balances_df: pd.DataFrame = None) -> dict:
    """
    Generate overall portfolio summary with key metrics.
    Includes cash savings accounts in total portfolio value.
    """
    summary = {}
    
    # Current portfolio value (investments)
    if not holdings_df.empty:
        investment_value = round(holdings_df["CurrentValue"].sum(), 2)
        summary["InvestmentValue"] = investment_value
        summary["TotalCostBasis"] = round(holdings_df["CostBasis"].sum(), 2)
        summary["TotalUnrealizedGain"] = round(holdings_df["UnrealizedGain"].sum(), 2)
        summary["UnrealizedGainPct"] = round(
            summary["TotalUnrealizedGain"] / summary["TotalCostBasis"] * 100 
            if summary["TotalCostBasis"] > 0 else 0, 2
        )
        summary["NumPositions"] = len(holdings_df)
        summary["NumAccounts"] = holdings_df["Account"].nunique()
    else:
        investment_value = 0
        summary["InvestmentValue"] = 0
        summary["TotalCostBasis"] = 0
        summary["TotalUnrealizedGain"] = 0
        summary["UnrealizedGainPct"] = 0
        summary["NumPositions"] = 0
        summary["NumAccounts"] = 0
    
    # Cash balances (savings accounts like Apple Savings)
    cash_value = 0
    cash_interest = 0
    if cash_balances_df is not None and not cash_balances_df.empty:
        cash_value = round(cash_balances_df["CurrentBalance"].sum(), 2)
        cash_interest = round(cash_balances_df["TotalInterest"].sum(), 2)
        summary["CashValue"] = cash_value
        summary["CashInterest"] = cash_interest
        summary["CashAccounts"] = cash_balances_df[["Account", "CurrentBalance", "TotalInterest"]].to_dict('records')
        summary["NumAccounts"] = summary.get("NumAccounts", 0) + len(cash_balances_df)
    else:
        summary["CashValue"] = 0
        summary["CashInterest"] = 0
    
    # Total portfolio value (investments + cash)
    summary["TotalValue"] = round(investment_value + cash_value, 2)
    
    # Realized gains from account summary
    if not account_summary_df.empty:
        summary["AllTimeRealizedGain"] = round(account_summary_df["AllTimeRealizedGain"].sum(), 2)
        summary["YTDRealizedGain"] = round(account_summary_df["YTDRealizedGain"].sum(), 2)
        summary["AllTimeIncome"] = round(account_summary_df["AllTimeIncome"].sum(), 2)
        summary["YTDIncome"] = round(account_summary_df["YTDIncome"].sum(), 2)
    else:
        summary["AllTimeRealizedGain"] = 0
        summary["YTDRealizedGain"] = 0
        summary["AllTimeIncome"] = 0
        summary["YTDIncome"] = 0
    
    # Total return (unrealized + realized + income)
    summary["TotalReturn"] = round(
        summary["TotalUnrealizedGain"] + summary["AllTimeRealizedGain"] + summary["AllTimeIncome"], 2
    )
    summary["TotalReturnPct"] = round(
        summary["TotalReturn"] / summary["TotalCostBasis"] * 100 
        if summary["TotalCostBasis"] > 0 else 0, 2
    )
    
    # Historical performance
    if not historical_df.empty and len(historical_df) > 1:
        first_value = historical_df.iloc[0]["TotalValue"]
        last_value = historical_df.iloc[-1]["TotalValue"]
        summary["AllTimeGrowth"] = round(last_value - first_value, 2)
        summary["AllTimeGrowthPct"] = round(
            (last_value - first_value) / first_value * 100 if first_value > 0 else 0, 2
        )
    
    # Asset allocation by account type (rough categorization)
    if not holdings_df.empty or (cash_balances_df is not None and not cash_balances_df.empty):
        account_allocation = {}
        total = summary["TotalValue"]
        
        # Add investment accounts
        if not holdings_df.empty:
            for acc, val in holdings_df.groupby("Account")["CurrentValue"].sum().items():
                account_allocation[acc] = val
        
        # Add cash accounts
        if cash_balances_df is not None and not cash_balances_df.empty:
            for _, row in cash_balances_df.iterrows():
                account_allocation[row["Account"]] = row["CurrentBalance"]
        
        summary["AccountAllocation"] = {
            acc: round(val / total * 100, 2) for acc, val in account_allocation.items()
        } if total > 0 else {}
    
    # Top holdings
    if not holdings_df.empty:
        top_holdings = holdings_df.nlargest(10, "CurrentValue")[["Symbol", "CurrentValue", "UnrealizedGainPct"]].to_dict('records')
        summary["TopHoldings"] = top_holdings
    
    return summary


# =============================================================================
# Export Functions for Reports
# =============================================================================

def export_holdings_detail_csv(df: pd.DataFrame, filename: str = "holdings_detail.csv"):
    """Export detailed holdings with cost basis to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported detailed holdings to: {output_path}")
    return output_path


def export_realized_gains_csv(df: pd.DataFrame, filename: str = "realized_gains.csv"):
    """Export realized gains/losses to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported realized gains to: {output_path}")
    return output_path


def export_tax_lots_csv(df: pd.DataFrame, filename: str = "tax_lots.csv"):
    """Export tax lots detail to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported tax lots to: {output_path}")
    return output_path


def export_income_report_csv(df: pd.DataFrame, filename: str = "income_report.csv"):
    """Export income report to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported income report to: {output_path}")
    return output_path


def export_income_by_year_csv(df: pd.DataFrame, filename: str = "income_by_year.csv"):
    """Export income by year to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported income by year to: {output_path}")
    return output_path


def export_account_summary_csv(df: pd.DataFrame, filename: str = "account_summary.csv"):
    """Export account summary to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported account summary to: {output_path}")
    return output_path


def export_historical_holdings_csv(df: pd.DataFrame, filename: str = "historical_holdings.csv"):
    """Export historical holdings summary to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported historical holdings to: {output_path}")
    return output_path


def export_portfolio_summary_js(summary: dict, filename: str = "portfolio_summary.js"):
    """Export portfolio summary as a JavaScript file to dashboard/data folder."""
    js_content = "// Portfolio data - auto-generated by detectAndClean.py\n"
    js_content += "const portfolioData = "
    js_content += json.dumps(summary, indent=2)
    js_content += ";\n"
    
    # Write to dashboard/data folder only
    output_path = DASHBOARD_DATA_PATH / filename
    with open(output_path, "w") as f:
        f.write(js_content)
    
    print(f"✓ Exported portfolio summary to: {output_path}")
    return output_path


def export_dataframe_js(df: pd.DataFrame, var_name: str, filename: str):
    """Export a DataFrame as a JavaScript file to dashboard/data folder."""
    # Convert DataFrame to list of dicts for JSON serialization
    data = df.to_dict(orient='records')
    js_content = f"// {var_name} - auto-generated by detectAndClean.py\n"
    js_content += f"const {var_name} = "
    js_content += json.dumps(data, indent=2)
    js_content += ";\n"
    
    # Write to dashboard/data folder only
    output_path = DASHBOARD_DATA_PATH / filename
    with open(output_path, "w") as f:
        f.write(js_content)
    
    print(f"✓ Exported {var_name} to: {output_path}")
    return output_path


def export_holdings_csv(df: pd.DataFrame, filename: str = "holdings.csv"):
    """Export the holdings DataFrame to CSV."""
    output_path = DATA_OUTPUT_PATH / filename
    df.to_csv(output_path, index=False)
    print(f"✓ Exported holdings to: {output_path}")
    return output_path


def print_holdings_summary(holdings: pd.DataFrame):
    """Print a summary of holdings by account."""
    if holdings.empty:
        print("\nNo holdings to display.")
        return
    
    # Check which columns are available
    value_col = "CurrentValue" if "CurrentValue" in holdings.columns else "Value"
    price_col = "CurrentPrice"
    
    print("\n" + "="*60)
    print("Holdings Summary")
    print("="*60)
    
    # Summary by account
    account_totals = holdings.groupby("Account")[value_col].sum().sort_values(ascending=False)
    
    print("\nTotal Value by Account:")
    for account, value in account_totals.items():
        print(f"  {account}: ${value:,.2f}")
    
    print(f"\n  {'─'*40}")
    print(f"  Total Portfolio Value: ${account_totals.sum():,.2f}")
    
    # Top holdings
    print("\nTop 10 Holdings by Value:")
    top_holdings = holdings.nlargest(10, value_col)
    for _, row in top_holdings.iterrows():
        print(f"  {row['Symbol']:12} {row['Quantity']:>12.4f} @ ${row[price_col]:>10.2f} = ${row[value_col]:>12,.2f}  ({row['Account']})")


def print_portfolio_summary(summary: dict):
    """Print the full portfolio summary to console."""
    print("\n" + "="*80)
    print("PORTFOLIO SUMMARY")
    print("="*80)
    
    print(f"\n{'─'*40}")
    print("CURRENT HOLDINGS")
    print(f"{'─'*40}")
    print(f"  Total Portfolio Value:      ${summary.get('TotalValue', 0):>15,.2f}")
    
    # Show breakdown if we have both investments and cash
    if summary.get('CashValue', 0) > 0:
        print(f"    ├─ Investments:           ${summary.get('InvestmentValue', 0):>15,.2f}")
        print(f"    └─ Cash/Savings:          ${summary.get('CashValue', 0):>15,.2f}")
    
    print(f"  Total Cost Basis:           ${summary.get('TotalCostBasis', 0):>15,.2f}")
    print(f"  Unrealized Gain/Loss:       ${summary.get('TotalUnrealizedGain', 0):>15,.2f} ({summary.get('UnrealizedGainPct', 0):>+.2f}%)")
    print(f"  Number of Positions:        {summary.get('NumPositions', 0):>15}")
    print(f"  Number of Accounts:         {summary.get('NumAccounts', 0):>15}")
    
    # Long-term / Short-term breakdown
    if "LongTermValue" in summary or "ShortTermValue" in summary:
        print(f"\n{'─'*40}")
        print("TAX LOT BREAKDOWN")
        print(f"{'─'*40}")
        print(f"  Long-term Value:            ${summary.get('LongTermValue', 0):>15,.2f}")
        print(f"  Long-term Unrealized Gain:  ${summary.get('LongTermUnrealizedGain', 0):>15,.2f}")
        print(f"  Short-term Value:           ${summary.get('ShortTermValue', 0):>15,.2f}")
        print(f"  Short-term Unrealized Gain: ${summary.get('ShortTermUnrealizedGain', 0):>15,.2f}")
    
    print(f"\n{'─'*40}")
    print("REALIZED GAINS/LOSSES")
    print(f"{'─'*40}")
    print(f"  YTD Realized Gain/Loss:     ${summary.get('YTDRealizedGain', 0):>15,.2f}")
    print(f"  All-Time Realized Gain/Loss:${summary.get('AllTimeRealizedGain', 0):>15,.2f}")
    
    print(f"\n{'─'*40}")
    print("INCOME")
    print(f"{'─'*40}")
    print(f"  YTD Income:                 ${summary.get('YTDIncome', 0):>15,.2f}")
    print(f"  All-Time Income:            ${summary.get('AllTimeIncome', 0):>15,.2f}")
    
    print(f"\n{'─'*40}")
    print("TOTAL RETURN")
    print(f"{'─'*40}")
    print(f"  Total Return (All-Time):    ${summary.get('TotalReturn', 0):>15,.2f} ({summary.get('TotalReturnPct', 0):>+.2f}%)")
    
    if "AllTimeGrowth" in summary:
        print(f"  Portfolio Growth:           ${summary.get('AllTimeGrowth', 0):>15,.2f} ({summary.get('AllTimeGrowthPct', 0):>+.2f}%)")
    
    # Account allocation
    if "AccountAllocation" in summary and summary["AccountAllocation"]:
        print(f"\n{'─'*40}")
        print("ALLOCATION BY ACCOUNT")
        print(f"{'─'*40}")
        for account, pct in sorted(summary["AccountAllocation"].items(), key=lambda x: -x[1]):
            print(f"  {account:30} {pct:>6.2f}%")
    
    # Top holdings
    if "TopHoldings" in summary and summary["TopHoldings"]:
        print(f"\n{'─'*40}")
        print("TOP 10 HOLDINGS")
        print(f"{'─'*40}")
        for holding in summary["TopHoldings"]:
            gain_pct = holding.get("UnrealizedGainPct", 0)
            gain_str = f"({gain_pct:+.1f}%)" if gain_pct != 0 else ""
            print(f"  {holding['Symbol']:12} ${holding['CurrentValue']:>12,.2f} {gain_str}")
    
    print("\n" + "="*80)


# =============================================================================
# Retirement Data Parsing
# =============================================================================

def load_retirement_data() -> pd.DataFrame:
    """
    Load retirement data from the retirement-data.csv file.
    This file contains salary history, bonus history, and other retirement-related info.
    
    Returns a DataFrame with columns: Type, Date, Amount, Symbol, Note
    """
    retirement_file = DATA_INPUT_PATH / "retirement-data.csv"
    
    if not retirement_file.exists():
        print(f"Retirement data file not found: {retirement_file}")
        return pd.DataFrame()
    
    df = pd.read_csv(retirement_file)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Amount"] = pd.to_numeric(df["Amount"], errors='coerce').fillna(0)
    
    return df


def calculate_retirement_projections(retirement_df: pd.DataFrame, 
                                       holdings_detail_df: pd.DataFrame = None,
                                       current_age: int = 30) -> dict:
    """
    Calculate retirement projections based on salary, contributions, and holdings.
    
    Assumptions:
    - 401k contribution limit: $23,500 (2025), increases ~$500/year
    - IRA contribution limit: $7,000 (2025)
    - Employer 401k match: typically 50% up to 6% of salary
    - Retirement age: 65
    - Social Security FRA: 67
    """
    result = {
        "current_salary": 0,
        "salary_history": [],
        "bonus_history": [],
        "total_bonuses_ytd": 0,
        "total_bonuses_all_time": 0,
        "avg_annual_bonus": 0,
        "contribution_limits_401k": 23500,  # 2025 limit
        "contribution_limits_ira": 7000,    # 2025 limit
        "contribution_limits_roth_ira": 7000,
        "retirement_accounts": {},
        "taxable_accounts": {},
        "estimated_annual_savings_capacity": 0,
    }
    
    if retirement_df.empty:
        return result
    
    # Extract salary history
    salary_df = retirement_df[retirement_df["Type"] == "Salary History"].copy()
    salary_df = salary_df.sort_values("Date")
    
    if not salary_df.empty:
        result["current_salary"] = salary_df.iloc[-1]["Amount"]
        result["salary_history"] = [
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "amount": row["Amount"],
                "note": row["Note"]
            }
            for _, row in salary_df.iterrows()
        ]
    
    # Extract bonus history
    bonus_df = retirement_df[retirement_df["Type"] == "Bonus History"].copy()
    bonus_df = bonus_df.sort_values("Date")
    
    if not bonus_df.empty:
        result["bonus_history"] = [
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "amount": row["Amount"],
                "note": row["Note"]
            }
            for _, row in bonus_df.iterrows()
        ]
        
        # Calculate bonus stats
        current_year = datetime.now().year
        ytd_bonuses = bonus_df[bonus_df["Date"].dt.year == current_year]["Amount"].sum()
        result["total_bonuses_ytd"] = round(ytd_bonuses, 2)
        result["total_bonuses_all_time"] = round(bonus_df["Amount"].sum(), 2)
        
        # Average annual bonus (excluding current year if incomplete)
        years_with_data = bonus_df["Date"].dt.year.nunique()
        if years_with_data > 1:
            past_bonuses = bonus_df[bonus_df["Date"].dt.year < current_year]["Amount"].sum()
            past_years = bonus_df[bonus_df["Date"].dt.year < current_year]["Date"].dt.year.nunique()
            if past_years > 0:
                result["avg_annual_bonus"] = round(past_bonuses / past_years, 2)
    
    # Calculate savings capacity
    salary = result["current_salary"]
    if salary > 0:
        # Estimate take-home after taxes (rough estimate ~70% of gross)
        estimated_take_home = salary * 0.70
        # Max 401k + IRA contributions
        max_tax_advantaged = result["contribution_limits_401k"] + result["contribution_limits_ira"]
        # Assume employer match of 50% up to 6%
        employer_match = min(salary * 0.03, result["contribution_limits_401k"] * 0.5)
        result["estimated_employer_match"] = round(employer_match, 2)
        result["estimated_annual_savings_capacity"] = round(max_tax_advantaged + employer_match, 2)
    
    # Categorize accounts from holdings
    if holdings_detail_df is not None and not holdings_detail_df.empty:
        account_values = holdings_detail_df.groupby("Account")["CurrentValue"].sum()
        
        retirement_keywords = ["401k", "401K", "IRA", "ira", "Rollover", "Roth"]
        
        for account, value in account_values.items():
            is_retirement = any(kw in account for kw in retirement_keywords)
            if is_retirement:
                result["retirement_accounts"][account] = round(value, 2)
            else:
                result["taxable_accounts"][account] = round(value, 2)
        
        result["total_retirement_value"] = round(sum(result["retirement_accounts"].values()), 2)
        result["total_taxable_value"] = round(sum(result["taxable_accounts"].values()), 2)
    
    return result


def calculate_contribution_tracking(master_df: pd.DataFrame, retirement_df: pd.DataFrame = None) -> dict:
    """
    Track 401k and IRA contributions for the current year.
    
    Returns contribution amounts by account type.
    """
    result = {
        "year": datetime.now().year,
        "traditional_401k": 0,
        "roth_401k": 0,
        "traditional_ira": 0,
        "roth_ira": 0,
        "employer_contributions": 0,
        "by_account": {},
        "roth_ira_history": [],
        "roth_ira_total_contributed": 0,
    }
    
    current_year = datetime.now().year
    
    # IRA contribution limits by year
    IRA_LIMITS = {
        2019: 6000,
        2020: 6000,
        2021: 6000,
        2022: 6000,
        2023: 6500,
        2024: 7000,
        2025: 7000,
    }
    
    # Track contributions by tax year from transaction data
    # Contributions are identified by:
    # 1. Note containing "CONTRIBUTION" (e.g., "PRIOR YEAR CONTRIBUTION", "CURRENT YEAR CONTRIBUTION")
    # 2. Cash transfers into the account (Transfer action with no symbol)
    roth_ira_by_year = {}
    
    if not master_df.empty:
        df = master_df.copy()
        df["Year"] = pd.to_datetime(df["Date"]).dt.year
        
        # Find Roth IRA accounts (exclude Roth 401k)
        roth_ira_mask = (
            df["Account"].str.lower().str.contains("roth") & 
            df["Account"].str.lower().str.contains("ira") &
            ~df["Account"].str.lower().str.contains("401")
        )
        roth_ira_txns = df[roth_ira_mask].copy()
        
        if not roth_ira_txns.empty:
            # Method 1: Look for transactions with "CONTRIBUTION" in the Note
            # This catches USAA Victory Capital style contributions
            contrib_note_mask = (
                roth_ira_txns["Note"].str.upper().str.contains("CONTRIBUTION", na=False) &
                (roth_ira_txns["Amount"] > 0)
            )
            
            # Method 2: Look for cash transfers (Transfer with no symbol = bank transfer in)
            cash_transfer_mask = (
                (roth_ira_txns["Action"] == "Transfer") &
                (roth_ira_txns["Amount"] > 0) &
                (roth_ira_txns["Symbol"].isna() | (roth_ira_txns["Symbol"] == ""))
            )
            
            # Combine both methods
            roth_contribs = roth_ira_txns[contrib_note_mask | cash_transfer_mask]
            
            for _, row in roth_contribs.iterrows():
                transaction_year = row["Year"]
                note = str(row.get("Note", "")).upper()
                
                # Determine tax year - check if it's a prior year contribution
                if "PRIOR YEAR" in note:
                    tax_year = transaction_year - 1
                else:
                    tax_year = transaction_year
                
                if tax_year not in roth_ira_by_year:
                    roth_ira_by_year[tax_year] = 0
                roth_ira_by_year[tax_year] += row["Amount"]
    
    # Build the history from combined sources
    for year in sorted(roth_ira_by_year.keys()):
        amount = roth_ira_by_year[year]
        limit = IRA_LIMITS.get(year, 6000)
        # Cap at the limit (shouldn't exceed, but just in case)
        amount = min(amount, limit)
        result["roth_ira_history"].append({
            "year": year,
            "amount": amount,
            "note": f"Roth IRA - {'maxed' if amount >= limit else 'partial'}"
        })
        result["roth_ira_total_contributed"] += amount
        
        if year == current_year:
            result["roth_ira"] = amount
    
    if master_df.empty:
        return result
    
    # 401k contribution limits by year
    LIMIT_401K = {
        2019: 19000,
        2020: 19500,
        2021: 19500,
        2022: 20500,
        2023: 22500,
        2024: 23000,
        2025: 23500,
    }
    
    # Filter to current year contributions
    df = master_df.copy()
    df["Year"] = pd.to_datetime(df["Date"]).dt.year
    ytd_df = df[df["Year"] == current_year]
    
    # Track 401k contributions from transaction data
    for account in ytd_df["Account"].unique():
        account_lower = account.lower()
        account_df = ytd_df[ytd_df["Account"] == account]
        
        # Sum positive amounts for contributions
        buys = account_df[account_df["Action"].isin(["Buy", "Contribution"])]
        transfers_in = account_df[(account_df["Action"] == "Transfer") & (account_df["Amount"] > 0)]
        
        total = buys["Amount"].sum() + transfers_in["Amount"].sum()
        
        if total > 0:
            result["by_account"][account] = round(total, 2)
            
            # Categorize by account type - only track 401k from transactions
            if "401k" in account_lower or "401(k)" in account_lower:
                if "roth" in account_lower:
                    result["roth_401k"] += total
                else:
                    result["traditional_401k"] += total
    
    # If we didn't get Roth IRA from retirement_df, use fallback
    if result["roth_ira"] == 0:
        result["roth_ira"] = IRA_LIMITS.get(current_year, 7000)
        
        # Calculate historical IRA contributions from transaction data
        roth_accounts = df[df["Account"].str.lower().str.contains("roth") & 
                           df["Account"].str.lower().str.contains("ira")]
        if not roth_accounts.empty:
            first_roth_year = roth_accounts["Year"].min()
            total_roth_contributions = sum(
                IRA_LIMITS.get(year, 6000) 
                for year in range(first_roth_year, current_year + 1)
                if year in IRA_LIMITS
            )
            result["roth_ira_total_contributed"] = total_roth_contributions
            result["roth_ira_contribution_years"] = list(range(first_roth_year, current_year + 1))
    
    return result


def calculate_budget_metrics(retirement_data: dict) -> dict:
    """
    Calculate monthly budget metrics based on salary.
    """
    salary = retirement_data.get("current_salary", 0)
    if salary == 0:
        return {}
    
    monthly_gross = salary / 12
    
    # Rough tax estimates (federal + state + FICA)
    # This is a simplified estimate
    if salary > 200000:
        effective_tax_rate = 0.35
    elif salary > 100000:
        effective_tax_rate = 0.28
    elif salary > 50000:
        effective_tax_rate = 0.22
    else:
        effective_tax_rate = 0.15
    
    monthly_taxes = monthly_gross * effective_tax_rate
    monthly_net = monthly_gross - monthly_taxes
    
    # 401k contribution (max pre-tax)
    annual_401k_limit = 23500
    monthly_401k = annual_401k_limit / 12
    
    # IRA contribution
    annual_ira_limit = 7000
    monthly_ira = annual_ira_limit / 12
    
    return {
        "monthly_gross": round(monthly_gross, 2),
        "monthly_estimated_taxes": round(monthly_taxes, 2),
        "monthly_net": round(monthly_net, 2),
        "monthly_max_401k": round(monthly_401k, 2),
        "monthly_max_ira": round(monthly_ira, 2),
        "annual_gross": round(salary, 2),
        "annual_max_401k": annual_401k_limit,
        "annual_max_ira": annual_ira_limit,
        "effective_tax_rate": round(effective_tax_rate * 100, 1)
    }


def generate_retirement_summary(master_df: pd.DataFrame, 
                                 holdings_detail_df: pd.DataFrame = None) -> dict:
    """
    Generate comprehensive retirement summary for dashboard.
    """
    # Load retirement data
    retirement_df = load_retirement_data()
    
    # Extract personal info (birthday)
    birthday = None
    current_age = 35  # Default
    years_to_retirement = 30  # Default (retire at 65)
    retirement_age = 65
    
    if not retirement_df.empty:
        personal_info = retirement_df[retirement_df["Type"] == "Personal Info"]
        birthday_row = personal_info[personal_info["Note"].str.contains("Birthday", case=False, na=False)]
        if not birthday_row.empty:
            birthday = birthday_row.iloc[0]["Date"]
            if pd.notna(birthday):
                today = datetime.now()
                # Calculate age
                age = today.year - birthday.year
                # Adjust if birthday hasn't occurred this year
                if (today.month, today.day) < (birthday.month, birthday.day):
                    age -= 1
                current_age = age
                years_to_retirement = max(0, retirement_age - current_age)
    
    # Calculate projections
    projections = calculate_retirement_projections(retirement_df, holdings_detail_df, current_age)
    
    # Calculate contributions (pass retirement_df for IRA contribution history)
    contributions = calculate_contribution_tracking(master_df, retirement_df)
    
    # Calculate budget metrics
    budget = calculate_budget_metrics(projections)
    
    # Combine into summary
    summary = {
        "personal": {
            "birthday": birthday.strftime("%Y-%m-%d") if pd.notna(birthday) else None,
            "current_age": current_age,
            "retirement_age": retirement_age,
            "years_to_retirement": years_to_retirement,
        },
        "salary": {
            "current": projections.get("current_salary", 0),
            "history": projections.get("salary_history", []),
        },
        "bonuses": {
            "history": projections.get("bonus_history", []),
            "ytd": projections.get("total_bonuses_ytd", 0),
            "all_time": projections.get("total_bonuses_all_time", 0),
            "avg_annual": projections.get("avg_annual_bonus", 0),
        },
        "contributions": contributions,
        "limits": {
            "traditional_401k": 23500,
            "roth_401k": 23500,  # Combined limit with traditional
            "total_401k": 23500,
            "catch_up_401k": 7500,  # Age 50+
            "traditional_ira": 7000,
            "roth_ira": 7000,
            "catch_up_ira": 1000,  # Age 50+
        },
        "accounts": {
            "retirement": projections.get("retirement_accounts", {}),
            "taxable": projections.get("taxable_accounts", {}),
            "total_retirement": projections.get("total_retirement_value", 0),
            "total_taxable": projections.get("total_taxable_value", 0),
        },
        "budget": budget,
        "employer_match": projections.get("estimated_employer_match", 0),
        "savings_capacity": projections.get("estimated_annual_savings_capacity", 0),
    }
    
    return summary


def export_retirement_data_js(summary: dict, filename: str = "retirement_data.js"):
    """Export retirement data as JavaScript for dashboard."""
    js_content = "// retirementData - auto-generated by detectAndClean.py\n"
    js_content += "const retirementData = "
    js_content += json.dumps(summary, indent=2)
    js_content += ";\n"
    
    output_path = DASHBOARD_DATA_PATH / filename
    with open(output_path, "w") as f:
        f.write(js_content)
    
    print(f"✓ Exported retirement data to: {output_path}")
    return output_path


def generate_all_reports(master_df: pd.DataFrame, price_cache: dict):
    """
    Generate all reports from the master transaction data.
    
    Reports generated:
    1. holdings.csv - Simple current holdings (Account, Symbol, Quantity, Price, Value)
    2. holdings_detail.csv - Detailed holdings with cost basis and returns
    3. tax_lots.csv - Individual tax lots with holding periods
    4. realized_gains.csv - All realized gains/losses from sales
    5. income_report.csv - Detailed income by symbol and year
    6. income_by_year.csv - Income summary by year for tax purposes
    7. cash_balances.csv - Cash account balances (e.g., Apple Savings)
    8. account_summary.csv - Summary metrics per account
    9. historical_holdings.csv - Portfolio value over time
    10. portfolio_summary.json - Overall portfolio metrics
    """
    print("\n" + "="*60)
    print("Generating Reports")
    print("="*60)
    
    # 1. Simple holdings (for backwards compatibility)
    print("\n1. Calculating current holdings...")
    simple_holdings = calculate_holdings(master_df, price_cache)
    export_holdings_csv(simple_holdings)
    
    # 2. Detailed holdings with cost basis and tax lots
    print("\n2. Calculating cost basis (FIFO method) and tax lots...")
    holdings_detail, realized_gains, tax_lots = calculate_cost_basis(master_df, price_cache)
    
    # Add sector information to holdings
    if not holdings_detail.empty:
        print("\n2b. Adding sector information...")
        sector_cache = load_sector_cache()
        sectors = get_sectors_for_holdings(holdings_detail, sector_cache)
        holdings_detail["Sector"] = holdings_detail["Symbol"].map(sectors)
        export_holdings_detail_csv(holdings_detail)
    
    # 3. Tax lots
    print("\n3. Exporting tax lots...")
    if not tax_lots.empty:
        export_tax_lots_csv(tax_lots)
        long_term = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]
        short_term = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]
        print(f"   Long-term lots: {len(long_term)}, Short-term lots: {len(short_term)}")
    else:
        print("   No tax lots to export.")
    
    # 4. Realized gains
    print("\n4. Exporting realized gains...")
    if not realized_gains.empty:
        export_realized_gains_csv(realized_gains)
        print(f"   Total realized gains: ${realized_gains['RealizedGain'].sum():,.2f}")
    else:
        print("   No realized gains to export.")
    
    # 5. Income report
    print("\n5. Calculating income...")
    income_report = calculate_income(master_df)
    if not income_report.empty:
        export_income_report_csv(income_report)
        print(f"   Total income transactions: {len(income_report)}")
    else:
        print("   No income to report.")
    
    # 6. Income by year
    print("\n6. Summarizing income by year...")
    income_by_year = calculate_income_by_year(master_df)
    if not income_by_year.empty:
        export_income_by_year_csv(income_by_year)
    
    # 7. Cash balances (for USD-only accounts like Apple Savings)
    print("\n7. Calculating cash account balances...")
    cash_balances = calculate_cash_balances(master_df)
    if not cash_balances.empty:
        export_cash_balances_csv(cash_balances)
        for _, row in cash_balances.iterrows():
            print(f"   {row['Account']}: Balance=${row['CurrentBalance']:,.2f}, Interest=${row['TotalInterest']:,.2f} ({row['ReturnPct']:.2f}%)")
    else:
        print("   No cash-only accounts found.")
    
    # 8. Account summary
    print("\n8. Generating account summary...")
    account_summary = generate_account_summary(holdings_detail, income_report, realized_gains, cash_balances)
    if not account_summary.empty:
        export_account_summary_csv(account_summary)
    
    # 9. Historical holdings (with per-account breakdown)
    print("\n9. Calculating historical holdings...")
    historical_summary, historical_details = calculate_historical_holdings(
        master_df, price_cache, include_cash=cash_balances
    )
    if not historical_summary.empty:
        export_historical_holdings_csv(historical_summary)
    
    # 10. Portfolio summary
    print("\n10. Generating portfolio summary...")
    portfolio_summary = generate_portfolio_summary(
        holdings_detail, account_summary, historical_summary, income_by_year, cash_balances
    )
    portfolio_summary["GeneratedAt"] = datetime.now().isoformat()
    
    # Add tax lot summary to portfolio summary
    if not tax_lots.empty:
        long_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["CurrentValue"].sum()
        short_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]["CurrentValue"].sum()
        long_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["UnrealizedGain"].sum()
        short_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]["UnrealizedGain"].sum()
        portfolio_summary["LongTermValue"] = round(long_term_value, 2)
        portfolio_summary["ShortTermValue"] = round(short_term_value, 2)
        portfolio_summary["LongTermUnrealizedGain"] = round(long_term_gain, 2)
        portfolio_summary["ShortTermUnrealizedGain"] = round(short_term_gain, 2)
    
    export_portfolio_summary_js(portfolio_summary)
    
    # Export additional data as JS for the dashboard
    print("\n11. Exporting JavaScript data files for dashboard...")
    if not holdings_detail.empty:
        export_dataframe_js(holdings_detail, "holdingsDetailData", "holdings_detail.js")
    if not account_summary.empty:
        export_dataframe_js(account_summary, "accountSummaryData", "account_summary.js")
    if not income_by_year.empty:
        export_dataframe_js(income_by_year, "incomeByYearData", "income_by_year.js")
    if not historical_summary.empty:
        export_dataframe_js(historical_summary, "historicalHoldingsData", "historical_holdings.js")
    if not realized_gains.empty:
        export_dataframe_js(realized_gains, "realizedGainsData", "realized_gains.js")
    if not cash_balances.empty:
        export_dataframe_js(cash_balances, "cashBalancesData", "cash_balances.js")
    
    # Export master transactions with running balances for transaction detail view
    print("\n12. Adding running balances to transactions...")
    master_with_balances = add_running_balances(master_df, simple_holdings)
    
    # Add running cost basis using FIFO
    print("\n13. Adding running cost basis to transactions...")
    master_with_balances = add_running_cost_basis(master_with_balances)
    export_dataframe_js(master_with_balances, "masterTransactionsData", "master_transactions.js")
    
    # Calculate and export portfolio cost basis history
    print("\n14. Calculating portfolio cost basis history...")
    portfolio_cost_basis = calculate_portfolio_cost_basis_history(master_df)
    if not portfolio_cost_basis.empty:
        export_dataframe_js(portfolio_cost_basis, "portfolioCostBasisData", "portfolio_cost_basis.js")
        print(f"   Exported {len(portfolio_cost_basis)} cost basis data points")
    
    # 15. Retirement data (salary, bonuses, contribution tracking)
    print("\n15. Generating retirement summary...")
    retirement_summary = generate_retirement_summary(master_df, holdings_detail)
    export_retirement_data_js(retirement_summary)
    
    # Print summary to console
    print_portfolio_summary(portfolio_summary)
    
    return {
        "simple_holdings": simple_holdings,
        "holdings_detail": holdings_detail,
        "tax_lots": tax_lots,
        "realized_gains": realized_gains,
        "income_report": income_report,
        "income_by_year": income_by_year,
        "cash_balances": cash_balances,
        "account_summary": account_summary,
        "historical_summary": historical_summary,
        "portfolio_summary": portfolio_summary
    }


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    # Process all input files
    master_df = process_all_files()
    
    # Export to CSV
    if len(master_df) > 0:
        export_master_csv(master_df)
        
        # Reload price cache after processing (parsers may have added entries)
        price_cache = load_price_cache()
        
        # Generate all reports
        reports = generate_all_reports(master_df, price_cache)
        
        # Save price cache (may have new prices from report generation)
        save_price_cache(price_cache)
        
        # Show sample of output
        print("\nSample of master transactions (first 10 rows):")
        print(master_df.head(10).to_string())
    else:
        print("\nNo transactions to export.")
