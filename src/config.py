"""All constants, paths, and broker mappings."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
#
# The defaults resolve relative to the repo root.  Tests and advanced
# users override via env vars (FIN_PROJECT_ROOT / FIN_DATA_DIR /
# FIN_CACHE_DIR / FIN_EXPORT_DIR) so the pipeline can point at a
# tmp directory during an end-to-end test run without rewriting source.

_ENV_ROOT = os.environ.get("FIN_PROJECT_ROOT")
PROJECT_ROOT = Path(_ENV_ROOT).resolve() if _ENV_ROOT else (
    Path(__file__).resolve().parent.parent
)


def _resolve(env_var: str, default: Path) -> Path:
    override = os.environ.get(env_var)
    return Path(override).resolve() if override else default


DATA_DIR   = _resolve("FIN_DATA_DIR",   PROJECT_ROOT / "data")
CACHE_DIR  = _resolve("FIN_CACHE_DIR",  PROJECT_ROOT / "cache")
EXPORT_DIR = _resolve("FIN_EXPORT_DIR", PROJECT_ROOT / "exports")

# ---------------------------------------------------------------------------
# Broker detection: parser name → canonical file prefix
# ---------------------------------------------------------------------------

CANONICAL_PREFIXES = {
    "robinhood": "robinhood",
    "coinbase": "coinbase",
    "coinbase_pro": "coinbase-pro-gdax",
    "schwab_rollover": "schwab-rollover-ira",
    "schwab_roth": "schwab-roth-ira",
    "vanguard_401k": "vanguard-401k",
    "voya_401k": "voya-401k",
    "usaa": "usaa-roth-ira",
    "apple_savings": "apple-savings",
}

# Files that should never be renamed
SKIP_RENAME = {"manual-adjustments.csv", "metadata.csv", "retirement-data.csv"}

# ---------------------------------------------------------------------------
# Account grouping & typing — populated by data/metadata.csv
# ---------------------------------------------------------------------------
#
# Both dicts start empty.  At pipeline startup (step 4b-pre in
# src/main.py), parse_metadata reads `Account Group` and `Account Type`
# rows from data/metadata.csv and mutates these dicts in place.  Every
# downstream module imports them by reference, so the user's overrides
# propagate everywhere without any plumbing.
#
# These mappings are inherently user-specific (which 401K rolled into
# which IRA, what you want to call your Coinbase wallets, etc.) — they
# don't belong in source.  See data/metadata.csv (or
# samples/portfolio.snapshot.json for an example) for the row format.
#
# Fallbacks when an entry is missing:
#   ACCOUNT_GROUPS.get(origin, origin)   — group defaults to the raw
#                                          broker account name
#   ACCOUNT_TYPES.get(group, "Taxable")  — type defaults to Taxable

ACCOUNT_GROUPS: dict[str, str] = {}
ACCOUNT_TYPES:  dict[str, str] = {}

# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

# Crypto tickers that should get a -USD suffix for price lookup / display
CRYPTO_SYMBOLS = {
    "ADA", "BAT", "BOBA", "BTC", "CBETH", "ETH", "ETH2", "FLR",
    "LUNA", "MATIC", "OMG", "OP", "REP", "USDC",
    "XLM", "XRP", "ZEC", "ZRX",
    # NOTE: "QTUM" intentionally excluded.  It's both a crypto ticker
    # and Defiance Quantum ETF; for this user's data it's always the
    # ETF (Robinhood holdings with dividends, lending rebates, etc.).
    # If you trade QTUM crypto, re-add it and distinguish at parse time.
}

# Explicit remap (applied before the crypto-suffix rule)
SYMBOL_MAP = {
    "ETH2": "ETH-USD",
}

# Symbols that are really just cash (value = 1 per unit)
CASH_SYMBOLS = {"USD"}
