"""Single-file portable snapshot of ``data/``.

The pipeline expects a folder full of broker-CSV exports.  After
years of trading you accumulate dozens.  Moving them to a fresh
checkout (new machine, VM, container) is friction.

This module bundles every CSV in ``data/`` into one JSON file and
restores it on the other side.  The bundle is plain text — diffable,
inspectable, no archive tooling required to read it.

CLI::

    python -m src.main --export-snapshot snapshot.json
    python -m src.main --import-snapshot snapshot.json [--force]

Programmatic::

    from src.snapshot import export_snapshot, import_snapshot
    export_snapshot(Path("data"), Path("snap.json"))
    import_snapshot(Path("snap.json"), Path("data"))

Schema (v1)::

    {
      "version": 1,
      "exported_at": "2026-05-09T15:30:00+00:00",
      "file_count": 12,
      "files": {
        "robinhood-1.csv": "<raw CSV text>",
        "schwab-roth-ira-1.csv": "<raw CSV text>",
        ...
      }
    }
"""

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_VERSION = 1


def export_snapshot(data_dir: Path, out_path: Path) -> dict:
    """Bundle every ``*.csv`` in ``data_dir`` into one JSON file.

    Returns the bundle dict (also written to ``out_path``).  Skips
    subdirectories — only top-level CSVs in ``data_dir`` are included.
    Empty files are still bundled (a CSV with only a header is a
    valid input the pipeline knows how to handle).
    """
    if not data_dir.exists():
        raise FileNotFoundError(f"data_dir does not exist: {data_dir}")

    files: dict[str, str] = {}
    for p in sorted(data_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".csv":
            files[p.name] = p.read_text(encoding="utf-8-sig")

    bundle = {
        "version": SNAPSHOT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_count": len(files),
        "files": files,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return bundle


def import_snapshot(snapshot_path: Path, data_dir: Path,
                    overwrite: bool = False) -> dict:
    """Restore CSVs from a snapshot into ``data_dir``.

    Returns ``{"written": [...], "skipped": [...]}``.  Existing files
    are preserved unless ``overwrite=True`` — protects against
    accidentally clobbering local edits with a stale snapshot.
    """
    bundle = json.loads(snapshot_path.read_text(encoding="utf-8"))
    version = bundle.get("version")
    if version != SNAPSHOT_VERSION:
        raise ValueError(
            f"Unsupported snapshot version: {version} "
            f"(this build expects v{SNAPSHOT_VERSION})"
        )

    files = bundle.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError("Snapshot 'files' field is malformed (expected dict)")

    data_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []

    for name, content in files.items():
        # Defense against path traversal — refuse anything with a
        # separator or parent reference in the filename.  CSV exports
        # are flat by definition.
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"Refusing suspicious filename in snapshot: {name!r}")

        target = data_dir / name
        if target.exists() and not overwrite:
            skipped.append(name)
            continue
        target.write_text(content, encoding="utf-8")
        written.append(name)

    return {"written": written, "skipped": skipped, "total": len(files)}
