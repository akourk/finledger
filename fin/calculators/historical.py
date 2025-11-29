"""
Historical Holdings Calculator Module
=====================================
Calculates historical holdings over time with proper valuations.
Includes time-weighted return and S&P 500 comparison calculations.
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict

from ..utils.prices import get_price_from_yfinance
from .holdings import calculate_holdings_quantities_only


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
    """
    if not historical_results or len(historical_results) < 2:
        return historical_results
    
    sp500_symbol = "^GSPC"
    
    # Build list of cost basis changes with dates
    cost_basis_changes = calculate_cost_basis_changes(df, price_cache)
    
    # Track S&P 500 shares over time using FIFO for sells
    sp500_lots = []  # Each lot: {"shares": float, "price": float}
    
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
            sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache, None, None)
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
        sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache, None, None)
        cost_basis_changes.append({
            "date": prev_date,
            "delta": pending_delta,
            "sp500_price": sp500_price
        })
    
    return cost_basis_changes


def calculate_cumulative_invested(df: pd.DataFrame, up_to_date: datetime) -> float:
    """
    Calculate total cost basis (money invested in current holdings) up to a given date.
    
    Uses simplified FIFO to estimate cost basis of remaining positions.
    """
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    # Filter to transactions up to the date
    period_df = df[df["Date"] <= up_to_date].copy()
    
    if period_df.empty:
        return 0.0
    
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
        if date != last_date:
            if last_date is not None and cost_basis_history:
                pass  # Keep the last entry for the previous date
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


def calculate_historical_holdings(df: pd.DataFrame, price_cache: dict, 
                                   snapshot_dates: list = None,
                                   include_cash: pd.DataFrame = None,
                                   unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
                                   split_cache: Optional[Dict[str, Dict[str, float]]] = None) -> Tuple[pd.DataFrame, list]:
    """
    Calculate holdings at specific points in time with per-account breakdown.
    Uses historical prices for accurate point-in-time valuations.
    
    Returns tuple of (summary_df, results_list) showing portfolio value over time.
    """
    if df.empty:
        return pd.DataFrame(), []
    
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    
    today = datetime.now()
    
    # Default: monthly snapshots (end of each month) from 2019 onwards
    if snapshot_dates is None:
        min_year = max(df["Date"].min().year, 2019)
        
        snapshot_dates = []
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
                    pass
        
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
            price, _ = get_price_from_yfinance(symbol, snapshot_date_str, price_cache, unavailable_ticker_cache, split_cache)
            
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
        if include_cash is not None and not include_cash.empty:
            for _, cash_row in include_cash.iterrows():
                cash_account = cash_row["Account"]
                if cash_account in accounts:
                    # Get cash transactions up to this date from original df
                    cash_txns = df[(df["Account"] == cash_account) & (df["Date"] <= snapshot_date)]
                    if not cash_txns.empty:
                        deposits = cash_txns[cash_txns["Action"] == "Buy"]["Amount"].sum()
                        withdrawals = cash_txns[cash_txns["Action"] == "Sell"]["Amount"].abs().sum()
                        interest = cash_txns[cash_txns["Action"] == "Interest"]["Amount"].sum()
                        cash_balance = deposits - withdrawals + interest
                        
                        if cash_balance > 0:
                            account_values[cash_account] = account_values.get(cash_account, 0) + cash_balance
                            total_value += cash_balance
        
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
    sp500_symbol = "^GSPC"
    for result in results:
        date_str = result["Date"]
        sp500_price, _ = get_price_from_yfinance(sp500_symbol, date_str, price_cache, unavailable_ticker_cache, split_cache)
        result["SP500"] = round(sp500_price, 2) if sp500_price else None
    
    # Calculate Time-Weighted Return
    print("Calculating time-weighted return...")
    results = calculate_time_weighted_return(results, df)
    
    # Calculate "What If S&P 500"
    print("Calculating 'What If S&P 500' comparison...")
    results = calculate_what_if_sp500(results, df, price_cache)
    
    # Create summary DataFrame with account columns
    if results:
        summary_df = pd.DataFrame(results)
    else:
        summary_df = pd.DataFrame()
    
    return summary_df, results
