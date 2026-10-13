"""Report recency may resolve only exact fee-only Coinbase trade overlaps.

All values and identifiers are independently fictional.
"""

from collections import Counter
from copy import deepcopy
import csv
from itertools import permutations
import os

import pytest


HEADERS = ["ID", "Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
           "Price Currency", "Price at Transaction", "Subtotal",
           "Total (inclusive of fees and/or spread)", "Fees and/or Spread", "Notes"]


def trade(fee="2", **changes):
    row = dict(zip(HEADERS, ["fictional-sale", "2024-01-02 12:00:00 UTC", "Sell",
                           "TEST", "-2", "USD", "37.5", "75", "73", fee,
                           "Fictional sale"]))
    row.update(changes)
    return row


@pytest.fixture
def report(isolated_workdir):
    from src.parsers.coinbase import parse_coinbase

    def write(name, fees=("2",), *, latest="2024-02-01T00:00:00Z",
              generated=None, changes=None, reverse=False, preamble=None):
        rows = [trade(fee, **(changes or {})) for fee in fees]
        if latest is not None:
            rows.append(dict(zip(HEADERS, ["fictional-reward-" + latest, latest,
                "Staking Income", "TEST", "0.01", "USD", "37", "0.37", "0.37",
                "0", "Fictional reward"])))
        if reverse:
            rows.reverse()
        path = isolated_workdir / "data" / name
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\n")
            if generated is not None:
                csv.writer(stream, lineterminator="\n").writerow(["Report generated at", generated])
            for record in preamble or []:
                csv.writer(stream, lineterminator="\n").writerow(record)
            writer.writeheader()
            writer.writerows(rows)
        return parse_coinbase(path)
    return write


def sales(rows):
    return [row for row in rows if row["action"] == "Sell"]


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_transaction_anywhere_in_report_selects_only_fee(report, reverse):
    from src.export import deduplicate
    old = report("coinbase-1.csv", latest="2024-02-01T00:00:00Z", reverse=reverse)
    new = report("coinbase-9.csv", ("1.5",), latest="2024-03-01T00:00:00Z", reverse=reverse)
    before = deepcopy(old + new)
    for order in ((old + new), (new + old)):
        kept, removed = deduplicate(order)
        selected = sales(kept)
        assert removed == 1 and len(selected) == 1
        assert selected[0]["fees"] == 1.5
        assert selected[0]["amount"] == 73
        assert selected[0]["quantity"] == 2
        evidence = selected[0]["fee_resolution"]
        assert evidence["criterion"] == "latest_transaction_at"
        assert evidence["selected_source"] == "coinbase-9.csv"
        assert [r["fees"] for r in evidence["reports"]] == [[2.0], [1.5]]
        assert deduplicate(kept) == (kept, 0)
    assert old + new == before


def test_generated_dates_override_coverage_only_when_every_report_has_one(report):
    from src.export import deduplicate
    old = report("old.csv", latest="2024-04-01T00:00:00Z", generated="2024-05-01T00:00:00Z")
    new = report("new.csv", ("1.5",), latest="2024-03-01T00:00:00Z", generated="2024-06-01T00:00:00Z")
    selected = sales(deduplicate(old + new)[0])[0]
    assert selected["source"] == "new.csv"
    assert selected["fee_resolution"]["criterion"] == "generated_at"


@pytest.mark.parametrize("unknown", [None, "not a timestamp", "2024-06-01",
                                     "2024-06-01T00:00:00", "2024-02-01T00:00:00Z"])
def test_unknown_or_inconsistent_generation_uses_one_common_coverage_criterion(report, unknown):
    from src.export import deduplicate
    old = report("old.csv", generated="2024-08-01T00:00:00Z")
    new = report("new.csv", ("1.5",), latest="2024-03-01T00:00:00Z", generated=unknown)
    selected = sales(deduplicate(old + new)[0])[0]
    assert selected["source"] == "new.csv"
    assert selected["fee_resolution"]["criterion"] == "latest_transaction_at"


