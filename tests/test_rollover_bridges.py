"""Rollover-bridge detection + the consumers that must apply it.

A custodial rollover (e.g. Voya 401K → Schwab Rollover IRA, both mapped
to the "Rollover IRA" group) leaves the account value at $0 while the
cash is in transit — main.py deliberately doesn't track USD in
non-Savings accounts.  ``detect_rollover_bridges`` pairs the
Distribution with the arriving Transfer In(s) so consumers can add the
in-flight amount back.  These tests pin (a) the matcher's robustness to
real-world shapes (multi-day liquidation legs, multiple wires,
amount-less Transfer In rows) and (b) that drawdown / monthly P&L
actually apply the bridge instead of reporting a phantom crash.
"""

from __future__ import annotations

import pytest

from src.analytics._shared import bridge_adjustment, detect_rollover_bridges


def _d(date, group="Rollover IRA", amount=50000.0):
    return {"date": date, "account_group": group, "action": "Distribution",
            "symbol": "FUND", "quantity": 100.0, "amount": amount}


def _tin(date, group="Rollover IRA", amount=50000.0, symbol="USD",
         quantity=None, price=0.0):
    return {"date": date, "account_group": group, "action": "Transfer In",
            "symbol": symbol, "amount": amount,
            "quantity": quantity if quantity is not None else amount,
            "price": price}


class TestDetection:
    def test_simple_distribution_matched_by_single_transfer_in(self):
        txns = [_d("2022-12-20"), _tin("2023-01-10")]
        bridges = detect_rollover_bridges(txns)
        assert len(bridges) == 1
        b = bridges[0]
        assert b["group"] == "Rollover IRA"
        assert b["start_date"] == "2022-12-20"
        assert b["end_date"] == "2023-01-10"
        assert b["amount"] == pytest.approx(50000.0)
        # In-flight window covered; outside it, no adjustment.
        assert bridge_adjustment("2022-12-31", None, bridges) == pytest.approx(50000.0)
        assert bridge_adjustment("2023-01-10", None, bridges) == 0.0

    def test_multi_day_liquidation_legs_merge_into_one_event(self):
        """Custodians often sell a 401K's funds across a few days; the
        legs must merge and match one arriving wire."""
        txns = [
            _d("2022-12-19", amount=30000.0),
            _d("2022-12-21", amount=20000.0),
            _tin("2023-01-10", amount=50000.0),
        ]
        bridges = detect_rollover_bridges(txns)
        # One bridge per leg, each starting when its cash left.
        assert len(bridges) == 2
        assert bridge_adjustment("2022-12-20", None, bridges) == pytest.approx(30000.0)
        assert bridge_adjustment("2022-12-31", None, bridges) == pytest.approx(50000.0)
        assert bridge_adjustment("2023-01-10", None, bridges) == 0.0

    def test_multiple_arriving_wires_sum_match(self):
        """Pre-tax and after-tax sub-balances arriving as two wires with
        no single wire matching the total."""
        txns = [
            _d("2022-12-20", amount=50000.0),
            _tin("2023-01-05", amount=38000.0),
            _tin("2023-01-18", amount=12000.0),
        ]
        bridges = detect_rollover_bridges(txns)
        assert len(bridges) == 2
        # Before either wire lands: full amount in flight.
        assert bridge_adjustment("2022-12-31", None, bridges) == pytest.approx(50000.0)
        # After wire 1: only the second wire is still in flight.
        assert bridge_adjustment("2023-01-06", None, bridges) == pytest.approx(12000.0)
        assert bridge_adjustment("2023-01-18", None, bridges) == 0.0

    def test_transfer_in_with_no_amount_uses_usd_quantity(self):
        """Some exports carry the dollar value only in quantity."""
        txns = [
            _d("2022-12-20", amount=50000.0),
            _tin("2023-01-10", amount=0.0, symbol="USD", quantity=50000.0),
        ]
        bridges = detect_rollover_bridges(txns)
        assert len(bridges) == 1
        assert bridges[0]["end_date"] == "2023-01-10"

    def test_no_match_beyond_tolerance_returns_nothing(self):
        txns = [_d("2022-12-20", amount=50000.0),
                _tin("2023-01-10", amount=30000.0)]   # 40% off — not it
        assert detect_rollover_bridges(txns) == []


