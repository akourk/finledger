"""Stage complete file sets, with rollback and optional cache recovery guards."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

RECOVERY_FILE = ".fin-recovery.json"


def check_pending_recovery(directory: Path) -> None:
    """Reject a cache whose last save did not finish or completely roll back."""
    marker = directory / RECOVERY_FILE
    if marker.exists() or marker.is_symlink():
        raise ValueError("An incomplete cache save needs recovery; inspect "
                         f"{marker} locally before running again. Preserve the "
                         "recovery manifest and backups until the previous "
                         "file set has been restored.")


def _prepare(target: Path, data: bytes, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def replace_files(contents: dict[Path, bytes], *, deletions=(),
                  recovery_dir: Path | None = None) -> None:
    """Prepare replacements and backups, then restore the set on failure.

    Replacements precede deletions; callers can put a migration source last.
    A cache can supply ``recovery_dir`` to persist a manifest before mutation
    and reject subsequent loads if the process died or rollback failed. The
    manifest records original hashes and backup paths for local recovery.

    This is a single-writer protocol, not an atomic multi-file filesystem
    transaction or a guarantee against power loss. Each replacement is atomic;
    readers of exports should prefer the self-contained HTML.
    """
    # Normalize lexical paths without following a symbolic-link destination.
    paths = [Path(os.path.abspath(p)) for p in contents]
    removed = [Path(os.path.abspath(p)) for p in deletions]
    targets = paths + removed
    if len(set(targets)) != len(targets):
        raise ValueError("Replacement and deletion destinations must be unique")
    contents = dict(zip(paths, contents.values()))
    recovery_dir = Path(os.path.abspath(recovery_dir)) if recovery_dir is not None else None
    if recovery_dir is not None:
        check_pending_recovery(recovery_dir)
        if any(not target.is_relative_to(recovery_dir) for target in targets):
            raise ValueError("Recovery destinations must stay within the cache directory")
    for target in targets:
        if target.is_symlink():
            raise ValueError("Refusing to overwrite a symbolic-link destination")
        if target.exists() and not target.is_file():
            raise ValueError("Replacement destinations must be regular files")
    if not contents and not any(p.exists() for p in removed):
        return

    staged = {}
    backups = {}
    original_hashes = {}
    attempted = []
    marker = None
    preserve_recovery = False
    try:
        for target in targets:
            if target.exists():
                original = target.read_bytes()
                backups[target] = _prepare(target, original, ".fin-backup-")
                original_hashes[target] = hashlib.sha256(original).hexdigest()
            else:
                backups[target] = None
                original_hashes[target] = None
        for target, data in contents.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            staged[target] = _prepare(target, data, ".fin-stage-")
        if recovery_dir is not None:
            recovery_dir.mkdir(parents=True, exist_ok=True)
            manifest = {"version": 1, "files": [
                {"path": str(target.relative_to(recovery_dir)),
                 "backup": str(backups[target].relative_to(recovery_dir)) if backups[target] else None,
                 "sha256": original_hashes[target]}
                for target in targets
            ]}
            # Exclusive creation prevents replacing another save's recovery
            # instructions. All backups are durable before live files change.
            candidate = recovery_dir / RECOVERY_FILE
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            marker = candidate
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(json.dumps(manifest, indent=2).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        for target, temporary in staged.items():
            attempted.append(target)
            os.replace(temporary, target)
        for target in removed:
            attempted.append(target)
            target.unlink(missing_ok=True)
    except BaseException as error:
        failures = []
        for target in reversed(attempted):
            try:
                if backups[target] is None:
                    target.unlink(missing_ok=True)
                else:
                    # A failed atomic replace may not have changed this file.
                    # Avoid turning a persistent error on that destination
                    # into a spurious rollback failure.
                    try:
                        if hashlib.sha256(target.read_bytes()).hexdigest() == original_hashes[target]:
                            continue
                    except OSError:
                        pass
                    # Keep the original backup intact until every restore
                    # succeeds, so manual recovery can still restore the set.
                    restore = _prepare(target, backups[target].read_bytes(), ".fin-stage-")
                    try:
                        os.replace(restore, target)
                    finally:
                        restore.unlink(missing_ok=True)
            except BaseException:
                failures.append(target)
        if not failures and marker is not None:
            try:
                marker.unlink()
                marker = None
            except BaseException:
                failures.append(marker)
        if failures:
            preserve_recovery = True
            location = str(marker) if marker else ", ".join(str(p) for p in backups.values() if p)
            raise OSError("File replacement recovery was incomplete; preserve "
                          f"the local recovery files: {location}. Restore the "
                          "previous file set before retrying.") from error
        raise
    else:
        # Removing the marker is the commit point. Never start rollback from
        # this branch: an interrupt can arrive after unlink already succeeded,
        # when a fresh reader is allowed to observe the complete new set.
        if marker is not None:
            try:
                marker.unlink()
            except BaseException:
                preserve_recovery = True
                raise
    finally:
        # Once a restore fails, backups must outlive this call. Never remove
        # them as incidental cleanup of the original error.
        if not preserve_recovery:
            for temporary in [*staged.values(), *backups.values()]:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass  # Orphaned private scratch files are safe to retain.
