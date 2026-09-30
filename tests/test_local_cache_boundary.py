"""Local sidecars keep their paths, empty defaults, and legacy evidence.

Every cache below is independently fictional and created under isolated FIN
directories. The public mapping example is a format aid, never a runtime seed.
Existing persistence tests cover failed replacement of saved files and recovery;
these tests add the first-save boundary where no previous file exists.
"""

import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "samples" / "symbol_proxy_map.example.json"
FUND = "Fictional Example Fund"
DAY = "2024-01-03"
NEXT_DAY = "2024-01-04"
LATER_DAY = "2024-01-05"


@pytest.fixture
def local_sidecars(isolated_workdir):
    from src import prices, sectors

    # Filename compatibility matters independently of any Git tracking choice.
    return {
        "dividends_cache.json": (prices, prices.DIVIDENDS_CACHE_FILE,
                                 prices._load_dividends, "_dividends_dirty", prices.save_caches),
        "sector_cache.json": (sectors, sectors.SECTOR_CACHE_FILE,
                              sectors._load_cache, "_dirty", sectors.save_cache),
        "splits_cache.json": (prices, prices.SPLITS_CACHE_FILE,
                             prices._load_splits, "_splits_dirty", prices.save_caches),
        "symbol_proxy_map.json": (prices, prices.PROXY_MAP_FILE,
                                 prices._load_proxy_map, "_proxy_dirty", prices.save_caches),
    }


def _fictional_documents():
    return {
        "dividends_cache.json": {"SPY": [[LATER_DAY, 0.75]]},
        "sector_cache.json": {"FICT": "Technology"},
        "splits_cache.json": {"FICT": [[LATER_DAY, 2.0]]},
        "symbol_proxy_map.json": {
            "_comment": "Independently fictional local configuration",
            FUND: {"proxy": "SPY", "method": "scaled", "display": "Local fictional fund",
                   "anchor_date": DAY, "anchor_price": 25.0},
        },
    }


def _write_documents(local_sidecars, documents):
    for name, document in documents.items():
        path = local_sidecars[name][1]
        # Unusual formatting makes accidental parse-and-rewrite detectable.
        path.write_text(json.dumps(document, indent=1) + "\n\n", encoding="utf-8")


def _disk_bytes(cache):
    return {path.relative_to(cache): path.read_bytes()
            for path in cache.rglob("*") if path.is_file()}


def test_missing_sidecars_start_empty_without_creating_defaults(
        local_sidecars, isolated_workdir):
    from src import prices, sectors

    cache = isolated_workdir / "cache"
    for name, (_, path, load, _, _) in local_sidecars.items():
        assert path == cache / name
        assert not path.exists()
        assert load() == {}
    assert prices.build_display_map() == {}
    assert prices.split_factor_since("FICT", DAY) == 1.0
    assert prices.get_price(FUND, DAY) is None
    assert sectors.get_sector("USD") == "Cash"
    prices.ensure_proxy_anchors([{"symbol": FUND, "date": DAY, "price": 25}], verbose=False)
    prices.save_caches()
    sectors.save_cache()
    assert _disk_bytes(cache) == {}


@pytest.mark.parametrize("missing", [
    "dividends_cache.json", "sector_cache.json", "splits_cache.json", "symbol_proxy_map.json",
])
def test_missing_one_sidecar_does_not_seed_or_rewrite_the_others(
        local_sidecars, isolated_workdir, missing):
    from src import prices, sectors

    documents = _fictional_documents()
    del documents[missing]
    _write_documents(local_sidecars, documents)
    before = _disk_bytes(isolated_workdir / "cache")
    for name, (_, _, load, _, _) in local_sidecars.items():
        assert load() == documents.get(name, {})
    prices.save_caches()
    sectors.save_cache()
    assert _disk_bytes(isolated_workdir / "cache") == before
    assert not local_sidecars[missing][1].exists()


