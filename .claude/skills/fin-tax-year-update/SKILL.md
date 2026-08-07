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

**Transcribe each status independently; never carry a pattern forward.**
This is how F-029 happened. In 2024 and 2025 the Head-of-Household and
Single 24% bracket ceilings were the SAME figure. For 2026 the IRS split
them by $25, and the table kept Single's value in the HoH row. The error
survived every existing test, because internal consistency and Python↔JS
agreement are both properties a wrong-but-uniform figure satisfies
perfectly. Two columns that have always matched are exactly the ones to
re-read.

**Cross-check with the Rev. Proc.'s own arithmetic.** Each rate schedule
prints the cumulative tax at every boundary ("$39,207 plus 32% of the
excess over $201,750"). Recomputing that running total from the
thresholds you typed either reproduces the printed figure or proves a
threshold is wrong — which is what confirmed F-029 rather than leaving
it a judgement call.

## Tests

`tests/test_tax_tables_vs_irs.py` — **add the new year here.** This is
the module that compares figures to the published documents, with each
value transcribed beside its citation, plus the cumulative-tax
cross-check described above. `test_tax_tables.py` checks shape and
Python↔JS agreement; only this one checks *correctness*.

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
