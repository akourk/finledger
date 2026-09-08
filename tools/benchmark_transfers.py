"""Measure transfer pairing and lot/history walks with fictional inputs only.

Run ``python -m tools.benchmark_transfers --cycles 250 500 1000 --symbols 1 8``.
Compare elapsed times and semantic digests across checkouts. Prices are constant,
the date is fixed, and all financial paths live in a fresh temporary directory.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import types


FIRST = date(2020, 1, 1)
AS_OF = date(2025, 12, 31)
GROUPS = ("Fictional Alpha", "Fictional Zeta")


def transactions(cycles: int, symbols: int) -> list[dict]:
    """Each cycle buys ten shares, moves five, and sells two at destination."""
    rows = []
    for index in range(cycles):
        symbol = f"FICT{index % symbols}"
        day = FIRST + timedelta(days=index * 2)
        price = 80.0 + index % 11
        for offset, group, action, qty, unit_price in (
            (0, GROUPS[0], "Buy", 10, price),
            (1, GROUPS[0], "Transfer Out", 5, price),
            (2, GROUPS[1], "Transfer In", 5, price),
            (3, GROUPS[1], "Sell", 2, 100.0),
        ):
            rows.append({"date": (day + timedelta(days=offset)).isoformat(),
                         "account": group, "account_group": group,
                         "account_type": "Taxable", "symbol": symbol,
                         "action": action, "raw_action": action, "quantity": qty,
                         "price": unit_price, "amount": qty * unit_price,
                         "fees": 0.0, "description": "", "source": "fictional.csv",
                         "seq": len(rows)})
    return rows


def _walk(rows: list[dict]) -> tuple[str, int]:
    from src import basis, history

    annotated = copy.deepcopy(rows)
    state = basis.compute_basis_default(annotated)
    methods = basis.compute_basis_all_methods(annotated)
    snapshots = history.compute_history(
        annotated, {t["symbol"]: "Fictional equities" for t in annotated})
    holdings = basis.state_to_holdings(state, "fifo")
    final_history = [{key: p[key] for key in
                      ("account_group", "symbol", "quantity", "cost_basis")}
                     for p in snapshots[-1]["positions"]]
    order = lambda p: (p["account_group"], p["symbol"])
    if sorted(holdings, key=order) != sorted(final_history, key=order):
        raise AssertionError("fictional basis/history holdings disagree")
    output = {"annotations": annotated, "holdings": holdings,
              "methods": {method: {"holdings": basis.state_to_holdings(value, method),
                                   "realized": value["realized_total"]}
                          for method, value in methods.items()}, "history": snapshots}
    digest = hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest()
    return digest, len(snapshots)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, nargs="+", default=[500])
    parser.add_argument("--symbols", type=int, nargs="+", default=[8])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if any(n < 1 or n > 1000 for n in args.cycles):
        parser.error("cycles must be between 1 and 1000 to stay within the fixed calendar")
    if any(n < 1 or n > 32 for n in args.symbols) or not 1 <= args.repeats <= 20:
        parser.error("symbols must be between 1 and 32; repeats between 1 and 20")
    if "src.config" in sys.modules:
        raise RuntimeError("benchmark requires a fresh Python process")
    with tempfile.TemporaryDirectory(prefix="fin-fictional-benchmark-") as name:
        work = Path(name)
        for child in ("data", "cache", "exports"):
            (work / child).mkdir()
        os.environ.update({"FIN_PROJECT_ROOT": str(work),
                           "FIN_DATA_DIR": str(work / "data"),
                           "FIN_CACHE_DIR": str(work / "cache"),
                           "FIN_EXPORT_DIR": str(work / "exports"),
                           "FIN_AS_OF_DATE": AS_OF.isoformat()})
        attempts = []

        def deny_network(event, _args):
            if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
                attempts.append(event)
                raise RuntimeError("network access is forbidden in the fictional benchmark")

        sys.addaudithook(deny_network)
        yf = types.ModuleType("yfinance")

        def deny_yfinance(attribute):
            attempts.append("yfinance." + attribute)
            raise RuntimeError("fictional benchmark cannot fetch market data")

        yf.__getattr__ = deny_yfinance
        sys.modules["yfinance"] = yf
        from src import basis, prices
        from src.config import ACCOUNT_TYPES

        ACCOUNT_TYPES.update(dict.fromkeys(GROUPS, "Taxable"))
        days = [(FIRST + timedelta(days=n)).isoformat()
                for n in range((AS_OF - FIRST).days + 1)]
        prices_dir = work / "cache" / "prices"
        prices_dir.mkdir()
        for symbol in [f"FICT{i}" for i in range(max(args.symbols))] + ["SPY", "BND", "VXUS"]:
            (prices_dir / f"{symbol}.json").write_text(
                json.dumps(dict.fromkeys(days, 100.0)), encoding="utf-8")
        for filename in ("splits_cache.json", "dividends_cache.json", "symbol_proxy_map.json"):
            (work / "cache" / filename).write_text("{}", encoding="utf-8")
        prices._load_prices()  # Exclude initial cache I/O from the measured walks.
        print(json.dumps({"python": sys.version.split()[0], "as_of": AS_OF.isoformat(),
                          "repeats": args.repeats, "timing": "median wall seconds"}))
        for symbols in args.symbols:
            for cycles in args.cycles:
                rows = transactions(cycles, symbols)
                pair_times, walk_times, digests = [], [], set()
                for _ in range(args.repeats):
                    start = time.perf_counter()
                    pairs = basis._pair_transfers(rows)
                    pair_times.append(time.perf_counter() - start)
                    if len(pairs["tin_to_tout"]) != cycles:
                        raise AssertionError("fictional transfer pair is missing")
                    start = time.perf_counter()
                    digest, samples = _walk(rows)
                    walk_times.append(time.perf_counter() - start)
                    digests.add(digest)
                if len(digests) != 1 or attempts:
                    raise AssertionError("benchmark output changed or attempted network access")
                print(json.dumps({"cycles": cycles, "rows": len(rows), "symbols": symbols,
                                  "samples": samples, "pair_seconds": statistics.median(pair_times),
                                  "five_basis_plus_history_seconds": statistics.median(walk_times),
                                  "sha256": digest}), flush=True)


if __name__ == "__main__":
    main()
