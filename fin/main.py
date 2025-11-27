"""
Transaction Data Aggregator - Main Entry Point
===============================================
Reads transaction history files from multiple financial sources,
normalizes them to a unified format, and outputs a master CSV.

Supported Sources:
- Schwab (Rollover IRA, Roth Contributory IRA)
- Robinhood
- Coinbase
- Vanguard SF 401K
- USAA Victory Capital
- Voya Atos 401K
- Apple Savings
- Custom Input (pre-normalized format)
"""

import pandas as pd
from datetime import datetime

from .config import (
    DATA_INPUT_PATH, DATA_OUTPUT_PATH, UNIFIED_COLUMNS,
)
from .utils.cache import (
    load_price_cache, save_price_cache,
    load_split_cache, save_split_cache,
    load_sector_cache, save_sector_cache,
)
from .utils.prices import (
    convert_price_based_symbols, adjust_for_splits,
    get_sectors_for_holdings,
)
from .parsers import detect_source, PARSER_REGISTRY, merge_accounts
from .parsers.base import normalize_amounts, create_empty_dataframe, standardize_symbols
from .calculators import (
    calculate_holdings, calculate_holdings_quantities_only, add_running_balances,
    calculate_cost_basis, add_running_cost_basis,
    calculate_income, calculate_income_by_year,
    calculate_cash_balances,
    calculate_historical_holdings, calculate_portfolio_cost_basis_history,
)
from .reports import (
    generate_account_summary, generate_portfolio_summary,
    print_portfolio_summary,
    export_master_csv, export_holdings_csv, export_holdings_detail_csv,
    export_realized_gains_csv, export_tax_lots_csv,
    export_income_report_csv, export_income_by_year_csv,
    export_account_summary_csv, export_historical_holdings_csv,
    export_cash_balances_csv,
    export_portfolio_summary_js, export_dataframe_js,
    generate_retirement_summary, export_retirement_data_js,
)


def process_file(file_path) -> pd.DataFrame:
    """Process a single input file and return normalized DataFrame."""
    print(f"  Processing: {file_path.name}")
    
    # Detect source
    source = detect_source(file_path)
    if source is None:
        print(f"    ⚠ Unknown source - skipping")
        return None
    
    print(f"    ✓ Detected source: {source}")
    
    # Get parser
    parser = PARSER_REGISTRY.get(source)
    if parser is None:
        print(f"    ⚠ No parser available for source: {source}")
        return None
    
    # Parse file
    try:
        df = parser(file_path)
        # Add source filename for deduplication across files
        df["_SourceFile"] = file_path.name
        print(f"    ✓ Parsed {len(df)} transactions")
        return df
    except Exception as e:
        print(f"    ✗ Error parsing file: {e}")
        return None


