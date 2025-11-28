"""
Tests for Holdings Calculator
=============================
"""
import pytest
import pandas as pd
from fin.calculators.holdings import get_signed_quantity, calculate_holdings


class TestHoldingsCalculator:
    """Test holdings calculation logic."""
    
    def test_get_signed_quantity_buy(self):
        """Test Buy action returns positive quantity."""
        row = pd.Series({
            'Action': 'Buy',
            'Quantity': 100,
            'Amount': 1500.0,
            'Source': 'Test',
            'Note': ''
        })
        assert get_signed_quantity(row) == 100
    
    def test_get_signed_quantity_sell(self):
        """Test Sell action returns negative quantity."""
        row = pd.Series({
            'Action': 'Sell',
            'Quantity': 50,
            'Amount': 800.0,
            'Source': 'Test',
            'Note': ''
        })
        assert get_signed_quantity(row) == -50
    
    def test_get_signed_quantity_dividend_cash(self):
        """Test Dividend with no quantity returns 0."""
        row = pd.Series({
            'Action': 'Dividend',
            'Quantity': 0,
            'Amount': 25.0,
            'Source': 'Schwab',
            'Note': 'Cash dividend'
        })
        assert get_signed_quantity(row) == 0
    
    def test_get_signed_quantity_stock_split(self):
        """Test Stock Split returns 0 (already adjusted)."""
        row = pd.Series({
            'Action': 'StockSplit',
            'Quantity': 100,  # Split shares
            'Amount': 0,
            'Source': 'Test',
            'Note': '2:1 split'
        })
        assert get_signed_quantity(row) == 0
    
    def test_calculate_holdings_simple(self):
        """Test basic holdings calculation."""
        df = pd.DataFrame({
            'Date': ['2024-01-01', '2024-01-02'],
            'Account': ['Test', 'Test'],
            'Symbol': ['AAPL', 'AAPL'],
            'Action': ['Buy', 'Buy'],
            'Quantity': [100, 50],
            'Price': [150.0, 155.0],
            'Fee': [0, 0],
            'Amount': [15000.0, 7750.0],
            'Currency': ['USD', 'USD'],
            'Note': ['', ''],
            'Source': ['Test', 'Test']
        })
        
        holdings = calculate_holdings(df, {})
        assert len(holdings) == 1
        assert holdings.loc[0, 'Quantity'] == 150
        assert holdings.loc[0, 'Symbol'] == 'AAPL'
    
    def test_calculate_holdings_buy_sell(self):
        """Test holdings with buy and sell transactions."""
        df = pd.DataFrame({
            'Date': ['2024-01-01', '2024-01-02', '2024-01-03'],
            'Account': ['Test', 'Test', 'Test'],
            'Symbol': ['AAPL', 'AAPL', 'AAPL'],
            'Action': ['Buy', 'Buy', 'Sell'],
            'Quantity': [100, 50, 30],
            'Price': [150.0, 155.0, 160.0],
            'Fee': [0, 0, 0],
            'Amount': [15000.0, 7750.0, 4800.0],
            'Currency': ['USD', 'USD', 'USD'],
            'Note': ['', '', ''],
            'Source': ['Test', 'Test', 'Test']
        })
        
        holdings = calculate_holdings(df, {})
        assert len(holdings) == 1
        assert holdings.loc[0, 'Quantity'] == 120  # 100 + 50 - 30


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
