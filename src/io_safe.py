"""Stage complete file sets before replacement, restoring on I/O failure."""

import os
import tempfile
from pathlib import Path


def replace_files(contents: dict[Path, bytes]) -> None:
    """Prepare every file first and roll back replacements on ordinary errors.

    Each rename is atomic. A filesystem cannot atomically rename a pair of
    independent paths, so readers should prefer the self-contained HTML.
    """
    staged = {}
    previous = {}
    replaced = []
    try:
        for target, data in contents.items():
            if target.is_symlink():
                raise ValueError("Refusing to overwrite a symbolic-link destination")
            previous[target] = target.read_bytes() if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".fin-stage-", dir=target.parent)
            staged[target] = Path(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        for target, temporary in staged.items():
            os.replace(temporary, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            original = previous[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                # Restore through a sibling temporary so readers never see
                # a truncated last-good file while rollback is in progress.
                temporary = staged[target]
                temporary.write_bytes(original)
                os.replace(temporary, target)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
