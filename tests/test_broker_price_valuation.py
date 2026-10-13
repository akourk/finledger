"""Dated fictional broker observations obey every valuation price contract."""

from copy import deepcopy
import csv
from datetime import date, datetime, timezone
import json
from unittest.mock import Mock

import pytest


@pytest.fixture
def broker_market(isolated_workdir, monkeypatch):
    from src import prices

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-20")
    monkeypatch.setattr(prices, "_lazy_yf", Mock(side_effect=AssertionError("network forbidden")))
    monkeypatch.setattr(prices, "_now_utc",
                        lambda: datetime(2024, 6, 20, 23, tzinfo=timezone.utc))
    monkeypatch.setattr(prices, "_today", lambda: date(2024, 6, 20))
    return prices


def _write_broker(prices, rows):
    from src.config import DATA_DIR

    with (DATA_DIR / "broker-prices.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Symbol", "Price"])
        writer.writerows(rows)
    prices.reload_broker_prices()


def _buy(day, symbol="FAKEA", price=50.0, quantity=2.0):
    return {"date": day, "account": "Robinhood", "account_group": "Robinhood",
            "account_type": "Taxable", "symbol": symbol, "action": "Buy",
            "raw_action": "Buy", "quantity": quantity, "price": price,
            "amount": quantity * price, "fees": 0.0, "description": "fictional purchase",
            "source": "fictional.csv"}


def test_actual_quotes_choose_newest_date_and_broker_wins_ties(broker_market):
    prices = broker_market
    prices._load_prices()["FAKEA"] = {
        "2024-06-17": 100.0, "2024-06-19": 120.0, "2024-06-20": 130.0}
    _write_broker(prices, [("2024-06-18", "FAKEA", 110.0),
                           ("2024-06-20", "FAKEA", 125.0)])
    assert prices.get_price("FAKEA", "2024-06-17") == 100.0
    assert prices.get_price("FAKEA", "2024-06-18") == 110.0
    assert prices.get_price("FAKEA", "2024-06-19") == 120.0
    assert prices.get_price("FAKEA", "2024-06-20") == 125.0
    observation = prices.get_price_observation("FAKEA", "2024-06-20")
    assert observation["date"] == "2024-06-20"
    assert observation["estimated"] is False
    from src.price_provenance import describe_pricing
    assert describe_pricing("FAKEA", "2024-06-20", "cache")["source"] == "broker"


@pytest.mark.parametrize("symbol", ["FAKEA", "Example Private Fund", "FAKE^",
                                   "FAKEA 12/20/2024 Call $100.00"])
def test_broker_marks_price_unfetchable_symbols_without_future_or_unbounded_fill(broker_market, symbol):
    prices = broker_market
    _write_broker(prices, [("2024-06-14", symbol, 80.0), ("2024-06-25", symbol, 90.0)])
    assert prices.get_price(symbol, "2024-06-13") is None
    assert prices.get_price(symbol, "2024-06-14") == 80.0
    assert prices.get_price(symbol, "2024-06-21") == 80.0
    assert prices.get_price(symbol, "2024-06-22") is None
    assert prices.get_price(symbol, "2024-06-24") is None
    assert prices.get_price(symbol, "2024-06-25") == 90.0


@pytest.mark.parametrize("ratio", [2.0, 0.5])
def test_observation_share_units_match_current_and_historical_marks(broker_market, ratio):
    prices = broker_market
    from src.valuation import mark

    prices._load_splits()["FAKEA"] = [["2024-06-17", ratio]]
    _write_broker(prices, [("2024-06-14", "FAKEA", 100.0),
                           ("2024-06-18", "FAKEA", 110.0 / ratio)])
    assert prices.get_price("FAKEA", "2024-06-14") == pytest.approx(100.0 / ratio)
    # Historical quantity is in that date's units; mark restates it once.
    historical = mark("FAKEA", 10.0, "2024-06-14")
    assert historical.qty == pytest.approx(10.0 * ratio)
    assert historical.value == pytest.approx(1000.0)
    # A broker observation before the split remains comparable after the split.
    current = mark("FAKEA", 10.0 * ratio, "2024-06-17", restate_qty=False)
    assert current.value == pytest.approx(1000.0)
    newer = mark("FAKEA", 10.0 * ratio, "2024-06-18", restate_qty=False)
    assert newer.value == pytest.approx(1100.0)
    assert prices.get_price_observation("FAKEA", "2024-06-14")["date"] == "2024-06-14"


