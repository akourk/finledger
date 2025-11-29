"""
Performance Calculator Module
=============================
Calculates multi-period returns for assets, accounts, and portfolio.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ..utils.prices import PERFORMANCE_PERIODS, get_multi_period_returns, get_price_from_yfinance


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
) -> dict:
    """
    Generate a comprehensive performance report with all levels of analysis.

    Returns dict with:
    - portfolio: Portfolio-level performance
    - accounts: Account-level performance DataFrame
    - sectors: Sector-level performance DataFrame
    - assets: Asset-level performance DataFrame
    - periods: List of period names for reference
    """
    print("\n16. Calculating multi-period performance...")

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
