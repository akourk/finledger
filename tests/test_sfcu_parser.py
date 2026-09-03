"""State Farm FCU share-savings parser.

The export is named generically (``ExportedTransactions.csv``), carries
no account column, and encodes direction in a ``Transaction Type`` label
that this parser has only ever observed set to ``Credit``.  Those three
facts are what the tests below pin: detection has to work off the
HEADER, both directional conventions have to land on the right action,
and the ``Balance`` column has to be checked rather than trusted.

Synthetic round numbers only — never the user's real figures.
"""

from __future__ import annotations

import pytest

from .conftest import write_sfcu_csv


def _rows(*rows: dict) -> list[dict]:
    return list(rows)


def _row(date: str, amount: str, *, ttype: str = "Credit",
         kind: str = "ACH", balance: str = "", status: str = "Posted",
         description: str = "") -> dict:
    return {
        "Transaction ID": f"{date.replace('/', '')} 000000",
        "Posting Date": date,
        "Transaction Type": ttype,
        "Posting Status": status,
        "Amount": amount,
        "Type": kind,
        "Balance": balance,
        "Description": description or f"{kind} activity",
    }


def _parse(workdir, rows, name="ExportedTransactions.csv"):
    from src.parsers import parse_sfcu

    path = workdir / "data" / name
    write_sfcu_csv(path, rows)
    return parse_sfcu(path)


def _nonneg(txn: dict) -> None:
    for field in ("quantity", "price", "fees", "amount"):
        val = txn.get(field)
        if isinstance(val, (int, float)):
            assert val >= 0, (
                f"{field}={val} is negative after parsing — direction must "
                f"be encoded in the action (action={txn['action']!r})"
            )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestDetection:
    def test_generic_filename_detected_by_header(self, isolated_workdir):
        """The credit union names every export ``ExportedTransactions.csv``
        — no filename pattern can identify it, so the header must."""
        from src.scanner import detect_broker

        path = isolated_workdir / "data" / "ExportedTransactions.csv"
        write_sfcu_csv(path, _rows(_row("8/27/2026", "100.00")))
        assert detect_broker(path) == "sfcu"

    def test_renamed_file_detected_by_filename(self, isolated_workdir):
        from src.scanner import detect_broker

        for name in ("state-farm-fcu.csv", "state-farm-fcu-2.csv",
                     "sfcu.csv"):
            path = isolated_workdir / "data" / name
            write_sfcu_csv(path, _rows(_row("8/27/2026", "100.00")))
            assert detect_broker(path) == "sfcu", name

    def test_header_rule_does_not_steal_other_brokers(self, isolated_workdir):
        """``posting date`` + ``posting status`` has to be specific enough
        that no existing broker's export is re-routed to this parser."""
        from src.scanner import detect_broker
        from .conftest import write_robinhood_csv, write_voya_csv

        rh = isolated_workdir / "data" / "robinhood-1.csv"
        write_robinhood_csv(rh, [{"Activity Date": "1/15/2022",
                                  "Trans Code": "ACH", "Amount": "$100.00"}])
        assert detect_broker(rh) == "robinhood"

        voya = isolated_workdir / "data" / "voya-401k-1.csv"
        write_voya_csv(voya, [{"Activity Date": "2020-12-01",
                               "Activity": "CONTRIBUTION"}])
        assert detect_broker(voya) == "voya_401k"

    def test_renames_to_canonical_prefix(self, isolated_workdir):
        from src.scanner import rename_data_files

        path = isolated_workdir / "data" / "ExportedTransactions.csv"
        write_sfcu_csv(path, _rows(_row("8/27/2026", "100.00")))
        renames = rename_data_files(isolated_workdir / "data")
        assert renames == {"ExportedTransactions.csv": "state-farm-fcu.csv"}
        assert (isolated_workdir / "data" / "state-farm-fcu.csv").exists()


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------

