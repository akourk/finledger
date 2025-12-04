"""
Cost Basis Calculator Module
============================
Calculates cost basis using FIFO (First In, First Out) method.
Tracks realized and unrealized gains/losses.
"""

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd

from ..utils.prices import get_price_changes, get_price_from_yfinance

logger = logging.getLogger(__name__)


def get_transaction_effect(row: pd.Series) -> Tuple[Optional[str], float]:
    """
    Determine how a transaction affects cost basis.

    Args:
        row: Transaction row from DataFrame

    Returns:
        Tuple of (effect_type, quantity) where effect_type is:
        - 'buy': adds to position
        - 'sell': removes from position
        - 'merger_receive': receives shares with inherited cost basis
        - 'merger_remove': removes shares (saves cost basis for transfer)
        - 'merger_cash': cash from merger (realizes gain/loss)
        - 'cash_in_lieu': cash for fractional shares
        - None: no effect on cost basis
    """
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
        # Transfer handling for cost basis - must match calculate_holdings logic
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
            return "merger_receive", abs(qty)
        else:
            return "merger_remove", 0
    elif action == "MergerCash":
        return "merger_cash", 0
    elif action == "CashInLieu":
        return "cash_in_lieu", 0
    else:
        return None, 0


def get_action_order(row: pd.Series) -> int:
    """
    Determine action order for same-day transactions.
    Lower number = processed first.

    Args:
        row: Transaction row from DataFrame

    Returns:
        Integer order value (0-4)
    """
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


