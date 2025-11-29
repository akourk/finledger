"""
Data Quality Module
==================
Functions for validating data quality and detecting potential issues.
"""

import logging
from datetime import datetime
from typing import Dict, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class QualityIssue:
    """Represents a data quality issue."""

    def __init__(self, severity: str, category: str, message: str, details: Dict = None):
        self.severity = severity  # 'error', 'warning', 'info'
        self.category = category  # 'holdings', 'transactions', 'pricing', etc.
        self.message = message
        self.details = details or {}

    def __str__(self):
        icon = "🔴" if self.severity == "error" else "🟡" if self.severity == "warning" else "🔵"
        return f"{icon} [{self.category}] {self.message}"


def check_negative_holdings(holdings_df: pd.DataFrame) -> List[QualityIssue]:
    """
    Check for negative holdings which usually indicate an error.

    Args:
        holdings_df: DataFrame with current holdings

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if holdings_df.empty:
        return issues

    negative = holdings_df[holdings_df["Quantity"] < -0.0001]  # Allow for rounding

    for _, row in negative.iterrows():
        issues.append(
            QualityIssue(
                severity="error",
                category="holdings",
                message=f"Negative holding: {row['Account']} - {row['Symbol']} = {row['Quantity']:.4f} shares",
                details={
                    "account": row["Account"],
                    "symbol": row["Symbol"],
                    "quantity": row["Quantity"],
                },
            )
        )

    return issues


def check_holdings_without_buys(df: pd.DataFrame, holdings_df: pd.DataFrame) -> List[QualityIssue]:
    """
    Check for holdings that exist without any buy transactions.

    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if holdings_df.empty:
        return issues

    for _, holding in holdings_df.iterrows():
        if holding["Quantity"] <= 0:
            continue

        # Look for buy-type transactions for this holding
        buys = df[
            (df["Account"] == holding["Account"])
            & (df["Symbol"] == holding["Symbol"])
            & (df["Action"].isin(["Buy", "Transfer", "SpinOff", "Staking"]))
        ]

        if buys.empty:
            issues.append(
                QualityIssue(
                    severity="warning",
                    category="holdings",
                    message=f"Holding without buy transaction: {holding['Account']} - {holding['Symbol']} ({holding['Quantity']:.2f} shares)",
                    details={
                        "account": holding["Account"],
                        "symbol": holding["Symbol"],
                        "quantity": holding["Quantity"],
                    },
                )
            )

    return issues


def check_large_price_discrepancies(
    holdings_df: pd.DataFrame, threshold_pct: float = 1000
) -> List[QualityIssue]:
    """
    Check for unusually large price changes that might indicate data errors.

    Args:
        holdings_df: DataFrame with holdings including cost basis
        threshold_pct: Threshold percentage for flagging (default 1000%)

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if holdings_df.empty or "CostBasis" not in holdings_df.columns:
        return issues

    for _, row in holdings_df.iterrows():
        if row["Quantity"] <= 0:
            continue

        if pd.notna(row.get("CurrentPrice")) and row.get("CurrentPrice", 0) > 0:
            avg_cost = row["CostBasis"] / row["Quantity"] if row["Quantity"] > 0 else 0

            if avg_cost > 0:
                price_change_pct = abs((row["CurrentPrice"] - avg_cost) / avg_cost) * 100

                if price_change_pct > threshold_pct:
                    issues.append(
                        QualityIssue(
                            severity="warning",
                            category="pricing",
                            message=f"Large price change: {row['Symbol']} - {price_change_pct:.1f}% (${avg_cost:.2f} → ${row['CurrentPrice']:.2f})",
                            details={
                                "symbol": row["Symbol"],
                                "avg_cost": avg_cost,
                                "current_price": row["CurrentPrice"],
                                "change_pct": price_change_pct,
                            },
                        )
                    )

    return issues


def check_missing_cost_basis(holdings_df: pd.DataFrame) -> List[QualityIssue]:
    """
    Check for holdings that are missing cost basis information.

    Args:
        holdings_df: DataFrame with holdings

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if holdings_df.empty or "CostBasis" not in holdings_df.columns:
        return issues

    missing = holdings_df[
        (holdings_df["Quantity"] > 0)
        & ((holdings_df["CostBasis"].isna()) | (holdings_df["CostBasis"] == 0))
    ]

    for _, row in missing.iterrows():
        issues.append(
            QualityIssue(
                severity="warning",
                category="cost_basis",
                message=f"Missing cost basis: {row['Account']} - {row['Symbol']} ({row['Quantity']:.2f} shares)",
                details={
                    "account": row["Account"],
                    "symbol": row["Symbol"],
                    "quantity": row["Quantity"],
                },
            )
        )

    return issues