class TestDirection:
    """Direction is the one thing that must be right — a sign error here
    invents portfolio value.  Both conventions the export could plausibly
    use have to land on the same answer."""

    def test_credit_is_a_deposit(self, isolated_workdir):
        txns = _parse(isolated_workdir,
                      _rows(_row("8/27/2026", "100.00", ttype="Credit")))
        assert [t["action"] for t in txns] == ["Deposit"]
        assert txns[0]["quantity"] == 100.0
        assert txns[0]["price"] == 1.0
        assert txns[0]["symbol"] == "USD"
        _nonneg(txns[0])

    def test_debit_label_with_positive_magnitude_is_a_withdrawal(
            self, isolated_workdir):
        txns = _parse(isolated_workdir,
                      _rows(_row("8/27/2026", "40.00", ttype="Debit")))
        assert [t["action"] for t in txns] == ["Withdrawal"]
        assert txns[0]["amount"] == 40.0
        _nonneg(txns[0])

    def test_negative_amount_is_a_withdrawal_whatever_the_label(
            self, isolated_workdir):
        """A signed amount is unambiguous; the label is not.  If the two
        ever disagree the sign has to win, because reading a debit as a
        credit doubles the error."""
        txns = _parse(isolated_workdir,
                      _rows(_row("8/27/2026", "-40.00", ttype="Credit")))
        assert [t["action"] for t in txns] == ["Withdrawal"]
        assert txns[0]["amount"] == 40.0
        _nonneg(txns[0])

    def test_walked_balance_matches_the_export(self, isolated_workdir):
        """End-to-end on the direction logic: deposits minus withdrawals
        must reproduce the credit union's own closing balance."""
        from src.actions import SUBTRACT_ACTIONS
        from src.normalize import normalize_action

        txns = _parse(isolated_workdir, _rows(
            _row("8/31/2026", "5.00", kind="Dividends", balance="765.00"),
            _row("8/29/2026", "240.00", ttype="Debit", kind="ACH",
                 balance="760.00"),
            _row("8/27/2026", "1000.00", kind="ACH", balance="1000.00"),
        ))
        balance = 0.0
        for t in txns:
            t["account_group"] = "State Farm FCU Savings"
            canon = normalize_action(t)
            qty = t["quantity"]
            balance += -qty if canon in SUBTRACT_ACTIONS else qty
        assert balance == pytest.approx(765.00)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_share_dividends_normalize_to_interest(self, isolated_workdir):
        """A credit union pays "dividends" on a share account, but they
        are economically interest and land on a 1099-INT — so they must
        reach the interest income bucket, not dividends."""
        from src.actions import INCOME_ACTION_KINDS
        from src.normalize import normalize_action

        txns = _parse(isolated_workdir, _rows(
            _row("8/31/2026", "5.00", kind="Dividends",
                 description="Dividend Deposit")))
        t = txns[0]
        assert t["action"] == "Dividend"
        t["account_group"] = "State Farm FCU Savings"
        assert normalize_action(t) == "Interest"
        assert INCOME_ACTION_KINDS["Interest"] == "interest"

    def test_fee_debits_are_fees_and_carry_the_fees_field(
            self, isolated_workdir):
        txns = _parse(isolated_workdir, _rows(
            _row("8/29/2026", "25.00", ttype="Debit", kind="NSF Fee")))
        t = txns[0]
        assert t["action"] == "Fee"
        # `fees` is informational (never applied to the balance), so
        # stamping it surfaces the drag on the Performance tab without
        # double-counting the Fee action's own balance subtraction.
        assert t["fees"] == 25.0
        assert t["quantity"] == 25.0

    def test_fee_wording_on_a_credit_is_not_a_fee(self, isolated_workdir):
        """A fee REFUND is money arriving, not a fee charged."""
        txns = _parse(isolated_workdir, _rows(
            _row("8/29/2026", "25.00", ttype="Credit", kind="Fee Refund")))
        assert txns[0]["action"] == "Deposit"

    def test_unknown_type_falls_back_to_plain_direction(
            self, isolated_workdir):
        """The broker's ``Type`` column is open-ended.  An unrecognized
        value must still produce a correct cash movement rather than an
        un-normalized action leaking into the dashboard."""
        from src.normalize import normalize_action

        txns = _parse(isolated_workdir, _rows(
            _row("8/29/2026", "60.00", ttype="Debit", kind="Wire Transfer"),
            _row("8/27/2026", "60.00", ttype="Credit", kind="Share Draft"),
        ))
        assert [t["action"] for t in txns] == ["Withdrawal", "Deposit"]
        for t in txns:
            t["account_group"] = "State Farm FCU Savings"
        assert [normalize_action(t) for t in txns] == ["Withdrawal", "Deposit"]

    def test_every_emitted_action_has_a_normalize_rule(self, isolated_workdir):
        """The rules are scoped to the account name the parser stamps.
        If either side is renamed without the other, every action silently
        falls through to the title-cased default — this pins the pair."""
        from src.normalize import RULES
        from src.parsers.sfcu import ACCOUNT

        scoped = {r["action"] for r in RULES
                  if r.get("account_group") == ACCOUNT}
        assert scoped == {"Dividend", "Deposit", "Withdrawal", "Fee"}

    def test_emitted_actions_map_to_catalog_actions(self, isolated_workdir):
        from src.actions import ACTIONS
        from src.normalize import RULES
        from src.parsers.sfcu import ACCOUNT

        for rule in RULES:
            if rule.get("account_group") != ACCOUNT:
                continue
            assert rule["normalized"] in ACTIONS, rule