def test_mixed_three_report_comparison_is_order_independent(report):
    from src.export import deduplicate
    groups = [report("a.csv", ("1",), latest="2024-03-01T00:00:00Z", generated="2024-06-01T00:00:00Z"),
              report("b.csv", ("2",), latest="2024-04-01T00:00:00Z", generated="2024-05-01T00:00:00Z"),
              report("c.csv", ("3",), latest="2024-02-01T00:00:00Z")]
    for ordered in permutations(groups):
        selected = sales(deduplicate(sum(ordered, []))[0])[0]
        assert selected["fees"] == 2 and selected["source"] == "b.csv"
        assert selected["fee_resolution"]["criterion"] == "latest_transaction_at"


@pytest.mark.parametrize("generation", [False, True])
def test_tied_authority_with_disagreeing_fees_rejects_without_private_fields(report, generation):
    from src.export import deduplicate
    old = report("old.csv", generated="2024-06-01T00:00:00Z" if generation else None)
    new = report("new.csv", ("1.5",),
                 latest="2024-03-01T00:00:00Z" if generation else "2024-01-31T19:00:00-05:00",
                 generated="2024-05-31T20:00:00-04:00" if generation else None)
    for rows in (old + new, new + old):
        with pytest.raises(ValueError, match="recency is tied") as error:
            deduplicate(rows)
        for private in ("old.csv", "new.csv", "fictional-sale", "37.5", "73", "2024", "TEST"):
            assert private not in str(error.value)


def test_tied_newest_reports_with_agreeing_fee_evidence_can_resolve_older_conflict(report):
    from src.export import deduplicate
    groups = [report("a.csv"),
              report("z.csv", ("1",), latest="2024-03-01T00:00:00Z"),
              report("b.csv", ("1",), latest="2024-03-01T00:00:00Z")]
    for ordered in permutations(groups):
        selected = sales(deduplicate(sum(ordered, []))[0])[0]
        assert selected["fees"] == 1
        assert selected["fee_resolution"]["selected_source"] == "b.csv"


def test_intraday_and_fractional_timestamps_rank_in_utc(report):
    from src.export import deduplicate
    old = report("a.csv", latest="2024-02-01T12:00:00.000001Z")
    new = report("b.csv", ("1",), latest="2024-02-01T07:00:00.000002-05:00")
    assert sales(deduplicate(old + new)[0])[0]["source"] == "b.csv"


@pytest.mark.parametrize("field,value", [
    ("ID", "different-fictional-id"), ("ID", ""),
    ("Timestamp", "2024-01-02 13:00:00 UTC"),
    ("Quantity Transacted", "-2.000000003"), ("Quantity Transacted", "2"),
    ("Price at Transaction", "37.500000003"),
    ("Price at Transaction", "37.500000000000000000000000001"),
    ("Subtotal", "75.01"), ("Subtotal", ""),
    ("Total (inclusive of fees and/or spread)", "73.003"),
    ("Total (inclusive of fees and/or spread)", "-73"),
    ("Price Currency", "EUR"), ("Notes", "A different fictional note"),
])
def test_nonfee_differences_cannot_use_recency_even_when_old_hash_matches(report, field, value):
    from src.export import deduplicate
    old = report("old.csv")
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z", changes={field: value})
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate(old + new)


def test_numeric_formatting_and_equivalent_timestamp_are_not_economic_differences(report):
    from src.export import deduplicate
    old = report("old.csv")
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z", changes={
        "Quantity Transacted": "(2.000)", "Price at Transaction": "$37.5000",
        "Subtotal": "75.000", "Total (inclusive of fees and/or spread)": "$73.00",
        "Timestamp": "2024-01-02T07:00:00-05:00",
    })
    assert sales(deduplicate(old + new)[0])[0]["fees"] == 1


def test_blank_fee_is_not_evidence_of_an_explicit_zero(report):
    from src.export import deduplicate
    old = report("old.csv", ("",))
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z")
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate(old + new)


def test_decimal_evidence_does_not_expand_large_negative_exponents(report):
    from src.export import deduplicate
    old = report("old.csv", changes={"Subtotal": "1e-1000000000"})
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z",
                 changes={"Subtotal": "10e-1000000001"})
    assert sales(deduplicate(old + new)[0])[0]["fees"] == 1


