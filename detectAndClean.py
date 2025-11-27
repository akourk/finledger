#!/usr/bin/env python3
"""
Transaction Data Aggregator - Entry Point
==========================================
This is the main entry point for the transaction data aggregator.
The implementation is in the `fin` package.

Usage:
    python detectAndClean.py

This script reads transaction history files from multiple financial sources
(Schwab, Robinhood, Coinbase, Vanguard, etc.), normalizes them to a unified
format, calculates holdings and cost basis, and generates comprehensive reports.

For module-level access, import from the fin package:
    from fin import main
    from fin.parsers import detect_source, PARSER_REGISTRY
    from fin.calculators import calculate_holdings, calculate_cost_basis
    from fin.reports import generate_portfolio_summary
"""

from fin.main import main

if __name__ == "__main__":
    main()
