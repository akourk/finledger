"""Drive ``tools/dashboard_probe.js`` and hand back what the dashboard
actually rendered.

The JS half explains why the probe renders rather than computing.  This
half exists so both the test suite and an interactive session reach it
the same way, and so the bundle always comes from the real bundler —
``_read_app_js`` owns the concatenation order, and a hand-rolled glob
here would be exactly the duplicated-logic drift (PLAN-audit.md
taxonomy 1) this repo keeps paying for.

Interactive use::

    python -m tools.dashboard_probe path/to/transactions.json | python -m json.tool

Never point it at the user's real ``exports/`` for anything whose output
leaves the machine — it prints portfolio figures.  Tests drive it from a
synthetic fixture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_JS = Path(__file__).resolve().parent / "dashboard_probe.js"


class ProbeUnavailable(RuntimeError):
    """Node is not installed.  Callers skip rather than fail."""


def node_path() -> str | None:
    return shutil.which("node")


def build_bundle(transactions_json: Path, dest: Path) -> Path:
    """Write the shipped app bundle, with real data substituted, to
    ``dest``.  Goes through ``src.dashboard`` so concatenation order and
    the ``__JSON_DATA__`` marker stay single-sourced."""
    from src.dashboard import _DATA_MARKER, _read_app_js

    data = json.loads(Path(transactions_json).read_text(encoding="utf-8"))
    app_js = _read_app_js().replace(_DATA_MARKER, json.dumps(data, ensure_ascii=False))
    dest.write_text(app_js, encoding="utf-8")
    return dest


def probe(transactions_json: Path) -> dict:
    """Render every (tab x filter x window) combination and return the
    parsed figures.  Raises ``ProbeUnavailable`` when node is missing."""
    node = node_path()
    if node is None:
        raise ProbeUnavailable("node not found on PATH")

    with tempfile.TemporaryDirectory() as td:
        bundle = build_bundle(Path(transactions_json), Path(td) / "bundle.js")
        proc = subprocess.run(
            [node, str(PROBE_JS), str(bundle)],
            capture_output=True, text=True, timeout=180,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"probe exited {proc.returncode}\nstderr:\n{proc.stderr[:4000]}"
        )
    if not proc.stdout.strip():
        raise RuntimeError(f"probe produced no output\nstderr:\n{proc.stderr[:4000]}")
    return json.loads(proc.stdout)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(probe(Path(argv[1]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
