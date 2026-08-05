"""Every alert source must be able to fire, and stay quiet otherwise.

`alerts.py` is the Overview tab's "Attention" panel — the one place the
user is expected to look for problems. It had no dedicated test file:
80% line coverage, with five of the seven `alerts.append(...)` sites
never executed by the suite, so most alert kinds had never been emitted
in a test.

Each source gets an input that must produce it and a neutral input that
must not. The neutral half is the important one: an alert that fires
unconditionally is noise, and noise trains the user to ignore the
panel — which is worse than the alert not existing.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

from src.analytics.alerts import compute_alerts


def _call(**over) -> list[dict]:
    """compute_alerts with everything neutral except what's overridden."""
    kwargs = {
        "txns": [],
        "holdings_by_account": [],
        "history": [],
        "tax_analytics": {},
        "concentration": {},
        "price_meta": {},
        "cusip_collisions": None,
    }
    kwargs.update(over)
    return compute_alerts(**kwargs)


def _kinds(alerts: list[dict]) -> set[str]:
    return {a["kind"] for a in alerts}


# (id, kind_substring, triggering_kwargs)
SOURCES = [
    ("concentration", "concentration_", {
        "concentration": {"flags": [
            {"kind": "top5", "severity": "warn", "message": "top 5 concentrated"},
        ]},
    }),
    ("harvest_lots", "harvest", {
        "tax_analytics": {"harvest_lots": [
            {"symbol": "SYM", "loss": -500.0, "account_group": "Broker",
             "wash_risk": False},
        ]},
    }),
    ("harvest_candidates_fallback", "harvest", {
        "tax_analytics": {"harvest_candidates": [
            {"symbol": "SYM", "unrealized_gain": -500.0},
        ]},
    }),
    ("stale_data", "stale_data", {
        "txns": [{"date": "2020-01-01"}],
    }),
    ("coverage_gap", "coverage_gap", {
        "history": [{"date": "2024-01-31", "priced_pct": 0.5}],
    }),
    ("fetch_failing", "fetch_failing", {
        "price_meta": {"symbols": {"DEAD": {"failure_count": 3}}},
    }),
    ("cusip_rename", "cusip_rename", {
        "cusip_collisions": [{"stale": "OLD", "canon": "NEW", "cusip": "000000000"}],
    }),
    ("long_term_soon", "long_term_soon", {
        "tax_analytics": {"lt_horizon": [
            {"symbol": "SYM", "is_long_term": False, "days_to_lt": 30,
             "unrealized_gain": 500.0},
        ]},
    }),
]

_IDS = [s[0] for s in SOURCES]


class TestEveryAlertSourceCanFire:

    @pytest.mark.parametrize("_id,kind,trigger", SOURCES, ids=_IDS)
    def test_fires(self, _id, kind, trigger):
        alerts = _call(**trigger)
        assert any(kind in k for k in _kinds(alerts)), (
            f"no {kind!r} alert produced by {trigger!r} — this source cannot "
            "fire, so the Attention panel is blind to it"
        )
        for a in alerts:
            assert a.get("severity") in {"info", "warn", "high"}
            assert a.get("message"), "an alert with no message renders blank"

    def test_neutral_input_produces_nothing(self):
        assert _call() == [], (
            "alerts fired on a wholly empty portfolio — an alert that always "
            "fires is noise, and noise trains the user to ignore the panel"
        )


class TestAlertThresholds:
    """Near-misses. Each threshold is a decision; pin it."""

    def test_fresh_transactions_are_not_stale(self):
        from datetime import date
        assert "stale_data" not in _kinds(_call(txns=[{"date": date.today().isoformat()}]))

    def test_fully_priced_history_reports_no_coverage_gap(self):
        assert "coverage_gap" not in _kinds(
            _call(history=[{"date": "2024-01-31", "priced_pct": 1.0}]))

    def test_symbols_that_are_not_failing_do_not_alert(self):
        assert "fetch_failing" not in _kinds(
            _call(price_meta={"symbols": {"FINE": {"failure_count": 0}}}))

    def test_already_long_term_lots_do_not_alert(self):
        assert "long_term_soon" not in _kinds(_call(tax_analytics={"lt_horizon": [
            {"symbol": "SYM", "is_long_term": True, "days_to_lt": 0,
             "unrealized_gain": 500.0}]}))

    def test_lot_beyond_the_60_day_horizon_does_not_alert(self):
        assert "long_term_soon" not in _kinds(_call(tax_analytics={"lt_horizon": [
            {"symbol": "SYM", "is_long_term": False, "days_to_lt": 90,
             "unrealized_gain": 500.0}]}))

    def test_small_unrealized_gain_does_not_alert(self):
        """Below the $100 floor there's no decision to make, so staying
        silent is the point — otherwise every dust lot generates a card."""
        assert "long_term_soon" not in _kinds(_call(tax_analytics={"lt_horizon": [
            {"symbol": "SYM", "is_long_term": False, "days_to_lt": 30,
             "unrealized_gain": 10.0}]}))

    def test_harvest_lots_preferred_over_position_level_candidates(self):
        """The per-lot list is taxable-only; the position-level list has
        no account filter, so a loss inside an IRA (never deductible)
        could be surfaced. When both exist, only the per-lot one is used.
        """
        alerts = _call(tax_analytics={
            "harvest_lots": [{"symbol": "GOOD", "loss": -500.0,
                              "account_group": "Broker", "wash_risk": False}],
            "harvest_candidates": [{"symbol": "IRAONLY",
                                    "unrealized_gain": -900.0}],
        })
        msgs = " ".join(a["message"] for a in alerts)
        assert "GOOD" in msgs
        assert "IRAONLY" not in msgs

    def test_wash_risk_is_called_out_in_the_message(self):
        alerts = _call(tax_analytics={"harvest_lots": [
            {"symbol": "SYM", "loss": -500.0, "account_group": "Broker",
             "wash_risk": True}]})
        assert any("wash" in a["message"].lower() for a in alerts)
