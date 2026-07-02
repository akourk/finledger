"""Budget rollup (analytics/budget.py) + `Budget` metadata row parsing."""

from __future__ import annotations

import pytest


def _rows():
    return [
        {"label": "Rent", "category": "Housing", "amount": 1800.0,
         "cadence": "monthly", "date": "2024-01-01"},
        {"label": "Netflix", "category": "Subscriptions", "amount": 15.99,
         "cadence": "monthly", "date": ""},
        {"label": "Car insurance", "category": "Insurance", "amount": 1200.0,
         "cadence": "yearly", "date": ""},
    ]


def test_budget_normalizes_cadence_to_monthly():
    from src.analytics.budget import compute_budget
    out = compute_budget(_rows())
    assert out is not None
    by_label = {r["label"]: r for r in out["rows"]}
    assert by_label["Rent"]["monthly"] == 1800.0
    assert by_label["Netflix"]["monthly"] == 15.99
    assert by_label["Car insurance"]["monthly"] == 100.0   # 1200 / 12
    assert out["monthly_total"] == pytest.approx(1915.99)
    assert out["annual_total"] == pytest.approx(1915.99 * 12)


def test_budget_latest_row_per_label_wins():
    """A rent increase is a new row; Amount=0 cancels a subscription."""
    from src.analytics.budget import compute_budget
    rows = _rows() + [
        {"label": "Rent", "category": "Housing", "amount": 2000.0,
         "cadence": "monthly", "date": "2025-06-01"},
        {"label": "Netflix", "category": "Subscriptions", "amount": 0.0,
         "cadence": "monthly", "date": "2025-01-01"},
    ]
    out = compute_budget(rows)
    by_label = {r["label"]: r for r in out["rows"]}
    assert by_label["Rent"]["monthly"] == 2000.0
    assert "Netflix" not in by_label          # cancelled
    assert out["superseded_count"] == 2


def test_budget_by_category_and_coverage():
    from src.analytics.budget import compute_budget
    out = compute_budget(_rows(), annual_expenses=40000.0, ttm_income=2300.0)
    cats = {c["category"]: c for c in out["by_category"]}
    assert cats["Housing"]["monthly"] == 1800.0
    assert cats["Insurance"]["annual"] == pytest.approx(1200.0)
    # Coverage: ttm income ÷ annualized budget
    assert out["income_coverage_pct"] == pytest.approx(
        2300.0 / (1915.99 * 12) * 100, abs=0.01)
    # Bottom-up vs declared lump
    v = out["vs_annual_expenses"]
    assert v["annual_expenses"] == 40000.0
    assert v["delta"] == pytest.approx(1915.99 * 12 - 40000.0, abs=0.01)


def test_budget_empty_inputs_return_none():
    from src.analytics.budget import compute_budget
    assert compute_budget(None) is None
    assert compute_budget([]) is None
    # All rows cancelled → nothing to show
    assert compute_budget([{"label": "X", "category": "Y", "amount": 0.0,
                            "cadence": "monthly", "date": ""}]) is None


def test_metadata_parses_budget_rows(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Budget,2024-01-01,1800,Housing,Rent\n"
        "Budget,,15.99,Subscriptions,Netflix @monthly\n"
        "Budget,,1200,Insurance,Car insurance @yearly\n"
        "Budget,,45,Utilities,Internet @Monthly\n"
        "Budget,,10,Subscriptions,user@example.com hosting\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    b = {r["label"]: r for r in meta["budget"]}
    assert b["Rent"]["cadence"] == "monthly"
    assert b["Rent"]["amount"] == 1800.0
    assert b["Rent"]["category"] == "Housing"
    assert b["Netflix"]["cadence"] == "monthly"
    assert b["Car insurance"]["cadence"] == "yearly"
    assert b["Internet"]["cadence"] == "monthly"      # case-insensitive
    # '@' that isn't a cadence suffix stays part of the label.
    assert "user@example.com hosting" in b
    assert b["user@example.com hosting"]["cadence"] == "monthly"
