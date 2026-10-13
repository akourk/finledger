"""Dated broker price inputs use only independently fictional local marks."""

import csv
from copy import deepcopy
import json

import pytest


def _write(root, rows, *, headers=("Date", "Symbol", "Price")):
    path = root / "data" / "broker-prices.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def test_missing_and_header_only_files_have_no_observations(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = isolated_workdir / "data" / "broker-prices.csv"
    assert load_broker_prices(path) == {}
    assert not path.exists()
    assert load_broker_prices(_write(isolated_workdir, [])) == {}


def test_marks_are_dated_canonical_units_without_transaction_fields(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [
        [" 2024-01-03 ", " Example Fund ", "$25.50"],
        ["2024-01-02", "FAKEA", "1,250.00"],
        ["2024-01-04", "FAKE-USD", "2e1"],
        ["2024-01-02", "FAKEA 2024-06-21 Call $100", "0"],
        ["2024-01-02", "Example Fund", ".5"],
    ])

    prices = load_broker_prices(path)

    assert prices == {
        "Example Fund": {"2024-01-02": 0.5, "2024-01-03": 25.5},
        "FAKE-USD": {"2024-01-04": 20.0},
        "FAKEA": {"2024-01-02": 1250.0},
        "FAKEA 2024-06-21 Call $100": {"2024-01-02": 0.0},
    }
    assert list(prices) == sorted(prices)
    assert list(prices["Example Fund"]) == ["2024-01-02", "2024-01-03"]
    serialized = json.dumps(prices, allow_nan=False)
    assert all(field not in serialized for field in ("account", "quantity", "basis", "source"))


def test_identical_duplicates_are_idempotent_after_normalization(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [
        ["2024-01-02", "FAKEA", "20"],
        [" 2024-01-02 ", " FAKEA ", "$20.00"],
    ])

    assert load_broker_prices(path) == {"FAKEA": {"2024-01-02": 20.0}}


def test_conflicting_duplicate_rejects_complete_input_with_redacted_location(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [
        ["2024-01-02", "FAKEA", "20"], ["2024-01-02", "FAKEA", "25"],
    ])

    with pytest.raises(ValueError) as error:
        load_broker_prices(path)

    assert str(error.value) == (
        "broker-prices.csv: row 3, column Price: conflicting price for this symbol/date")


@pytest.mark.parametrize("value", ["-1", "($20)", "NaN", "Infinity", "1e999", "1,2", "20oops", ""])
def test_invalid_prices_fail_with_location_without_cell_contents(isolated_workdir, value):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [["2024-01-02", "FAKEA", value]])

    with pytest.raises(ValueError) as error:
        load_broker_prices(path)

    message = str(error.value)
    assert "broker-prices.csv: row 2, column Price:" in message
    assert "FAKEA" not in message
    if value:
        assert value not in message


@pytest.mark.parametrize("value", ["2024-02-30", "01/02/2024", "20240102", "2024-1-2", "2024-01-02T12:00:00", ""])
def test_dates_must_be_canonical_iso_without_silent_drops(isolated_workdir, value):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [[value, "FAKEA", "20"]])

    with pytest.raises(ValueError) as error:
        load_broker_prices(path)

    message = str(error.value)
    assert "broker-prices.csv: row 2, column Date:" in message
    if value:
        assert value not in message


@pytest.mark.parametrize("headers, row, expected", [
    (["Date", "Symbol"], ["2024-01-02", "FAKEA"], "missing required CSV column(s): Price"),
    (["Date", "Symbol", "Price", "Price"], ["2024-01-02", "FAKEA", "20", "20"], "duplicate CSV column names"),
    (["Date", "Symbol", "Price"], ["2024-01-02", "FAKEA"], "missing required cells"),
    (["Date", "Symbol", "Price"], ["2024-01-02", "FAKEA", "20", "extra"], "more cells than header columns"),
    (["Date", "Symbol", "Price"], ["2024-01-02", "", "20"], "column Symbol: required value is blank"),
])
def test_malformed_csv_shape_cannot_produce_partial_prices(isolated_workdir, headers, row, expected):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [row], headers=headers)

    with pytest.raises(ValueError, match=expected.replace("(", r"\(").replace(")", r"\)")):
        load_broker_prices(path)


def test_extra_columns_preserve_multiline_csv_record_and_error_locations(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [
        ["2024-01-02", "FAKEA", "20", "fictional first line\nsecond line"],
        ["2024-01-03", "FAKEA", "invalid", ""],
    ], headers=["Date", "Symbol", "Price", "Extra"])

    with pytest.raises(ValueError) as error:
        load_broker_prices(path)

    assert "row 4, column Price: invalid number" in str(error.value)
    path = _write(isolated_workdir, [["2024-01-02", "FAKEA", "20", "ignored"]],
                  headers=["Date", "Symbol", "Price", "Extra"])
    assert load_broker_prices(path) == {"FAKEA": {"2024-01-02": 20.0}}


def test_unclosed_csv_quote_is_rejected(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = isolated_workdir / "data" / "broker-prices.csv"
    path.write_text('Date,Symbol,Price\n2024-01-02,FAKEA,"20', encoding="utf-8")

    with pytest.raises(ValueError, match="malformed CSV quoting"):
        load_broker_prices(path)


def test_reloads_are_atomic_and_never_modify_inputs_or_existing_outputs(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = _write(isolated_workdir, [["2024-01-02", "FAKEA", "20"]])
    old = load_broker_prices(path)
    expected = deepcopy(old)
    _write(isolated_workdir, [["2024-01-03", "FAKEA", "25"],
                             ["2024-01-04", "FAKEA", "invalid"]])
    (isolated_workdir / "cache" / "fictional.json").write_text("{}", encoding="utf-8")
    (isolated_workdir / "exports" / "fictional.html").write_text("fictional", encoding="utf-8")
    before = {p.relative_to(isolated_workdir): p.read_bytes()
              for p in isolated_workdir.rglob("*") if p.is_file()}

    with pytest.raises(ValueError):
        load_broker_prices(path)

    assert old == expected
    assert before == {p.relative_to(isolated_workdir): p.read_bytes()
                      for p in isolated_workdir.rglob("*") if p.is_file()}
    _write(isolated_workdir, [["2024-01-03", "FAKEA", "25"]])
    assert load_broker_prices(path) == {"FAKEA": {"2024-01-03": 25.0}}


def test_arbitrary_input_paths_and_invalid_encoding_are_redacted(isolated_workdir):
    from src.broker_prices import load_broker_prices

    path = isolated_workdir / "data" / "fictional-private-label.csv"
    path.write_bytes(b"\xff\xff")
    with pytest.raises(ValueError) as error:
        load_broker_prices(path)
    assert str(error.value) == "broker-prices.csv: expected UTF-8 CSV text"

    with pytest.raises(OSError) as error:
        load_broker_prices(isolated_workdir / "data")
    assert str(error.value) == "broker-prices.csv: unable to read price input"
