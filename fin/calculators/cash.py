"""
Cash Balance Calculator Module
==============================
Calculates cash balances for USD-only accounts like savings accounts.
"""

import pandas as pd


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
