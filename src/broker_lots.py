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
# form (1099-DIV / 1099-INT / 1099-B / 1099-MISC).  Reading top-down, the
# FIRST row of each new column-0 tag run is that section's header; the rest
# of the run is data (see _iter_1099_rows).  The 1099-B section is the
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


def _iter_1099_rows(rows):
    """Yield ``(form_tag, cols, row)`` for each DATA row of a
    multi-section consolidated 1099.

    Section rule (the file format is self-describing): reading top-down,
    the FIRST row whose column-0 form tag differs from the previous
    form-tagged row starts a new section, and that row IS the section's
    header; the following rows carrying the same tag are its data.  No
    column name needs to be known in advance.  A mid-run row whose
    column 1 reads "ACCOUNT NUMBER" additionally refreshes the header
    map (defensive — some exports repeat headers per account).

    ``cols`` maps the section's lower-cased column names to indices.
    Rows whose column 0 is not a known form tag (preamble, blanks,
    summary lines) are skipped without breaking the current run.
    """
    cols_by_form: dict[str, dict[str, int]] = {}
    last_tag: str | None = None
    for r in rows:
        if not r or r[0] not in _1099_FORMS:
            continue
        tag = r[0]
        is_header = (tag != last_tag) or (
            len(r) > 1 and r[1].strip().upper() == "ACCOUNT NUMBER")
        last_tag = tag
        if is_header:
            cols_by_form[tag] = {c.strip().lower(): i for i, c in enumerate(r)}
            continue
        yield tag, cols_by_form[tag], r


def robinhood_1099_tax_year(path: Path) -> str | None:
    """Tax year of a consolidated 1099 — from the first data row's
    TAX YEAR column (every section carries one).  Used by the scanner's
    rename pass so a fresh UUID-named yearly download self-names to
    ``robinhood-1099-{year}.csv`` like the transaction CSVs do.
    Returns None when the file has no parsable year."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for _tag, cols, r in _iter_1099_rows(csv.reader(f)):
                i = cols.get("tax year")
                if i is None or i >= len(r):
                    continue
                y = r[i].strip()
                if len(y) == 4 and y.isdigit():
                    return y
    except OSError:
        return None
    return None


def _parse_1099b_rows(path: Path) -> list[dict]:
    """Section-aware parse of one consolidated 1099: yield one dict per
    1099-B DATA row.  Section boundaries follow the format's own rule
    (see ``_iter_1099_rows``): the first row of each column-0 form-tag
    run is that section's header."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except OSError:
        return []
    out: list[dict] = []
    for tag, bcols, r in _iter_1099_rows(rows):
        if tag != "1099-B":
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


