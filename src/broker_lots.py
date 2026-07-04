"""Broker-reported disposal lots — report-directed lot relief.

Coinbase's tax center exports a per-lot gain/loss report (kept in
``data/`` as reference; the scanner classifies it ``skip``).  Each row
is one TAX LOT consumed by a disposal: asset, quantity, date acquired,
cost basis, date of disposition.  That is exactly the information fin's
lot walker otherwise has to GUESS via a lot-relief method (FIFO/HIFO) —
and two defensible orderings can diverge on which lots survive, drifting
fin's realized gains and remaining-pool basis away from what the broker
reports to the IRS (observed: a customer-provided 2021 lot survived in
Coinbase's pool to 2026 while fin's HIFO consumed it in 2024).

This module parses the report into per-disposal-day "hints":

    {(account_group, symbol, iso_sell_date): [
        {"acquired": "YYYY-MM-DD", "qty": float, "qty_left": float,
         "per_unit": float},
    ...], ...}

``basis._consume_lots_directed`` consumes pool lots matching each
hint (by acquired date, then near-date, then per-unit basis), falling
back to the account's normal method order for anything unmatched.  This
is purely a CONSUME-ORDER strategy — booked basis is always the actual
pool-lot basis, so balance↔basis parity and the annotation
reconstruction (`derive_basis_by_key_from_txns`) hold unchanged.

The report is Coinbase-format; hints are keyed to the Coinbase account
group.  Wraps (ETH↔CBETH) preserve lot acquired-dates through
``_rescale_lots``, so a report hint naming a 2021 acquisition finds the
wrapped lot in the destination symbol's queue.
"""

from __future__ import annotations

import csv
from pathlib import Path

# The account group the Coinbase tax-center report describes.
_REPORT_ACCOUNT_GROUP = "Coinbase"
_ROBINHOOD_ACCOUNT_GROUP = "Robinhood"

# Header signature — matches the scanner's skip rule for this file.
_LOT_ID_COL = "tax lot id"


def _norm_symbol(asset: str) -> str:
    from .config import CRYPTO_SYMBOLS, SYMBOL_MAP
    a = SYMBOL_MAP.get(asset, asset)
    if (a in CRYPTO_SYMBOLS or asset in CRYPTO_SYMBOLS) and not a.endswith("-USD"):
        return a + "-USD"
    return a


