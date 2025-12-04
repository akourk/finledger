"""
Performance Calculator Module
=============================
Calculates multi-period returns for assets, accounts, and portfolio.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

from ..utils.prices import PERFORMANCE_PERIODS, get_multi_period_returns


def calculate_cash_account_performance(
    cash_balances_df: pd.DataFrame,
    master_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate multi-period returns for cash accounts (like Apple Savings).

    Cash accounts earn interest, so we calculate returns based on
    interest earned over each time period relative to the average balance.

    Args:
        cash_balances_df: Cash balances DataFrame from calculate_cash_balances()
        master_df: Full transaction DataFrame to get interest history

    Returns:
        DataFrame with cash account performance matching asset performance structure
    """
    if cash_balances_df is None or cash_balances_df.empty:
        return pd.DataFrame()

    if master_df is None or master_df.empty:
        return pd.DataFrame()

    results = []
    today = datetime.now().date()

    for _, cash_row in cash_balances_df.iterrows():
        account = cash_row["Account"]
        current_balance = cash_row["CurrentBalance"]
        total_interest = cash_row["TotalInterest"]
        net_contributions = cash_row["NetContributions"]

        # Get all transactions for this account
        account_txns = master_df[master_df["Account"] == account].copy()
        if account_txns.empty:
            continue

        # Ensure Date is datetime
        account_txns["Date"] = pd.to_datetime(account_txns["Date"])

        # Get interest transactions
        interest_txns = account_txns[account_txns["Action"] == "Interest"].copy()

        result = {
            "Account": account,
            "Symbol": "USD",
            "Sector": "Cash",
            "CurrentValue": current_balance,
            "CostBasis": net_contributions,
            "TotalReturn": cash_row["ReturnPct"],
        }

        # Calculate returns for each period based on interest earned
        for period_name, period_days in PERFORMANCE_PERIODS.items():
            period_start = today - timedelta(days=period_days)

            # Get interest earned in this period
            period_interest = interest_txns[
                interest_txns["Date"].dt.date >= period_start
            ]["Amount"].sum()

            # Estimate average balance during period (simplified: use current balance)
            # For a more accurate calculation, we'd need to track daily balances
            avg_balance = current_balance - (period_interest / 2)  # Rough approximation

            if avg_balance > 0 and period_interest > 0:
                # Annualize the return for comparison with other assets
                period_return = (period_interest / avg_balance) * 100
                result[f"Return_{period_name.upper()}"] = round(period_return, 2)
            else:
                result[f"Return_{period_name.upper()}"] = 0.0

        results.append(result)

    return pd.DataFrame(results)