def test_direct_alias_uses_its_own_observation_and_underlying_split_units(broker_market):
    prices = broker_market
    from src.valuation import mark

    prices._load_proxy_map()["ALIAS"] = {"proxy": "FAKEA", "method": "direct"}
    prices._load_prices()["FAKEA"] = {"2024-06-14": 50.0}
    prices._load_splits()["FAKEA"] = [["2024-06-19", 2.0]]
    _write_broker(prices, [("2024-06-18", "ALIAS", 110.0)])
    assert prices.get_price("ALIAS", "2024-06-18") == 55.0
    assert mark("ALIAS", 10.0, "2024-06-18").value == 1100.0
    assert mark("ALIAS", 20.0, "2024-06-20", restate_qty=False).value == 1100.0


def test_fresh_own_fund_observation_beats_newer_scaled_estimate_until_it_expires(broker_market):
    prices = broker_market
    from src.price_provenance import describe_pricing

    prices._load_proxy_map()["Example Fund"] = {
        "proxy": "FAKEA", "method": "scaled", "anchor_date": "2024-06-14",
        "anchor_price": 50.0}
    prices._load_prices()["FAKEA"] = {
        "2024-06-14": 100.0, "2024-06-19": 120.0, "2024-06-26": 140.0}
    _write_broker(prices, [("2024-06-18", "Example Fund", 55.0)])
    assert prices.get_price("Example Fund", "2024-06-19") == 55.0
    assert describe_pricing("Example Fund", "2024-06-19", "cache")["source"] == "broker"
    assert prices.get_price("Example Fund", "2024-06-26") == 70.0
    assert describe_pricing("Example Fund", "2024-06-26", "cache")["source"] == "proxy"


def test_broker_observation_of_proxy_does_not_make_the_fund_estimate_actual(broker_market):
    prices = broker_market
    from src.price_provenance import describe_pricing

    prices._load_proxy_map()["Example Fund"] = {
        "proxy": "FAKEA", "method": "scaled", "anchor_date": "2024-06-14",
        "anchor_price": 50.0}
    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0}
    _write_broker(prices, [("2024-06-19", "FAKEA", 130.0)])
    assert prices.get_price("Example Fund", "2024-06-19") == 65.0
    result = describe_pricing("Example Fund", "2024-06-19", "cache")
    assert result["source"] == "proxy"
    assert result["date"] == "2024-06-19"


@pytest.mark.parametrize("direct_alias", [False, True])
def test_future_broker_proxy_anchor_cannot_change_an_earlier_fund_estimate(broker_market, direct_alias):
    prices = broker_market
    proxy = "ALIAS" if direct_alias else "FAKEA"
    prices._load_proxy_map()["Example Fund"] = {
        "proxy": proxy, "method": "scaled", "anchor_date": "2024-06-20",
        "anchor_price": 50.0}
    if direct_alias:
        prices._load_proxy_map()["ALIAS"] = {"proxy": "FAKEA", "method": "direct"}
    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-20": 120.0}
    before = prices.get_price("Example Fund", "2024-06-14")
    assert before == pytest.approx(50.0 * 100.0 / 120.0)
    _write_broker(prices, [("2024-06-20", "FAKEA", 240.0)])
    assert prices.get_price("FAKEA", "2024-06-20") == 240.0
    assert prices.get_price("Example Fund", "2024-06-14") == before


def test_broker_benchmark_observation_retains_total_return_adjustment(broker_market):
    prices = broker_market
    prices._load_prices()["SPY"] = {"2024-06-14": 100.0, "2024-06-17": 99.0}
    prices._load_dividends()["SPY"] = [["2024-06-17", 1.0]]
    _write_broker(prices, [("2024-06-14", "SPY", 100.0)])
    assert prices.get_price("SPY", "2024-06-14") == pytest.approx(99.0)
    assert prices.get_series("SPY", "2024-06-14", "2024-06-17") == {
        "2024-06-14": pytest.approx(99.0), "2024-06-17": 99.0}


