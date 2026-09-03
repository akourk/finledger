"""State Farm Federal Credit Union share savings account.

The online-banking platform exports a generic ``ExportedTransactions.csv``
with these columns::

    Transaction ID, Posting Date, Effective Date, Transaction Type,
    Posting Status, Amount, Check Number, Reference Number, Description,
    Transaction Category, Type, Balance, Memo, Extended Description

Rows are newest-first, and ``Balance`` is a running balance in file
order.  Everything in a share savings account is a USD cash movement, so
every row parses to ``symbol="USD"`` with ``price=1.0`` and
``quantity == amount`` — the same shape the Apple Savings parser emits.

**Direction** is the one thing that has to be right (a sign error here
invents portfolio value — see ``cash_bridge``'s docstring).  Two
independent signals carry it, and neither is guaranteed present:

* ``Transaction Type`` is the label — ``Credit`` / ``Debit``.
* ``Amount`` may be signed, or may be a positive magnitude leaning on
  the label.

The observed export writes ``Credit`` with a positive magnitude, but a
negative amount is unambiguous whatever the label says, so the sign wins
when the two disagree.  ``Balance`` then gives an independent check:
:func:`_check_balance_continuity` re-walks the parsed rows against it and
warns loudly if they do not reconcile, which is what would catch a
direction convention this parser has not seen.

**Action vocabulary** is deliberately bounded — ``Dividend`` /
``Deposit`` / ``Withdrawal`` / ``Fee``.  The broker's own ``Type`` column
is open-ended (``ACH``, ``Dividends``, and whatever the credit union
adds next), so an unrecognized type falls back to plain direction, which
is always correct for a cash account.  The original ``Type`` is
preserved in the description for traceability.

Credit-union "dividends" on a share account are economically interest
and are reported on a 1099-INT, so they normalize to ``Interest``, not
``Dividend`` — see the rules in :mod:`src.normalize`.

**Known limitation**: the export carries no account identifier column,
so a second account at the same credit union (IRA Savings, E-Shares)
exports in this identical format and would be parsed into the same
account.  Supporting that needs a per-file account hint.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ._helpers import Transaction, _date_mdy, _num, _txn


# The account name the parser stamps on every row.  ``normalize.RULES``
# scopes this broker's rules to the matching ``account_group``, and an
# account with no ``Account Group`` metadata row falls back to its raw
# account name — so keeping the two identical means the rules fire with
# or without that row.  Only the ``Account Type`` row (-> ``Savings``) is
# strictly required, and without it the USD balance is not tracked at
# all (see ``pipeline_stages.walk_balances``).
ACCOUNT = "State Farm FCU Savings"

# Rows in these posting states are provisional: the amount can still
# change, and the posted row arrives later carrying its own transaction
# id.  Ingesting both would double-count, and the hash dedupe cannot
# collapse them because the two rows genuinely differ.  Anything not
# listed here parses, so an unfamiliar status is never silently dropped.
_PENDING_STATUSES = {"pending", "memo", "hold", "authorized"}

# Matched against the structured ``Type`` / ``Transaction Category``
# columns only — never the free-text description, where a merchant name
# ("COFFEE") would false-positive on a substring test.
_FEE_RE = re.compile(r"\b(fee|fees|charge|charges|nsf|penalty)\b", re.I)
_INTEREST_RE = re.compile(r"\b(dividend|dividends|interest)\b", re.I)

# Balance-continuity tolerance: half a cent, so ordinary rounding in the
# export cannot trip the warning.
_BALANCE_EPSILON = 0.005


def _classify(is_debit: bool, kind: str) -> str:
    """Raw action for a row, from its direction and its ``Type`` /
    ``Transaction Category`` text.

    Income is recognized only on credits and fees only on debits — a
    reversed dividend is a withdrawal of cash, not negative income.
    """
    if not is_debit and _INTEREST_RE.search(kind):
        return "Dividend"
    if is_debit and _FEE_RE.search(kind):
        return "Fee"
    return "Withdrawal" if is_debit else "Deposit"


def _check_balance_continuity(rows: list[tuple[float, float]],
                              filename: str) -> None:
    """Warn when the parsed signed amounts do not re-walk the export's
    own ``Balance`` column.

    ``rows`` is ``(signed_amount, reported_balance)`` in oldest-first
    order.  The first row seeds the walk (there is no prior balance to
    check it against); every later row must land on its reported
    balance.  A mismatch means this parser read a direction the credit
    union did not intend — the one failure mode that would quietly
    invent or destroy portfolio value.
    """
    prior = None
    for signed, reported in rows:
        if reported == 0.0:
            # No balance reported for this row — cannot check it, and
            # cannot trust it as the base for the next one either.
            prior = None
            continue
        if prior is not None and abs(prior + signed - reported) > _BALANCE_EPSILON:
            print(f"  !! WARNING: {filename} — parsed amounts do not "
                  f"reconcile against the export's own Balance column. "
                  f"The Credit/Debit convention has probably changed, "
                  f"which means transaction DIRECTIONS may be wrong. "
                  f"Check this account's balance before trusting it.")
            return
        prior = reported


def parse_sfcu(filepath: Path) -> list[Transaction]:
    txns: list[Transaction] = []
    checked: list[tuple[float, float]] = []

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            status = (row.get("Posting Status") or "").strip().lower()
            if status in _PENDING_STATUSES:
                continue

            raw_date = ((row.get("Posting Date") or "").strip()
                        or (row.get("Effective Date") or "").strip())
            try:
                date = _date_mdy(raw_date)
            except (ValueError, TypeError):
                continue

            raw_amount = _num(row.get("Amount", ""))
            if raw_amount == 0:
                # Zero-dollar informational rows (status changes,
                # notices) move no money and would only add ledger noise.
                continue

            ttype = (row.get("Transaction Type") or "").strip().lower()
            # A negative amount is unambiguous whatever the label says;
            # otherwise the label decides, defaulting to a credit.
            is_debit = raw_amount < 0 or ttype == "debit"
            amount = abs(raw_amount)

            kind = " ".join(filter(None, [
                (row.get("Type") or "").strip(),
                (row.get("Transaction Category") or "").strip(),
            ]))
            action = _classify(is_debit, kind)

            description = " ".join((
                (row.get("Description") or "").strip()
                or (row.get("Extended Description") or "").strip()
                or (row.get("Memo") or "").strip()
            ).split())
            if kind and kind.lower() not in description.lower():
                description = f"{kind}: {description}" if description else kind

            txns.append(_txn(
                date=date,
                account=ACCOUNT,
                symbol="USD",
                action=action,
                quantity=amount,
                price=1.0,
                # `fees` is informational only (never applied to the
                # balance — see analytics.savings.compute_fees), so
                # stamping it alongside the Fee action's own balance
                # subtraction surfaces the drag without double-counting.
                fees=amount if action == "Fee" else 0.0,
                amount=amount,
                description=description,
                source=filepath.name,
            ))
            checked.append((-amount if is_debit else amount,
                            _num(row.get("Balance", ""))))

    # The export is newest-first; the balance walk needs oldest-first.
    _check_balance_continuity(checked[::-1], filepath.name)
    return txns
