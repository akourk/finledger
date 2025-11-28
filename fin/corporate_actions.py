"""
Corporate Actions Module
========================
Handles stock splits, spin-offs, mergers, and other corporate actions.

Corporate Action Types:
- Stock Splits (forward and reverse)
- Spin-offs (shares received from parent company)
- Mergers (stock-for-stock, cash, or mixed)
- Liquidations
- Cash in Lieu (fractional shares)
- Rights offerings
- Name/Symbol changes
"""

import logging
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class CorporateAction:
    """Represents a corporate action event."""
    action_type: str  # 'split', 'spinoff', 'merger', 'liquidation', 'symbol_change'
    date: str
    symbol: str
    account: str = ""
    
    # Split-specific
    split_ratio: float = 1.0  # e.g., 4.0 for 4:1 split, 0.1 for 1:10 reverse
    
    # SpinOff-specific
    parent_symbol: str = ""
    shares_received: float = 0.0
    cost_basis_allocation_pct: float = 0.0  # % of parent's basis to allocate
    
    # Merger-specific
    acquired_symbol: str = ""
    exchange_ratio: float = 0.0  # shares of new per share of old
    cash_per_share: float = 0.0
    
    # Additional metadata
    cusip: str = ""
    description: str = ""
    estimated_basis: float = 0.0  # Estimated cost basis allocation


@dataclass 
class SpinOffAllocation:
    """
    Cost basis allocation for a spin-off.
    
    IRS requires allocating parent's cost basis between parent and spun-off shares
    based on fair market value on the first trading day after the spin-off.
    """
    parent_symbol: str
    spinoff_symbol: str
    distribution_date: str
    
    # FMV on first trading day after distribution
    parent_fmv: float = 0.0
    spinoff_fmv: float = 0.0
    
    # Calculated allocation percentages
    parent_allocation_pct: float = 100.0
    spinoff_allocation_pct: float = 0.0
    
    # Distribution ratio (spinoff shares per parent share)
    ratio: float = 0.0


# =============================================================================
# Known Corporate Actions Database
# =============================================================================

# Known spin-offs with cost basis allocation percentages
# Format: {spinoff_symbol: SpinOffAllocation}
KNOWN_SPINOFFS: Dict[str, SpinOffAllocation] = {
    # Example: PayPal spin-off from eBay (2015)
    # "PYPL": SpinOffAllocation(
    #     parent_symbol="EBAY",
    #     spinoff_symbol="PYPL",
    #     distribution_date="2015-07-17",
    #     parent_fmv=27.97,
    #     spinoff_fmv=42.55,
    #     parent_allocation_pct=39.68,
    #     spinoff_allocation_pct=60.32,
    #     ratio=1.0
    # ),
    
    # GameStop Warrants (2025) - No cost basis allocated (issued as dividend)
    "GME+": SpinOffAllocation(
        parent_symbol="GME",
        spinoff_symbol="GME+",
        distribution_date="2025-10-07",
        parent_fmv=0.0,  # Warrants typically have no allocated basis
        spinoff_fmv=0.0,
        parent_allocation_pct=100.0,
        spinoff_allocation_pct=0.0,
        ratio=0.1  # 1 warrant per 10 shares
    ),
    
    # Enovix Warrants (ENVXW) from ENVX
    "ENVXW": SpinOffAllocation(
        parent_symbol="ENVX",
        spinoff_symbol="ENVXW",
        distribution_date="2025-07-21",
        parent_fmv=0.0,
        spinoff_fmv=0.0,
        parent_allocation_pct=100.0,
        spinoff_allocation_pct=0.0,
        ratio=0.1
    ),
}

# Known symbol changes (old -> new)
SYMBOL_CHANGES: Dict[str, str] = {
    "FB": "META",
    "TWTR": "X",  # Twitter acquired, symbol retired
}

# Known mergers with exchange ratios
# Format: {acquired_symbol: (acquirer_symbol, exchange_ratio, cash_per_share)}
KNOWN_MERGERS: Dict[str, Tuple[str, float, float]] = {
    # AMD acquired Xilinx (2022-02-15)
    "XLNX": ("AMD", 1.7234, 0.0),
    
    # Twitter acquired for cash (2022-10-31)
    "TWTR": ("", 0.0, 54.20),
    
    # Verve Therapeutics acquired VRNA (2025)
    "VRNA": ("", 0.0, 107.00),
    
    # Turning Point Therapeutics acquired for cash
    "TPTX": ("", 0.0, 76.00),
}


# =============================================================================
# Split Detection & Adjustment
# =============================================================================

