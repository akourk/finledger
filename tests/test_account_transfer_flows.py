"""Fictional in-kind transfers cross selected accounts, not the portfolio."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest


def _txn(group, action, *, day="2024-06-01", symbol="TEST", qty=2,
         price=100, amount=None, **extra):
    from src.basis import txn_external_cash_flow

    row = {"date": day, "account": group, "account_group": group,
           "account_type": "Taxable", "symbol": symbol, "action": action,
           "raw_action": action, "quantity": qty, "price": price,
           "amount": qty if amount is None else amount, "fees": 0,
           "source": "fictional-transfer-fixture", "description": "", **extra}
    row["cash_flow"] = txn_external_cash_flow(row)
    return row


def _pair(**kwargs):
    return [_txn("Example A", "Transfer Out", **kwargs),
            _txn("Example B", "Transfer In", **kwargs)]


@pytest.fixture
def market(isolated_workdir, monkeypatch):
    """Use deterministic local marks, without consulting any price store."""
    from src import valuation
    from src.config import ACCOUNT_TYPES

    ACCOUNT_TYPES.update({"Example A": "Taxable", "Example B": "Taxable"})
    prices = {"TEST": 100.0}
    monkeypatch.setattr(valuation, "get_price", lambda symbol, day: prices.get(symbol))
    monkeypatch.setattr(valuation, "split_factor_since", lambda symbol, day: 1.0)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda symbol, day: None)
    return prices


def _funded_case():
    rows = [_txn(group, "Contribution", day="2024-01-01", qty=10, amount=1000)
            for group in ("Example A", "Example B")]
    rows += _pair()
    history = []
    for day, a, b in [("2024-01-01", 1000, 1000),
                      ("2024-06-01", 800, 1200),
                      ("2024-12-31", 800, 1200)]:
        history.append({"date": day, "total": a + b,
                        "by_account_group": {"Example A": a, "Example B": b},
                        "by_account_type": {"Taxable": a + b},
                        "by_sector": {}, "positions": [], "net_contributed": 2000})
    return rows, history


@pytest.mark.parametrize("cached", [None, 50.0])
def test_split_receipt_fallback_uses_quote_date_units(market, monkeypatch, cached):
    from src import prices, valuation
    from src.return_flows import annotate_account_transfers

    market["TEST"] = cached
    for module in (prices, valuation):
        monkeypatch.setattr(module, "split_factor_since",
                            lambda symbol, day: 2.0 if day < "2024-06-03" else 1.0)
    rows = [_txn("Example A", "Buy", day="2024-05-01", qty=10, price=100),
            _txn("Example A", "Transfer Out", day="2024-06-01", qty=10, price=0),
            _txn("Example B", "Transfer In", day="2024-06-05", qty=20, price=0)]
    annotate_account_transfers(rows)
    assert rows[1]["account_transfer"]["flow"] == -1000
    assert rows[2]["account_transfer"]["flow"] == 1000


@pytest.mark.parametrize("groups, expected", [
    (None, [0, 0]), (set(), [0, 0]),
    ({"Example A"}, [-200, 0]), ({"Example B"}, [0, 200]),
    ({"Example A", "Example B"}, [0, 0]),
    ({"Example A", "Other"}, [-200, 0]),
])
def test_selected_scope_requires_exactly_one_endpoint(market, groups, expected):
    from src.return_flows import annotate_account_transfers, txn_cash_flow_for_groups

    rows = _pair(amount=2, cost_basis=17)
    original = deepcopy(rows)
    annotate_account_transfers(rows)
    assert [txn_cash_flow_for_groups(t, groups) for t in rows] == expected
    for row, before in zip(rows, original):
        assert {k: v for k, v in row.items() if k != "account_transfer"} == before


@pytest.mark.parametrize("groups", [
    None, {"Example A"}, {"Example B"}, {"Example A", "Example B"},
])
def test_transfer_is_not_return_in_annual_twr_xirr_or_daily_twr(market, groups):
    from src.analytics._shared import (
        compute_annual_returns, compute_money_weighted_return,
        compute_twr_daily_summary, compute_twr_summary,
    )
    from src.return_flows import annotate_account_transfers

    rows, history = _funded_case()
    if groups in ({"Example A"}, {"Example B"}):
        # The prior portfolio-only classification books the moved asset as
        # a loss for the sender and a gain for the recipient.
        before = compute_twr_summary(rows, history, [], groups)
        assert before["cumulative"] == pytest.approx(-0.2 if "Example A" in groups else 0.2)
        assert compute_money_weighted_return(rows, history, [], groups)["annualized"] != 0
    annotate_account_transfers(rows)
    annual = compute_annual_returns(rows, history, [], groups)
    assert annual[0]["dollar_return"] == 0
    assert annual[0]["twr_pct"] == 0
    assert compute_twr_summary(rows, history, [], groups)["cumulative"] == 0
    assert compute_money_weighted_return(rows, history, [], groups)["annualized"] == 0
    assert compute_twr_daily_summary(rows, history, [], groups)["cumulative"] == 0


def test_transfer_events_obey_dates_without_creating_combined_scope_flows(market):
    from src.analytics._shared import _cash_flow_events, net_cash_flow
    from src.return_flows import annotate_account_transfers

    rows = _pair()
    rows[1]["date"] = "2024-06-08"
    annotate_account_transfers(rows)
    assert _cash_flow_events(rows, "2024-05-31", "2024-06-08", {"Example A"}) == [
        ("2024-06-01", -200)]
    assert _cash_flow_events(rows, "2024-05-31", "2024-06-08", {"Example B"}) == [
        ("2024-06-08", 200)]
    assert net_cash_flow(rows, "2024-06-01", "2024-06-07", {"Example A"}) == 0
    assert net_cash_flow(rows, "2024-06-07", "2024-06-08", {"Example B"}) == 200
    assert net_cash_flow(rows, "", "2024-06-07", {"Example A", "Example B"}) == 0
    assert _cash_flow_events(rows, "", "2024-06-08", None) == []


@pytest.mark.parametrize("groups", [{"Example A"}, {"Example B"}])
def test_delayed_transfer_keeps_each_accounts_returns_neutral(market, groups):
    from src.analytics._shared import (
        compute_money_weighted_return, compute_twr_daily_summary, compute_twr_summary,
    )
    from src.return_flows import annotate_account_transfers

    rows, history = _funded_case()
    rows[-1]["date"] = "2024-06-08"
    history[1].update(total=1800, by_account_group={"Example A": 800, "Example B": 1000})
    history.insert(2, {"date": "2024-06-08", "total": 2000,
                       "by_account_group": {"Example A": 800, "Example B": 1200}})
    annotate_account_transfers(rows)
    # Whole-portfolio in-transit balance behavior is intentionally separate.
    assert compute_twr_summary(rows, history, [], groups)["cumulative"] == 0
    assert compute_twr_daily_summary(rows, history, [], groups)["cumulative"] == 0
    assert compute_money_weighted_return(rows, history, [], groups)["annualized"] == 0


def test_marks_use_own_dates_and_never_future_transaction_prices(market):
    from src.return_flows import annotate_account_transfers

    market.clear()
    rows = _pair(price=0)
    rows[1].update(date="2024-06-08", price=130)
    rows += [_txn("Other", "Buy", day="2024-05-01", qty=1, price=90),
             _txn("Other", "Buy", day="2024-07-01", qty=1, price=999)]
    annotate_account_transfers(rows)
    assert rows[0]["account_transfer"]["flow"] == -180
    assert rows[1]["account_transfer"]["flow"] == 260
    later = _txn("Other", "Buy", day="2024-08-01", qty=1, price=9999)
    annotate_account_transfers(rows + [later])
    assert rows[0]["account_transfer"]["flow"] == -180
    assert rows[1]["account_transfer"]["flow"] == 260


def test_complete_same_day_symbol_price_is_shared_across_groups(market):
    from src.return_flows import annotate_account_transfers

    market.clear()
    rows = _pair(price=0)
    rows.append(_txn("Z Other", "Buy", qty=1, price=125))
    annotate_account_transfers(rows)
    assert [t["account_transfer"]["flow"] for t in rows[:2]] == [-250, 250]


@pytest.mark.parametrize("reordered_quotes", [False, True])
def test_exact_date_value_and_daily_return_share_history_fallback_order(
        market, monkeypatch, reordered_quotes):
    from src.analytics._shared import (
        _balance_sort_key, _value_at_date, compute_twr_daily_summary,
    )
    from src.history import compute_history
    from src.return_flows import annotate_account_transfers

    market.clear()
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-30")
    rows, _ = _funded_case()
    for seq, row in enumerate(rows):
        row["seq"] = seq
    rows[-1]["price"] = 110
    if reordered_quotes:
        # Export ordering must not make the earlier ingest quote win.
        rows += [_txn("Example B", "Neutral", qty=0, price=110, seq=6),
                 _txn("Example B", "Neutral", qty=0, price=120, seq=5)]
    annotate_account_transfers(rows)
    assert [row["account_transfer"]["flow"] for row in rows[2:4]] == [-220, 220]
    history = compute_history(rows, {"TEST": "Other"})
    ordered = sorted(rows, key=_balance_sort_key)
    for group, expected in [("Example A", 880), ("Example B", 1320)]:
        groups = {group}
        assert history[-1]["by_account_group"][group] == expected
        assert _value_at_date(ordered, "2024-06-01", groups, []) == expected
        assert compute_twr_daily_summary(rows, history, [], groups)["cumulative"] == 0.1


def test_reconciled_synthetic_outbound_carries_the_account_boundary(market):
    from src.return_flows import annotate_account_transfers, txn_cash_flow_for_groups

    rows = _pair()
    rows[0]["action"] = "Synthetic Transfer Out"
    annotate_account_transfers(rows)
    assert rows[0]["account_transfer"] == {"counterparty_group": "Example B", "flow": -200}
    assert rows[1]["account_transfer"] == {"counterparty_group": "Example A", "flow": 200}
    assert txn_cash_flow_for_groups(rows[0], {"Example A"}) == -200
    assert txn_cash_flow_for_groups(rows[1], {"Example B"}) == 200
    assert all(txn_cash_flow_for_groups(row, None) == 0 for row in rows)


@pytest.mark.parametrize("symbol, cached, split, expected", [
    ("TEST", 25, 4, 200),
    ("TEST 12/20/2024 Call $100.00", 3, 1, 600),
    ("TEST 12/20/2024 Call $100.00", None, 1, 20000),
])
def test_transfer_marks_share_split_and_option_valuation(
        market, monkeypatch, symbol, cached, split, expected):
    from src import valuation
    from src.return_flows import annotate_account_transfers

    market[symbol] = cached
    monkeypatch.setattr(valuation, "split_factor_since", lambda symbol, day: split)
    rows = _pair(symbol=symbol)
    annotate_account_transfers(rows)
    assert [t["account_transfer"]["flow"] for t in rows] == [-expected, expected]


@pytest.mark.parametrize("case", ["same_group", "usd", "unpaired", "external_out",
                                 "external_in", "empty_external_out", "empty_external_in",
                                 "deposit", "empty_deposit", "income", "paired_airdrop"])
def test_existing_portfolio_classifications_are_not_rewritten(market, case):
    from src.basis import txn_external_cash_flow
    from src.return_flows import annotate_account_transfers, txn_cash_flow_for_groups

    rows = _pair()
    if case == "same_group":
        rows[1]["account_group"] = rows[0]["account_group"]
    elif case == "usd":
        for row in rows:
            row["symbol"] = "USD"
    elif case == "unpaired":
        rows.pop()
    elif case in ("external_out", "empty_external_out"):
        rows[0].update(account_group="Coinbase", raw_action="Send",
                       amount=200 if case == "external_out" else 0)
    elif case in ("external_in", "empty_external_in"):
        rows[1].update(account_group="Coinbase", raw_action="Receive",
                       amount=200 if case == "external_in" else 0)
    elif case in ("deposit", "empty_deposit"):
        rows[1].update(action="Deposit", amount=200 if case == "deposit" else 0)
    elif case == "income":
        rows[1].update(action="Reward", amount=200)
    elif case == "paired_airdrop":
        rows[1].update(account_group="Coinbase", raw_action="Receive", amount=200,
                       description="Fictional airdrop")
    expected = [txn_external_cash_flow(t) for t in rows]
    annotate_account_transfers(rows)
    assert all("account_transfer" not in t for t in rows)
    assert [txn_cash_flow_for_groups(t, None) for t in rows] == expected
    assert [txn_cash_flow_for_groups(t, {t["account_group"]}) for t in rows] == expected


def test_unpaired_airdrop_receive_remains_income(market):
    from src.return_flows import annotate_account_transfers, txn_cash_flow_for_groups

    row = _txn("Coinbase", "Transfer In", raw_action="Receive", amount=200,
               description="Fictional airdrop")
    annotate_account_transfers([row])
    assert "account_transfer" not in row
    assert txn_cash_flow_for_groups(row, {"Coinbase"}) == 0


@pytest.mark.parametrize("kind", ["Retirement", "Savings"])
def test_account_type_filters_apply_their_own_transfer_boundaries(market, kind):
    from src.analytics._shared import _account_filter_sets, net_cash_flow
    from src.config import ACCOUNT_TYPES
    from src.return_flows import annotate_account_transfers

    ACCOUNT_TYPES.update({"Example A": kind, "Example C": kind,
                          "Example B": "Taxable", "Example D": "Taxable"})
    holdings = [{"account_group": group} for group in
                ("Example A", "Example B", "Example C", "Example D")]
    filters = _account_filter_sets(holdings)
    rows = _pair()
    annotate_account_transfers(rows)
    assert net_cash_flow(rows, "", "2024-12-31", filters[kind]) == -200
    assert net_cash_flow(rows, "", "2024-12-31", filters["Taxable"]) == 200
    assert net_cash_flow(rows, "", "2024-12-31", filters["Total"]) == 0


def test_canonical_seq_pairing_survives_export_order(market):
    from src.return_flows import annotate_account_transfers

    outs = [_txn("Example A", "Transfer Out", seq=2, account="Alpha"),
            _txn("Example A", "Transfer Out", seq=1, account="Zulu")]
    ins = [_txn("Example B", "Transfer In", seq=3),
           _txn("Example C", "Transfer In", seq=4)]
    rows = outs + ins
    annotate_account_transfers(rows)
    expected = {t["seq"]: t["account_transfer"] for t in rows}
    assert expected[1]["counterparty_group"] == "Example B"
    assert expected[2]["counterparty_group"] == "Example C"
    reloaded = json.loads(json.dumps(sorted(rows, key=lambda t: t["account"])))
    annotate_account_transfers(reloaded)
    assert {t["seq"]: t["account_transfer"] for t in reloaded} == expected


def test_refresh_reprices_then_removes_stale_pair_annotations(market):
    from src.return_flows import annotate_account_transfers

    rows = _pair()
    annotate_account_transfers(rows)
    market["TEST"] = 120
    reloaded = json.loads(json.dumps(rows))
    annotate_account_transfers(reloaded)
    assert [t["account_transfer"]["flow"] for t in reloaded] == [-240, 240]
    reloaded[1]["date"] = "2024-07-01"
    annotate_account_transfers(reloaded)
    assert all("account_transfer" not in t for t in reloaded)


@pytest.mark.parametrize("bad_price", [None, float("nan"), float("inf"), -1, 0])
def test_unpriceable_pair_warns_without_using_amount_or_basis(market, bad_price):
    from src.analytics.data_health import compute_data_health
    from src.config import CACHE_DIR
    from src.return_flows import annotate_account_transfers, txn_cash_flow_for_groups

    market["TEST"] = bad_price
    rows = _pair(price=0, amount=8765, cost_basis=4321)
    annotate_account_transfers(rows)
    assert all(t["account_transfer"]["flow"] is None for t in rows)
    assert all(txn_cash_flow_for_groups(t, {t["account_group"]}) == 0 for t in rows)
    json.dumps(rows, allow_nan=False)
    issues = compute_data_health(rows, [], [], {}, CACHE_DIR, parse_report=[])
    warnings = [i for i in issues if i["kind"] == "unpriced_account_transfer"]
    assert warnings
    assert all(i["severity"] == "warn" for i in warnings)


def test_build_export_reload_rebuilds_account_returns(market, isolated_workdir, monkeypatch):
    from src import analytics
    from src.export import export_json

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-12-31")
    rows, history = _funded_case()
    holdings = [{"account_group": group, "account_type": "Taxable", "symbol": "TEST",
                 "quantity": value / 100, "price": 100, "value": value,
                 "cost_basis": value, "sector": "Other"}
                for group, value in [("Example A", 800), ("Example B", 1200)]]
    first = analytics.build_analytics(rows, history, [], holdings)
    for group in ("Example A", "Example B", "Taxable", "Total"):
        entry = first["performance_by_filter"][group]
        assert entry["summary"]["cumulative"] == 0
        assert entry["money_weighted"]["annualized"] == 0
    output = isolated_workdir / "exports" / "fictional.json"
    export_json(rows, output, analytics=first)
    reloaded = json.loads(output.read_text(encoding="utf-8"))["transactions"]
    assert sum("account_transfer" in t for t in reloaded) == 2
    for row in reloaded:
        if "account_transfer" in row:
            row["account_transfer"]["flow"] = 1
    second = analytics.build_analytics(reloaded, history, [], holdings)
    assert second["performance_by_filter"] == first["performance_by_filter"]
