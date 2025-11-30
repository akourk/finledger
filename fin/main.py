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

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

    # Fallback: simple progress indicator
    class tqdm:
        def __init__(self, iterable=None, desc=None, total=None, disable=False, **kwargs):
            self.iterable = iterable
            self.desc = desc
            self.total = total or (len(iterable) if iterable else 0)
            self.disable = disable
            self.n = 0

        def __iter__(self):
            for item in self.iterable:
                yield item
                self.update()

        def update(self, n=1):
            self.n += n
            if not self.disable and self.n % 5 == 0:
                print(f"  Progress: {self.n}/{self.total}")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from .calculators import (  # noqa: E402
    add_running_balances,
    add_running_cost_basis,
    calculate_cash_balances,
    calculate_cost_basis,
    calculate_historical_holdings,
    calculate_holdings,
    calculate_income,
    calculate_income_by_year,
    calculate_portfolio_cost_basis_history,
)
from .config import (  # noqa: E402
    DATA_INPUT_PATH,
    DATA_OUTPUT_PATH,
)
from .corporate_actions import (  # noqa: E402
    generate_corporate_actions_report,
)
from .parsers import PARSER_REGISTRY, detect_source, merge_accounts  # noqa: E402
from .parsers.base import (  # noqa: E402
    create_empty_dataframe,
    normalize_amounts,
    standardize_symbols,
    validate_dataframe,
)
from .quality import print_quality_report, run_quality_checks  # noqa: E402
from .reports import (  # noqa: E402
    export_account_summary_csv,
    export_cash_balances_csv,
    export_dataframe_js,
    export_historical_holdings_csv,
    export_holdings_csv,
    export_holdings_detail_csv,
    export_income_by_year_csv,
    export_income_report_csv,
    export_master_csv,
    export_portfolio_summary_js,
    export_realized_gains_csv,
    export_retirement_data_js,
    export_tax_lots_csv,
    generate_account_summary,
    generate_portfolio_summary,
    generate_retirement_summary,
    print_portfolio_summary,
)
from .utils.cache import (  # noqa: E402
    load_price_cache,
    load_sector_cache,
    load_split_cache,
    load_unavailable_ticker_cache,
    save_price_cache,
    save_split_cache,
    save_unavailable_ticker_cache,
)
from .utils.prices import (  # noqa: E402
    adjust_for_splits,
    convert_price_based_symbols,
    get_sectors_for_holdings,
)


def _process_files_sequential(files: List[Path], results: List[pd.DataFrame]) -> None:
    """
    Process files sequentially with progress bar.

    Args:
        files: List of file paths to process
        results: List to append successful results to
    """
    for file_path in tqdm(files, desc="Processing files", unit="file", disable=not TQDM_AVAILABLE):
        try:
            result = process_file(file_path)
            if result is not None and len(result) > 0:
                results.append(result)
        except Exception as e:
            logger.exception(f"Unexpected error processing {file_path.name}")
            print(f"  ✗ Unexpected error with {file_path.name}: {e}")


