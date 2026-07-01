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
