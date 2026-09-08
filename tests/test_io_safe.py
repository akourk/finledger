"""Fictional file sets retain recovery evidence across failed saves."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _files(directory):
    return {p.relative_to(directory): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


def _originals(directory):
    first, second = directory / "one.json", directory / "two.json"
    first.write_bytes(b'{"fictional": "first"}')
    second.write_bytes(b'{"fictional": "second"}')
    return first, second


def _verify_backups(directory, previous):
    from src.io_safe import RECOVERY_FILE
    record = json.loads((directory / RECOVERY_FILE).read_text())
    assert record["version"] == 1
    for entry in record["files"]:
        if entry["backup"]:
            body = (directory / entry["backup"]).read_bytes()
            assert body == previous[Path(entry["path"])]
            assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        else:
            assert Path(entry["path"]) not in previous
            assert entry["sha256"] is None


def test_successful_replacements_and_deletions_remove_recovery_material(tmp_path):
    from src.io_safe import replace_files
    first, second = _originals(tmp_path)
    third = tmp_path / "three.json"
    replace_files({first: b"new first", third: b"new third"},
                  deletions=[second], recovery_dir=tmp_path)
    assert _files(tmp_path) == {Path("one.json"): b"new first", Path("three.json"): b"new third"}


@pytest.mark.parametrize("failed_flush", [1, 3, 5])
def test_preparation_failure_never_changes_the_previous_set(tmp_path, monkeypatch, failed_flush):
    from src.io_safe import replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    flushes = 0
    real_fsync = os.fsync

    def fail_once(fd):
        nonlocal flushes
        flushes += 1
        if flushes == failed_flush:
            raise OSError("fictional flush failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_once)
    with pytest.raises(OSError, match="fictional flush"):
        replace_files({first: b"new first", second: b"new second"}, recovery_dir=tmp_path)
    assert _files(tmp_path) == previous


def test_interruption_after_mutation_restores_previous_set(tmp_path, monkeypatch):
    from src.io_safe import replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    real_replace = os.replace
    interrupted = False

    def interrupt_once(source, target):
        nonlocal interrupted
        real_replace(source, target)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        replace_files({first: b"new first"}, deletions=[second], recovery_dir=tmp_path)
    assert _files(tmp_path) == previous


def test_failed_rollback_preserves_complete_backups_and_blocks_retry(tmp_path, monkeypatch):
    from src.io_safe import check_pending_recovery, replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    real_replace = os.replace
    first_replaced = False

    def fail_save_and_restore(source, target):
        nonlocal first_replaced
        if target == second or (target == first and first_replaced):
            raise OSError("fictional persistent failure")
        real_replace(source, target)
        first_replaced = True

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_save_and_restore)
        with pytest.raises(OSError, match="recovery was incomplete"):
            replace_files({first: b"new first", second: b"new second"}, recovery_dir=tmp_path)
    assert first.read_bytes() == b"new first"
    _verify_backups(tmp_path, previous)
    with pytest.raises(ValueError, match="incomplete cache save"):
        check_pending_recovery(tmp_path)
    evidence = _files(tmp_path)
    with pytest.raises(ValueError, match="incomplete cache save"):
        replace_files({first: b"another attempt"}, recovery_dir=tmp_path)
    assert _files(tmp_path) == evidence


def test_second_interrupt_preserves_marker_and_backups(tmp_path, monkeypatch):
    from src.io_safe import RECOVERY_FILE, replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    real_replace, real_unlink = os.replace, Path.unlink

    def fail_replace(source, target):
        if target == second:
            raise KeyboardInterrupt
        return real_replace(source, target)

    def interrupt_cleanup(path, *args, **kwargs):
        if path.name == RECOVERY_FILE:
            raise KeyboardInterrupt
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        patch.setattr(Path, "unlink", interrupt_cleanup)
        with pytest.raises(OSError, match="recovery was incomplete"):
            replace_files({first: b"new first", second: b"new second"}, recovery_dir=tmp_path)
    _verify_backups(tmp_path, previous)


@pytest.mark.parametrize("remove_marker", [False, True])
def test_commit_marker_interruption_never_starts_a_post_commit_rollback(tmp_path, monkeypatch, remove_marker):
    from src.io_safe import RECOVERY_FILE, check_pending_recovery, replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    real_unlink = Path.unlink

    def interrupted_commit(path, *args, **kwargs):
        if path.name == RECOVERY_FILE:
            if remove_marker:
                real_unlink(path, *args, **kwargs)
            raise KeyboardInterrupt
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted_commit)
        with pytest.raises(KeyboardInterrupt):
            replace_files({first: b"new first"}, deletions=[second], recovery_dir=tmp_path)
    assert first.read_bytes() == b"new first"
    assert not second.exists()
    if remove_marker:
        check_pending_recovery(tmp_path)  # Complete new set is committed.
        assert sorted(p.read_bytes() for p in tmp_path.glob(".fin-backup-*")) == sorted(previous.values())
    else:
        _verify_backups(tmp_path, previous)
        with pytest.raises(ValueError, match="incomplete cache save"):
            check_pending_recovery(tmp_path)


@pytest.mark.parametrize("stop_after", ["replace", "delete"])
def test_abrupt_process_exit_leaves_a_guard_and_recoverable_originals(tmp_path, stop_after):
    from src.io_safe import check_pending_recovery
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    program = """
import os, sys
from pathlib import Path
from src.io_safe import replace_files
root = Path(sys.argv[1])
original_replace, original_unlink = os.replace, Path.unlink
def stop_replace(source, target):
    original_replace(source, target)
    os._exit(17)
def stop_delete(path, *args, **kwargs):
    original_unlink(path, *args, **kwargs)
    if path == root / 'two.json':
        os._exit(17)
if sys.argv[2] == 'replace':
    os.replace = stop_replace
else:
    Path.unlink = stop_delete
replace_files({root / 'one.json': b'new first'},
              deletions=[root / 'two.json'], recovery_dir=root)
"""
    env = os.environ.copy()
    env.update(FIN_PROJECT_ROOT=str(tmp_path), FIN_DATA_DIR=str(tmp_path / "data"),
               FIN_CACHE_DIR=str(tmp_path), FIN_EXPORT_DIR=str(tmp_path / "exports"))
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), stop_after],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 17
    assert first.read_bytes() == b"new first"
    assert second.exists() == (stop_after == "replace")
    _verify_backups(tmp_path, previous)
    with pytest.raises(ValueError, match="incomplete cache save"):
        check_pending_recovery(tmp_path)


def test_conflicting_operations_are_rejected_before_mutation(tmp_path):
    from src.io_safe import replace_files
    first, second = _originals(tmp_path)
    previous = _files(tmp_path)
    with pytest.raises(ValueError, match="unique"):
        replace_files({first: b"new first"}, deletions=[first], recovery_dir=tmp_path)
    with pytest.raises(ValueError, match="within the cache"):
        replace_files({first: b"new first"}, recovery_dir=tmp_path / "other")
    assert _files(tmp_path) == previous
