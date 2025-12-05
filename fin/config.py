"""
Configuration Module
====================
All constants, mappings, and path configurations for the financial aggregator.
"""

import pathlib

# =============================================================================
# Path Configuration
# =============================================================================

# Base paths - resolve relative to this file's parent (the 'fin' package is inside the project root)
PACKAGE_PATH = pathlib.Path(__file__).resolve().parent
PROJECT_PATH = PACKAGE_PATH.parent
DATA_PATH = PROJECT_PATH / "data"
DATA_INPUT_PATH = DATA_PATH / "input"
DATA_OUTPUT_PATH = DATA_PATH / "output"
DASHBOARD_PATH = PROJECT_PATH / "dashboard"
DASHBOARD_DATA_PATH = DASHBOARD_PATH / "data"

# Ensure directories exist
DATA_PATH.mkdir(parents=True, exist_ok=True)
DATA_INPUT_PATH.mkdir(parents=True, exist_ok=True)
DATA_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
DASHBOARD_PATH.mkdir(parents=True, exist_ok=True)
DASHBOARD_DATA_PATH.mkdir(parents=True, exist_ok=True)

# Cache file paths
PRICE_CACHE_PATH = DATA_PATH / "price_cache.json"
SPLIT_CACHE_PATH = DATA_PATH / "split_cache.json"
SPLIT_CACHE_METADATA_PATH = DATA_PATH / "split_cache_metadata.json"
COST_BASIS_CACHE_PATH = DATA_PATH / "cost_basis_cache.json"
HISTORICAL_HOLDINGS_CACHE_PATH = DATA_PATH / "historical_holdings_cache.json"
SECTOR_CACHE_PATH = DATA_PATH / "sector_cache.json"
UNAVAILABLE_TICKER_CACHE_PATH = DATA_PATH / "unavailable_ticker_cache.json"

# Split cache refresh interval (days) - tickers not checked in this many days will be refreshed
SPLIT_CACHE_REFRESH_DAYS = 30

# =============================================================================
# Unified Output Schema
# =============================================================================

# All parsers must output DataFrames with these columns
UNIFIED_COLUMNS = [
    "Date",  # YYYY-MM-DD format
    "Account",  # Source account name
    "Symbol",  # Ticker symbol
    "Action",  # Standardized: Buy, Sell, Dividend, Transfer, Interest, Fee, Staking, Contribution
    "Quantity",  # Number of shares/units
    "Price",  # Unit price
    "Fee",  # Transaction fees
    "Amount",  # Total transaction amount
    "Currency",  # Currency code (USD, etc.)
    "Note",  # Additional details
    "Source",  # Original data source identifier
]

# =============================================================================
# Action Type Standardization
# =============================================================================

ACTION_MAP = {
    # Buy actions (stocks)
    "buy": "Buy",
    "reinvest shares": "Buy",
    "reinvest dividend": "Buy",
    "long term cap gain reinvest": "Buy",
    "short term cap gain reinvest": "Buy",
    "contribution": "Buy",
    # Sell actions (stocks)
    "sell": "Sell",
    # Options actions
    "bto": "OptionBuy",  # Robinhood: Buy to Open (options)
    "stc": "OptionSell",  # Robinhood: Sell to Close (options)
    # Dividend/Income actions
    "dividend": "Dividend",
    "cdiv": "Dividend",
    "cash div": "Dividend",
    "qualified dividend": "Dividend",
    "staking income": "Staking",
    "staking": "Staking",
    "reward income": "Staking",
    # Transfer actions
    "funds received": "Transfer",
    "moneylink transfer": "Transfer",
    "security transfer": "Transfer",
    "wire funds received": "Transfer",
    "ira rollover cont": "Transfer",
    "send": "Transfer",
    "receive": "Transfer",
    "convert": "Transfer",
    "conv": "Transfer",  # Robinhood: Conversion
    "ach": "Transfer",  # Robinhood: ACH transfer
    "deposit": "Transfer",
    "withdrawal": "Transfer",
    "exchange deposit": "Transfer",
    "exchange withdrawal": "Transfer",
    "pro deposit": "Transfer",
    "pro withdrawal": "Transfer",
    "vault withdrawal": "Transfer",
    "wrap asset": "Transfer",
    "unwrap": "Transfer",
    "retail staking transfer": "Transfer",
    "retail unstaking transfer": "Transfer",
    "retail eth2 deprecation": "Transfer",
    # Interest
    "bank interest": "Interest",
    "interest": "Interest",
    "int": "Interest",  # Robinhood: Interest
    "subscription rebates": "Interest",
    # Fees
    "fee": "Fee",
    # Tax
    "dtax": "Tax",  # Robinhood: Dividend tax withholding
    # Stock lending
    "slip": "StockLending",
    # Options
    "oexp": "OptionsExpiration",  # Robinhood: Options expiration
    # Stock splits/corporate actions
    "spl": "StockSplit",
    "stock split": "StockSplit",
    "soff": "SpinOff",
    "spr": "ReverseStockSplit",
    "sxch": "Exchange",
    # Mergers & Acquisitions
    "mrgc": "MergerCash",  # Robinhood: Cash received from merger/acquisition
    "mrgs": "MergerOut",  # Robinhood: Shares removed or received in merger
    "liq": "Liquidation",
    "cil": "CashInLieu",  # Fractional share cash payment
    "rec": "Receive",
}