def get_all_splits_for_symbol(ticker: str, split_cache: Dict) -> Dict[str, float]:
    """
    Get all stock splits for a ticker from yfinance.
    
    Args:
        ticker: Stock symbol
        split_cache: Cache dictionary to store/retrieve splits
        
    Returns:
        Dict of {date_str: ratio} for all splits
    """
    if ticker not in split_cache:
        try:
            stock = yf.Ticker(ticker)
            splits = stock.splits
            
            if splits is None or splits.empty:
                split_cache[ticker] = {}
            else:
                split_cache[ticker] = {
                    date.strftime("%Y-%m-%d"): float(ratio) 
                    for date, ratio in splits.items()
                }
                logger.debug(f"Found {len(split_cache[ticker])} splits for {ticker}")
        except Exception as e:
            logger.warning(f"Error fetching splits for {ticker}: {e}")
            split_cache[ticker] = {}
    
    return split_cache.get(ticker, {})


def get_cumulative_split_multiplier(
    ticker: str, 
    from_date: str, 
    to_date: str,
    split_cache: Dict
) -> float:
    """
    Calculate cumulative split multiplier between two dates.
    
    Args:
        ticker: Stock symbol
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        split_cache: Cache dictionary
        
    Returns:
        Cumulative multiplier (e.g., 40 for NVDA 4:1 + 10:1 splits)
    """
    splits = get_all_splits_for_symbol(ticker, split_cache)
    
    if not splits:
        return 1.0
    
    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    
    multiplier = 1.0
    for split_date_str, ratio in splits.items():
        split_dt = datetime.strptime(split_date_str, "%Y-%m-%d")
        if from_dt < split_dt <= to_dt:
            multiplier *= ratio
            logger.debug(f"{ticker}: Applied {ratio}:1 split from {split_date_str}")
    
    return multiplier


def detect_unadjusted_transactions(
    df: pd.DataFrame,
    split_cache: Dict
) -> List[Dict[str, Any]]:
    """
    Detect transactions that may need split adjustment.
    
    This helps identify potential data issues where historical prices
    don't match the expected split-adjusted values.
    
    Args:
        df: Transaction DataFrame
        split_cache: Split cache
        
    Returns:
        List of potentially unadjusted transactions
    """
    issues = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Only check stock-like symbols
    stock_mask = (
        (df["Symbol"].str.len() <= 5) &
        (df["Symbol"].str.match(r'^[A-Z]+$', na=False)) &
        (df["Action"].isin(["Buy", "Sell"])) &
        (df["Price"] > 0)
    )
    
    for symbol in df.loc[stock_mask, "Symbol"].unique():
        multiplier = get_cumulative_split_multiplier(symbol, "2000-01-01", today, split_cache)
        
        if multiplier > 1:
            symbol_df = df[(df["Symbol"] == symbol) & stock_mask]
            
            for _, row in symbol_df.iterrows():
                # Check if price seems pre-split
                tx_multiplier = get_cumulative_split_multiplier(
                    symbol, row["Date"], today, split_cache
                )
                
                if tx_multiplier > 1:
                    expected_adjusted_price = row["Price"] / tx_multiplier
                    
                    # Flag if price looks pre-split (high relative to typical values)
                    # This is a heuristic that may need tuning
                    if row["Price"] > 500 and tx_multiplier >= 4:
                        issues.append({
                            "symbol": symbol,
                            "date": row["Date"],
                            "price": row["Price"],
                            "multiplier": tx_multiplier,
                            "expected_price": expected_adjusted_price,
                            "action": "Price may be pre-split adjusted"
                        })
    
    return issues


# =============================================================================
# Spin-Off Cost Basis Allocation
# =============================================================================

def calculate_spinoff_basis_allocation(
    spinoff: SpinOffAllocation,
    parent_cost_basis: float,
    parent_shares: float
) -> Tuple[float, float]:
    """
    Calculate cost basis allocation for a spin-off.
    
    IRS rules require allocating basis based on relative FMV
    on the first trading day after distribution.
    
    Args:
        spinoff: SpinOffAllocation with FMV and ratio info
        parent_cost_basis: Original cost basis of parent shares
        parent_shares: Number of parent shares held
        
    Returns:
        Tuple of (new_parent_basis, spinoff_basis)
    """
    if spinoff.spinoff_allocation_pct == 0:
        # No basis allocated to spinoff (e.g., warrants)
        return parent_cost_basis, 0.0
    
    spinoff_shares = parent_shares * spinoff.ratio
    
    # Allocate based on percentages
    new_parent_basis = parent_cost_basis * (spinoff.parent_allocation_pct / 100)
    spinoff_basis = parent_cost_basis * (spinoff.spinoff_allocation_pct / 100)
    
    return new_parent_basis, spinoff_basis