def calculate_cost_basis(
    df: pd.DataFrame,
    price_cache: Dict[str, Dict[str, float]],
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Calculate cost basis for all holdings using FIFO (First In, First Out) method.

    For each Account/Symbol combination, tracks:
    - Total cost basis (sum of purchase costs)
    - Average cost per share
    - Realized gains/losses from sales
    - Unrealized gains/losses for current holdings

    Args:
        df: DataFrame with transaction data
        price_cache: Nested dict of {symbol: {date: price}}
        unavailable_ticker_cache: Optional cache of tickers without historical data

    Returns:
        Tuple of (holdings_df, realized_gains_df, tax_lots_df)
    """
    if df.empty:
        logger.warning("Empty DataFrame passed to calculate_cost_basis")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    logger.info("Calculating cost basis using FIFO method")

    df = df.copy()

    try:
        df["ActionOrder"] = df.apply(get_action_order, axis=1)
    except Exception as e:
        logger.exception("Error determining action order")
        raise ValueError(f"Failed to determine action order: {e}")
    # Sort order: Account, Date, ActionOrder, Symbol
    df = df.sort_values(["Account", "Date", "ActionOrder", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])

    # Track lots for FIFO processing
    # Structure: {(account, symbol): [{"date": date, "qty": qty, "price": price, "cost": cost, ...}, ...]}
    lots = {}

    # Track realized gains
    realized_gains = []

    # Track pending mergers (cost basis to transfer)
    pending_mergers = {}

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

            lots[key].append(
                {
                    "date": date,
                    "qty": qty,
                    "price": price if price > 0 else (cost / qty if qty > 0 else 0),
                    "cost": cost,
                    "remaining_qty": qty,
                    "remaining_cost": cost,
                }
            )

        elif effect == "sell" and qty > 0:
            # FIFO: sell from oldest lots first
            qty_to_sell = qty
            proceeds = amount - fee
            cost_basis_sold = 0
            oldest_lot_date = None  # Track oldest lot date for holding period

            while qty_to_sell > 0 and lots[key]:
                lot = lots[key][0]

                # Track the oldest lot date (first lot sold determines holding period for FIFO)
                if oldest_lot_date is None:
                    oldest_lot_date = lot["date"]

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

            # Calculate holding period
            holding_period = "Short-term"
            if oldest_lot_date:
                try:
                    sale_date = (
                        datetime.strptime(date, "%Y-%m-%d")
                        if isinstance(date, str)
                        else date
                    )
                    purchase_date = (
                        datetime.strptime(oldest_lot_date, "%Y-%m-%d")
                        if isinstance(oldest_lot_date, str)
                        else oldest_lot_date
                    )
                    days_held = (sale_date - purchase_date).days
                    if days_held > 365:
                        holding_period = "Long-term"
                except (ValueError, TypeError):
                    pass  # Default to short-term if date parsing fails

            # Record realized gain/loss
            gain = proceeds - cost_basis_sold
            realized_gains.append(
                {
                    "Date": date,
                    "Account": account,
                    "Symbol": symbol,
                    "Quantity": qty,
                    "Proceeds": round(proceeds, 2),
                    "CostBasis": round(cost_basis_sold, 2),
                    "RealizedGain": round(gain, 2),
                    "HoldingPeriod": holding_period,
                }
            )

        elif effect == "merger_remove":
            # Shares removed in merger - save cost basis for transfer or cash proceeds
            if lots[key]:
                total_qty = sum(lot["remaining_qty"] for lot in lots[key])
                total_cost = sum(lot["remaining_cost"] for lot in lots[key])
                pending_mergers[key] = {"cost_basis": total_cost, "qty": total_qty, "date": date}
                lots[key] = []  # Remove all lots

        elif effect == "merger_cash":
            # Cash received from merger - realize gain/loss
            if key in pending_mergers:
                cost_basis = pending_mergers[key]["cost_basis"]
                qty_sold = pending_mergers[key]["qty"]
                del pending_mergers[key]
            else:
                cost_basis = 0
                qty_sold = 0

            gain = amount - cost_basis
            realized_gains.append(
                {
                    "Date": date,
                    "Account": account,
                    "Symbol": symbol,
                    "Quantity": qty_sold,
                    "Proceeds": round(amount, 2),
                    "CostBasis": round(cost_basis, 2),
                    "RealizedGain": round(gain, 2),
                    "HoldingPeriod": "Long-term",  # Mergers typically involve long-held positions
                }
            )

        elif effect == "merger_receive":
            # Shares received in stock-for-stock merger
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
            lots[key].append(
                {
                    "date": inherited_date,
                    "qty": qty,
                    "price": cost / qty if qty > 0 else 0,
                    "cost": cost,
                    "remaining_qty": qty,
                    "remaining_cost": cost,
                }
            )

        elif effect == "cash_in_lieu":
            # Cash for fractional shares - small sale with gain/loss
            if key in pending_mergers:
                del pending_mergers[key]

            if amount > 0:
                realized_gains.append(
                    {
                        "Date": date,
                        "Account": account,
                        "Symbol": symbol,
                        "Quantity": 0,  # Fractional
                        "Proceeds": round(amount, 2),
                        "CostBasis": 0,
                        "RealizedGain": round(amount, 2),
                        "HoldingPeriod": "Short-term",  # Fractional shares, assume short-term
                    }
                )

    # Build current holdings with cost basis
    holdings_with_basis = []

    # Get current prices
    today = datetime.now().strftime("%Y-%m-%d")

    # Get unique symbols for price change calculation
    unique_symbols = list(
        set(
            symbol
            for (account, symbol) in lots.keys()
            if sum(lot["remaining_qty"] for lot in lots[(account, symbol)]) >= 0.0001
        )
    )

    # Fetch 7d and 30d price changes for all symbols
    price_changes = get_price_changes(
        unique_symbols, price_cache, unavailable_ticker_cache, split_cache
    )

    for (account, symbol), symbol_lots in lots.items():
        if not symbol_lots:
            continue

        total_qty = sum(lot["remaining_qty"] for lot in symbol_lots)
        total_cost = sum(lot["remaining_cost"] for lot in symbol_lots)

        if total_qty < 0.0001:
            continue

        avg_cost = total_cost / total_qty if total_qty > 0 else 0

        # Get current price
        current_price, _ = get_price_from_yfinance(
            symbol, today, price_cache, unavailable_ticker_cache, split_cache
        )
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

        holdings_with_basis.append(
            {
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
                "NumLots": len(symbol_lots),
            }
        )

    holdings_df = pd.DataFrame(holdings_with_basis)
    if not holdings_df.empty:
        holdings_df = holdings_df.sort_values("CurrentValue", ascending=False).reset_index(
            drop=True
        )

    # Store realized gains for reporting
    realized_df = pd.DataFrame(realized_gains)

    # Build tax lots detail for remaining positions
    tax_lots_detail = []
    today_dt = datetime.now()

    for (account, symbol), symbol_lots in lots.items():
        for lot in symbol_lots:
            if lot["remaining_qty"] < 0.0001:
                continue

            purchase_date = datetime.strptime(lot["date"], "%Y-%m-%d")
            holding_days = (today_dt - purchase_date).days
            is_long_term = holding_days > 365

            # Get current price for unrealized calculation
            current_price, _ = get_price_from_yfinance(
                symbol,
                today_dt.strftime("%Y-%m-%d"),
                price_cache,
                unavailable_ticker_cache,
                split_cache,
            )
            current_price = current_price or 0

            current_value = lot["remaining_qty"] * current_price
            unrealized_gain = current_value - lot["remaining_cost"]

            tax_lots_detail.append(
                {
                    "Account": account,
                    "Symbol": symbol,
                    "PurchaseDate": lot["date"],
                    "Quantity": round(lot["remaining_qty"], 6),
                    "CostBasis": round(lot["remaining_cost"], 2),
                    "CostPerShare": (
                        round(lot["remaining_cost"] / lot["remaining_qty"], 4)
                        if lot["remaining_qty"] > 0
                        else 0
                    ),
                    "CurrentPrice": round(current_price, 4),
                    "CurrentValue": round(current_value, 2),
                    "UnrealizedGain": round(unrealized_gain, 2),
                    "HoldingDays": holding_days,
                    "HoldingPeriod": "Long-term" if is_long_term else "Short-term",
                }
            )

    tax_lots_df = pd.DataFrame(tax_lots_detail)
    if not tax_lots_df.empty:
        tax_lots_df = tax_lots_df.sort_values(["Account", "Symbol", "PurchaseDate"]).reset_index(
            drop=True
        )

    return holdings_df, realized_df, tax_lots_df


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

    df["ActionOrder"] = df.apply(get_action_order, axis=1)
    # Sort chronologically with action order
    df = df.sort_values(["Account", "Date", "ActionOrder", "Symbol"]).reset_index(drop=True)
    df = df.drop(columns=["ActionOrder"])

    # Track lots for FIFO processing
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

            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})

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
                pending_mergers[key] = {"cost_basis": total_cost, "qty": total_qty, "date": date}
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
            lots[key].append({"remaining_qty": qty, "remaining_cost": cost})

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
