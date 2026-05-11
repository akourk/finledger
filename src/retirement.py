"""Backwards-compat shim — the real implementation moved to
``src/metadata.py``.

The user-maintained CSV is now ``data/metadata.csv`` and carries
account-mapping overrides in addition to the original retirement
metadata.  This module exists so older imports (``from .retirement
import parse_retirement_data``) keep working.  Prefer
``from .metadata import parse_metadata`` in new code.
"""

from .metadata import (  # noqa: F401  (re-export)
    METADATA_FILE,
    LEGACY_METADATA_FILE,
    RETIREMENT_FILE,
    parse_metadata,
    parse_retirement_data,
)