# ---------------------------------------------------------------------------
# Row filtering
# ---------------------------------------------------------------------------

class TestRowFiltering:
    def test_pending_rows_are_skipped(self, isolated_workdir):
        """A pending row posts later under its own transaction id.  The
        hash dedupe cannot collapse the pair (the rows genuinely differ),
        so ingesting both would double-count."""
        txns = _parse(isolated_workdir, _rows(
            _row("8/29/2026", "50.00", status="Pending"),
            _row("8/27/2026", "100.00", status="Posted"),
        ))
        assert [t["amount"] for t in txns] == [100.0]

    def test_unfamiliar_status_still_parses(self, isolated_workdir):
        """Only known-provisional states are dropped, so a status string
        this parser has not seen never silently loses a transaction."""
        txns = _parse(isolated_workdir, _rows(
            _row("8/27/2026", "100.00", status="Settled")))
        assert len(txns) == 1

    def test_zero_amount_rows_are_skipped(self, isolated_workdir):
        txns = _parse(isolated_workdir, _rows(
            _row("8/27/2026", "0.00"),
            _row("8/26/2026", "100.00"),
        ))
        assert [t["amount"] for t in txns] == [100.0]

    def test_unparseable_date_is_counted_as_a_drop(self, isolated_workdir):
        """Dropped rows must reach the parse report rather than vanishing
        — a date-format change is how a whole account goes missing."""
        from src.parsers._helpers import take_date_failures

        take_date_failures()
        _parse(isolated_workdir, _rows(_row("2026-08-27", "100.00")))
        assert take_date_failures() == 1

    def test_effective_date_used_when_posting_date_is_blank(
            self, isolated_workdir):
        rows = _rows(_row("8/27/2026", "100.00"))
        rows[0]["Posting Date"] = ""
        rows[0]["Effective Date"] = "8/26/2026"
        txns = _parse(isolated_workdir, rows)
        assert txns[0]["date"] == "2026-08-26"


# ---------------------------------------------------------------------------
# Balance continuity
# ---------------------------------------------------------------------------

