"""Account activity dates use independently fictional, dated CSV provenance."""

from copy import deepcopy
from datetime import date
import json

import pytest


def _txn(group, day, *, source="fictional.csv", account_type="Taxable"):
    return {"account_group": group, "account_type": account_type,
            "date": day, "source": source}


def _holding(group, account_type="Taxable"):
    return {"account_group": group, "account_type": account_type,
            "symbol": "FAKEA", "quantity": 2.0}


def _alerts(txns, holdings, **kwargs):
    from src.analytics.alerts import compute_alerts

    return compute_alerts(txns, holdings, [], {}, {}, {}, **kwargs)


def test_recent_activity_in_one_account_cannot_mask_another(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    txns = [_txn("Quiet IRA", "2024-01-01", account_type="Retirement"),
            _txn("Active", "2024-04-10"), _txn("Active", "2024-04-09")]
    holdings = [_holding("Quiet IRA", "Retirement"), _holding("Active")]

    rows = compute_account_freshness(txns, holdings, as_of="2024-04-10")

    assert rows == [
        {"account_group": "Active", "account_type": "Taxable",
         "latest_transaction_date": "2024-04-10", "days_since_latest": 0,
         "transaction_count": 2},
        {"account_group": "Quiet IRA", "account_type": "Retirement",
         "latest_transaction_date": "2024-01-01", "days_since_latest": 100,
         "transaction_count": 1},
    ]
    alerts = _alerts(txns, holdings, account_freshness=rows)
    assert len(alerts) == 1
    assert alerts[0]["account_group"] == "Quiet IRA"
    assert alerts[0]["kind"] == "account_transaction_age"
    assert alerts[0]["severity"] == "info"
    assert "inactivity may be normal" in alerts[0]["message"]
    assert "does not establish missing activity" in alerts[0]["message"]


def test_closed_accounts_keep_their_dates_without_age_alerts(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    txns = [_txn("Closed", "2020-01-02"), _txn("Current", "2024-04-10")]
    holdings = [_holding("Current")]

    rows = compute_account_freshness(txns, holdings, as_of="2024-04-10")

    assert [row["account_group"] for row in rows] == ["Closed", "Current"]
    assert rows[0]["latest_transaction_date"] == "2020-01-02"
    assert _alerts(txns, holdings, account_freshness=rows) == []


def test_future_or_invalid_records_never_move_an_as_of_date(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    txns = [_txn("Known", "2024-04-08"), _txn("Known", "2024-04-11"),
            _txn("Future only", "2024-04-11"), _txn("Known", "2024-02-30"),
            _txn("Known", "20240409"), _txn("Known", None)]

    rows = compute_account_freshness(txns, [], as_of="2024-04-10")

    assert rows == [{
        "account_group": "Known", "account_type": "Taxable",
        "latest_transaction_date": "2024-04-08", "days_since_latest": 2,
        "transaction_count": 1,
    }]


@pytest.mark.parametrize("source", [
    "auto-balance-anchor", "auto-reconcile-transfer",
    "auto-reconcile-example-funding", "", None,
])
def test_inferred_or_unproven_rows_cannot_make_imports_look_recent(isolated_workdir, source):
    from src.analytics.freshness import compute_account_freshness

    txns = [_txn("Example", "2024-01-01"),
            _txn("Example", "2024-04-10", source=source)]

    row, = compute_account_freshness(txns, [], as_of="2024-04-10")

    assert row["latest_transaction_date"] == "2024-01-01"
    assert row["transaction_count"] == 1
    assert row["days_since_latest"] == 100


def test_held_account_without_imported_records_is_unknown(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    # A legacy export can lack source provenance; inferred rows are also not
    # evidence of a CSV import. Holdings still identify the account to display.
    txns = [{"account_group": "Legacy", "date": "2024-04-10"},
            _txn("Inferred", "2024-04-10", source="auto-balance-anchor"),
            _txn("Future", "2024-04-11")]
    holdings = [_holding(group) for group in ("Legacy", "Inferred", "Future")]

    rows = compute_account_freshness(txns, holdings, as_of="2024-04-10")

    assert [row["account_group"] for row in rows] == ["Future", "Inferred", "Legacy"]
    for row in rows:
        assert row["latest_transaction_date"] is None
        assert row["days_since_latest"] is None
        assert row["transaction_count"] == 0
    alerts = _alerts(txns, holdings, account_freshness=rows)
    assert len(alerts) == 3
    assert all(alert["severity"] == "info" for alert in alerts)
    assert all("unknown" in alert["message"] for alert in alerts)


def test_manual_csv_dates_count_without_exporting_source_names(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    txns = [_txn("Example", "2024-04-09", source="manual-adjustments.csv"),
            _txn("Example", "2024-04-10", source="fictional-second.CSV")]
    before = deepcopy(txns)

    rows = compute_account_freshness(txns, [], as_of=date(2024, 4, 10))

    assert rows[0]["transaction_count"] == 2
    assert rows[0]["latest_transaction_date"] == "2024-04-10"
    assert rows == compute_account_freshness(list(reversed(txns)), [], as_of="2024-04-10")
    assert txns == before
    serialized = json.dumps(rows, allow_nan=False)
    assert "source" not in serialized
    assert ".csv" not in serialized.lower()


def test_clock_date_is_used_only_when_no_as_of_supplied(isolated_workdir, monkeypatch):
    from src.analytics.freshness import compute_account_freshness

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-04-10")
    txns = [_txn("Example", "2024-04-09")]

    assert compute_account_freshness(txns, [])[0]["days_since_latest"] == 1
    assert compute_account_freshness(txns, [], as_of="2024-04-09")[0]["days_since_latest"] == 0
    assert compute_account_freshness([], []) == []


@pytest.mark.parametrize("age, expected", [(30, 0), (31, 1), (100, 1)])
def test_age_signals_remain_informational_and_use_shared_rows(isolated_workdir, age, expected):
    # Supplied analytics dates must not be recomputed from unrelated later rows.
    txns = [_txn("Example", "2024-04-10")]
    rows = [{"account_group": "Example", "account_type": "Taxable",
             "latest_transaction_date": "2024-01-01", "days_since_latest": age,
             "transaction_count": 1}]

    alerts = _alerts(txns, [_holding("Example")], account_freshness=rows)

    assert len(alerts) == expected
    assert all(alert["severity"] == "info" for alert in alerts)


def test_invalid_as_of_does_not_silently_use_today(isolated_workdir):
    from src.analytics.freshness import compute_account_freshness

    with pytest.raises(ValueError, match="as_of"):
        compute_account_freshness([], [], as_of="invalid")


@pytest.mark.parametrize("history_day, expected_date, expected_age, expected_count", [
    ("2024-04-11", "2024-04-09", 1, 1),
    ("2024-04-08", None, None, 0),
])
def test_analytics_caps_activity_at_export_and_run_dates(
    isolated_workdir, stub_prices, monkeypatch,
    history_day, expected_date, expected_age, expected_count,
):
    from src.analytics import build_analytics

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-04-10")
    # Neutral, zero-amount imported records isolate transaction dates from
    # financial changes while exercising the actual analytics orchestrator.
    txns = [dict(_txn("Example", day), account="Example", action="Neutral",
                 symbol="USD", quantity=0.0, price=1.0, amount=0.0, fees=0.0)
            for day in ("2024-04-09", "2024-04-11")]
    history = [{"date": history_day, "total": 0.0, "total_cost_basis": 0.0,
                "net_contributed": 0.0, "positions": [], "by_account_group": {}}]

    analytics = build_analytics(txns, history, [], [])

    rows = analytics["account_freshness"]
    if expected_date is None:
        assert rows == []
    else:
        assert rows == [{
            "account_group": "Example", "account_type": "Taxable",
            "latest_transaction_date": expected_date,
            "days_since_latest": expected_age, "transaction_count": expected_count,
        }]
