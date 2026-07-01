"""Portfolio value time series.

Walks the transaction log chronologically, maintains running balances per
`(account_group, symbol)`, and at each sample date looks up split-adjusted
prices from the cache to compute portfolio value broken down by account
group, account type, and sector.

Default cadence is monthly end-of-month snapshots plus today.  Daily data
is available in the price cache if you want to switch cadence — the JSON
stays small either way.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from .basis import (
    BASIS_EFFECTS, _basis_dollars, _consume_lots, _pair_transfers,
    _pair_wraps, _rescale_lots, _sort_key as _basis_sort_key,
)
from .config import ACCOUNT_TYPES, CASH_SYMBOLS
from .prices import get_price, split_factor_since

# Sourced from src/actions.py — single source of truth for the action
# vocabulary, so any new canonical action lands here automatically.
from .actions import SUBTRACT_ACTIONS as _SUBTRACT_ACTIONS
from .actions import NEUTRAL_ACTIONS as _NEUTRAL_ACTIONS


def _basis_effect_for_sym(sym: str, action: str) -> str:
    """Same classification as basis.py, inlined to avoid the pairing
    machinery (history walks forward once with main.py-style sort).
    """
    if not sym or sym == "USD":
        return "ignore"
    return BASIS_EFFECTS.get(action, "unknown")


def _sample_dates(first: str, last: str, cadence: str) -> list[str]:
    """Return the list of sample dates in [first, last].

    `cadence`:
      - "month" → last calendar day of each month, plus `last`
      - "week"  → every 7 days from `first`, plus `last`
      - "day"   → every day (can be large; use sparingly)
    """
    start = datetime.strptime(first, "%Y-%m-%d").date()
    end   = datetime.strptime(last,  "%Y-%m-%d").date()
    out: list[str] = []
    if cadence == "month":
        cur = date(start.year, start.month, 1)
        while cur <= end:
            if cur.month == 12:
                eom = date(cur.year, 12, 31)
            else:
                eom = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
            if start <= eom <= end:
                out.append(eom.isoformat())
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
    elif cadence == "week":
        cur = start
        while cur <= end:
            out.append(cur.isoformat())
            cur += timedelta(days=7)
    elif cadence == "day":
        cur = start
        while cur <= end:
            out.append(cur.isoformat())
            cur += timedelta(days=1)
    else:
        raise ValueError(f"unknown cadence: {cadence}")
    last_s = end.isoformat()
    if not out or out[-1] != last_s:
        out.append(last_s)
    return out


from .basis import txn_external_cash_flow


def _compute_net_contributed_series(txns: list[dict], samples: list[str]) -> dict[str, float]:
    """Running cumulative net contributions at each sample date.

    Apples-to-apples with the benchmark line (both track external money
    flows only).  ``txn_external_cash_flow`` is the single source of
    truth for the cash-flow rule — handles Contribution Reversal and
    the Roth/Rollover-IRA Distribution carve-out consistently with
    basis.compute_cash_summary and analytics._shared.net_cash_flow.
    """
    flows: list[tuple[str, float]] = []
    for t in txns:
        if not t.get("date"):
            continue
        signed = txn_external_cash_flow(t)
        if signed != 0:
            flows.append((t["date"], signed))
    flows.sort(key=lambda f: f[0])

    out: dict[str, float] = {}
    running = 0.0
    idx = 0
    for sample_date in samples:
        while idx < len(flows) and flows[idx][0] <= sample_date:
            running += flows[idx][1]
            idx += 1
        out[sample_date] = round(running, 2)
    return out


def _compute_benchmark_series(txns: list[dict], samples: list[str],
                              benchmark_symbol: str = "SPY") -> dict[str, float]:
    """Simulate a hypothetical portfolio that bought/sold SPY at every
    net contribution event (using the day's closing price).  Returns
    `{sample_date: hypothetical_value}`.

    This answers "what would my portfolio be worth if I'd invested all my
    contributions in SPY instead".  Withdrawals reduce the SPY share
    count proportionally.  If SPY has no price for a given contribution
    date (shouldn't happen for trading-day dates), that cash flow is
    skipped from the benchmark.
    """
    # Build chronological cash flows.  Uses txn_external_cash_flow so
    # the SPY-equivalent series respects the same Roth/Rollover skip
    # rule as net_contributed — otherwise the benchmark would buy SPY
    # against a phantom $17k withdrawal during a Roth conversion.
    flows: list[tuple[str, float]] = []   # (date, signed_dollars)
    for t in txns:
        if not t.get("date"):
            continue
        signed = txn_external_cash_flow(t)
        if signed != 0:
            flows.append((t["date"], signed))
    flows.sort(key=lambda f: f[0])

    out: dict[str, float] = {}
    shares = 0.0
    flow_idx = 0
    for sample_date in samples:
        # Apply all flows up through sample_date
        while flow_idx < len(flows) and flows[flow_idx][0] <= sample_date:
            d, dollars = flows[flow_idx]
            flow_idx += 1
            px = get_price(benchmark_symbol, d)
            if not px or px <= 0:
                continue
            # Buying fractional shares with the full cash amount;
            # withdrawal sells proportionally.
            shares += dollars / px
            if shares < 0:
                shares = 0.0
        sample_px = get_price(benchmark_symbol, sample_date)
        out[sample_date] = round(shares * sample_px, 2) if (sample_px and shares > 0) else 0.0
    return out


def compute_history(txns: list[dict],
                    sector_of: dict[str, str],
                    *,
                    cadence: str = "month",
                    account_methods: dict[str, str] | None = None) -> list[dict]:
    """Build the portfolio value time series.

    Each snapshot is::

        {
          "date":                "YYYY-MM-DD",
          "total":               float,
          "by_account_group":    {"Robinhood": float, ...},
          "by_account_type":     {"Taxable": float, ...},
          "by_sector":            {"Technology": float, ...},
          "total_cost_basis":    float,
          "cost_basis_by_group": {...},
          "cost_basis_by_type":  {...},
          "priced_pct":          float,   # fraction of balance that had a price
          "benchmark_spy":       float,   # hypothetical SPY-only portfolio value
          "benchmark_spy_price": float,   # raw SPY close price on this date
          "net_contributed":     float,   # cumulative external cash in
          "positions":           [         # per-(account_group, symbol) rollup
            {"account_group": str, "symbol": str,
             "quantity": float, "price": float|None,
             "value": float|None, "cost_basis": float},
            ...
          ],
        }

    `sector_of` is `{symbol: sector}` sourced from the `sectors` module so
    sector breakdowns align with the holdings table.  The `positions`
    list enables arbitrary-date holdings filtering in the dashboard (the
    FIFO state at every snapshot is captured, so no JS replay is
    needed).  Dust filter mirrors main.py so position counts match the
    holdings table exactly.

    ``account_methods`` optionally overrides the lot-relief method per
    account_group (same contract as ``basis.compute_basis_default``) so
    the snapshot lot walker consumes lots in the same order the
    annotated basis walk does — required for the latest snapshot's
    per-position cost basis to match the holdings table when any
    account uses a non-FIFO method (e.g. Coinbase on HIFO).

    INVARIANT: the lot rules here must mirror ``basis._walk`` —
    wrap/unwrap carries basis, unpaired transfer-ins get FMV basis,
    ``basis_override`` wins on lot-creating branches.  The
    ``history_holdings_basis_parity`` data-health check pins the two
    walkers together; if you change a rule in basis.py, change it here.
    """
    if not txns:
        return []
    dates = [t.get("date", "") for t in txns if t.get("date")]
    if not dates:
        return []

    first = min(dates)
    today = datetime.now().date().isoformat()
    last  = max(max(dates), today)
    samples = _sample_dates(first, last, cadence)

    # Benchmarks: hypothetical portfolios that bought each tracker at
    # every contribution event.  SPY (US large-cap, total return) is
    # the default; BND (US aggregate bond) and VXUS (international ex-
    # US) round out a common 3-fund comparison; "60/40" is a blended
    # 60% SPY + 40% BND that approximates a classic balanced portfolio.
    benchmark_series   = _compute_benchmark_series(txns, samples, "SPY")
    benchmark_bnd      = _compute_benchmark_series(txns, samples, "BND")
    benchmark_vxus     = _compute_benchmark_series(txns, samples, "VXUS")
    # 60/40 = run two separate sims, blend per snapshot.  Doing a
    # rebalance simulation here would be overkill; this static-share
    # blend approximates the realized total return well enough for a
    # comparison line on the chart.
    benchmark_60_40 = {d: round(0.6 * benchmark_series.get(d, 0)
                                + 0.4 * benchmark_bnd.get(d, 0), 2)
                       for d in samples}
    # Running net contributions — cumulative external cash put in at
    # each snapshot.  Apples-to-apples with the SPY benchmark.
    net_contrib_series = _compute_net_contributed_series(txns, samples)

    # Coinbase implicit USD bridge.  For Coinbase-only (where the
    # complete-data property holds after the GDAX-deprecation marker
    # fix and cumulative-min synth), we know the implicit USD wallet
    # balance at every date.  Adding this to snapshot value
    # eliminates the spurious "drop to zero" mid-period when the user
    # sells crypto and the next Buy / Withdrawal is on a different
    # snapshot.  See ``coinbase.usd_series`` for the full rationale.
    from .coinbase_reconcile import usd_series as _cb_usd_series
    _cb_usd_eod = _cb_usd_series(txns)
    _cb_usd_dates = [d for d, _ in _cb_usd_eod]
    _cb_usd_balances = [b for _, b in _cb_usd_eod]

    def _coinbase_usd_at(sample_date: str) -> float:
        """Implicit Coinbase USD balance at end-of-day on sample_date.

        Walk-back lookup: the most recent end-of-day balance with
        date <= sample_date.  Returns 0.0 before any Coinbase
        activity.  Negative results clamp to 0 — the Coinbase
        reconciliation guarantees the natural flow ends ≈ $0, but
        intermediate dates can show transient deficits when same-day
        Sells haven't been counted yet (the cumulative-min approach
        in _reconcile_coinbase_external_funding accepts these as
        recycled-cash transients, not real external funding gaps).
        """
        # Linear search; len(_cb_usd_dates) ~= a few thousand at most
        # and we lookup only ~108 sample dates.  Binary search would
        # micro-optimize; not worth the dependency on bisect for the
        # readability tradeoff.
        bal = 0.0
        for d, b in zip(_cb_usd_dates, _cb_usd_balances):
            if d <= sample_date:
                bal = b
            else:
                break
        return max(bal, 0.0)

    # Pre-pair cross-account transfers exactly like basis.py so basis
    # carries from source to destination on paired transfers, and
    # intra-group pairs (same (account_group, symbol) on both legs) stay
    # no-ops in the lot queue.  Without this, the latest history snapshot
    # would under-count total basis vs. main.py's FIFO holdings total.
    pairings = _pair_transfers(txns)
    tin_to_tout  = pairings["tin_to_tout"]
    intra_group  = pairings["intra_group"]
    paired_touts = pairings["paired_touts"]
    stashed_tout_lots: dict[int, list[dict]] = {}
    tout_handled: set[int] = set()

    # Wrap/unwrap groups — basis-carrying conversions processed
    # atomically the first time any leg is met, exactly like basis.py.
    wrap_groups = _pair_wraps(txns)
    wrap_done: set[tuple] = set()

    def _method_for(acct: str) -> str:
        if not account_methods:
            return "fifo"
        m = account_methods.get(acct, "fifo")
        return m if m in ("fifo", "lifo", "hifo") else "fifo"

    # Same sort as basis.py: within (date, account, symbol), adds before
    # subtracts.  Required for correct same-day FIFO matching.
    txns_sorted = sorted(txns, key=_basis_sort_key)
    balances: dict[tuple[str, str], float] = defaultdict(float)
    lots: dict[tuple[str, str], list[dict]] = defaultdict(list)
    # Fallback price basis per symbol — last non-zero `price` observed in a
    # transaction at or before the current sample date.  Used when the price
    # cache can't resolve a symbol (multi-word fund display names, delisted
    # tickers, etc.).  This mirrors what main.py's holdings computation does
    # for "now" and lets history stay in lockstep with the holdings total.
    last_txn_price: dict[str, float] = {}
    idx = 0

    def _consume(key, qty_to_remove):
        """Remove qty_to_remove from lots[key] using the owning account's
        lot-relief method; return (basis_removed, carried).  Delegates to
        basis._consume_lots so consume order (FIFO/LIFO/HIFO) matches
        the annotated basis walk exactly."""
        return _consume_lots(lots[key], qty_to_remove, _method_for(key[0]))

    def _push(key, qty_add, basis_dollars, date):
        if qty_add > 0:
            lots[key].append({
                "date": date,
                "qty": qty_add,
                "basis_per_share": basis_dollars / qty_add,
            })

    history: list[dict] = []
    for sample_date in samples:
        # Advance balances (and lot queues) up through sample_date.
        while idx < len(txns_sorted) and txns_sorted[idx].get("date", "") <= sample_date:
            t = txns_sorted[idx]
            idx += 1
            acct = t.get("account_group", "")
            sym  = t.get("symbol", "")
            p    = float(t.get("price", 0) or 0)
            if sym and p > 0:
                last_txn_price[sym] = p
            action = t.get("action", "")
            qty = float(t.get("quantity", 0) or 0)

            # Balance walk (skips USD outside Savings, matches main.py)
            if not (sym in CASH_SYMBOLS and ACCOUNT_TYPES.get(acct) != "Savings"):
                if action not in _NEUTRAL_ACTIONS:
                    if action in _SUBTRACT_ACTIONS:
                        balances[(acct, sym)] -= qty
                    else:
                        balances[(acct, sym)] += qty

            # Intra-group transfers net to zero in the same lot queue.
            if id(t) in intra_group:
                continue

            # Cost-basis walk — rules mirror basis._walk (see docstring
            # invariant).  A user-supplied basis_override (metadata `Cost
            # Basis` row, stamped by cost_basis_overrides.match_and_stamp)
            # wins on the lot-creating branches, same as basis.py's _ov.
            effect = _basis_effect_for_sym(sym, action)
            key = (acct, sym)
            if effect == "add":
                bo = t.get("basis_override")
                dollars = float(bo) if bo is not None else _basis_dollars(t)
                _push(key, qty, dollars, t.get("date", ""))
            elif effect == "zero_basis":
                # FMV-at-receipt when the broker recorded a price, else $0.
                _push(key, qty, qty * p if p > 0 else 0.0, t.get("date", ""))
            elif effect == "remove":
                _consume(key, qty)
            elif effect == "transfer_out":
                if id(t) in tout_handled:
                    pass  # already moved eagerly by same-day paired TIN
                else:
                    _basis, carried = _consume(key, qty)
                    if id(t) in paired_touts:
                        stashed_tout_lots[id(t)] = carried
                    # Unpaired TOUT (transferred out to somewhere not in the
                    # dataset) — basis is lost; that's accurate.
            elif effect == "transfer_in":
                paired = tin_to_tout.get(id(t))
                if paired is None:
                    # External arrival with no visible origin leg — FMV at
                    # the transfer date (matches basis.py), override wins.
                    bo = t.get("basis_override")
                    basis = (float(bo) if bo is not None
                             else (qty * p if p > 0 else 0.0))
                    _push(key, qty, basis, t.get("date", ""))
                elif id(paired) in stashed_tout_lots:
                    for lot in stashed_tout_lots.pop(id(paired)):
                        lots[key].append(dict(lot))
                else:
                    src_key = (paired.get("account_group", ""), paired.get("symbol", ""))
                    _basis, carried = _consume(src_key, float(paired.get("quantity", 0) or 0))
                    for lot in carried:
                        lots[key].append(dict(lot))
                    tout_handled.add(id(paired))
            elif effect in ("wrap_out", "wrap_in"):
                # Basis-carrying conversion (ETH↔CBETH).  Process the whole
                # (account, date, kind) group atomically the first time any
                # leg walks — consume source lots (no gain), carry total
                # basis rescaled to the destination qty.  Mirrors basis.py.
                kind = "unwrap" if "Unwrap" in (action or "") else "wrap"
                gkey = (acct, t.get("date", "") or "", kind)
                if gkey not in wrap_done:
                    wrap_done.add(gkey)
                    g = wrap_groups.get(gkey)
                    if g and g["out"] and g["in"] and g["q_out"] > 0 and g["q_in"] > 0:
                        _b, carried = _consume((acct, g["src"]), g["q_out"])
                        for lot in _rescale_lots(carried, g["q_in"]):
                            lots[(acct, g["dst"])].append(lot)
                    elif g:
                        # Lone / malformed group — per-leg fallback so basis
                        # isn't silently lost (bare out = sale, bare in =
                        # FMV buy), same as basis.py.
                        for ol in g["out"]:
                            oq = float(ol.get("quantity", 0) or 0)
                            _consume((acct, ol.get("symbol", "")), oq)
                        for il in g["in"]:
                            iq = float(il.get("quantity", 0) or 0)
                            px = float(il.get("price", 0) or 0)
                            bo = il.get("basis_override")
                            b = (float(bo) if bo is not None
                                 else (iq * px if px > 0 else 0.0))
                            _push((acct, il.get("symbol", "")), iq, b,
                                  il.get("date", ""))
            elif effect == "split":
                lq = lots[key]
                old_total = sum(lot["qty"] for lot in lq)
                if old_total > 0 and qty > 0:
                    ratio = (old_total + qty) / old_total
                    for lot in lq:
                        lot["qty"] *= ratio
                        lot["basis_per_share"] /= ratio
            # else: ignore / unknown → no-op

        by_group:       dict[str, float] = defaultdict(float)
        by_type:        dict[str, float] = defaultdict(float)
        by_sector:      dict[str, float] = defaultdict(float)
        basis_group:    dict[str, float] = defaultdict(float)
        basis_type:     dict[str, float] = defaultdict(float)
        positions:      list[dict] = []
        total = 0.0
        total_basis = 0.0
        priced = 0.0
        attempted = 0.0
        for (acct, sym), qty in balances.items():
            # Resolve price + today-basis qty for valuation
            if sym in CASH_SYMBOLS:
                price = 1.0
                adj_qty = qty
            else:
                cache_px = get_price(sym, sample_date)
                if cache_px is not None:
                    # yfinance's price is in today's share basis (split-adjusted).
                    # Scale the as-of-date balance to match.  For assets with no
                    # splits after sample_date the factor is 1.0 (the common case).
                    price = cache_px
                    adj_qty = qty * split_factor_since(sym, sample_date)
                else:
                    # No cache price — fall back to the most recent txn price
                    # at or before sample_date.  Txn prices are as-of-trade,
                    # so no split adjustment is needed on the balance side.
                    price = last_txn_price.get(sym)
                    adj_qty = qty

            # Dust filter — mirrors main.py _is_dust so history positions
            # exactly match the holdings table (no phantom sub-penny rows).
            if price and price > 0:
                if abs(qty * price) < 0.01:
                    continue
            else:
                if qty < 0 or abs(qty) < 1e-6:
                    continue

            # Cost basis for this position at this date
            if sym in CASH_SYMBOLS:
                pos_basis = qty  # cash "basis" = face value
            else:
                pos_basis = sum(lot["qty"] * lot["basis_per_share"]
                                for lot in lots.get((acct, sym), []))

            val = (adj_qty * price) if (price is not None and price > 0) else None

            attempted += abs(qty)
            if val is not None:
                priced += abs(qty)
                total += val
                by_group[acct] += val
                by_type[ACCOUNT_TYPES.get(acct, "Taxable")] += val
                by_sector[sector_of.get(sym, "Other")] += val

            total_basis += pos_basis
            basis_group[acct] += pos_basis
            basis_type[ACCOUNT_TYPES.get(acct, "Taxable")] += pos_basis

            # Per-position snapshot row — shape matches holdings_by_account
            # so the dashboard can filter by as-of-date the same way it
            # filters the current holdings table.  `price` here is the
            # effective historical per-share price (value / quantity), so
            # it's pre-split and matches the raw quantity — not the
            # yfinance split-adjusted price.
            eff_price = (val / qty) if (val is not None and qty) else None
            positions.append({
                "account_group": acct,
                "symbol":        sym,
                "quantity":      round(qty, 8),
                "price":         round(eff_price, 4) if eff_price is not None else None,
                "value":         round(val, 2) if val is not None else None,
                "cost_basis":    round(pos_basis, 2),
            })

        # Coinbase implicit USD bridge — the running USD wallet balance
        # for Coinbase at this snapshot date.  See _coinbase_usd_at
        # docstring above for the full motivation.  Adds 1:1 to value,
        # basis, sector "Cash", and creates a synthetic USD position so
        # the dashboard's as-of-date holdings filter shows the cash
        # bridge during sell→buy gaps.
        #
        # Threshold is $1 (looser than main.py's $0.01 dust rule)
        # because the running balance accumulates floating-point drift
        # across thousands of txns — the natural flow ends at ~$0.01
        # rather than exactly $0 even when the user's actual balance
        # is $0.  Sub-dollar bridge values are noise, not real cash.
        cb_usd = round(_coinbase_usd_at(sample_date), 2)
        if cb_usd >= 1.0:
            total += cb_usd
            by_group["Coinbase"] += cb_usd
            by_type[ACCOUNT_TYPES.get("Coinbase", "Taxable")] += cb_usd
            by_sector["Cash"] += cb_usd
            total_basis += cb_usd
            basis_group["Coinbase"] += cb_usd
            basis_type[ACCOUNT_TYPES.get("Coinbase", "Taxable")] += cb_usd
            positions.append({
                "account_group": "Coinbase",
                "symbol":        "USD",
                "quantity":      cb_usd,
                "price":         1.0,
                "value":         cb_usd,
                "cost_basis":    cb_usd,
            })

        history.append({
            "date":                  sample_date,
            "total":                 round(total, 2),
            "by_account_group":      {k: round(v, 2) for k, v in by_group.items()},
            "by_account_type":       {k: round(v, 2) for k, v in by_type.items()},
            "by_sector":             {k: round(v, 2) for k, v in by_sector.items()},
            "total_cost_basis":      round(total_basis, 2),
            "cost_basis_by_group":   {k: round(v, 2) for k, v in basis_group.items()},
            "cost_basis_by_type":    {k: round(v, 2) for k, v in basis_type.items()},
            "priced_pct":            round(priced / attempted, 4) if attempted else 1.0,
            "benchmark_spy":         benchmark_series.get(sample_date, 0.0),
            "benchmark_bnd":         benchmark_bnd.get(sample_date, 0.0),
            "benchmark_vxus":        benchmark_vxus.get(sample_date, 0.0),
            "benchmark_60_40":       benchmark_60_40.get(sample_date, 0.0),
            "net_contributed":       net_contrib_series.get(sample_date, 0.0),
            # Raw close prices for benchmark tickers at this snapshot
            # date.  The dashboard uses these for filter-aware
            # benchmark overlays (simulating "what if I'd put my
            # filtered subset's contributions into SPY/BND/VXUS"),
            # which requires per-event price lookup.  The simulated
            # ``benchmark_*`` dollar series above are lifetime-only.
            "benchmark_spy_price":   (lambda p: round(p, 2) if p else None)(get_price("SPY", sample_date)),
            "benchmark_bnd_price":   (lambda p: round(p, 4) if p else None)(get_price("BND", sample_date)),
            "benchmark_vxus_price":  (lambda p: round(p, 4) if p else None)(get_price("VXUS", sample_date)),
            # Per-position rollup — one entry per non-dust
            # (account_group, symbol) with quantity / price / value /
            # cost_basis as of sample_date.  Enables arbitrary-date
            # holdings filtering in the dashboard without recomputing
            # the FIFO walker in JS.
            "positions":             positions,
        })

    return history
