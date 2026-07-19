"""Test infrastructure: temp directories, yfinance stubbing, helpers.

The fin pipeline reaches out to yfinance for sector lookups and price
fetching.  Tests should never make real network calls — every test
gets a `stub_prices` fixture that injects deterministic prices into
the cache files in a tmp-managed working directory.

The price/sector caches and the parsers' module-level state both rely
on globals, so the `isolated_workdir` fixture sets `cwd` to a tmp dir
with empty caches *before* importing fin modules, and resets module
caches between tests.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from pathlib import Path

import pytest


# Make `src/` importable as a package
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Session-wide isolation guard — must run BEFORE any src module is imported.
#
# fin's config.py resolves DATA_DIR / CACHE_DIR / EXPORT_DIR from FIN_*_DIR
# env vars AT IMPORT TIME.  Most tests opt into `isolated_workdir`, which
# redirects those per-test — but a test that imports a src module without
# the fixture (module-level imports at collection time count too) resolves
# the REAL repo dirs and can silently write there.  That actually happened:
# tests calling build_analytics() with bare inputs clobbered the real
# cache/last_run.json with an empty snapshot, so the dashboard's "What's
# Changed" panel reported the entire portfolio value as new on every real
# run after a pytest run.
#
# conftest.py is imported before collection, so pointing the env vars at a
# session tmp dir here guarantees no test touches the real dirs even when
# it skips the fixture.  `isolated_workdir` still narrows to a per-test
# dir on top of this.  Assignment is unconditional: ambient FIN_*_DIR vars
# from the developer's shell must never leak into a test run either.
import tempfile

_GUARD_ROOT = Path(tempfile.mkdtemp(prefix="fin-test-guard-"))
for _sub in ("data", "cache", "exports"):
    (_GUARD_ROOT / _sub).mkdir()
os.environ["FIN_PROJECT_ROOT"] = str(_GUARD_ROOT)
os.environ["FIN_DATA_DIR"]     = str(_GUARD_ROOT / "data")
os.environ["FIN_CACHE_DIR"]    = str(_GUARD_ROOT / "cache")
os.environ["FIN_EXPORT_DIR"]   = str(_GUARD_ROOT / "exports")


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_workdir(tmp_path, monkeypatch):
    """Run a test in a clean working directory.

    Creates `data/`, `cache/`, `exports/` under tmp_path, sets env
    vars so fin config resolves to those paths, and resets fin
    module-level caches so each test starts fresh.  Yields tmp_path.
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "cache").mkdir()
    (tmp_path / "exports").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FIN_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FIN_DATA_DIR",   str(tmp_path / "data"))
    monkeypatch.setenv("FIN_CACHE_DIR",  str(tmp_path / "cache"))
    monkeypatch.setenv("FIN_EXPORT_DIR", str(tmp_path / "exports"))
    # Tests run with invariant assertions enabled — any high-severity
    # data-health violation becomes an immediate test failure rather
    # than a silently-displayed dashboard panel.  Catches refactor
    # regressions at the point of introduction.
    monkeypatch.setenv("FIN_ASSERT_INVARIANTS", "1")

    # Reload fin modules so they pick up the new paths and start with
    # empty cache state.
    _reset_fin_modules()

    # Account Group / Account Type mappings live in data/metadata.csv
    # in production.  Tests that call src functions directly (without
    # going through main()'s metadata loader) need the dicts seeded
    # with sane defaults — otherwise every "is this a Savings cash
    # position?" gate fails because ACCOUNT_TYPES.get(acct) returns
    # None.  Pipeline-level tests (synthetic_pipeline) write their
    # own metadata.csv which then mutates the same dicts in place.
    from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
    ACCOUNT_GROUPS.update({
        "Robinhood": "Robinhood",
        "Coinbase": "Coinbase",
        "Coinbase Pro": "Coinbase",
        "Schwab Rollover IRA": "Rollover IRA",
        "Schwab Roth IRA": "Roth IRA",
        "Schwab Roth Contributory IRA": "Roth IRA",
        "Vanguard 401K": "401K",
        "Voya 401K": "Rollover IRA",
        "USAA Roth IRA": "Roth IRA",
        "USAA Victory Capital Roth IRA": "Roth IRA",
        "Apple Savings": "Apple Savings",
    })
    ACCOUNT_TYPES.update({
        "Robinhood": "Taxable",
        "Coinbase": "Taxable",
        "Rollover IRA": "Retirement",
        "Roth IRA": "Retirement",
        "401K": "Retirement",
        "Apple Savings": "Savings",
    })

    yield tmp_path

    _reset_fin_modules()


