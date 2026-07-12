"""CLI entry point — scan, rename, parse, and export."""

import argparse
import sys
from datetime import datetime

from .analytics import build_analytics
from .basis import (
    compute_basis_all_methods, compute_basis_default,
    compute_cash_summary, state_to_holdings,
)
from .config import (
    ACCOUNT_GROUPS, ACCOUNT_TYPES, CASH_SYMBOLS, CRYPTO_SYMBOLS,
    DATA_DIR, EXPORT_DIR, SYMBOL_MAP,
)
from .dashboard import generate_dashboard
from .export import deduplicate, export_json
from .history import compute_history
from .normalize import normalize_action
from .parsers import parse_all_files
from .prices import (
    build_display_map,
    ensure_coverage, ensure_proxy_anchors,
    fetch_latest_close_batch, get_price,
    revalidate_stale_caches,
    save_caches as save_price_cache,
)
from .metadata import parse_metadata
from .scanner import rename_data_files, scan_data_files
from .sectors import enrich_holdings, save_cache as save_sector_cache

def _usaa_position_before_date(txns: list[dict], symbol: str, asof_date: str) -> float:
    bal = 0.0
    for t in txns:
        if t.get("account") != "USAA Roth IRA":
            continue
        if t.get("symbol") != symbol:
            continue
        if t.get("date", "") > asof_date:
            continue

        action = t.get("action", "")
        qty = float(t.get("quantity", 0) or 0)

        if action in {"Fee", "Sell", "Transfer Out", "Withdrawal", "Distribution"}:
            bal -= qty
        elif action in {"Neutral"}:
            pass
        else:
            bal += qty
    return bal


# Coinbase reconciliation lives in src/coinbase_reconcile.py (named to
# disambiguate from src/parsers/coinbase.py, which only parses the CSV).
# The aliases below preserve the historical main.* call sites.
from . import coinbase_reconcile as _coinbase

coinbase_usd_effect = _coinbase.usd_effect
coinbase_usd_series = _coinbase.usd_series
_reconcile_coinbase_external_funding = _coinbase.reconcile_external_funding
_reconcile_coinbase_intra_transfers = _coinbase.reconcile_intra_transfers


def _reconcile_usaa_to_schwab_transfer(txns: list[dict]) -> list[dict]:
    out = list(txns)

    inbound: dict[tuple[str, str], float] = {}
    for t in txns:
        if t.get("account") != "Schwab Roth IRA":
            continue
        if (t.get("raw_action") or t.get("action")) != "Security Transfer":
            continue
        dt = t.get("date", "")
        sym = t.get("symbol", "")
        if not dt or not sym:
            continue
        inbound[(dt, sym)] = inbound.get((dt, sym), 0.0) + float(t.get("quantity", 0) or 0)

    if not inbound:
        return out

    seen = set()
    for (dt, sym), qty_in in inbound.items():
        usaa_pos = _usaa_position_before_date(txns, sym, dt)
        if usaa_pos <= 0:
            continue

        transfer_qty = min(usaa_pos, qty_in)
        if transfer_qty <= 0:
            continue

        k1 = ("USAA Roth IRA", dt, sym, "Synthetic Transfer Out", round(transfer_qty, 8))
        if k1 not in seen:
            out.append({
                "date": dt,
                "account": "USAA Roth IRA",
                "symbol": sym,
                "action": "Synthetic Transfer Out",
                "quantity": round(transfer_qty, 8),
                "price": 0.0,
                "fees": 0.0,
                "amount": 0.0,
                "description": "Auto-generated to match Schwab Security Transfer In",
                "source": "auto-reconcile-transfer",
            })
            seen.add(k1)

        residual = usaa_pos - transfer_qty
        small_abs = abs(residual) <= 0.25
        small_rel = abs(residual) <= max(1e-8, abs(usaa_pos) * 0.001)

        if abs(residual) > 1e-8 and (small_abs or small_rel):
            k2 = ("USAA Roth IRA", dt, sym, "Transfer Reconcile", round(abs(residual), 8))
            if k2 not in seen:
                out.append({
                    "date": dt,
                    "account": "USAA Roth IRA",
                    "symbol": sym,
                    "action": "Transfer Reconcile",
                    "quantity": round(abs(residual), 8),
                    "price": 0.0,
                    "fees": 0.0,
                    "amount": 0.0,
                    "description": f"Auto residual write-off after Schwab transfer; residual={residual:.8f}",
                    "source": "auto-reconcile-transfer",
                })
                seen.add(k2)

    return out

