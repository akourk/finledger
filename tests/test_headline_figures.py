"""Headline figures, checked against hand-computed answers.

Segment 5 item 2. These are the numbers the user makes decisions on and
the hardest to eyeball: a TWR that is wrong by a factor is invisible,
whereas a wrong holdings row is obvious.

Everything else in this audit tests *rules* — does this condition fire,
does that carve-out apply. This module tests **arithmetic**: fixtures
small enough to compute by hand, with the expected value derived in the
comment rather than copied from a run. A test that records whatever the
code currently produces would pin a wrong answer just as firmly as a
right one.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


def _snap(d: str, total: float) -> dict:
    return {"date": d, "total": total, "by_account_group": {"Broker": total},
            "by_account_type": {"Taxable": total}, "positions": [],
            "net_contributed": 0.0}


class TestModifiedDietzPeriodReturn:
    """r = (EV - SV - net) / (SV + net/2)

    The mid-period weighting (net/2) is the "modified" part: a flow is
    assumed to arrive halfway through, so it earns half the period.
    """

    @pytest.fixture
    def period_return(self, isolated_workdir):
        from src.analytics._shared import _period_return
        return _period_return

    def test_no_flows_is_simple_growth(self, period_return):
        """1,000 -> 1,100 with no flows = exactly 10%."""
        r = period_return(_snap("2024-01-31", 1000.0), _snap("2024-02-29", 1100.0),
                          lambda s: s["total"], [], None)
        assert r == pytest.approx(0.10)

    def test_flow_is_weighted_at_half_the_period(self, period_return):
        """SV 1,000, EV 2,200, net +1,000.

        gain  = 2200 - 1000 - 1000 = 200
        base  = 1000 + 1000/2      = 1500
        r     = 200 / 1500         = 13.333%

        The naive alternative (dividing by SV alone) would give 20% —
        crediting a full period of growth to money that was only present
        for half of it.
        """
        r = period_return(_snap("2024-01-31", 1000.0), _snap("2024-02-29", 2200.0),
                          lambda s: s["total"], [], None, extra_flow=1000.0)
        assert r == pytest.approx(200.0 / 1500.0)
        assert r != pytest.approx(0.20)

    def test_a_loss_is_negative(self, period_return):
        r = period_return(_snap("2024-01-31", 1000.0), _snap("2024-02-29", 900.0),
                          lambda s: s["total"], [], None)
        assert r == pytest.approx(-0.10)

    def test_withdrawal_does_not_read_as_a_loss(self, period_return):
        """SV 10,000, EV 5,000, net -5,000 (a withdrawal, not a loss).

        gain = 5000 - 10000 + 5000 = 0
        base = 10000 - 2500        = 7500
        r    = 0  -> flat, correctly
        """
        r = period_return(_snap("2024-01-31", 10_000.0),
                          _snap("2024-02-29", 5_000.0),
                          lambda s: s["total"], [], None, extra_flow=-5_000.0)
        assert r == pytest.approx(0.0)

    def test_bootstrap_noise_is_skipped_not_guessed(self, period_return):
        """When a flow dwarfs the balance the period is unmeasurable, and
        returning None is the honest answer — a fabricated number here
        compounds into the chain-linked lifetime figure."""
        r = period_return(_snap("2024-01-31", 100.0),
                          _snap("2024-02-29", 10_100.0),
                          lambda s: s["total"], [], None, extra_flow=10_000.0)
        assert r is None

    def test_total_loss_returns_none_rather_than_minus_one(self, period_return):
        """r <= -1 would make the chain-link product zero forever."""
        r = period_return(_snap("2024-01-31", 1000.0), _snap("2024-02-29", 0.0),
                          lambda s: s["total"], [], None)
        assert r is None


class TestXirr:
    """Money-weighted return: the rate that sets NPV of the flows to 0."""

    @pytest.fixture
    def xirr(self, isolated_workdir):
        from src.analytics._shared import _solve_xirr
        return _solve_xirr

    def test_ten_percent_over_one_year(self, xirr):
        """-1,000 at t0, +1,100 one year later -> 10%.

        Flows are (YEARS_FROM_T0, amount) — not dates and not ordinals.
        Passing ordinals silently produces astronomically small
        discount factors and a ZeroDivisionError rather than a wrong
        number, which is at least a loud failure.
        """
        assert xirr([(0.0, -1000.0), (1.0, 1100.0)]) == pytest.approx(0.10, abs=1e-4)

    def test_doubling_over_one_year(self, xirr):
        assert xirr([(0.0, -1000.0), (1.0, 2000.0)]) == pytest.approx(1.0, abs=1e-4)

    def test_a_loss_is_negative(self, xirr):
        assert xirr([(0.0, -1000.0), (1.0, 900.0)]) == pytest.approx(-0.10, abs=1e-4)

    def test_half_year_annualizes_upward(self, xirr):
        """+10% in half a year annualizes to 1.1^2 - 1 = 21%, not 20% —
        XIRR compounds rather than scaling linearly."""
        assert xirr([(0.0, -1000.0), (0.5, 1100.0)]) == pytest.approx(0.21, abs=0.005)

    def test_flat_is_zero(self, xirr):
        assert xirr([(0.0, -1000.0), (1.0, 1000.0)]) == pytest.approx(0.0, abs=1e-6)

    def test_degenerate_flows_return_none_not_a_number(self, xirr):
        """All-negative flows never bracket a root. Showing "—" beats
        fabricating a rate."""
        assert xirr([(0.0, -1000.0), (1.0, -500.0)]) is None

    def test_empty_flows_return_the_lower_bound_not_none(self, xirr):
        """A latent edge, pinned as behaviour rather than asserted correct.

        With no flows `_xnpv` sums to 0.0, so the `f_lo == 0` shortcut
        fires and the solver returns its lower bound (-99.99%) instead of
        None — a fabricated rate, which is exactly what the docstring
        says it avoids.

        It is UNREACHABLE from the real caller:
        `compute_money_weighted_return` always appends an end-value flow
        and returns None when `total_in <= 0`. So this is a gap in a
        private helper's own contract, not a live defect. Pinned so that
        if the guard upstream is ever relaxed, the consequence is
        visible here rather than as -99.99% on the Performance tab.
        """
        assert xirr([]) == pytest.approx(-0.9999)

    def test_the_caller_returns_none_when_nothing_was_invested(self,
                                                               isolated_workdir):
        """The reachable version of the case above."""
        from src.analytics._shared import compute_money_weighted_return

        history = [_snap("2024-01-31", 0.0), _snap("2024-12-31", 0.0)]
        assert compute_money_weighted_return([], history, [], None) is None


class TestDrawdown:
    """Peak-to-trough decline, as a FRACTION of the running peak.

    Note the convention: `max_drawdown` and `current_drawdown_pct` both
    hold fractions (-0.25 for a 25% decline) and the dashboard multiplies
    by 100 at render time.  The `_pct` suffix on one of them is a
    misnomer — pinned here because a future consumer reading that name
    literally would be wrong by 100x.
    """

    @pytest.fixture
    def drawdown(self, isolated_workdir):
        from src.analytics.drawdown import compute_drawdown
        return compute_drawdown

    def test_forty_percent_peak_to_trough(self, drawdown):
        """100 -> 60 off a peak of 100 is -40%, recovering at 110."""
        hist = [_snap("2024-01-31", 100.0), _snap("2024-02-29", 100.0),
                _snap("2024-03-31", 60.0), _snap("2024-04-30", 80.0),
                _snap("2024-05-31", 110.0)]
        out = drawdown(hist)
        assert out["max_drawdown"] == pytest.approx(-0.40, abs=1e-4)
        assert out["current_drawdown_pct"] == pytest.approx(0.0, abs=1e-4)

    def test_drawdown_is_measured_from_the_running_peak(self, drawdown):
        """A new high resets the peak: 100 -> 200 -> 150 is -25% (off
        200), not -50% (off the earlier 100)."""
        hist = [_snap("2024-01-31", 100.0), _snap("2024-02-29", 200.0),
                _snap("2024-03-31", 150.0)]
        out = drawdown(hist)
        assert out["max_drawdown"] == pytest.approx(-0.25, abs=1e-4)

    def test_a_monotonic_rise_has_no_drawdown(self, drawdown):
        hist = [_snap("2024-01-31", 100.0), _snap("2024-02-29", 150.0),
                _snap("2024-03-31", 200.0)]
        out = drawdown(hist)
        assert out["max_drawdown"] == pytest.approx(0.0, abs=1e-4)
        assert out["current_drawdown_pct"] == pytest.approx(0.0, abs=1e-4)

    def test_current_drawdown_when_still_underwater(self, drawdown):
        """Ending at 75 off a peak of 100 is a live -25%."""
        hist = [_snap("2024-01-31", 100.0), _snap("2024-02-29", 75.0)]
        out = drawdown(hist)
        assert out["current_drawdown_pct"] == pytest.approx(-0.25, abs=1e-4), (
            "values are FRACTIONS despite the _pct suffix; the dashboard "
            "multiplies by 100 at render time"
        )

    def test_too_few_points_returns_empty_not_garbage(self, drawdown):
        out = drawdown([_snap("2024-01-31", 100.0)])
        assert out.get("series") in (None, [])
        assert not out.get("max_drawdown")