def test_unrepresentable_decimal_evidence_is_unavailable_without_traceback(report):
    from src.export import deduplicate
    old = report("old.csv", changes={"Subtotal": "1e-9999999999999999999"})
    assert "_coinbase_import" not in sales(old)[0]
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z")
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate(old + new)


def test_invalid_timezone_offset_cannot_establish_report_recency(report):
    from src.export import deduplicate
    old = report("old.csv", latest="2024-02-01T00:00:00+00:99")
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z")
    with pytest.raises(ValueError, match="recency is unavailable"):
        deduplicate(old + new)


def test_equal_multiplicity_preserves_newest_fee_distribution(report):
    from src.export import deduplicate
    old = report("old.csv", ("1", "3", "1"))
    new = report("new.csv", ("2", "2", "1"), latest="2024-03-01T00:00:00Z")
    selected, removed = deduplicate(old + new)
    assert Counter(row["fees"] for row in sales(selected)) == Counter([2, 2, 1])
    assert removed == 3


@pytest.mark.parametrize("old_fees,new_fees", [(('1', '1'), ('2',)), (('1',), ('2', '2'))])
def test_incompatible_occurrence_counts_remain_ambiguous(report, old_fees, new_fees):
    from src.export import deduplicate
    old = report("old.csv", old_fees)
    new = report("new.csv", new_fees, latest="2024-03-01T00:00:00Z")
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate(old + new)


def test_compatible_partial_reports_keep_existing_deduplication(report):
    from src.export import deduplicate
    old = report("old.csv", ("1", "2", "1"))
    new = report("new.csv", ("2",), latest="2024-03-01T00:00:00Z")
    selected, _ = deduplicate(old + new)
    assert Counter(row["fees"] for row in sales(selected)) == Counter([1, 2, 1])
    assert not any("fee_resolution" in row for row in selected)


@pytest.mark.parametrize("action", ["Fee", "Convert", "Staking Income"])
def test_nontrade_rows_cannot_use_the_exception(report, action):
    from src.export import deduplicate
    old = report("old.csv", changes={"Transaction Type": action})
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z", changes={"Transaction Type": action})
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate(old + new)


def test_filenames_and_filesystem_dates_do_not_supply_recency(report, isolated_workdir):
    from src.export import deduplicate
    old = report("coinbase-1.csv")
    new = report("coinbase-99.csv", ("1",), latest="2024-03-01T00:00:00Z")
    os.utime(isolated_workdir / "data" / "coinbase-1.csv", (2000000000, 2000000000))
    os.utime(isolated_workdir / "data" / "coinbase-99.csv", (1000000000, 1000000000))
    assert sales(deduplicate(new + old)[0])[0]["fees"] == 1


def test_timezone_free_report_coverage_is_unknown(report):
    from src.export import deduplicate
    old = report("old.csv", latest="2024-02-01T00:00:00")
    new = report("new.csv", ("1",), latest="2024-03-01T00:00:00Z")
    with pytest.raises(ValueError, match="recency is unavailable"):
        deduplicate(old + new)


@pytest.mark.parametrize("preamble,expected", [
    ([["Generated at", "2024-05-01T00:00:00Z"]], "2024-05-01T00:00:00.000000Z"),
    ([["Report generated at: 2024-05-01 01:00:00+01:00"]], "2024-05-01T00:00:00.000000Z"),
    ([["Report period", "2024-05-01T00:00:00Z"]], None),
    ([["Generated at", "2024-05-01"]], None),
    ([["Generated at", "2024-05-01T00:00:00"]], None),
    ([["Generated at", "2024-05-01T00:00:00+00:99"]], None),
    ([["Generated at", "2024-05-01T00:00:00+24:00"]], None),
    ([["Generated at", "2024-05-01T00:00:00Z"], ["Generated at", "invalid"]], None),
    ([["Generated at", "2024-05-01T00:00:00Z"], ["Generated at", "2024-06-01T00:00:00Z"]], None),
])
def test_generation_metadata_requires_explicit_unambiguous_zoned_value(report, preamble, expected):
    parsed = report("example.csv", preamble=preamble)
    assert sales(parsed)[0]["_coinbase_import"]["generated_at"] == expected
