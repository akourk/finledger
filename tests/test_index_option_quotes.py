"""Index option roots must fetch valid quotes and retain contract units.

All dates, strikes, and price observations are independently fictional.
"""

import pytest


@pytest.mark.parametrize(
    ("root", "target", "scale"),
    [
        ("SPX", "^GSPC", 1.0),
        ("SPXW", "^GSPC", 1.0),
        ("XSP", "^GSPC", 0.1),
        ("NDX", "^NDX", 1.0),
        ("NDXP", "^NDX", 1.0),
        ("EXAMPLE", "EXAMPLE", 1.0),
    ],
)
def test_option_quote_identifiers(root, target, scale):
    from src.market_symbols import option_underlying_quote

    assert option_underlying_quote(root) == (target, scale)


def test_fetch_dependencies_dedupe_actual_indices(isolated_workdir):
    from src.prices import option_underlyings

    assert option_underlyings([
        "SPX 06/20/2025 Call $4,900.00",
        "SPXW 06/20/2025 Put $5,100.00",
        "XSP 06/20/2025 Call $490.00",
        "NDX 06/20/2025 Call $19,000.00",
        "NDXP 06/20/2025 Put $21,000.00",
        "EXAMPLE 06/20/2025 Call $90.00",
        "ORDINARY",
    ]) == {"^GSPC", "^NDX", "EXAMPLE"}


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("SPX 06/20/2025 Call $4,900.00", 100.0),
        ("SPXW 06/20/2025 Put $5,100.00", 100.0),
        ("XSP 06/20/2025 Call $490.00", 10.0),
        ("XSP 06/20/2025 Put $510.00", 10.0),
        ("XSP 06/20/2025 Call $510.00", 0.0),
        ("NDX 06/20/2025 Call $19,000.00", 1000.0),
        ("NDXP 06/20/2025 Put $21,000.00", 1000.0),
    ],
)
def test_intrinsic_uses_index_level_in_strike_units(
        isolated_workdir, symbol, expected):
    from src import prices

    prices._load_prices().update({
        "^GSPC": {"2025-06-18": 5000.0},
        "^NDX": {"2025-06-18": 20000.0},
    })
    prices._load_splits().update({"^GSPC": [], "^NDX": []})
    assert prices.option_intrinsic(symbol, "2025-06-18") == pytest.approx(expected)


def test_intrinsic_preserves_equity_split_adjustment(isolated_workdir):
    from src import prices

    prices._load_prices()["EXAMPLE"] = {"2025-06-18": 50.0}
    prices._load_splits()["EXAMPLE"] = [["2025-07-01", 2.0]]
    assert prices.option_intrinsic(
        "EXAMPLE 06/20/2025 Call $90.00", "2025-06-18"
    ) == pytest.approx(10.0)


def test_historical_intrinsic_reads_the_requested_date(isolated_workdir):
    from src import prices

    symbol = "XSP 06/20/2025 Call $490.00"
    prices._load_prices()["^GSPC"] = {
        "2025-06-18": 5000.0,
        "2025-07-01": 8000.0,
    }
    prices._load_splits()["^GSPC"] = []
    # The contract has since expired, but its held-date valuation still
    # needs the earlier index observation, not today's index level.
    assert prices.option_intrinsic(symbol, "2025-06-18") == pytest.approx(10.0)
    assert prices.option_intrinsic(symbol, "2025-06-17") is None
    assert prices.parse_option_symbol(symbol) == {
        "underlying": "XSP", "expiry": "2025-06-20",
        "type": "Call", "strike": 490.0,
    }


def test_current_mark_keeps_option_contract_multiplier(isolated_workdir):
    from src import prices, valuation

    symbol = "XSP 06/20/2025 Call $490.00"
    prices._load_prices()["^GSPC"] = {"2025-06-18": 5000.0}
    prices._load_splits()["^GSPC"] = []
    marked = valuation.mark(symbol, 2.0, "2025-06-18", {symbol: 3.0})
    assert marked.price == pytest.approx(10.0)
    assert marked.value == pytest.approx(2000.0)
    assert marked.source == "intrinsic"


def test_freshness_follows_the_actual_quote_cache(isolated_workdir):
    from src import prices

    symbol = "XSP 06/20/2025 Call $490.00"
    prices._load_meta()["symbols"]["^GSPC"] = {
        "last_fetch": "2025-06-18T18:00:00",
    }
    assert prices.price_source_symbol(symbol) == "^GSPC"
    assert prices.last_fetch_at(symbol) == "2025-06-18T18:00:00"
    # Naming the contract root for quote lookup must not rename holdings.
    assert prices.price_source_symbol("ORDINARY") == "ORDINARY"