def test_scaled_proxy_returns_remain_consistent_with_broker_dividend_adjustment(broker_market):
    prices = broker_market
    prices._load_proxy_map()["Example Fund"] = {
        "proxy": "FAKEA", "method": "scaled", "anchor_date": "2024-06-14",
        "anchor_price": 50.0}
    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-17": 99.0}
    prices._load_dividends()["FAKEA"] = [["2024-06-17", 1.0]]
    _write_broker(prices, [("2024-06-14", "FAKEA", 100.0)])
    assert prices.get_price("Example Fund", "2024-06-17") == pytest.approx(50.0)


def test_option_broker_premium_is_kept_by_kernel_and_current_holdings_floor(broker_market):
    prices = broker_market
    from src.valuation import mark

    symbol = "FAKEA 12/20/2024 Call $100.00"
    prices._load_prices()["FAKEA"] = {"2024-06-20": 120.0}
    _write_broker(prices, [("2024-06-20", symbol, 18.0)])
    assert prices.option_intrinsic(symbol, "2024-06-20") == 20.0
    valued = mark(symbol, 2.0, "2024-06-20", {symbol: 3.0})
    assert valued.price == 18.0 and valued.value == 3600.0
    current_prices = {symbol: prices.get_price(symbol, "2024-06-20")}
    assert prices.apply_option_intrinsic_floor(current_prices, [symbol], "2024-06-20") == 0
    assert current_prices[symbol] == 18.0


def test_broker_underlying_price_restores_historical_option_strike_units(broker_market):
    prices = broker_market
    from src.valuation import mark

    prices._load_splits()["FAKEA"] = [["2024-06-17", 2.0]]
    _write_broker(prices, [("2024-06-14", "FAKEA", 120.0)])
    symbol = "FAKEA 12/20/2024 Call $100.00"
    assert prices.get_price("FAKEA", "2024-06-14") == 60.0
    assert prices.option_intrinsic(symbol, "2024-06-14") == 20.0
    assert mark(symbol, 2.0, "2024-06-14", {symbol: 3.0}).value == 4000.0


def test_explicit_zero_underlying_can_price_put_intrinsic(broker_market):
    prices = broker_market
    from src.valuation import mark

    _write_broker(prices, [("2024-06-20", "FAKEA", 0.0)])
    put = "FAKEA 12/20/2024 Put $100.00"
    call = "FAKEA 12/20/2024 Call $100.00"
    assert prices.option_intrinsic(put, "2024-06-20") == 100.0
    assert prices.option_intrinsic(call, "2024-06-20") == 0.0
    assert mark(put, 2.0, "2024-06-20", {put: 3.0}).value == 20000.0
    current_prices = {put: 3.0}
    assert prices.apply_option_intrinsic_floor(current_prices, [put], "2024-06-20") == 1
    assert current_prices[put] == 100.0


@pytest.mark.parametrize("symbol", ["FAKEA", "FAKEA 12/20/2024 Call $100.00"])
def test_explicit_zero_is_a_known_value_in_holdings_and_history(broker_market, symbol):
    prices = broker_market
    from src.history import compute_history
    from src.pipeline_stages import build_holdings
    from src.price_provenance import annotate_holdings, summarize_pricing
    from src.valuation import mark

    prices._load_prices()["FAKEA"] = {"2024-06-20": 120.0} if "Call" in symbol else {}
    _write_broker(prices, [("2024-06-20", symbol, 0.0)])
    quote = prices.get_price(symbol, "2024-06-20")
    assert quote == 0.0
    marked = mark(symbol, 2.0, "2024-06-20", {symbol: 50.0})
    assert marked.price == 0.0 and marked.value == 0.0 and marked.priced
    current_prices = {symbol: quote}
    prices.apply_option_intrinsic_floor(current_prices, [symbol], "2024-06-20")
    holdings, account_rows = build_holdings(
        {("Robinhood", symbol): 2.0}, current_prices,
        {("Robinhood", symbol): 100.0}, {})
    txns = [_buy("2024-06-01", symbol=symbol)]
    annotate_holdings(holdings, account_rows, txns, "2024-06-20")
    for row in (holdings[0], account_rows[0]):
        assert row["value"] == 0.0 and row["quantity"] == 2.0
        assert row["cost_basis"] == 100.0
        assert row["pricing"]["source"] == "broker"
    assert summarize_pricing(account_rows)["total"]["unavailable"] == 0
    latest = compute_history(txns, {symbol: "Other"})[-1]
    position = next(row for row in latest["positions"] if row["symbol"] == symbol)
    assert position["value"] == 0.0 and position["quantity"] == 2.0