def check_missing_cost_basis_filtered(
    df: pd.DataFrame, holdings_df: pd.DataFrame
) -> List[QualityIssue]:
    """
    Check for holdings missing cost basis, excluding corporate action derivatives.

    Corporate actions like spin-offs that create warrants, CVRs, or other
    derivatives typically have $0 cost basis by design. This check filters
    those out to reduce noise while still catching real issues.

    Args:
        df: Transaction DataFrame (to check for spin-offs)
        holdings_df: DataFrame with holdings

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if holdings_df.empty or "CostBasis" not in holdings_df.columns:
        return issues

    # Patterns that indicate corporate action derivatives (typically $0 basis)
    CORPORATE_ACTION_SUFFIXES = ["+", "^", "W", ".WS", ".RT"]

    # Get symbols that came from SpinOff transactions
    spinoff_symbols = set()
    if not df.empty and "Action" in df.columns:
        spinoffs = df[df["Action"] == "SpinOff"]
        for _, row in spinoffs.iterrows():
            spinoff_symbols.add((row["Account"], row["Symbol"]))

    # Find holdings with missing cost basis
    missing = holdings_df[
        (holdings_df["Quantity"] > 0)
        & ((holdings_df["CostBasis"].isna()) | (holdings_df["CostBasis"] == 0))
    ]

    for _, row in missing.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]

        # Skip if this is a known corporate action derivative
        is_derivative = any(symbol.endswith(suffix) for suffix in CORPORATE_ACTION_SUFFIXES)
        is_spinoff = (account, symbol) in spinoff_symbols

        if is_derivative or is_spinoff:
            # These are handled by check_corporate_action_basis instead
            continue

        issues.append(
            QualityIssue(
                severity="warning",
                category="cost_basis",
                message=f"Missing cost basis: {account} - {symbol} ({row['Quantity']:.2f} shares)",
                details={"account": account, "symbol": symbol, "quantity": row["Quantity"]},
            )
        )

    return issues


def check_orphaned_sells(df: pd.DataFrame) -> List[QualityIssue]:
    """
    Check for sell transactions without corresponding buy transactions.

    This check now accounts for:
    - Transfers (positive = in, negative = out)
    - SpinOffs (shares received)
    - Staking rewards (shares received)
    - MergerOut (shares received in stock-for-stock mergers)

    Args:
        df: Transaction DataFrame

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if df.empty:
        return issues

    # Actions that add shares
    BUY_ACTIONS = {"Buy", "SpinOff", "Staking"}

    # Group by account and symbol
    for (account, symbol), group in df.groupby(["Account", "Symbol"]):
        if symbol == "" or symbol == "USD":
            continue

        # Calculate cumulative quantity considering all transaction types
        group = group.sort_values("Date")
        cumulative = 0

        for _, row in group.iterrows():
            action = row["Action"]
            qty = abs(row["Quantity"]) if pd.notna(row["Quantity"]) else 0

            if action in BUY_ACTIONS:
                cumulative += qty
            elif action == "Sell":
                cumulative -= qty
            elif action == "Transfer":
                # Transfer direction based on quantity sign or amount
                if row["Quantity"] >= 0:
                    cumulative += qty
                else:
                    cumulative -= qty
            elif action == "MergerOut":
                # MergerOut with qty > 0 = receiving shares
                if row["Quantity"] > 0:
                    cumulative += qty
                # MergerOut with qty = 0 means shares removed (handled separately)
            # Note: MergerCash, Dividend, etc. don't affect share count

            # Check for deficit after sells
            if action == "Sell" and cumulative < -0.01:
                issues.append(
                    QualityIssue(
                        severity="error",
                        category="transactions",
                        message=f"Sell without sufficient buy: {account} - {symbol} on {row['Date']} (deficit: {abs(cumulative):.4f} shares)",
                        details={
                            "account": account,
                            "symbol": symbol,
                            "date": row["Date"],
                            "deficit": abs(cumulative),
                        },
                    )
                )
                break  # Only report once per symbol

    return issues


