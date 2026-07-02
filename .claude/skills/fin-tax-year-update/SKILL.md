---
name: fin-tax-year-update
description: Annual chore — add a new IRS tax year to the `fin` portfolio tracker (federal brackets, LTCG brackets, standard deduction, 401k limit, Roth MAGI phase-out). Use when a new tax year's figures are announced (typically Oct–Nov via a Rev. Proc. + retirement Notice), when the Tax tab silently falls back to the prior year's tables, or when Congress retroactively changes already-published figures (as OBBBA did to 2025). Lists every table, the authoritative sources, the verification rule, and the tests.
---

# Adding a new tax year to `fin`

All tax reference data is **single-sourced in `src/analytics/tax.py`**
and serialized into the export via `tax_tables_to_json()` (under
`DATA.tax_tables`).  The dashboard JS reads from there — the literals in
`app/80-tax.js` are emergency fallbacks only.  **Never add a year in the
JS.**

## Tables to update (all in `src/analytics/tax.py`)

| Table | What | Source document |
|---|---|---|
| `_BRACKETS_BY_YEAR_STATUS` | 7-rate ordinary brackets × 4 filing statuses; rows are `(cumulative_cap, rate)`, top cap `math.inf` | IRS Rev. Proc. for the year (e.g. 2026 = Rev. Proc. 2025-32) |
| `_LTCG_BY_YEAR_STATUS` | 0/15/20 LTCG thresholds × 4 statuses | same Rev. Proc. |
| `_STD_DED_BY_YEAR_STATUS` | standard deduction × 4 statuses | same Rev. Proc. |
| `_K401_LIMIT_BY_YEAR` | elective-deferral limit | IRS retirement-limits Notice (e.g. 2026 = Notice 2025-67) |
| `_ROTH_MAGI_PHASEOUT_BY_STATUS` | phase-out `start` per year (+ fixed `width`: 15000 Single/HoH, 10000 MFJ/MFS) | same Notice |

Conventions to preserve:
- MFS ordinary brackets = Single thresholds except the 35% cap = half
  the MFJ 37% threshold; MFS LTCG 15% cap = half of MFJ's.
- `_pick_by_year_status` falls back to the closest prior year, so a
  missing year doesn't crash — it silently uses stale numbers.  That
  silence is exactly why this chore needs doing promptly each fall.

## Verification rule (non-negotiable)

**Never write figures from memory.**  Web-search the actual Rev. Proc. /
Notice numbers and cross-check at least two sources (irs.gov newsroom +
Tax Foundation's year page are reliable).  Watch for **retroactive
legislation**: OBBBA (July 2025) replaced the already-published 2025
standard deductions ($15,000→$15,750 Single, $30,000→$31,500 MFJ,
$22,500→$23,625 HoH) — when that happens, update the *existing* year's
rows and leave a comment citing the act.

## Tests

`tests/test_tax_tables.py` — update/extend:
- `test_2026_tables_present_with_obbba_2025_std_deduction` is the
  pattern: assert the new year's first bracket row, first LTCG row, std
  deduction, and Roth MAGI start, so a fat-fingered table fails loudly.
- `test_emit_matches_python_source_tables` and the finite-JSON tests
  pass automatically if the table shape is right (top bracket must be
  `math.inf`, which serializes to `null`).

```bash
python -m pytest tests/test_tax_tables.py -q
```

## Related figures that are NOT in the tables

- NIIT thresholds are hardcoded in `_tax_rate_estimate` (statutory,
  not inflation-indexed — $200k/$250k/$125k; don't "update" them).
- State tax is a single user-supplied marginal rate
  (`State Tax Rate` metadata row), no brackets.
- §1256 underlyings (`SECTION_1256_UNDERLYINGS`) change only when the
  user trades a new broad-based index product.