def test_unknown_price_stays_unavailable_when_zero_is_supported(broker_market):
    from src.pipeline_stages import build_holdings

    holdings, accounts = build_holdings(
        {("Robinhood", "FAKEA"): 2.0}, {}, {("Robinhood", "FAKEA"): 100.0}, {})
    assert holdings[0]["value"] is None and accounts[0]["value"] is None


@pytest.mark.parametrize("symbol,multiplier", [
    ("FAKEA", 1), ("FAKEA 12/20/2024 Call $100.00", 100),
])
@pytest.mark.parametrize("known_zero", [True, False])
def test_zero_lot_value_and_window_loss_remain_distinct_from_missing_marks(
        broker_market, symbol, multiplier, known_zero):
    prices = broker_market
    from src.analytics.lots import compute_open_lots, open_lot_rows, price_lookup
    from src.analytics.position_pnl import compute_position_pnl
    from src.pipeline_stages import build_holdings

    _write_broker(prices, [("2024-06-19", symbol, 50.0 / multiplier),
                           ("2024-06-20", symbol, 0.0)])
    state = {"lots": {("Robinhood", symbol): [
        {"date": "2024-06-01", "qty": 2.0, "basis_per_share": 50.0},
    ]}}
    _, accounts = build_holdings(
        {("Robinhood", symbol): 2.0}, {symbol: 0.0} if known_zero else {},
        {("Robinhood", symbol): 100.0}, {})
    lot, = open_lot_rows(state, price_lookup(accounts))
    assert lot["value"] == (0.0 if known_zero else None)
    assert lot["cost_basis"] == 100.0
    assert lot["unrealized_gain"] == (-100.0 if known_zero else None)
    grouped = compute_open_lots(state, accounts)["positions"][0]
    assert grouped["cost_basis"] == 100.0 and grouped["quantity"] == 2.0
    if known_zero:
        assert grouped["micro"]["value"] == 0.0
    pnl = compute_position_pnl(state, accounts, [], as_of="2024-06-20")
    for result in (pnl["by_account"][0], pnl["by_symbol"][0]):
        assert result["value"] == (0.0 if known_zero else None)
        assert result["windows"]["1d"] == (
            {"pnl": -100.0, "pct": -100.0, "start_value": 100.0}
            if known_zero else None)


def test_history_keeps_original_basis_and_excludes_future_broker_observations(broker_market):
    prices = broker_market
    from src.basis import compute_basis_default
    from src.history import compute_history

    transactions = [_buy("2024-06-01")]
    before_basis = compute_basis_default(deepcopy(transactions))
    _write_broker(prices, [("2024-06-19", "FAKEA", 80.0),
                           ("2024-06-21", "FAKEA", 900.0)])
    snapshots = compute_history(transactions, {"FAKEA": "Other"})
    earlier = next(row for row in snapshots if row["date"] == "2024-06-15")
    position = next(row for row in earlier["positions"] if row["symbol"] == "FAKEA")
    assert position["value"] == 100.0 and position["cost_basis"] == 100.0
    assert position["pricing"]["source"] == "transaction"
    today = next(row for row in snapshots if row["date"] == "2024-06-20")
    position = next(row for row in today["positions"] if row["symbol"] == "FAKEA")
    assert position["value"] == 160.0 and position["cost_basis"] == 100.0
    assert position["pricing"]["source"] == "broker"
    assert position["pricing"]["date"] == "2024-06-19"
    assert compute_basis_default(deepcopy(transactions)) == before_basis
    assert len(transactions) == 1