def _reset_fin_modules() -> None:
    """Reset in-memory cache state between tests.

    Fin uses FIN_*_DIR env vars to pick up per-test tmp dirs, but
    modules cache the resolved paths as ``CACHE_DIR`` / ``DATA_DIR``
    constants at import time — so we have to drop everything from
    ``sys.modules`` to force re-import with the new env values.
    Explicit ``reset_*()`` helpers on the stateful modules do the
    in-memory cache clearing where possible; ``sys.modules`` cleanup
    handles the captured-at-import-time path constants.
    """
    # Stateful modules with explicit reset helpers — call these first
    # in case any previous test left dirty state that would get written
    # to disk on save.
    for name, reset_fn in (
        ("src.prices",           "reset_caches"),
        ("src.sectors",          "reset_cache"),
        ("src.parsers._helpers", "reset_ticker_renames_cache"),
    ):
        mod = sys.modules.get(name)
        if mod is not None:
            fn = getattr(mod, reset_fn, None)
            if fn is not None:
                fn()

    # Then drop all fin modules from the import cache so the next
    # test's imports re-resolve paths from the (new) env vars.
    targets = [m for m in list(sys.modules) if m == "src" or m.startswith("src.")]
    for m in targets:
        del sys.modules[m]


# ---------------------------------------------------------------------------
# Network stubs — never hit yfinance in tests
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_prices(monkeypatch, isolated_workdir):
    """Stub the yfinance-backed price fetch with deterministic data.

    Returns a callable that test bodies use to register prices:

        stub_prices.set("AAPL", {"2024-01-15": 185.0, "2024-04-30": 195.0})

    Any symbol not registered returns "no data" (yfinance behaviour for
    unknown / delisted tickers).  Sector lookups stub to "Other" for
    unregistered symbols.
    """
    # Pre-load fin.prices and override its private fetch / yfinance entry
    from src import prices as _prices_mod
    from src import sectors as _sectors_mod

    registered: dict[str, dict[str, float]] = {}
    sectors_registered: dict[str, str] = {}

    def fake_fetch_range(symbol, start, end):
        return dict(registered.get(symbol, {}))

    def fake_fetch_splits(symbol):
        return []

    def fake_fetch_dividends(symbol):
        return []

    def fake_batch_fetch_ranges(symbols, start, end):
        # Mirror the real contract: only symbols with data appear;
        # split_event None = unknown (caller does the full splits
        # compare via the stubbed _fetch_splits).
        out = {}
        for sym in symbols:
            data = fake_fetch_range(sym, start, end)
            if data:
                out[sym] = {"prices": data, "split_event": None}
        return out

    def fake_get_sector(symbol):
        from src.sectors import _classify_no_fetch
        quick = _classify_no_fetch(symbol)
        if quick is not None:
            return quick
        return sectors_registered.get(symbol, "Other")

    monkeypatch.setattr(_prices_mod, "_fetch_range", fake_fetch_range)
    monkeypatch.setattr(_prices_mod, "_fetch_splits", fake_fetch_splits)
    monkeypatch.setattr(_prices_mod, "_fetch_dividends", fake_fetch_dividends)
    monkeypatch.setattr(_prices_mod, "_batch_fetch_ranges", fake_batch_fetch_ranges)
    monkeypatch.setattr(_sectors_mod, "get_sector", fake_get_sector)

    class _StubAPI:
        def set(self, symbol: str, prices: dict[str, float]) -> None:
            registered[symbol] = dict(prices)

        def set_sector(self, symbol: str, sector: str) -> None:
            sectors_registered[symbol] = sector

        @property
        def registered(self) -> dict[str, dict[str, float]]:
            return registered

    return _StubAPI()


# ---------------------------------------------------------------------------
# CSV fixture writers
# ---------------------------------------------------------------------------

def write_robinhood_csv(path: Path, rows: list[dict]) -> None:
    """Write a Robinhood-format CSV.  `rows` are dicts with keys matching
    the broker's headers; missing keys are blanked.
    """
    headers = [
        "Activity Date", "Process Date", "Settle Date", "Instrument",
        "Description", "Trans Code", "Quantity", "Price", "Amount",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def write_voya_csv(path: Path, rows: list[dict]) -> None:
    """Write a Voya 401K-format CSV with the multi-line preamble Voya
    actually emits."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("123456 Test 401(k) Savings Plan\n")
        f.write("Date Range :\t01/01/2020 - 01/01/2026\n")
        f.write('"\n"\n"\n"\n')
        headers = ["Activity Date", "Activity", "Fund", "Money Source",
                   "# of Units", "Unit Price", "Amount"]
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def write_apple_savings_csv(path: Path, rows: list[dict]) -> None:
    """Write an Apple Savings-format CSV."""
    headers = ["Transaction Date", "Clearing Date", "Description", "Merchant",
               "Category", "Type", "Amount (USD)", "Balance (USD)"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})