def check_duplicate_detection_quality(df: pd.DataFrame, dupes_removed: int) -> List[QualityIssue]:
    """
    Report on duplicate detection effectiveness.

    Args:
        df: Transaction DataFrame
        dupes_removed: Number of duplicates removed

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if dupes_removed > 0:
        dupe_pct = (dupes_removed / (len(df) + dupes_removed)) * 100

        if dupe_pct > 10:
            issues.append(
                QualityIssue(
                    severity="info",
                    category="data_quality",
                    message=f"High duplicate rate: {dupes_removed} duplicates removed ({dupe_pct:.1f}% of original data)",
                    details={"duplicates": dupes_removed, "percentage": dupe_pct},
                )
            )
        else:
            issues.append(
                QualityIssue(
                    severity="info",
                    category="data_quality",
                    message=f"Duplicates removed: {dupes_removed} transactions ({dupe_pct:.1f}%)",
                    details={"duplicates": dupes_removed, "percentage": dupe_pct},
                )
            )

    return issues


def check_corporate_action_basis(df: pd.DataFrame, holdings_df: pd.DataFrame) -> List[QualityIssue]:
    """
    Check for corporate actions that may need cost basis review.

    Identifies:
    - Spin-offs without allocated cost basis
    - Mergers that may need basis adjustment
    - Holdings from corporate actions (warrants, CVRs, etc.)

    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame

    Returns:
        List of QualityIssue objects
    """
    issues = []

    if df.empty:
        return issues

    # Known spin-off patterns (warrants, CVRs typically have no allocated basis)
    SPINOFF_PATTERNS = {
        "+": "warrant",  # GME+ = warrant
        "^": "cvr",  # FREQ^ = contingent value right
        "W": "warrant",  # Some use W suffix
    }

    # Find spin-off transactions
    spinoffs = df[df["Action"] == "SpinOff"]

    for _, row in spinoffs.iterrows():
        symbol = row["Symbol"]
        account = row["Account"]
        date = row["Date"]
        qty = row["Quantity"]

        # Check if this is a known no-basis spinoff type
        is_special = False
        special_type = None
        for suffix, stype in SPINOFF_PATTERNS.items():
            if symbol.endswith(suffix):
                is_special = True
                special_type = stype
                break

        if is_special:
            # Warrants/CVRs typically have $0 basis - INFO level
            issues.append(
                QualityIssue(
                    severity="info",
                    category="corporate_action",
                    message=f"SpinOff ({special_type}): {account} - {symbol} ({qty:.4f} shares) - typically $0 cost basis",
                    details={
                        "account": account,
                        "symbol": symbol,
                        "quantity": qty,
                        "date": date,
                        "type": special_type,
                        "note": "Warrants and CVRs from corporate actions typically have $0 allocated cost basis",
                    },
                )
            )
        else:
            # Regular spin-off - may need basis allocation
            # Check if there's a holding with $0 basis
            if holdings_df is not None and not holdings_df.empty:
                holding = holdings_df[
                    (holdings_df["Account"] == account) & (holdings_df["Symbol"] == symbol)
                ]

                if not holding.empty and holding.iloc[0].get("CostBasis", 0) == 0:
                    issues.append(
                        QualityIssue(
                            severity="info",
                            category="corporate_action",
                            message=f"SpinOff: {account} - {symbol} may need cost basis allocation from parent stock",
                            details={
                                "account": account,
                                "symbol": symbol,
                                "quantity": qty,
                                "date": date,
                                "note": "IRS requires allocating parent's basis based on FMV on first trading day",
                            },
                        )
                    )

    # Find merger transactions
    mergers = df[df["Action"].isin(["MergerOut", "MergerCash"])]

    # Group by date to identify complete merger events
    merger_dates = mergers.groupby(["Account", "Symbol", "Date"]).size().reset_index()

    for _, row in merger_dates.iterrows():
        account = row["Account"]
        symbol = row["Symbol"]
        date = row["Date"]

        # Get the merger transactions for this event
        event = mergers[
            (mergers["Account"] == account)
            & (mergers["Symbol"] == symbol)
            & (mergers["Date"] == date)
        ]

        has_stock = "MergerOut" in event["Action"].values and any(
            event[event["Action"] == "MergerOut"]["Quantity"] > 0
        )
        has_cash = "MergerCash" in event["Action"].values

        if has_stock and has_cash:
            merger_type = "mixed (stock + cash)"
        elif has_stock:
            merger_type = "stock-for-stock"
        else:
            merger_type = "cash"

        issues.append(
            QualityIssue(
                severity="info",
                category="corporate_action",
                message=f"Merger ({merger_type}): {account} - {symbol} on {date}",
                details={
                    "account": account,
                    "symbol": symbol,
                    "date": date,
                    "merger_type": merger_type,
                },
            )
        )

    return issues


def run_quality_checks(
    df: pd.DataFrame, holdings_df: pd.DataFrame = None, dupes_removed: int = 0
) -> Tuple[List[QualityIssue], Dict[str, int]]:
    """
    Run all quality checks and return issues.

    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame (optional)
        dupes_removed: Number of duplicates removed

    Returns:
        Tuple of (list of issues, summary dict)
    """
    logger.info("Running data quality checks")

    all_issues = []

    # Run all checks
    if holdings_df is not None and not holdings_df.empty:
        all_issues.extend(check_negative_holdings(holdings_df))
        all_issues.extend(check_holdings_without_buys(df, holdings_df))
        all_issues.extend(check_large_price_discrepancies(holdings_df))
        all_issues.extend(check_missing_cost_basis_filtered(df, holdings_df))
        all_issues.extend(check_corporate_action_basis(df, holdings_df))

    all_issues.extend(check_orphaned_sells(df))
    all_issues.extend(check_duplicate_detection_quality(df, dupes_removed))

    # Summarize by severity
    summary = {
        "errors": sum(1 for i in all_issues if i.severity == "error"),
        "warnings": sum(1 for i in all_issues if i.severity == "warning"),
        "info": sum(1 for i in all_issues if i.severity == "info"),
        "total": len(all_issues),
    }

    logger.info(
        f"Quality checks complete: {summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info"
    )

    return all_issues, summary


def print_quality_report(issues: List[QualityIssue], summary: Dict[str, int]) -> None:
    """
    Print a formatted quality report.

    Args:
        issues: List of QualityIssue objects
        summary: Summary dict with counts
    """
    print("\n" + "=" * 60)
    print("Data Quality Report")
    print("=" * 60)

    if summary["total"] == 0:
        print("\n✅ No data quality issues found!")
        return

    # Print errors first
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(f"\n❌ ERRORS ({len(errors)})")
        for issue in errors:
            print(f"  {issue}")

    # Then warnings
    warnings = [i for i in issues if i.severity == "warning"]
    if warnings:
        print(f"\n⚠️  WARNINGS ({len(warnings)})")
        for issue in warnings[:10]:  # Limit to first 10
            print(f"  {issue}")
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more warnings")

    # Then info
    info = [i for i in issues if i.severity == "info"]
    if info:
        print(f"\nℹ️  INFO ({len(info)})")
        for issue in info:
            print(f"  {issue}")

    # Summary
    print(f"\n{'='*60}")
    print(
        f"Total Issues: {summary['total']} ({summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info)"
    )
    print("=" * 60)
