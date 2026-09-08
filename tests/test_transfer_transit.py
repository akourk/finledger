"""Fictional custody transfers preserve assets while moving between accounts.

Posted account holdings remain literal broker positions.  Effective portfolio
measurements add the separate in-transit position only when both custodians
belong to the selected scope.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest


SOURCE = "Transit Example A"
DESTINATION = "Transit Example B"
THIRD = "Transit Example C"
SYMBOL = "PRISM"


@pytest.fixture
def market(isolated_workdir, monkeypatch):
    from src import history, prices, valuation
    from src.config import ACCOUNT_TYPES

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-03-31")
    ACCOUNT_TYPES.update({SOURCE: "Taxable", DESTINATION: "Taxable",
                          THIRD: "Retirement"})
    state = {"quotes": {SYMBOL: {"2024-01-01": 100.0}}, "split": 1.0}

    def price(symbol, day):
        if symbol in {"SPY", "BND", "VXUS"}:
            return 100.0
        eligible = [(d, p) for d, p in state["quotes"].get(symbol, {}).items()
                    if d <= day]
        return max(eligible)[1] if eligible else None

    def split(symbol, day):
        value = state["split"]
        return value(day) if callable(value) else value

    monkeypatch.setattr(prices, "get_price", price)
    monkeypatch.setattr(history, "get_price", price)
    monkeypatch.setattr(valuation, "get_price", price)
    monkeypatch.setattr(prices, "split_factor_since", split)
    monkeypatch.setattr(valuation, "split_factor_since", split)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda symbol, day: None)
    return state


def _row(group, action, *, day="2024-01-01", qty=10.0, price=100.0,
         symbol=SYMBOL, amount=None, **extra):
    from src.basis import txn_external_cash_flow
    from src.config import ACCOUNT_TYPES

    row = {"date": day, "account": group, "account_group": group,
           "account_type": ACCOUNT_TYPES[group], "symbol": symbol,
           "action": action, "raw_action": action, "quantity": qty,
           "price": price, "amount": qty * price if amount is None else amount,
           "fees": 0, "description": "", "source": "fictional-transit-fixture",
           **extra}
    row["cash_flow"] = txn_external_cash_flow(row)
    return row


def _case(*, qty=2.0, symbol=SYMBOL, price=100.0):
    rows = [_row(group, "Contribution", symbol=symbol, price=price, amount=1000)
            for group in (SOURCE, DESTINATION)]
    rows += [_row(SOURCE, "Transfer Out", day="2024-02-27", qty=qty,
                  symbol=symbol, price=price, amount=qty),
             _row(DESTINATION, "Transfer In", day="2024-03-05", qty=qty,
                  symbol=symbol, price=price, amount=qty)]
    for seq, row in enumerate(rows):
        row["seq"] = seq
    return rows


def _walk(rows):
    from src.basis import compute_basis_default
    from src.history import compute_history
    from src.return_flows import annotate_account_transfers

    state = compute_basis_default(rows)
    annotate_account_transfers(rows)
    history = compute_history(rows, {row["symbol"]: "Other" for row in rows},
                              cadence="day")
    return state, history


@pytest.mark.parametrize("groups", [None, set(), {SOURCE}, {DESTINATION},
                                    {SOURCE, DESTINATION}, {SOURCE, THIRD}])
def test_departure_and_arrival_boundaries_agree_across_value_paths(market, groups):
    from src.analytics._shared import _balance_sort_key, _filter_value_fn, _value_at_date
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_value

    rows = _case()
    _, history = _walk(rows)
    daily = dict(compute_daily_totals(rows))
    snapshots = {h["date"]: h for h in history}
    for day in ("2024-02-26", "2024-02-27", "2024-03-04", "2024-03-05"):
        h = snapshots[day]
        posted = {SOURCE: 1000, DESTINATION: 1000}
        if day >= "2024-02-27":
            posted[SOURCE] -= 200
        if day >= "2024-03-05":
            posted[DESTINATION] += 200
        during = "2024-02-27" <= day < "2024-03-05"
        base = sum(posted.values()) if groups is None else sum(
            posted.get(g, 0) for g in groups)
        expected = base + (200 if during and
                           (groups is None or {SOURCE, DESTINATION} <= groups) else 0)
        assert h["total"] == sum(posted.values())
        assert scope_snapshot_value(h, groups) == expected
        assert _filter_value_fn(groups)(h) == expected
        assert _value_at_date(sorted(rows, key=_balance_sort_key), day, groups, []) == expected
        assert daily[day] == 2000
        assert len(h.get("in_transit", [])) == int(during)


@pytest.mark.parametrize("qty", [2.0, 10.0])
def test_transit_basis_is_carried_once_without_rewriting_custody_holdings(market, qty):
    from src.basis import state_to_holdings
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    rows = _case(qty=qty)
    state, history = _walk(rows)
    during = next(h for h in history if h["date"] == "2024-02-29")
    transit, = during["in_transit"]
    assert transit["source_group"] == SOURCE
    assert transit["destination_group"] == DESTINATION
    assert transit["symbol"] == SYMBOL
    assert transit["quantity"] == qty
    assert transit["value"] == qty * 100
    assert transit["cost_basis"] == qty * 100
    assert during["total"] == during["total_cost_basis"] == 2000 - qty * 100
    assert sum(p["value"] or 0 for p in during["positions"]) == during["total"]
    assert sum(p["cost_basis"] for p in during["positions"]) == during["total_cost_basis"]
    assert scope_snapshot_basis(during) == scope_snapshot_value(during) == 2000
    assert scope_snapshot_basis(during, {SOURCE, DESTINATION}) == 2000
    assert scope_snapshot_basis(during, {SOURCE}) == 1000 - qty * 100
    assert scope_snapshot_basis(during, {DESTINATION}) == 1000
    assert scope_snapshot_basis(during, set()) == 0

    final = history[-1]
    assert not final.get("in_transit")
    assert final["total"] == final["total_cost_basis"] == 2000
    expected = {(p["account_group"], p["symbol"]): (p["quantity"], p["cost_basis"])
                for p in state_to_holdings(state, "fifo")}
    actual = {(p["account_group"], p["symbol"]): (p["quantity"], p["cost_basis"])
              for p in final["positions"]}
    assert actual == expected
    assert rows[2]["cash_flow"] == rows[3]["cash_flow"] == 0
    assert rows[2].get("realized_gain", 0) == rows[3].get("realized_gain", 0) == 0


@pytest.mark.parametrize("changing", [False, True])
def test_transfer_gap_is_not_a_return_drawdown_or_monthly_loss(market, changing):
    from src.analytics._shared import (
        compute_annual_returns, compute_money_weighted_return,
        compute_twr_daily_summary, compute_twr_summary,
    )
    from src.analytics.drawdown import compute_drawdown
    from src.analytics.monthly_pnl import compute_monthly_pnl
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_value

    if changing:
        market["quotes"][SYMBOL]["2024-02-28"] = 120.0
    rows = _case()
    _, history = _walk(rows)
    expected = 0.2 if changing else 0.0
    during = next(h for h in history if h["date"] == "2024-02-29")
    assert during["in_transit"][0]["value"] == (240 if changing else 200)
    assert scope_snapshot_value(during) == (2400 if changing else 2000)
    for groups in (None, {SOURCE, DESTINATION}, {SOURCE}, {DESTINATION}):
        assert compute_twr_summary(rows, history, [], groups)["cumulative"] == expected
        assert compute_twr_daily_summary(rows, history, [], groups)["cumulative"] == expected
        annual, = compute_annual_returns(rows, history, [], groups)
        assert annual["twr_pct"] == expected * 100
    if not changing:
        for end in ("2024-02-29", "2024-03-31"):
            window = [h for h in history if h["date"] <= end]
            for groups in (None, {SOURCE, DESTINATION}, {SOURCE}, {DESTINATION}):
                assert compute_money_weighted_return(rows, window, [], groups)["annualized"] == 0
    for daily in (None, compute_daily_totals(rows)):
        drawdown = compute_drawdown(history, daily_totals=daily)
        assert drawdown["max_drawdown"] == 0
        assert drawdown["current_drawdown_pct"] == 0
    monthly = compute_monthly_pnl(history, rows)
    year, = monthly["rows"]
    assert year["months"][2] == expected
    assert year["months"][3] == 0


def test_overlapping_transfers_are_included_only_for_their_own_endpoint_pair(market):
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    rows = _case()
    rows += [_row(THIRD, "Contribution", qty=10, amount=1000),
             _row(SOURCE, "Transfer Out", day="2024-02-28", qty=3, amount=3),
             _row(THIRD, "Transfer In", day="2024-03-06", qty=3, amount=3)]
    _, history = _walk(rows)
    h = next(h for h in history if h["date"] == "2024-02-29")
    assert len(h["in_transit"]) == 2
    assert h["total"] == 2500
    for scope, expected in [(None, 3000), ({SOURCE, DESTINATION}, 1700),
                            ({SOURCE, THIRD}, 1800), ({DESTINATION, THIRD}, 2000),
                            ({SOURCE, DESTINATION, THIRD}, 3000)]:
        assert scope_snapshot_value(h, scope) == expected
        assert scope_snapshot_basis(h, scope) == expected


@pytest.mark.parametrize("changing", [False, True])
def test_entire_portfolio_can_be_in_transit_without_disappearing(market, changing):
    from src.analytics._shared import (
        _balance_sort_key, _value_at_date, compute_money_weighted_return,
        compute_twr_daily_summary, compute_twr_summary,
    )
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    if changing:
        market["quotes"][SYMBOL]["2024-02-28"] = 120.0
    rows = _case(qty=10)
    rows.pop(1)  # The recipient has no preexisting holdings.
    _, history = _walk(rows)
    h = next(h for h in history if h["date"] == "2024-02-29")
    expected = 1200 if changing else 1000
    assert h["total"] == h["total_cost_basis"] == 0
    assert not h["positions"]
    assert scope_snapshot_basis(h) == 1000
    assert scope_snapshot_value(h) == expected
    assert scope_snapshot_value(h, {SOURCE}) == 0
    assert scope_snapshot_value(h, {DESTINATION}) == 0
    assert dict(compute_daily_totals(rows))[h["date"]] == expected
    assert _value_at_date(sorted(rows, key=_balance_sort_key), h["date"], None, []) == expected
    for groups in (None, {SOURCE, DESTINATION}):
        assert compute_twr_summary(rows, history, [], groups)["cumulative"] == (
            0.2 if changing else 0)
        assert compute_twr_daily_summary(rows, history, [], groups)["cumulative"] == (
            0.2 if changing else 0)
        if not changing:
            through_gap = [s for s in history if s["date"] <= h["date"]]
            assert compute_money_weighted_return(rows, through_gap, [], groups)["annualized"] == 0


def test_same_type_filter_includes_transit_but_cross_type_filter_does_not(market):
    from src.analytics._shared import _account_filter_sets, _filter_value_fn
    from src.config import ACCOUNT_TYPES

    rows = _case()
    rows += [_row(THIRD, "Contribution", qty=10, amount=1000),
             _row(SOURCE, "Transfer Out", day="2024-02-28", qty=3, amount=3),
             _row(THIRD, "Transfer In", day="2024-03-06", qty=3, amount=3)]
    _, history = _walk(rows)
    h = next(h for h in history if h["date"] == "2024-02-29")
    # Combined type filters are only emitted when distinct from an individual
    # account or Total. Empty companion accounts exercise the actual filter map.
    ACCOUNT_TYPES.update({"Transit Example D": "Retirement",
                          "Transit Example Savings": "Savings"})
    filters = _account_filter_sets([{"account_group": g} for g in
                                    (SOURCE, DESTINATION, THIRD,
                                     "Transit Example D", "Transit Example Savings")])
    assert _filter_value_fn(filters["Total"])(h) == 3000
    assert _filter_value_fn(filters["Taxable"])(h) == 1700
    assert _filter_value_fn(filters["Retirement"])(h) == 1000
    assert _filter_value_fn(filters["Investments"])(h) == 3000


def test_in_transit_marks_exclude_future_prices_and_preserve_canonical_daily_order(market):
    from src.analytics._shared import _balance_sort_key, _value_at_date
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_value

    market["quotes"].clear()
    rows = _case(price=0)
    rows += [_row(SOURCE, "Neutral", day="2024-02-26", qty=0, price=100, seq=6),
             _row(SOURCE, "Neutral", day="2024-02-26", qty=0, price=110, seq=5)]
    rows[-3]["price"] = 140  # Arrival is evidence of the pair, not an earlier price.
    _, history = _walk(rows)
    first = next(h for h in history if h["date"] == "2024-02-29")
    assert first["in_transit"][0]["value"] == 200
    assert scope_snapshot_value(first) == 2000
    assert dict(compute_daily_totals(rows))["2024-02-29"] == 2000
    assert _value_at_date(sorted(rows, key=_balance_sort_key), "2024-02-29", None, []) == 2000

    later = _row(THIRD, "Neutral", day="2024-03-15", qty=0, price=9999, seq=20)
    _, after = _walk(list(reversed(rows)) + [later])
    assert next(h for h in after if h["date"] == "2024-02-29") == first
    assert dict(compute_daily_totals(list(reversed(rows)) + [later]))["2024-02-29"] == 2000


@pytest.mark.parametrize("symbol,price,split,value", [
    (SYMBOL, 25.0, 4.0, 200.0),
    ("PRISM 12/20/2024 Call $100.00", 3.0, 1.0, 600.0),
])
def test_transit_uses_shared_split_and_option_valuation(market, symbol, price, split, value):
    market["quotes"] = {symbol: {"2024-01-01": price}}
    market["split"] = split
    _, history = _walk(_case(symbol=symbol))
    h = next(h for h in history if h["date"] == "2024-02-29")
    assert h["in_transit"][0]["value"] == value


@pytest.mark.parametrize("case", ["same_day", "same_group", "usd", "unmatched",
                                  "deposit", "external_out", "airdrop"])
def test_transit_does_not_expand_existing_pair_eligibility(market, case):
    rows = _case()
    if case == "same_day":
        rows[-1]["date"] = rows[-2]["date"]
    elif case == "same_group":
        rows[-1]["account_group"] = SOURCE
    elif case == "usd":
        for row in rows:
            row["symbol"] = "USD"
    elif case == "unmatched":
        rows.pop()
    elif case == "deposit":
        rows[-1]["action"] = "Deposit"
    elif case == "external_out":
        rows[-2].update(account_group="Coinbase", raw_action="Send", amount=200)
    elif case == "airdrop":
        rows[-1].update(description="Fictional airdrop", raw_action="Receive")
    _, history = _walk(rows)
    assert all(not h.get("in_transit") for h in history)


def test_unavailable_transit_value_is_explicit_and_warns(market):
    from src.analytics.data_health import compute_data_health
    from src.config import CACHE_DIR
    from src.return_flows import scope_snapshot_value

    rows = _case(price=0)
    market["quotes"].clear()
    _, history = _walk(rows)
    h = next(h for h in history if h["date"] == "2024-02-29")
    transit, = h["in_transit"]
    assert transit["value"] is None
    assert transit["valuation_issue"] == "unpriced"
    assert scope_snapshot_value(h) == h["total"]
    json.dumps(history, allow_nan=False)
    warnings = compute_data_health(rows, [], history, {}, CACHE_DIR, parse_report=[])
    assert any("transit" in item["kind"] and item["severity"] == "warn"
               for item in warnings)


def test_export_reload_reconstructs_transit_and_whole_portfolio_analytics(
        market, isolated_workdir):
    from src import analytics
    from src.export import export_json
    from src.return_flows import scope_snapshot_value

    rows = _case()
    _, history = _walk(rows)
    holdings = [{**p, "account_type": "Taxable", "sector": "Other"}
                for p in history[-1]["positions"]]
    first = analytics.build_analytics(rows, history, [], holdings)
    for group in (SOURCE, DESTINATION, "Taxable", "Total"):
        entry = first["performance_by_filter"][group]
        assert entry["summary"]["cumulative"] == 0
        assert entry["money_weighted"]["annualized"] == 0
    assert first["drawdown"]["max_drawdown"] == 0

    output = isolated_workdir / "exports" / "fictional-transit.json"
    export_json(rows, output, history=history, holdings_by_account=holdings, analytics=first)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    serialized = next(h for h in loaded["history"] if h["date"] == "2024-02-29")
    assert serialized["total"] == 1800
    assert scope_snapshot_value(serialized) == 2000
    original = deepcopy(loaded["transactions"])
    _, rebuilt = _walk(loaded["transactions"])
    assert rebuilt == history
    second = analytics.build_analytics(loaded["transactions"], rebuilt, [], holdings)
    assert second["performance_by_filter"] == first["performance_by_filter"]
    assert second["drawdown"] == first["drawdown"]
    assert [t["cash_flow"] for t in loaded["transactions"]] == [t["cash_flow"] for t in original]


@pytest.mark.parametrize("split_on_arrival", [False, True])
def test_incompatible_arrival_units_warn_without_claiming_transit(market, split_on_arrival):
    from src import analytics
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_value

    market["quotes"][SYMBOL] = {"2024-01-01": 50.0}
    market["split"] = (lambda day: 2.0 if day < "2024-03-05" else 1.0
                       ) if split_on_arrival else 2.0
    rows = [_row(SOURCE, "Contribution", qty=10, price=100),
            _row(SOURCE, "Transfer Out", day="2024-02-27", qty=10, price=0, amount=10),
            _row(DESTINATION, "Transfer In", day="2024-03-05", qty=10, price=0, amount=10)]
    _, history = _walk(rows)
    issues = []
    daily = dict(compute_daily_totals(rows, transit_issues=issues))
    assert daily["2024-03-04"] == (0 if split_on_arrival else 1000)
    assert daily["2024-03-05"] == (500 if split_on_arrival else 1000)
    assert not issues  # Unverified transfers never enter the transit valuation path.
    prior = next(h for h in history if h["date"] == "2024-03-04")
    assert scope_snapshot_value(prior) == (0 if split_on_arrival else 1000)
    if split_on_arrival:
        assert not prior.get("in_transit")
    else:
        assert prior["in_transit"][0]["value"] == 1000
        assert "valuation_issue" not in prior["in_transit"][0]

    holdings = [{**p, "account_type": "Taxable", "sector": "Other"}
                for p in history[-1]["positions"]]
    before = deepcopy(rows)
    actual = analytics.build_analytics(rows, history, [], holdings)
    warnings = [issue for issue in actual["data_health"]
                if issue["kind"] == "unsupported_transfer_share_units"]
    assert rows == before
    assert len(warnings) == int(split_on_arrival)
    if warnings:
        warning, = warnings
        assert warning["severity"] == "warn"
        assert warning["count"] == 1
        assert all(text in warning["details"][0] for text in (
            "2024-02-27", "2024-03-05", SOURCE, DESTINATION, SYMBOL))
        assert actual["drawdown"]["max_drawdown"] == -1


def test_daily_only_price_gap_warns_when_snapshot_and_transfer_marks_are_priced(
        market, monkeypatch):
    from src import analytics
    from src.basis import compute_basis_default
    from src.history import compute_daily_totals, compute_history
    from src.return_flows import annotate_account_transfers

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-30")
    market["quotes"][SYMBOL].update({"2024-06-04": None, "2024-06-05": 100.0})
    rows = _case(price=0)
    rows[0]["date"] = rows[1]["date"] = "2024-05-01"
    rows[2]["date"], rows[3]["date"] = "2024-06-02", "2024-06-08"
    compute_basis_default(rows)
    annotate_account_transfers(rows)
    assert [t["account_transfer"]["flow"] for t in rows[2:]] == [-200, 200]
    history = compute_history(rows, {SYMBOL: "Other"})
    assert all(not h.get("in_transit") for h in history)
    assert all(h["total"] == 2000 for h in history)

    issues = []
    daily = dict(compute_daily_totals(rows, transit_issues=issues))
    assert daily["2024-06-03"] == daily["2024-06-05"] == 2000
    assert daily["2024-06-04"] == 0
    assert [issue["date"] for issue in issues] == ["2024-06-04"]
    missing, = issues[0]["in_transit"]
    assert missing["symbol"] == SYMBOL
    assert missing["value"] is None

    holdings = [{**p, "account_type": "Taxable", "sector": "Other"}
                for p in history[-1]["positions"]]
    actual = analytics.build_analytics(rows, history, [], holdings)
    warning, = [issue for issue in actual["data_health"]
                if issue["kind"] == "unpriced_transfer_transit"]
    assert warning["severity"] == "warn"
    assert warning["count"] == 1
    assert "2024-06-04" in warning["details"][0]


def test_contribution_warning_scales_to_portfolio_exposure_during_transit():
    from src.analytics.data_health import _check_net_contributed_monotonicity

    history = [{"date": "2024-01-31", "total": 400_000, "net_contributed": 400_000},
               {"date": "2024-02-29", "total": 400_000, "net_contributed": 550_000,
                "in_transit": [{"value": 600_000}]}]
    assert not _check_net_contributed_monotonicity(history)
    history[-1]["net_contributed"] = 650_000
    assert _check_net_contributed_monotonicity(history)


def test_subcent_position_stays_dust_before_during_and_after_transfer(market):
    from src.analytics._shared import _balance_sort_key, _value_at_date
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    market["quotes"][SYMBOL] = {"2024-01-01": 0.003}
    rows = [_row(SOURCE, "Contribution", qty=2, price=0.003, amount=0.006),
            _row(SOURCE, "Transfer Out", day="2024-02-27", qty=2, price=0.003, amount=2),
            _row(DESTINATION, "Transfer In", day="2024-03-05", qty=2, price=0.003, amount=2)]
    _, history = _walk(rows)
    daily = dict(compute_daily_totals(rows))
    ordered = sorted(rows, key=_balance_sort_key)
    for day in ("2024-02-26", "2024-02-27", "2024-03-04", "2024-03-05"):
        h = next(h for h in history if h["date"] == day)
        assert not h["positions"]
        assert not h.get("in_transit")
        assert scope_snapshot_value(h) == scope_snapshot_basis(h) == 0
        assert daily[day] == 0
        assert _value_at_date(ordered, day, None, []) == 0


def test_partial_transfer_rounds_combined_value_and_basis_only_once(market):
    from src.analytics._shared import _balance_sort_key, _value_at_date
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    price = 100.012
    market["quotes"][SYMBOL] = {"2024-01-01": price}
    rows = [_row(SOURCE, "Contribution", qty=1, price=price, amount=price),
            _row(SOURCE, "Transfer Out", day="2024-02-27", qty=0.5,
                 price=price, amount=0.5),
            _row(DESTINATION, "Transfer In", day="2024-03-05", qty=0.5,
                 price=price, amount=0.5)]
    _, history = _walk(rows)
    daily = dict(compute_daily_totals(rows))
    ordered = sorted(rows, key=_balance_sort_key)
    for day in ("2024-02-26", "2024-02-27", "2024-03-04", "2024-03-05"):
        h = next(h for h in history if h["date"] == day)
        assert scope_snapshot_value(h) == 100.01
        assert scope_snapshot_basis(h) == 100.01
        assert daily[day] == 100.01
        assert _value_at_date(ordered, day, None, []) == pytest.approx(price)
        if "2024-02-27" <= day < "2024-03-05":
            transit, = h["in_transit"]
            precision = h["valuation_precision"]
            assert transit["value"] == transit["cost_basis"] == pytest.approx(price / 2)
            assert precision["total"] + transit["value"] == pytest.approx(price)
            assert precision["total_cost_basis"] + transit["cost_basis"] == pytest.approx(price)
            assert h["total"] == h["total_cost_basis"] == 50.01
            assert h["by_account_group"][SOURCE] == h["cost_basis_by_group"][SOURCE] == 50.01
            assert scope_snapshot_value(h, {SOURCE, DESTINATION}) == 100.01
            assert scope_snapshot_basis(h, {SOURCE, DESTINATION}) == 100.01
            assert scope_snapshot_value(h, {SOURCE}) == scope_snapshot_basis(h, {SOURCE}) == 50.01
            assert scope_snapshot_value(h, {DESTINATION}) == scope_snapshot_basis(h, {DESTINATION}) == 0
