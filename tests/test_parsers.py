"""
Tests for Parser Module
=======================
"""
import pytest
import pandas as pd
from pathlib import Path
from fin.parsers.base import normalize_amounts, standardize_symbols, create_empty_dataframe
from fin.config import UNIFIED_COLUMNS


class TestParserBase:
    """Test base parser functionality."""
    
    def test_create_empty_dataframe(self):
        """Test empty DataFrame creation with correct schema."""
        df = create_empty_dataframe()
        assert list(df.columns) == UNIFIED_COLUMNS
        assert len(df) == 0
    
    def test_normalize_amounts_buy(self):
        """Test amount normalization for Buy action."""
        df = pd.DataFrame({
            'Date': ['2024-01-01'],
            'Account': ['Test'],
            'Symbol': ['AAPL'],
            'Action': ['Buy'],
            'Quantity': [10],
            'Price': [150.0],
            'Fee': [0],
            'Amount': [-1500.0],  # Negative amount (cost)
            'Currency': ['USD'],
            'Note': [''],
            'Source': ['Test']
        })
        
        result = normalize_amounts(df)
        assert result.loc[0, 'Amount'] == 1500.0  # Should be positive
    
    def test_normalize_amounts_sell(self):
        """Test amount normalization for Sell action."""
        df = pd.DataFrame({
            'Date': ['2024-01-01'],
            'Account': ['Test'],
            'Symbol': ['AAPL'],
            'Action': ['Sell'],
            'Quantity': [10],
            'Price': [200.0],
            'Fee': [0],
            'Amount': [2000.0],
            'Currency': ['USD'],
            'Note': [''],
            'Source': ['Test']
        })
        
        result = normalize_amounts(df)
        assert result.loc[0, 'Amount'] == 2000.0  # Should remain positive
    
    def test_standardize_symbols(self):
        """Test symbol standardization using SYMBOL_MAP."""
        df = pd.DataFrame({
            'Date': ['2024-01-01'],
            'Account': ['Test'],
            'Symbol': ['BRK.B'],  # Should be converted to BRK-B
            'Action': ['Buy'],
            'Quantity': [1],
            'Price': [400.0],
            'Fee': [0],
            'Amount': [400.0],
            'Currency': ['USD'],
            'Note': [''],
            'Source': ['Test']
        })
        
        result = standardize_symbols(df)
        assert result.loc[0, 'Symbol'] == 'BRK-B'


class TestParserRegistry:
    """Test parser registry and detection."""
    
    def test_detect_schwab(self):
        """Test detection of Schwab files."""
        from fin.parsers.registry import detect_source
        
        # Mock file with schwab in name
        class MockPath:
            def __init__(self, name):
                self.name = name
            def read_text(self, encoding='utf-8'):
                return ""
        
        file_path = MockPath('schwabrolloverira-2023-01-23to2024-12-13.csv')
        assert detect_source(file_path) == 'schwab'
    
    def test_detect_robinhood(self):
        """Test detection of Robinhood files."""
        from fin.parsers.registry import detect_source
        
        class MockPath:
            def __init__(self, name):
                self.name = name
            def read_text(self, encoding='utf-8'):
                return ""
        
        file_path = MockPath('robinhood-2018-11-09to2025-11-20.csv')
        assert detect_source(file_path) == 'robinhood'
    
    def test_detect_unknown(self):
        """Test detection returns None for unknown files."""
        from fin.parsers.registry import detect_source
        
        class MockPath:
            def __init__(self, name):
                self.name = name
            def read_text(self, encoding='utf-8'):
                return ""
        
        file_path = MockPath('unknown-source.csv')
        assert detect_source(file_path) is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
