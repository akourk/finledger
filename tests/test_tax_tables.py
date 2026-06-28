"""Tests for the single-sourced federal tax tables emitted to the JSON.

The dashboard JS reads DATA.tax_tables instead of hardcoding brackets, so
the emitted payload must (a) be finite JSON and (b) faithfully reflect the
Python tables that analytics/tax.py actually computes against.
"""

from __future__ import annotations

import json
import math


def test_tax_tables_emit_is_finite_json():
    from src.analytics.tax import tax_tables_to_json
    tt = tax_tables_to_json()
    # allow_nan=False raises on any inf/nan — guards the no-non-finite rule.
    json.dumps(tt, allow_nan=False)
    for key in ("federal_brackets", "ltcg_brackets", "std_deduction",
                "k401_limit", "roth_magi_phaseout", "section_1256_underlyings"):
        assert key in tt, f"tax_tables missing {key}"


def test_top_bracket_threshold_is_null_sentinel():
    """The open-ended top bracket emits null (→ Infinity in JS), never a
    non-finite literal."""
    from src.analytics.tax import tax_tables_to_json
    tt = tax_tables_to_json()
    top = tt["federal_brackets"]["2025"]["Single"][-1]
    assert top[0] is None and top[1] == 0.37


def test_emit_matches_python_source_tables():
    """The emitted tables must match the in-Python tables the analytics
    uses — otherwise the JS and Python tax math would diverge again."""
    from src.analytics import tax as T
    tt = T.tax_tables_to_json()
    # Spot-check every (year, status) bracket row round-trips, with inf↔null.
    for (yr, status), rows in T._BRACKETS_BY_YEAR_STATUS.items():
        emitted = tt["federal_brackets"][str(yr)][status]
        for (p_thr, p_rate), (j_thr, j_rate) in zip(rows, emitted):
            exp = None if math.isinf(p_thr) else p_thr
            assert (j_thr, j_rate) == (exp, p_rate)
    for (yr, status), amt in T._STD_DED_BY_YEAR_STATUS.items():
        assert tt["std_deduction"][str(yr)][status] == amt
    assert set(tt["section_1256_underlyings"]) == set(T.SECTION_1256_UNDERLYINGS)


def test_form_8949_taxable_only_and_term_classified():
    from src.analytics.tax import _build_form_8949
    rows = _build_form_8949([
        {"account_type": "Taxable", "account_group": "Robinhood",
         "symbol": "AAPL", "date": "2025-06-01", "quantity": 10,
         "amount": 2000, "cost_basis": 1500, "realized_gain": 500,
         "holding_days": 400},
        {"account_type": "Taxable", "account_group": "Robinhood",
         "symbol": "TSLA", "date": "2025-03-01", "quantity": 5,
         "amount": 1000, "cost_basis": 1200, "realized_gain": -200,
         "holding_days": 100},
        # Retirement disposal must be excluded (not taxable).
        {"account_type": "Retirement", "account_group": "Roth IRA",
         "symbol": "VOO", "date": "2025-01-01", "quantity": 1,
         "amount": 400, "cost_basis": 300, "realized_gain": 100,
         "holding_days": 50},
    ])
    assert len(rows) == 2, "retirement disposal should be excluded"
    by_sym = {r["description"].split()[1]: r for r in rows}
    assert by_sym["AAPL"]["term"] == "long"     # > 365 days
    assert by_sym["TSLA"]["term"] == "short"    # < 365 days
    assert by_sym["AAPL"]["date_acquired"] == "2024-04-27"  # sold - holding_days
    assert by_sym["TSLA"]["gain"] == -200.0


def test_form_8949_various_when_no_holding_days():
    from src.analytics.tax import _build_form_8949
    rows = _build_form_8949([
        {"account_type": "Taxable", "symbol": "X", "date": "2025-01-01",
         "quantity": 1, "amount": 10, "cost_basis": 5, "realized_gain": 5},
    ])
    assert rows[0]["date_acquired"] == "VARIOUS"


def test_estimated_cap_gains_tax_federal_state_niit():
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single", "state_tax_rate": 0.093,
            "salary_history": [{"date": "2024-01-01", "amount": 250000}]}
    txns = [{"date": "2026-03-01", "account_type": "Taxable", "symbol": "AAPL",
             "realized_gain": 50000, "holding_days": 400,
             "amount": 100000, "cost_basis": 50000}]
    e = _tax_rate_estimate("2026", meta, txns)
    assert e["realized_lt"] == 50000.0
    assert e["est_cap_gains_tax_federal"] == 7500.0   # 50k × 15% LTCG
    assert e["est_cap_gains_tax_state"] == 4650.0      # 50k × 9.3%
    assert e["est_niit"] == 1900.0                      # 3.8% × 50k (above MAGI thresh)
    assert e["est_cap_gains_tax_total"] == 14050.0
    assert e["est_quarterly_payment"] == 3512.5


def test_estimated_cap_gains_tax_no_state_no_niit_low_income():
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single", "state_tax_rate": 0.0,
            "salary_history": [{"date": "2024-01-01", "amount": 40000}]}
    txns = [{"date": "2026-02-01", "account_type": "Taxable", "symbol": "X",
             "realized_gain": 2000, "holding_days": 100,
             "amount": 5000, "cost_basis": 3000}]
    e = _tax_rate_estimate("2026", meta, txns)
    assert e["est_cap_gains_tax_state"] == 0.0
    assert e["est_niit"] == 0.0   # AGI well under $200k threshold


def test_bracket_fill_top_room_left_is_none_not_inf():
    """The open-ended top bracket's room_left must be None, never inf —
    inf would serialize as an invalid JSON literal and leak Infinity into
    the dashboard (same class as the NaN price-cache bug)."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2024-01-01", "amount": 80000}]}
    e = _tax_rate_estimate("2025", meta, [])
    bf = e["bracket_fill"]
    assert bf[-1]["room_left"] is None, "top bracket room_left should be None"
    assert all(b["room_left"] is None or math.isfinite(b["room_left"]) for b in bf)
    json.dumps(e, allow_nan=False)   # whole estimate must be finite


def test_tax_tables_embedded_in_export():
    """export_json must include tax_tables at the top level (the JS reads
    DATA.tax_tables)."""
    from src.export import export_json
    import tempfile, os
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.json"
        export_json([], out)
        data = json.loads(out.read_text(encoding="utf-8"))
    assert "tax_tables" in data
    assert "federal_brackets" in data["tax_tables"]
