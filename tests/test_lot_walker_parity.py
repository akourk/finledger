"""The two lot walkers must stay structurally in step.

`basis._walk` and the inline walker in `history.compute_history` apply
the same basis rules to the same lot state. history keeps its own copy
because it needs lot state at *every sample date*, not just the end.
CLAUDE.md records the consequence:

    basis-rule changes have TWICE landed in basis.py without the
    matching history.py change (wrap basis-carrying, FMV transfer-ins)

The existing defences are real but incomplete:

* `_check_history_holdings_basis_parity` compares the two numerically —
  but only on the LATEST snapshot, and only for effects some fixture
  happens to exercise. A branch that exists in one walker and not the
  other is invisible to it until data reaches that branch.
* The `fin-lot-walker-sync` skill asks a human to remember.

This module adds the structural half: **both walkers must dispatch on
the same set of basis effects**, and the rules that were extracted to be
shared must actually still be shared. Neither needs a fixture to reach
the branch, so neither can be silently outrun by a new rule.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import src.basis as B
import src.history as H


def _dispatched_effects(fn) -> set[str]:
    """Every basis-effect string a function branches on.

    Reads `effect == "..."` and `effect in ("...", ...)` out of the AST
    rather than the source text, so a reformat cannot change the answer.
    """
    tree = ast.parse(inspect.getsource(fn).lstrip())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id == "effect"):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                if isinstance(comp.value, str):
                    found.add(comp.value)
            elif isinstance(op, ast.In) and isinstance(comp, (ast.Tuple,
                                                              ast.List,
                                                              ast.Set)):
                for elt in comp.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.add(elt.value)
    return found


class TestBothWalkersHandleTheSameEffects:

    def test_the_effect_sets_are_identical(self):
        basis_effects = _dispatched_effects(B._walk)
        hist_effects = _dispatched_effects(H.compute_history)

        only_basis = basis_effects - hist_effects
        only_hist = hist_effects - basis_effects
        assert not only_basis, (
            f"basis._walk handles {sorted(only_basis)} and history's walker "
            "does not — a basis rule that never reaches the snapshot series. "
            "This is the exact failure CLAUDE.md says has happened twice."
        )
        assert not only_hist, (
            f"history's walker handles {sorted(only_hist)} and basis._walk "
            f"does not — the Holdings table and the history chart will "
            "disagree."
        )

    def test_the_extraction_is_not_vacuous(self):
        """Guard: if the AST reader stopped finding anything, the test
        above would pass by comparing two empty sets."""
        found = _dispatched_effects(B._walk)
        assert len(found) >= 6, (
            f"only found {sorted(found)} — the effect reader is not "
            "seeing the dispatch, so the parity assertion proves nothing"
        )
        assert {"add", "remove", "split"} <= found

    def test_every_dispatched_effect_is_a_real_catalog_effect(self):
        """A typo'd branch (`"remvoe"`) is dead code that silently falls
        through to the ignore case — and would satisfy the parity test
        above if both walkers had the same typo."""
        from src.actions import BASIS_EFFECTS

        known = set(BASIS_EFFECTS.values()) | {"unknown"}
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            unknown = _dispatched_effects(fn) - known
            assert not unknown, (
                f"{name} branches on {sorted(unknown)}, which no action in "
                "the catalog produces — dead branch, or a typo that falls "
                "through to `ignore`"
            )

    def test_every_catalog_effect_is_handled_somewhere(self):
        """The other direction: an effect a real action can produce, that
        neither walker branches on, silently does nothing to lot state."""
        from src.actions import BASIS_EFFECTS

        produced = set(BASIS_EFFECTS.values())
        handled = _dispatched_effects(B._walk)
        # `ignore` is the documented no-op default and needs no branch of
        # its own; the walkers fall through to it.
        missing = produced - handled - {"ignore"}
        assert not missing, (
            f"actions produce basis effect(s) {sorted(missing)} that "
            "basis._walk never branches on — those transactions leave lot "
            "state untouched with no error"
        )


class TestTheSharedRulesAreStillShared:
    """The rules pulled out of both walkers must stay pulled out.

    Re-inlining one is how the drift started: the FMV-at-transfer rule
    and the split rescale each existed twice, and CLAUDE.md names both as
    changes that landed in one copy only.
    """

    def test_history_uses_the_shared_split_helper(self):
        src = inspect.getsource(H.compute_history)
        assert "_apply_split_to_lots" in src, (
            "history's split branch no longer calls the shared helper — "
            "the rescale rule is duplicated again (F-015)"
        )
        assert "basis_per_share /= ratio" not in src, (
            "history has re-inlined the split rescale"
        )

    def test_both_walkers_use_the_shared_fmv_rule(self):
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            assert "fmv_basis" in inspect.getsource(fn), (
                f"{name} no longer uses the shared FMV-at-transfer rule"
            )

    def test_both_walkers_use_the_shared_effect_classifier(self):
        """The symbol-aware `(sym, action) -> effect` rule.  history kept
        its own copy (`_basis_effect_for_sym`), and the copy had already
        drifted: it omitted the `.strip()`, so a whitespace-only symbol
        classified as "ignore" in one walker and fell through to the
        action lookup in the other."""
        assert "basis_effect_for" in inspect.getsource(H.compute_history), (
            "history's walker no longer calls basis.basis_effect_for — the "
            "effect classifier is duplicated again"
        )
        assert not hasattr(H, "_basis_effect_for_sym"), (
            "history has re-introduced its own effect classifier"
        )

    def test_both_walkers_use_the_shared_reservation_rule(self):
        """Which lots a later report disposal is holding, so an
        undirected consume avoids them.  Verbatim in both walkers before,
        with history's docstring saying it 'mirrors basis._walk's'."""
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            assert "reserved_for(" in inspect.getsource(fn), (
                f"{name} no longer routes lot reservation through the "
                "shared rule"
            )

    def test_both_walkers_agree_on_what_a_wrap_leg_is(self):
        """Both group wrap legs by (account, date, kind), so a
        disagreement on `kind` splits one atomic group into two."""
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert "wrap_kind(" in src, (
                f"{name} no longer uses the shared wrap-kind rule")
            assert '"Unwrap" in' not in src, (
                f"{name} has re-inlined the wrap-kind test")

    def test_both_walkers_use_the_shared_zero_basis_origin(self):
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert "zero_basis_origin(" in src, (
                f"{name} no longer routes zero-basis provenance through the "
                "shared rule")
            assert 'origin="reconstructed" if' not in src, (
                f"{name} has re-inlined the provenance rule")

    def test_both_walkers_use_the_shared_wrap_carry_rule(self):
        """The basis-carrying half of a wrap: which source lots go, and
        how they rescale to the destination.  CLAUDE.md names wrap
        basis-carrying as one of the two rules that shipped to basis.py
        only, and until this extraction it was ~25 duplicated lines in
        each walker."""
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert "wrap_carry_lots(" in src, (
                f"{name} no longer routes wrap carry through the shared rule")
            assert "take_wrap_demand(" not in src, (
                f"{name} has re-inlined the wrap demand lookup — the "
                f"direction rule is duplicated again")

    def test_both_walkers_use_the_shared_roc_rules(self):
        """Return of capital: the netting of a reversal against the
        credit it reverses, and the pro-rata basis reduction itself.

        Both are pure lot-state rules that each walker needs, so both
        live at module level in basis.py.  Re-inlining either is how a
        reversed distribution ends up applied twice in one walker and
        once in the other.
        """
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert "pair_roc_events(" in src, (
                f"{name} no longer nets ROC reversals through the shared rule")
            assert "apply_roc_to_lots(" in src, (
                f"{name} no longer applies ROC through the shared rule")
            assert "ROC_REVERSAL_ACTIONS" not in src, (
                f"{name} has re-inlined the ROC sign lookup — the netting "
                f"rule is duplicated again")


    def test_both_walkers_use_the_shared_override_rule(self):
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert ("basis_override_or" in src or "_ov(" in src), (
                f"{name} no longer routes cost-basis overrides through the "
                "shared helper"
            )

    def test_both_walkers_use_shared_transaction_lot_creation(self):
        """Broker acquisition pieces must survive in both lot queues.

        Replacing a multi-piece acquisition with one blended lot can
        preserve total basis at receipt while changing later HIFO or
        report-directed disposals.  Origin metadata is shared too.
        """
        for fn, name in ((B._walk, "basis._walk"),
                         (H.compute_history, "history's walker")):
            src = inspect.getsource(fn)
            assert "_push_txn_lots(" in src, (
                f"{name} no longer uses the shared transaction lot creator")
            assert 'get("basis_override_lots")' not in src, (
                f"{name} has re-inlined broker acquisition pieces")

    def test_both_walkers_share_transfer_carry_for_stashed_and_eager_arrivals(self):
        for fn in (B._walk, H.compute_history):
            source = inspect.getsource(fn)
            assert source.count("_push_transfer_lots(") == 2

    def test_history_uses_the_shared_cash_principal_rule(self):
        """Cash has no lots, so its basis is the one figure neither
        walker can read off a lot queue — each had to answer separately,
        and they answered differently for years: principal in
        `build_holdings`, face value here.  The numeric parity check
        could not catch it, because it skipped USD outright.
        """
        src = inspect.getsource(H.compute_history)
        assert "cash_principal_effect(" in src, (
            "history's walker no longer routes savings-cash basis through "
            "the shared principal rule — the convention is duplicated again"
        )
        assert "pos_basis = qty" not in src, (
            "history has re-inlined face-value cash basis, which disagrees "
            "with the holdings table by every dollar of interest earned"
        )


class TestTheSharedRulesThemselves:
    """`fmv_basis` and `basis_override_or` are now load-bearing for both
    walkers, so they get their own tests rather than being covered only
    through whichever walker a fixture happens to drive."""

    def _t(self, **kw):
        return {"symbol": "AAA", "quantity": 10.0, "price": 0.0,
                "amount": 0.0, **kw}

    def test_fmv_uses_quantity_times_price(self):
        assert B.fmv_basis(self._t(price=25.0), 10.0) == pytest.approx(250.0)

    def test_fmv_falls_back_to_zero_without_a_price(self):
        """$0 is the honest answer when the broker recorded no spot
        price — and it books the whole proceeds as gain on the eventual
        sale, which is why the FMV path matters."""
        assert B.fmv_basis(self._t(price=0.0), 10.0) == 0.0

    def test_an_override_beats_the_fmv_estimate(self):
        """The override is the broker's customer-provided basis for an
        off-platform acquisition. FMV is fin's guess; the override is
        the fact."""
        got = B.fmv_basis(self._t(price=25.0, basis_override=100.0), 10.0)
        assert got == pytest.approx(100.0)

    def test_a_zero_override_is_honoured_not_treated_as_absent(self):
        """A genuine $0 basis (a true zero-cost receive) must survive.
        Testing `if bo` instead of `if bo is not None` would silently
        substitute FMV."""
        got = B.fmv_basis(self._t(price=25.0, basis_override=0.0), 10.0)
        assert got == 0.0

    def test_override_or_returns_the_default_when_absent(self):
        assert B.basis_override_or(self._t(), 42.0) == 42.0

    def test_override_or_honours_a_zero_override(self):
        assert B.basis_override_or(self._t(basis_override=0.0), 42.0) == 0.0

    def test_a_negative_price_is_not_treated_as_fmv(self):
        """Parsers abs() price, but the rule guards on `> 0` rather than
        truthiness, so a bad row cannot produce negative basis."""
        assert B.fmv_basis(self._t(price=-5.0), 10.0) == 0.0