def _reconcile_apex_conversions(txns: list[dict]) -> list[dict]:
    """Neutralize Robinhood clearing-migration CONV rows whose position
    is already covered by the hand-entered Apex-era file.

    Robinhood's late-2018 move from Apex clearing to self-clearing
    emitted a CONV row for every position carried across — previously
    the only record of those positions, since the transaction exports
    don't reach back into the Apex era.  With the hand-entered
    robinhood-apex CSV supplying the true buy legs, an untouched CONV
    double-adds the position (observed: an expired put showing +1 open
    contract forever).  A CONV is re-tagged Neutral only when the Apex
    file's net quantity for that symbol on/before the CONV date covers
    it — positions with no Apex-file history (e.g. a referral free
    share) keep their CONV as the origin row.
    """
    apex_rows = [t for t in txns if "apex" in (t.get("source") or "").lower()]
    if not apex_rows:
        return txns
    _SELLS = {"Sell", "Option Sell"}

    def _apex_net(symbol: str, on_or_before: str) -> float:
        net = 0.0
        for t in apex_rows:
            if t.get("symbol") != symbol or (t.get("date") or "") > on_or_before:
                continue
            q = abs(float(t.get("quantity", 0) or 0))
            net += -q if t.get("action") in _SELLS else q
        return net

    for t in txns:
        if (t.get("raw_action") or t.get("action") or "").upper() != "CONV":
            continue
        sym = t.get("symbol") or ""
        qty = abs(float(t.get("quantity", 0) or 0))
        if not sym or qty <= 0:
            continue
        if _apex_net(sym, t.get("date") or "") >= qty - 1e-6:
            t["action"] = "Neutral"
            t["description"] = ((t.get("description") or "")
                                + " [auto-reconciled: position covered by "
                                  "Apex-era file]").strip()
    return txns