def process_all_files() -> pd.DataFrame:
    """Process all CSV files in the input directory."""
    print("\n" + "="*60)
    print("Transaction Data Aggregator")
    print("="*60 + "\n")
    
    all_transactions = []
    files_processed = 0
    files_skipped = 0
    
    # Load caches
    price_cache = load_price_cache()
    split_cache = load_split_cache()
    
    # Process each CSV file
    for file_path in sorted(DATA_INPUT_PATH.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() == ".csv":
            result = process_file(file_path)
            if result is not None and len(result) > 0:
                all_transactions.append(result)
                files_processed += 1
            else:
                files_skipped += 1
    
    # Combine all transactions
    if all_transactions:
        master_df = pd.concat(all_transactions, ignore_index=True)
        
        # Remove duplicate transactions (from overlapping date ranges in input files)
        dupe_cols = ["Date", "Account", "Symbol", "Action", "Quantity", "Amount"]
        initial_count = len(master_df)
        
        # Step 1: Within each file, number occurrences of each transaction
        master_df["_OccurrenceInFile"] = master_df.groupby(
            ["_SourceFile"] + dupe_cols
        ).cumcount()
        
        # Step 2: Now dedupe across files - keep first occurrence
        master_df = master_df.drop_duplicates(
            subset=dupe_cols + ["_OccurrenceInFile"], 
            keep="first"
        )
        
        # Remove helper columns
        master_df = master_df.drop(columns=["_SourceFile", "_OccurrenceInFile"])
        
        dupes_removed = initial_count - len(master_df)
        if dupes_removed > 0:
            print(f"Removed {dupes_removed} duplicate transactions (from overlapping files)")
        
        # Normalize amounts to be consistently positive
        master_df = normalize_amounts(master_df)
        
        # Standardize fund names to ticker symbols
        master_df = standardize_symbols(master_df)
        
        # Merge accounts that have been transferred/rolled over
        master_df = merge_accounts(master_df)
        
        # Convert price-based symbols (e.g., VANG TR II 2055 → VFFVX)
        master_df = convert_price_based_symbols(master_df, price_cache)
        
        # Adjust historical transactions for stock splits
        master_df = adjust_for_splits(master_df, split_cache)
        
        # Reload price cache to capture any additions from parsers
        final_price_cache = load_price_cache()
        for symbol, dates in price_cache.items():
            if symbol not in final_price_cache:
                final_price_cache[symbol] = {}
            final_price_cache[symbol].update(dates)
        
        # Save updated caches
        save_price_cache(final_price_cache)
        save_split_cache(split_cache)
        
        # Sort by date (newest first)
        master_df = master_df.sort_values("Date", ascending=False).reset_index(drop=True)
        
        print(f"\n" + "-"*60)
        print(f"Summary:")
        print(f"  Files processed: {files_processed}")
        print(f"  Files skipped:   {files_skipped}")
        print(f"  Total transactions: {len(master_df)}")
        print(f"-"*60)
        
        return master_df
    else:
        print("\nNo transactions found!")
        return create_empty_dataframe()


def generate_all_reports(master_df: pd.DataFrame, price_cache: dict):
    """
    Generate all reports from the master transaction data.
    
    Reports generated:
    1. holdings.csv - Simple current holdings (Account, Symbol, Quantity, Price, Value)
    2. holdings_detail.csv - Detailed holdings with cost basis and returns
    3. tax_lots.csv - Individual tax lots with holding periods
    4. realized_gains.csv - All realized gains/losses from sales
    5. income_report.csv - Detailed income by symbol and year
    6. income_by_year.csv - Income summary by year for tax purposes
    7. cash_balances.csv - Cash account balances (e.g., Apple Savings)
    8. account_summary.csv - Summary metrics per account
    9. historical_holdings.csv - Portfolio value over time
    10. portfolio_summary.json - Overall portfolio metrics
    """
    print("\n" + "="*60)
    print("Generating Reports")
    print("="*60)
    
    # 1. Simple holdings (for backwards compatibility)
    print("\n1. Calculating current holdings...")
    simple_holdings = calculate_holdings(master_df, price_cache)
    export_holdings_csv(simple_holdings)
    
    # 2. Detailed holdings with cost basis and tax lots
    print("\n2. Calculating cost basis (FIFO method) and tax lots...")
    holdings_detail, realized_gains, tax_lots = calculate_cost_basis(master_df, price_cache)
    
    # Add sector information to holdings
    if not holdings_detail.empty:
        print("\n2b. Adding sector information...")
        sector_cache = load_sector_cache()
        sectors = get_sectors_for_holdings(holdings_detail, sector_cache)
        holdings_detail["Sector"] = holdings_detail["Symbol"].map(sectors)
        export_holdings_detail_csv(holdings_detail)
    
    # 3. Tax lots
    print("\n3. Exporting tax lots...")
    if not tax_lots.empty:
        export_tax_lots_csv(tax_lots)
        long_term = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]
        short_term = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]
        print(f"   Long-term lots: {len(long_term)}, Short-term lots: {len(short_term)}")
    else:
        print("   No tax lots to export.")
    
    # 4. Realized gains
    print("\n4. Exporting realized gains...")
    if not realized_gains.empty:
        export_realized_gains_csv(realized_gains)
        print(f"   Total realized gains: ${realized_gains['RealizedGain'].sum():,.2f}")
    else:
        print("   No realized gains to export.")
    
    # 5. Income report
    print("\n5. Calculating income...")
    income_report = calculate_income(master_df)
    if not income_report.empty:
        export_income_report_csv(income_report)
        print(f"   Total income transactions: {len(income_report)}")
    else:
        print("   No income to report.")
    
    # 6. Income by year
    print("\n6. Summarizing income by year...")
    income_by_year = calculate_income_by_year(master_df)
    if not income_by_year.empty:
        export_income_by_year_csv(income_by_year)
    
    # 7. Cash balances (for USD-only accounts like Apple Savings)
    print("\n7. Calculating cash account balances...")
    cash_balances = calculate_cash_balances(master_df)
    if not cash_balances.empty:
        export_cash_balances_csv(cash_balances)
        for _, row in cash_balances.iterrows():
            print(f"   {row['Account']}: Balance=${row['CurrentBalance']:,.2f}, Interest=${row['TotalInterest']:,.2f} ({row['ReturnPct']:.2f}%)")
    else:
        print("   No cash-only accounts found.")
    
    # 8. Account summary
    print("\n8. Generating account summary...")
    account_summary = generate_account_summary(holdings_detail, income_report, realized_gains, cash_balances)
    if not account_summary.empty:
        export_account_summary_csv(account_summary)
    
    # 9. Historical holdings (with per-account breakdown)
    print("\n9. Calculating historical holdings...")
    historical_summary, historical_details = calculate_historical_holdings(
        master_df, price_cache, include_cash=cash_balances
    )
    if not historical_summary.empty:
        export_historical_holdings_csv(historical_summary)
    
    # 10. Portfolio summary
    print("\n10. Generating portfolio summary...")
    portfolio_summary = generate_portfolio_summary(
        holdings_detail, account_summary, historical_summary, income_by_year, cash_balances
    )
    portfolio_summary["GeneratedAt"] = datetime.now().isoformat()
    
    # Add tax lot summary to portfolio summary
    if not tax_lots.empty:
        long_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["CurrentValue"].sum()
        short_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]["CurrentValue"].sum()
        long_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["UnrealizedGain"].sum()
        short_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]["UnrealizedGain"].sum()
        portfolio_summary["LongTermValue"] = round(long_term_value, 2)
        portfolio_summary["ShortTermValue"] = round(short_term_value, 2)
        portfolio_summary["LongTermUnrealizedGain"] = round(long_term_gain, 2)
        portfolio_summary["ShortTermUnrealizedGain"] = round(short_term_gain, 2)
    
    export_portfolio_summary_js(portfolio_summary)
    
    # Export additional data as JS for the dashboard
    print("\n11. Exporting JavaScript data files for dashboard...")
    if not holdings_detail.empty:
        export_dataframe_js(holdings_detail, "holdingsDetailData", "holdings_detail.js")
    if not account_summary.empty:
        export_dataframe_js(account_summary, "accountSummaryData", "account_summary.js")
    if not income_by_year.empty:
        export_dataframe_js(income_by_year, "incomeByYearData", "income_by_year.js")
    if not historical_summary.empty:
        export_dataframe_js(historical_summary, "historicalHoldingsData", "historical_holdings.js")
    if not realized_gains.empty:
        export_dataframe_js(realized_gains, "realizedGainsData", "realized_gains.js")
    if not cash_balances.empty:
        export_dataframe_js(cash_balances, "cashBalancesData", "cash_balances.js")
    
    # Export master transactions with running balances for transaction detail view
    print("\n12. Adding running balances to transactions...")
    master_with_balances = add_running_balances(master_df, simple_holdings)
    
    # Add running cost basis using FIFO
    print("\n13. Adding running cost basis to transactions...")
    master_with_balances = add_running_cost_basis(master_with_balances)
    export_dataframe_js(master_with_balances, "masterTransactionsData", "master_transactions.js")
    
    # Calculate and export portfolio cost basis history
    print("\n14. Calculating portfolio cost basis history...")
    portfolio_cost_basis = calculate_portfolio_cost_basis_history(master_df)
    if not portfolio_cost_basis.empty:
        export_dataframe_js(portfolio_cost_basis, "portfolioCostBasisData", "portfolio_cost_basis.js")
        print(f"   Exported {len(portfolio_cost_basis)} cost basis data points")
    
    # 15. Retirement data (salary, bonuses, contribution tracking)
    print("\n15. Generating retirement summary...")
    retirement_summary = generate_retirement_summary(master_df, holdings_detail)
    export_retirement_data_js(retirement_summary)
    
    # Print summary to console
    print_portfolio_summary(portfolio_summary)
    
    return {
        "simple_holdings": simple_holdings,
        "holdings_detail": holdings_detail,
        "tax_lots": tax_lots,
        "realized_gains": realized_gains,
        "income_report": income_report,
        "income_by_year": income_by_year,
        "cash_balances": cash_balances,
        "account_summary": account_summary,
        "historical_summary": historical_summary,
        "portfolio_summary": portfolio_summary
    }


def main():
    """Main entry point."""
    # Process all input files
    master_df = process_all_files()
    
    # Export to CSV
    if len(master_df) > 0:
        export_master_csv(master_df)
        
        # Reload price cache after processing (parsers may have added entries)
        price_cache = load_price_cache()
        
        # Generate all reports
        reports = generate_all_reports(master_df, price_cache)
        
        # Save price cache (may have new prices from report generation)
        save_price_cache(price_cache)
        
        # Show sample of output
        print("\nSample of master transactions (first 10 rows):")
        print(master_df.head(10).to_string())
    else:
        print("\nNo transactions to export.")


if __name__ == "__main__":
    main()
