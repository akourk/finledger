"""
Cache Management Module
=======================
Functions for loading and saving various caches (prices, splits, sectors, etc.)
"""

import json
import logging
from typing import Dict, Any
from pathlib import Path

from fin.config import (
    PRICE_CACHE_PATH, SPLIT_CACHE_PATH, COST_BASIS_CACHE_PATH,
    HISTORICAL_HOLDINGS_CACHE_PATH, SECTOR_CACHE_PATH
)

logger = logging.getLogger(__name__)


def load_price_cache() -> Dict[str, Dict[str, float]]:
    """
    Load price cache from disk.
    
    Returns:
        Nested dict of {symbol: {date: price}}
    """
    if PRICE_CACHE_PATH.exists():
        try:
            with open(PRICE_CACHE_PATH, "r") as f:
                cache = json.load(f)
                logger.debug(f"Loaded price cache with {len(cache)} symbols")
                return cache
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse price cache JSON: {e}")
            return {}
        except IOError as e:
            logger.error(f"Failed to read price cache file: {e}")
            return {}
    logger.debug("No existing price cache found")
    return {}


def save_price_cache(cache: Dict[str, Dict[str, float]]) -> None:
    """
    Save price cache to disk.
    
    Args:
        cache: Nested dict of {symbol: {date: price}}
    """
    try:
        with open(PRICE_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        logger.debug(f"Saved price cache with {len(cache)} symbols")
    except IOError as e:
        logger.error(f"Failed to save price cache: {e}")
        raise


def load_split_cache() -> Dict[str, Dict[str, float]]:
    """
    Load split cache from disk.
    
    Returns:
        Nested dict of {symbol: {date: split_ratio}}
    """
    if SPLIT_CACHE_PATH.exists():
        try:
            with open(SPLIT_CACHE_PATH, "r") as f:
                cache = json.load(f)
                logger.debug(f"Loaded split cache with {len(cache)} symbols")
                return cache
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse split cache JSON: {e}")
            return {}
        except IOError as e:
            logger.error(f"Failed to read split cache file: {e}")
            return {}
    logger.debug("No existing split cache found")
    return {}


def save_split_cache(cache: Dict[str, Dict[str, float]]) -> None:
    """
    Save split cache to disk.
    
    Args:
        cache: Nested dict of {symbol: {date: split_ratio}}
    """
    try:
        with open(SPLIT_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        logger.debug(f"Saved split cache with {len(cache)} symbols")
    except IOError as e:
        logger.error(f"Failed to save split cache: {e}")
        raise


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


def load_sector_cache() -> Dict[str, str]:
    """
    Load sector cache from disk.
    
    Returns:
        Dict of {symbol: sector}
    """
    if SECTOR_CACHE_PATH.exists():
        try:
            with open(SECTOR_CACHE_PATH, "r") as f:
                cache = json.load(f)
                logger.debug(f"Loaded sector cache with {len(cache)} symbols")
                return cache
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse sector cache JSON: {e}")
            return {}
        except IOError as e:
            logger.error(f"Failed to read sector cache file: {e}")
            return {}
    logger.debug("No existing sector cache found")
    return {}


def save_sector_cache(cache: Dict[str, str]) -> None:
    """
    Save sector cache to disk.
    
    Args:
        cache: Dict of {symbol: sector}
    """
    try:
        with open(SECTOR_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        logger.debug(f"Saved sector cache with {len(cache)} symbols")
    except IOError as e:
        logger.error(f"Failed to save sector cache: {e}")
        raise