def _refresh_prices_only(args) -> None:
    """Fast path: re-pull today's prices and regenerate the dashboard
    without re-processing CSVs.

    Loads the previously-exported ``transactions.json`` (so all txns
    keep their basis annotations from the prior full run), then runs
    just the price-fetch + holdings rebuild + history + analytics +
    export pipeline.  Saves ~2-3 seconds vs the full pipeline AND
    forces yfinance to re-pull today's close even if the cache thinks
    today is already covered (necessary for intraday refreshes).

    Output is identical in shape to a full run — same JSON schema,
    same dashboard layout — so consumers don't need to special-case
    "this came from refresh mode".  The only thing that changes is
    the displayed prices and any figures derived from them
    (current value, unrealized P&L, today's snapshot, history's
    final point, basis_methods totals).
    """
    import json as _json
    from collections import defaultdict
    from pathlib import Path

    from .pipeline_stages import (
        build_basis_methods_totals, build_holdings, compute_cash_principal,
        walk_balances,
    )

    output_path = Path(args.output) if args.output else (EXPORT_DIR / "transactions.json")
    if not output_path.exists():
        print(f"  --refresh-prices needs an existing {output_path} to start "
              f"from. Run the full pipeline first: python -m src.main")
        sys.exit(1)

    print(f"Loading previous export from {output_path}...")
    data = _json.loads(output_path.read_text(encoding="utf-8"))
    txns               = data.get("transactions", []) or []
    sector_of_existing = data.get("sector_of", {}) or {}
    print(f"  {len(txns)} transactions, {len(sector_of_existing)} symbol sectors")

    # Stage: walk balances (with USD-non-Savings skip rule).  This is
    # the same stage main() uses for the full pipeline — single source
    # of truth.
    txns, balances = walk_balances(txns)

    # Held symbols across all account groups (for the price refresh).
    final_bal: dict[str, float] = defaultdict(float)
    for (_, sym), q in balances.items():
        final_bal[sym] += q
    held_symbols = sorted({s for s, q in final_bal.items() if abs(q) > 1e-9})

    # Refresh today's close for held + benchmark symbols via a single
    # batched yfinance call (one HTTP roundtrip for ~150 symbols, ~3s
    # vs ~30s individually).  Historical backfill is NOT re-run —
    # refresh-prices mode assumes the prior full pipeline already
    # filled in the historical cache.
    _BENCHMARK_SYMBOLS = ("SPY", "BND", "VXUS")
    refresh_set = sorted(set(held_symbols) | set(_BENCHMARK_SYMBOLS))
    print(f"Refreshing latest close for {len(refresh_set)} "
          f"held + benchmark symbol(s) (batched)...")
    ensure_proxy_anchors(txns, verbose=False)
    fetch_latest_close_batch(refresh_set)

    # Build last_prices: txn-fallback + cache lookup at today's date.
    today_str = datetime.now().date().isoformat()
    last_prices: dict[str, float] = {}
    for txn in txns:
        price = float(txn.get("price", 0) or 0)
        if price > 0:
            last_prices[txn.get("symbol", "")] = price
    for sym in held_symbols:
        cached = get_price(sym, today_str)
        if cached is not None:
            last_prices[sym] = cached

    # FIFO basis by (account, symbol) — derived from per-txn
    # basis_effect annotations on the loaded txns, so we don't re-walk.
    # ``derive_basis_by_key_from_txns`` is the single source of truth
    # for this reconstruction (also used by the lot-queue parity check
    # in data_health) — handles transfer_out, zero_basis, etc., not
    # just "add" and "remove".
    from .basis import derive_basis_by_key_from_txns
    fifo_basis_by_key = derive_basis_by_key_from_txns(txns)

    # Stage: build holdings (uses the same dust filter + cash-principal
    # logic as main()).
    cash_principal_by_key = compute_cash_principal(txns)
    holdings, holdings_by_account = build_holdings(
        balances, last_prices, dict(fifo_basis_by_key), cash_principal_by_key,
    )

    # Sector enrichment (cheap, cache-first)
    from .sectors import get_sector
    all_symbols_ever = {t.get("symbol", "") for t in txns if t.get("symbol")}
    all_symbols_ever.update(_BENCHMARK_SYMBOLS)
    sector_of = {sym: sector_of_existing.get(sym) or get_sector(sym)
                 for sym in all_symbols_ever}
    save_sector_cache()
    enrich_holdings(holdings, verbose=False)
    enrich_holdings(holdings_by_account, verbose=False)

    # Personal metadata + account-mapping overrides.  Loaded BEFORE the
    # basis_methods rebuild loop below since that loop calls
    # ACCOUNT_TYPES.get(...) — overrides need to be in place first.
    retirement_meta = parse_metadata(DATA_DIR)
    if retirement_meta.get("account_groups"):
        ACCOUNT_GROUPS.update(retirement_meta["account_groups"])
    if retirement_meta.get("account_types"):
        ACCOUNT_TYPES.update(retirement_meta["account_types"])

    # Stage: rebuild basis_methods totals from per-method holdings (with
    # fresh prices) + fold cash.  Mirrors the full pipeline exactly.
    basis_methods = data.get("basis_methods", {}) or {}
    for m, block in basis_methods.items():
        for r in block.get("holdings", []):
            px = last_prices.get(r.get("symbol", ""), 0.0)
            r["price"] = round(px, 2)
            r["value"] = round(r["quantity"] * px, 2) if px else None
            r["unrealized_gain"] = (
                round(r["value"] - r["cost_basis"], 2)
                if (r.get("value") is not None and r.get("cost_basis") is not None) else None
            )
            r["account_type"] = ACCOUNT_TYPES.get(r.get("account_group", ""), "Taxable")
    build_basis_methods_totals(basis_methods, holdings_by_account)

    cash = data.get("cash_summary", {}) or {}

    # Stamp user-supplied cost-basis overrides BEFORE the history walk —
    # history.compute_history honours basis_override on lot-creating
    # branches (the exported JSON strips the stamp, so it must be
    # re-applied on load).
    from .cost_basis_overrides import match_and_stamp as _stamp_cb
    _stamp_cb(txns, retirement_meta.get("cost_basis_overrides"))

    # Broker-reported disposal lots + acquisition basis — same sources
    # as the full pipeline (see the main() call sites); the history
    # walker and the FIFO re-walk below must see the same overrides or
    # the refresh path would silently disagree with the full run.
    from .broker_lots import (load_acquisition_lots, load_disposal_lots,
                              load_robinhood_1099_income,
                              merge_auto_reconcile_rows,
                              stamp_acquisition_basis)
    disposal_lots = load_disposal_lots(DATA_DIR, txns)
    stamp_acquisition_basis(txns, load_acquisition_lots(DATA_DIR))

    # Auto Reconcile Income rows from consolidated 1099s — mirrors the
    # full pipeline so the refresh path's reconciliation panel matches.
    retirement_meta["reconcile"] = merge_auto_reconcile_rows(
        retirement_meta.get("reconcile"), load_robinhood_1099_income(DATA_DIR))

    print("Computing portfolio history with refreshed prices...")
    history = compute_history(txns, sector_of,
                              account_methods=retirement_meta.get("lot_methods"),
                              disposal_lots=disposal_lots)
    save_price_cache()
    print(f"  {len(history)} snapshot(s) from {history[0]['date'] if history else '—'} "
          f"to {history[-1]['date'] if history else '—'}")

    print("Computing analytics...")
    # Re-derive the FIFO lot-state so the tax tab's "approaching
    # long-term" horizon can enumerate open lots.  Idempotent — the
    # walker re-writes identical annotations onto txns it's already
    # seen, and we only need state["lots"] downstream.
    refresh_fifo_state = compute_basis_default(
        txns, account_methods=retirement_meta.get("lot_methods"),
        disposal_lots=disposal_lots)
    analytics = build_analytics(txns, history, holdings, holdings_by_account,
                                 retirement_meta,
                                 cash_summary=cash,
                                 basis_methods=basis_methods,
                                 fifo_state=refresh_fifo_state)

    export_json(txns, output_path, holdings=holdings,
                holdings_by_account=holdings_by_account,
                history=history,
                basis_methods=basis_methods,
                cash_summary=cash,
                retirement_meta=retirement_meta,
                analytics=analytics,
                sector_of=sector_of,
                display_of=build_display_map())
    print(f"\nExported {len(txns)} transactions to {output_path}")

    dashboard_path = output_path.parent / "dashboard.html"
    generate_dashboard(output_path, dashboard_path)
    print(f"Dashboard: {dashboard_path}")

    _maybe_assert_invariants(txns, holdings_by_account, history, analytics,
                             cash)