def _history_with_dip():
    """Portfolio steady at $100k; Dec 2022 snapshot catches the rollover
    in flight ($50k of Rollover IRA at $0)."""
    return [
        {"date": "2022-10-31", "total": 100000.0, "net_contributed": 80000.0},
        {"date": "2022-11-30", "total": 100000.0, "net_contributed": 80000.0},
        {"date": "2022-12-31", "total": 50000.0,  "net_contributed": 80000.0},
        {"date": "2023-01-31", "total": 100000.0, "net_contributed": 80000.0},
        {"date": "2023-02-28", "total": 101000.0, "net_contributed": 80000.0},
    ]


_BRIDGE = [{"group": "Rollover IRA", "start_date": "2022-12-20",
            "end_date": "2023-01-10", "amount": 50000.0}]


class TestConsumers:
    def test_drawdown_ignores_bridged_rollover_dip(self):
        from src.analytics.drawdown import compute_drawdown
        out = compute_drawdown(_history_with_dip(), _BRIDGE)
        # Bridged: no 50% phantom crash; worst drawdown ~0.
        assert out["max_drawdown"] == pytest.approx(0.0, abs=0.001)
        dec = [p for p in out["series"] if p["date"] == "2022-12-31"][0]
        assert dec["drawdown_pct"] == pytest.approx(0.0, abs=0.001)

    def test_drawdown_without_bridge_shows_the_dip(self):
        from src.analytics.drawdown import compute_drawdown
        out = compute_drawdown(_history_with_dip(), [])
        assert out["max_drawdown"] == pytest.approx(-0.5, abs=0.001)

    def test_monthly_pnl_ignores_bridged_rollover_dip(self):
        from src.analytics.monthly_pnl import compute_monthly_pnl
        out = compute_monthly_pnl(_history_with_dip(), [], _BRIDGE)
        dec = out["rows"][0]["months"].get(12)
        jan = [r for r in out["rows"] if r["year"] == 2023][0]["months"].get(1)
        assert dec == pytest.approx(0.0, abs=0.001)
        assert jan == pytest.approx(0.0, abs=0.001)

    def test_monthly_pnl_without_bridge_shows_phantom_swing(self):
        from src.analytics.monthly_pnl import compute_monthly_pnl
        out = compute_monthly_pnl(_history_with_dip(), [], [])
        dec = out["rows"][0]["months"].get(12)
        assert dec == pytest.approx(-0.5, abs=0.001)


class TestDataHealth:
    def test_unbridged_distribution_flagged(self, tmp_path):
        from src.analytics.data_health import compute_data_health
        txns = [_d("2022-12-20", amount=50000.0)]   # no Transfer In at all
        issues = compute_data_health(
            txns, [], [], {"rollover_bridges": []}, tmp_path)
        assert any(i["kind"] == "unbridged_retirement_distribution"
                   and i["severity"] == "warn" for i in issues)

    def test_bridged_distribution_clean(self, tmp_path):
        from src.analytics.data_health import compute_data_health
        txns = [_d("2022-12-20", amount=50000.0), _tin("2023-01-10")]
        bridges = detect_rollover_bridges(txns)
        issues = compute_data_health(
            txns, [], [], {"rollover_bridges": bridges}, tmp_path)
        assert not any(i["kind"] == "unbridged_retirement_distribution"
                       for i in issues)


@pytest.fixture
def overlapping_transfer(isolated_workdir, monkeypatch):
    """Two independently fictional movements happen to have the same value."""
    from src import history, prices, valuation
    from src.config import ACCOUNT_TYPES

    ACCOUNT_TYPES.update({"Example Source": "Taxable", "Roth IRA": "Retirement"})
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-01-06")
    for module in (history, prices, valuation):
        monkeypatch.setattr(module, "get_price", lambda symbol, day: 100.0)
    monkeypatch.setattr(valuation, "split_factor_since", lambda symbol, day: 1.0)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda symbol, day: None)

    def row(group, action, day, symbol="PRISM"):
        return {"date": day, "account_group": group, "account": group,
                "account_type": ACCOUNT_TYPES[group], "action": action,
                "raw_action": action, "symbol": symbol, "quantity": 10.0,
                "price": 100.0, "amount": 1000.0, "fees": 0.0,
                "description": "", "source": "fictional-rollover-fixture"}

    return [row("Example Source", "Contribution", "2024-01-01"),
            row("Roth IRA", "Contribution", "2024-01-01", "ORBIT"),
            row("Roth IRA", "Distribution", "2024-01-02", "ORBIT"),
            row("Example Source", "Transfer Out", "2024-01-03"),
            row("Roth IRA", "Transfer In", "2024-01-05")]


