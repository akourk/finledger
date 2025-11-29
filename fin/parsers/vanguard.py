"""
Vanguard SF 401K Parser
=======================
Parser for Vanguard SF 401K transaction files.
"""

import pathlib
import pandas as pd
from fin.parsers.base import create_empty_dataframe
from fin.utils.formatting import parse_date, standardize_action
from fin.utils.cache import load_price_cache, save_price_cache
from fin.utils.prices import get_price_from_yfinance
from fin.config import UNIFIED_COLUMNS


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
        needs_price_lookup = pd.isna(quantity) or quantity == 0
        
        if needs_price_lookup and symbol and amount > 0:
            # Fetch actual price from yfinance
            actual_price, was_fetched = get_price_from_yfinance(symbol, date_str, price_cache, None)
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
                actual_price, was_fetched = get_price_from_yfinance(symbol, date_str, price_cache, None)
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