def lookup_spinoff_fmv(
    parent_symbol: str,
    spinoff_symbol: str,
    distribution_date: str,
    price_cache: Dict
) -> Tuple[float, float]:
    """
    Look up fair market values for spin-off allocation calculation.
    
    Uses the closing prices on the first trading day after distribution.
    
    Args:
        parent_symbol: Parent company symbol
        spinoff_symbol: Spun-off company symbol
        distribution_date: Distribution date (YYYY-MM-DD)
        price_cache: Price cache
        
    Returns:
        Tuple of (parent_fmv, spinoff_fmv)
    """
    # Get price on day after distribution (or next trading day)
    dist_date = datetime.strptime(distribution_date, "%Y-%m-%d")
    lookup_date = (dist_date + timedelta(days=1)).strftime("%Y-%m-%d")
    
    from .utils.prices import get_price_from_yfinance
    
    parent_fmv, _ = get_price_from_yfinance(parent_symbol, lookup_date, price_cache)
    spinoff_fmv, _ = get_price_from_yfinance(spinoff_symbol, lookup_date, price_cache)
    
    return parent_fmv or 0.0, spinoff_fmv or 0.0


def estimate_spinoff_allocation(
    spinoff_symbol: str,
    distribution_date: str,
    parent_symbol: str = None,
    price_cache: Dict = None
) -> SpinOffAllocation:
    """
    Estimate spin-off cost basis allocation.
    
    First checks known spin-offs database, then tries to estimate
    from market prices.
    
    Args:
        spinoff_symbol: The spun-off company symbol
        distribution_date: Date of distribution
        parent_symbol: Parent company (if known)
        price_cache: Price cache for FMV lookup
        
    Returns:
        SpinOffAllocation with estimated values
    """
    # Check known database first
    if spinoff_symbol in KNOWN_SPINOFFS:
        return KNOWN_SPINOFFS[spinoff_symbol]
    
    # Try to estimate from market data
    if parent_symbol and price_cache:
        parent_fmv, spinoff_fmv = lookup_spinoff_fmv(
            parent_symbol, spinoff_symbol, distribution_date, price_cache
        )
        
        if parent_fmv > 0 and spinoff_fmv > 0:
            total_fmv = parent_fmv + spinoff_fmv
            parent_pct = (parent_fmv / total_fmv) * 100
            spinoff_pct = (spinoff_fmv / total_fmv) * 100
            
            return SpinOffAllocation(
                parent_symbol=parent_symbol,
                spinoff_symbol=spinoff_symbol,
                distribution_date=distribution_date,
                parent_fmv=parent_fmv,
                spinoff_fmv=spinoff_fmv,
                parent_allocation_pct=parent_pct,
                spinoff_allocation_pct=spinoff_pct,
                ratio=1.0  # Unknown, default to 1:1
            )
    
    # Return default (no allocation - treat as $0 basis)
    logger.warning(f"Unknown spin-off {spinoff_symbol} - using $0 cost basis")
    return SpinOffAllocation(
        parent_symbol=parent_symbol or "UNKNOWN",
        spinoff_symbol=spinoff_symbol,
        distribution_date=distribution_date,
        parent_allocation_pct=100.0,
        spinoff_allocation_pct=0.0,
        ratio=1.0
    )


# =============================================================================
# Merger Handling
# =============================================================================

def get_merger_info(acquired_symbol: str) -> Optional[Tuple[str, float, float]]:
    """
    Get merger information for an acquired company.
    
    Args:
        acquired_symbol: Symbol of acquired company
        
    Returns:
        Tuple of (acquirer_symbol, exchange_ratio, cash_per_share) or None
    """
    return KNOWN_MERGERS.get(acquired_symbol)


