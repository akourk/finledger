---
name: fin-add-broker
description: Add support for a new brokerage CSV format to the `fin` portfolio tracker. Use when the user wants `fin` to ingest exports from a broker it doesn't yet parse, or asks to "add a broker / parser / new CSV format". Walks the detection → prefix → parser → dispatch → normalize → test checklist with the exact files and signatures to edit.
---

# Adding a new broker to `fin`

A broker is supported once its CSV is **detected**, **parsed into the common
10-field transaction dict**, and its raw action strings are **normalized** to the
canonical vocabulary. Follow the steps in order; each names the exact file.

First, get the broker's real CSV header and a few representative rows from the
user (buy, sell, dividend, deposit, and any directional/ambiguous action). You
need the actual column names and at least one example of every action type.
**Do not paste real financial rows into commits or fixtures** — once you know the
shape, build synthetic rows for the test.

## 1. Detection — `src/scanner.py`

Add to `detect_broker()` a filename-pattern branch, and to `_detect_by_headers()`
a header-signature fallback (so a not-yet-renamed file is still recognized):

```python
# in detect_broker(), filename branch:
if "newbroker" in name or name.startswith("newbroker-"):
    return "newbroker"

# in _detect_by_headers(), header fallback — pick columns unique to this broker:
if "their unique col" in header and "another col" in header:
    return "newbroker"
```

Order matters in `detect_broker` — more-specific patterns first (e.g. Coinbase
Pro is checked before generic Coinbase). The function returns a broker **key**,
or `"manual"` / `"skip"` / `"unknown"`.

## 2. Canonical prefix — `src/config.py`

Add the key → rename-prefix to `CANONICAL_PREFIXES`. This is the `{prefix}.csv` /
`{prefix}-{n}.csv` name the two-pass renamer normalizes files to:

```python
CANONICAL_PREFIXES = {
    ...
    "newbroker": "newbroker",
}
```

## 3. Account mapping — usually none

`ACCOUNT_GROUPS` / `ACCOUNT_TYPES` in `config.py` start **empty**; the user wires
their accounts in `data/metadata.csv` (`Account Group` / `Account Type` rows),
and unmapped accounts fall back to the raw account name / `Taxable`. So you
normally add **nothing** here. Only touch these if the broker needs a built-in
default that isn't user-specific.

## 4. Parser — `src/parsers/newbroker.py`

Create a new module. Return a list of the common 10-field dict via the `_txn()`
helper. Use the shared helpers from `._helpers`:

- `_num(val)` — parse a numeric string to float (handles `$`, commas, blanks).
- `_date_mdy` / `_date_ymd` / `_date_dmy` / `_date_iso` — date → ISO `YYYY-MM-DD`.
- `_txn(date, account, symbol, action, quantity, price, fees, amount,
  description, source, cusip=None)` — builds the dict and applies the ticker-
  rename layer. Pass `source=filepath.name`.

Pattern (see `src/parsers/apple_savings.py` for the simplest real example):

```python
"""NewBroker taxable brokerage."""
from __future__ import annotations
import csv
from pathlib import Path
from ._helpers import Transaction, _date_mdy, _num, _txn

def parse_newbroker(filepath: Path) -> list[Transaction]:
    txns = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            action = (row.get("Action") or "").strip()
            if not action:
                continue
            txns.append(_txn(
                date=_date_mdy(row["Date"]),
                account="NewBroker",
                symbol=(row.get("Symbol") or "").strip(),
                action=action,
                quantity=_num(row.get("Quantity", "")),
                price=_num(row.get("Price", "")),
                fees=_num(row.get("Fees", "")),
                amount=_num(row.get("Amount", "")),
                description=(row.get("Description") or "").strip(),
                source=filepath.name,
            ))
    return txns
```

**Critical invariants for the parser:**

- **Quantities, amounts, and fees must be non-negative after parsing.** Direction
  is carried by the `action`, not by sign. `abs()` these fields on the way in.
- **Split ambiguous actions by sign BEFORE abs().** If one raw action means both
  inflow and outflow (e.g. an ACH that's deposit-or-withdrawal, a generic
  Transfer, a Coinbase Convert), emit two distinct action strings based on the
  raw amount sign (`"ACH Deposit"` vs `"ACH Withdrawal"`) *before* you abs the
  amount. Doing this downstream is a bug. See the "Ambiguous actions" list in
  CLAUDE.md for the existing precedents.
- For corporate actions (mergers, splits, spinoffs, cash-in-lieu), reuse the
  helpers in `src/reorgs.py` rather than inlining pairing/classification logic.

## 5. Register in the dispatch table — `src/parsers/__init__.py`

Import and add one row to `_PARSERS` (key must match step 1's broker key):

```python
from .newbroker import parse_newbroker
...
_PARSERS = {
    ...
    "newbroker": parse_newbroker,
}
```

## 6. Normalization rules — `src/normalize.py`

Add rules to `RULES` mapping the broker's **raw** action strings to canonical
actions (`Buy`, `Sell`, `Dividend`, `Deposit`, `Withdrawal`, `Transfer In/Out`,
`Contribution`, `Reinvest`, `Neutral`, …). **Scope each rule by `account_group`**
so you don't collide with another broker's action names. First match wins:

```python
{"account_group": "NewBroker", "action": "Bought", "normalized": "Buy"},
{"account_group": "NewBroker", "action": "Sold",   "normalized": "Sell"},
```

Matchers (all optional): `account_group`, `action` (case-insensitive on the raw
action), `symbol`, `amount_sign` (`"+"`/`"-"`/`"0"`), `description` (substring).
Unmatched actions pass through title-cased and show up un-normalized in the
dashboard — that's your signal to add a rule.

If you need a **brand-new canonical action** (not in the existing vocabulary),
add an `Action(...)` row to `_ACTIONS` in `src/actions.py` first (set `balance` /
`basis` / `cash_flow` / `color`) — that single source threads it through every
consumer. See the `fin-add-action` recipe / CLAUDE.md "Adding a new normalized
action".

## 7. Test it

1. **Unit test the parser** with a synthetic fixture CSV. Mirror an existing test
   (e.g. `tests/test_voya_parser.py`) and the `write_*_csv` helpers in
   `tests/conftest.py`. Cover every action type, especially any directional split
   from step 4.
2. **Add the broker to the sample portfolio** so the shipped snapshot exercises
   it: add a per-broker CSV writer in `tools/build_sample_snapshot.py` (these
   writers double as format documentation), then rebuild and smoke-test:

   ```bash
   python tools/build_sample_snapshot.py
   python -m pytest tests/ -q
   ```

   (Run the full-pipeline smoke test in a scratch dir per the `fin-dev-loop`
   skill — never against the user's real `data/`.)

## Done when

- `python -m pytest tests/ -q` is green, including `test_pipeline_snapshot.py`.
- A sample run parses the new broker's rows with correct signs/actions and the
  account's balances reconcile (no spurious negative balances, no un-normalized
  actions leaking into the dashboard).