def test_verified_account_arrival_cannot_also_confirm_rollover_cash(overlapping_transfer):
    from src.analytics._shared import detect_rollover_bridges
    from src.analytics.data_health import _check_unbridged_retirement_distribution
    from src.return_flows import eligible_account_transfer_pairs

    txns = overlapping_transfer
    assert eligible_account_transfer_pairs(txns) == [(txns[-2], txns[-1])]
    bridges = detect_rollover_bridges(txns)
    assert bridges == []
    # The original Distribution now remains explicitly unresolved; its amount
    # must not invent a second asset supported by the already-claimed arrival.
    issues = _check_unbridged_retirement_distribution(txns, {"rollover_bridges": bridges})
    assert [issue["kind"] for issue in issues] == ["unbridged_retirement_distribution"]


def test_unreconciled_account_arrival_cannot_confirm_rollover_cash(overlapping_transfer):
    from src.basis import _pair_transfers
    from src.return_flows import eligible_account_transfer_pairs

    txns = overlapping_transfer
    txns[-1]["quantity"] = 9.995
    assert eligible_account_transfer_pairs(txns) == []
    assert _pair_transfers(txns)["issues"]
    assert detect_rollover_bridges(txns) == []
    # Independent cash evidence remains usable despite an unresolved asset move.
    txns.append(_tin("2024-01-06", group="Roth IRA", amount=1000.0))
    assert detect_rollover_bridges(txns) == [
        {"group": "Roth IRA", "start_date": "2024-01-02",
         "end_date": "2024-01-06", "amount": 1000.0}]


def test_claimed_arrival_does_not_create_a_loss_when_transit_ends(overlapping_transfer):
    from src.analytics._shared import (
        _balance_sort_key, _value_at_date, compute_twr_daily_summary,
        compute_twr_summary, detect_rollover_bridges,
    )
    from src.history import compute_history
    from src.return_flows import annotate_account_transfers, scope_snapshot_value

    txns = overlapping_transfer
    annotate_account_transfers(txns)
    history = compute_history(txns, {"PRISM": "Other", "ORBIT": "Other"}, cadence="day")
    window = [h for h in history if "2024-01-03" <= h["date"] <= "2024-01-05"]
    bridges = detect_rollover_bridges(txns)
    # The unsupported Distribution predates this window. PRISM is the only
    # evidenced asset throughout it, first in transit and then at its custodian.
    assert [scope_snapshot_value(h) for h in window] == [1000.0] * 3
    ordered = sorted(txns, key=_balance_sort_key)
    assert [_value_at_date(ordered, h["date"], None, bridges) for h in window] == [1000.0] * 3
    assert compute_twr_summary(txns, window, bridges, None)["cumulative"] == 0.0
    assert compute_twr_daily_summary(txns, window, bridges, None)["cumulative"] == 0.0


def test_independent_usd_arrival_still_confirms_rollover(overlapping_transfer):
    from src.analytics._shared import _balance_sort_key, _value_at_date, detect_rollover_bridges

    txns = overlapping_transfer
    txns.append(_tin("2024-01-06", group="Roth IRA", amount=1000.0))
    bridges = detect_rollover_bridges(txns)
    assert bridges == [{"group": "Roth IRA", "start_date": "2024-01-02",
                        "end_date": "2024-01-06", "amount": 1000.0}]
    ordered = sorted(txns, key=_balance_sort_key)
    # Each of the two positions now has its own arrival evidence. The cash
    # remains in flight even after the separately transferred PRISM arrives.
    assert _value_at_date(ordered, "2024-01-03", None, bridges) == 2000.0
    assert _value_at_date(ordered, "2024-01-05", None, bridges) == 2000.0


def test_same_group_lot_pair_keeps_existing_rollover_eligibility(overlapping_transfer):
    from src.analytics._shared import detect_rollover_bridges
    from src.return_flows import eligible_account_transfer_pairs

    txns = overlapping_transfer
    txns[-2]["account_group"] = "Roth IRA"
    assert eligible_account_transfer_pairs(txns) == []
    assert detect_rollover_bridges(txns) == [
        {"group": "Roth IRA", "start_date": "2024-01-02",
         "end_date": "2024-01-05", "amount": 1000.0}]
