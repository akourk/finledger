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
- ``Budget``           — a recurring living expense (rent,
  subscription, insurance, …).  Symbol = category (``Housing``,
  ``Subscriptions``, ``Utilities``, …), Amount = cost per period,
  Note = label with an optional cadence suffix ``@monthly`` (default)
  / ``@yearly`` / ``@quarterly`` / ``@6mo`` (aliases ``semiannual`` /
  ``6months``) / ``@weekly``.  Date = effective-from (optional); rows
  sharing a label supersede each other by date, so a rent increase is
  a new row and ``Amount = 0`` cancels a subscription.  Drives the
  Income tab's Budget section (analytics/budget.py).
- ``Paycheck Deduction`` — a recurring per-paycheck payroll line.
  Symbol = kind (``Pre-Tax`` / ``Tax`` / ``Post-Tax`` /
  ``Withholding``), Amount = $ per paycheck (negative = a credit,
  e.g. a wellness-incentive refund), Note = label, Date =
  effective-from (undated rows apply from the current year onward so
  history isn't rewritten).  Pre-Tax rows reduce W-2 wages → AGI /
  MAGI (analytics/tax.py); ``Withholding`` = voluntary extra federal
  withholding (reduces take-home, credited against the estimated
  year-end tax bill on the Tax tab); all rows feed the Income tab's
  Paycheck panel (analytics/paycheck.py).  401(k) deferrals do NOT
  belong here — derived from actual contribution txns.
- ``Pay Frequency``    — Amount = pay periods per year (12 / 24 / 26
  / 52; default 26 = biweekly).  Annualizes the paycheck rows.
- ``Tax Return``       — a figure from a FILED 1040.  Date = tax year,
  Symbol = field (``Total Tax`` = line 24, ``AGI`` = line 11,
  ``Withholding`` = line 25d, ``Wages`` = line 1z, ``Capital Gains``
  = line 7), Amount = $.  The prior year's ``Total Tax`` + ``AGI``
  drive the Tax tab's safe-harbor check (100% / 110% of prior-year
  tax vs projected withholding); other fields are kept for reference
  / future cross-checks.

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
        "reconcile": [],
        "cost_basis_overrides": [],
        "lot_methods": {},
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
        "budget": [],
        # Paycheck lines (Medical pre-tax, OASDI, Medicare, state payroll
        # taxes, post-tax insurance…).  Biweekly pay is the default; a
        # `Pay Frequency` row overrides (12 / 24 / 26 / 52).
        "paycheck_deductions": [],
        "pay_frequency": 26,
        # Filed-1040 figures by tax year: {"2025": {"total_tax": ..,
        # "agi": .., "withholding": ..}, ...}
        "tax_returns": {},
        # Statement balances for hand-maintained CASH accounts, used to
        # true up untracked deposits (Apple Card Daily Cash landing in
        # Apple Savings).  See balance_anchor.py.
        "balance_anchors": [],
        # Effective-dated savings interest rates, used for FORECASTS
        # only — actual history comes from real Interest txns.
        "savings_apr": [],
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
            elif typ == "Cost Basis":
                # User-supplied true cost basis for an off-platform crypto
                # receive fin can't see (e.g. Coinbase "customer provided"
                # basis).  Symbol = account_group; Date = acquired date;
                # Amount = total cost basis; Note = "<qty> <asset>"
                # (optionally trailing "#N" to target the Nth lot when
                # several share the same date+qty).  Matched onto the
                # transfer-in/deposit lot by cost_basis_overrides.py.
                parts = note.split()
                ov_index = None
                if parts and parts[-1].startswith("#"):
                    try:
                        ov_index = int(parts[-1][1:])
                    except ValueError:
                        ov_index = None
                    parts = parts[:-1]
                if symbol and date and len(parts) >= 2:
                    try:
                        ov_qty = float(parts[0])
                    except ValueError:
                        ov_qty = None
                    if ov_qty is not None:
                        out["cost_basis_overrides"].append({
                            "account_group": symbol, "date": date,
                            "amount": amt, "qty": ov_qty,
                            "asset": parts[1].upper(), "index": ov_index,
                        })
            elif typ == "Lot Method":
                # Symbol = account_group; Note = lot-relief method the
                # broker actually uses (FIFO / LIFO / HIFO).  Overrides
                # the FIFO default for that account's realized-gain /
                # cost-basis attribution (e.g. Coinbase defaults to
                # HIFO).  Affects realized gains / MAGI, never balances.
                m = note.strip().lower()
                if symbol and m in ("fifo", "lifo", "hifo"):
                    out["lot_methods"][symbol] = m
            elif typ == "Budget":
                # Recurring living expense.  Symbol = category, Amount =
                # cost per period, Note = "<label> [@cadence]" where
                # cadence ∈ monthly (default) / yearly / quarterly /
                # weekly.  Date = effective-from; analytics/budget.py
                # keeps the latest row per label so increases /
                # cancellations (Amount = 0) supersede older rows.
                label, cadence = note, "monthly"
                if "@" in note:
                    label, _, cad = note.rpartition("@")
                    cad = cad.strip().lower()
                    _CADENCE_ALIASES = {
                        "monthly": "monthly",
                        "yearly": "yearly", "annual": "yearly",
                        "annually": "yearly",
                        "quarterly": "quarterly",
                        "weekly": "weekly",
                        # every-6-months billing (car insurance, etc.)
                        "6mo": "6mo", "6months": "6mo", "6month": "6mo",
                        "semiannual": "6mo", "semi-annual": "6mo",
                        "semiannually": "6mo",
                    }
                    if cad in _CADENCE_ALIASES:
                        cadence = _CADENCE_ALIASES[cad]
                        label = label.strip()
                    else:
                        label = note   # '@' was part of the label itself
                if label.strip():
                    out["budget"].append({
                        "label": label.strip(),
                        "category": symbol or "Other",
                        "amount": amt,
                        "cadence": cadence,
                        "date": date,
                    })
            elif typ == "Paycheck Deduction":
                # A recurring per-paycheck payroll line.  Symbol = kind
                # (`Pre-Tax` / `Tax` / `Post-Tax`), Amount = $ per
                # paycheck (negative = a credit, e.g. a wellness
                # incentive refund), Note = label, Date = effective-from
                # (rows sharing a label supersede by date; undated rows
                # apply from the current year onward).  401(k) elective
                # deferrals do NOT belong here — they're derived from
                # the actual contribution transactions.
                kind_raw = symbol.strip().lower().replace("-", "").replace(" ", "")
                kind = {"pretax": "pretax", "tax": "tax",
                        "posttax": "posttax", "aftertax": "posttax",
                        # Voluntary extra federal withholding — reduces
                        # take-home cash but PREPAYS the year-end tax
                        # bill rather than being a tax cost itself.
                        "withholding": "withholding",
                        "extrawithholding": "withholding"}.get(kind_raw)
                if kind and note:
                    out["paycheck_deductions"].append({
                        "label": note, "kind": kind,
                        "amount": amt, "date": date,
                    })
            elif typ == "Tax Return":
                # A figure from a filed 1040.  Date = tax year,
                # Symbol = field name, Amount = $.  Unknown fields are
                # ignored so users can annotate freely.
                field = symbol.strip().lower().replace(" ", "_").replace("-", "_")
                field = {"total_tax": "total_tax", "totaltax": "total_tax",
                         "agi": "agi", "withholding": "withholding",
                         "wages": "wages",
                         "capital_gains": "capital_gains"}.get(field)
                yr = (date or "")[:4]
                if field and yr:
                    out["tax_returns"].setdefault(yr, {})[field] = amt
            elif typ == "Pay Frequency":
                # Amount = pay periods per year.  Snapped to the
                # standard payroll set so a typo can't skew every
                # annualized figure.  Default (absent) is biweekly, 26.
                n = int(round(amt))
                if n in (12, 24, 26, 52):
                    out["pay_frequency"] = n
            elif typ == "Balance Anchor":
                # True statement balance for a hand-maintained CASH
                # account.  Symbol = account_group, Date = as-of,
                # Amount = the real balance.  balance_anchor.py books
                # the difference vs fin's computed balance as a
                # `Cash Back` row.  See that module for why this is
                # restricted to cash accounts.
                if symbol and date:
                    out["balance_anchors"].append({
                        "account_group": symbol,
                        "date": date,
                        "amount": amt,
                        "note": note,
                    })
            elif typ == "Savings APR":
                # Effective-dated annual interest rate for a savings
                # account.  Accepts 0.042 or 4.2 (both -> 4.2%).
                # FORECAST ONLY: real Interest txns remain the
                # authority for history, so there's no double-count.
                rate = amt / 100.0 if amt > 1 else amt
                if symbol and 0 <= rate <= 0.25:
                    out["savings_apr"].append({
                        "account_group": symbol,
                        "date": date,
                        "rate": rate,
                        "note": note,
                    })
            elif typ.startswith("Reconcile "):
                # User-supplied ground truth from broker statements /
                # 1099s, compared against fin's computed figures by
                # analytics/reconcile.py.  Symbol = account_group;
                # Date = as-of date (balance) or year (realized / income
                # / §1256); Amount = the broker-reported value.
                kind = typ[len("Reconcile "):].strip().lower().replace(" ", "_")
                if kind in ("balance", "realized", "income",
                            "section_1256", "other_income") and symbol:
                    out["reconcile"].append({
                        "kind": kind,
                        "account_group": symbol,
                        "date": date,
                        "amount": amt,
                        "note": note,
                    })

    out["salary_history"].sort(key=lambda x: x["date"])
    out["bonus_history"].sort(key=lambda x: x["date"])
    out["annual_expenses"].sort(key=lambda x: x["date"])
    out["targets"].sort(key=lambda x: x["year"])
    # Chronological: each anchor books only the drift since the
    # previous one, and APR lookup takes the latest rate at or before
    # a date.
    out["balance_anchors"].sort(key=lambda x: x["date"])
    out["savings_apr"].sort(key=lambda x: x["date"])
    return out


# Backwards-compat alias.  Older code (and any external scripts) may
# still call ``parse_retirement_data``.  The returned dict has the
# new ``account_groups`` / ``account_types`` keys, which old callers
# simply ignore.
parse_retirement_data = parse_metadata
