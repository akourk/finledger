"""
Command Line Interface Module
=============================
Argument parsing and CLI commands for the financial aggregator.
"""

import argparse
import sys
import logging
from pathlib import Path
from typing import Optional

__version__ = "1.0.0"


def setup_logging(verbose: bool = False) -> None:
    """
    Configure logging based on verbosity level.
    
    Args:
        verbose: If True, set DEBUG level; otherwise INFO
    """
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Suppress noisy loggers
    logging.getLogger('yfinance').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def create_parser() -> argparse.ArgumentParser:
    """
    Create and configure the argument parser.
    
    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog='fin',
        description='Financial Portfolio Aggregator - Process transaction data from multiple sources',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all files with default settings
  python detectAndClean.py
  
  # Verbose output for debugging
  python detectAndClean.py --verbose
  
  # Custom input/output paths
  python detectAndClean.py --input /path/to/csvs --output /path/to/reports
  
  # Skip specific reports
  python detectAndClean.py --no-dashboard
  
  # Show version
  python detectAndClean.py --version
  
For more information, visit: https://github.com/akourk/fin
        """
    )
    
    # Version
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )
    
    # Verbosity
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output (DEBUG level logging)'
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Minimize output (only show errors)'
    )
    
    # Paths
    parser.add_argument(
        '--input',
        type=Path,
        metavar='PATH',
        help='Input directory containing CSV files (default: data/input)'
    )
    
    parser.add_argument(
        '--output',
        type=Path,
        metavar='PATH',
        help='Output directory for reports (default: data/output)'
    )
    
    # Processing options
    parser.add_argument(
        '--parallel',
        action='store_true',
        default=True,
        help='Enable parallel file processing (default: enabled)'
    )
    
    parser.add_argument(
        '--no-parallel',
        dest='parallel',
        action='store_false',
        help='Disable parallel file processing'
    )
    
    parser.add_argument(
        '--workers',
        type=int,
        default=4,
        metavar='N',
        help='Number of parallel workers (default: 4)'
    )
    
    # Report options
    parser.add_argument(
        '--no-dashboard',
        action='store_true',
        help='Skip dashboard data generation'
    )
    
    parser.add_argument(
        '--quality-check',
        action='store_true',
        default=True,
        help='Run data quality checks (default: enabled)'
    )
    
    parser.add_argument(
        '--no-quality-check',
        dest='quality_check',
        action='store_false',
        help='Skip data quality checks'
    )
    
    # Cache options
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear all caches before processing'
    )
    
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable cache usage (fetch all prices fresh)'
    )
    
    # Specific reports
    parser.add_argument(
        '--report',
        choices=['all', 'holdings', 'cost-basis', 'income', 'tax', 'historical'],
        default='all',
        help='Generate specific report only (default: all)'
    )
    
    return parser


def parse_args(args: Optional[list] = None) -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Args:
        args: List of arguments to parse (defaults to sys.argv)
    
    Returns:
        Parsed arguments namespace
    """
    parser = create_parser()
    parsed = parser.parse_args(args)
    
    # Validate arguments
    if parsed.quiet and parsed.verbose:
        parser.error("Cannot use both --quiet and --verbose")
    
    if parsed.workers < 1:
        parser.error("--workers must be at least 1")
    
    return parsed


def print_banner(verbose: bool = False) -> None:
    """
    Print application banner.
    
    Args:
        verbose: If True, print detailed banner
    """
    print("\n" + "="*60)
    print("Financial Portfolio Aggregator")
    print(f"Version {__version__}")
    print("="*60 + "\n")
    
    if verbose:
        print("Configuration:")
        print("  Parallel Processing: Enabled")
        print("  Quality Checks: Enabled")
        print("  Logging Level: DEBUG")
        print()
