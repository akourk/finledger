"""Validate explicit account categories and prepare local starter mappings."""

import csv
from pathlib import Path

from .config import ACCOUNT_GROUPS, ACCOUNT_TYPES

ACCOUNT_CATEGORIES = frozenset({"Taxable", "Retirement", "Savings"})

# Only parser names that explicitly identify an account category. A generic
# broker such as Robinhood can offer both taxable and retirement accounts.
_INTRINSIC_TYPES = {
    "Schwab Rollover IRA": "Retirement", "Schwab Roth IRA": "Retirement",
    "USAA Roth IRA": "Retirement", "Vanguard 401K": "Retirement",
    "Voya 401K": "Retirement", "Apple Savings": "Savings",
    "State Farm FCU Savings": "Savings", "Coinbase": "Taxable",
    "Coinbase Pro": "Taxable",
}


def configure_accounts(txns: list[dict], metadata: dict) -> None:
    """Load this run's mappings, validate every group, then tag transactions."""
    groups = metadata.get("account_groups", {})
    types = metadata.get("account_types", {})
    if not isinstance(groups, dict) or not isinstance(types, dict):
        raise ValueError("Account mappings must be dictionaries")
    if any(not isinstance(key, str) or not key.strip()
           or not isinstance(value, str) or not value.strip()
           for key, value in groups.items()):
        raise ValueError("Account Group entries need nonempty names and groups")
    if any(value not in ACCOUNT_CATEGORIES for value in types.values()):
        raise ValueError("Account Type must be Taxable, Retirement, or Savings")
    missing = sorted({groups.get(txn["account"], txn["account"]) for txn in txns}
                     - types.keys())
    if missing:
        raise ValueError("Missing Account Type mappings for: " + ", ".join(missing)
                         + ". Run --init-account-mappings, review the starter "
                         "CSV, and add its rows to data/metadata.csv.")
    ACCOUNT_GROUPS.clear()
    ACCOUNT_GROUPS.update(groups)
    ACCOUNT_TYPES.clear()
    ACCOUNT_TYPES.update(types)
    for txn in txns:
        group = groups.get(txn["account"], txn["account"])
        txn["account_group"] = group
        txn["account_type"] = types[group]


def write_account_mapping_starter(txns: list[dict], metadata: dict,
                                 output_path: Path) -> None:
    """Write suggestions without modifying an existing metadata file.

    Unknown/generic broker accounts get REVIEW_REQUIRED. The resulting
    metadata intentionally fails validation until the user selects a type.
    """
    groups = metadata.get("account_groups", {})
    types = metadata.get("account_types", {})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Type", "Date", "Amount", "Symbol", "Note"])
        seen = set()
        for account in sorted({txn["account"] for txn in txns}):
            group = groups.get(account, account)
            writer.writerow(["Account Group", "", "", account, group])
            if group not in seen:
                suggested = types.get(group) or _INTRINSIC_TYPES.get(account, "REVIEW_REQUIRED")
                writer.writerow(["Account Type", "", "", group, suggested])
                seen.add(group)
