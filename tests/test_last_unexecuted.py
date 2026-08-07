"""The last five functions no test had ever entered.

Closing F-003, F-004 and F-005. Coverage's second table — functions with
zero executed lines — is the actionable one, because an unexecuted line
is unprotected *by construction*: no test enters it, so no test can
notice it breaking. These five were what remained after the network
layer was covered.

They are small and none of them was wrong. That is the expected result
and not the point: the point is that until now, any of them could have
been deleted or inverted with the suite green.

Grouped by what they actually do rather than by module, because the
interesting content is the rule each one encodes.

Synthetic values only.
"""

from __future__ import annotations

import json

import pytest


class TestSnapshotExport:
    """F-003. `export_snapshot` is half of the feature that lets a user
    move their data between machines, and the round trip had only ever
    been exercised in one direction — every test imports the shipped
    sample, none produced one."""

    def _data(self, workdir, files: dict[str, str]):
        d = workdir / "data"
        d.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")
        return d

    def test_it_bundles_every_csv(self, isolated_workdir):
        from src.snapshot import export_snapshot

        d = self._data(isolated_workdir, {"a.csv": "h\n1\n", "b.csv": "h\n2\n"})
        out = isolated_workdir / "snap.json"
        bundle = export_snapshot(d, out)

        assert bundle["file_count"] == 2
        assert set(bundle["files"]) == {"a.csv", "b.csv"}
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8"))["files"]["a.csv"] \
            == "h\n1\n"

    def test_non_csv_files_are_left_out(self, isolated_workdir):
        """`data/` is gitignored at any extension, so a stray .xlsx or
        .bak can sit beside the CSVs. The bundle is a pipeline input,
        not a backup."""
        from src.snapshot import export_snapshot

        d = self._data(isolated_workdir, {"a.csv": "h\n", "notes.txt": "x",
                                          "sheet.xlsx": "binary"})
        bundle = export_snapshot(d, isolated_workdir / "snap.json")
        assert set(bundle["files"]) == {"a.csv"}

    def test_subdirectories_are_skipped(self, isolated_workdir):
        from src.snapshot import export_snapshot

        d = self._data(isolated_workdir, {"a.csv": "h\n"})
        (d / "archive").mkdir()
        (d / "archive" / "old.csv").write_text("h\n", encoding="utf-8")
        bundle = export_snapshot(d, isolated_workdir / "snap.json")
        assert set(bundle["files"]) == {"a.csv"}

    def test_an_empty_csv_is_still_bundled(self, isolated_workdir):
        """A header-only file is valid pipeline input. Dropping it would
        silently change what the snapshot reproduces."""
        from src.snapshot import export_snapshot

        d = self._data(isolated_workdir, {"a.csv": ""})
        bundle = export_snapshot(d, isolated_workdir / "snap.json")
        assert "a.csv" in bundle["files"]

    def test_a_missing_data_dir_raises(self, isolated_workdir):
        from src.snapshot import export_snapshot

        with pytest.raises(FileNotFoundError):
            export_snapshot(isolated_workdir / "nope",
                            isolated_workdir / "snap.json")

    def test_a_bom_is_stripped_on_read(self, isolated_workdir):
        """Broker exports routinely carry a UTF-8 BOM. It must not
        survive into the bundle, or the re-imported header no longer
        matches what the scanner looks for."""
        from src.snapshot import export_snapshot

        d = isolated_workdir / "data"
        d.mkdir(parents=True, exist_ok=True)
        (d / "a.csv").write_text("Date,Amount\n", encoding="utf-8-sig")
        bundle = export_snapshot(d, isolated_workdir / "snap.json")
        assert bundle["files"]["a.csv"].startswith("Date"), (
            "the BOM survived into the bundle"
        )

    def test_the_round_trip_reproduces_the_directory(self, isolated_workdir):
        """The property the feature exists for, and the one neither
        direction alone can prove."""
        from src.snapshot import export_snapshot, import_snapshot

        src = {"robinhood-1.csv": "Activity Date\n1/2/2024\n",
               "metadata.csv": "Type,Date,Amount,Symbol,Note\n"}
        d = self._data(isolated_workdir, src)
        out = isolated_workdir / "snap.json"
        export_snapshot(d, out)

        dest = isolated_workdir / "restored"
        import_snapshot(out, dest, overwrite=True)
        got = {p.name: p.read_text(encoding="utf-8") for p in dest.glob("*.csv")}
        assert got == src


class TestAmountSign:
    """F-004, the sign-split family. Ambiguous broker actions must be
    split by the sign of the amount BEFORE the parser abs()es it — get
    this wrong and a deposit becomes a withdrawal."""

    @pytest.mark.parametrize("amount,expected", [
        (100.0, "+"), (-100.0, "-"), (0.0, "0"),
        ("100", "+"), ("-100", "-"),
        (None, "0"), ("", "0"), ("n/a", "0"), ([], "0"),
    ])
    def test_sign_classification(self, amount, expected):
        from src.normalize import _amount_sign

        assert _amount_sign({"amount": amount}) == expected

    def test_a_missing_amount_is_zero_not_an_error(self):
        from src.normalize import _amount_sign

        assert _amount_sign({}) == "0"

    def test_zero_is_its_own_class_not_positive(self):
        """A rule matching on '+' must not fire for a $0 row. Merging
        them is the mistake that turns a zero-amount corporate action
        into a deposit."""
        from src.normalize import _amount_sign

        assert _amount_sign({"amount": 0.0}) != "+"