def test_get_series_includes_actual_broker_observations_without_filled_dates(broker_market):
    prices = broker_market
    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-17": 110.0}
    _write_broker(prices, [("2024-06-14", "FAKEA", 105.0),
                           ("2024-06-19", "FAKEA", 120.0),
                           ("2024-06-21", "FAKEA", 900.0)])
    assert prices.get_series("FAKEA", "2024-06-14", "2024-06-20") == {
        "2024-06-14": 105.0, "2024-06-17": 110.0, "2024-06-19": 120.0}


def test_daily_pnl_uses_broker_observation_dates_and_current_share_units(broker_market):
    prices = broker_market
    from src.analytics.daily_pnl import compute_daily_pnl

    prices._load_splits()["FAKEA"] = [["2024-06-17", 2.0]]
    _write_broker(prices, [("2024-06-14", "FAKEA", 100.0),
                           ("2024-06-17", "FAKEA", 55.0),
                           ("2024-06-20", "FAKEA", 60.0)])
    history = [{"date": "2024-06-20", "total": 1200.0, "positions": [
        {"symbol": "FAKEA", "quantity": 20.0, "value": 1200.0,
         "account_group": "Robinhood", "cost_basis": 1000.0},
    ]}]
    result = compute_daily_pnl(history, [], window_days=6)
    assert [(row["date"], row["value"], row["change"]) for row in result] == [
        ("2024-06-17", 1100.0, 100.0), ("2024-06-20", 1200.0, 100.0)]


def test_broker_only_header_shows_observation_date_without_missing_market_evidence(broker_market):
    prices = broker_market
    from src.analytics.header import compute_header_summary
    from src.history import compute_history

    _write_broker(prices, [("2024-06-19", "Example Fund", 80.0)])
    transactions = [_buy("2024-06-01", symbol="Example Fund")]
    history = compute_history(transactions, {"Example Fund": "Other"})
    summary = compute_header_summary(transactions, history, [])
    assert summary["prices_quote_date"] == "2024-06-19"
    assert summary["prices_as_of"] is None
    assert summary["prices_market_required"] is False
    assert summary["prices_freshness_unknown"] is False


def test_broker_lookup_does_not_mutate_market_cache_or_refresh_evidence(broker_market):
    prices = broker_market
    # Settle legacy metadata migration before recording the baseline.
    prices._load_prices()
    prices._load_meta()["symbols"]["FAKEA"] = {
        "failure_count": 2, "retry_after": "2024-06-25",
        "last_error": "fictional provider outage", "last_attempt": "2024-06-19T00:00:00"}
    before_prices = deepcopy(prices._load_prices())
    before_meta = deepcopy(prices._load_meta())
    _write_broker(prices, [("2024-06-20", "FAKEA", 80.0)])
    assert prices.get_price("FAKEA", "2024-06-20") == 80.0
    assert prices._load_prices() == before_prices
    assert prices._load_meta() == before_meta
    assert prices.last_fetch_at("FAKEA") is None
    assert prices.price_refresh_report() == []
    assert not prices._prices_dirty_syms
    prices.save_caches()
    assert not prices._shard_path("FAKEA").exists()
    if prices.PRICE_META_FILE.exists():
        assert json.loads(prices.PRICE_META_FILE.read_text(encoding="utf-8")) == before_meta


def test_reload_and_cache_reset_read_new_broker_file_without_changing_market_shards(broker_market):
    prices = broker_market
    prices._load_prices()["FAKEB"] = {"2024-06-20": 40.0}
    existing = prices._load_prices()
    _write_broker(prices, [("2024-06-20", "FAKEA", 80.0)])
    assert prices.get_price("FAKEA", "2024-06-20") == 80.0
    _write_broker(prices, [("2024-06-20", "FAKEA", 90.0)])
    assert prices.get_price("FAKEA", "2024-06-20") == 90.0
    assert prices._load_prices() is existing
    prices.reset_caches()
    assert prices.get_price("FAKEA", "2024-06-20") == 90.0
