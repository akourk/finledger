---
name: fin-add-broker
description: Add or extend a brokerage CSV parser in finledger. Use for a new broker or changed export format; covers strict row validation, detection, account mappings, normalization, snapshot round trips, and isolated fictional fixtures.
---

# Add or extend a broker parser

All paths below are repository-relative. Use public format documentation and
independently fictional examples to establish the header, preamble/footer,
actions, and sign conventions. If diagnosing an authorized local export,
inspect structural facts locally and reproduce the shape with fictional cells;
do not ask the user to send personal financial rows into chat or copy them into
fixtures. See [PRIVACY.md](../../../docs/PRIVACY.md) and
[CSV investigations](../../../CONTRIBUTING.md#csv-parser-investigations).

## Detection and dispatch

1. Add filename detection in `src/scanner.py::detect_broker` and a distinctive
   header signature in `_detect_by_headers`. Specific formats must precede
   generic broker names. Header detection examines the first ten lines because
   some exports start with metadata. Reference reports must remain `skip`;
   importing them as transactions would double-count the normal export.
2. Add the broker key to `CANONICAL_PREFIXES` in `src/config.py` when the new
   format needs a canonical filename. Preserve the renamer's collision and
   rollback behavior; never change local personal filenames while developing.
3. Create `src/parsers/<broker>.py`, register it in `_PARSERS` in
   `src/parsers/__init__.py`, and add the public parser export where appropriate.
   The parser returns `list[Transaction]` using `_txn(...)`: the core fields
   are `date, account, symbol, action, quantity, price, fees, amount,
   description, source`, with optional `cusip` and later pipeline annotations.

## Strict CSV and numeric handling

Model the parser on a current neighbor such as `apple_savings.py`. Use
`read_csv_rows` from `src/parsers/_helpers.py`, with format-specific `required`
headers and `nonblank` fields. It validates widths, duplicate headers, and
required cells; wraps values with redacted error locations; and counts
substantive rows for empty-import detection. Using a raw `csv.DictReader`
in a new parser bypasses those boundaries.

Open with `newline=""` and `encoding="utf-8-sig"`. Pass complete CSV records
through the shared reader; do not split or prefilter physical lines, because
quoted fields may span lines. If scanning a preamble, preserve `line_offset`.
For an informational footer, use a narrowly recognized `is_informational`
callback where applicable, checking the exact header and empty transaction
cells before ignoring an overflow record. Never discard a malformed row that
contains transaction data. Inspect `robinhood.py` and its footer tests.

Use `_num` for finite numeric parsing and `_date_mdy`, `_date_ymd`, `_date_dmy`,
or `_date_iso` for dates. `_num` returns zero for an optional blank cell and
rejects malformed or non-finite input; do not replace it with a forgiving
`float(...)` fallback. Preserve the shared date-failure accounting if a parser
catches a date error. The full pipeline calls `validate_ingestion` and must
reject dropped, malformed, or unrecognized input before replacing outputs.

Direction belongs in the action after parsing:

- Resolve ambiguous signs and broker markers (including surrender suffixes)
  **before** converting quantity, amount, and fees to nonnegative magnitudes.
- Keep the validated numeric value; reparsing formatted quantities can lose
  grouped digits or shares.
- Use `src/reorgs.py` for corporate-action pairing and classification. Keep
  options under their contract symbol, with quantity in contracts and premium
  per share; valuation applies the contract multiplier.
- Pass `source=filepath.name`; do not embed a personal absolute path.

## Account mappings and actions

`src/accounts.py::configure_accounts` validates each run's metadata and applies
`Account Group` / `Account Type` mappings before normalization. Every resulting
group needs an explicit `Taxable`, `Retirement`, or `Savings` type. Missing types
fail closed; **there is no normal pipeline fallback to Taxable**. Keep personal
mappings out of `config.py`. Add fictional mappings to the sample metadata.
`--init-account-mappings` writes a starter for user review; `_INTRINSIC_TYPES`
may suggest a category only when the parser's account identity establishes it.
A generic broker can offer multiple account types.

Add ordered rules in `src/normalize.py::RULES`, scoped to the intended
`account_group` for broker-specific vocabulary. Confirm that scope still
matches after account grouping. Unmatched actions pass through title-cased;
cover every supported raw action. If no existing canonical action fits, use
[fin-add-action](../fin-add-action/SKILL.md). Direction cannot be recovered
from `amount_sign` after the parser has discarded the original sign.

## Regression and sample coverage

Use existing fictional writers in `tests/conftest.py` and fixtures. Cover normal
actions, both directions of ambiguous actions, malformed/non-finite values,
missing headers/cells, empty input, multiline fields, and any new footer boundary.
When extending snapshots, test CLI export, copy, restore, and normal ingestion;
assert final share balances and preservation of previous outputs after failure.
Snapshot transport succeeding does not prove the restored CSV parses.

A new broker should have a writer in `tools/build_sample_snapshot.py` and
appropriate fictional price coverage in `samples/prices.fixture.json`. Rebuild
the shipped snapshot only after updating the fictional generator or explaining
a stale-sample failure:

```bash
uv run python -m tools.build_sample_snapshot
uv run python -m pytest tests/test_snapshot_workflow.py tests/test_sample_snapshot.py tests/test_import_safety.py tests/test_parser_silent_drop.py -q -rs
uv run python -m pytest tests/ -q -rs
uv run python -m tools.build_demo --output _site
npm test
```

Include the parser's own focused tests. Follow
[fin-dev-loop](../fin-dev-loop/SKILL.md) for temporary paths, fixed dates, and
blocked network. Finish when supported formats normalize and balance correctly,
invalid imports preserve previous outputs, and the fictional demo still builds.
