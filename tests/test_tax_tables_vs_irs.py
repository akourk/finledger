"""fin's tax tables against the IRS source documents.

`test_tax_tables.py` checks the tables are internally consistent and
that Python and JS agree. Both can be true of figures that are simply
wrong — the tables are hand-transcribed, and nothing had ever compared
them to what the IRS actually published.

This module does. Every figure below is transcribed from the primary
source, cited inline, so a future year's update has something to diff
against rather than a second copy of whatever is already in the code.

**It found a real error.** fin's 2026 Head-of-Household 24% ceiling was
$201,775 — Single's figure. The IRS sets it at $201,750. In 2024 and
2025 those two brackets shared a boundary; for 2026 the IRS split them
by $25, and the table carried the old pattern forward. Small in dollars
(a HoH filer inside that $25 band was taxed at 24% instead of 32%, worth
about $2) and exactly the kind of drift a "check it against the source"
test exists to catch — the tables look right, are self-consistent, and
agree with the JS fallback.

Sources:
  Rev. Proc. 2025-32 (2026 inflation adjustments) — brackets §4.01,
      capital gains §4.03, standard deduction 2025 §3.01
  Notice 2025-67 (2026 retirement amounts) — 401(k) elective deferral,
      Roth IRA MAGI phase-out
"""

from __future__ import annotations

import pytest

from src.analytics.tax import tax_tables_to_json


@pytest.fixture(scope="module")
def tables():
    return tax_tables_to_json()


# --- Rev. Proc. 2025-32 §4.01 ---------------------------------------------
# Ordinary-income bracket ceilings, in rate order (10, 12, 22, 24, 32, 35).
# The top rate has no ceiling and is omitted.
IRS_BRACKETS_2026 = {
    "Single":                    [12400,  50400, 105700, 201775, 256225, 640600],
    "Married Filing Jointly":    [24800, 100800, 211400, 403550, 512450, 768700],
    "Married Filing Separately": [12400,  50400, 105700, 201775, 256225, 384350],
    "Head of Household":         [17700,  67450, 105700, 201750, 256200, 640600],
}

# The cumulative tax the Rev. Proc. states at each bracket boundary.  Used
# as an independent check on the ceilings above: these figures are
# printed in the source table, so reproducing them from the thresholds
# confirms the thresholds were transcribed correctly.
IRS_CUMULATIVE_TAX_2026 = {
    "Single":            {201775: 41024, 256225: 58448},
    "Head of Household": {201750: 39207, 256200: 56631},
}

# --- Rev. Proc. 2025-32 §4.03 --------------------------------------------
IRS_LTCG_2026 = {
    "Married Filing Jointly":    (98900, 613700),
    "Married Filing Separately": (49450, 306850),
    "Head of Household":         (66200, 579600),
    "Single":                    (49450, 545500),
}

# --- Rev. Proc. 2025-32 §4.14 / §3.01 ------------------------------------
IRS_STD_DEDUCTION = {
    2026: {"Single": 16100, "Married Filing Jointly": 32200,
           "Married Filing Separately": 16100, "Head of Household": 24150},
    # 2025 as amended by the OBBBA, which superseded Rev. Proc. 2024-40.
    2025: {"Single": 15750, "Married Filing Jointly": 31500,
           "Married Filing Separately": 15750, "Head of Household": 23625},
}

# --- Notice 2025-67 -------------------------------------------------------
IRS_401K_ELECTIVE_DEFERRAL = {2026: 24500, 2025: 23500, 2024: 23000}
IRS_ROTH_MAGI = {          # (phase-out start, phase-out end)
    2026: {"Single": (153000, 168000), "Married Filing Jointly": (242000, 252000)},
    2025: {"Single": (150000, 165000), "Married Filing Jointly": (236000, 246000)},
}


class TestOrdinaryBrackets:

    @pytest.mark.parametrize("status", sorted(IRS_BRACKETS_2026))
    def test_2026_ceilings_match_the_rev_proc(self, tables, status):
        got = [r[0] for r in tables["federal_brackets"]["2026"][status]
               if r[0] is not None]
        assert got == IRS_BRACKETS_2026[status], (
            f"2026 {status} brackets disagree with Rev. Proc. 2025-32 "
            f"§4.01:\n  fin {got}\n  irs {IRS_BRACKETS_2026[status]}"
        )

    @pytest.mark.parametrize("status", sorted(IRS_CUMULATIVE_TAX_2026))
    def test_cumulative_tax_reproduces_the_published_figures(self, tables,
                                                             status):
        """Independent check on the thresholds.

        The Rev. Proc. prints the running tax at each boundary ("$39,207
        plus 32% of the excess over $201,750"). Recomputing it from the
        bracket edges either reproduces those figures or proves an edge
        is wrong — which is how the Head-of-Household error was
        confirmed rather than guessed at.
        """
        rows = tables["federal_brackets"]["2026"][status]
        tax, prev = 0.0, 0
        for ceiling, rate in rows:
            if ceiling is None:
                break
            tax += (ceiling - prev) * rate
            prev = ceiling
            expected = IRS_CUMULATIVE_TAX_2026[status].get(ceiling)
            if expected is not None:
                assert round(tax) == expected, (
                    f"tax at the {status} {ceiling:,} boundary computes to "
                    f"{tax:,.0f}, but Rev. Proc. 2025-32 states {expected:,} "
                    "— a bracket edge below this point is wrong"
                )

    def test_the_head_of_household_regression_specifically(self, tables):
        """The bug this module found, pinned by name.

        HoH and Single shared this boundary in 2024 and 2025. The IRS
        split them for 2026, and copying Single's figure across is the
        natural mistake — so it gets its own assertion rather than
        living only inside a list comparison.
        """
        hoh = [r[0] for r in tables["federal_brackets"]["2026"]["Head of Household"]]
        single = [r[0] for r in tables["federal_brackets"]["2026"]["Single"]]
        assert hoh[3] == 201750
        assert single[3] == 201775
        assert hoh[3] != single[3], (
            "Head of Household is using Single's 24% ceiling again"
        )


