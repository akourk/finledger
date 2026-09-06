"""Apply user-supplied cost-basis overrides onto fin's lots.

fin cannot reconstruct the cost basis of crypto received from off-platform
wallets — it assigns a $0/FMV guess to those transfer-in/deposit lots.  The
user can supply the real "customer-provided" basis (e.g. from Coinbase) via
`Cost Basis` rows in metadata.csv:

    Cost Basis, <date-acquired>, <cost-basis-USD>, <account_group>, <qty> <asset>
    # e.g.  Cost Basis, 2021-07-09, 12000.00, Coinbase, 5.1234567 ETH

Each row is matched to exactly one lot-creating txn by
(account_group, symbol, date±2d, qty) and stamps ``t["basis_override"]``,
which the basis walker honours on its add / unpaired-transfer-in /
unpaired-wrap-in branches.

Safeguards against mis-application:
  1. **Consume 1:1** — a lot is claimed by at most one override (so N
     same-key rows map to N same-key lots, never silently overlap).
  2. **Prefer non-intra-group lots** — when both an external deposit and an
     internal-move leg match, the external deposit is the likelier target.
     An intra-group Transfer In IS overridable though: the basis walker
     turns the pair into a *rebase* (consume the carried lots with no gain,
     push one lot at the override basis) — matching how Coinbase's tax
     engine treats Pro→regular arrivals as receives with customer-provided
     basis.
  3. **Per-unit sanity** — if a row's implied per-unit cost is wildly off
     the asset's market price on that date, warn (catches copy/paste typos).
  4. **`#N` index** — disambiguate same-(date, qty) lots when needed.
"""

from __future__ import annotations

from datetime import date as _date

_ADD_ACTIONS = {"Buy", "Transfer In", "Deposit", "Wrap Asset In",
                "Convert In", "Reinvest",
                # zero-basis lot creators whose true basis fin can't see:
                # a clearing-migration Conversion (e.g. a referral free
                # share whose grant-FMV basis exists only on the 1099)
                # or an award/reward the broker re-based.
                "Conversion", "Reward"}
_DATE_TOL_DAYS = 2


def _norm_symbol(asset: str, account: str = "Coinbase") -> str:
    from .config import normalize_symbol
    return normalize_symbol(asset, account)


def _pdate(s: str):
    try:
        return _date.fromisoformat((s or "")[:10])
    except (ValueError, TypeError):
        return None


def _sanity(ov: dict, sym: str, warnings: list) -> None:
    """Warn if the row's per-unit cost is far from market on that date."""
    if ov["qty"] <= 0:
        return
    per_unit = ov["amount"] / ov["qty"]
    try:
        from .prices import get_price
        px = get_price(sym, ov["date"])
    except Exception:
        px = None
    if px and px > 0 and (per_unit > px * 10 or per_unit < px * 0.1):
        warnings.append(
            f"Cost Basis: {ov['asset']} {ov['date']} per-unit ${per_unit:,.2f} "
            f"is far from market ${px:,.2f} — check that row (copy/paste error?)")


def match_and_stamp(txns: list, overrides: list | None) -> tuple[int, list]:
    """Stamp ``basis_override`` onto matched txns.

    Returns ``(applied_count, warnings)``.  ``applied_count`` counts rows
    that landed on an *overridable* (non-intra-group) lot.
    """
    if not overrides:
        return 0, []
    from .basis import _pair_transfers
    intra = _pair_transfers(txns)["intra_group"]

    warnings: list = []
    claimed: set = set()
    applied = 0

    for ov in sorted(overrides, key=lambda o: (o["date"], o["asset"], o["qty"])):
        origins = {txn.get("account", txn.get("account_group", ""))
                   for txn in txns if txn.get("account_group") == ov["account_group"]}
        crypto = origins and origins <= {"Coinbase", "Coinbase Pro"}
        sym = _norm_symbol(ov["asset"], "Coinbase" if crypto else ov["account_group"])
        od = _pdate(ov["date"])
        cands = []
        for t in txns:
            if id(t) in claimed:
                continue
            if t.get("account_group") != ov["account_group"]:
                continue
            if t.get("symbol") != sym or t.get("action") not in _ADD_ACTIONS:
                continue
            td = _pdate(t.get("date", ""))
            if not (td and od) or abs((td - od).days) > _DATE_TOL_DAYS:
                continue
            tq = float(t.get("quantity", 0) or 0)
            if abs(tq - ov["qty"]) > max(1e-6, ov["qty"] * 0.02):
                continue
            # sort key: non-intra first, then closest date, then closest qty
            cands.append(((id(t) in intra), abs((td - od).days),
                          abs(tq - ov["qty"]), t))

        if not cands:
            warnings.append(
                f"Cost Basis: no matching {ov['account_group']} {ov['asset']} lot "
                f"near {ov['date']} qty {ov['qty']:g} — typo, already-claimed by "
                f"another row, or more rows than lots for this key")
            continue

        cands.sort(key=lambda c: (c[0], c[1], c[2]))
        pick = None
        if ov.get("index"):
            i = ov["index"] - 1
            if 0 <= i < len(cands):
                pick = cands[i][3]
        if pick is None:
            pick = cands[0][3]

        claimed.add(id(pick))
        pick["basis_override"] = float(ov["amount"])
        _sanity(ov, sym, warnings)
        # Both external-deposit lots AND intra-group transfer-in legs are
        # overridable — the walker rebases the latter (consume carried
        # lots, push the override basis; see basis._walk's rebase block).
        applied += 1

    return applied, warnings