def calculate_asset_performance(
    holdings_df: pd.DataFrame,
    price_cache: dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> pd.DataFrame:
    """
    Calculate multi-period returns for each holding.

    Returns DataFrame with columns for each period's return.
    """
    if holdings_df.empty:
        return pd.DataFrame()

    # Get unique symbols
    symbols = holdings_df["Symbol"].unique().tolist()

    # Get multi-period returns for all symbols
    returns_data = get_multi_period_returns(
        symbols, price_cache, unavailable_ticker_cache, split_cache
    )

    # Build result dataframe
    results = []

    for _, row in holdings_df.iterrows():
        symbol = row["Symbol"]
        symbol_returns = returns_data.get(symbol, {})

        result = {
            "Account": row["Account"],
            "Symbol": symbol,
            "Sector": row.get("Sector", "Other"),
            "CurrentValue": row.get("CurrentValue", row.get("current_value", 0)),
            "CostBasis": row.get("CostBasis", row.get("cost_basis", 0)),
            "TotalReturn": row.get("UnrealizedGainPct", row.get("unrealized_gain_pct", 0)),
        }

        # Add each period's return
        for period_name in PERFORMANCE_PERIODS.keys():
            result[f"Return_{period_name.upper()}"] = symbol_returns.get(f"return_{period_name}")

        results.append(result)

    df = pd.DataFrame(results)
    return df


def calculate_account_performance(
    holdings_df: pd.DataFrame,
    price_cache: dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> pd.DataFrame:
    """
    Calculate multi-period returns for each account.

    Aggregates holdings by account and calculates value-weighted returns.
    """
    if holdings_df.empty:
        return pd.DataFrame()

    # Get asset-level performance first
    asset_perf = calculate_asset_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )

    if asset_perf.empty:
        return pd.DataFrame()

    # Group by account and calculate value-weighted returns
    accounts = (
        asset_perf.groupby("Account").agg({"CurrentValue": "sum", "CostBasis": "sum"}).reset_index()
    )

    results = []

    for _, account_row in accounts.iterrows():
        account = account_row["Account"]
        account_holdings = asset_perf[asset_perf["Account"] == account]
        total_value = account_row["CurrentValue"]

        result = {
            "Account": account,
            "CurrentValue": total_value,
            "CostBasis": account_row["CostBasis"],
            "TotalReturn": (
                ((total_value - account_row["CostBasis"]) / account_row["CostBasis"] * 100)
                if account_row["CostBasis"] > 0
                else 0
            ),
            "NumPositions": len(account_holdings),
        }

        # Calculate value-weighted returns for each period
        for period_name in PERFORMANCE_PERIODS.keys():
            col = f"Return_{period_name.upper()}"
            if col in account_holdings.columns:
                # Filter out None values and calculate weighted average
                valid_holdings = account_holdings[account_holdings[col].notna()]
                if not valid_holdings.empty and valid_holdings["CurrentValue"].sum() > 0:
                    weights = valid_holdings["CurrentValue"] / valid_holdings["CurrentValue"].sum()
                    weighted_return = (valid_holdings[col] * weights).sum()
                    result[col] = round(weighted_return, 2)
                else:
                    result[col] = None
            else:
                result[col] = None

        results.append(result)

    df = pd.DataFrame(results)
    return df.sort_values("CurrentValue", ascending=False).reset_index(drop=True)


def calculate_portfolio_performance(
    holdings_df: pd.DataFrame,
    price_cache: dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> dict:
    """
    Calculate multi-period returns for the total portfolio.

    Returns dict with period returns and summary metrics.
    """
    if holdings_df.empty:
        return {}

    # Get asset-level performance
    asset_perf = calculate_asset_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )

    if asset_perf.empty:
        return {}

    total_value = asset_perf["CurrentValue"].sum()
    total_cost = asset_perf["CostBasis"].sum()

    result = {
        "TotalValue": round(total_value, 2),
        "CostBasis": round(total_cost, 2),
        "TotalReturn": round(
            ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0, 2
        ),
        "NumPositions": len(asset_perf),
        "NumAccounts": asset_perf["Account"].nunique(),
    }

    # Calculate value-weighted returns for each period
    for period_name in PERFORMANCE_PERIODS.keys():
        col = f"Return_{period_name.upper()}"
        if col in asset_perf.columns:
            valid_holdings = asset_perf[asset_perf[col].notna()]
            if not valid_holdings.empty and valid_holdings["CurrentValue"].sum() > 0:
                weights = valid_holdings["CurrentValue"] / valid_holdings["CurrentValue"].sum()
                weighted_return = (valid_holdings[col] * weights).sum()
                result[col] = round(weighted_return, 2)
            else:
                result[col] = None
        else:
            result[col] = None

    return result


def calculate_sector_performance(
    holdings_df: pd.DataFrame,
    price_cache: dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> pd.DataFrame:
    """
    Calculate multi-period returns grouped by sector.
    """
    if holdings_df.empty:
        return pd.DataFrame()

    # Get asset-level performance
    asset_perf = calculate_asset_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )

    if asset_perf.empty:
        return pd.DataFrame()

    # Group by sector
    sectors = (
        asset_perf.groupby("Sector").agg({"CurrentValue": "sum", "CostBasis": "sum"}).reset_index()
    )

    results = []

    for _, sector_row in sectors.iterrows():
        sector = sector_row["Sector"]
        sector_holdings = asset_perf[asset_perf["Sector"] == sector]
        total_value = sector_row["CurrentValue"]

        result = {
            "Sector": sector,
            "CurrentValue": total_value,
            "CostBasis": sector_row["CostBasis"],
            "TotalReturn": (
                ((total_value - sector_row["CostBasis"]) / sector_row["CostBasis"] * 100)
                if sector_row["CostBasis"] > 0
                else 0
            ),
            "NumPositions": len(sector_holdings),
        }

        # Calculate value-weighted returns for each period
        for period_name in PERFORMANCE_PERIODS.keys():
            col = f"Return_{period_name.upper()}"
            if col in sector_holdings.columns:
                valid_holdings = sector_holdings[sector_holdings[col].notna()]
                if not valid_holdings.empty and valid_holdings["CurrentValue"].sum() > 0:
                    weights = valid_holdings["CurrentValue"] / valid_holdings["CurrentValue"].sum()
                    weighted_return = (valid_holdings[col] * weights).sum()
                    result[col] = round(weighted_return, 2)
                else:
                    result[col] = None
            else:
                result[col] = None

        results.append(result)

    df = pd.DataFrame(results)
    return df.sort_values("CurrentValue", ascending=False).reset_index(drop=True)


def generate_performance_report(
    holdings_df: pd.DataFrame,
    price_cache: dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
    cash_balances_df: Optional[pd.DataFrame] = None,
    master_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Generate a comprehensive performance report with all levels of analysis.

    Args:
        holdings_df: Holdings detail DataFrame
        price_cache: Price cache dictionary
        unavailable_ticker_cache: Cache of unavailable tickers
        split_cache: Stock split cache
        cash_balances_df: Cash balances DataFrame (for accounts like Apple Savings)
        master_df: Full transaction DataFrame (needed for cash interest calculations)

    Returns dict with:
    - portfolio: Portfolio-level performance
    - accounts: Account-level performance DataFrame
    - sectors: Sector-level performance DataFrame
    - assets: Asset-level performance DataFrame
    - periods: List of period names for reference
    """
    print("\n16. Calculating multi-period performance...")

    # Calculate performance for investment holdings
    portfolio_perf = calculate_portfolio_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )
    account_perf = calculate_account_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )
    sector_perf = calculate_sector_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )
    asset_perf = calculate_asset_performance(
        holdings_df, price_cache, unavailable_ticker_cache, split_cache
    )

    # Calculate performance for cash accounts (like Apple Savings)
    cash_perf = calculate_cash_account_performance(cash_balances_df, master_df)

    # Merge cash accounts into results
    if not cash_perf.empty:
        # Add cash to assets
        asset_perf = pd.concat([asset_perf, cash_perf], ignore_index=True)

        # Add cash accounts to account performance
        cash_account_perf = []
        for _, row in cash_perf.iterrows():
            account_result = {
                "Account": row["Account"],
                "CurrentValue": row["CurrentValue"],
                "CostBasis": row["CostBasis"],
                "TotalReturn": row["TotalReturn"],
                "NumPositions": 1,
            }
            # Copy period returns
            for period_name in PERFORMANCE_PERIODS.keys():
                col = f"Return_{period_name.upper()}"
                account_result[col] = row.get(col)
            cash_account_perf.append(account_result)

        if cash_account_perf:
            cash_account_df = pd.DataFrame(cash_account_perf)
            account_perf = pd.concat([account_perf, cash_account_df], ignore_index=True)
            account_perf = account_perf.sort_values("CurrentValue", ascending=False).reset_index(
                drop=True
            )

        # Add cash to sector performance (as "Cash" sector)
        cash_sector = {
            "Sector": "Cash",
            "CurrentValue": cash_perf["CurrentValue"].sum(),
            "CostBasis": cash_perf["CostBasis"].sum(),
            "TotalReturn": cash_perf["TotalReturn"].mean() if len(cash_perf) > 0 else 0,
            "NumPositions": len(cash_perf),
        }
        # Calculate weighted returns for cash sector
        total_cash_value = cash_perf["CurrentValue"].sum()
        for period_name in PERFORMANCE_PERIODS.keys():
            col = f"Return_{period_name.upper()}"
            if col in cash_perf.columns and total_cash_value > 0:
                weights = cash_perf["CurrentValue"] / total_cash_value
                weighted_return = (cash_perf[col].fillna(0) * weights).sum()
                cash_sector[col] = round(weighted_return, 2)
            else:
                cash_sector[col] = 0.0
        sector_perf = pd.concat(
            [sector_perf, pd.DataFrame([cash_sector])], ignore_index=True
        )
        sector_perf = sector_perf.sort_values("CurrentValue", ascending=False).reset_index(
            drop=True
        )

        # Update portfolio totals to include cash
        portfolio_perf["TotalValue"] = round(
            portfolio_perf.get("TotalValue", 0) + cash_perf["CurrentValue"].sum(), 2
        )
        portfolio_perf["CostBasis"] = round(
            portfolio_perf.get("CostBasis", 0) + cash_perf["CostBasis"].sum(), 2
        )
        portfolio_perf["NumPositions"] = portfolio_perf.get("NumPositions", 0) + len(cash_perf)
        portfolio_perf["NumAccounts"] = portfolio_perf.get("NumAccounts", 0) + len(cash_perf)

        # Recalculate total return with cash included
        if portfolio_perf["CostBasis"] > 0:
            portfolio_perf["TotalReturn"] = round(
                (
                    (portfolio_perf["TotalValue"] - portfolio_perf["CostBasis"])
                    / portfolio_perf["CostBasis"]
                    * 100
                ),
                2,
            )

        # Recalculate weighted period returns including cash
        all_assets_for_weighting = asset_perf.copy()
        total_value = all_assets_for_weighting["CurrentValue"].sum()
        for period_name in PERFORMANCE_PERIODS.keys():
            col = f"Return_{period_name.upper()}"
            if col in all_assets_for_weighting.columns and total_value > 0:
                valid = all_assets_for_weighting[all_assets_for_weighting[col].notna()]
                if not valid.empty and valid["CurrentValue"].sum() > 0:
                    weights = valid["CurrentValue"] / valid["CurrentValue"].sum()
                    weighted_return = (valid[col] * weights).sum()
                    portfolio_perf[col] = round(weighted_return, 2)

        print(f"   Including {len(cash_perf)} cash account(s) in performance")

    periods = list(PERFORMANCE_PERIODS.keys())

    print(
        f"   Calculated performance for {len(asset_perf)} assets across {len(periods)} time periods"
    )

    return {
        "portfolio": portfolio_perf,
        "accounts": account_perf,
        "sectors": sector_perf,
        "assets": asset_perf,
        "periods": periods,
    }
