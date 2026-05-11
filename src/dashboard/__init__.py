"""Single-file HTML dashboard generator.

The dashboard is split across three asset files for editor ergonomics:

- ``template.html`` — page structure (HTML head/body, no CSS or JS).
  Contains two placeholder markers that this module substitutes:
  ``/* @@STYLES@@ */`` and ``// @@APP_JS@@``.
- ``styles.css`` — all dashboard styling.
- ``app.js`` — all dashboard behaviour (tab routing, chart rendering,
  filter state, etc.).

The output is still a single self-contained HTML file with the JSON
data, CSS, and JS inlined — no network calls at view time, same
single-file portability as before.

Adding a new tab or feature: edit ``app.js`` (and ``template.html`` if
the new tab needs new DOM nodes); never touch this Python module.
"""

from __future__ import annotations

import json
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

# Placeholder markers used by template.html — kept as constants so a
# stray edit to one file is easy to detect.
_STYLES_MARKER = "/* @@STYLES@@ */"
_APP_JS_MARKER = "// @@APP_JS@@"
_DATA_MARKER   = "__JSON_DATA__"


def _read_asset(filename: str) -> str:
    return (_PKG_DIR / filename).read_text(encoding="utf-8")


def generate_dashboard(json_path: Path, output_path: Path) -> None:
    """Read the transactions JSON and produce a single self-contained
    HTML dashboard at ``output_path``.

    Steps: load template + CSS + JS from the package directory,
    substitute the CSS into the ``<style>`` block, the JS into the
    ``<script>`` block, and the JSON payload into ``app.js``'s
    ``__JSON_DATA__`` placeholder.
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    template = _read_asset("template.html")
    styles   = _read_asset("styles.css")
    app_js   = _read_asset("app.js")

    # Inject the JSON payload into app.js's __JSON_DATA__ placeholder
    # before splicing app.js into the template — this keeps the JSON
    # at the same call site (DATA = __JSON_DATA__) it's been at all
    # along, so the JS doesn't need any structural changes.
    app_js = app_js.replace(_DATA_MARKER, json.dumps(data, ensure_ascii=False))

    for marker, replacement in (
        (_STYLES_MARKER, styles),
        (_APP_JS_MARKER, app_js),
    ):
        if marker not in template:
            raise RuntimeError(
                f"dashboard template is missing marker {marker!r} — "
                "the asset files are out of sync with __init__.py"
            )
        template = template.replace(marker, replacement)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: stream to a sibling .tmp file first, then rename
    # into place.  Without this, hitting Refresh in the browser during
    # a pipeline run can race with the write and render an empty page
    # (Python's write_text truncates-then-streams; the browser reads
    # the 0-byte window mid-write).  os.replace is atomic on both
    # POSIX and Windows within a single filesystem.
    import os
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(template, encoding="utf-8")
    os.replace(tmp_path, output_path)
