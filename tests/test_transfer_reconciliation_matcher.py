"""Fictional transfer candidates require evidence of equal share ownership."""
from __future__ import annotations

import math

import pytest


def _leg(action, quantity=10.0, when="2024-01-05", group="Fictional Source"):
    return {"action": action, "date": when, "quantity": quantity,
            "symbol": "FICT", "account_group": group}


def _pair(out_qty=10.0, in_qty=10.0, *, destination="Fictional Destination"):
    return [_leg("Transfer Out", out_qty),
            _leg("Transfer In", in_qty, "2024-01-12", destination)]


@pytest.mark.parametrize("out_qty,in_qty", [(1000, 999.5), (1000, 1000.5),
                                            (1e-6, 2e-6), (1e-12, 2e-12),
                                            (10, 35)])
def test_quantity_discrepancy_is_diagnosed_without_carrying(out_qty, in_qty,
                                                          isolated_workdir):
    from src.basis import _pair_transfers

    out, arrival = _pair(out_qty, in_qty)
    result = _pair_transfers([out, arrival])
    assert result["tin_to_tout"] == {}
    assert result["paired_touts"] == result["intra_group"] == set()
    assert result["share_ratios"] == {}
    assert result["unverified_tins"] == {id(arrival)}
    assert result["issues"] == [{
        "source_group": "Fictional Source",
        "destination_group": "Fictional Destination",
        "start_date": "2024-01-05", "end_date": "2024-01-12",
        "symbol": "FICT", "reason": "quantity_mismatch",
    }]


def test_approximate_arrival_does_not_claim_a_later_exact_pair(isolated_workdir):
    from src.basis import _pair_transfers

    out = _leg("Transfer Out")
    approximate = _leg("Transfer In", 9.999, "2024-01-06", "Fictional Destination")
    exact = _leg("Transfer In", 10, "2024-01-07", "Fictional Destination")
    result = _pair_transfers([out, approximate, exact])
    assert result["tin_to_tout"] == {id(exact): out}
    assert result["share_ratios"] == {id(exact): 1.0}
    # An outbound already accounted for cannot confirm another arrival.
    assert result["issues"] == []
    assert result["unverified_tins"] == set()


@pytest.mark.parametrize("ratio", [2.0, 0.1, 1.5])
def test_forward_reverse_fractional_splits_match_common_units(ratio, isolated_workdir):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-10", ratio]]}
    out, arrival = _pair(10, 10 * ratio)
    result = basis._pair_transfers([out, arrival])
    assert result["tin_to_tout"] == {id(arrival): out}
    assert result["share_ratios"][id(arrival)] == ratio
    assert result["issues"] == []


def test_arrival_date_split_and_later_splits_are_applied_once(isolated_workdir):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-08", 3.0], ["2024-01-12", 0.5],
                              ["2024-07-01", 7.0]]}
    out, arrival = _pair(10, 15)
    result = basis._pair_transfers([out, arrival])
    assert result["tin_to_tout"] == {id(arrival): out}
    assert result["share_ratios"][id(arrival)] == 1.5


def test_split_effective_on_departure_is_already_in_both_posted_units(isolated_workdir):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-05", 2.0], ["2024-07-01", 3.0]]}
    out, arrival = _pair()
    result = basis._pair_transfers([out, arrival])
    assert result["share_ratios"] == {id(arrival): 1.0}


def test_same_raw_quantities_across_split_are_unverified(isolated_workdir):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-10", 2.0]]}
    rows = _pair()
    result = basis._pair_transfers(rows)
    assert result["tin_to_tout"] == {}
    assert result["issues"][0]["reason"] == "share_unit_mismatch"


@pytest.mark.parametrize("destination,split_group,split_day,verified", [
    ("Fictional Source", None, None, False),
    ("Fictional Destination", "Fictional Destination", "2024-01-10", False),
    ("Fictional Destination", "Fictional Destination", "2024-01-12", False),
    ("Fictional Destination", "Fictional Destination", "2024-01-05", True),
    ("Fictional Destination", "Fictional Destination", "2024-01-13", True),
    ("Fictional Destination", "Fictional Source", "2024-01-10", True),
])
def test_changed_units_require_unambiguous_posted_corporate_actions(
        destination, split_group, split_day, verified, isolated_workdir):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-10", 2.0]]}
    out, arrival = _pair(10, 20, destination=destination)
    rows = [out, arrival]
    if split_group:
        rows.append(_leg("Split", 10, split_day, split_group))
    result = basis._pair_transfers(rows)
    assert bool(result["tin_to_tout"]) is verified
    if not verified:
        assert result["issues"][0]["reason"] == "unsupported_split_transfer"
        assert result["intra_group"] == set()


@pytest.mark.parametrize("factor", [0.0, -1.0, math.inf, math.nan])
def test_invalid_share_factors_cannot_confirm_ownership(factor, isolated_workdir, monkeypatch):
    from src import basis, prices

    monkeypatch.setattr(prices, "split_factor_since", lambda *_: factor)
    result = basis._pair_transfers(_pair())
    assert result["tin_to_tout"] == {}
    assert result["issues"][0]["reason"] == "share_unit_mismatch"


def test_multiple_unverified_candidates_produce_one_stable_diagnostic(isolated_workdir):
    from src.basis import _pair_transfers

    far = _leg("Transfer Out", 40, "2024-01-11", "Fictional Far")
    near = _leg("Transfer Out", 10.005, "2024-01-05", "Fictional Near")
    tied = _leg("Transfer Out", 10.005, "2024-01-05", "Fictional Tied")
    arrival = _leg("Transfer In", 10, "2024-01-12", "Fictional Destination")
    result = _pair_transfers([far, near, tied, arrival])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["source_group"] == "Fictional Near"


def test_split_factor_lookups_are_cached_per_symbol_and_date(isolated_workdir, monkeypatch):
    from src import basis, prices

    calls = []
    monkeypatch.setattr(prices, "split_factor_since",
                        lambda symbol, day: calls.append((symbol, day)) or 1.0)
    outs = [_leg("Transfer Out", 10 + i) for i in range(10)]
    ins = [_leg("Transfer In", 10 + i, "2024-01-12", "Fictional Destination")
           for i in range(10)]
    assert len(basis._pair_transfers(outs + ins)["tin_to_tout"]) == 10
    assert sorted(calls) == [("FICT", "2024-01-05"), ("FICT", "2024-01-12")]


@pytest.mark.parametrize("same_group", [False, True])
def test_cancelling_factors_do_not_resolve_destination_split_rows(isolated_workdir, same_group):
    from src import basis, prices

    prices._splits = {"FICT": [["2024-01-08", 2.0], ["2024-01-10", 0.5]]}
    destination = "Fictional Source" if same_group else "Fictional Destination"
    out, arrival = _pair(destination=destination)
    result = basis._pair_transfers([out, arrival, _leg("Split", 10, "2024-01-08", destination)])
    assert result["tin_to_tout"] == {}
    assert result["issues"][0]["reason"] == "unsupported_split_transfer"
