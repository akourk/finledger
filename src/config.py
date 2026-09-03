"""All constants, paths, and broker mappings."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
#
# The defaults resolve relative to the repo root.  Tests and advanced
# users override via env vars (FIN_PROJECT_ROOT / FIN_DATA_DIR /
# FIN_CACHE_DIR / FIN_EXPORT_DIR) so the pipeline can point at a
# tmp directory during an end-to-end test run without rewriting source.

_ENV_ROOT = os.environ.get("FIN_PROJECT_ROOT")
PROJECT_ROOT = Path(_ENV_ROOT).resolve() if _ENV_ROOT else (
    Path(__file__).resolve().parent.parent
)


def _resolve(env_var: str, default: Path) -> Path:
    override = os.environ.get(env_var)
    return Path(override).resolve() if override else default


def load_json_cache(path, default):
    """Load a JSON cache file, or return ``default`` when it is absent.

    A CORRUPT file raises with the path in the message.  These caches
    are documented as hand-editable and safe to delete, so the user is
    actively invited to edit exactly the files whose failure mode was a
    bare ``JSONDecodeError: Expecting value: line 1 column 11`` naming
    neither the file nor the cache it belongs to.

    Deliberately still RAISES rather than degrading to ``default``.  A
    silently-empty price-coverage sidecar would claim nothing is cached
    and trigger a full refetch; a silently-empty rename map would mis-key
    every renamed symbol.  Loud is the right failure here — anonymous is
    not.  (Individual price SHARDS take the opposite trade and skip with
    a named note, because one unreadable symbol should not stop a run.)
    """
    import json as _json
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.exists():
        return default
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return _json.load(fh)
    except _json.JSONDecodeError as exc:
        raise ValueError(
            f"{p} is not valid JSON ({exc}).  This cache is hand-editable "
            f"and safe to delete — remove it and the next run rebuilds it."
        ) from exc


DATA_DIR   = _resolve("FIN_DATA_DIR",   PROJECT_ROOT / "data")
CACHE_DIR  = _resolve("FIN_CACHE_DIR",  PROJECT_ROOT / "cache")
EXPORT_DIR = _resolve("FIN_EXPORT_DIR", PROJECT_ROOT / "exports")

# ---------------------------------------------------------------------------
# Broker detection: parser name → canonical file prefix
# ---------------------------------------------------------------------------

CANONICAL_PREFIXES = {
    "robinhood": "robinhood",
    "robinhood_apex": "robinhood-apex",
    "coinbase": "coinbase",
    "coinbase_pro": "coinbase-pro-gdax",
    "schwab_rollover": "schwab-rollover-ira",
    "schwab_roth": "schwab-roth-ira",
    "vanguard_401k": "vanguard-401k",
    "voya_401k": "voya-401k",
    "usaa": "usaa-roth-ira",
    "apple_savings": "apple-savings",
    "sfcu": "state-farm-fcu",
}

# Files that should never be renamed
SKIP_RENAME = {"manual-adjustments.csv", "metadata.csv", "retirement-data.csv"}


def contract_multiplier(symbol: str) -> float:
    """Valuation multiplier for a symbol's quantity × price product.

    Equity option contracts control 100 shares: fin stores quantity in
    CONTRACTS and price as the PER-SHARE premium (matching broker CSVs
    and how brokers quote), so valuing a position needs the ×100
    contract multiplier.  Cost basis is unaffected — it comes from the
    txn ``amount``, which is already the full cash paid.  Without this,
    every open option was valued 100x low (a $1,065 contract showed as
    $10.65 of value against $1,065 of basis — a phantom unrealized
    loss).  Everything else is ×1.
    """
    s = symbol or ""
    return 100.0 if (" Call " in s or " Put " in s) else 1.0

# ---------------------------------------------------------------------------
# Account grouping & typing — populated by data/metadata.csv
# ---------------------------------------------------------------------------
#
# Both dicts start empty.  At pipeline startup (step 4b-pre in
# src/main.py), parse_metadata reads `Account Group` and `Account Type`
# rows from data/metadata.csv and mutates these dicts in place.  Every
# downstream module imports them by reference, so the user's overrides
# propagate everywhere without any plumbing.
#
# These mappings are inherently user-specific (which 401K rolled into
# which IRA, what you want to call your Coinbase wallets, etc.) — they
# don't belong in source.  See data/metadata.csv (or
# samples/portfolio.snapshot.json for an example) for the row format.
#
# Fallbacks when an entry is missing:
#   ACCOUNT_GROUPS.get(origin, origin)   — group defaults to the raw
#                                          broker account name
#   ACCOUNT_TYPES.get(group, "Taxable")  — type defaults to Taxable

ACCOUNT_GROUPS: dict[str, str] = {}
ACCOUNT_TYPES:  dict[str, str] = {}

# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

# Crypto tickers that should get a -USD suffix for price lookup / display
CRYPTO_SYMBOLS = {
    "ADA", "BAT", "BOBA", "BTC", "CBETH", "ETH", "ETH2", "FLR",
    "LUNA", "MATIC", "OMG", "OP", "REP", "USDC",
    "XLM", "XRP", "ZEC", "ZRX",
    # NOTE: "QTUM" intentionally excluded.  It's both a crypto ticker
    # and Defiance Quantum ETF; for this user's data it's always the
    # ETF (Robinhood holdings with dividends, lending rebates, etc.).
    # If you trade QTUM crypto, re-add it and distinguish at parse time.
}

# Explicit remap (applied before the crypto-suffix rule)
SYMBOL_MAP = {
    "ETH2": "ETH-USD",
}

# Symbols that are really just cash (value = 1 per unit)
CASH_SYMBOLS = {"USD"}

# Benchmark tickers for the Performance tab / Overview overlay.
# SPY = US large-cap, BND = US aggregate bond, VXUS = international
# ex-US.  Priced as total return so the comparison against the user's
# TWR is apples-to-apples (see prices._is_total_return_symbol).
#
# Their price series must run through TODAY regardless of whether the
# user holds them, so they are exempt from the closed-position fetch
# clamp and the trivial-symbol filter (see pipeline_stages.
# compute_position_endings).  Without that exemption, buying and then
# selling a benchmark ticker turns it into a "closed position", clamps
# its fetch range to the sell date, and silently freezes the benchmark
# line at zero from there on — which is exactly what happened to VXUS.
BENCHMARK_SYMBOLS: tuple[str, ...] = ("SPY", "BND", "VXUS")