@pytest.mark.parametrize("existing_local", [False, True])
def test_public_example_is_never_loaded_merged_or_copied_implicitly(
        local_sidecars, isolated_workdir, existing_local):
    from src import prices

    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    mappings = {name: entry for name, entry in example.items() if not name.startswith("_")}
    assert mappings == {FUND: {"proxy": "SPY", "method": "scaled"}}
    assert "anchor_date" not in example[FUND] and "anchor_price" not in example[FUND]
    samples = isolated_workdir / "samples"
    samples.mkdir()
    (samples / EXAMPLE.name).write_bytes(EXAMPLE.read_bytes())
    expected = _fictional_documents()["symbol_proxy_map.json"] if existing_local else {}
    if existing_local:
        _write_documents(local_sidecars, {"symbol_proxy_map.json": expected})
    before = _disk_bytes(isolated_workdir / "cache")

    assert prices._load_proxy_map() == expected
    assert prices.build_display_map() == ({FUND: "Local fictional fund"} if existing_local else {})
    prices.ensure_proxy_anchors([{"symbol": FUND, "date": NEXT_DAY, "price": 30}], verbose=False)
    prices.save_caches()
    assert prices._load_proxy_map() == expected
    assert _disk_bytes(isolated_workdir / "cache") == before


def test_legacy_local_anchors_and_sidecar_bytes_survive_reads_and_cold_reload(
        local_sidecars, isolated_workdir):
    from src import prices, sectors

    documents = _fictional_documents()
    _write_documents(local_sidecars, documents)
    before = _disk_bytes(isolated_workdir / "cache")
    for name, (_, _, load, _, _) in local_sidecars.items():
        assert load() == documents[name]
    # Supply fictional closes in memory; no market lookup or shard migration.
    prices._load_prices()["SPY"] = {DAY: 100.0, NEXT_DAY: 110.0, LATER_DAY: 120.0}
    assert prices.get_price(FUND, NEXT_DAY) == pytest.approx(27.5)
    assert sectors.get_sector("FICT") == "Technology"
    prices.ensure_proxy_anchors([{"symbol": FUND, "date": NEXT_DAY, "price": 30}], verbose=False)
    assert prices.get_price(FUND, NEXT_DAY) == 30
    assert prices._load_proxy_map()[FUND]["anchor_price"] == 25
    assert prices._load_proxy_map()[FUND]["anchor_date"] == DAY
    prices.save_caches()
    sectors.save_cache()
    # Loading raw prices can create migration metadata; the local sidecars must
    # retain their exact preexisting bytes even when another cache is saved.
    for name, original in before.items():
        assert (isolated_workdir / "cache" / name).read_bytes() == original

    prices.reset_caches()
    sectors.reset_cache()
    assert not prices._proxy_anchor_series
    prices._load_prices()["SPY"] = {DAY: 100.0, NEXT_DAY: 110.0, LATER_DAY: 120.0}
    for name, (_, _, load, _, _) in local_sidecars.items():
        assert load() == documents[name]
    assert prices.get_price(FUND, NEXT_DAY) == pytest.approx(27.5)
    for name, original in before.items():
        assert (isolated_workdir / "cache" / name).read_bytes() == original


@pytest.mark.parametrize("name", [
    "dividends_cache.json", "sector_cache.json", "splits_cache.json", "symbol_proxy_map.json",
])
def test_failed_first_save_restores_absence_and_retains_work_for_retry(
        local_sidecars, isolated_workdir, monkeypatch, name):
    module, target, load, dirty, save = local_sidecars[name]
    expected = _fictional_documents()[name]
    load().update(expected)
    setattr(module, dirty, True)
    cache = isolated_workdir / "cache"
    before = _disk_bytes(cache)
    real_replace = os.replace
    failed = False

    def fail_after_creation(source, destination):
        nonlocal failed
        real_replace(source, destination)
        if Path(destination) == target and not failed:
            failed = True
            raise OSError("fictional failure after first cache creation")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_after_creation)
        with pytest.raises(OSError, match="fictional failure"):
            save()
    assert failed
    assert _disk_bytes(cache) == before == {}
    assert getattr(module, dirty)
    assert load() == expected
    save()
    assert not getattr(module, dirty)
    assert set(_disk_bytes(cache)) == {Path(name)}
    assert json.loads(target.read_text(encoding="utf-8")) == expected
    if name == "sector_cache.json":
        module.reset_cache()
    else:
        module.reset_caches()
    assert load() == expected