class TestBalanceContinuity:
    """The export ships its own running balance.  Checking the parsed
    amounts against it is the only independent signal that would catch
    the credit union changing its Credit/Debit convention."""

    def test_consistent_balances_are_silent(self, isolated_workdir, capsys):
        _parse(isolated_workdir, _rows(
            _row("8/31/2026", "5.00", kind="Dividends", balance="765.00"),
            _row("8/29/2026", "240.00", ttype="Debit", balance="760.00"),
            _row("8/27/2026", "1000.00", balance="1000.00"),
        ))
        assert "WARNING" not in capsys.readouterr().out

    def test_reversed_direction_is_caught(self, isolated_workdir, capsys):
        """Same balances, but the middle row is labelled a Credit — the
        walk then overshoots and the mismatch has to be reported."""
        _parse(isolated_workdir, _rows(
            _row("8/31/2026", "5.00", kind="Dividends", balance="765.00"),
            _row("8/29/2026", "240.00", ttype="Credit", balance="760.00"),
            _row("8/27/2026", "1000.00", balance="1000.00"),
        ))
        out = capsys.readouterr().out
        assert "WARNING" in out and "Balance" in out

    def test_missing_balance_column_is_not_an_error(
            self, isolated_workdir, capsys):
        """An export without balances is simply unchecked, not broken."""
        _parse(isolated_workdir, _rows(
            _row("8/29/2026", "240.00", ttype="Debit"),
            _row("8/27/2026", "1000.00"),
        ))
        assert "WARNING" not in capsys.readouterr().out

    def test_rounding_does_not_trip_the_check(self, isolated_workdir, capsys):
        _parse(isolated_workdir, _rows(
            _row("8/31/2026", "0.004", kind="Dividends", balance="100.00"),
            _row("8/27/2026", "100.00", balance="100.00"),
        ))
        assert "WARNING" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_parse_all_files_dispatches_to_the_parser(self, isolated_workdir):
        from src.parsers import parse_all_files

        write_sfcu_csv(isolated_workdir / "data" / "ExportedTransactions.csv",
                       _rows(_row("8/27/2026", "100.00", balance="100.00")))
        txns = parse_all_files(isolated_workdir / "data")
        assert [t["account"] for t in txns] == ["State Farm FCU Savings"]

    def test_usd_balance_is_tracked_because_the_account_is_savings(
            self, isolated_workdir):
        """USD balances are tracked ONLY for Savings-type accounts.  Without
        the ``Account Type`` metadata row this account would default to
        Taxable and its balance would be dropped entirely."""
        from src.config import ACCOUNT_TYPES
        from src.normalize import normalize_action
        from src.pipeline_stages import walk_balances
        from src.parsers.sfcu import ACCOUNT

        assert ACCOUNT_TYPES.get(ACCOUNT) == "Savings"

        txns = _parse(isolated_workdir, _rows(
            _row("8/31/2026", "5.00", kind="Dividends", balance="105.00"),
            _row("8/27/2026", "100.00", balance="100.00"),
        ))
        for t in txns:
            t["account_group"] = ACCOUNT
            t["account_type"] = "Savings"
            t["action"] = normalize_action(t)
        _, balances = walk_balances(txns)
        assert balances[(ACCOUNT, "USD")] == pytest.approx(105.00)

    def test_metadata_ships_the_rows_the_account_needs(self):
        """The parser only works end-to-end with an ``Account Type`` row
        mapping this group to Savings; the shipped metadata must carry it
        (plus the declared rate for the income forecast)."""
        from pathlib import Path

        from src.metadata import parse_metadata
        from src.parsers.sfcu import ACCOUNT

        data_dir = Path(__file__).resolve().parent.parent / "data"
        if not (data_dir / "metadata.csv").exists():   # public checkout has no data/
            pytest.skip("no local metadata.csv")
        meta = parse_metadata(data_dir)
        assert meta["account_types"].get(ACCOUNT) == "Savings"
        assert any(r["account_group"] == ACCOUNT
                   for r in meta["savings_apr"])
