"""
Summary Reports Module
======================
Generates account and portfolio summary reports.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd


def calculate_daily_portfolio_change(
    holdings_df: pd.DataFrame,
    price_cache: Dict,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[float, float]:
    """
    Calculate the portfolio's change from yesterday to today.

    Returns:
        Tuple of (dollar_change, percent_change)
    """
    from fin.utils.prices import get_price_from_yfinance

    if holdings_df.empty:
        return 0.0, 0.0

    today = datetime.now()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    total_change = 0.0
    yesterday_value = 0.0

    for _, row in holdings_df.iterrows():
        symbol = row["Symbol"]
        quantity = row["Quantity"]
        current_price = row.get("CurrentPrice", 0)

        if quantity == 0 or current_price == 0:
            continue

        # Get yesterday's price
        yesterday_price, _ = get_price_from_yfinance(
            symbol, yesterday_str, price_cache, unavailable_ticker_cache, split_cache
        )

        if yesterday_price is None or yesterday_price == 0:
            # Fall back to current price (no change)
            yesterday_price = current_price

        # Calculate change for this position
        position_change = quantity * (current_price - yesterday_price)
        total_change += position_change
        yesterday_value += quantity * yesterday_price

    # Calculate percentage change
    percent_change = (total_change / yesterday_value * 100) if yesterday_value > 0 else 0.0

    return round(total_change, 2), round(percent_change, 2)


def generate_account_summary(
    holdings_df: pd.DataFrame,
    income_df: pd.DataFrame,
    realized_gains_df: pd.DataFrame,
    cash_balances_df: pd.DataFrame = None,
) -> pd.DataFrame:
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
    account_summary = (
        holdings_df.groupby("Account")
        .agg({"CurrentValue": "sum", "CostBasis": "sum", "UnrealizedGain": "sum"})
        .reset_index()
    )

    account_summary = account_summary.rename(
        columns={
            "CurrentValue": "TotalValue",
            "CostBasis": "TotalCostBasis",
            "UnrealizedGain": "TotalUnrealizedGain",
        }
    )

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
        all_time_realized = all_time_realized.rename(
            columns={"RealizedGain": "AllTimeRealizedGain"}
        )

        # YTD realized gains
        ytd_realized = (
            realized_gains_df[realized_gains_df["Year"] == current_year]
            .groupby("Account")["RealizedGain"]
            .sum()
            .reset_index()
        )
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
        ytd_income = (
            income_df[income_df["Year"] == current_year]
            .groupby("Account")["TotalAmount"]
            .sum()
            .reset_index()
        )
        ytd_income = ytd_income.rename(columns={"TotalAmount": "YTDIncome"})

        account_summary = account_summary.merge(all_time_income, on="Account", how="left")
        account_summary = account_summary.merge(ytd_income, on="Account", how="left")
    else:
        account_summary["AllTimeIncome"] = 0
        account_summary["YTDIncome"] = 0

    # Fill NaN with 0
    account_summary = account_summary.fillna(0)

    # Round values
    for col in [
        "TotalValue",
        "TotalCostBasis",
        "TotalUnrealizedGain",
        "AllTimeRealizedGain",
        "YTDRealizedGain",
        "AllTimeIncome",
        "YTDIncome",
    ]:
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

            cash_accounts.append(
                {
                    "Account": row["Account"],
                    "TotalValue": round(row["CurrentBalance"], 2),
                    "TotalCostBasis": round(row["NetContributions"], 2),
                    "TotalUnrealizedGain": round(row["TotalInterest"], 2),  # Interest is the "gain"
                    "UnrealizedGainPct": round(row["ReturnPct"], 2),
                    "AllTimeRealizedGain": 0.0,
                    "YTDRealizedGain": 0.0,
                    "AllTimeIncome": round(row["TotalInterest"], 2),  # Interest is income
                    "YTDIncome": round(ytd_interest, 2),
                }
            )

        if cash_accounts:
            cash_df = pd.DataFrame(cash_accounts)
            account_summary = pd.concat([account_summary, cash_df], ignore_index=True)

    # Sort by total value
    account_summary = account_summary.sort_values("TotalValue", ascending=False).reset_index(
        drop=True
    )

    return account_summary


def generate_portfolio_summary(
    holdings_df: pd.DataFrame,
    account_summary_df: pd.DataFrame,
    historical_df: pd.DataFrame,
    income_by_year_df: pd.DataFrame,
    cash_balances_df: pd.DataFrame = None,
    price_cache: Dict = None,
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> dict:
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
            (
                summary["TotalUnrealizedGain"] / summary["TotalCostBasis"] * 100
                if summary["TotalCostBasis"] > 0
                else 0
            ),
            2,
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
        summary["CashAccounts"] = cash_balances_df[
            ["Account", "CurrentBalance", "TotalInterest"]
        ].to_dict("records")
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
        summary["TotalUnrealizedGain"] + summary["AllTimeRealizedGain"] + summary["AllTimeIncome"],
        2,
    )
    summary["TotalReturnPct"] = round(
        (
            summary["TotalReturn"] / summary["TotalCostBasis"] * 100
            if summary["TotalCostBasis"] > 0
            else 0
        ),
        2,
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

        summary["AccountAllocation"] = (
            {acc: round(val / total * 100, 2) for acc, val in account_allocation.items()}
            if total > 0
            else {}
        )

    # Top holdings
    if not holdings_df.empty:
        top_holdings = holdings_df.nlargest(10, "CurrentValue")[
            ["Symbol", "CurrentValue", "UnrealizedGainPct"]
        ].to_dict("records")
        summary["TopHoldings"] = top_holdings

    # Daily change (if price_cache provided)
    if price_cache is not None and not holdings_df.empty:
        daily_change, daily_change_pct = calculate_daily_portfolio_change(
            holdings_df, price_cache, unavailable_ticker_cache, split_cache
        )
        summary["DailyChange"] = daily_change
        summary["DailyChangePct"] = daily_change_pct
    else:
        summary["DailyChange"] = 0
        summary["DailyChangePct"] = 0

    return summary


def print_holdings_summary(holdings: pd.DataFrame):
    """Print a summary of holdings by account."""
    if holdings.empty:
        print("\nNo holdings to display.")
        return

    # Check which columns are available
    value_col = "CurrentValue" if "CurrentValue" in holdings.columns else "Value"
    price_col = "CurrentPrice"

    print("\n" + "=" * 60)
    print("Holdings Summary")
    print("=" * 60)

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
        print(
            f"  {row['Symbol']:12} {row['Quantity']:>12.4f} @ ${row[price_col]:>10.2f} = ${row[value_col]:>12,.2f}  ({row['Account']})"
        )


def print_portfolio_summary(summary: dict):
    """Print the full portfolio summary to console."""
    print("\n" + "=" * 80)
    print("PORTFOLIO SUMMARY")
    print("=" * 80)

    print(f"\n{'─'*40}")
    print("CURRENT HOLDINGS")
    print(f"{'─'*40}")
    print(f"  Total Portfolio Value:      ${summary.get('TotalValue', 0):>15,.2f}")

    # Show breakdown if we have both investments and cash
    if summary.get("CashValue", 0) > 0:
        print(f"    ├─ Investments:           ${summary.get('InvestmentValue', 0):>15,.2f}")
        print(f"    └─ Cash/Savings:          ${summary.get('CashValue', 0):>15,.2f}")

    print(f"  Total Cost Basis:           ${summary.get('TotalCostBasis', 0):>15,.2f}")
    print(
        f"  Unrealized Gain/Loss:       ${summary.get('TotalUnrealizedGain', 0):>15,.2f} ({summary.get('UnrealizedGainPct', 0):>+.2f}%)"
    )
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
    print(
        f"  Total Return (All-Time):    ${summary.get('TotalReturn', 0):>15,.2f} ({summary.get('TotalReturnPct', 0):>+.2f}%)"
    )

    if "AllTimeGrowth" in summary:
        print(
            f"  Portfolio Growth:           ${summary.get('AllTimeGrowth', 0):>15,.2f} ({summary.get('AllTimeGrowthPct', 0):>+.2f}%)"
        )

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

    print("\n" + "=" * 80)
