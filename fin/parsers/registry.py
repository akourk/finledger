"""
Parser Registry
===============
Source detection and parser registration.
"""

import pathlib
from typing import Callable, Optional

import pandas as pd

from fin.parsers.apple_savings import parse_apple_savings
from fin.parsers.coinbase import parse_coinbase
from fin.parsers.coinbase_pro import parse_coinbase_pro
from fin.parsers.custom import parse_custom_input
from fin.parsers.robinhood import parse_robinhood
from fin.parsers.schwab import parse_schwab
from fin.parsers.usaa import parse_usaa_victory_capital
from fin.parsers.vanguard import parse_vanguard_sf401k
from fin.parsers.voya import parse_voya_atos401k

# Maps source identifiers to their parser functions
PARSER_REGISTRY: dict[str, Callable[[pathlib.Path], pd.DataFrame]] = {
    "schwab": parse_schwab,
    "robinhood": parse_robinhood,
    "coinbase": parse_coinbase,
    "coinbase_pro": parse_coinbase_pro,
    "vanguard_sf401k": parse_vanguard_sf401k,
    "usaa_victory_capital": parse_usaa_victory_capital,
    "voya_atos401k": parse_voya_atos401k,
    "apple_savings": parse_apple_savings,
    "custom_input": parse_custom_input,
}


def detect_source(file_path: pathlib.Path) -> Optional[str]:
    """
    Detect the data source based on filename and file content.
    Returns source identifier string or None if unknown.
    """
    filename = file_path.name.lower()

    # Skip retirement data file - it's not a transaction file
    if "retirement-data" in filename or "retirement_data" in filename:
        return None

    # Check filename patterns first (faster)
    if "apple-savings" in filename:
        return "apple_savings"

    if "usaavictorycapital" in filename or filename == "mutualfund.csv":
        return "usaa_victory_capital"

    if "vanguardsf" in filename or filename == "vanguardsf401k.csv":
        return "vanguard_sf401k"

    if "schwab" in filename or "rollover_ira" in filename or "roth_contributory_ira" in filename:
        return "schwab"

    # Check for Coinbase Pro/GDAX before regular Coinbase (more specific match first)
    if "coinbase-pro" in filename or "gdax" in filename:
        return "coinbase_pro"

    if "coinbase" in filename:
        return "coinbase"

    if "custominput" in filename:
        return "custom_input"

    if "robinhood" in filename:
        return "robinhood"

    if "voyaatos401k" in filename:
        return "voya_atos401k"

    # Check file content for additional identification
    try:
        content = file_path.read_text(encoding="utf-8")

        # Check for Robinhood signature at end of file
        if content[-30:-21] == "Robinhood":
            return "robinhood"

        # Check for Voya/Atos 401k header
        if "Atos 401(k)" in content[:50]:
            return "voya_atos401k"

        # Check first line for Coinbase metadata
        first_lines = content.split("\n")[:5]
        if any("Transactions" in line for line in first_lines):
            return "coinbase"

    except Exception as e:
        print(f"Warning: Could not read file content for detection: {e}")

    return None