class TestPickByYear:
    """F-005. Tax tables are keyed by year, and a year with no entry has
    to resolve to SOMETHING. Falling forward to a future year's table
    would apply figures that don't exist yet."""

    TABLE = {2024: "a", 2026: "c"}

    def test_an_exact_year_wins(self):
        from src.analytics.tax import _pick_by_year
        assert _pick_by_year(2024, self.TABLE) == "a"

    def test_a_gap_year_falls_BACK_not_forward(self):
        """2025 is absent. It must resolve to 2024's table, not 2026's —
        using next year's not-yet-effective figures would be wrong in a
        way nothing downstream could detect."""
        from src.analytics.tax import _pick_by_year
        assert _pick_by_year(2025, self.TABLE) == "a"

    def test_a_year_after_the_table_uses_the_newest(self):
        from src.analytics.tax import _pick_by_year
        assert _pick_by_year(2099, self.TABLE) == "c"

    def test_a_year_before_the_table_uses_the_oldest_available(self):
        """Nothing is ≤ the requested year, so the fallback is the last
        key — the only defined behaviour left."""
        from src.analytics.tax import _pick_by_year
        assert _pick_by_year(1990, self.TABLE) == "c"

    def test_an_empty_table_is_none(self):
        from src.analytics.tax import _pick_by_year
        assert _pick_by_year(2025, {}) is None


class TestNamesWithBasisEffect:
    """F-005. A catalog accessor — trivial, and the kind of thing that
    silently returns an empty set after a field rename."""

    def test_it_returns_the_actions_with_that_effect(self):
        from src.actions import names_with_basis_effect

        adds = names_with_basis_effect("add")
        assert "Buy" in adds
        assert "Sell" not in adds

    def test_an_unknown_effect_is_empty_not_an_error(self):
        from src.actions import names_with_basis_effect

        assert names_with_basis_effect("not_a_real_effect") == frozenset()

    def test_it_agrees_with_the_basis_effects_map(self):
        """Both derive from the same catalog, so a divergence means one
        of them stopped reading it."""
        from src.actions import BASIS_EFFECTS, names_with_basis_effect

        for effect in set(BASIS_EFFECTS.values()):
            expected = {n for n, e in BASIS_EFFECTS.items() if e == effect}
            assert names_with_basis_effect(effect) == expected


class TestUsaaPositionBeforeDate:
    """F-005. Walks a USAA position's balance to a date, with its own
    add/subtract classification — a fourth copy of a rule that lives in
    `actions.py`, which is why it is worth having pinned."""

    def _t(self, action, qty, date="2024-01-01", symbol="FUND",
           account="USAA Roth IRA"):
        return {"account": account, "symbol": symbol, "action": action,
                "quantity": qty, "date": date}

    def _bal(self, txns, symbol="FUND", asof="2024-12-31"):
        from src.main import _usaa_position_before_date
        return _usaa_position_before_date(txns, symbol, asof)

    def test_buys_add_and_sells_subtract(self):
        assert self._bal([self._t("Buy", 10.0), self._t("Sell", 4.0)]) == 6.0

    @pytest.mark.parametrize("action", ["Fee", "Sell", "Transfer Out",
                                        "Withdrawal", "Distribution"])
    def test_every_subtract_action(self, action):
        assert self._bal([self._t("Buy", 10.0), self._t(action, 1.0)]) == 9.0

    def test_neutral_actions_do_not_move_the_balance(self):
        assert self._bal([self._t("Buy", 10.0), self._t("Neutral", 5.0)]) \
            == 10.0

    def test_activity_after_the_asof_date_is_excluded(self):
        """The whole purpose of the function. A later sale must not
        reduce the as-of balance."""
        got = self._bal([self._t("Buy", 10.0, date="2024-01-01"),
                         self._t("Sell", 10.0, date="2024-06-01")],
                        asof="2024-03-01")
        assert got == 10.0

    def test_activity_ON_the_asof_date_is_included(self):
        """Near-miss on the boundary: the comparison is `>`, so same-day
        rows count."""
        got = self._bal([self._t("Buy", 10.0, date="2024-03-01")],
                        asof="2024-03-01")
        assert got == 10.0

    def test_other_accounts_are_excluded(self):
        got = self._bal([self._t("Buy", 10.0),
                         self._t("Buy", 99.0, account="Robinhood")])
        assert got == 10.0

    def test_other_symbols_are_excluded(self):
        got = self._bal([self._t("Buy", 10.0),
                         self._t("Buy", 99.0, symbol="OTHER")])
        assert got == 10.0
