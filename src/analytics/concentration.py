"""Concentration risk: how much of the portfolio is in any one
position / sector / account / type.

Outputs:
- ``positions``:  per-symbol weight as % of total, with risk flags
- ``sectors``:    per-sector weight
- ``account_groups`` / ``account_types``:  same for those breakdowns
- ``herfindahl``: HHI index (sum of squared weights × 10000); 10000 =
  one position, 100 = perfect equal weighting across 100 holdings
- ``top_5_concentration``:  share of total in top 5 positions
- ``flags``: list of warnings (single position > 25%, top-5 > 70%, etc)
"""

from __future__ import annotations

from collections import defaultdict


# Threshold tiers — purely advisory.  These come from rough rules of
# thumb common in retail portfolio guidance; the user can ignore.
_FLAG_SINGLE_POSITION_PCT  = 20.0   # >20% in one ticker = flag
_FLAG_TOP_5_PCT             = 70.0   # >70% in top 5 = flag
_FLAG_SECTOR_PCT            = 35.0   # >35% in one sector = flag


def compute_concentration(holdings_by_account: list[dict]) -> dict:
    if not holdings_by_account:
        return {"positions": [], "sectors": [], "account_groups": [],
                "account_types": [], "herfindahl": 0,
                "top_5_concentration": 0, "flags": []}

    # Aggregate value by symbol / sector / account_group / account_type.
    # Skip positions without a known value (unpriced exotic holdings).
    #
    # By Sector specifically EXCLUDES Cash — concentration risk is
    # about the equity side of the portfolio.  Cash isn't a sector
    # "risk" the way Technology or Energy are; it's the absence of
    # market exposure.  Including it makes the percentages look more
    # diversified than the equity portfolio actually is, and makes the
    # 35% sector-concentration flag too lenient.  Account/type
    # breakdowns still include cash since custodial/tax-treatment
    # concentration is a different question.
    by_sym:  dict[str, dict] = {}
    by_sec:  dict[str, float] = defaultdict(float)
    by_grp:  dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    sector_total = 0.0   # excludes Cash — denominator for By Sector
    total = 0.0
    for h in holdings_by_account:
        v = h.get("value")
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        total += v
        sym = h.get("symbol") or ""
        sector = h.get("sector") or "Other"
        if sym not in by_sym:
            by_sym[sym] = {"symbol": sym, "value": 0.0, "sector": sector}
        by_sym[sym]["value"] += v
        if sector != "Cash":
            by_sec[sector] += v
            sector_total += v
        by_grp[h.get("account_group") or ""] += v
        by_type[h.get("account_type") or ""] += v

    if total <= 0:
        return {"positions": [], "sectors": [], "account_groups": [],
                "account_types": [], "herfindahl": 0,
                "top_5_concentration": 0, "flags": []}

    def _entries(d, key_field, denom):
        out = []
        for k, v in d.items():
            if isinstance(v, dict):
                pct = (v["value"] / denom) * 100 if denom > 0 else 0
                out.append({key_field: v["symbol"],
                            "value": round(v["value"], 2),
                            "pct":   round(pct, 2),
                            "sector": v.get("sector")})
            else:
                pct = (v / denom) * 100 if denom > 0 else 0
                out.append({key_field: k,
                            "value": round(v, 2),
                            "pct":   round(pct, 2)})
        out.sort(key=lambda r: r["pct"], reverse=True)
        return out

    positions      = _entries(by_sym,  "symbol",        total)
    # Sectors are percent of equity (excludes Cash) — see denominator
    # rationale in the comment above.  If the user holds 100% cash,
    # sector_total = 0 and the list is empty.
    sectors        = _entries(by_sec,  "sector",        sector_total)
    account_groups = _entries(by_grp,  "account_group", total)
    account_types  = _entries(by_type, "account_type",  total)

    # Herfindahl-Hirschman Index — sum of squared weights × 10000.
    # 10000 = single holding, → 0 = perfectly diversified.
    hhi = sum((p["pct"] / 100) ** 2 for p in positions) * 10000

    # Top-5 concentration (or top-N if < 5 positions)
    top_n = positions[:5]
    top_5_pct = sum(p["pct"] for p in top_n)

    flags = []
    biggest = positions[0] if positions else None
    if biggest and biggest["pct"] > _FLAG_SINGLE_POSITION_PCT:
        flags.append({
            "kind": "single_position",
            "severity": "warn" if biggest["pct"] < 35 else "high",
            "message": (f"{biggest['symbol']} is {biggest['pct']:.1f}% of "
                        f"the portfolio — concentration risk."),
        })
    if top_5_pct > _FLAG_TOP_5_PCT:
        flags.append({
            "kind": "top_5",
            "severity": "warn",
            "message": (f"Top 5 holdings are {top_5_pct:.1f}% of total — "
                        f"limited diversification."),
        })
    biggest_sec = sectors[0] if sectors else None
    if biggest_sec and biggest_sec["pct"] > _FLAG_SECTOR_PCT:
        flags.append({
            "kind": "sector",
            "severity": "warn",
            "message": (f"{biggest_sec['sector']} sector is "
                        f"{biggest_sec['pct']:.1f}% of total."),
        })

    return {
        "positions":           positions,
        "sectors":             sectors,
        "account_groups":      account_groups,
        "account_types":       account_types,
        "herfindahl":          round(hhi, 1),
        "top_5_concentration": round(top_5_pct, 2),
        "flags":               flags,
        "total":               round(total, 2),
        # Equity-only denominator used for the By Sector percentages
        # — the dashboard labels this so the user knows the % is of
        # invested capital, not total portfolio.
        "sector_total":        round(sector_total, 2),
        "cash_excluded_from_sectors": round(total - sector_total, 2),
    }
