"""Robinhood Apex-era hand-entered CSV (parsers/robinhood_apex.py).

Robinhood's transaction exports don't reach back into the 2017–2018
Apex clearing era; those trades are transcribed by hand from the old
1099 PDFs into a small CSV.  Pins: scanner detection (filename +
header, and NOT stealing the modern robinhood CSVs), the rename to
robinhood-apex.csv, parsing (dates / PURCHASE→Buy / option symbols
pass through / CUSIP capture), and that an option leg entered in fin's
symbol format pairs with its sell from the modern CSVs in the basis
walker.
"""

from __future__ import annotations

import pytest

_HEADER = ("Date,Security Description,CUSIP,Transaction Description,"
           "Quantity,Price,Amount\n")


def _write_apex(path):
    path.write_text(
        _HEADER
        + "10/30/2017,NETFLIX COM INC,64110L106,PURCHASE,1,196.10,196.10\n"
        + "11/06/2017,NETFLIX COM INC,64110L106,SELL,1,198.00,198.00\n"
        + "06/08/2018,AMD 11/9/2018 Put $15.50,,PURCHASE,1,1.71,171.00\n",
        encoding="utf-8")


def test_scanner_detects_apex_and_not_modern_robinhood(tmp_path):
    from src.scanner import detect_broker
    f = tmp_path / "robinhood apex transactions.csv"
    _write_apex(f)
    assert detect_broker(f) == "robinhood_apex"
    # Renamed canonical form still detects.
    g = tmp_path / "robinhood-apex.csv"
    _write_apex(g)
    assert detect_broker(g) == "robinhood_apex"
    # Header fallback for a bland filename.
    h = tmp_path / "old-trades.csv"
    _write_apex(h)
    assert detect_broker(h) == "robinhood_apex"
    # A modern robinhood CSV is untouched by the new rule.
    m = tmp_path / "robinhood-2.csv"
    m.write_text("Activity Date,Process Date,Settle Date,Instrument,"
                 "Description,Trans Code,Quantity,Price,Amount\n",
                 encoding="utf-8")
    assert detect_broker(m) == "robinhood"


def test_apex_file_renames_to_canonical(tmp_path):
    from src.scanner import rename_data_files
    f = tmp_path / "robinhood apex transactions.csv"
    _write_apex(f)
    renames = rename_data_files(tmp_path)
    assert renames.get(f.name) == "robinhood-apex.csv"
    assert (tmp_path / "robinhood-apex.csv").exists()


def test_parse_apex_rows(tmp_path):
    from src.parsers import parse_robinhood_apex
    f = tmp_path / "robinhood-apex.csv"
    _write_apex(f)
    txns = parse_robinhood_apex(f)
    assert len(txns) == 3
    buy, sell, opt = txns
    assert buy["date"] == "2017-10-30"
    assert buy["account"] == "Robinhood"
    assert buy["action"] == "Buy"
    assert buy["symbol"] == "NETFLIX COM INC"
    assert buy["cusip"] == "64110L106"
    assert sell["action"] == "Sell"
    # Option symbol passes through in fin's own format, and the action
    # becomes the option-specific opener so the Options tab pairs it.
    assert opt["symbol"] == "AMD 11/9/2018 Put $15.50"
    assert opt["action"] == "Option Buy"
    assert opt["amount"] == pytest.approx(171.0)


def test_apex_option_buy_pairs_with_modern_sell():
    """The Apex-era BTO leg supplies basis for the STC recorded in the
    modern Robinhood CSVs — the original motivating case (an option
    sell with no visible buy side)."""
    from src.basis import compute_basis_default
    sym = "AMD 11/9/2018 Put $15.50"
    txns = [
        {"date": "2018-06-08", "account": "Robinhood",
         "account_group": "Robinhood", "account_type": "Taxable",
         "symbol": sym, "action": "Option Buy", "quantity": 1.0,
         "price": 1.71, "fees": 0.0, "amount": 171.0,
         "description": "", "source": "robinhood-apex.csv"},
        {"date": "2018-08-20", "account": "Robinhood",
         "account_group": "Robinhood", "account_type": "Taxable",
         "symbol": sym, "action": "Option Sell", "quantity": 1.0,
         "price": 2.21, "fees": 0.0, "amount": 221.0,
         "description": "", "source": "robinhood-1.csv"},
    ]
    compute_basis_default(txns)
    assert txns[1]["cost_basis"] == pytest.approx(171.0)
    assert txns[1]["realized_gain"] == pytest.approx(50.0)


def test_apex_covered_conv_is_neutralized():
    """Robinhood's clearing-migration CONV row double-adds a position
    whose true buy is now in the Apex file — it must be re-tagged
    Neutral.  A CONV with no Apex-file coverage (referral free share)
    keeps originating its position."""
    from src.main import _reconcile_apex_conversions
    sym = "AMD 11/9/2018 Put $15.50"
    txns = [
        {"date": "2018-10-30", "symbol": sym, "action": "Option Buy",
         "quantity": 1.0, "source": "robinhood-apex.csv", "description": ""},
        {"date": "2018-11-09", "symbol": sym, "action": "CONV",
         "quantity": 1.0, "source": "robinhood-12.csv", "description": ""},
        {"date": "2018-11-12", "symbol": "S", "action": "CONV",
         "quantity": 1.0, "source": "robinhood-12.csv", "description": ""},
    ]
    out = _reconcile_apex_conversions(txns)
    assert out[1]["action"] == "Neutral"
    assert "Apex-era" in out[1]["description"]
    assert out[2]["action"] == "CONV"   # no apex coverage — untouched