def calculate_merger_basis(
    acquired_symbol: str,
    acquired_shares: float,
    acquired_cost_basis: float,
    cash_received: float = 0.0
) -> Dict[str, Any]:
    """
    Calculate cost basis after a merger.
    
    For stock-for-stock mergers: Cost basis transfers to new shares
    For cash mergers: Realizes gain/loss
    For mixed mergers: Partial realization + basis transfer
    
    Args:
        acquired_symbol: Symbol of acquired company
        acquired_shares: Number of shares held
        acquired_cost_basis: Total cost basis of acquired shares
        cash_received: Cash received in merger (if any)
        
    Returns:
        Dict with new_symbol, new_shares, new_basis, realized_gain
    """
    merger_info = get_merger_info(acquired_symbol)
    
    if not merger_info:
        # Unknown merger - assume all cash
        return {
            "new_symbol": None,
            "new_shares": 0,
            "new_basis": 0,
            "realized_gain": cash_received - acquired_cost_basis,
            "is_taxable": True
        }
    
    acquirer_symbol, exchange_ratio, cash_per_share = merger_info
    
    if exchange_ratio == 0 and cash_per_share > 0:
        # Pure cash merger
        proceeds = acquired_shares * cash_per_share
        return {
            "new_symbol": None,
            "new_shares": 0,
            "new_basis": 0,
            "realized_gain": proceeds - acquired_cost_basis,
            "is_taxable": True
        }
    
    elif exchange_ratio > 0 and cash_per_share == 0:
        # Pure stock-for-stock merger (tax-free reorganization)
        new_shares = acquired_shares * exchange_ratio
        return {
            "new_symbol": acquirer_symbol,
            "new_shares": new_shares,
            "new_basis": acquired_cost_basis,  # Basis carries over
            "realized_gain": 0,
            "is_taxable": False
        }
    
    else:
        # Mixed merger (stock + cash)
        new_shares = acquired_shares * exchange_ratio
        cash_proceeds = acquired_shares * cash_per_share
        
        # Boot (cash) is taxable up to gain
        per_share_basis = acquired_cost_basis / acquired_shares
        gain_per_share = cash_per_share + (exchange_ratio * 0)  # simplified
        
        # Recognize gain to extent of boot received
        recognized_gain = min(cash_proceeds, cash_proceeds - (per_share_basis * acquired_shares * (cash_per_share / (cash_per_share + exchange_ratio))))
        
        return {
            "new_symbol": acquirer_symbol,
            "new_shares": new_shares,
            "new_basis": acquired_cost_basis - cash_proceeds + recognized_gain,
            "realized_gain": recognized_gain,
            "is_taxable": True
        }


# =============================================================================
# Corporate Action Detection
# =============================================================================

def identify_corporate_actions(df: pd.DataFrame) -> List[CorporateAction]:
    """
    Identify all corporate actions in a transaction DataFrame.
    
    Args:
        df: Transaction DataFrame
        
    Returns:
        List of CorporateAction objects
    """
    actions = []
    
    for _, row in df.iterrows():
        action_type = row["Action"]
        
        if action_type == "StockSplit":
            actions.append(CorporateAction(
                action_type="split",
                date=row["Date"],
                symbol=row["Symbol"],
                account=row["Account"],
                split_ratio=row["Quantity"],  # Shares received
                description=row.get("Note", "")
            ))
        
        elif action_type == "ReverseStockSplit":
            actions.append(CorporateAction(
                action_type="reverse_split",
                date=row["Date"],
                symbol=row["Symbol"],
                account=row["Account"],
                split_ratio=1 / row["Quantity"] if row["Quantity"] > 0 else 1,
                description=row.get("Note", "")
            ))
        
        elif action_type == "SpinOff":
            # Extract CUSIP if in note
            cusip = ""
            note = row.get("Note", "")
            if "CUSIP:" in note:
                cusip = note.split("CUSIP:")[1].strip().split()[0]
            
            actions.append(CorporateAction(
                action_type="spinoff",
                date=row["Date"],
                symbol=row["Symbol"],
                account=row["Account"],
                shares_received=row["Quantity"],
                cusip=cusip,
                description=note
            ))
        
        elif action_type in ["MergerOut", "MergerCash"]:
            actions.append(CorporateAction(
                action_type="merger",
                date=row["Date"],
                symbol=row["Symbol"],
                account=row["Account"],
                exchange_ratio=row["Quantity"] if action_type == "MergerOut" else 0,
                cash_per_share=row["Amount"] if action_type == "MergerCash" else 0,
                description=row.get("Note", "")
            ))
        
        elif action_type == "Liquidation":
            actions.append(CorporateAction(
                action_type="liquidation",
                date=row["Date"],
                symbol=row["Symbol"],
                account=row["Account"],
                cash_per_share=row["Amount"],
                description=row.get("Note", "")
            ))
    
    return actions