# =============================================================================
# Symbol Mappings
# =============================================================================

# Symbol name to ticker mapping (for funds that use full names instead of tickers)
SYMBOL_MAP = {
    "VANG WELLINGTON ADM": "VWENX",
    "VANG INTL GROWTH ADM": "VWILX",
    "VANG INST TR 2055": "VIVLX",
    "VANG US GROWTH ADM": "VWUAX",
    # Crypto symbols that need -USD suffix for yfinance
    "ETH": "ETH-USD",
    "ETH2": "ETH-USD",
    "CBETH": "CBETH-USD",
    "BTC": "BTC-USD",
    "ADA": "ADA-USD",
    "BOBA": "BOBA-USD",
    "OMG": "OMG-USD",
    "MATIC": "MATIC-USD",
    "ZRX": "ZRX-USD",
    "ZEC": "ZEC-USD",
    "REP": "REP-USD",
    "XLM": "XLM-USD",
    "XRP": "XRP-USD",
    # Stock symbols that need adjustment for yfinance
    "BRK.B": "BRK-B",
}

# Symbols that require price-based quantity conversion
# Format: {source_symbol: target_ticker}
# These symbols will have their quantities recalculated as: quantity = amount / target_ticker_price
PRICE_CONVERSION_MAP = {
    "VANG TR II 2055": "VFFVX",  # Vanguard Target Retirement 2055 Trust II → VFFVX equivalent
}

# =============================================================================
# Account Mappings
# =============================================================================

# Account merge mapping (for accounts that were transferred/rolled over)
# Format: {old_account_name: new_account_name}
ACCOUNT_MERGE_MAP = {
    "Voya Atos 401K": "Schwab Rollover IRA",
    "USAA Victory Capital Roth IRA": "Schwab Roth Contributory IRA",
}

# =============================================================================
# Delisted/Acquired Symbols
# =============================================================================

# Delisted/acquired symbols - skip price lookups for these
# These companies were acquired, merged, or delisted and no longer trade
DELISTED_SYMBOLS = {
    "XLNX",  # Xilinx - acquired by AMD (Feb 2022)
    "TWTR",  # Twitter - taken private by Elon Musk (Oct 2022)
    "TPTX",  # Turning Point Therapeutics - acquired by Bristol-Myers (Aug 2022)
    "VRNA",  # Verona Pharma - acquired by Merck (Oct 2025)
    "EYEN",  # Eyenovia - delisted
    "FREQ",  # Frequency Therapeutics - delisted
    "FREQ^",  # Frequency Therapeutics preferred - delisted
    "S",  # Sprint - merged with T-Mobile (Apr 2020)
    "GME+",  # GameStop units - converted
}

# =============================================================================
# Retirement/Contribution Limits
# =============================================================================

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

# 401k contribution limits by year
LIMIT_401K = {
    2019: 19000,
    2020: 19500,
    2021: 19500,
    2022: 20500,
    2023: 22500,
    2024: 23000,
    2025: 23500,
}
