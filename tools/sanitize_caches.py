"""Pre-publication sanitation for ``cache/`` files.

The pipeline auto-populates ``anchor_date`` / ``anchor_price`` in
``cache/symbol_proxy_map.json`` from the user's own data on every
run.  Those values are user-specific (the date they first acquired
a position).  Strip them before pushing to a public fork.

The proxy mapping itself stays — it's hand-curated knowledge about
which CITs map to which retail funds, valuable for any user.  Only
the per-user anchor values are removed; the next pipeline run on a
new checkout will re-seed them from the new user's data.

Run from the repo root::

    python tools/sanitize_caches.py

Idempotent — running on already-clean caches is a no-op.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sanitize_proxy_map(path: Path) -> int:
    """Remove ``anchor_date`` and ``anchor_price`` from every entry.

    Returns the number of entries that had values stripped.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    stripped = 0
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        # Pop both unconditionally — using `or` short-circuits and
        # would skip the second pop when the first key existed.
        had_date  = val.pop("anchor_date", None) is not None
        had_price = val.pop("anchor_price", None) is not None
        if had_date or had_price:
            stripped += 1
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return stripped


def main() -> None:
    proxy_path = ROOT / "cache" / "symbol_proxy_map.json"
    if proxy_path.exists():
        n = sanitize_proxy_map(proxy_path)
        print(f"Stripped per-user anchors from {n} proxy entries "
              f"in {proxy_path.relative_to(ROOT)}")
    else:
        print(f"(no {proxy_path.relative_to(ROOT)} — nothing to do)")

    last_run = ROOT / "cache" / "last_run.json"
    if last_run.exists():
        print(f"Reminder: {last_run.relative_to(ROOT)} is gitignored, "
              f"but it carries portfolio totals — don't add it explicitly.")

    print("\nFinal pre-publish checklist:")
    print("  [ ] git status — no data/*.csv or exports/ staged")
    print("  [ ] grep your name / email / account numbers across the diff")
    print("  [ ] confirm samples/portfolio.snapshot.json works:")
    print("        python -m src.main --import-snapshot "
          "samples/portfolio.snapshot.json")


if __name__ == "__main__":
    main()
