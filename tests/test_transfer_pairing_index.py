"""Indexed transfer candidates must preserve verified exhaustive matching."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import random
import math

import pytest


def _reference_pair_transfers(txns):
    """Exhaustive oracle for equal quantities without split metadata.

    Keep this intentionally simple: it is an independent behavioral oracle,
    including stable ties and quantity validation only for eligible dates.
    Split conversion and unverified candidates have dedicated contract tests.
    """
    from src.basis import _basis_effect

    outs = [t for t in txns if _basis_effect(t) == "transfer_out"]
    ins = [t for t in txns if _basis_effect(t) == "transfer_in"]
    matches, intra_group, used = {}, set(), set()

    def parse(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    for tin in sorted(ins, key=lambda t: t.get("date", "")):
        symbol = tin.get("symbol", "")
        qty = float(tin.get("quantity", 0) or 0)
        when = parse(tin.get("date", ""))
        if when is None or not math.isfinite(qty) or qty <= 0:
            continue
        best, best_score = None, None
        for tout in outs:
            if id(tout) in used or tout.get("symbol", "") != symbol:
                continue
            out_date = parse(tout.get("date", ""))
            if out_date is None:
                continue
            delta = (when - out_date).days
            if delta < 0 or delta > 14:
                continue
            out_qty = float(tout.get("quantity", 0) or 0)
            if not math.isfinite(out_qty) or out_qty <= 0:
                continue
            rel = abs(qty - out_qty) / max(out_qty, qty)
            if not math.isclose(qty, out_qty, rel_tol=1e-12, abs_tol=0.0):
                continue
            score = (rel, delta)
            if best_score is None or score < best_score:
                best, best_score = tout, score
        if best is not None:
            used.add(id(best))
            matches[id(tin)] = best
            if (best.get("account_group") == tin.get("account_group")
                    and best.get("symbol") == tin.get("symbol")):
                intra_group.update((id(tin), id(best)))
    return {"tin_to_tout": matches, "intra_group": intra_group,
            "paired_touts": used}


def _txn(action, when="2024-01-15", qty=10.0, *, symbol="FICT",
         account="Fictional Alpha"):
    return {"action": action, "date": when, "quantity": qty,
            "symbol": symbol, "account_group": account}


def _assert_reference(txns):
    from src.basis import _pair_transfers

    actual = _pair_transfers(txns)
    expected = _reference_pair_transfers(txns)
    # Object identities matter: equal-valued transactions are distinct legs.
    assert {key: id(value) for key, value in actual["tin_to_tout"].items()} == {
        key: id(value) for key, value in expected["tin_to_tout"].items()}
    assert actual["intra_group"] == expected["intra_group"]
    assert actual["paired_touts"] == expected["paired_touts"]
    return actual


@pytest.mark.parametrize("delta,matched", [(-1, False), (0, True),
                                           (14, True), (15, False)])
def test_transfer_window_boundaries(delta, matched):
    tout = _txn("Transfer Out")
    when = date(2024, 1, 15) + timedelta(days=delta)
    tin = _txn("Transfer In", when.isoformat())
    result = _assert_reference([tout, tin])
    assert (id(tin) in result["tin_to_tout"]) is matched


def test_relative_quantity_precedes_closest_date_and_stable_ties():
    earlier_exact = _txn("Transfer Out", "2024-01-01")
    closer_approx = _txn("Transfer Out", "2024-01-15", 10.005)
    later_exact = _txn("Transfer Out", "2024-01-10")
    tied_exact = dict(later_exact)
    tin = _txn("Transfer In")
    result = _assert_reference([earlier_exact, closer_approx,
                                later_exact, tied_exact, tin])
    assert result["tin_to_tout"][id(tin)] is later_exact


def test_earliest_inbound_and_input_order_for_same_date_take_each_out_once():
    first_out = _txn("Transfer Out", "2024-01-01")
    second_out = dict(first_out)
    late_in = _txn("Transfer In", "2024-01-03")
    first_in = _txn("Transfer In", "2024-01-02")
    second_in = dict(first_in)
    result = _assert_reference([first_out, second_out, late_in,
                                first_in, second_in])
    assert result["tin_to_tout"][id(first_in)] is first_out
    assert result["tin_to_tout"][id(second_in)] is second_out
    assert id(late_in) not in result["tin_to_tout"]


@pytest.mark.parametrize("out_qty,in_qty,matched", [
    (1000.0, 1001.0, False), (1000.0, 1001.001, False),
    (0.000001, 0.000002, False), (0.000001, 0.0000021, False),
    (1000.0, 1000.0 + 1e-10, True), (1e-12, 2e-12, False),
    (0.0, 1.0, False), (-1.0, 1.0, False), (1.0, 0.0, False),
])
def test_only_machine_precision_quantity_differences_are_verified(out_qty, in_qty, matched):
    tout = _txn("Transfer Out", qty=out_qty)
    tin = _txn("Transfer In", qty=in_qty)
    result = _assert_reference([tout, tin])
    assert (id(tin) in result["tin_to_tout"]) is matched


def test_canonical_symbol_identity_and_same_group_flags_are_preserved():
    outs = [_txn("Transfer Out", symbol=symbol)
            for symbol in ("FICT", " FICT ", "fict", "USD", " ", None)]
    ins = [_txn("Transfer In", symbol=symbol,
                account="Fictional Zeta" if symbol == "FICT" else "Fictional Alpha")
           for symbol in ("FICT", " FICT ", "fict", "USD", " ", None)]
    result = _assert_reference(outs + ins)
    assert len(result["tin_to_tout"]) == 3
    assert result["intra_group"] == {id(outs[1]), id(ins[1]), id(outs[2]), id(ins[2])}


def test_bad_dates_and_irrelevant_bad_quantities_stay_ignored():
    outs = [_txn("Transfer Out", when=value, qty="bad quantity")
            for value in (None, "", "invalid", "2024-02-30", [], 42)]
    outs += [_txn("Transfer Out", "2023-12-31", "bad quantity"),
             _txn("Transfer Out", "2024-01-16", "bad quantity"),
             _txn("Transfer Out", qty="bad quantity", symbol="OTHER")]
    valid = _txn("Transfer Out", "2024-1-1")
    tin = _txn("Transfer In")
    result = _assert_reference(outs + [valid, tin])
    assert result["tin_to_tout"][id(tin)] is valid


@pytest.mark.parametrize("bad", ["not a number", [1], {"value": 1}])
def test_eligible_bad_quantities_keep_the_existing_error(bad):
    txns = [_txn("Transfer Out", qty=bad), _txn("Transfer In")]
    from src.basis import _pair_transfers

    with pytest.raises((ValueError, TypeError)) as reference:
        _reference_pair_transfers(txns)
    with pytest.raises(type(reference.value)):
        _pair_transfers(txns)


@pytest.mark.parametrize("special", [float("nan"), float("inf"), -float("inf")])
def test_special_quantities_do_not_change_candidate_order(special):
    # Imports reject non-finite values; direct callers cannot verify them
    # or let them steal the ordinary valid candidate.
    txns = [_txn("Transfer Out", "2024-01-01", special),
            _txn("Transfer Out", "2024-01-10"), _txn("Transfer In")]
    _assert_reference(txns)


def test_earliest_representable_date_does_not_underflow():
    txns = [_txn("Transfer Out", "0001-01-01"),
            _txn("Transfer In", "0001-01-01")]
    assert len(_assert_reference(txns)["tin_to_tout"]) == 1


@pytest.mark.parametrize("seed", range(40))
def test_seeded_fictional_ledgers_match_exhaustive_reference(seed):
    rng = random.Random(seed)
    txns = []
    for _ in range(100):
        day = date(2024, 1, 1) + timedelta(days=rng.randrange(45))
        txns.append(_txn(rng.choice(["Transfer Out", "Transfer In", "Buy", "Sell"]),
                         day.isoformat(), rng.choice([0, 1, 1.0005, 1.0011, 1e-6, 2e-6]),
                         symbol=rng.choice(["FICT", "FICT2", " FICT ", "USD"]),
                         account=rng.choice(["Fictional Alpha", "Fictional Zeta"])))
    rng.shuffle(txns)
    _assert_reference(txns)


def test_disjoint_dates_are_parsed_once_per_transfer(monkeypatch):
    from src import basis

    parsed = []
    original = basis._safe_date

    def record(value):
        parsed.append(value)
        return original(value)

    monkeypatch.setattr(basis, "_safe_date", record)
    txns = []
    for i in range(80):
        when = (date(2020, 1, 1) + timedelta(days=i * 20)).isoformat()
        txns.extend([_txn("Transfer Out", when), _txn("Transfer In", when)])
    assert len(basis._pair_transfers(txns)["tin_to_tout"]) == 80
    assert len(parsed) == len(txns)
