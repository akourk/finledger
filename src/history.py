"""Portfolio value time series.

Walks the transaction log chronologically, maintains running balances per
`(account_group, symbol)`, and at each sample date looks up split-adjusted
prices from the cache to compute portfolio value broken down by account
group, account type, and sector.

Default cadence is semimonthly (the 15th + last day of each month) plus
today.  EOM dates match the older monthly cadence exactly, so
latest-in-month consumers see the same month boundaries; the mid-month
samples add chart / drawdown / TWR resolution.  Daily data is available
in the price cache if you want to switch cadence — sampling density and
fetch cost are decoupled (the cache stores every trading day either way).
"""

from . import clock
from collections import defaultdict
from datetime import date, datetime, timedelta

from .basis import (
    BASIS_EFFECTS, _apply_split_to_lots, _basis_dollars, _basis_effect,
    basis_effect_for, reserved_for, wrap_carry_lots, wrap_kind,
    zero_basis_origin,
    _consume_for_rebase, _consume_lots, _consume_lots_directed,
    _consume_lots_reserving, _pair_transfers, _pair_wraps, _rebase_is_move,
    _push_txn_lots,
    _sort_key as _basis_sort_key,
    apply_roc_to_lots, basis_override_or, fmv_basis, pair_roc_events,
)
from .broker_lots import (build_wrap_demand, copy_disposal_lots, hints_for,
                          wrap_next_dates, wrap_symbol_families)
from .config import ACCOUNT_TYPES, CASH_SYMBOLS
from .pipeline_stages import cash_principal_effect
from .prices import get_price
from .valuation import QTY_EPSILON, mark, mark_is_dust
from .return_flows import eligible_account_transfer_pairs, transit_positions

# Sourced from src/actions.py — single source of truth for the action
# vocabulary, so any new canonical action lands here automatically.
from .actions import SUBTRACT_ACTIONS as _SUBTRACT_ACTIONS
from .actions import NEUTRAL_ACTIONS as _NEUTRAL_ACTIONS


