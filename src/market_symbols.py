"""Public market-data identifiers for option underlying quotes.

These mappings affect quote lookup only. Contract roots remain unchanged in
the ledger, option grouping, and tax classification. The scale converts the
quoted index level into the units used by the contract's strike; it does not
replace the option contract multiplier.

References:
https://www.cboe.com/tradable_products/sp_500/spx_options/specifications
https://www.cboe.com/tradable_products/sp_500/mini_spx_options/specifications
https://www.nasdaq.com/NDXP-factsheet
https://finance.yahoo.com/quote/%5EGSPC/
https://finance.yahoo.com/quote/%5ENDX/
"""


_INDEX_OPTION_QUOTES: dict[str, tuple[str, float]] = {
    "SPX": ("^GSPC", 1.0),
    "SPXW": ("^GSPC", 1.0),
    "XSP": ("^GSPC", 0.1),
    "NDX": ("^NDX", 1.0),
    "NDXP": ("^NDX", 1.0),
}


def option_underlying_quote(root: str) -> tuple[str, float]:
    """Return the quote ticker and strike-unit scale for an option root.

    Ordinary equity roots retain their own quote ticker and share units.
    Call only for parsed option underlyings; this is not a general security
    rename or a replacement for corporate-action accounting.
    """
    return _INDEX_OPTION_QUOTES.get(root, (root, 1.0))
