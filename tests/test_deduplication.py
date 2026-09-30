"""Overlapping exports retain multiplicity and every observed fee value.

All transaction fields below are independently constructed fictional inputs.
"""

from collections import Counter
from itertools import permutations

import pytest

from src.export import deduplicate


def _row(source="first.csv", fees=1, **changes):
    return dict(date="2024-01-02", account="Fictional Account", symbol="FICT",
                action="Buy", quantity=5, price=17.53, amount=87.65,
                description="Fictional trade", source=source, fees=fees,
                **changes)


def _rows(distributions):
    return [_row(source=f"source-{index}.csv", fees=fee)
            for index, fees in enumerate(distributions) for fee in fees]


@pytest.mark.parametrize("distributions", [
    ((1, 1, 1), (1,)),
    ((1, 2, 1), (2, 1), (2,)),
    ((0.003127, 0.003128), (0.003128,)),
    ((1, 2), (2, 1)),
    ((1,), (1.0,)),
])
def test_compatible_fee_multisets_survive_every_row_and_source_order(distributions):
    rows = _rows(distributions)
    maximum = max(map(len, distributions))
    expected = Counter(next(fees for fees in distributions if len(fees) == maximum))
    for ordered in permutations(rows):
        before = [dict(row) for row in ordered]
        kept, removed = deduplicate(list(ordered))
        assert Counter(row["fees"] for row in kept) == expected
        assert len(kept) == maximum
        assert removed == len(rows) - maximum
        assert len({row["source"] for row in kept}) == 1
        assert deduplicate(kept) == (kept, 0)
        assert list(ordered) == before


@pytest.mark.parametrize("distributions", [
    ((1,), (2,)),
    ((1, 1), (1, 2)),
    ((1, 1, 1), (1, 2)),
    ((0, 2), (1, 1)),  # Equal totals do not establish equal fee evidence.
    ((1, 2, 3), (1, 1)),  # Presence alone is insufficient; count matters.
    ((1, 2), (1,), (3,)),  # A third partial export can expose a conflict.
    ((0.003127,), (0.003128,)),  # Do not round fees to cents.
])
def test_conflicting_fee_multisets_fail_for_every_row_and_source_order(distributions):
    for ordered in permutations(_rows(distributions)):
        with pytest.raises(ValueError, match="Conflicting fee evidence"):
            deduplicate(list(ordered))


def test_empty_input_and_legitimate_different_fee_repeats_are_preserved():
    assert deduplicate([]) == ([], 0)
    rows = [_row(fees=fee) for fee in (1, 2, 1, 0.003127)]
    assert deduplicate(rows) == (rows, 0)


def test_missing_source_and_fee_keep_the_existing_defaults():
    first, second = _row(fees=0), _row(source="second.csv", fees=0.0)
    del first["source"]
    del first["fees"]
    assert deduplicate([first, second]) == ([first], 1)
    assert deduplicate([first, first]) == ([first, first], 0)


def test_representative_preserves_first_maximum_source_and_its_row_order():
    rows = _rows(((1,), (2, 1), (1, 2)))
    assert deduplicate(rows) == (rows[1:3], 3)
    reordered = rows[3:] + rows[:3]
    assert deduplicate(reordered) == (rows[3:], 3)


@pytest.mark.parametrize(("field", "same", "different"), [
    ("quantity", 5.000000003, 5.000000006),
    ("price", 17.530000003, 17.530000006),
    ("amount", 87.654, 87.656),
])
def test_identity_retains_existing_eight_digit_and_cent_boundaries(field, same, different):
    first = _row()
    rounded_same = _row(source="second.csv")
    rounded_same[field] = same
    assert deduplicate([first, rounded_same]) == ([first], 1)
    rounded_same["fees"] = 2
    with pytest.raises(ValueError, match="Conflicting fee evidence"):
        deduplicate([first, rounded_same])
    distinct = dict(rounded_same, **{field: different})
    assert deduplicate([first, distinct]) == ([first, distinct], 0)


@pytest.mark.parametrize(("field", "different"), [
    ("date", "2024-01-03"), ("account", "Another Fictional Account"),
    ("symbol", "OTHER"), ("action", "BUY"), ("quantity", 6),
    ("price", 18.53), ("amount", 88.65),
])
def test_fees_do_not_conflict_across_distinct_transaction_identities(field, different):
    first = _row(fees=1)
    second = _row(source="second.csv", fees=2)
    second[field] = different
    assert deduplicate([first, second]) == ([first, second], 0)


def test_conflict_error_contains_no_input_fields_or_identity_hash():
    from src.export import _txn_hash

    rows = [_row(source="private-looking-fictional-export.csv", fees=0.003127),
            _row(source="another-fictional-export.csv", fees=0.003128)]
    with pytest.raises(ValueError) as caught:
        deduplicate(rows)
    message = str(caught.value)
    for row in rows:
        for value in row.values():
            assert str(value) not in message
        assert _txn_hash(row) not in message
