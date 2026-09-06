"""The reconciliation panel must be able to REPORT A BREAK.

Companion to `test_data_health_guards.py`. `reconcile.py` was 92%
covered, but the one uncovered decision was
`_status`'s balance band — `return "warn" if pct < 0.04 else "off"` had
never executed, meaning no test had ever produced a balance row that
wasn't "ok". The panel's entire purpose is flagging breaks, and the
flagging path was unexercised.

This matters more than an ordinary coverage gap because
`docs/PLAN-audit.md`'s technique (6) runs *through* this panel: ground-truth
reconciliation is measured with it, so a defect here silently
miscalibrates Segment 7.

Synthetic round numbers only — never real portfolio figures.
"""

from __future__ import annotations

import pytest

from src.analytics.reconcile import (
    _expected_delta,
    _status,
    compute_reconciliation,
)


class TestStatusBands:
    """Every band must be reachable, for both kinds.

    Balance checks are deliberately looser than form checks ($25 floor /
    1.5% / 4% vs $5 / 0.5% / 2%) because a statement date rarely lines up
    with a snapshot and proxy-priced funds drift. Pinning both means a
    later tolerance edit has to be deliberate.
    """

    @pytest.mark.parametrize("kind,reported,delta,expected", [
        # --- balance: $25 absolute floor, then 1.5% / 4% ---
        ("balance", 10_000.0,    10.0, "ok"),    # under the $25 floor
        ("balance", 10_000.0,   100.0, "ok"),    # over floor, under 1.5%
        ("balance", 10_000.0,   200.0, "warn"),  # over 1.5%, under 4%
        ("balance", 10_000.0,   500.0, "off"),   # over 4%
        # --- form figures (1099-derived): $5 floor, then 0.5% / 2% ---
        ("realized", 10_000.0,    3.0, "ok"),
        ("realized", 10_000.0,   40.0, "ok"),
        ("realized", 10_000.0,  100.0, "warn"),
        ("realized", 10_000.0,  500.0, "off"),
        ("income",   10_000.0,  500.0, "off"),
        ("section_1256", 10_000.0, 100.0, "warn"),
    ])
    def test_band(self, kind, reported, delta, expected):
        assert _status(kind, reported, delta) == expected

    def test_sign_of_delta_does_not_change_the_band(self):
        """Banding is on magnitude — an overstatement and an
        understatement of the same size are equally serious."""
        for kind in ("balance", "realized"):
            for mag in (100.0, 500.0):
                assert _status(kind, 10_000.0, mag) == _status(kind, 10_000.0, -mag)

    def test_balance_band_is_looser_than_the_form_band(self):
        """The documented asymmetry. If these ever equalise, either the
        statement-noise rationale went away or someone edited one
        constant and not the other."""
        assert _status("balance", 10_000.0, 100.0) == "ok"
        assert _status("realized", 10_000.0, 100.0) == "warn"

    def test_zero_reported_does_not_divide_by_zero(self):
        assert _status("realized", 0.0, 0.0) == "ok"
        assert _status("realized", 0.0, 1000.0) == "off"


class TestExpectedDeltaToken:
    """`[expected ±N.NN]` declares a KNOWN delta so a documented
    difference renders 'explained' instead of crying wolf."""

    @pytest.mark.parametrize("note,want", [
        ("[expected +100.00]", 100.0),
        ("[expected -50]", -50.0),
        ("[expected 1250.00 ]", 1250.0),
        ("K-1 entity [expected +100.00] not visible on the 1099", 100.0),
        ("no token here", None),
        ("", None),
        (None, None),
    ])
    def test_parse(self, note, want):
        assert _expected_delta(note) == want

    @pytest.mark.parametrize("note", [
        "[expected +1,250.00]",   # thousands separator (documented as unsupported)
        "[expected: +100]",       # stray colon
        "[expected]",             # no amount
    ])
    def test_malformed_token_is_silently_ignored(self, note):
        """Documents a real sharp edge rather than asserting it's fine.

        A malformed token parses as *no token at all*, so the row is
        banded on its raw delta and re-flags. That fails SAFE — a typo
        produces a false alarm, never a masked break — but it is silent:
        nothing tells the user their annotation did nothing. Pinned so
        the failure direction can't silently invert into masking.
        """
        assert _expected_delta(note) is None


class TestComputeReconciliation:

    def test_returns_none_without_rows(self):
        """No Reconcile rows -> the dashboard hides the panel entirely."""
        assert compute_reconciliation([], [], []) is None

    def test_form_row_off_by_a_large_delta_is_flagged(self):
        txns = [{
            "account_group": "Broker", "date": "2024-03-01", "action": "Sell",
            "symbol": "SYM", "realized_gain": 1000.0,
        }]
        meta = [{"kind": "realized", "account_group": "Broker",
                 "date": "2024", "amount": 5000.0, "note": ""}]
        out = compute_reconciliation(txns, [], meta)
        row = out["rows"][0]
        assert row["status"] == "off", (
            "a realized figure off by 80% of the reported amount must flag"
        )
        assert row["computed"] == 1000.0
        assert row["delta"] == -4000.0
        assert out["summary"]["off"] == 1

    def test_expected_token_explains_a_known_delta(self):
        txns = [{
            "account_group": "Broker", "date": "2024-03-01", "action": "Sell",
            "symbol": "SYM", "realized_gain": 1000.0,
        }]
        meta = [{"kind": "realized", "account_group": "Broker",
                 "date": "2024", "amount": 5000.0,
                 "note": "[expected -4000.00] documented structural delta"}]
        row = compute_reconciliation(txns, [], meta)["rows"][0]
        assert row["status"] == "explained"
        assert row["residual"] == 0.0

    def test_expectation_that_drifts_re_flags(self):
        """An explanation must never become a permanent mute.

        Same declared expectation as above, but reality moved. The
        residual is what gets banded, so the row goes hot again.
        """
        txns = [{
            "account_group": "Broker", "date": "2024-03-01", "action": "Sell",
            "symbol": "SYM", "realized_gain": 1000.0,
        }]
        meta = [{"kind": "realized", "account_group": "Broker",
                 "date": "2024", "amount": 9000.0,
                 "note": "[expected -4000.00] documented structural delta"}]
        row = compute_reconciliation(txns, [], meta)["rows"][0]
        assert row["status"] == "off"
        assert row["residual"] == -4000.0

    def test_balance_dated_past_the_last_snapshot_reports_nodata(self):
        """fin must say "I can't speak to this" rather than answering a
        future-dated statement with today's positions."""
        history = [{"date": "2024-01-31", "total": 1000.0}]
        meta = [{"kind": "balance", "account_group": "Broker",
                 "date": "2099-12-31", "amount": 1000.0, "note": ""}]
        row = compute_reconciliation([], history, meta)["rows"][0]
        assert row["status"] == "nodata"
        assert row["computed"] is None
        assert row["delta"] is None

    def test_unknown_kind_is_skipped(self):
        meta = [{"kind": "not_a_real_kind", "account_group": "Broker",
                 "date": "2024", "amount": 1.0, "note": ""}]
        out = compute_reconciliation([], [], meta)
        assert out["rows"] == []
        assert out["summary"]["total"] == 0
