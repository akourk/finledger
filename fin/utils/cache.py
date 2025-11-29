"""
Cache Management Module
=======================
Functions for loading and saving various caches (prices, splits, sectors, etc.)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fin.config import (
    COST_BASIS_CACHE_PATH,
    HISTORICAL_HOLDINGS_CACHE_PATH,
    PRICE_CACHE_PATH,
    SECTOR_CACHE_PATH,
    SPLIT_CACHE_PATH,
    UNAVAILABLE_TICKER_CACHE_PATH,
)

logger = logging.getLogger(__name__)


def load_price_cache() -> Dict[str, Dict[str, float]]:
    """
    Load price cache from disk.

    Returns:
        Nested dict of {symbol: {date: price, '_split_count': count, '_last_validated': timestamp}}

        Note: '_split_count' tracks how many splits were known when prices were cached.
        If a new split occurs, all cached prices for that symbol should be invalidated.
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


def load_unavailable_ticker_cache() -> Dict[str, Dict[str, str]]:
    """
    Load unavailable ticker cache from disk.
    This tracks tickers that don't have historical data available (e.g., not yet listed).

    Returns:
        Dict of {ticker: {date: "unavailable"}}
    """
    if UNAVAILABLE_TICKER_CACHE_PATH.exists():
        try:
            with open(UNAVAILABLE_TICKER_CACHE_PATH, "r") as f:
                cache = json.load(f)
                logger.debug(f"Loaded unavailable ticker cache with {len(cache)} tickers")
                return cache
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse unavailable ticker cache JSON: {e}")
            return {}
        except IOError as e:
            logger.error(f"Failed to read unavailable ticker cache file: {e}")
            return {}
    logger.debug("No existing unavailable ticker cache found")
    return {}


def save_unavailable_ticker_cache(cache: Dict[str, Dict[str, str]]) -> None:
    """
    Save unavailable ticker cache to disk.

    Args:
        cache: Dict of {ticker: {date: "unavailable"}}
    """
    try:
        with open(UNAVAILABLE_TICKER_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        logger.debug(f"Saved unavailable ticker cache with {len(cache)} tickers")
    except IOError as e:
        logger.error(f"Failed to save unavailable ticker cache: {e}")
        raise


def validate_price_cache_against_splits(
    price_cache: Dict[str, Dict[str, float]], split_cache: Dict[str, Dict[str, float]]
) -> int:
    """
    Validate price cache against split cache and invalidate stale prices.

    When a stock splits, yfinance retroactively adjusts all historical prices.
    If we detect a new split (split_count increased), we must invalidate all
    cached prices for that symbol.

    Args:
        price_cache: Price cache dictionary (modified in-place)
        split_cache: Split cache dictionary

    Returns:
        Number of symbols with invalidated caches
    """
    invalidated_count = 0
    symbols_to_invalidate = []

    for symbol in list(price_cache.keys()):
        if symbol.startswith("_"):  # Skip metadata keys
            continue

        # Get current split count from split cache
        current_split_count = len(split_cache.get(symbol, {}))

        # Get cached split count (when prices were last fetched)
        cached_split_count = price_cache[symbol].get("_split_count", 0)

        # If split count changed, invalidate all cached prices for this symbol
        if current_split_count != cached_split_count:
            logger.info(
                f"Split detected for {symbol}: {cached_split_count} -> {current_split_count} splits. Invalidating cached prices."
            )
            symbols_to_invalidate.append(symbol)
            invalidated_count += 1

    # Remove invalidated symbols
    for symbol in symbols_to_invalidate:
        del price_cache[symbol]
        print(f"   Invalidated price cache for {symbol} due to stock split")

    return invalidated_count
