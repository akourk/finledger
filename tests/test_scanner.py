

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
