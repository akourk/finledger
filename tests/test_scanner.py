

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
