"""
Income Calculator Module
========================
Calculates income from dividends, interest, staking rewards, and stock lending.
"""

import pandas as pd


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
    income_summary = (
        income_df.groupby(["Account", "Symbol", "Year", "Action"])
        .agg({"Amount": "sum", "Quantity": "sum", "Date": "count"})  # Count of transactions
        .reset_index()
    )

    income_summary = income_summary.rename(
        columns={"Amount": "TotalAmount", "Quantity": "TotalQuantity", "Date": "NumTransactions"}
    )

    income_summary["TotalAmount"] = income_summary["TotalAmount"].round(2)
    income_summary["TotalQuantity"] = income_summary["TotalQuantity"].round(6)

    return income_summary.sort_values(
        ["Year", "TotalAmount"], ascending=[False, False]
    ).reset_index(drop=True)


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
