"""Robinhood informational footers, using independently fictional CSV data."""

import csv
import io

import pytest


HEADERS = [
    "Activity Date", "Process Date", "Settle Date", "Instrument",
    "Description", "Trans Code", "Quantity", "Price", "Amount",
]
NOTICE = "The data provided is for informational purposes only. Fictional test notice."
BUY = ["01/15/2024", "", "", "AAA", "Fictional purchase", "Buy", "2", "$50.00", "($100.00)"]


def _csv_text(rows, headers=HEADERS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def _write(root, rows, headers=HEADERS):
    path = root / "data" / "robinhood.csv"
    path.write_text(_csv_text(rows, headers), encoding="utf-8", newline="")
    return path


def test_informational_footer_preserves_transactions_without_findings(
    isolated_workdir, capsys
):
    from src.parsers import parse_all_files, parse_report, validate_ingestion

    _write(isolated_workdir, [BUY, [""] * 9 + [NOTICE]])
    txns = parse_all_files(isolated_workdir / "data")

    assert txns == [{
        "date": "2024-01-15", "account": "Robinhood", "symbol": "AAA",
        "action": "Buy", "quantity": 2.0, "price": 50.0,
        "fees": 0.0, "amount": 100.0, "description": "Fictional purchase",
        "source": "robinhood.csv", "cusip": None,
    }]
    assert parse_report() == []
    assert "WARNING" not in capsys.readouterr().out
    validate_ingestion(txns)


def test_footer_alone_is_not_a_dropped_or_substantive_row(isolated_workdir, capsys):
    from src.parsers import (
        parse_all_files, parse_report, parse_robinhood, validate_ingestion,
    )
    from src.parsers._helpers import take_date_failures, take_substantive_rows

    path = _write(isolated_workdir, [[""] * 9 + [NOTICE]])
    assert parse_robinhood(path) == []
    assert take_substantive_rows() == 0
    assert take_date_failures() == 0

    txns = parse_all_files(isolated_workdir / "data")
    assert txns == []
    assert parse_report() == []
    assert "WARNING" not in capsys.readouterr().out
    with pytest.raises(ValueError, match="No valid transactions"):
        validate_ingestion(txns)


def test_snapshot_round_trip_keeps_multiline_transaction_and_footer(
    isolated_workdir, tmp_path
):
    from src.parsers import parse_all_files, parse_report, validate_ingestion
    from src.snapshot import export_snapshot, import_snapshot

    buy = list(BUY)
    buy[4] = 'Fictional purchase, first line\nSecond line with "quoted" text'
    path = _write(isolated_workdir, [buy, [""] * 9 + [NOTICE]])
    before = parse_all_files(isolated_workdir / "data")
    validate_ingestion(before)
    assert before[0]["description"] == buy[4]

    snapshot = tmp_path / "snapshot.json"
    restored_dir = tmp_path / "restored" / "data"
    bundle = export_snapshot(isolated_workdir / "data", snapshot)
    result = import_snapshot(snapshot, restored_dir)

    assert result["written"] == [path.name]
    assert bundle["files"][path.name] == path.read_text(encoding="utf-8")
    assert (restored_dir / path.name).read_text(encoding="utf-8") == bundle["files"][path.name]
    after = parse_all_files(restored_dir)
    assert after == before
    assert parse_report() == []
    validate_ingestion(after)


@pytest.mark.parametrize("extra", [NOTICE, "Unexpected extra transaction cell"])
def test_overflow_transaction_is_still_rejected(isolated_workdir, extra):
    from src.parsers import parse_robinhood

    path = _write(isolated_workdir, [BUY + [extra], [""] * 9 + [NOTICE]])
    with pytest.raises(ValueError, match=r"robinhood.csv: row 2: more cells than header columns"):
        parse_robinhood(path)


@pytest.mark.parametrize("extra_cells", [
    ["Unrecognized informational footer"],
    [NOTICE, ""],
    [NOTICE, "Unexpected extra footer cell"],
    ["", NOTICE],
])
def test_unknown_or_wider_footer_is_rejected(isolated_workdir, extra_cells):
    from src.parsers import parse_robinhood

    path = _write(isolated_workdir, [BUY, [""] * 9 + extra_cells])
    with pytest.raises(ValueError, match=r"robinhood.csv: row 3: more cells than header columns"):
        parse_robinhood(path)


@pytest.mark.parametrize("column", range(len(HEADERS)), ids=HEADERS)
def test_known_notice_does_not_hide_a_nonblank_header_cell(isolated_workdir, column):
    from src.parsers import parse_robinhood

    footer = [""] * 9 + [NOTICE]
    footer[column] = "Unexpected transaction data"
    path = _write(isolated_workdir, [footer])
    with pytest.raises(ValueError, match="more cells than header columns"):
        parse_robinhood(path)


@pytest.mark.parametrize("headers", [
    ["Changed Date"] + HEADERS[1:],
    HEADERS[:1] + ["Changed Process Date"] + HEADERS[2:],
    HEADERS[:-1] + ["Price"],
    HEADERS + ["Extra column"],
])
def test_footer_exception_does_not_hide_header_drift(isolated_workdir, headers):
    from src.parsers import parse_robinhood

    path = _write(isolated_workdir, [[""] * len(headers) + [NOTICE]], headers)
    with pytest.raises(ValueError):
        parse_robinhood(path)


def test_shared_reader_has_no_generic_footer_exception(isolated_workdir):
    from src.parsers._helpers import read_csv_rows

    content = _csv_text([[""] * 9 + [NOTICE]])
    with pytest.raises(ValueError, match="more cells than header columns"):
        list(read_csv_rows(io.StringIO(content), "example.csv", required=HEADERS))


def test_other_broker_still_rejects_the_same_informational_notice(isolated_workdir):
    from src.parsers import parse_manual

    headers = ["Account", "Date", "Type", "Symbol", "Quantity", "Price", "Amount", "Description"]
    path = isolated_workdir / "data" / "manual-adjustments.csv"
    path.write_text(_csv_text([[""] * len(headers) + [NOTICE]], headers),
                    encoding="utf-8", newline="")
    with pytest.raises(ValueError, match="more cells than header columns"):
        parse_manual(path)


def test_errors_after_multiline_records_keep_physical_line_locations(isolated_workdir):
    from src.parsers import parse_robinhood

    buy = list(BUY)
    buy[4] = "Fictional purchase\nSecond description line"
    bad_buy = list(BUY)
    bad_buy[8] = "invalid-number"
    footer = [""] * 9 + [NOTICE + "\nSecond notice line"]
    path = _write(isolated_workdir, [buy, footer, bad_buy])

    with pytest.raises(ValueError) as error:
        parse_robinhood(path)
    assert "robinhood.csv: row 6, column Amount: invalid number" in str(error.value)
    assert "invalid-number" not in str(error.value)