class TestCapitalGains:

    @pytest.mark.parametrize("status", sorted(IRS_LTCG_2026))
    def test_2026_breakpoints_match_the_rev_proc(self, tables, status):
        rows = tables["ltcg_brackets"]["2026"][status]
        zero_max, fifteen_max = rows[0][0], rows[1][0]
        assert (zero_max, fifteen_max) == IRS_LTCG_2026[status], (
            f"2026 {status} capital-gains breakpoints disagree with "
            f"Rev. Proc. 2025-32 §4.03"
        )

    def test_the_top_bracket_is_unbounded(self, tables):
        for status in IRS_LTCG_2026:
            rows = tables["ltcg_brackets"]["2026"][status]
            assert rows[-1][0] is None and rows[-1][1] == 0.20


class TestStandardDeduction:

    @pytest.mark.parametrize("year", sorted(IRS_STD_DEDUCTION))
    def test_matches_the_source(self, tables, year):
        got = tables["std_deduction"][str(year)]
        assert got == IRS_STD_DEDUCTION[year], (
            f"{year} standard deduction disagrees with the source"
        )

    def test_2025_reflects_the_obbba_amendment(self, tables):
        """2025's figures were RAISED retroactively by the One Big
        Beautiful Bill, superseding Rev. Proc. 2024-40. A table built
        from the original figures would read 15,000 / 30,000."""
        got = tables["std_deduction"]["2025"]
        assert got["Single"] == 15750 and got["Married Filing Jointly"] == 31500


class TestRetirementAmounts:

    @pytest.mark.parametrize("year", sorted(IRS_401K_ELECTIVE_DEFERRAL))
    def test_401k_elective_deferral_limit(self, tables, year):
        assert tables["k401_limit"][str(year)] == \
            IRS_401K_ELECTIVE_DEFERRAL[year]

    @staticmethod
    def _start(po_status: dict, year: int):
        """Year keys in this table are ints, while every other table in
        the export uses strings.  Harmless through `json.dumps`, which
        stringifies keys either way, but it means a PYTHON consumer of
        `tax_tables_to_json()` has to know which table it is holding.
        Accept both rather than pin the current shape — see
        `test_year_keys_survive_a_json_round_trip` for the contract that
        actually matters.
        """
        s = po_status["start"]
        return s.get(year, s.get(str(year)))

    @pytest.mark.parametrize("year", sorted(IRS_ROTH_MAGI))
    def test_roth_magi_phaseout(self, tables, year):
        po = tables["roth_magi_phaseout"]
        for status, (start, end) in IRS_ROTH_MAGI[year].items():
            got_start = self._start(po[status], year)
            assert got_start is not None, f"no {year} entry for {status}"
            got_end = got_start + po[status]["width"]
            assert (got_start, got_end) == (start, end), (
                f"{year} {status} Roth MAGI phase-out is "
                f"{got_start:,}-{got_end:,}, source says {start:,}-{end:,}"
            )

    def test_year_keys_survive_a_json_round_trip(self, tables):
        """The contract the dashboard depends on.

        `tax_tables_to_json` feeds the export, and the JS reads
        `DATA.tax_tables` with string year keys. Whatever the in-memory
        key type is, it has to come back addressable by string.
        """
        import json

        rt = json.loads(json.dumps(tables))
        po = rt["roth_magi_phaseout"]["Single"]["start"]
        assert "2026" in po, (
            "the Roth phase-out years are not addressable by string after "
            "serialization — the dashboard looks them up that way"
        )
        assert po["2026"] == 153000

    def test_married_filing_separately_is_not_inflation_adjusted(self, tables):
        """Notice 2025-67: the MFS range is fixed at $0-$10,000 and takes
        no annual COLA. A table that quietly indexed it would drift."""
        po = tables["roth_magi_phaseout"]["Married Filing Separately"]
        assert po["width"] == 10000
        assert set(po["start"].values()) == {0}
