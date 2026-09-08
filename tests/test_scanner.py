from pathlib import Path

import pytest


def test_coinbase_reference_reports_are_skipped(tmp_path):
    """The tax-center raw-transactions and gain/loss downloads are
    reference data — parsing them would double-count every trade, and
    the renamer must leave them alone."""
    from src.scanner import detect_broker

    raw = tmp_path / "Coinbase-0-CB-RAWTX.csv"
    raw.write_text(
        "Transaction ID,Transaction Type,Date & time,Asset Acquired,"
        "Quantity Acquired (Bought, Received, etc),"
        "Cost Basis (incl. fees and/or spread) (USD),Data Source,"
        "Asset Disposed (Sold, Sent, etc),Quantity Disposed,"
        "Proceeds (excl. fees and/or spread) (USD)\n",
        encoding="utf-8")
    gl = tmp_path / "Coinbase-0-CB-GAINLOSSCSV.csv"
    gl.write_text("Gain/loss report\n", encoding="utf-8")
    assert detect_broker(raw) == "skip"
    assert detect_broker(gl) == "skip"

    # Header fallback catches them even if renamed to something bland.
    renamed = tmp_path / "report.csv"
    renamed.write_text(
        "Transaction Type,Transaction ID,Tax lot ID,Asset name,Amount,"
        "Date Acquired,Cost basis (USD),Date of Disposition,"
        "Proceeds (USD),Gains (Losses) (USD),Holding period (Days),"
        "Data source\n",
        encoding="utf-8")
    assert detect_broker(renamed) == "skip"


def test_robinhood_1099_is_skipped(tmp_path):
    """Robinhood's consolidated 1099 CSV is a multi-section reference
    report (1099-DIV / 1099-INT / 1099-B / 1099-MISC, each with its own
    header keyed on column 0).  It must be skipped — parsing it as
    transactions would double-count — and detected by HEADER so a fresh
    UUID-named yearly download is caught before renaming."""
    from src.scanner import detect_broker

    # Renamed convention → filename match.
    named = tmp_path / "robinhood-1099-2024.csv"
    named.write_text("1099-DIV,ACCOUNT NUMBER,TAX YEAR,ORDINARY DIV\n",
                     encoding="utf-8")
    assert detect_broker(named) == "skip"

    # Fresh download with Robinhood's UUID filename → header fallback.
    uuid = tmp_path / "6a123a1a-063c-45a0-b985-708786a2d61a.csv"
    uuid.write_text(
        "1099-DIV,ACCOUNT NUMBER,TAX YEAR,ORDINARY DIV,QUALIFIED DIV\n"
        "1099-DIV,X,2024,1.00,1.00\n"
        "1099-B,ACCOUNT NUMBER,TAX YEAR,DATE ACQUIRED,SALE DATE,"
        "DESCRIPTION,SHARES,COST BASIS,SALES PRICE,TERM\n",
        encoding="utf-8")
    assert detect_broker(uuid) == "skip"

    # A real robinhood transaction CSV must still parse (not mis-skipped).
    txn = tmp_path / "robinhood-9.csv"
    txn.write_text(
        "Activity Date,Process Date,Settle Date,Instrument,Description,"
        "Trans Code,Quantity,Price,Amount\n",
        encoding="utf-8")
    assert detect_broker(txn) == "robinhood"


def test_robinhood_1099_uuid_upload_auto_renames(tmp_path):
    """A fresh UUID-named consolidated-1099 download self-names to
    robinhood-1099-{TAX YEAR}.csv (year read from the file); an
    existing canonical file for the same year is never clobbered."""
    from src.scanner import rename_data_files

    body_2024 = (
        "1099-DIV,ACCOUNT NUMBER,TAX YEAR,ORDINARY DIV,QUALIFIED DIV\n"
        "1099-DIV,X,2024,1.00,1.00\n"
        "1099-B,ACCOUNT NUMBER,TAX YEAR,DATE ACQUIRED,SALE DATE,"
        "DESCRIPTION,SHARES,COST BASIS,SALES PRICE,TERM\n"
    )
    uuid = tmp_path / "6a123a1a-063c-45a0-b985-708786a2d61a.csv"
    uuid.write_text(body_2024, encoding="utf-8")

    renames = rename_data_files(tmp_path)
    assert renames.get(uuid.name) == "robinhood-1099-2024.csv"
    assert (tmp_path / "robinhood-1099-2024.csv").exists()
    assert not uuid.exists()

    # Already-canonical file: no rename on a second pass.
    assert rename_data_files(tmp_path) == {}

    # A second upload for the SAME year must not clobber the existing
    # file — it stays under its original name.
    dup = tmp_path / "0f0f0f0f-1111-2222-3333-444444444444.csv"
    dup.write_text(body_2024, encoding="utf-8")
    renames = rename_data_files(tmp_path)
    assert dup.name not in renames
    assert dup.exists()

    # Dry run reports the rename without touching the file.
    uuid2 = tmp_path / "9b999b9b-063c-45a0-b985-708786a2d61a.csv"
    uuid2.write_text(body_2024.replace("2024", "2023"), encoding="utf-8")
    plan = rename_data_files(tmp_path, dry_run=True)
    assert plan.get(uuid2.name) == "robinhood-1099-2023.csv"
    assert uuid2.exists()
    rename_data_files(tmp_path)
    assert (tmp_path / "robinhood-1099-2023.csv").exists()