def _process_files_parallel(
    files: List[Path], results: List[pd.DataFrame], max_workers: int = 4
) -> None:
    """
    Process files in parallel with progress bar.

    Args:
        files: List of file paths to process
        results: List to append successful results to
        max_workers: Maximum number of parallel workers
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs
        future_to_file = {executor.submit(process_file, f): f for f in files}

        # Process completed jobs with progress bar
        with tqdm(
            total=len(files), desc="Processing files", unit="file", disable=not TQDM_AVAILABLE
        ) as pbar:
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    if result is not None and len(result) > 0:
                        results.append(result)
                except Exception as e:
                    logger.exception(f"Unexpected error processing {file_path.name}")
                    print(f"  ✗ Unexpected error with {file_path.name}: {e}")
                finally:
                    pbar.update(1)


def process_file(file_path: Path) -> Optional[pd.DataFrame]:
    """
    Process a single input file and return normalized DataFrame.

    Args:
        file_path: Path to CSV file to process

    Returns:
        DataFrame with normalized transactions, or None if processing failed
    """
    logger.info(f"Processing: {file_path.name}")
    print(f"  Processing: {file_path.name}")

    try:
        # Detect source
        source = detect_source(file_path)
        if source is None:
            logger.warning(f"Unknown source for file: {file_path.name}")
            print("    ⚠ Unknown source - skipping")
            return None

        logger.info(f"Detected source: {source} for {file_path.name}")
        print(f"    ✓ Detected source: {source}")

        # Get parser
        parser = PARSER_REGISTRY.get(source)
        if parser is None:
            logger.error(f"No parser available for source: {source}")
            print(f"    ⚠ No parser available for source: {source}")
            return None

        # Parse file
        df = parser(file_path)

        if df is None or df.empty:
            logger.warning(f"Parser returned empty DataFrame for {file_path.name}")
            print("    ⚠ No transactions found in file")
            return None

        # Validate DataFrame
        is_valid, errors = validate_dataframe(df)
        if not is_valid:
            logger.error(f"Validation failed for {file_path.name}: {errors}")
            print(f"    ✗ Validation errors: {', '.join(errors)}")
            return None

        # Add source filename for deduplication across files
        df["_SourceFile"] = file_path.name
        logger.info(f"Successfully parsed {len(df)} transactions from {file_path.name}")
        print(f"    ✓ Parsed {len(df)} transactions")
        return df

    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path.name} - {e}")
        print(f"    ✗ File not found: {e}")
        return None
    except pd.errors.ParserError as e:
        logger.error(f"CSV parsing error in {file_path.name}: {e}")
        print(f"    ✗ CSV parsing error: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error parsing {file_path.name}")
        print(f"    ✗ Error parsing file: {e}")
        return None


def process_all_files() -> pd.DataFrame:
    """
    Process all CSV files in the input directory.

    Returns:
        DataFrame containing all normalized transactions
    """
    logger.info("Starting transaction data aggregation")
    print("\n" + "=" * 60)
    print("Transaction Data Aggregator")
    print("=" * 60 + "\n")

    all_transactions = []
    files_processed = 0
    files_skipped = 0

    # Validate input directory exists
    if not DATA_INPUT_PATH.exists():
        logger.error(f"Input directory does not exist: {DATA_INPUT_PATH}")
        print(f"\n✗ Error: Input directory not found: {DATA_INPUT_PATH}")
        return create_empty_dataframe()

    # Load caches
    try:
        price_cache = load_price_cache()
        split_cache = load_split_cache()
        unavailable_ticker_cache = load_unavailable_ticker_cache()
        logger.info("Loaded caches successfully")

        # Validate price cache against split cache (invalidate prices if splits occurred)
        from .utils.cache import validate_price_cache_against_splits

        invalidated = validate_price_cache_against_splits(price_cache, split_cache)
        if invalidated > 0:
            print(f"\n⚠ Invalidated price cache for {invalidated} symbol(s) due to stock splits")

    except Exception as e:
        logger.error(f"Failed to load caches: {e}")
        print(f"\n✗ Error loading caches: {e}")
        price_cache = {}
        split_cache = {}
        unavailable_ticker_cache = {}

    # Process each CSV file
    csv_files = [f for f in DATA_INPUT_PATH.iterdir() if f.is_file() and f.suffix.lower() == ".csv"]

    if not csv_files:
        logger.warning(f"No CSV files found in {DATA_INPUT_PATH}")
        print(f"\n⚠ No CSV files found in {DATA_INPUT_PATH}")
        return create_empty_dataframe()

    logger.info(f"Found {len(csv_files)} CSV files to process")
    print(f"Found {len(csv_files)} CSV files to process\n")

    # Process files (use environment variable or default)
    use_parallel = getattr(process_all_files, "_parallel", True)
    max_workers = getattr(process_all_files, "_workers", 4)

    if use_parallel and len(csv_files) > 1:
        logger.info(f"Using parallel processing with {max_workers} workers")
        _process_files_parallel(sorted(csv_files), all_transactions, max_workers)
    else:
        logger.info("Using sequential processing")
        _process_files_sequential(sorted(csv_files), all_transactions)

    files_processed = len(all_transactions)
    files_skipped = len(csv_files) - files_processed

    # Combine all transactions
    if all_transactions:
        try:
            master_df = pd.concat(all_transactions, ignore_index=True)
            logger.info(
                f"Combined {len(master_df)} total transactions from {len(all_transactions)} files"
            )
        except Exception as e:
            logger.exception("Failed to combine transaction DataFrames")
            print(f"\n✗ Error combining transactions: {e}")
            return create_empty_dataframe()

        # Remove duplicate transactions (from overlapping date ranges in input files)
        dupe_cols = ["Date", "Account", "Symbol", "Action", "Quantity", "Amount"]
        initial_count = len(master_df)

        # Step 1: Within each file, number occurrences of each transaction
        master_df["_OccurrenceInFile"] = master_df.groupby(["_SourceFile"] + dupe_cols).cumcount()

        # Step 2: Now dedupe across files - keep first occurrence
        master_df = master_df.drop_duplicates(
            subset=dupe_cols + ["_OccurrenceInFile"], keep="first"
        )

        # Remove helper columns
        master_df = master_df.drop(columns=["_SourceFile", "_OccurrenceInFile"])

        dupes_removed = initial_count - len(master_df)
        # Store for quality checks
        master_df._dupes_removed = dupes_removed
        if dupes_removed > 0:
            logger.info(f"Removed {dupes_removed} duplicate transactions")
            print(f"Removed {dupes_removed} duplicate transactions (from overlapping files)")

        # Normalize amounts to be consistently positive
        try:
            master_df = normalize_amounts(master_df)
            logger.debug("Normalized transaction amounts")
        except Exception as e:
            logger.error(f"Error normalizing amounts: {e}")
            print(f"⚠ Warning: Error normalizing amounts: {e}")

        # Standardize fund names to ticker symbols
        try:
            master_df = standardize_symbols(master_df)
            logger.debug("Standardized ticker symbols")
        except Exception as e:
            logger.error(f"Error standardizing symbols: {e}")
            print(f"⚠ Warning: Error standardizing symbols: {e}")

        # Merge accounts that have been transferred/rolled over
        try:
            master_df = merge_accounts(master_df)
            logger.debug("Merged rolled-over accounts")
        except Exception as e:
            logger.error(f"Error merging accounts: {e}")
            print(f"⚠ Warning: Error merging accounts: {e}")

        # Convert price-based symbols (e.g., VANG TR II 2055 → VFFVX)
        try:
            master_df = convert_price_based_symbols(master_df, price_cache)
            logger.debug("Converted price-based symbols")
        except Exception as e:
            logger.error(f"Error converting price-based symbols: {e}")
            print(f"⚠ Warning: Error converting symbols: {e}")

        # Adjust historical transactions for stock splits
        try:
            master_df = adjust_for_splits(master_df, split_cache)
            logger.debug("Adjusted for stock splits")
        except Exception as e:
            logger.error(f"Error adjusting for splits: {e}")
            print(f"⚠ Warning: Error adjusting for splits: {e}")

        # Reload price cache to capture any additions from parsers
        final_price_cache = load_price_cache()
        for symbol, dates in price_cache.items():
            if symbol not in final_price_cache:
                final_price_cache[symbol] = {}
            final_price_cache[symbol].update(dates)

        # Save updated caches
        try:
            save_price_cache(final_price_cache)
            save_split_cache(split_cache)
            save_unavailable_ticker_cache(unavailable_ticker_cache)
            logger.info("Saved updated caches")
        except Exception as e:
            logger.error(f"Failed to save caches: {e}")
            print(f"⚠ Warning: Failed to save caches: {e}")

        # Sort by date (newest first)
        master_df = master_df.sort_values("Date", ascending=False).reset_index(drop=True)

        print("\n" + "-" * 60)
        print("Summary:")
        print(f"  Files processed: {files_processed}")
        print(f"  Files skipped:   {files_skipped}")
        print(f"  Total transactions: {len(master_df)}")
        print("-" * 60)

        logger.info(f"Processing complete: {files_processed} files, {len(master_df)} transactions")
        return master_df
    else:
        logger.warning("No transactions found in any files")
        print("\nNo transactions found!")
        return create_empty_dataframe()


def generate_all_reports(
    master_df: pd.DataFrame,
    price_cache: Dict[str, Dict[str, float]],
    unavailable_ticker_cache: Optional[Dict[str, Dict[str, str]]] = None,
    split_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
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

    Args:
        master_df: Master transaction DataFrame
        price_cache: Price cache dictionary
        unavailable_ticker_cache: Optional cache of tickers without historical data
    """
    logger.info("Starting report generation")
    print("\n" + "=" * 60)
    print("Generating Reports")
    print("=" * 60)

    if master_df.empty:
        logger.warning("Cannot generate reports from empty DataFrame")
        print("\n⚠ No data to generate reports")
        return {}

    # 1. Simple holdings (for backwards compatibility)
    print("\n1. Calculating current holdings...")
    try:
        simple_holdings = calculate_holdings(
            master_df, price_cache, unavailable_ticker_cache, split_cache
        )
        export_holdings_csv(simple_holdings)
        logger.info(f"Generated simple holdings report with {len(simple_holdings)} positions")
    except Exception as e:
        logger.exception("Failed to calculate holdings")
        print(f"✗ Error calculating holdings: {e}")
        simple_holdings = pd.DataFrame()

    # 2. Detailed holdings with cost basis and tax lots
    print("\n2. Calculating cost basis (FIFO method) and tax lots...")
    try:
        holdings_detail, realized_gains, tax_lots = calculate_cost_basis(
            master_df, price_cache, unavailable_ticker_cache, split_cache
        )
        logger.info(f"Calculated cost basis for {len(holdings_detail)} holdings")
    except Exception as e:
        logger.exception("Failed to calculate cost basis")
        print(f"✗ Error calculating cost basis: {e}")
        holdings_detail = pd.DataFrame()
        realized_gains = pd.DataFrame()
        tax_lots = pd.DataFrame()

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
            acct = row["Account"]
            bal = row["CurrentBalance"]
            interest = row["TotalInterest"]
            pct = row["ReturnPct"]
            print(f"   {acct}: Balance=${bal:,.2f}, Interest=${interest:,.2f} ({pct:.2f}%)")
    else:
        print("   No cash-only accounts found.")

    # 8. Account summary
    print("\n8. Generating account summary...")
    account_summary = generate_account_summary(
        holdings_detail, income_report, realized_gains, cash_balances
    )
    if not account_summary.empty:
        export_account_summary_csv(account_summary)

    # 9. Historical holdings (with per-account breakdown)
    print("\n9. Calculating historical holdings...")
    historical_summary, historical_details = calculate_historical_holdings(
        master_df,
        price_cache,
        include_cash=cash_balances,
        unavailable_ticker_cache=unavailable_ticker_cache,
        split_cache=split_cache,
    )

    # Calculate cost basis history and merge with historical holdings
    print("   Adding historical cost basis...")
    cost_basis_history = calculate_portfolio_cost_basis_history(master_df)
    if not cost_basis_history.empty and not historical_summary.empty:
        # Merge cost basis into historical summary
        # For each snapshot date, find the cost basis at or before that date
        cost_basis_history["Date"] = pd.to_datetime(cost_basis_history["Date"])
        historical_summary["Date"] = pd.to_datetime(historical_summary["Date"])

        # Create a function to get cost basis for each snapshot date
        def get_cost_basis_at_date(snapshot_date):
            prior = cost_basis_history[cost_basis_history["Date"] <= snapshot_date]
            if not prior.empty:
                return prior.iloc[-1]["TotalCostBasis"]
            return 0.0

        historical_summary["CostBasis"] = historical_summary["Date"].apply(get_cost_basis_at_date)
        historical_summary["Date"] = historical_summary["Date"].dt.strftime("%Y-%m-%d")

    if not historical_summary.empty:
        export_historical_holdings_csv(historical_summary)

    # 10. Portfolio summary
    print("\n10. Generating portfolio summary...")
    portfolio_summary = generate_portfolio_summary(
        holdings_detail,
        account_summary,
        historical_summary,
        income_by_year,
        cash_balances,
        price_cache,
        unavailable_ticker_cache,
        split_cache,
    )
    portfolio_summary["GeneratedAt"] = datetime.now().isoformat()

    # Add tax lot summary to portfolio summary
    if not tax_lots.empty:
        long_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["CurrentValue"].sum()
        short_term_value = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"]["CurrentValue"].sum()
        long_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Long-term"]["UnrealizedGain"].sum()
        short_term_gain = tax_lots[tax_lots["HoldingPeriod"] == "Short-term"][
            "UnrealizedGain"
        ].sum()
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
        export_dataframe_js(
            portfolio_cost_basis, "portfolioCostBasisData", "portfolio_cost_basis.js"
        )
        print(f"   Exported {len(portfolio_cost_basis)} cost basis data points")

    # 15. Retirement data (salary, bonuses, contribution tracking)
    print("\n15. Generating retirement summary...")
    retirement_summary = generate_retirement_summary(master_df, holdings_detail)
    export_retirement_data_js(retirement_summary)

    # 16. Corporate actions report
    print("\n16. Generating corporate actions report...")
    corporate_actions = generate_corporate_actions_report(master_df)
    if not corporate_actions.empty:
        # Export to CSV
        ca_path = DATA_OUTPUT_PATH / "corporate_actions.csv"
        corporate_actions.to_csv(ca_path, index=False)
        print(f"   Found {len(corporate_actions)} corporate actions")

        # Export to JS for dashboard
        export_dataframe_js(corporate_actions, "corporateActionsData", "corporate_actions.js")

        # Print summary
        action_counts = corporate_actions["Type"].value_counts()
        for action_type, count in action_counts.items():
            print(f"     - {action_type}: {count}")

    # 17. Multi-period performance report
    print("\n17. Calculating multi-period performance...")
    from .calculators.performance import generate_performance_report
    from .reports.exporters import export_performance_data_js

    performance_report = None
    if not holdings_detail.empty:
        performance_report = generate_performance_report(
            holdings_detail, price_cache, unavailable_ticker_cache, split_cache
        )
        export_performance_data_js(performance_report)

    # 18. Benchmark comparison report
    print("\n18. Generating benchmark comparison...")
    from .calculators.benchmark import generate_benchmark_report
    from .reports.exporters import export_benchmark_data_js

    benchmark_report = None
    if not historical_summary.empty and performance_report:
        portfolio_perf = performance_report.get("portfolio", {})
        benchmark_report = generate_benchmark_report(
            historical_summary, portfolio_perf, price_cache=price_cache
        )
        export_benchmark_data_js(benchmark_report)

    # 19. Monte Carlo simulation report
    print("\n19. Running Monte Carlo projections...")
    from .calculators.monte_carlo import generate_monte_carlo_report
    from .reports.exporters import export_monte_carlo_data_js

    monte_carlo_report = None
    if portfolio_summary.get("TotalValue", 0) > 0:
        # Get annual contribution from retirement data
        annual_contribution = 0
        if retirement_summary:
            contributions = retirement_summary.get("contributions", {})
            annual_contribution = (
                contributions.get("traditional_401k", 0)
                + contributions.get("roth_ira", 0)
                + retirement_summary.get("employer_match", 0)
            )

        # Get years to retirement
        years_to_retirement = 30
        if retirement_summary:
            personal = retirement_summary.get("personal", {})
            years_to_retirement = personal.get("years_to_retirement", 30)

        monte_carlo_report = generate_monte_carlo_report(
            portfolio_value=portfolio_summary.get("TotalValue", 0),
            annual_contribution=annual_contribution,
            historical_df=historical_summary,
            years_to_retirement=years_to_retirement,
            retirement_data=retirement_summary,
        )
        export_monte_carlo_data_js(monte_carlo_report)

    # Print summary to console
    print_portfolio_summary(portfolio_summary)

    # Run data quality checks if enabled
    run_quality = getattr(generate_all_reports, "_quality_check", True)
    if run_quality:
        dupes = getattr(master_df, "_dupes_removed", 0)
        issues, summary = run_quality_checks(
            master_df, holdings_detail if not holdings_detail.empty else None, dupes
        )
        print_quality_report(issues, summary)

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
        "portfolio_summary": portfolio_summary,
    }


