"""Personal metadata + project config — parsed from ``data/metadata.csv``.

This is the single hand-maintained file in the ``data/`` directory.
Brokers don't export the things this file carries, but the dashboard
needs them to compute meaningful figures: birthday, salary history,
bonus history, annual expenses, year-end targets, and (optionally)
account-name → account-group / account-type mappings.

Schema (CSV header)::

    Type, Date, Amount, Symbol, Note

Type is one of:

- ``Personal Info``    — Note is one of {``Birthday``}.  Date carries
  the value (ISO YYYY-MM-DD).
- ``Salary History``   — base-salary changes; Date = effective date,
  Amount = annual salary, Note = free text (job change, raise %, etc.).
- ``Bonus History``    — lump-sum bonuses; Date = pay date, Amount = $.
- ``Annual Expenses``  — yearly expense estimate.  Most recent entry
  is the active one — × 25 = FI number for the Planning tab.
- ``Target``           — per-year portfolio-value goals.  Date is the
  year (or full ISO date — only the year prefix is used).
- ``Account Group``    — override for the global ``ACCOUNT_GROUPS``
  table.  Symbol = the broker's raw account name (e.g.
  ``"Schwab Roth Contributory IRA"``); Note = the simplified group
  key (e.g. ``"Roth IRA"``).
- ``Account Type``     — override for the global ``ACCOUNT_TYPES``
  table.  Symbol = the simplified group key (e.g. ``"Roth IRA"``);
  Note = the tax category (``Taxable`` / ``Retirement`` / ``Savings``).
- ``Filing Status``    — Note is one of {``Single``,
  ``Married Filing Jointly``, ``Married Filing Separately``,
  ``Head of Household``}.  Drives federal tax brackets, LTCG
  brackets, standard deduction, and Roth IRA MAGI phaseout window.
  Defaults to ``Single`` when absent.
- ``State``            — Note is the 2-letter US state code
  (e.g. ``WA``, ``CA``).  Display label on the Tax tab.  Paired
  with the ``State Tax Rate`` row below for the marginal-rate
  composition; the code is informational only.
- ``State Tax Rate``   — Amount is the marginal state income tax
  rate as a decimal (e.g. ``0.0`` for WA, ``0.093`` for CA's 9.3%
  bracket).  State tax codes vary too much to encode brackets
  reliably (no-tax / flat / progressive / different thresholds);
  the user enters a single representative marginal rate that gets
  added to the federal marginal for the "Combined" rate display.
- ``Retirement Age``   — Amount is the integer target age (default
  67).  Drives the Monte Carlo simulation horizon and the
  scenario-projection age input on the Planning tab.

Account Group and Account Type rows let users add new brokers /
account names without editing ``src/config.py``.  When the metadata
file has no override for a key, the built-in default applies.

The scanner classifies ``metadata.csv`` (and the legacy filename
``retirement-data.csv``) as ``"skip"`` so the transaction pipeline
doesn't try to parse it as broker data.
"""

import csv
from pathlib import Path

METADATA_FILE = "metadata.csv"
LEGACY_METADATA_FILE = "retirement-data.csv"

# Public alias kept for downstream code that still uses the old name.
RETIREMENT_FILE = LEGACY_METADATA_FILE

# Canonical filing-status strings.  Mapped from user-typed variants
# (case-insensitive, accepts common abbreviations) so we don't break
# downstream code that switches on the exact spelling.
_FILING_STATUS_ALIASES = {
    "single":                       "Single",
    "married filing jointly":       "Married Filing Jointly",
    "married filing joint":         "Married Filing Jointly",
    "mfj":                          "Married Filing Jointly",
    "joint":                        "Married Filing Jointly",
    "married filing separately":    "Married Filing Separately",
    "married filing separate":      "Married Filing Separately",
    "mfs":                          "Married Filing Separately",
    "head of household":            "Head of Household",
    "hoh":                          "Head of Household",
}


def _normalize_filing_status(s: str) -> str:
    """Map a user-typed filing status to the canonical spelling.

    Unrecognized values pass through unchanged — keeps the door open
    for new statuses without breaking the parser.
    """
    return _FILING_STATUS_ALIASES.get(s.strip().lower(), s.strip())


