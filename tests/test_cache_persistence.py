"""Cache saves preserve last-good state and retry after ordinary failures.

All files and figures are independently constructed in isolated_workdir.
Failures are injected at serialization and actual filesystem mutation seams.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


DAY = "2024-01-03"
NEXT_DAY = "2024-01-04"


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _disk_contents(cache_dir):
    return {
        path.relative_to(cache_dir): path.read_bytes()
        for path in cache_dir.rglob("*") if path.is_file()
    }


def _pending(prices):
    return (
        set(prices._prices_dirty_syms), set(prices._prices_deleted_syms),
        prices._meta_dirty, prices._splits_dirty, prices._dividends_dirty,
        prices._proxy_dirty, prices._legacy_prices_pending_delete,
    )


@pytest.fixture
def cached_prices(isolated_workdir):
    from src import prices

    for symbol, close in (("AAA", 100.0), ("BBB", 200.0)):
        _write_json(prices._shard_path(symbol), {
            "symbol": symbol, "prices": {DAY: close},
        })
    _write_json(prices.PRICE_META_FILE, {
        "version": 1, "auto_adjusted": False,
        "migrated_v1": True, "migrated_tr_close_v2": True,
        "symbols": {
            symbol: {"covered_start": DAY, "covered_end": DAY,
                     "settled_through": DAY}
            for symbol in ("AAA", "BBB")
        },
    })
    _write_json(prices.SPLITS_CACHE_FILE, {"AAA": [], "BBB": []})
    _write_json(prices.DIVIDENDS_CACHE_FILE, {"AAA": [[DAY, 0.5]]})
    _write_json(prices.PROXY_MAP_FILE, {
        "FICTIONAL ALIAS": {"proxy": "AAA", "method": "direct"},
    })
    prices._load_prices()
    prices._load_splits()
    prices._load_dividends()
    prices._load_proxy_map()
    return prices


@pytest.fixture
def changed_prices(cached_prices):
    prices = cached_prices
    prices._prices["AAA"][NEXT_DAY] = 110.25
    prices._mark_prices_dirty("AAA")
    prices._prices["CCC"] = {NEXT_DAY: 30.0}
    prices._mark_prices_dirty("CCC")
    del prices._prices["BBB"]
    prices._mark_prices_deleted("BBB")
    prices._meta["symbols"]["AAA"].update({
        "covered_end": NEXT_DAY, "settled_through": NEXT_DAY,
    })
    prices._meta["symbols"].pop("BBB")
    prices._meta_dirty = True
    prices._splits["CCC"] = []
    prices._splits_dirty = True
    prices._dividends["AAA"].append([NEXT_DAY, 0.75])
    prices._dividends_dirty = True
    prices._proxy["FICTIONAL ALIAS"]["display"] = "Fictional Fund"
    prices._proxy_dirty = True
    return prices


def _assert_saved_changes(prices):
    assert not prices._shard_path("BBB").exists()
    assert json.loads(prices._shard_path("AAA").read_text())["prices"] == {
        DAY: 100.0, NEXT_DAY: 110.25,
    }
    assert json.loads(prices._shard_path("CCC").read_text())["prices"] == {
        NEXT_DAY: 30.0,
    }
    for path, expected in (
        (prices.PRICE_META_FILE, prices._meta),
        (prices.SPLITS_CACHE_FILE, prices._splits),
        (prices.DIVIDENDS_CACHE_FILE, prices._dividends),
        (prices.PROXY_MAP_FILE, prices._proxy),
    ):
        assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert _pending(prices) == (set(), set(), False, False, False, False, False)
    prices.reset_caches()
    assert prices.get_price("AAA", NEXT_DAY) == 110.25
    assert prices.get_price("CCC", NEXT_DAY) == 30.0
    assert prices.get_price("BBB", DAY) is None


@pytest.mark.parametrize("component", [
    "prices", "meta", "splits", "dividends", "proxy",
])
@pytest.mark.parametrize("bad", [object(), float("nan"), float("inf"), -float("inf")],
                         ids=["unserializable", "nan", "infinity", "negative-infinity"])
def test_serialization_failure_preserves_complete_cache_and_can_retry(
        changed_prices, component, bad):
    prices = changed_prices
    before = _disk_contents(prices.PRICES_DIR.parent)
    pending = _pending(prices)
    if component == "prices":
        target, key = prices._prices["AAA"], NEXT_DAY
    else:
        target, key = getattr(prices, "_" + component), "invalid"
    previous = target.get(key)
    target[key] = bad

    with pytest.raises((TypeError, ValueError)):
        prices.save_caches()

    assert _disk_contents(prices.PRICES_DIR.parent) == before
    assert _pending(prices) == pending
    if component == "prices":
        target[key] = previous
    else:
        del target[key]
    prices.save_caches()
    _assert_saved_changes(prices)


def test_late_replacement_failure_rolls_back_all_files_and_can_retry(
        changed_prices, monkeypatch):
    prices = changed_prices
    before = _disk_contents(prices.PRICES_DIR.parent)
    pending = _pending(prices)
    real_replace = os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(destination) == prices.PROXY_MAP_FILE and not failed:
            failed = True
            raise OSError("fictional late replacement failure")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_once)
        with pytest.raises(OSError, match="fictional late"):
            prices.save_caches()

    assert failed
    assert _disk_contents(prices.PRICES_DIR.parent) == before
    assert _pending(prices) == pending
    prices.save_caches()
    _assert_saved_changes(prices)


def test_failed_shard_deletion_rolls_back_sidecars_and_can_retry(
        changed_prices, monkeypatch):
    prices = changed_prices
    before = _disk_contents(prices.PRICES_DIR.parent)
    pending = _pending(prices)
    real_unlink = Path.unlink
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path == prices._shard_path("BBB") and not failed:
            failed = True
            raise OSError("fictional shard deletion failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_once)
        with pytest.raises(OSError, match="fictional shard"):
            prices.save_caches()

    assert failed
    assert _disk_contents(prices.PRICES_DIR.parent) == before
    assert _pending(prices) == pending
    prices.save_caches()
    _assert_saved_changes(prices)


def test_failed_split_invalidation_does_not_mix_price_and_split_bases(
        cached_prices, monkeypatch):
    prices = cached_prices
    before = _disk_contents(prices.PRICES_DIR.parent)
    new_splits = [[NEXT_DAY, 2.0]]
    assert prices._invalidate_prices_for_split_change(
        "AAA", [], new_splits, verbose=False)
    prices._splits["AAA"] = new_splits
    prices._splits_dirty = True
    real_replace = os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(destination) == prices.SPLITS_CACHE_FILE and not failed:
            failed = True
            raise OSError("fictional split replacement failure")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_once)
        with pytest.raises(OSError, match="fictional split"):
            prices.save_caches()

    assert _disk_contents(prices.PRICES_DIR.parent) == before
    prices.save_caches()
    prices.reset_caches()
    assert prices.get_price("AAA", DAY) is None
    assert prices._load_splits()["AAA"] == new_splits
    entry = prices._load_meta()["symbols"]["AAA"]
    assert not {"covered_start", "covered_end", "settled_through"} & entry.keys()
    assert prices.get_price("BBB", DAY) == 200.0


@pytest.mark.parametrize("legacy", [{}, {"AAA": {DAY: 100.0}, "BBB": {DAY: 200.0}}],
                         ids=["empty", "with-prices"])
def test_failed_legacy_retirement_preserves_migration_and_can_retry(
        isolated_workdir, monkeypatch, legacy):
    from src import prices

    _write_json(prices.PRICE_CACHE_FILE, legacy)
    prices._load_prices()
    before = _disk_contents(prices.PRICES_DIR.parent)
    pending = _pending(prices)
    real_unlink = Path.unlink
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path == prices.PRICE_CACHE_FILE and not failed:
            failed = True
            raise OSError("fictional legacy retirement failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_once)
        with pytest.raises(OSError, match="fictional legacy"):
            prices.save_caches()

    assert failed
    assert _disk_contents(prices.PRICES_DIR.parent) == before
    assert _pending(prices) == pending
    prices.save_caches()
    assert not prices.PRICE_CACHE_FILE.exists()
    prices.reset_caches()
    assert prices._load_prices() == legacy


def test_already_missing_deleted_shard_does_not_block_save(changed_prices):
    prices = changed_prices
    prices._shard_path("BBB").unlink()
    prices.save_caches()
    _assert_saved_changes(prices)


@pytest.mark.parametrize("body", ["", '{"AAA":', "[]", "null"])
def test_unreadable_legacy_is_preserved_during_other_cache_writes(
        isolated_workdir, body):
    from src import prices

    prices.PRICE_CACHE_FILE.write_text(body, encoding="utf-8")
    prices._load_prices()["AAA"] = {DAY: 100.0}
    prices._mark_prices_dirty("AAA")
    prices.save_caches()

    assert prices.PRICE_CACHE_FILE.read_text(encoding="utf-8") == body
    assert json.loads(prices._shard_path("AAA").read_text())["prices"] == {
        DAY: 100.0,
    }


def test_clean_cache_save_does_not_replace_files(changed_prices, monkeypatch):
    prices = changed_prices
    prices.save_caches()
    before = _disk_contents(prices.PRICES_DIR.parent)

    def unexpected_replace(*args, **kwargs):
        pytest.fail("a clean cache save attempted file replacement")

    monkeypatch.setattr(os, "replace", unexpected_replace)
    prices.save_caches()
    assert _disk_contents(prices.PRICES_DIR.parent) == before


@pytest.mark.parametrize("loader", [
    "_load_prices", "_load_meta", "_load_splits", "_load_dividends",
    "_load_proxy_map", "sector",
])
def test_pending_recovery_blocks_cold_cache_reads(cached_prices, loader):
    from src import sectors

    prices = cached_prices
    prices.reset_caches()
    sectors.reset_cache()
    marker = prices.PRICES_DIR.parent / ".fin-recovery.json"
    marker.write_text("{}", encoding="utf-8")
    before = _disk_contents(prices.PRICES_DIR.parent)
    load = sectors._load_cache if loader == "sector" else getattr(prices, loader)

    with pytest.raises(ValueError, match="recover"):
        load()

    assert _disk_contents(prices.PRICES_DIR.parent) == before


@pytest.mark.parametrize("failure", ["serialization", "nan", "replacement"])
def test_sector_save_preserves_last_good_file_and_retries(
        isolated_workdir, monkeypatch, failure):
    from src import sectors

    _write_json(sectors.SECTOR_CACHE_FILE, {"AAA": "Technology"})
    sectors._load_cache()["BBB"] = "Healthcare"
    sectors._dirty = True
    before = _disk_contents(sectors.SECTOR_CACHE_FILE.parent)
    if failure in ("serialization", "nan"):
        sectors._cache["BBB"] = object() if failure == "serialization" else float("nan")

    real_replace = os.replace
    failed = False

    def fail_replace(source, destination):
        nonlocal failed
        if Path(destination) == sectors.SECTOR_CACHE_FILE and not failed:
            failed = True
            raise OSError("fictional sector replacement failure")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        if failure == "replacement":
            patch.setattr(os, "replace", fail_replace)
        with pytest.raises((TypeError, ValueError, OSError)):
            sectors.save_cache()

    assert _disk_contents(sectors.SECTOR_CACHE_FILE.parent) == before
    assert sectors._dirty
    sectors._cache["BBB"] = "Healthcare"
    sectors.save_cache()
    assert not sectors._dirty
    sectors.reset_cache()
    assert sectors._load_cache() == {"AAA": "Technology", "BBB": "Healthcare"}