def load_robinhood_1099_income(data_dir: Path) -> list[dict]:
    """Auto-generate ``Reconcile Income`` rows from the consolidated
    1099s' DIV/INT sections: 1099-DIV box 1a (``ORDINARY DIV``, which
    includes qualified) + 1099-INT box 1 (``INT INCOME``) per tax year.

    This is exactly the figure fin's ``income`` reconcile kind checks
    (dividends + interest + lending — Robinhood bundles stock-lending
    income into the 1099-INT as substitute interest), so each year with
    a 1099 on disk gets a broker-ground-truth row without hand-entering
    it in metadata.csv.  Returned rows have the same shape as parsed
    ``Reconcile *`` metadata rows; a hand-entered row for the same
    (kind, account, year) wins (see ``merge_auto_reconcile_rows``).
    """
    out: list[dict] = []
    if not data_dir.exists():
        return out
    for p in sorted(data_dir.glob("*.csv")):
        if not _is_robinhood_1099_file(p):
            continue
        year = robinhood_1099_tax_year(p)
        if not year:
            continue
        try:
            with open(p, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        except OSError:
            continue
        # Section headers are self-describing (see _iter_1099_rows), so
        # the box-1a / box-1 columns are looked up by name in whatever
        # header each section actually carries.
        want = {"1099-DIV": "ordinary div", "1099-INT": "int income"}
        total = 0.0
        found_col = False
        for tag, cols, r in _iter_1099_rows(rows):
            col = want.get(tag)
            if col is None:
                continue
            i = cols.get(col)
            if i is None or i >= len(r):
                continue
            found_col = True
            total += _num(r[i])
        if found_col:
            out.append({
                "kind": "income",
                "account_group": _ROBINHOOD_ACCOUNT_GROUP,
                "date": year,
                "amount": round(total, 2),
                "note": f"auto: {p.name} (1099-DIV 1a + 1099-INT box 1)",
            })
    return out


def merge_auto_reconcile_rows(manual: list[dict] | None,
                              auto: list[dict]) -> list[dict]:
    """Append auto-generated reconcile rows to the user's hand-entered
    ones, skipping any (kind, account_group, year) the user already
    covers — a hand row may carry a corrected figure and must win."""
    manual = list(manual or [])
    have = {(r.get("kind"), r.get("account_group"), (r.get("date") or "")[:4])
            for r in manual}
    for r in auto:
        key = (r.get("kind"), r.get("account_group"), (r.get("date") or "")[:4])
        if key not in have:
            manual.append(r)
    return manual


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


def reserved_future_demand(disposal_lots: dict | None, account_group: str,
                           date: str, symbols: set[str] | None = None
                           ) -> dict[str, float] | None:
    """``{acquired_date: qty}`` of this account's report lots still to be
    disposed strictly AFTER ``date`` — the units the broker's inventory
    is holding for a later sale.

    Undirected fallback consumption (a sale remainder the hints don't
    cover, a wrap remainder past the demand window, a rebase) must avoid
    these lots or it destroys per-unit flavors the report's later
    disposals name (observed: HIFO fallbacks ate the customer-provided
    2021 lot years before the report's 2026 sale relieved it, producing
    offsetting multi-$k per-year realized drift).

    ``symbols`` restricts the demand to those disposal symbols — pass
    the consuming pool's wrap FAMILY (``wrap_symbol_families``): wraps
    relocate lots across symbols with acquired dates preserved, so
    demand for the wrapped symbol must reserve lots still sitting in
    the source pool, but demand for an UNRELATED symbol must not (a
    same-date buy of another ticker would spuriously deflect this
    pool's consumption).  Qty is a per-date budget — a date's surplus
    above it stays freely consumable."""
    if not disposal_lots:
        return None
    cutoff = (date or "")[:10]
    out: dict[str, float] = {}
    for (a, sym, sold), hints in disposal_lots.items():
        if a != account_group or sold <= cutoff:
            continue
        if symbols is not None and sym not in symbols:
            continue
        for h in hints:
            acq = h.get("acquired") or ""
            ql = float(h.get("qty_left", 0) or 0)
            if acq and ql > 0:
                out[acq] = out.get(acq, 0.0) + ql
    return out or None


def wrap_symbol_families(wrap_groups: dict | None) -> dict[str, set[str]]:
    """``{symbol: {family symbols}}`` — symbols connected by wrap/unwrap
    groups (ETH↔CBETH) share one family; everything else is alone.
    Takes the ``_pair_wraps`` group dict (both walkers build it)."""
    fam: dict[str, set[str]] = {}
    for (_a, _d, _k), g in (wrap_groups or {}).items():
        s, d = g.get("src"), g.get("dst")
        if not s or not d:
            continue
        union = fam.get(s, {s}) | fam.get(d, {d})
        for x in union:
            fam[x] = union
    return fam


# ---------------------------------------------------------------------------
# Wrap demand — which lots should a basis-carrying wrap move?
# ---------------------------------------------------------------------------
# A wrap (ETH→CBETH) is not itself a disposal, but it decides WHICH lots
# end up in the destination pool — and the broker's later sales of the
# destination relieve specific acquired-date lots.  fin's HIFO wrap order
# routinely moved a different lot mix than the broker's chain carried, so
# even with sale hints directing every disposal, the destination pool
# lacked the named acquired dates and the directed consume fell back to
# the method order (observed: offsetting multi-$k per-year realized drift
# between fin and the Coinbase report in convert-heavy years).
#
# The demand pool aggregates, per (account, symbol), every report
# disposal lot in sold-date order.  When a wrap fires, it takes demand
# entries with sold-date on/after the wrap date — up to the wrapped
# quantity — and directs its SOURCE consumption to those acquired dates
# (dates survive `_rescale_lots`, so the later directed sale then finds
# exactly those lots).  The pool is separate from the sale hints
# (`qty_left` budgets are independent) and each walk builds its own copy.

def build_wrap_demand(disposal_lots: dict | None) -> dict | None:
    """``{(account_group, symbol): [{sold, acquired, qty_left}, ...]}``
    sorted by sold date.  Entries without an acquired date (``Various``)
    are skipped — they can't direct anything."""
    if not disposal_lots:
        return None
    out: dict[tuple[str, str], list[dict]] = {}
    for (acct, sym, sold), hints in disposal_lots.items():
        for h in hints:
            acq = h.get("acquired") or ""
            q = float(h.get("qty", 0) or 0)
            if not acq or q <= 0:
                continue
            out.setdefault((acct, sym), []).append(
                {"sold": sold, "acquired": acq, "qty_left": q,
                 "per_unit": float(h.get("per_unit", 0) or 0)})
    for v in out.values():
        v.sort(key=lambda e: e["sold"])
    return out or None


def take_wrap_demand(demand: dict | None, account_group: str, dst_sym: str,
                     date: str, q_in: float, q_out: float,
                     until: str | None = None) -> list[dict] | None:
    """Consume up to ``q_in`` destination-units of future demand for
    ``dst_sym`` and return the equivalent SOURCE-unit hint list for
    ``_consume_lots_directed`` (``per_unit`` 0 disables the per-unit
    fallback — acquired-date matching only).

    ``sold >= date − 1 day`` mirrors the UTC-shift tolerance used by
    ``hints_for``.  ``until`` (exclusive) bounds the window at the NEXT
    wrap of the same destination: a lot sold after that wrap could have
    arrived via it, so this wrap must not grab its demand — without the
    bound, early wraps greedily drained the whole pool and the final
    wrap→sell run got nothing (observed: the last wrap fell back to
    HIFO and the directed sale missed the broker's 2021-dated lot).
    Mutates the shared pool so two wraps feeding the same sales split
    the demand instead of both moving it.
    """
    if not demand or q_in <= 0 or q_out <= 0:
        return None
    entries = demand.get((account_group, dst_sym))
    if not entries:
        return None
    base = _pdate(date)
    if base is None:
        return None
    from datetime import timedelta
    cutoff = (base - timedelta(days=1)).isoformat()
    ratio = q_out / q_in
    taken: list[dict] = []
    remaining = q_in
    for e in entries:
        if remaining <= 1e-12:
            break
        if until and e["sold"] >= until:
            break   # sorted by sold — everything after belongs to later wraps
        if e["sold"] < cutoff or e["qty_left"] <= 1e-12:
            continue
        take = min(e["qty_left"], remaining)
        e["qty_left"] -= take
        remaining -= take
        # per_unit converts dest-units → src-units (basis is invariant
        # across the wrap: pu_src = pu_dst × q_in/q_out).  Lets the
        # directed consume tie-break among same-date source lots.
        taken.append({"acquired": e["acquired"],
                      "qty_left": take * ratio,
                      "per_unit": float(e.get("per_unit", 0) or 0) / ratio
                                  if ratio > 0 else 0.0})
    return taken or None


def wrap_next_dates(wrap_groups: dict) -> dict:
    """``{(account, dst_sym, wrap_date): next_wrap_date | None}`` for the
    valid wrap groups — the ``until`` bound for ``take_wrap_demand``.
    Takes the ``_pair_wraps`` group dict (both walkers build it)."""
    by_dst: dict[tuple[str, str], list[str]] = {}
    for (acct, date, _kind), g in (wrap_groups or {}).items():
        if g.get("out") and g.get("in") and g.get("q_out", 0) > 0 and g.get("q_in", 0) > 0:
            by_dst.setdefault((acct, g["dst"]), []).append(date)
    out: dict = {}
    for (acct, dst), dates in by_dst.items():
        dates.sort()
        for i, d in enumerate(dates):
            out[(acct, dst, d)] = dates[i + 1] if i + 1 < len(dates) else None
    return out


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
    row_used = [False] * len(acq_rows)
    stamped = 0

    def _candidate(t, sym, rd):
        if id(t) in claimed or t.get("basis_override") is not None:
            return False
        if t.get("account_group") != _REPORT_ACCOUNT_GROUP:
            return False
        if t.get("symbol") != sym:
            return False
        act = t.get("action") or ""
        if act not in _LOT_ADD_ACTIONS and act != "Neutral":
            return False
        td = _pd(t.get("date", ""))
        return td is not None and abs((td - rd).days) <= 2

    # ----- Pass 1: exact 1:1 matches (row qty ≈ txn qty) -----------------
    for i, row in enumerate(acq_rows):
        rd = _pd(row["date"])
        if rd is None:
            continue
        pick = None
        for t in txns:
            if not _candidate(t, row["symbol"], rd):
                continue
            tq = float(t.get("quantity", 0) or 0)
            if abs(tq - row["qty"]) > max(1e-6, row["qty"] * 0.02):
                continue
            pick = t
            break
        if pick is None:
            continue
        claimed.add(id(pick))
        row_used[i] = True
        pick["basis_override"] = float(row["basis"])
        stamped += 1

    # ----- Pass 2: GROUP matches — several report rows sum to one txn ----
    # Coinbase's inventory keeps per-lot granularity (e.g. a 25-unit
    # arrival is several receive lots at different per-unit basis) while
    # fin records one transaction.  A blended single lot loses the
    # per-unit flavors that the report's later disposals name, which is
    # the root of the per-year realized timing drift.  When a subset of
    # unclaimed rows (same symbol, date ±2d) sums to a txn's quantity,
    # stamp the total AND attach ``basis_override_lots`` — a per-piece
    # breakdown both walkers push as separate lots.  Piece quantities
    # are normalized to sum EXACTLY to the txn quantity so the
    # balance↔lot-queue parity invariant holds.
    for t in txns:
        if id(t) in claimed or t.get("basis_override") is not None:
            continue
        if t.get("account_group") != _REPORT_ACCOUNT_GROUP:
            continue
        act = t.get("action") or ""
        if act not in _LOT_ADD_ACTIONS and act != "Neutral":
            continue
        td = _pd(t.get("date", ""))
        tq = float(t.get("quantity", 0) or 0)
        if td is None or tq <= 0:
            continue
        sym = t.get("symbol")
        cand = [i for i, row in enumerate(acq_rows)
                if not row_used[i] and row["symbol"] == sym
                and _pd(row["date"]) is not None
                and abs((_pd(row["date"]) - td).days) <= 2
                and row["qty"] > 0]
        if len(cand) < 2:
            continue
        tol = max(1e-6, tq * 0.02)
        subset = _subset_summing_to(
            [(i, acq_rows[i]["qty"]) for i in cand], tq, tol)
        if subset is None:
            continue
        pieces_qty = sum(acq_rows[i]["qty"] for i in subset)
        scale = tq / pieces_qty if pieces_qty > 0 else 1.0
        claimed.add(id(t))
        t["basis_override"] = float(sum(acq_rows[i]["basis"] for i in subset))
        t["basis_override_lots"] = [
            {"qty": acq_rows[i]["qty"] * scale,
             "basis": float(acq_rows[i]["basis"])}
            for i in subset
        ]
        for i in subset:
            row_used[i] = True
        stamped += 1

    unmatched = sum(1 for u in row_used if not u)
    return stamped, unmatched


def _subset_summing_to(items: list[tuple[int, float]], target: float,
                       tol: float, node_cap: int = 50000) -> list[int] | None:
    """Find a subset of ``items`` (id, qty) whose qtys sum to
    ``target ± tol``.  Prefers the FULL set (the common case: every
    unclaimed report row on the date belongs to the one fin arrival),
    then backtracks largest-first with a sum bound.  ``node_cap``
    bounds the search so a pathological row set can't hang the
    pipeline; returns None when nothing fits."""
    total = sum(q for _, q in items)
    if abs(total - target) <= tol:
        return [i for i, _ in items]
    ordered = sorted(items, key=lambda x: -x[1])
    n = len(ordered)
    nodes = 0

    def _walk(idx: int, acc: float, chosen: list[int]) -> list[int] | None:
        nonlocal nodes
        nodes += 1
        if nodes > node_cap:
            return None
        if abs(acc - target) <= tol and chosen:
            return list(chosen)
        if idx >= n or acc - target > tol:
            return None
        # Remaining mass can't reach the target → prune.
        if acc + sum(q for _, q in ordered[idx:]) < target - tol:
            return None
        i, q = ordered[idx]
        chosen.append(i)
        got = _walk(idx + 1, acc + q, chosen)
        if got is not None:
            return got
        chosen.pop()
        return _walk(idx + 1, acc, chosen)

    return _walk(0, 0.0, [])