def _maybe_assert_invariants(txns, holdings_by_account, history, analytics,
                             cash_summary=None) -> None:
    """When ``FIN_ASSERT_INVARIANTS=1``, fail loudly on any high-
    severity data-health violation.  Off by default in production —
    minor coverage gaps shouldn't crash a daily pipeline run.  Tests
    enable it via conftest so refactor regressions get caught at
    the point of introduction, not via "What's Changed" weeks later."""
    import os
    if not os.environ.get("FIN_ASSERT_INVARIANTS"):
        return
    from .analytics.data_health import check_invariants
    from .config import CACHE_DIR
    check_invariants(txns, holdings_by_account, history, analytics, CACHE_DIR,
                     cash_summary=cash_summary)


def main():
    parser = argparse.ArgumentParser(description="Personal portfolio tracker")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview file renames without making changes",
    )
    parser.add_argument(
        "--skip-rename", action="store_true",
        help="Skip the file rename step",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output JSON path (default: exports/transactions.json)",
    )
    parser.add_argument(
        "--refresh-caches", action="store_true",
        help="Force a deep refresh of the splits cache and clear all "
             "tombstones, ignoring the weekly throttle.  Use after a "
             "known split or to retry permanently-failed tickers.",
    )
    parser.add_argument(
        "--refresh-prices", action="store_true",
        help="Fast mode: skip CSV scanning, parsing, deduplication, "
             "normalisation, and the basis walker.  Loads the previous "
             "exports/transactions.json, force-refreshes today's prices "
             "from yfinance, then re-runs history + analytics + dashboard. "
             "Use when transactions haven't changed but you want updated "
             "values reflecting today's market close.",
    )
    parser.add_argument(
        "--export-snapshot", metavar="PATH", default=None,
        help="Bundle every CSV in data/ into a single JSON snapshot at "
             "PATH and exit.  Useful for moving your portfolio data to a "
             "fresh checkout — one file instead of dozens.",
    )
    parser.add_argument(
        "--import-snapshot", metavar="PATH", default=None,
        help="Restore CSVs from a snapshot file into data/ and exit. "
             "Existing files are preserved (use --force to overwrite).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Allow --import-snapshot to overwrite existing files in data/.",
    )
    args = parser.parse_args()

    if args.export_snapshot:
        from pathlib import Path
        from .snapshot import export_snapshot
        out = Path(args.export_snapshot)
        bundle = export_snapshot(DATA_DIR, out)
        print(f"Exported {bundle['file_count']} file(s) to {out}")
        return

    if args.import_snapshot:
        from pathlib import Path
        from .snapshot import import_snapshot
        src = Path(args.import_snapshot)
        result = import_snapshot(src, DATA_DIR, overwrite=args.force)
        print(f"Imported {len(result['written'])}/{result['total']} file(s) "
              f"into {DATA_DIR}")
        if result["skipped"]:
            print(f"  Skipped {len(result['skipped'])} existing file(s) "
                  f"(use --force to overwrite):")
            for name in result["skipped"][:10]:
                print(f"    {name}")
            if len(result["skipped"]) > 10:
                print(f"    ... and {len(result['skipped']) - 10} more")
        return

    if args.refresh_prices:
        _refresh_prices_only(args)
        return

    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True)
        print(f"Created {DATA_DIR}/ — drop your broker CSV exports there and re-run.")
        sys.exit(0)

    csv_files = list(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {DATA_DIR}/")
        print("Drop your broker CSV exports there and re-run.")
        sys.exit(0)

    # --- Step 1: Scan ---
    detections = scan_data_files(DATA_DIR)
    print(f"Found {len(detections)} CSV file(s) in data/:\n")
    for filename, broker in detections.items():
        label = broker if broker not in ("skip", "unknown") else f"({broker})"
        print(f"  {filename:<55} {label}")

    unknown = [f for f, b in detections.items() if b == "unknown"]
    if unknown:
        print(f"\n  Warning: {len(unknown)} file(s) could not be identified.")

    # --- Step 2: Rename ---
    if not args.skip_rename:
        print()
        if args.dry_run:
            print("Dry run — proposed renames:")
        else:
            print("Renaming files:")

        renames = rename_data_files(DATA_DIR, dry_run=args.dry_run)

        if not renames:
            print("  (no renames needed)")
        elif args.dry_run:
            print(f"\n  {len(renames)} file(s) would be renamed. Run without --dry-run to apply.")
            print()
            return

    # --- Step 3: Parse ---
    print("\nParsing transactions...")
    txns = parse_all_files(DATA_DIR)
    print(f"\nTotal raw transactions: {len(txns)}")

    # --- Step 4: Deduplicate ---
    txns, removed = deduplicate(txns)
    if removed:
        print(f"Removed {removed} duplicates")

    # CUSIP collision diagnostic — surfaces likely retroactive ticker
    # renames the user may want to add to cache/ticker_renames.json.
    # See src/cusips.py for the heuristic.
    from .cusips import detect_collisions
    _coll = detect_collisions(txns)
    rename_candidates = _coll.get("rename_candidates", [])
    # Filter out CUSIPs whose mapped tickers are ALL already covered by
    # existing rules so we don't nag about the rename being applied.
    from .parsers import _load_ticker_renames as _load_renames
    rules = (_load_renames() or {}).get("Robinhood", [])
    covered = {(r.get("from"), r.get("to")) for r in rules
               if isinstance(r, dict) and r.get("from") and r.get("to")}
    novel = []
    for r in rename_candidates:
        canon = r["canonical"]
        for stale in r["stale"]:
            if (stale, canon) not in covered:
                novel.append((stale, canon, r["cusip"]))
    if novel:
        print(f"  CUSIP collisions: {len(novel)} potential rename(s) "
              f"not in cache/ticker_renames.json:")
        for stale, canon, cusip in novel[:10]:
            print(f"    {stale} -> {canon}  (CUSIP {cusip})")
    print(f"Unique transactions: {len(txns)}")

    # --- Step 4a: Reconcile known custodial transfers (USAA -> Schwab Roth) ---
    txns = _reconcile_usaa_to_schwab_transfer(txns)

    # --- Step 4a-ii: Neutralize Apex-covered Robinhood CONV migration rows ---
    # Runs pre-normalization (matches the raw CONV code).  See the
    # function docstring for the double-count this prevents.
    txns = _reconcile_apex_conversions(txns)

    # --- Step 4b-pre: Load user metadata + apply account-mapping overrides ---
    # Read data/metadata.csv (or legacy data/retirement-data.csv).  Any
    # `Account Group` / `Account Type` rows the user added override the
    # built-in dicts in src/config.py — mutates the dicts in place so
    # every downstream module sees the same view.  Also returns the
    # birthday / salary / bonus / target / annual-expense data the
    # dashboard's Retirement, Planning, Income, and Tax tabs consume.
    retirement_meta = parse_metadata(DATA_DIR)
    if retirement_meta.get("account_groups"):
        ACCOUNT_GROUPS.update(retirement_meta["account_groups"])
    if retirement_meta.get("account_types"):
        ACCOUNT_TYPES.update(retirement_meta["account_types"])

    # --- Step 4b: Normalize symbols ---
    for txn in txns:
        sym = (txn.get("symbol") or "").strip()

        # Empty symbol → USD (cash movement)
        if not sym:
            sym = "USD"

        # Explicit remap (e.g. ETH2 → ETH-USD)
        sym = SYMBOL_MAP.get(sym, sym)

        # Crypto tickers get -USD suffix
        if sym in CRYPTO_SYMBOLS:
            sym = f"{sym}-USD"

        txn["symbol"] = sym

    # --- Step 4c: Add account_group and account_type ---
    for txn in txns:
        origin = txn.get("account", "")
        group = ACCOUNT_GROUPS.get(origin, origin)
        txn["account_group"] = group
        txn["account_type"] = ACCOUNT_TYPES.get(group, "Taxable")

    # --- Step 4d: Normalize actions ---
    for txn in txns:
        txn["raw_action"] = txn.get("action", "")
        txn["action"] = normalize_action(txn)

    # --- Step 4d-i: Reconcile Coinbase regular ↔ Pro intra-wallet transfers.
    # Must run AFTER normalize_action because we match on the canonical
    # action names (Transfer Out / Deposit) that normalize produces.
    txns = _reconcile_coinbase_intra_transfers(txns)

    # --- Step 4d-ii: Coinbase external bank-funding heuristic.
    # Synthesizes Deposit USD rows for Buys that must have been
    # bank-funded (no marker, but the implicit USD balance shows a
    # deficit).  Must run AFTER intra-Coinbase reconciliation so we
    # don't mistake intra-Coinbase shuffles for external funding.
    txns = _reconcile_coinbase_external_funding(txns)

    # --- Step 4e: Ensure positive qty/amount ---
    # After normalization, direction is encoded in the action.
    # Make quantity and amount always non-negative for display consistency.
    for txn in txns:
        txn["quantity"] = abs(float(txn.get("quantity", 0) or 0))
        txn["amount"] = abs(float(txn.get("amount", 0) or 0))
        txn["fees"] = abs(float(txn.get("fees", 0) or 0))

    # --- Step 4e-bis: Cost basis (FIFO, annotated onto txns in place) ---
    # Annotates each txn with cost_basis, realized_gain (sells only), and
    # basis_effect.  Returns the final lot state keyed on
    # (account_group, symbol) for merging into the holdings table below.
    # Stamp user-supplied off-platform cost basis onto matching lots
    # (metadata `Cost Basis` rows) before the walk consumes them.
    from .cost_basis_overrides import match_and_stamp as _stamp_cb
    _cb_applied, _cb_warn = _stamp_cb(txns, retirement_meta.get("cost_basis_overrides"))
    if retirement_meta.get("cost_basis_overrides"):
        print(f"  Cost-basis overrides: {_cb_applied} applied"
              + (f", {len(_cb_warn)} warning(s)" if _cb_warn else ""))
        for _w in _cb_warn:
            print(f"    ! {_w}")

    # Broker-reported disposal lots (Coinbase gain/loss report + Robinhood
    # consolidated 1099 in data/, scanner-skipped) — direct sell-consumption
    # to the exact lots the broker reported, superseding the method order
    # where the report has rows.  Robinhood needs `txns` to resolve the
    # 1099-B security name to fin's symbol.  See src/broker_lots.py.
    from .broker_lots import (load_acquisition_lots, load_disposal_lots,
                              load_robinhood_1099_income,
                              merge_auto_reconcile_rows,
                              stamp_acquisition_basis)
    disposal_lots = load_disposal_lots(DATA_DIR, txns)
    if disposal_lots:
        _n_lots = sum(len(v) for v in disposal_lots.values())
        print(f"  Broker lot report: {_n_lots} disposal lot(s) across "
              f"{len(disposal_lots)} disposal day(s) — directing lot relief")

    # Auto Reconcile Income rows from the consolidated 1099s' DIV/INT
    # sections (broker ground truth per tax year).  Hand-entered
    # Reconcile rows for the same (kind, account, year) win.
    _auto_income = load_robinhood_1099_income(DATA_DIR)
    if _auto_income:
        _before = len(retirement_meta.get("reconcile") or [])
        retirement_meta["reconcile"] = merge_auto_reconcile_rows(
            retirement_meta.get("reconcile"), _auto_income)
        _added = len(retirement_meta["reconcile"]) - _before
        if _added:
            print(f"  1099 income cross-check: {_added} auto Reconcile "
                  f"Income row(s) from consolidated 1099s")

    # Broker-reported ACQUISITION basis (Coinbase RAWTX report) — adopts
    # Coinbase's own cost basis (incl. customer-provided receives and
    # the gain-0 ETH2-deprecation rebases) onto matching lots.  Runs
    # AFTER the user's Cost Basis rows so hand-entered values win.
    _acq_rows = load_acquisition_lots(DATA_DIR)
    if _acq_rows:
        _n_st, _n_un = stamp_acquisition_basis(txns, _acq_rows)
        print(f"  Broker acquisition basis: {_n_st} lot(s) stamped from "
              f"RAWTX ({_n_un} report row(s) unmatched)")

    fifo_state = compute_basis_default(
        txns, account_methods=retirement_meta.get("lot_methods"),
        disposal_lots=disposal_lots)
    fifo_basis_by_key: dict[tuple[str, str], float] = {}
    for row in state_to_holdings(fifo_state, "fifo"):
        fifo_basis_by_key[(row["account_group"], row["symbol"])] = row["cost_basis"]

    # Annotate per-txn cash flow.  Single source of truth is
    # basis.txn_external_cash_flow; the dashboard surfaces this as a
    # column so the user can sort/sum to verify portfolio-level
    # contributions match the per-row classification.
    from .basis import txn_external_cash_flow as _cf
    for t in txns:
        cf = _cf(t)
        # Round to 2dp to keep the JSON readable; preserve the sign so
        # negative withdrawals stay negative.
        t["cash_flow"] = round(cf, 2) if cf else 0.0

    # --- Step 4f: Sort + walk balances + annotate txns ---
    # Stage extracted to src/pipeline_stages.py so the refresh-prices
    # path uses the same logic.  Includes the USD-non-Savings skip rule.
    from .pipeline_stages import walk_balances
    txns, balances = walk_balances(txns)

    # --- Step 4f: Fetch / fill price history cache ---
    # Current holdings: only symbols with non-zero balance need a current price.
    # The history module will also need prices back to the earliest txn date.
    symbols_with_balance = {sym for (_acct, sym), qty in balances.items()
                            if abs(qty) > 1e-9}
    all_symbols_ever = {t.get("symbol", "") for t in txns if t.get("symbol")}

    # Closed-position bookkeeping: derives both the per-symbol fetch-end
    # override map (for symbols whose final balance is 0 — RNAM,
    # LUNA-USD, etc.) AND the "trivial" set (corp-action artifacts like
    # ENVXW spinoff warrants under 1 share that never produce material
    # snapshot value).  Pulled into pipeline_stages so the refresh path
    # can reuse it.
    from .pipeline_stages import compute_position_endings
    closed_position_ends, trivial = compute_position_endings(txns)
    all_symbols_ever -= trivial
    if trivial:
        print(f"  Skipping price fetch for {len(trivial)} trivial symbol(s) "
              f"(max qty < 1 share, final bal = 0): "
              f"{', '.join(sorted(trivial)[:8])}"
              f"{' ...' if len(trivial) > 8 else ''}")

    # Benchmark tickers for the Performance tab / Overview overlay.
    # SPY = US large-cap, BND = US aggregate bond, VXUS = international
    # ex-US.  All fetched as total return (Adj Close) so the comparison
    # vs. the user's portfolio TWR is apples-to-apples; see
    # prices._is_total_return_symbol.
    _BENCHMARK_SYMBOLS = ("SPY", "BND", "VXUS")
    all_symbols_ever.update(_BENCHMARK_SYMBOLS)
    earliest_date = min((t.get("date", "") for t in txns if t.get("date")),
                        default="")
    today_str = datetime.now().date().isoformat()
    # Populate anchors for any scaled proxy-map entries that need them,
    # using each mapped symbol's first observed txn price.  Must run
    # BEFORE ensure_coverage so the resulting `anchor_date` is within
    # the fetch range for the proxy ticker.
    ensure_proxy_anchors(txns)
    if earliest_date:
        # Weekly deep refresh of splits + tombstones — closes the gap
        # where a stock splits AFTER its price cache is already covered
        # through today, and gives previously-tombstoned tickers
        # another chance.  Throttled internally; daily runs no-op.
        # `--refresh-caches` flag forces it.
        revalidate_stale_caches(sorted(all_symbols_ever),
                                force=args.refresh_caches)
        ensure_coverage(sorted(all_symbols_ever),
                        earliest_date, today_str,
                        symbol_end_overrides=closed_position_ends)

    # Build last_prices for holdings.  Preference:
    #   1. Cache lookup at today's date (handles buy-and-hold assets whose
    #      last transaction was years ago — far better than the stale price
    #      from that transaction).
    #   2. Most recent non-zero transaction price (fallback for symbols the
    #      cache doesn't cover — mutual funds with multi-word display names,
    #      delisted tickers, etc.).
    last_prices: dict[str, float] = {}
    for txn in txns:
        price = float(txn.get("price", 0) or 0)
        if price > 0:
            last_prices[txn.get("symbol", "")] = price
    for sym in symbols_with_balance:
        cached = get_price(sym, today_str)
        if cached is not None:
            last_prices[sym] = cached

    # Cash principal per (account, USD) for Savings-type accounts —
    # basis = deposits − withdrawals so interest reads as unrealized
    # gain.  Stage extracted to pipeline_stages.
    from .pipeline_stages import build_holdings, compute_cash_principal
    cash_principal_by_key = compute_cash_principal(txns)

    # Build holdings_by_account + holdings.  Encapsulates the dust
    # filter and the basis-source-routing rules (FIFO walker for
    # non-cash, principal for Savings cash, None otherwise).
    holdings, holdings_by_account = build_holdings(
        balances, last_prices, fifo_basis_by_key, cash_principal_by_key,
    )

    # --- Step 4f-bis: Per-method basis comparison ---
    # Walks the same txns under LIFO / HIFO / Average (in addition to the
    # FIFO walk we already did).  For each method, roll up per-holding
    # basis into totals per account_type so the dashboard can show a
    # side-by-side comparison — particularly useful for retirement
    # accounts where the tax treatment is identical but the bookkeeping
    # question "which lot method best characterises my position" is
    # meaningful.
    method_states = compute_basis_all_methods(txns)
    basis_methods: dict[str, dict] = {}
    for m, st in method_states.items():
        rows = state_to_holdings(st, m)
        # Per-holding unrealized using today's cache price
        for r in rows:
            px = last_prices.get(r["symbol"], 0.0)
            value = round(r["quantity"] * px, 2) if px else None
            r["price"] = round(px, 2)
            r["value"] = value
            r["account_type"] = ACCOUNT_TYPES.get(r["account_group"], "Taxable")
            r["unrealized_gain"] = (round(value - r["cost_basis"], 2)
                                    if value is not None else None)
        # Sum only the positions that have a known value; count the ones
        # we couldn't price so the dashboard can note it.
        totals_by_type: dict[str, dict] = {}
        for r in rows:
            t = totals_by_type.setdefault(r["account_type"], {
                "cost_basis": 0.0, "value": 0.0, "unrealized_gain": 0.0,
                "unpriced_count": 0,
            })
            t["cost_basis"] += r["cost_basis"]
            if r["value"] is None:
                t["unpriced_count"] += 1
            else:
                t["value"] += r["value"]
                if r["unrealized_gain"] is not None:
                    t["unrealized_gain"] += r["unrealized_gain"]
        totals_all = {
            "cost_basis":      round(sum(r["cost_basis"] for r in rows), 2),
            "value":           round(sum((r["value"] or 0) for r in rows), 2),
            "unrealized_gain": round(sum((r["unrealized_gain"] or 0) for r in rows), 2),
            "realized_gain":   round(st["realized_total"], 2),
            "unpriced_count":  sum(1 for r in rows if r["value"] is None),
        }
        totals_by_type_out = {}
        for k, t in totals_by_type.items():
            totals_by_type_out[k] = {
                "cost_basis":      round(t["cost_basis"], 2),
                "value":           round(t["value"], 2),
                "unrealized_gain": round(t["unrealized_gain"], 2),
                "unpriced_count":  t["unpriced_count"],
            }
        basis_methods[m] = {
            "holdings":        rows,
            "totals":          totals_all,
            "totals_by_type":  totals_by_type_out,
        }

    # Fold cash holdings (Apple Savings USD) into every method's
    # totals.  The basis walker skips USD; without this fold, the
    # dashboard's headline Cost Basis / Value / Unrealized P&L cards
    # would miss the HYSA's full return.
    from .pipeline_stages import fold_cash_into_basis_methods
    fold_cash_into_basis_methods(basis_methods, holdings_by_account)

    # --- Step 4f-ter: Cash-flow summary for header stat cards ---
    cash = compute_cash_summary(txns)

    # --- Step 4f-quater: Personal retirement metadata ---
    # Already loaded above in step 4b-pre (we needed it earlier to
    # apply Account Group / Account Type overrides before step 4c).
    # Re-binding here is just a defensive no-op so the variable name
    # stays in scope for the export call below.
    pass

    # --- Step 4g: Sector enrichment (cache-first, yfinance fallback) ---
    enrich_holdings(holdings)
    enrich_holdings(holdings_by_account, verbose=False)  # same symbols, skip dup log

    # sector_of needs to cover every symbol that ever appeared in txns —
    # not just current holdings — so historical snapshots can classify
    # now-exited positions correctly (and so the dashboard can look up
    # sectors for historical-only symbols when filtering by as-of-date).
    # get_sector() is cache-first, so this is cheap after the first run.
    from .sectors import get_sector
    all_symbols = {t.get("symbol", "") for t in txns if t.get("symbol")}
    sector_of = {sym: get_sector(sym) for sym in all_symbols}
    save_sector_cache()

    # --- Step 4h: Portfolio value time series ---
    # account_methods: the snapshot lot walker must consume lots in the
    # same per-account order (FIFO/LIFO/HIFO) as the annotated basis walk,
    # or the latest snapshot's cost basis drifts from the holdings table.
    print("\nComputing portfolio history...")
    history = compute_history(txns, sector_of,
                              account_methods=retirement_meta.get("lot_methods"),
                              disposal_lots=disposal_lots)
    save_price_cache()
    print(f"  {len(history)} snapshot(s) from {history[0]['date'] if history else '—'} "
          f"to {history[-1]['date'] if history else '—'}")

    # --- Step 4i: Derived analytics (single source of truth) ---
    # Rollover bridges, retirement contributions by year, per-account
    # annual returns + TWR + SPY benchmark — everything the dashboard
    # used to compute in JS.  Centralised so every consumer agrees.
    print("Computing analytics...")
    analytics = build_analytics(txns, history, holdings, holdings_by_account,
                                 retirement_meta,
                                 cash_summary=cash,
                                 basis_methods=basis_methods,
                                 fifo_state=fifo_state)
    perf = analytics["performance_by_filter"]
    opt = analytics["options"]["stats"]
    tax = analytics["tax"]
    print(f"  {len(analytics['rollover_bridges'])} rollover bridge(s), "
          f"{len(perf)} account filter(s), "
          f"{opt['trades']} option trades ({opt['open_count']} open), "
          f"{len(tax['realized_by_symbol'])} symbols with realized gain")

    # --- Step 5: Export JSON ---
    output_path = args.output or (EXPORT_DIR / "transactions.json")
    from pathlib import Path
    output_path = Path(output_path)
    export_json(txns, output_path, holdings=holdings,
                holdings_by_account=holdings_by_account,
                history=history,
                basis_methods=basis_methods,
                cash_summary=cash,
                retirement_meta=retirement_meta,
                analytics=analytics,
                sector_of=sector_of,
                display_of=build_display_map())
    print(f"\nExported {len(txns)} transactions to {output_path}")

    # --- Step 6: Dashboard ---
    dashboard_path = output_path.parent / "dashboard.html"
    generate_dashboard(output_path, dashboard_path)
    print(f"Dashboard: {dashboard_path}")

    _maybe_assert_invariants(txns, holdings_by_account, history, analytics,
                             cash)


if __name__ == "__main__":
    main()