def generate_corporate_actions_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate a summary report of all corporate actions.
    
    Args:
        df: Transaction DataFrame
        
    Returns:
        DataFrame with corporate actions summary
    """
    actions = identify_corporate_actions(df)
    
    if not actions:
        return pd.DataFrame()
    
    report_data = []
    for action in actions:
        report_data.append({
            "Date": action.date,
            "Type": action.action_type.replace("_", " ").title(),
            "Symbol": action.symbol,
            "Account": action.account,
            "Details": action.description or f"Ratio: {action.split_ratio}" if action.action_type == "split" else "",
            "Shares": action.shares_received or action.exchange_ratio or "",
            "Cash": action.cash_per_share if action.cash_per_share else "",
            "CUSIP": action.cusip
        })
    
    report_df = pd.DataFrame(report_data)
    report_df = report_df.sort_values("Date", ascending=False)
    
    return report_df


# =============================================================================
# Quality Checks for Corporate Actions
# =============================================================================

def check_spinoff_basis_allocation(
    df: pd.DataFrame,
    holdings_df: pd.DataFrame
) -> List[Dict[str, Any]]:
    """
    Check for spin-offs that may need cost basis adjustment.
    
    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame
        
    Returns:
        List of issues/recommendations
    """
    issues = []
    
    # Find all spin-offs
    spinoffs = df[df["Action"] == "SpinOff"]
    
    for _, spinoff in spinoffs.iterrows():
        symbol = spinoff["Symbol"]
        
        # Check if we have allocation info
        if symbol in KNOWN_SPINOFFS:
            allocation = KNOWN_SPINOFFS[symbol]
            if allocation.spinoff_allocation_pct > 0:
                issues.append({
                    "severity": "info",
                    "symbol": symbol,
                    "message": f"Spin-off {symbol} from {allocation.parent_symbol}: "
                              f"{allocation.spinoff_allocation_pct:.1f}% of parent basis should be allocated",
                    "action_needed": "Review cost basis allocation"
                })
        else:
            # Unknown spin-off
            issues.append({
                "severity": "warning", 
                "symbol": symbol,
                "message": f"Unknown spin-off {symbol}: Cost basis allocation unknown",
                "action_needed": "Research basis allocation for tax reporting"
            })
    
    return issues


def check_unprocessed_mergers(
    df: pd.DataFrame,
    holdings_df: pd.DataFrame
) -> List[Dict[str, Any]]:
    """
    Check for merger-related issues.
    
    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame
        
    Returns:
        List of issues/recommendations
    """
    issues = []
    
    # Find merger transactions
    mergers = df[df["Action"].isin(["MergerOut", "MergerCash"])]
    
    # Group by symbol and date to find complete merger events
    for (symbol, date), group in mergers.groupby(["Symbol", "Date"]):
        has_out = "MergerOut" in group["Action"].values
        has_cash = "MergerCash" in group["Action"].values
        
        # Check if we have info about this merger
        if symbol in KNOWN_MERGERS:
            acquirer, ratio, cash = KNOWN_MERGERS[symbol]
            
            if ratio > 0 and has_out:
                issues.append({
                    "severity": "info",
                    "symbol": symbol,
                    "message": f"Merger: {symbol} → {acquirer} at {ratio}:1 ratio",
                    "action_needed": "Verify basis transferred correctly"
                })
            elif cash > 0 and has_cash:
                issues.append({
                    "severity": "info",
                    "symbol": symbol,
                    "message": f"Cash merger: {symbol} at ${cash}/share",
                    "action_needed": "Verify gain/loss calculated correctly"
                })
        else:
            issues.append({
                "severity": "warning",
                "symbol": symbol,
                "message": f"Unknown merger for {symbol} on {date}",
                "action_needed": "Research merger terms for accurate basis calculation"
            })
    
    return issues


# =============================================================================
# Integration with Quality Module
# =============================================================================

def check_corporate_actions(
    df: pd.DataFrame,
    holdings_df: pd.DataFrame = None
) -> List[Any]:
    """
    Run all corporate action quality checks.
    
    Args:
        df: Transaction DataFrame
        holdings_df: Holdings DataFrame (optional)
        
    Returns:
        List of QualityIssue objects
    """
    from .quality import QualityIssue
    
    issues = []
    
    # Check spin-offs
    spinoff_issues = check_spinoff_basis_allocation(df, holdings_df)
    for issue in spinoff_issues:
        issues.append(QualityIssue(
            severity=issue["severity"],
            category="corporate_action",
            message=issue["message"],
            details={"symbol": issue["symbol"], "action": issue.get("action_needed", "")}
        ))
    
    # Check mergers
    merger_issues = check_unprocessed_mergers(df, holdings_df)
    for issue in merger_issues:
        issues.append(QualityIssue(
            severity=issue["severity"],
            category="corporate_action", 
            message=issue["message"],
            details={"symbol": issue["symbol"], "action": issue.get("action_needed", "")}
        ))
    
    return issues
