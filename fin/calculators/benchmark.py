"""
Benchmark Calculator Module
===========================
Calculates portfolio performance relative to market benchmarks (S&P 500, etc.)
including alpha, beta, Sharpe ratio, and tracking error.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Common benchmark symbols
BENCHMARKS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "VTI": "Total US Market", "BND": "US Bonds"}

# Default benchmark for comparison
DEFAULT_BENCHMARK = "SPY"

# Risk-free rate assumption (approximate current Treasury rate)
RISK_FREE_RATE = 0.045  # 4.5% annual


def get_benchmark_prices(
    symbol: str,
    start_date: str,
    end_date: str = None,
    price_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> pd.DataFrame:
    """
    Fetch historical prices for a benchmark symbol, using cache when available.

    Args:
        symbol: Benchmark ticker (e.g., 'SPY')
        start_date: Start date in YYYY-MM-DD format
        end_date: End date (defaults to today)
        price_cache: Optional price cache to check before fetching

    Returns:
        DataFrame with Date and Price columns
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # Try to get prices from cache first
    if price_cache is not None and symbol in price_cache:
        cached_dates = price_cache[symbol]

        # Filter cached dates to those within our requested range
        # Exclude metadata keys that start with '_'
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        cached_prices = []
        for date_str, price in cached_dates.items():
            # Skip metadata keys
            if date_str.startswith("_"):
                continue
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if start_dt <= date <= end_dt:
                    cached_prices.append({"Date": date, "Price": price})
            except ValueError:
                continue

        # If we have substantial cached data (at least 50 trading days worth),
        # check if we have recent data. Markets have ~252 trading days/year.
        # For benchmark comparison, we mainly need the trend, not every single day.
        if len(cached_prices) >= 50:
            # Sort to check date coverage
            cached_prices.sort(key=lambda x: x["Date"])
            oldest_cached = cached_prices[0]["Date"]
            newest_cached = cached_prices[-1]["Date"]

            # Calculate how many trading days we expect (roughly 5/7 of calendar days)
            expected_trading_days = (end_dt - start_dt).days * 5 / 7
            actual_coverage = (
                len(cached_prices) / expected_trading_days if expected_trading_days > 0 else 0
            )

            # Use cache if:
            # 1. We have reasonable coverage (>60% of expected trading days)
            # 2. Our newest cached date is within 7 days of the requested end date
            days_since_newest = (end_dt - newest_cached).days

            if actual_coverage > 0.6 and days_since_newest <= 7:
                logger.debug(
                    f"Using cached data for {symbol} ({len(cached_prices)} days, {actual_coverage*100:.1f}% coverage, {days_since_newest} days old)"
                )
                df = pd.DataFrame(cached_prices)
                df["Date"] = pd.to_datetime(df["Date"])
                return df

    # Need to fetch from yfinance
    try:
        logger.debug(f"Fetching {symbol} data from yfinance for {start_date} to {end_date}")
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start_date, end=end_date)

        if hist.empty:
            logger.warning(f"No data found for benchmark {symbol}")
            return pd.DataFrame()

        # Reset index and normalize
        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
        hist = hist[["Date", "Close"]].rename(columns={"Close": "Price"})

        # Cache the fetched data
        if price_cache is not None:
            if symbol not in price_cache:
                price_cache[symbol] = {}
            for _, row in hist.iterrows():
                date_str = row["Date"].strftime("%Y-%m-%d")
                price_cache[symbol][date_str] = float(row["Price"])
            logger.debug(f"Cached {len(hist)} prices for {symbol}")

        return hist

    except Exception as e:
        logger.error(f"Error fetching benchmark {symbol}: {e}")
        return pd.DataFrame()