def main(args: Optional[Any] = None) -> None:
    """
    Main entry point for the transaction aggregator.

    Args:
        args: Parsed CLI arguments (from argparse.Namespace)
    """
    from .cli import parse_args, print_banner, setup_logging

    # Parse arguments if not provided
    if args is None:
        args = parse_args()

    # Setup logging based on verbosity
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        setup_logging(verbose=True)
    else:
        setup_logging(verbose=False)

    # Print banner unless quiet
    if not args.quiet:
        print_banner(args.verbose)

    logger.info("=" * 60)
    logger.info("Financial Portfolio Aggregator Starting")
    logger.info("=" * 60)

    # Configure processing options
    process_all_files._parallel = args.parallel
    process_all_files._workers = args.workers
    generate_all_reports._quality_check = args.quality_check

    # Override paths if provided
    if args.input:
        global DATA_INPUT_PATH
        DATA_INPUT_PATH = args.input
        logger.info(f"Using custom input path: {DATA_INPUT_PATH}")

    if args.output:
        global DATA_OUTPUT_PATH
        DATA_OUTPUT_PATH = args.output
        logger.info(f"Using custom output path: {DATA_OUTPUT_PATH}")

    # Clear caches if requested
    if args.clear_cache:
        logger.info("Clearing caches...")
        try:
            from .utils.cache import PRICE_CACHE_PATH, SECTOR_CACHE_PATH, SPLIT_CACHE_PATH

            for cache_path in [PRICE_CACHE_PATH, SPLIT_CACHE_PATH, SECTOR_CACHE_PATH]:
                if cache_path.exists():
                    cache_path.unlink()
                    logger.info(f"Cleared {cache_path.name}")
        except Exception as e:
            logger.error(f"Error clearing caches: {e}")

    try:
        # Process all input files
        master_df = process_all_files()
    except Exception as e:
        logger.exception("Fatal error during file processing")
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)

    # Export to CSV
    if len(master_df) > 0:
        try:
            export_master_csv(master_df)
            logger.info("Exported master transactions CSV")
        except Exception as e:
            logger.exception("Failed to export master CSV")
            print(f"\n✗ Error exporting master CSV: {e}")

        # Reload price cache after processing (parsers may have added entries)
        try:
            price_cache = load_price_cache()
        except Exception as e:
            logger.error(f"Failed to reload price cache: {e}")
            price_cache = {}

        # Generate all reports
        try:
            unavailable_ticker_cache = load_unavailable_ticker_cache()
            split_cache = load_split_cache()
            generate_all_reports(master_df, price_cache, unavailable_ticker_cache, split_cache)
        except Exception as e:
            logger.exception("Failed to generate reports")
            print(f"\n✗ Error generating reports: {e}")
            unavailable_ticker_cache = {}

        # Save caches (may have new prices and unavailable tickers from report generation)
        try:
            save_price_cache(price_cache)
            save_unavailable_ticker_cache(unavailable_ticker_cache)
            if unavailable_ticker_cache:
                print(
                    f"\n✓ Saved unavailable ticker cache with {len(unavailable_ticker_cache)} tickers"
                )
            logger.info("Saved final caches")
        except Exception as e:
            logger.error(f"Failed to save final caches: {e}")

        # Show sample of output
        print("\nSample of master transactions (first 10 rows):")
        print(master_df.head(10).to_string())
    else:
        logger.warning("No transactions to export")
        print("\nNo transactions to export.")

    logger.info("Processing completed successfully")


if __name__ == "__main__":
    main()
