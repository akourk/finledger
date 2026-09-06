#!/bin/sh
# Match documented uv setup without requiring shell activation or downloads.
set -eu
root=$(git rev-parse --show-toplevel)
cd "$root"
for candidate in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$candidate" -m tools.privacy_guard "$@"
  fi
done
echo 'privacy: Python 3.10+ is required; run uv sync --locked --all-groups before installing or using hooks.' >&2
exit 2
