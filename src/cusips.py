"""CUSIP-based identity resolution.

Tickers are unstable identifiers — they get reused after delistings,
relabeled retroactively after corporate actions, and don't disambiguate
between similarly-named instruments (BRK.B vs BRK-B, RNA vs RNAM).
CUSIPs are stable per security across the security's lifetime.

Robinhood embeds CUSIPs in the Description field for most rows in the
form ``"...\\nCUSIP: 05370A108"``.  This module extracts them and
provides utilities to:

- Parse a CUSIP out of a description string.
- Detect ticker collisions (symbol→CUSIP mappings where one ticker
  spans multiple CUSIPs, or one CUSIP spans multiple tickers).
- Suggest rename rules from CUSIP-stable groupings.

The pipeline records ``cusip`` on each txn when available, but the
math currently still keys on ``symbol`` — CUSIP is supplementary.
The ``suggest_renames`` utility helps populate
``cache/ticker_renames.json`` semi-automatically from observed CUSIP
collisions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


# CUSIPs are 9-character alphanumeric codes (8 chars + check digit).
# Real-world variants we see: standard 9-char (e.g. "05370A108"), and
# Robinhood's preferred-share suffix patterns ("009CVR044" — 9 chars
# but check-digit position is non-standard).  Be permissive: 8-9
# alphanumeric chars after "CUSIP:".
_CUSIP_RE = re.compile(r"\bCUSIP:\s*([A-Z0-9]{8,9})\b", re.IGNORECASE)


def extract_cusip(description: str) -> str | None:
    """Pull a CUSIP out of a Robinhood-style description.  Returns
    None if the field doesn't carry one.
    """
    if not description:
        return None
    m = _CUSIP_RE.search(description)
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------------------
# CUSIP / ticker collision diagnostics
# ---------------------------------------------------------------------------

def cusip_to_tickers(txns: Iterable[dict]) -> dict[str, set[str]]:
    """For each CUSIP observed in the txn stream, return the set of
    tickers that have referred to it.  CUSIPs with >1 ticker indicate
    a ticker rename (RNA→RNAM after the Avidity acquisition) or a
    format alias (BRK.B→BRK-B).
    """
    out: dict[str, set[str]] = defaultdict(set)
    for t in txns:
        cusip = t.get("cusip")
        sym = t.get("symbol")
        if cusip and sym:
            out[cusip].add(sym)
    return dict(out)


def ticker_to_cusips(txns: Iterable[dict]) -> dict[str, set[str]]:
    """Inverse map: ticker → CUSIPs seen under that ticker.  Tickers
    with >1 CUSIP indicate **ticker reuse** — the same string referring
    to different securities (Atrium Therapeutics taking over RNA from
    Avidity is the canonical case).
    """
    out: dict[str, set[str]] = defaultdict(set)
    for t in txns:
        cusip = t.get("cusip")
        sym = t.get("symbol")
        if cusip and sym:
            out[sym].add(cusip)
    return dict(out)


def detect_collisions(txns: list[dict]) -> dict:
    """Run both diagnostics and return a structured report:

    ``{
       "rename_candidates": [{cusip, tickers, last_seen}, ...],
            # CUSIPs with >1 ticker → likely retroactive renames
       "reuse_candidates": [{ticker, cusips, ...}, ...],
            # Tickers with >1 CUSIP → ticker-string reused for different
            # securities (Atrium took over RNA after Avidity's exit)
    }``
    """
    c2t = cusip_to_tickers(txns)
    t2c = ticker_to_cusips(txns)

    # Last-seen date per (cusip, ticker) pair — used to suggest the
    # canonical (most recent) ticker for each CUSIP.
    last_seen: dict[tuple[str, str], str] = {}
    for t in txns:
        cusip = t.get("cusip")
        sym = t.get("symbol")
        date = t.get("date", "")
        if not (cusip and sym and date):
            continue
        key = (cusip, sym)
        if key not in last_seen or date > last_seen[key]:
            last_seen[key] = date

    rename_candidates = []
    for cusip, tickers in c2t.items():
        if len(tickers) <= 1:
            continue
        # Pick the ticker with the latest last-seen date as canonical
        ranked = sorted(
            tickers,
            key=lambda s: last_seen.get((cusip, s), ""),
            reverse=True,
        )
        rename_candidates.append({
            "cusip": cusip,
            "tickers": list(tickers),
            "canonical": ranked[0],
            "stale": ranked[1:],
            "last_seen": {s: last_seen.get((cusip, s), "") for s in tickers},
        })

    reuse_candidates = []
    for ticker, cusips in t2c.items():
        if len(cusips) <= 1:
            continue
        reuse_candidates.append({
            "ticker": ticker,
            "cusips": list(cusips),
        })

    return {
        "rename_candidates": rename_candidates,
        "reuse_candidates": reuse_candidates,
    }


def suggest_rename_rules(report: dict) -> list[dict]:
    """Convert ``detect_collisions`` output into draft entries for
    ``cache/ticker_renames.json``.  The user reviews + commits.

    Each suggestion is::

        {"from": stale_ticker, "to": canonical, "before_date": ...,
         "note": "auto-suggested from CUSIP collision; review before committing"}
    """
    suggestions = []
    for r in report.get("rename_candidates", []):
        canon = r["canonical"]
        for stale in r["stale"]:
            stale_last = r["last_seen"].get(stale, "")
            canon_first = r["last_seen"].get(canon, "")
            suggestions.append({
                "from": stale,
                "to": canon,
                "before_date": canon_first or stale_last,
                "note": (f"auto-suggested from CUSIP {r['cusip']} collision: "
                         f"{stale} (last seen {stale_last}) shares CUSIP with "
                         f"{canon}; review before committing"),
            })
    return suggestions
