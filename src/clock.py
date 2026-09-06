"""Calendar seam for reproducible snapshots without changing everyday runs.

FIN_AS_OF_DATE is an explicit ISO calendar date used by the isolated demo
builder. It is read per call so tests and embedding applications can restore
their environment safely. Callers may supply their existing clock as fallback
so module-level clock stubs continue to work.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time


def as_of_date() -> date | None:
    value = os.environ.get("FIN_AS_OF_DATE")
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("FIN_AS_OF_DATE must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ValueError("FIN_AS_OF_DATE must be a valid YYYY-MM-DD date")
    return parsed


def today(*, fallback=None) -> date:
    return as_of_date() or (fallback or date.today)()


def now(tz=None, *, fallback=None) -> datetime:
    fixed = as_of_date()
    if fixed is not None:
        return datetime.combine(fixed, time.min, tzinfo=tz)
    return (fallback or datetime.now)(tz) if tz is not None else (fallback or datetime.now)()
