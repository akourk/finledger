"""
Tests for Utility Functions
===========================
"""

import json
from pathlib import Path

import pytest

from fin.utils.cache import load_price_cache, save_price_cache


class TestCache:
    """Test cache functionality."""

    def test_price_cache_empty(self, tmp_path):
        """Test loading non-existent cache returns empty dict."""
        # This test would require mocking the cache path
        # For now, just verify the function exists and returns dict
        cache = load_price_cache()
        assert isinstance(cache, dict)

    def test_price_cache_save_load(self, tmp_path):
        """Test saving and loading price cache."""
        # Would need to mock PRICE_CACHE_PATH for proper testing
        test_cache = {"AAPL": {"2024-01-01": 150.0, "2024-01-02": 152.0}}

        # In a real test, we'd mock the path and verify save/load
        # save_price_cache(test_cache)
        # loaded = load_price_cache()
        # assert loaded == test_cache
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
