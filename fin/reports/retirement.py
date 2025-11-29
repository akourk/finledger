"""
Retirement Planning Module
==========================
Handles retirement data parsing, projections, and contribution tracking.
"""

import json
from datetime import datetime

import pandas as pd

from ..config import DASHBOARD_DATA_PATH, DATA_INPUT_PATH


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
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df


def calculate_retirement_projections(
    retirement_df: pd.DataFrame, holdings_detail_df: pd.DataFrame = None, current_age: int = 30
) -> dict:
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
        "contribution_limits_ira": 7000,  # 2025 limit
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
            {"date": row["Date"].strftime("%Y-%m-%d"), "amount": row["Amount"], "note": row["Note"]}
            for _, row in salary_df.iterrows()
        ]

    # Extract bonus history
    bonus_df = retirement_df[retirement_df["Type"] == "Bonus History"].copy()
    bonus_df = bonus_df.sort_values("Date")

    if not bonus_df.empty:
        result["bonus_history"] = [
            {"date": row["Date"].strftime("%Y-%m-%d"), "amount": row["Amount"], "note": row["Note"]}
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


def calculate_contribution_tracking(
    master_df: pd.DataFrame, retirement_df: pd.DataFrame = None
) -> dict:
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
    roth_ira_by_year = {}

    if not master_df.empty:
        df = master_df.copy()
        df["Year"] = pd.to_datetime(df["Date"]).dt.year

        # Find Roth IRA accounts (exclude Roth 401k)
        roth_ira_mask = (
            df["Account"].str.lower().str.contains("roth")
            & df["Account"].str.lower().str.contains("ira")
            & ~df["Account"].str.lower().str.contains("401")
        )
        roth_ira_txns = df[roth_ira_mask].copy()

        if not roth_ira_txns.empty:
            # Method 1: Look for transactions with "CONTRIBUTION" in the Note
            contrib_note_mask = roth_ira_txns["Note"].str.upper().str.contains(
                "CONTRIBUTION", na=False
            ) & (roth_ira_txns["Amount"] > 0)

            # Method 2: Look for cash transfers (Transfer with no symbol = bank transfer in)
            cash_transfer_mask = (
                (roth_ira_txns["Action"] == "Transfer")
                & (roth_ira_txns["Amount"] > 0)
                & (roth_ira_txns["Symbol"].isna() | (roth_ira_txns["Symbol"] == ""))
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
        result["roth_ira_history"].append(
            {
                "year": year,
                "amount": amount,
                "note": f"Roth IRA - {'maxed' if amount >= limit else 'partial'}",
            }
        )
        result["roth_ira_total_contributed"] += amount

        if year == current_year:
            result["roth_ira"] = amount

    if master_df.empty:
        return result

    # 401k contribution limits by year (for reference)
    # 2019: 19000, 2020: 19500, 2021: 19500, 2022: 20500, 2023: 22500, 2024: 23000, 2025: 23500

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
        roth_accounts = df[
            df["Account"].str.lower().str.contains("roth")
            & df["Account"].str.lower().str.contains("ira")
        ]
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
        "effective_tax_rate": round(effective_tax_rate * 100, 1),
    }


def generate_retirement_summary(
    master_df: pd.DataFrame, holdings_detail_df: pd.DataFrame = None
) -> dict:
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
        birthday_row = personal_info[
            personal_info["Note"].str.contains("Birthday", case=False, na=False)
        ]
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
