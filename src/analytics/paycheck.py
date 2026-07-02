"""Paycheck breakdown — gross → deductions → estimated take-home.

Turns the user's ``Paycheck Deduction`` metadata rows (medical pre-tax,
OASDI, Medicare, state payroll taxes, post-tax insurance…) plus salary
history and the current-year tax estimate into the Income tab's
Paycheck panel.  Returns ``None`` when no deduction rows exist so the
panel can hide.

Returned shape::

    {
      "year": int, "frequency": int, "frequency_label": str,
      "salary_annual": float, "gross_per_paycheck": float,
      "pretax":  [{"label", "per_paycheck", "annual"}, ...],
      "taxes":   [...same...],     # user-listed payroll taxes (OASDI…)
      "posttax": [...same...],
      "withholding": [...same...], # voluntary extra federal withholding
      "pretax_annual": float,      # net of credits (refund rows negative)
      "taxes_annual": float,
      "posttax_annual": float,
      "withholding_annual": float, # prepays the EOY bill — not a tax cost
      "k401_annual": float,        # employee-only, from actual txns
      "k401_per_paycheck": float,  #   (current-year projection, capped)
      "est_federal_tax_annual": float,       # brackets on WAGE income only
      "est_federal_tax_per_paycheck": float,
      "est_federal_effective_pct": float | None,   # ÷ gross salary
      "take_home_per_paycheck": float,
      "take_home_annual": float,
      "take_home_pct": float | None,          # take-home ÷ gross
      "total_tax_annual": float,   # payroll taxes + est. federal
      "is_projection": bool,
    }

Semantics / limits (documented in the panel footnote too):

- Wage-only view: bonuses, dividends, and realized gains are excluded
  here — the Tax tab's income build-up covers the full picture.  The
  federal figure is estimated LIABILITY on wages (salary − pre-tax
  deductions − 401(k) − standard deduction through the ordinary
  brackets), not the actual W-4 withholding.
- 401(k) is the employee's elective deferral derived from real
  contribution transactions (the current-year rate estimate's ``k401``,
  which projects YTD pace and caps at the IRS limit) — never from a
  metadata row.
- Payroll-tax rows (OASDI, Medicare, WA Cares…) are taken at the
  user-supplied per-paycheck amounts, not recomputed from formulas —
  the pay stub is authoritative.
"""

from __future__ import annotations

from datetime import date as _date

from ._shared import active_paycheck_deductions, federal_tax_from_brackets

_FREQUENCY_LABEL = {12: "monthly", 24: "semi-monthly",
                    26: "biweekly", 52: "weekly"}

# Backwards-compat alias (tests import it) — canonical implementation
# lives in _shared so tax.py's safe-harbor math can't drift from it.
_federal_tax_on = federal_tax_from_brackets


def compute_paycheck(retirement_meta: dict | None,
                     rate_estimate: dict | None) -> dict | None:
    """Build the paycheck breakdown for the current year.

    ``rate_estimate`` is the current year's entry from
    ``tax.rate_estimates_by_year`` — supplies the projected employee
    401(k) deferral and the filing status already resolved there.
    """
    rm = retirement_meta or {}
    year = _date.today().year
    rows = active_paycheck_deductions(rm, year)
    if not rows:
        return None

    freq = int(rm.get("pay_frequency") or 26)

    # Current base salary (latest effective salary-history row).
    salary = 0.0
    for s in rm.get("salary_history") or []:
        if s.get("date") and s["date"] <= _date.today().isoformat():
            salary = float(s.get("amount", 0) or 0)
    if salary <= 0:
        return None
    gross_pp = salary / freq

    def _bucket(kind: str) -> list[dict]:
        return [{"label": r["label"],
                 "per_paycheck": round(r["amount"], 2),
                 "annual": round(r["amount"] * freq, 2)}
                for r in rows if r["kind"] == kind]

    pretax, taxes, posttax = _bucket("pretax"), _bucket("tax"), _bucket("posttax")
    withholding = _bucket("withholding")
    pretax_annual = sum(r["annual"] for r in pretax)
    taxes_annual = sum(r["annual"] for r in taxes)
    posttax_annual = sum(r["annual"] for r in posttax)
    # Voluntary extra federal withholding: cash out of the paycheck,
    # but a PREPAYMENT of the year-end bill, not a tax cost — kept out
    # of taxes_annual / total_tax_annual and shown as its own line.
    # tax.py credits it against the estimated cap-gains tax.
    withholding_annual = sum(r["annual"] for r in withholding)

    # Employee 401(k) deferral — from real txns via the tax estimate
    # (YTD pace projected, capped at the IRS limit).
    k401_annual = float((rate_estimate or {}).get("k401", 0) or 0)

    # Estimated federal income tax on WAGES only: salary − pre-tax
    # benefits − 401(k) − standard deduction, through the ordinary
    # brackets for the user's filing status.
    from .tax import (_BRACKETS_BY_YEAR_STATUS, _STD_DED_BY_YEAR_STATUS,
                      _pick_by_year_status)
    status = rm.get("filing_status") or "Single"
    brackets = _pick_by_year_status(year, status, _BRACKETS_BY_YEAR_STATUS)
    std = _pick_by_year_status(year, status, _STD_DED_BY_YEAR_STATUS) or 0
    wage_taxable = max(0.0, salary - pretax_annual - k401_annual - std)
    est_fed = _federal_tax_on(wage_taxable, brackets) if brackets else 0.0

    take_home_annual = (salary - pretax_annual - k401_annual - est_fed
                        - taxes_annual - posttax_annual - withholding_annual)
    total_tax_annual = taxes_annual + est_fed

    return {
        "year": year,
        "frequency": freq,
        "frequency_label": _FREQUENCY_LABEL.get(freq, f"{freq}/yr"),
        "salary_annual": round(salary, 2),
        "gross_per_paycheck": round(gross_pp, 2),
        "pretax": pretax,
        "taxes": taxes,
        "posttax": posttax,
        "withholding": withholding,
        "pretax_annual": round(pretax_annual, 2),
        "taxes_annual": round(taxes_annual, 2),
        "posttax_annual": round(posttax_annual, 2),
        "withholding_annual": round(withholding_annual, 2),
        "k401_annual": round(k401_annual, 2),
        "k401_per_paycheck": round(k401_annual / freq, 2),
        "est_federal_tax_annual": round(est_fed, 2),
        "est_federal_tax_per_paycheck": round(est_fed / freq, 2),
        "est_federal_effective_pct": (round(est_fed / salary * 100, 2)
                                      if salary > 0 else None),
        "take_home_per_paycheck": round(take_home_annual / freq, 2),
        "take_home_annual": round(take_home_annual, 2),
        "take_home_pct": (round(take_home_annual / salary * 100, 2)
                          if salary > 0 else None),
        "total_tax_annual": round(total_tax_annual, 2),
        "is_projection": bool((rate_estimate or {}).get("is_projection")),
    }