def calculate_benchmark_returns(benchmark_df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate multi-period returns for a benchmark.

    Args:
        benchmark_df: DataFrame with Date and Price columns

    Returns:
        Dict with period returns (1D, 1W, 1M, 3M, 6M, 1Y, 2Y, 5Y, YTD)
    """
    if benchmark_df.empty:
        return {}

    benchmark_df = benchmark_df.sort_values("Date")
    latest_price = benchmark_df.iloc[-1]["Price"]
    latest_date = benchmark_df.iloc[-1]["Date"]

    periods = {
        "1D": 1,
        "1W": 7,
        "2W": 14,
        "1M": 30,
        "3M": 90,
        "6M": 180,
        "1Y": 365,
        "2Y": 730,
        "5Y": 1825,
    }

    results = {"current_price": round(latest_price, 2)}

    for period_name, days in periods.items():
        target_date = latest_date - timedelta(days=days)
        # Find closest date on or before target
        prior_data = benchmark_df[benchmark_df["Date"] <= target_date]

        if not prior_data.empty:
            prior_price = prior_data.iloc[-1]["Price"]
            period_return = ((latest_price - prior_price) / prior_price) * 100
            results[f"Return_{period_name}"] = round(period_return, 2)
        else:
            results[f"Return_{period_name}"] = None

    # YTD return
    year_start = datetime(latest_date.year, 1, 1)
    ytd_data = benchmark_df[benchmark_df["Date"] <= year_start]
    if not ytd_data.empty:
        ytd_price = ytd_data.iloc[-1]["Price"]
        ytd_return = ((latest_price - ytd_price) / ytd_price) * 100
        results["Return_YTD"] = round(ytd_return, 2)
    else:
        results["Return_YTD"] = None

    return results


def calculate_portfolio_daily_returns(historical_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate daily returns for the portfolio from historical holdings data.

    Args:
        historical_df: DataFrame with Date and TotalValue columns

    Returns:
        DataFrame with Date and Return columns (daily % change)
    """
    if historical_df.empty or "TotalValue" not in historical_df.columns:
        return pd.DataFrame()

    df = historical_df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")

    # Calculate daily returns
    df["Return"] = df["TotalValue"].pct_change() * 100

    return df[["Date", "TotalValue", "Return"]].dropna()


def calculate_alpha_beta(
    portfolio_returns: pd.DataFrame,
    benchmark_returns: pd.DataFrame,
    risk_free_rate: float = RISK_FREE_RATE,
) -> Dict[str, float]:
    """
    Calculate alpha and beta of portfolio relative to benchmark.

    Alpha: Excess return over benchmark (risk-adjusted)
    Beta: Portfolio sensitivity to market movements

    Args:
        portfolio_returns: DataFrame with Date and Return columns
        benchmark_returns: DataFrame with Date and Price columns
        risk_free_rate: Annual risk-free rate

    Returns:
        Dict with alpha, beta, and R-squared
    """
    if portfolio_returns.empty or benchmark_returns.empty:
        return {"alpha": None, "beta": None, "r_squared": None}

    # Calculate benchmark daily returns
    bench_df = benchmark_returns.copy()
    bench_df["Date"] = pd.to_datetime(bench_df["Date"])
    bench_df = bench_df.sort_values("Date")
    bench_df["BenchReturn"] = bench_df["Price"].pct_change() * 100
    bench_df = bench_df.dropna()

    # Merge on date
    port_df = portfolio_returns.copy()
    port_df["Date"] = pd.to_datetime(port_df["Date"])

    merged = pd.merge(port_df, bench_df[["Date", "BenchReturn"]], on="Date", how="inner")

    if len(merged) < 30:  # Need sufficient data points
        logger.warning(f"Insufficient data points for alpha/beta calculation: {len(merged)}")
        return {"alpha": None, "beta": None, "r_squared": None}

    # Calculate beta using covariance / variance
    port_returns = merged["Return"].values
    bench_returns = merged["BenchReturn"].values

    covariance = np.cov(port_returns, bench_returns)[0, 1]
    benchmark_variance = np.var(bench_returns)

    if benchmark_variance == 0:
        return {"alpha": None, "beta": None, "r_squared": None}

    beta = covariance / benchmark_variance

    # Calculate alpha (annualized)
    # Jensen's Alpha = Portfolio Return - [Risk-Free Rate + Beta * (Market Return - Risk-Free Rate)]
    daily_rf = risk_free_rate / 252
    avg_port_return = np.mean(port_returns)
    avg_bench_return = np.mean(bench_returns)

    daily_alpha = avg_port_return - (daily_rf + beta * (avg_bench_return - daily_rf))
    annual_alpha = daily_alpha * 252  # Annualize

    # Calculate R-squared
    correlation = np.corrcoef(port_returns, bench_returns)[0, 1]
    r_squared = correlation**2

    return {
        "alpha": round(annual_alpha, 2),
        "beta": round(beta, 2),
        "r_squared": round(r_squared * 100, 1),  # As percentage
    }


def calculate_sharpe_ratio(
    portfolio_returns: pd.DataFrame, risk_free_rate: float = RISK_FREE_RATE
) -> float:
    """
    Calculate the Sharpe ratio of the portfolio.

    Sharpe Ratio = (Portfolio Return - Risk-Free Rate) / Portfolio Std Dev

    Args:
        portfolio_returns: DataFrame with Return column (daily %)
        risk_free_rate: Annual risk-free rate

    Returns:
        Annualized Sharpe ratio
    """
    if portfolio_returns.empty or "Return" not in portfolio_returns.columns:
        return None

    returns = portfolio_returns["Return"].values

    if len(returns) < 30:
        return None

    # Annualize metrics
    avg_return = np.mean(returns) * 252  # Daily to annual
    std_dev = np.std(returns) * np.sqrt(252)  # Daily to annual

    if std_dev == 0:
        return None

    sharpe = (avg_return - risk_free_rate * 100) / std_dev

    return round(sharpe, 2)


def calculate_tracking_error(
    portfolio_returns: pd.DataFrame, benchmark_returns: pd.DataFrame
) -> float:
    """
    Calculate tracking error (standard deviation of excess returns).

    Args:
        portfolio_returns: DataFrame with Date and Return columns
        benchmark_returns: DataFrame with Date and Price columns

    Returns:
        Annualized tracking error as percentage
    """
    if portfolio_returns.empty or benchmark_returns.empty:
        return None

    # Calculate benchmark daily returns
    bench_df = benchmark_returns.copy()
    bench_df["Date"] = pd.to_datetime(bench_df["Date"])
    bench_df = bench_df.sort_values("Date")
    bench_df["BenchReturn"] = bench_df["Price"].pct_change() * 100

    # Merge on date
    port_df = portfolio_returns.copy()
    port_df["Date"] = pd.to_datetime(port_df["Date"])

    merged = pd.merge(port_df, bench_df[["Date", "BenchReturn"]], on="Date", how="inner")

    if len(merged) < 30:
        return None

    # Excess returns
    excess_returns = merged["Return"] - merged["BenchReturn"]

    # Annualized tracking error
    tracking_error = np.std(excess_returns) * np.sqrt(252)

    return round(tracking_error, 2)


def calculate_max_drawdown(historical_df: pd.DataFrame) -> Dict[str, any]:
    """
    Calculate maximum drawdown for the portfolio.

    Args:
        historical_df: DataFrame with Date and TotalValue columns

    Returns:
        Dict with max drawdown percentage, peak date, trough date
    """
    if historical_df.empty or "TotalValue" not in historical_df.columns:
        return {"max_drawdown": None, "peak_date": None, "trough_date": None}

    df = historical_df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")

    # Calculate running maximum
    df["RunningMax"] = df["TotalValue"].cummax()

    # Calculate drawdown
    df["Drawdown"] = (df["TotalValue"] - df["RunningMax"]) / df["RunningMax"] * 100

    # Find max drawdown
    min_idx = df["Drawdown"].idxmin()
    max_dd = df.loc[min_idx, "Drawdown"]
    trough_date = df.loc[min_idx, "Date"]

    # Find peak before the trough
    peak_df = df[df["Date"] < trough_date]
    if not peak_df.empty:
        peak_idx = peak_df["TotalValue"].idxmax()
        peak_date = peak_df.loc[peak_idx, "Date"]
    else:
        peak_date = None

    return {
        "max_drawdown": round(max_dd, 2),
        "peak_date": peak_date.strftime("%Y-%m-%d") if peak_date else None,
        "trough_date": trough_date.strftime("%Y-%m-%d") if trough_date else None,
    }


def calculate_cagr(start_value: float, end_value: float, years: float) -> Optional[float]:
    """
    Calculate Compound Annual Growth Rate (CAGR).

    CAGR = (End Value / Start Value) ^ (1 / Years) - 1

    Args:
        start_value: Starting value (cost basis at start)
        end_value: Ending value (portfolio value now)
        years: Number of years

    Returns:
        CAGR as a percentage, or None if invalid
    """
    if start_value <= 0 or end_value <= 0 or years <= 0:
        return None

    cagr = (pow(end_value / start_value, 1 / years) - 1) * 100
    return round(cagr, 2)


def calculate_roi_metrics(historical_df: pd.DataFrame) -> Dict:
    """
    Calculate Return on Investment metrics using historical cost basis.

    Args:
        historical_df: DataFrame with Date, TotalValue, and CostBasis columns

    Returns:
        Dict with ROI metrics and historical ROI data
    """
    result = {"current_roi": None, "cagr": None, "avg_annual_return": None, "roi_history": []}

    if historical_df.empty:
        return result

    df = historical_df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")

    # Check for required columns
    if "CostBasis" not in df.columns or "TotalValue" not in df.columns:
        logger.warning("Missing CostBasis or TotalValue for ROI calculation")
        return result

    # Current ROI
    latest = df.iloc[-1]
    if latest["CostBasis"] > 0:
        current_roi = ((latest["TotalValue"] - latest["CostBasis"]) / latest["CostBasis"]) * 100
        result["current_roi"] = round(current_roi, 2)

    # CAGR from first to last
    first = df.iloc[0]
    years = (latest["Date"] - first["Date"]).days / 365.25

    if years > 0 and first["CostBasis"] > 0:
        result["cagr"] = calculate_cagr(first["CostBasis"], latest["TotalValue"], years)

    # Average Annual Return (simple arithmetic mean of yearly returns)
    if "TWR" in df.columns:
        # Use TWR if available for more accurate return
        df["Year"] = df["Date"].dt.year
        yearly = df.groupby("Year").last().reset_index()
        if len(yearly) > 1:
            yearly["YearReturn"] = yearly["TWR"].diff()
            avg_annual = yearly["YearReturn"].dropna().mean()
            result["avg_annual_return"] = round(avg_annual, 2)

    # Historical ROI for charting (ROI at each point in time)
    roi_history = []
    for _, row in df.iterrows():
        if row["CostBasis"] > 0:
            roi = ((row["TotalValue"] - row["CostBasis"]) / row["CostBasis"]) * 100
            roi_history.append(
                {
                    "date": row["Date"].strftime("%Y-%m-%d"),
                    "roi": round(roi, 2),
                    "value": round(row["TotalValue"], 2),
                    "costBasis": round(row["CostBasis"], 2),
                }
            )

    result["roi_history"] = roi_history

    return result


def generate_benchmark_report(
    historical_df: pd.DataFrame,
    portfolio_perf: Dict,
    benchmark_symbol: str = DEFAULT_BENCHMARK,
    price_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict:
    """
    Generate a comprehensive benchmark comparison report.

    Args:
        historical_df: Historical portfolio values (from historical_holdings)
        portfolio_perf: Portfolio performance data (from performance calculator)
        benchmark_symbol: Benchmark to compare against
        price_cache: Optional price cache to reduce API calls

    Returns:
        Dict with benchmark comparison metrics
    """
    print(f"\n18. Calculating benchmark comparison ({benchmark_symbol})...")

    result = {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_name": BENCHMARKS.get(benchmark_symbol, benchmark_symbol),
        "generated_at": datetime.now().isoformat(),
    }

    # Determine date range from historical data
    if historical_df.empty or "Date" not in historical_df.columns:
        logger.warning("No historical data available for benchmark comparison")
        return result

    hist_df = historical_df.copy()
    hist_df["Date"] = pd.to_datetime(hist_df["Date"])
    start_date = hist_df["Date"].min().strftime("%Y-%m-%d")
    end_date = hist_df["Date"].max().strftime("%Y-%m-%d")

    # Fetch benchmark data (using cache if available)
    print(f"   Getting {benchmark_symbol} data from {start_date} to {end_date}...")
    benchmark_df = get_benchmark_prices(benchmark_symbol, start_date, end_date, price_cache)

    if benchmark_df.empty:
        logger.warning(f"Could not fetch benchmark data for {benchmark_symbol}")
        return result

    # Calculate benchmark returns
    benchmark_returns = calculate_benchmark_returns(benchmark_df)
    result["benchmark"] = benchmark_returns

    # Add portfolio returns for comparison
    result["portfolio"] = {
        "Return_1D": portfolio_perf.get("Return_1D"),
        "Return_1W": portfolio_perf.get("Return_1W"),
        "Return_2W": portfolio_perf.get("Return_2W"),
        "Return_1M": portfolio_perf.get("Return_1M"),
        "Return_3M": portfolio_perf.get("Return_3M"),
        "Return_6M": portfolio_perf.get("Return_6M"),
        "Return_1Y": portfolio_perf.get("Return_1Y"),
        "Return_2Y": portfolio_perf.get("Return_2Y"),
        "Return_5Y": portfolio_perf.get("Return_5Y"),
        "TotalReturn": portfolio_perf.get("TotalReturn"),
    }

    # Calculate excess returns (portfolio - benchmark)
    excess = {}
    for period in ["1D", "1W", "2W", "1M", "3M", "6M", "1Y", "2Y", "5Y"]:
        port_ret = result["portfolio"].get(f"Return_{period}")
        bench_ret = benchmark_returns.get(f"Return_{period}")

        if port_ret is not None and bench_ret is not None:
            excess[f"Excess_{period}"] = round(port_ret - bench_ret, 2)
        else:
            excess[f"Excess_{period}"] = None

    result["excess_returns"] = excess

    # Calculate portfolio daily returns
    portfolio_returns = calculate_portfolio_daily_returns(hist_df)

    # Alpha, Beta, R-squared
    ab_metrics = calculate_alpha_beta(portfolio_returns, benchmark_df)
    result["alpha"] = ab_metrics["alpha"]
    result["beta"] = ab_metrics["beta"]
    result["r_squared"] = ab_metrics["r_squared"]

    # Sharpe ratio
    result["sharpe_ratio"] = calculate_sharpe_ratio(portfolio_returns)

    # Tracking error
    result["tracking_error"] = calculate_tracking_error(portfolio_returns, benchmark_df)

    # Max drawdown
    drawdown = calculate_max_drawdown(hist_df)
    result["max_drawdown"] = drawdown["max_drawdown"]
    result["drawdown_peak"] = drawdown["peak_date"]
    result["drawdown_trough"] = drawdown["trough_date"]

    # Summary statistics
    if portfolio_returns is not None and not portfolio_returns.empty:
        returns = portfolio_returns["Return"].values
        result["volatility"] = round(np.std(returns) * np.sqrt(252), 2)  # Annualized
        result["best_day"] = round(np.max(returns), 2)
        result["worst_day"] = round(np.min(returns), 2)
        result["positive_days"] = int(np.sum(returns > 0))
        result["negative_days"] = int(np.sum(returns < 0))
        result["total_days"] = len(returns)

    # ROI metrics using historical cost basis
    roi_metrics = calculate_roi_metrics(hist_df)
    result["current_roi"] = roi_metrics["current_roi"]
    result["cagr"] = roi_metrics["cagr"]
    result["avg_annual_return"] = roi_metrics["avg_annual_return"]
    result["roi_history"] = roi_metrics["roi_history"]

    # Historical comparison data for charting
    # Use TWR (Time-Weighted Return) if available, otherwise use TotalValue
    if not benchmark_df.empty and not hist_df.empty:
        chart_data = []

        # Check for available columns
        use_twr = "TWR" in hist_df.columns
        use_cost_basis = "CostBasis" in hist_df.columns

        cols_to_merge = ["Date", "TotalValue"]
        if use_twr:
            cols_to_merge.append("TWR")
        if use_cost_basis:
            cols_to_merge.append("CostBasis")

        merged = pd.merge(hist_df[cols_to_merge], benchmark_df, on="Date", how="inner")

        if not merged.empty:
            start_bench = merged.iloc[0]["Price"]

            if use_twr:
                # TWR is already cumulative percentage return, convert to indexed value
                # TWR of 0% = 100, TWR of 50% = 150, etc.
                merged["PortfolioNorm"] = 100 + merged["TWR"]
            else:
                # Fallback: Normalize total value (not ideal - includes contributions)
                start_port = merged.iloc[0]["TotalValue"]
                merged["PortfolioNorm"] = merged["TotalValue"] / start_port * 100

            merged["BenchmarkNorm"] = merged["Price"] / start_bench * 100

            # Calculate ROI at each point if cost basis available
            if use_cost_basis:
                merged["ROI"] = (
                    (merged["TotalValue"] - merged["CostBasis"]) / merged["CostBasis"]
                ) * 100

            # Sample monthly (use 'ME' for month-end)
            merged = merged.set_index("Date")
            monthly = merged.resample("ME").last().reset_index()

            # Drop rows with NaN values
            monthly = monthly.dropna(subset=["PortfolioNorm", "BenchmarkNorm"])

            for _, row in monthly.iterrows():
                if (
                    pd.notna(row["Date"])
                    and pd.notna(row["PortfolioNorm"])
                    and pd.notna(row["BenchmarkNorm"])
                ):
                    entry = {
                        "date": row["Date"].strftime("%Y-%m-%d"),
                        "portfolio": round(float(row["PortfolioNorm"]), 2),
                        "benchmark": round(float(row["BenchmarkNorm"]), 2),
                    }
                    # Add ROI if available
                    if use_cost_basis and "ROI" in row and pd.notna(row["ROI"]):
                        entry["roi"] = round(float(row["ROI"]), 2)
                    # Add cost basis normalized for chart
                    if use_cost_basis and "CostBasis" in row and pd.notna(row["CostBasis"]):
                        entry["costBasis"] = round(float(row["CostBasis"]), 2)
                        entry["value"] = round(float(row["TotalValue"]), 2)
                    chart_data.append(entry)

        result["chart_data"] = chart_data

    print(
        f"   Alpha: {result.get('alpha')}%, Beta: {result.get('beta')}, Sharpe: {result.get('sharpe_ratio')}, CAGR: {result.get('cagr')}%"
    )

    return result