def _sample_dates(first: str, last: str, cadence: str) -> list[str]:
    """Return the list of sample dates in [first, last].

    `cadence`:
      - "month"       → last calendar day of each month, plus `last`
      - "semimonthly" → 15th AND last calendar day of each month, plus
                        `last`.  EOM dates are identical to the "month"
                        cadence — latest-in-month consumers (monthly_pnl's
                        year×month grid, annual returns' year boundaries)
                        see exactly the same month-end boundaries; the
                        mid-month points only add resolution in between.
      - "week"  → every 7 days from `first`, plus `last`
      - "day"   → every day (can be large; use sparingly)
    """
    start = datetime.strptime(first, "%Y-%m-%d").date()
    end   = datetime.strptime(last,  "%Y-%m-%d").date()
    out: list[str] = []
    if cadence in ("month", "semimonthly"):
        cur = date(start.year, start.month, 1)
        while cur <= end:
            if cur.month == 12:
                eom = date(cur.year, 12, 31)
            else:
                eom = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
            if cadence == "semimonthly":
                mid = date(cur.year, cur.month, 15)
                if start <= mid <= end:
                    out.append(mid.isoformat())
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
                    cadence: str = "semimonthly",
                    account_methods: dict[str, str] | None = None,
                    disposal_lots: dict | None = None) -> list[dict]:
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

    These totals/maps and positions describe posted custody. Delayed matched
    transfers add optional ``in_transit`` rows and ``valuation_precision``
    posted components; ``return_flows.scope_snapshot_value`` and
    ``scope_snapshot_basis`` combine them for portfolio or multi-account views.

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

    CASH: a Savings cash position has no lots, so its basis is walked
    separately — principal only, via
    ``pipeline_stages.cash_principal_effect``, the same per-txn rule
    ``build_holdings`` uses.  This used to be face value (``basis ==
    qty``, unrealized always 0) while the holdings table used
    principal, so the two disagreed by every dollar of interest the
    account had ever earned.  Nothing caught it: the parity check
    skipped ``symbol == "USD"`` outright.  It surfaced on the
    Performance tab, whose Unrealized card reads live holdings for the
    lifetime window and this snapshot for every other window — one
    label, one date, two answers.  The USD skip is gone; keep the two
    conventions identical.
    """
    if not txns:
        return []
    dates = [t.get("date", "") for t in txns if t.get("date")]
    if not dates:
        return []

    first = min(dates)
    today = clock.now(fallback=datetime.now).date().isoformat()
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

    # Implicit broker-cash bridges.  For groups whose export is
    # provably complete (see cash_bridge.BRIDGED_GROUPS), we know the
    # uninvested cash balance at every date.  Adding it to snapshot
    # value eliminates the spurious "drop" mid-period when the user
    # sells and the next Buy / Withdrawal falls on a later snapshot —
    # a drop that has no offsetting external flow, so TWR would
    # otherwise book it as a market loss.  See cash_bridge.py.
    from .cash_bridge import all_series as _bridge_all_series, balance_at, BRIDGE_MIN
    _bridge_series = _bridge_all_series(txns)

    # Canonical walk order, established up front and fed to every
    # pre-pass below — same rule as basis._walk (see basis._sort_key)
    # so neither walker's decisions depend on the caller's list order.
    txns_sorted = sorted(txns, key=_basis_sort_key)

    # Pre-pair cross-account transfers exactly like basis.py so basis
    # carries from source to destination on paired transfers, and
    # intra-group pairs (same (account_group, symbol) on both legs) stay
    # no-ops in the lot queue.  Without this, the latest history snapshot
    # would under-count total basis vs. main.py's FIFO holdings total.
    pairings = _pair_transfers(txns_sorted)
    transfer_pairs = eligible_account_transfer_pairs(txns_sorted, pairings=pairings)
    tin_to_tout  = pairings["tin_to_tout"]
    intra_group  = pairings["intra_group"]
    paired_touts = pairings["paired_touts"]
    stashed_tout_lots: dict[int, list[dict]] = {}
    tout_handled: set[int] = set()

    # REBASE pairs — intra-group pairs whose Transfer In carries a user
    # basis_override.  Mirrors basis._walk exactly: consume the old lots
    # (no gain) THEN push one lot at the override basis, atomically on
    # whichever leg walks first (consume-then-push so LIFO/HIFO can't
    # eat the fresh override lot).  See the rebase block in basis.py.
    rebase_pairs: dict[int, tuple[dict, dict]] = {}
    for _t in txns_sorted:
        if (id(_t) in intra_group and _basis_effect(_t) == "transfer_in"
                and _t.get("basis_override") is not None):
            _tout = tin_to_tout.get(id(_t))
            if _tout is not None:
                rebase_pairs[id(_t)] = (_t, _tout)
                rebase_pairs[id(_tout)] = (_t, _tout)
    rebase_done: set[int] = set()

    # Return-of-capital rows netted against their reversals — the same
    # shared rule basis._walk uses, so a reversed-then-re-paid
    # distribution reduces basis once in both walkers.
    roc_net = pair_roc_events(txns_sorted)

    # Wrap/unwrap groups — basis-carrying conversions processed
    # atomically the first time any leg is met, exactly like basis.py.
    wrap_groups = _pair_wraps(txns_sorted)
    wrap_until = wrap_next_dates(wrap_groups)
    _sym_families = wrap_symbol_families(wrap_groups)
    wrap_done: set[tuple] = set()

    def _method_for(acct: str) -> str:
        if not account_methods:
            return "fifo"
        m = account_methods.get(acct, "fifo")
        return m if m in ("fifo", "lifo", "hifo") else "fifo"

    balances: dict[tuple[str, str], float] = defaultdict(float)
    lots: dict[tuple[str, str], list[dict]] = defaultdict(list)
    # The shared lot-creation helper needs only this view of the state.
    # Every history account uses the same lot-list representation; its
    # relief method is applied separately by _consume.
    lot_state = {"lots": lots}
    # Running principal for Savings cash positions — cash has no lots, so
    # its basis is walked here instead.  Same per-txn rule the holdings
    # table uses (pipeline_stages.cash_principal_effect); see the cash
    # note in this function's docstring for why face value was wrong.
    cash_principal: dict[tuple[str, str], float] = defaultdict(float)
    # Fallback price basis per symbol — last non-zero `price` observed in a
    # transaction at or before the current sample date.  Used when the price
    # cache can't resolve a symbol (multi-word fund display names, delisted
    # tickers, etc.).  This mirrors what main.py's holdings computation does
    # for "now" and lets history stay in lockstep with the holdings total.
    last_txn_price: dict[str, float] = {}
    idx = 0

    # Broker-report disposal hints — own copy, exactly like basis._walk
    # (consuming mutates hint state; each walker starts fresh).  The
    # wrap-demand pool (future-disposal direction for wraps) is likewise
    # per-walk with its own budget.
    _disposal_lots = copy_disposal_lots(disposal_lots)
    _wrap_demand = build_wrap_demand(_disposal_lots)

    def _reserved_for(acct, date, sym):
        """Bound alias for the module-level rule shared with basis._walk."""
        return reserved_for(_disposal_lots, _sym_families, acct, date, sym)

    def _consume(key, qty_to_remove, hints=None, reserved=None):
        """Remove qty_to_remove from lots[key] using the owning account's
        lot-relief method; return (basis_removed, carried).  Delegates to
        basis._consume_lots_reserving (or the report-directed variant when
        the broker's gain/loss report covers this disposal) so consume
        order matches the annotated basis walk exactly."""
        if hints:
            return _consume_lots_directed(lots[key], qty_to_remove,
                                          _method_for(key[0]), hints,
                                          reserved=reserved)
        return _consume_lots_reserving(lots[key], qty_to_remove,
                                       _method_for(key[0]), reserved)

    def _push_txn(key, t, qty_add, basis_dollars, date,
                  origin="reconstructed"):
        """Bind the annotated walker's lot creation to history's state."""
        _push_txn_lots(lot_state, "fifo", key, t, qty_add, basis_dollars,
                       date, origin=origin)

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

            # Cash basis walk — a no-op for everything except cash in a
            # Savings account.  Kept next to the balance walk (not in the
            # basis dispatch below) because cash never enters a lot queue:
            # basis_effect_for returns "ignore" for every USD row.
            _cash_eff = cash_principal_effect(t)
            if _cash_eff:
                cash_principal[(acct, sym)] += _cash_eff

            # Intra-group transfers net to zero in the same lot queue —
            # except rebase pairs (Transfer In with a user basis
            # override), which replace the carried basis.  Mirrors
            # basis._walk.
            if id(t) in intra_group:
                pair = rebase_pairs.get(id(t))
                if pair is not None:
                    tin, _tout = pair
                    if id(tin) not in rebase_done:
                        rebase_done.add(id(tin))
                        r_qty = float(tin.get("quantity", 0) or 0)
                        r_key = (tin.get("account_group", "") or "",
                                 tin.get("symbol", "") or "")
                        new_basis = float(tin["basis_override"])
                        consumed, carried = _consume_for_rebase(
                            lots[r_key], r_qty, _method_for(r_key[0]),
                            tin.get("date", ""), new_basis,
                            reserved=_reserved_for(r_key[0],
                                                   tin.get("date", ""),
                                                   r_key[1]))
                        if _rebase_is_move(consumed, new_basis):
                            # Wallet move of broker-tracked lots: keep
                            # them verbatim — mirrors basis._walk.
                            for lot in carried:
                                lots[r_key].append(dict(lot))
                        else:
                            _push_txn(r_key, tin, r_qty, new_basis,
                                      tin.get("date", ""))
                continue

            # Cost-basis walk — rules mirror basis._walk (see docstring
            # invariant).  A user-supplied basis_override (metadata `Cost
            # Basis` row, stamped by cost_basis_overrides.match_and_stamp)
            # wins on the lot-creating branches, same as basis.py's _ov.
            effect = basis_effect_for(sym, action)
            key = (acct, sym)
            if effect == "add":
                _push_txn(key, t, qty,
                          basis_override_or(t, _basis_dollars(t)),
                          t.get("date", ""))
            elif effect == "zero_basis":
                # FMV-at-receipt when the broker recorded a price, else $0.
                # Override wins — mirrors basis._walk's zero_basis branch.
                _push_txn(key, t, qty, fmv_basis(t, qty),
                          t.get("date", ""), origin=zero_basis_origin(t))
            elif effect == "remove":
                _consume(key, qty,
                         hints=hints_for(_disposal_lots, acct, sym,
                                         t.get("date", "")),
                         reserved=_reserved_for(acct, t.get("date", ""),
                                                sym))
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
                    _push_txn(key, t, qty, fmv_basis(t, qty),
                              t.get("date", ""), origin="fmv")
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
                kind = wrap_kind(action)
                gkey = (acct, t.get("date", "") or "", kind)
                if gkey not in wrap_done:
                    wrap_done.add(gkey)
                    g = wrap_groups.get(gkey)
                    if g and g["out"] and g["in"] and g["q_out"] > 0 and g["q_in"] > 0:
                        # Direct the wrap's source consumption by the
                        # destination's FUTURE report disposals (wrap
                        # demand) — mirrors basis._walk's wrap branch
                        # exactly.
                        _b, _dest = wrap_carry_lots(
                            g, acct, t.get("date", ""),
                            wrap_demand=_wrap_demand, wrap_until=wrap_until,
                            consume=lambda k, q, h, r: _consume(
                                k, q, hints=h, reserved=r),
                            reserved=_reserved_for(acct, t.get("date", ""),
                                                   g["src"]))
                        for lot in _dest:
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
                            _push_txn((acct, il.get("symbol", "")), il,
                                      iq, fmv_basis(il, iq),
                                      il.get("date", ""), origin="fmv")
            elif effect == "split":
                lq = lots[key]
                # Shared with basis._walk rather than reimplemented.  This
                # was a verbatim copy and F-015 is exactly that: the rule
                # existed twice and NEITHER copy was executed by a test.
                _apply_split_to_lots(lq, sum(lot["qty"] for lot in lq), qty)
            elif effect == "roc":
                # Return of capital — reduce basis pro-rata, excess is
                # gain (which this walker doesn't track; it only needs
                # the lot state).  Mirrors basis._walk's roc branch via
                # the same two shared rules.
                net = roc_net.get(id(t), 0.0)
                if net > 0:
                    apply_roc_to_lots(lots[key], _method_for(acct), net,
                                      t.get("date", ""))
            elif (effect == "ignore" and qty > 0 and sym
                  and sym != "USD" and t.get("basis_override") is not None):
                # Neutral same-pool conversion with broker-reported
                # basis (ETH2 deprecation etc.) — zero-gain rebase.
                # Mirrors basis._walk's rebase_neutral branch.
                _consume(key, qty,
                         reserved=_reserved_for(acct, t.get("date", ""),
                                                sym))
                _push_txn(key, t, qty, float(t["basis_override"]),
                          t.get("date", ""))
            # else: ignore / unknown → no-op

        active_transfers = [(tout, tin) for tout, tin in transfer_pairs
                            if tout["date"] <= sample_date < tin["date"]]
        by_group:       dict[str, float] = defaultdict(float)
        by_type:        dict[str, float] = defaultdict(float)
        by_sector:      dict[str, float] = defaultdict(float)
        basis_group:    dict[str, float] = defaultdict(float)
        basis_type:     dict[str, float] = defaultdict(float)
        positions:      list[dict] = []
        precision_positions: list[dict] = []
        total = 0.0
        total_basis = 0.0
        priced = 0.0
        attempted = 0.0
        px_on_date: dict[str, float | None] = {}
        for (acct, sym), qty in balances.items():
            # Price + today-basis quantity, resolved by the shared
            # kernel (see src/valuation.py for the ladder).
            m = mark(sym, qty, sample_date, last_txn_price,
                     price_cache=px_on_date)
            price, adj_qty = m.price, m.qty

            # Dust filter.  This used to be an inline copy whose comment
            # claimed it mirrored main.py's `_is_dust` "so history
            # positions exactly match the holdings table" -- and it did
            # not: `is_dust` also drops small NEGATIVE fractional priced
            # positions (unpaired corporate-action surrenders) and the
            # copy kept them, so those showed in the as-of-date holdings
            # view and not in the current one.  Now literally the same
            # function.
            if mark_is_dust(m, qty):
                continue

            # Cost basis for this position at this date
            if sym in CASH_SYMBOLS:
                # Principal, NOT face value — the holdings table's
                # convention (pipeline_stages.build_holdings), so a
                # HYSA's accrued interest reads as unrealized gain on
                # both sides.  Only Savings cash reaches `balances`;
                # the bridged-group synthetic USD rows appended below
                # are genuinely face-value.
                pos_basis = cash_principal.get((acct, sym), 0.0)
            else:
                pos_basis = sum(lot["qty"] * lot["basis_per_share"]
                                for lot in lots.get((acct, sym), []))

            # Already scaled by the option contract multiplier — quantity
            # is in contracts and price is the per-share premium (see
            # config.contract_multiplier).
            val = m.value

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
                "price":         eff_price,
                "value":         round(val, 2) if val is not None else None,
                "cost_basis":    round(pos_basis, 2),
            })
            if active_transfers:
                precision_positions.append({**positions[-1], "value": val,
                                            "cost_basis": pos_basis})

        # Implicit broker-cash bridge — the reconstructed uninvested
        # cash balance for each bridged group at this snapshot date.
        # Adds 1:1 to value, basis and sector "Cash", and emits a
        # synthetic USD position so the dashboard's as-of-date holdings
        # filter shows the cash during sell→buy gaps.
        for _bgroup, _bseries in _bridge_series.items():
            bcash = round(balance_at(_bseries, sample_date), 2)
            if bcash < BRIDGE_MIN:
                continue
            _btype = ACCOUNT_TYPES.get(_bgroup, "Taxable")
            total += bcash
            by_group[_bgroup] += bcash
            by_type[_btype] += bcash
            by_sector["Cash"] += bcash
            total_basis += bcash
            basis_group[_bgroup] += bcash
            basis_type[_btype] += bcash
            positions.append({
                "account_group": _bgroup,
                "symbol":        "USD",
                "quantity":      bcash,
                "price":         1.0,
                "value":         bcash,
                "cost_basis":    bcash,
            })
            if active_transfers:
                precision_positions.append(positions[-1])

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
        # Keep posted custody totals/maps intact. These lots still belong to
        # the portfolio but neither broker currently reports them; only scopes
        # containing both endpoints include the separate transit component.
        in_transit = transit_positions(
            active_transfers, sample_date, last_txn_price,
            carried_basis={id(tout): sum(lot["qty"] * lot["basis_per_share"]
                                        for lot in stashed_tout_lots.get(id(tout), ()))
                           for tout, _ in active_transfers},
            price_cache=px_on_date)
        if in_transit:
            history[-1]["in_transit"] = in_transit
            # The existing custody fields are display-rounded. Preserve the
            # underlying sums only while transit is active so combining two
            # separately rounded halves cannot manufacture a gain/loss.
            history[-1]["valuation_precision"] = {
                "total": total, "by_account_group": dict(by_group),
                "by_account_type": dict(by_type), "by_sector": dict(by_sector),
                "total_cost_basis": total_basis,
                "cost_basis_by_group": dict(basis_group),
                "cost_basis_by_type": dict(basis_type),
                "positions": precision_positions,
            }

    return history


def compute_daily_totals(txns: list[dict], *,
                         transit_issues: list[dict] | None = None) -> list[tuple[str, float]]:
    """Daily total portfolio value from the first transaction through
    today: ``[(YYYY-MM-DD, total), ...]`` for every calendar day.

    A lightweight companion to ``compute_history`` for consumers that
    need DAILY resolution of the TOTAL only — currently the drawdown
    stats, where monthly/semimonthly sampling structurally understates
    peak-to-trough depth.  No lots, no positions, no benchmarks, and the
    result is never exported wholesale (~3k tuples stay in memory).

    Totals include separately marked assets in transit, unlike the snapshot's
    raw posted-custody ``total``. Coverage failures are optionally collected in
    ``transit_issues`` for callers whose snapshots miss the transfer interval.
    Both walks share the same valuation rules:
      - balance walk skips USD outside Savings (matches main.py);
        NEUTRAL actions are no-ops, SUBTRACT_ACTIONS subtract
      - cache-priced: as-of-date qty × ``split_factor_since`` × close
      - unpriceable: most recent txn price at or before the day
        (no split adjustment — txn prices are as-of-trade)
      - option contracts × ``contract_multiplier``
      - dust filter mirrors main.py's ``_is_dust``
      - Coinbase implicit USD bridge added ($1 threshold, negatives
        clamp to 0 — same as the snapshot bridge)
    """
    if not txns:
        return []
    dated = [t for t in txns if t.get("date")]
    if not dated:
        return []

    first = min(t["date"] for t in dated)
    today = clock.now(fallback=datetime.now).date().isoformat()
    last = max(max(t["date"] for t in dated), today)

    # Quantities are end-of-day, but last-price fallbacks depend on order.
    # Use the same canonical sequence as history and exact-date valuation.
    dated = sorted(dated, key=_basis_sort_key)
    transfer_pairs = eligible_account_transfer_pairs(dated)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in dated:
        by_day[t["date"]].append(t)

    # Implicit broker-cash bridges, each walked with its own pointer.
    from .cash_bridge import all_series as _bridge_all_series, BRIDGE_MIN
    bridge_eod = _bridge_all_series(txns)
    bridge_idx: dict[str, int] = {g: 0 for g in bridge_eod}
    bridge_bal: dict[str, float] = {g: 0.0 for g in bridge_eod}

    balances: dict[tuple[str, str], float] = defaultdict(float)
    last_txn_price: dict[str, float] = {}

    out: list[tuple[str, float]] = []
    cur = datetime.strptime(first, "%Y-%m-%d").date()
    end = datetime.strptime(last, "%Y-%m-%d").date()
    while cur <= end:
        d_iso = cur.isoformat()
        for t in by_day.get(d_iso, ()):
            acct = t.get("account_group", "")
            sym = t.get("symbol", "")
            p = float(t.get("price", 0) or 0)
            if sym and p > 0:
                last_txn_price[sym] = p
            action = t.get("action", "")
            qty = float(t.get("quantity", 0) or 0)
            if sym in CASH_SYMBOLS and ACCOUNT_TYPES.get(acct) != "Savings":
                continue
            if action in _NEUTRAL_ACTIONS:
                continue
            if action in _SUBTRACT_ACTIONS:
                balances[(acct, sym)] -= qty
            else:
                balances[(acct, sym)] += qty

        for _bgroup, _bseries in bridge_eod.items():
            while (bridge_idx[_bgroup] < len(_bseries)
                   and _bseries[bridge_idx[_bgroup]][0] <= d_iso):
                bridge_bal[_bgroup] = _bseries[bridge_idx[_bgroup]][1]
                bridge_idx[_bgroup] += 1

        total = 0.0
        # Scoped to THIS date: one symbol is typically held in several
        # account groups, and the memo collapses those to one lookup.
        # It must not outlive the day or a stale price leaks forward.
        px_today: dict[str, float | None] = {}
        for (acct, sym), qty in balances.items():
            if abs(qty) < QTY_EPSILON:
                continue
            m = mark(sym, qty, d_iso, last_txn_price, price_cache=px_today)
            # Unpriceable positions contribute nothing — same as the
            # snapshot walker's priced_pct gap.
            if m.value is None or mark_is_dust(m, qty):
                continue
            total += m.value

        for _bbal in bridge_bal.values():
            # Same clamp as cash_bridge.balance_at — negatives are
            # same-day ordering artifacts, not real balances.
            if _bbal >= BRIDGE_MIN:
                total += _bbal
        in_transit = transit_positions(
            transfer_pairs, d_iso, last_txn_price, price_cache=px_today)
        total += sum(p["value"] or 0 for p in in_transit)
        if transit_issues is not None:
            missing = [p for p in in_transit if p["value"] is None]
            if missing:
                transit_issues.append({"date": d_iso, "in_transit": missing})
        out.append((d_iso, round(total, 2)))
        cur += timedelta(days=1)
    return out
