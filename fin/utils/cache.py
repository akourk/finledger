"""
Cache Management Module
=======================
Functions for loading and saving various caches (prices, splits, sectors, etc.)
"""

import json
from fin.config import (
    PRICE_CACHE_PATH, SPLIT_CACHE_PATH, COST_BASIS_CACHE_PATH,
    HISTORICAL_HOLDINGS_CACHE_PATH, SECTOR_CACHE_PATH
)


def load_price_cache() -> dict:
    """Load price cache from disk."""
    if PRICE_CACHE_PATH.exists():
        try:
            with open(PRICE_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_price_cache(cache: dict) -> None:
    """Save price cache to disk."""
    with open(PRICE_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_split_cache() -> dict:
    """Load split cache from disk."""
    if SPLIT_CACHE_PATH.exists():
        try:
            with open(SPLIT_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_split_cache(cache: dict) -> None:
    """Save split cache to disk."""
    with open(SPLIT_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_cost_basis_cache() -> dict:
    """Load cost basis cache from disk."""
    if COST_BASIS_CACHE_PATH.exists():
        try:
            with open(COST_BASIS_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cost_basis_cache(cache: dict) -> None:
    """Save cost basis cache to disk."""
    with open(COST_BASIS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_historical_holdings_cache() -> dict:
    """Load historical holdings cache from disk."""
    if HISTORICAL_HOLDINGS_CACHE_PATH.exists():
        try:
            with open(HISTORICAL_HOLDINGS_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_historical_holdings_cache(cache: dict) -> None:
    """Save historical holdings cache to disk."""
    with open(HISTORICAL_HOLDINGS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_sector_cache() -> dict:
    """Load sector cache from disk."""
    if SECTOR_CACHE_PATH.exists():
        try:
            with open(SECTOR_CACHE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_sector_cache(cache: dict) -> None:
    """Save sector cache to disk."""
    with open(SECTOR_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