def _empty() -> dict:
    return {
        "birthday": None,
        "salary_history": [],
        "bonus_history": [],
        "annual_expenses": [],
        "targets": [],
        "target_allocation": [],
        "account_groups": {},
        "account_types": {},
        # Defaults match the legacy hardcoded behavior — Single filer,
        # retirement age 67 (Fidelity standard).  State left None so
        # the Tax tab can show "—" instead of an arbitrary guess.
        # State tax rate defaults to 0 (no-tax states like WA / TX /
        # FL); the Tax tab combines this with the federal marginal.
        "filing_status": "Single",
        "state": None,
        "state_tax_rate": 0.0,
        "retirement_age": 67,
    }


def parse_metadata(data_dir: Path) -> dict:
    """Read the personal metadata + config file.

    Looks for ``metadata.csv`` first, then falls back to the legacy
    ``retirement-data.csv`` so existing setups keep working without
    renaming.

    Returns a dict shape that downstream code can rely on even when
    the file is missing — every key is present with an empty default.
    """
    primary = data_dir / METADATA_FILE
    legacy = data_dir / LEGACY_METADATA_FILE
    if primary.exists():
        path = primary
    elif legacy.exists():
        path = legacy
    else:
        return _empty()

    out = _empty()

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            typ    = (row.get("Type")   or "").strip()
            date   = (row.get("Date")   or "").strip()
            symbol = (row.get("Symbol") or "").strip()
            note   = (row.get("Note")   or "").strip()
            try:
                amt = float((row.get("Amount") or "0").replace(",", ""))
            except ValueError:
                amt = 0.0

            if typ == "Personal Info" and note.lower() == "birthday":
                out["birthday"] = date or None
            elif typ == "Salary History":
                out["salary_history"].append(
                    {"date": date, "amount": amt, "note": note})
            elif typ == "Bonus History":
                out["bonus_history"].append(
                    {"date": date, "amount": amt, "note": note})
            elif typ == "Annual Expenses":
                out["annual_expenses"].append(
                    {"date": date, "amount": amt, "note": note})
            elif typ == "Target":
                # Date may be a full ISO date or just a year — only
                # the YYYY prefix is used by the year-by-year table.
                year = (date or "")[:4]
                if year:
                    out["targets"].append(
                        {"year": year, "amount": amt, "note": note})
            elif typ == "Target Allocation":
                # Symbol = sector bucket name (matches the sectors the
                # holdings are classified into, e.g. Technology, Cash,
                # Cryptocurrency); Amount = target percent of portfolio.
                # Drives the Holdings tab's Target vs Actual / drift view.
                if symbol and amt > 0:
                    pct = amt * 100.0 if amt <= 1.0 else amt   # accept 0.6 or 60
                    out["target_allocation"].append(
                        {"bucket": symbol, "pct": round(pct, 4)})
            elif typ == "Account Group":
                # Symbol = broker's raw account name; Note = simplified
                # group key (Roth IRA, 401K, …).  Both required.
                if symbol and note:
                    out["account_groups"][symbol] = note
            elif typ == "Account Type":
                # Symbol = simplified group key; Note = tax category
                # (Taxable / Retirement / Savings).  Both required.
                if symbol and note:
                    out["account_types"][symbol] = note
            elif typ == "Filing Status":
                # Note is the status name (Single / Married Filing
                # Jointly / etc.).  Normalised to title case so
                # variants like "married filing jointly" map.
                if note:
                    out["filing_status"] = _normalize_filing_status(note)
            elif typ == "State":
                # Note is the 2-letter state code.  Upper-cased for
                # display consistency.
                if note:
                    out["state"] = note.strip().upper()
            elif typ == "State Tax Rate":
                # Amount is the marginal state income tax rate as a
                # decimal.  Sanity-clamp to [0, 0.20] so a typo
                # (e.g. "9.3" meaning percent instead of "0.093"
                # meaning decimal) doesn't render an absurd combined
                # rate.  We accept either input via the clamp.
                if amt > 1.0:
                    amt = amt / 100.0
                if 0.0 <= amt <= 0.20:
                    out["state_tax_rate"] = amt
            elif typ == "Retirement Age":
                # Amount carries the integer age.  Sanity-clamp to
                # 30..100 so a typo doesn't break the projections.
                age = int(round(amt))
                if 30 <= age <= 100:
                    out["retirement_age"] = age

    out["salary_history"].sort(key=lambda x: x["date"])
    out["bonus_history"].sort(key=lambda x: x["date"])
    out["annual_expenses"].sort(key=lambda x: x["date"])
    out["targets"].sort(key=lambda x: x["year"])
    return out


# Backwards-compat alias.  Older code (and any external scripts) may
# still call ``parse_retirement_data``.  The returned dict has the
# new ``account_groups`` / ``account_types`` keys, which old callers
# simply ignore.
parse_retirement_data = parse_metadata