def _iso(s: str) -> str:
    """Normalize a report date (MM/DD/YYYY, YYYYMMDD, or ISO) to
    YYYY-MM-DD.  Empty / unparseable → "" (Robinhood leaves acquired
    blank for "Various" lots)."""
    s = (s or "").strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            m, d, y = parts
            return f"{y[:4]}-{int(m):02d}-{int(d):02d}"
    if len(s) == 8 and s.isdigit():        # YYYYMMDD (Robinhood 1099-B)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _pdate(s: str):
    """Parse an ISO YYYY-MM-DD (or 10-char prefix) into a date, else None."""
    from datetime import date as _d
    try:
        return _d.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _num(s: str) -> float:
    try:
        return float((s or "0").replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def _is_gainloss_file(path: Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                if _LOT_ID_COL in line.lower():
                    return True
    except OSError:
        pass
    return False


def load_disposal_lots(data_dir: Path,
                       txns: list[dict] | None = None
                       ) -> dict[tuple[str, str, str], list[dict]] | None:
    """Find + parse broker disposal reports in ``data_dir`` into the
    per-disposal lot-hint dict, or ``None`` when none are present.

    Merges two sources into one dict keyed by
    ``(account_group, symbol, sale_date)``:
      - Coinbase tax-center gain/loss report(s) (symbol from the report)
      - Robinhood consolidated 1099 CSV(s) 1099-B section (symbol resolved
        from ``txns`` by matching each disposal to fin's Sell) — only when
        ``txns`` is supplied.

    Multiple report files merge (rows accumulate per key) — harmless if
    the user drops overlapping exports, since hints direct consume ORDER;
    exhausted hints simply fall back to the method order.
    """
    if not data_dir.exists():
        return None
    out: dict[tuple[str, str, str], list[dict]] = {}
    found = False
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_gainloss_file(p):
            continue
        found = True
        for row in _parse_gainloss(p):
            out.setdefault(
                (_REPORT_ACCOUNT_GROUP, row["symbol"], row["sold"]),
                []).append({
                    "acquired": row["acquired"],
                    "qty":      row["qty"],
                    "qty_left": row["qty"],
                    "per_unit": row["per_unit"],
                })

    rh = load_robinhood_1099_lots(data_dir, txns) if txns else None
    if rh:
        found = True
        for k, v in rh.items():
            out.setdefault(k, []).extend(v)

    return out if found else None


def _parse_gainloss(path: Path) -> list[dict]:
    """Parse one report: skip the preamble, locate the header row, and
    yield one dict per lot row."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hdr_idx = None
    for i, r in enumerate(rows[:20]):
        if any(_LOT_ID_COL == (c or "").strip().lower() for c in r):
            hdr_idx = i
            break
    if hdr_idx is None:
        return []
    hdr = [c.strip() for c in rows[hdr_idx]]
    col = {c.lower(): i for i, c in enumerate(hdr)}

    def _get(r, name):
        i = col.get(name)
        return r[i] if (i is not None and i < len(r)) else ""

    out: list[dict] = []
    for r in rows[hdr_idx + 1:]:
        if len(r) < len(hdr) - 2:
            continue
        qty = _num(_get(r, "amount"))
        basis = _num(_get(r, "cost basis (usd)"))
        acquired = _iso(_get(r, "date acquired"))
        sold = _iso(_get(r, "date of disposition"))
        asset = (_get(r, "asset name") or "").strip()
        if qty <= 0 or not asset or not sold:
            continue
        out.append({
            "symbol":   _norm_symbol(asset),
            "qty":      qty,
            "acquired": acquired,
            "sold":     sold,
            "per_unit": basis / qty if qty > 0 else 0.0,
        })
    return out


# ---------------------------------------------------------------------------
# Robinhood consolidated 1099 — 1099-B per-lot disposals (STOCK lot relief)
# ---------------------------------------------------------------------------
# The yearly consolidated 1099 CSV is multi-section: column 0 tags each row's
# form (1099-DIV / 1099-INT / 1099-B / 1099-MISC) and each section carries its
# own header row (col 1 == "ACCOUNT NUMBER").  The 1099-B section is the
# per-lot capital-gains detail — one row per tax lot a sale consumed:
# DATE ACQUIRED, SALE DATE, DESCRIPTION, SHARES, COST BASIS, SALES PRICE, TERM.
#
# fin's own Robinhood transaction CSVs already carry accurate buy prices, so
# unlike Coinbase we don't need the 1099 for BASIS — we need it for lot RELIEF
# ORDER (which acquisition lot each sale consumed; e.g. the NVDA sale fin's
# FIFO relieves short-term while the 1099-B reports it long-term).  The
# DESCRIPTION is a full security NAME (not a ticker) with a fixed-width
# space-wrap artifact, so instead of name→ticker resolution we match each
# disposal to fin's own Sell txn by (sale date, shares, proceeds) and take the
# symbol from there.  Options are skipped: each option position is a single
# lot, so lot relief is a no-op for them (their realized differences are
# BTO/STC pairing, not lot order).

_1099_FORMS = {"1099-DIV", "1099-INT", "1099-B", "1099-MISC"}


def _is_robinhood_1099_file(path: Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            head = "".join(f.readline() for _ in range(4)).lower()
    except OSError:
        return False
    return "1099-div" in head or ("date acquired" in head and "sale date" in head)


def _parse_1099b_rows(path: Path) -> list[dict]:
    """Section-aware parse of one consolidated 1099: yield one dict per
    1099-B DATA row.  Tracks the 1099-B header's column positions (a
    header row is the one whose column 1 is the literal "ACCOUNT NUMBER")
    so the section boundaries are handled exactly as the user described —
    switch on the column-0 form tag."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except OSError:
        return []
    bcols = None
    out: list[dict] = []
    for r in rows:
        if not r or r[0] not in _1099_FORMS:
            continue
        if r[0] != "1099-B":
            continue
        if len(r) > 1 and r[1].strip().upper() == "ACCOUNT NUMBER":
            bcols = {c.strip().lower(): i for i, c in enumerate(r)}
            continue
        if bcols is None:
            continue

        def g(name: str) -> str:
            i = bcols.get(name)
            return r[i].strip() if (i is not None and i < len(r)) else ""

        desc = " ".join(g("description").split())   # collapse wrap-artifact spaces
        shares = _num(g("shares"))
        if shares <= 0 or not desc:
            continue
        out.append({
            "desc":     desc,
            "acquired": _iso(g("date acquired")),
            "sold":     _iso(g("sale date")),
            "shares":   shares,
            "basis":    _num(g("cost basis")),
            "proceeds": _num(g("sales price")),
        })
    return out


def _is_option_desc(desc: str) -> bool:
    u = desc.upper()
    return " CALL $" in u or " PUT $" in u


def load_robinhood_1099_lots(data_dir: Path,
                             txns: list[dict]) -> dict | None:
    """Parse Robinhood consolidated 1099 CSV(s) → STOCK disposal-lot hints
    keyed by ``(Robinhood, symbol, sale_date)`` (same shape as the
    Coinbase gain/loss hints).  ``txns`` is required to resolve the
    symbol.  Returns ``None`` when no 1099 file is present.
    """
    if not data_dir.exists() or not txns:
        return None

    # fin's Robinhood STOCK sells (options excluded) with proceeds.
    sells: list[dict] = []
    for t in txns:
        if t.get("account_group") != _ROBINHOOD_ACCOUNT_GROUP:
            continue
        sym = t.get("symbol", "") or ""
        if " Call " in sym or " Put " in sym:
            continue
        if (t.get("action") or "") != "Sell":
            continue
        d = _pdate(t.get("date", ""))
        q = float(t.get("quantity", 0) or 0)
        if d is None or q <= 0:
            continue
        sells.append({"date": d, "symbol": sym, "qty": q,
                      "amt": abs(float(t.get("amount", 0) or 0))})

    out: dict = {}
    found = False
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_robinhood_1099_file(p):
            continue
        found = True
        rows = [r for r in _parse_1099b_rows(p)
                if not _is_option_desc(r["desc"])]
        # Group by (sale date, security) — one sale can consume several
        # tax lots (multiple rows), all sharing the fin Sell txn.
        groups: dict = {}
        for r in rows:
            groups.setdefault((r["sold"], r["desc"]), []).append(r)

        for (sold, desc), grp in groups.items():
            sd = _pdate(sold)
            if sd is None:
                continue
            tot_sh = sum(r["shares"] for r in grp)
            tot_pr = sum(r["proceeds"] for r in grp)
            # Match to fin's Sell: date ±3d, shares within 2%, proceeds
            # within 5% (a mis-match is a safe no-op — a hint on the wrong
            # symbol simply won't find a lot and falls back to the method).
            best = None
            best_score = None
            for s in sells:
                if abs((s["date"] - sd).days) > 3:
                    continue
                if abs(s["qty"] - tot_sh) > max(1e-6, tot_sh * 0.02):
                    continue
                if tot_pr > 0 and abs(s["amt"] - tot_pr) > max(1.0, tot_pr * 0.05):
                    continue
                score = (abs((s["date"] - sd).days), abs(s["qty"] - tot_sh))
                if best_score is None or score < best_score:
                    best, best_score = s, score
            if best is None:
                continue
            key_sym = best["symbol"]
            for r in grp:
                out.setdefault(
                    (_ROBINHOOD_ACCOUNT_GROUP, key_sym, r["sold"]), []).append({
                        "acquired": r["acquired"],
                        "qty":      r["shares"],
                        "qty_left": r["shares"],
                        "per_unit": r["basis"] / r["shares"] if r["shares"] > 0 else 0.0,
                    })
    return out if found else None


def hints_for(disposal_lots: dict | None, account_group: str, symbol: str,
              date: str) -> list[dict] | None:
    """Hint lots for a disposal: the exact day's list PLUS the adjacent
    days' lists, concatenated in (exact, −1, +1) priority.

    Report timestamps are UTC and systematically straddle fin's txn
    dates — a multi-day sell run can land such that fin's date X holds
    the report's X−1 lots while a (smaller) X key also exists.  Exact-
    only lookup then returns the wrong day's short list and the bulk of
    the disposal falls back to the method order.  Concatenation makes
    the lookup shift-tolerant: the returned list holds the SHARED hint
    dicts (``qty_left`` mutations persist across lookups), so multiple
    sells across adjacent days consume the same hint pool exactly once.
    """
    if not disposal_lots:
        return None
    from datetime import date as _d, timedelta
    try:
        base = _d.fromisoformat((date or "")[:10])
    except ValueError:
        return disposal_lots.get((account_group, symbol, date))
    combined: list[dict] = []
    for delta in (0, -1, 1):
        k = (account_group, symbol,
             (base + timedelta(days=delta)).isoformat())
        combined.extend(disposal_lots.get(k) or [])
    return combined or None


def copy_disposal_lots(disposal_lots: dict | None) -> dict | None:
    """Deep-copy the hint dict.  Each walker (basis, history, refresh
    re-walk) mutates ``qty_left`` as it consumes — every walk must start
    from a fresh copy or the second walk finds the hints exhausted."""
    if not disposal_lots:
        return None
    return {k: [dict(h) for h in v] for k, v in disposal_lots.items()}


# ---------------------------------------------------------------------------
# RAWTX — broker-reported ACQUISITION basis
# ---------------------------------------------------------------------------
# The tax-center "raw transactions" report carries Coinbase's own cost
# basis (incl. fees/spread) for every acquisition — including the
# customer-provided basis on receives and the carried basis on the
# ETH2-deprecation conversions that fin's Neutral treatment can't see.
# `stamp_acquisition_basis` auto-applies these onto fin's lot-creating
# txns (user `Cost Basis` metadata rows stamp first and always win).

_RAWTX_ACQ_COL = "asset acquired"
_RAWTX_DISP_COL = "asset disposed"

# Acquisition types whose basis we adopt.  Wrap/Unwrap/Stake/Unstake
# are carries fin already handles; Reward(s)/Credit are hundreds of
# micro-lots whose FMV fin already computes from the same price.
# Convert rows are included ONLY when they cross symbols (see the
# same-symbol exclusion below): measured on real data, excluding them
# entirely starves fin's pool of basis it has no other source for
# (2024 flipped +16k), while including same-symbol conversions
# double-injects carried lots at phantom dates.  The residual
# imperfection this leaves — Coinbase dates convert-carried lots by
# their ORIGINAL acquisition, fin by the convert date — is the known
# remaining source of per-year timing drift (see CLAUDE.md).
_ACQ_TYPES = {"Buy", "Receive", "Convert", "Converted to", "Airdrop",
              "Transfer"}

# fin actions that create lots (mirror cost_basis_overrides._ADD_ACTIONS).
_LOT_ADD_ACTIONS = {"Buy", "Transfer In", "Deposit", "Convert In",
                    "Reinvest"}


def _is_rawtx_file(path: Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            head = (f.readline() or "").lower()
        return _RAWTX_ACQ_COL in head and _RAWTX_DISP_COL in head
    except OSError:
        return False


def load_acquisition_lots(data_dir: Path) -> list[dict] | None:
    """Parse RAWTX report(s): one dict per acquisition row we adopt —
    ``{symbol, date, qty, basis, type}``, sorted by date."""
    if not data_dir.exists():
        return None
    out: list[dict] = []
    found = False
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_rawtx_file(p):
            continue
        found = True
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                typ = (r.get("Transaction Type") or "").strip()
                if typ not in _ACQ_TYPES:
                    continue
                asset = (r.get("Asset Acquired") or "").strip()
                qty = _num(r.get(
                    "Quantity Acquired (Bought, Received, etc)"))
                basis = _num(r.get(
                    "Cost Basis (incl. fees and/or spread) (USD)"))
                if not asset or qty <= 0 or basis <= 0:
                    continue
                # Same-pool conversions (ETH2→ETH deprecation etc.,
                # where disposed and acquired normalize to one symbol)
                # are carries fin already handles as Neutral — and the
                # customer-provided basis for those lots arrives via
                # the report's Receive rows at the correct per-unit.
                # Stamping the Convert row too would inject the SAME
                # lots again at a DIFFERENT per-unit than the one every
                # downstream disposal actually prices against (observed
                # on real data as a materially higher realized figure).
                disposed = (r.get(
                    "Asset Disposed (Sold, Sent, etc)") or "").strip()
                if disposed and _norm_symbol(disposed) == _norm_symbol(asset):
                    continue
                out.append({
                    "symbol": _norm_symbol(asset),
                    "date":   _iso((r.get("Date & time") or "")[:10]),
                    "qty":    qty,
                    "basis":  basis,
                    "type":   typ,
                })
    if not found:
        return None
    out.sort(key=lambda r: r["date"])
    return out


def stamp_acquisition_basis(txns: list[dict],
                            acq_rows: list[dict] | None) -> tuple[int, int]:
    """Stamp broker acquisition basis onto matching fin txns.

    Two match classes, both (Coinbase group, symbol, date ±2d, qty ±2%),
    each broker row claiming at most one txn, txns already stamped by a
    user `Cost Basis` row are never overwritten:

    1. Lot-creating txns (Buy / Transfer In / Deposit / Convert In /
       Reinvest) → plain ``basis_override`` (the walker's `_ov` /
       rebase branches pick it up).
    2. **Neutral txns** (fin's pooled ETH↔ETH2 conversions, incl. the
       ETH2 deprecation) → ``basis_override`` on a Neutral row, which
       the walker treats as a same-pool REBASE at zero gain — exactly
       how Coinbase's engine reports these (gain-0 dispositions whose
       acquired side carries the customer-provided basis).

    Returns ``(stamped_count, unmatched_count)``.
    """
    if not acq_rows:
        return 0, 0
    from datetime import date as _d, timedelta

    def _pd(s):
        try:
            return _d.fromisoformat((s or "")[:10])
        except ValueError:
            return None

    claimed: set[int] = set()
    stamped = 0
    unmatched = 0
    for row in acq_rows:
        rd = _pd(row["date"])
        if rd is None:
            continue
        pick = None
        for t in txns:
            if id(t) in claimed or t.get("basis_override") is not None:
                continue
            if t.get("account_group") != _REPORT_ACCOUNT_GROUP:
                continue
            if t.get("symbol") != row["symbol"]:
                continue
            act = t.get("action") or ""
            if act not in _LOT_ADD_ACTIONS and act != "Neutral":
                continue
            td = _pd(t.get("date", ""))
            if td is None or abs((td - rd).days) > 2:
                continue
            tq = float(t.get("quantity", 0) or 0)
            if abs(tq - row["qty"]) > max(1e-6, row["qty"] * 0.02):
                continue
            pick = t
            break
        if pick is None:
            unmatched += 1
            continue
        claimed.add(id(pick))
        pick["basis_override"] = float(row["basis"])
        stamped += 1
    return stamped, unmatched