def _rename_inputs(directory):
    # The second final name overlaps the first original name: recovery must
    # evacuate completed targets before restoring the originals.
    contents = {"robinhood-2.csv": b"fictional first export",
                "robinhood-3.csv": b"fictional second export"}
    for name, body in contents.items():
        (directory / name).write_bytes(body)
    return contents


@pytest.mark.parametrize("failed_move", [1, 2, 3, 4])
def test_rename_failure_restores_all_original_names_and_bytes(tmp_path, monkeypatch, failed_move):
    from src.scanner import rename_data_files

    originals = _rename_inputs(tmp_path)
    real_rename = Path.rename
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == failed_move:
            raise OSError("fictional filesystem failure")
        return real_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_once)
    with pytest.raises(OSError, match="fictional filesystem failure"):
        rename_data_files(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == originals


def test_rename_preserves_old_temporary_files_and_handles_name_overlap(tmp_path):
    from src.scanner import rename_data_files

    originals = _rename_inputs(tmp_path)
    previous_temporary = tmp_path / ".tmp-robinhood-2.csv"
    previous_temporary.write_bytes(b"fictional recovery copy")
    assert rename_data_files(tmp_path) == {
        "robinhood-2.csv": "robinhood-1.csv", "robinhood-3.csv": "robinhood-2.csv"}
    assert (tmp_path / "robinhood-1.csv").read_bytes() == originals["robinhood-2.csv"]
    assert (tmp_path / "robinhood-2.csv").read_bytes() == originals["robinhood-3.csv"]
    assert previous_temporary.read_bytes() == b"fictional recovery copy"
    assert not list(tmp_path.glob(".fin-rename-*"))


def test_rename_dry_run_does_not_create_temporary_directories(tmp_path):
    from src.scanner import rename_data_files

    originals = _rename_inputs(tmp_path)
    assert len(rename_data_files(tmp_path, dry_run=True)) == 2
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == originals


@pytest.mark.parametrize("failed_moves", [{4, 5}, {4, 5, 6}])
def test_rename_recovery_failure_keeps_every_source_byte(tmp_path, monkeypatch, failed_moves):
    from src.scanner import rename_data_files

    originals = _rename_inputs(tmp_path)
    real_rename = Path.rename
    calls = 0

    def fail_twice(source, destination):
        nonlocal calls
        calls += 1
        if calls in failed_moves:
            raise OSError("fictional filesystem failure")
        return real_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_twice)
    with pytest.raises(OSError, match="recovery was incomplete"):
        rename_data_files(tmp_path)
    assert sorted(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()) == sorted(originals.values())
    assert list(tmp_path.glob(".fin-rename-*")), "Keep stranded originals for manual recovery"
    with pytest.raises(ValueError, match="incomplete CSV rename"):
        rename_data_files(tmp_path)


def test_rename_rejects_directory_inputs_before_moving_any_csv(tmp_path):
    from src.scanner import rename_data_files

    originals = _rename_inputs(tmp_path)
    occupied = tmp_path / "robinhood-4.csv"
    occupied.mkdir()
    with pytest.raises(ValueError, match="regular files"):
        rename_data_files(tmp_path)
    assert occupied.is_dir()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == originals


@pytest.mark.parametrize("entrypoint", ["scan", "rename", "parse"])
def test_incomplete_recovery_blocks_the_next_import_even_without_renaming(tmp_path, entrypoint):
    from src.parsers import parse_all_files
    from src.scanner import rename_data_files, scan_data_files

    recovery = tmp_path / ".fin-rename-fictional"
    recovery.mkdir()
    (recovery / "robinhood.csv").write_bytes(b"fictional interrupted export")
    with pytest.raises(ValueError, match="incomplete CSV rename"):
        {"scan": scan_data_files, "rename": rename_data_files,
         "parse": parse_all_files}[entrypoint](tmp_path)
    assert (recovery / "robinhood.csv").read_bytes() == b"fictional interrupted export"
