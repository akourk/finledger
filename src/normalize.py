"""Action normalization — map raw broker actions to a consistent vocabulary.

The RULES table is checked top-to-bottom; first match wins.  Each rule is a
dict with **matchers** (all optional — omit a key to match anything) and a
**normalized** result string.

Matchers
--------
  account_group : exact string match on the account_group field
  action        : case-insensitive match on the *raw* action (before normalization)
  symbol        : exact match (after symbol normalization, e.g. "ETH-USD")
  amount_sign   : "+", "-", or "0" — sign of the amount field
  description   : substring search (case-insensitive) in the description field

If no rule matches a transaction, the raw action is kept as-is so you can
spot un-mapped actions in the dashboard and add rules for them.
"""

from .config import CASH_SYMBOLS

# ── Canonical action vocabulary ──────────────────────────────────────────
#
#   buy            — purchased an asset
#   sell           — sold an asset
#   deposit        — cash/crypto deposited into the account
#   withdrawal     — cash/crypto withdrawn out of the account
#   transfer_in    — asset moved in from another owned account
#   transfer_out   — asset moved out to another owned account
#   dividend       — cash dividend
#   interest       — interest earned
#   fee            — fee charged
#   tax            — tax withheld
#   contribution   — retirement account contribution
#   distribution   — retirement account distribution/withdrawal
#   reinvest       — dividend/gain automatically reinvested
#   split          — stock split
#   conversion     — asset converted to another asset
#   reward         — staking / promo reward
#   option_buy     — bought an option contract
#   option_sell    — sold/closed an option contract
#   option_expire  — option expired worthless
#   option_exercise — option exercised
#   merger         — corporate action / merger
#   spinoff        — corporate spinoff
#   lending        — stock lending income
#   other          — anything else
# ─────────────────────────────────────────────────────────────────────────

RULES = [
    # ── Robinhood ────────────────────────────────────────────────────────
    {"account_group": "Robinhood", "action": "Buy",   "normalized": "Buy"},
    {"account_group": "Robinhood", "action": "Sell",   "normalized": "Sell"},
    {"account_group": "Robinhood", "action": "BTO",   "normalized": "Option Buy"},
    {"account_group": "Robinhood", "action": "STC",   "normalized": "Option Sell"},
    {"account_group": "Robinhood", "action": "OEXP",  "normalized": "Option Expire"},
    {"account_group": "Robinhood", "action": "OEXCS", "normalized": "Option Exercise"},
    {"account_group": "Robinhood", "action": "OCC",   "normalized": "Option Exercise"},
    {"account_group": "Robinhood", "action": "ACH Deposit",    "normalized": "Deposit"},
    {"account_group": "Robinhood", "action": "ACH Withdrawal", "normalized": "Withdrawal"},
    {"account_group": "Robinhood", "action": "CDIV",  "normalized": "Dividend"},
    {"account_group": "Robinhood", "action": "MDIV",  "normalized": "Dividend"},
    {"account_group": "Robinhood", "action": "SCAP",  "normalized": "Dividend"},
    {"account_group": "Robinhood", "action": "INT",   "normalized": "Interest"},
    {"account_group": "Robinhood", "action": "SLIP",  "normalized": "Lending"},
    {"account_group": "Robinhood", "action": "SPL",   "normalized": "Split"},
    {"account_group": "Robinhood", "action": "SPR",   "normalized": "Spinoff"},
    {"account_group": "Robinhood", "action": "SOFF",  "normalized": "Spinoff"},
    {"account_group": "Robinhood", "action": "MRGC",  "normalized": "Merger"},
    {"account_group": "Robinhood", "action": "MRGS",  "normalized": "Merger"},
    {"account_group": "Robinhood", "action": "SXCH",  "normalized": "Merger"},
    {"account_group": "Robinhood", "action": "CIL",   "normalized": "Merger"},
    {"account_group": "Robinhood", "action": "REC",   "normalized": "Merger"},
    {"account_group": "Robinhood", "action": "LIQ",   "normalized": "Sell"},
    {"account_group": "Robinhood", "action": "AFEE",  "normalized": "Fee"},
    {"account_group": "Robinhood", "action": "DFEE",  "normalized": "Fee"},
    {"account_group": "Robinhood", "action": "DTAX",  "normalized": "Tax"},
    {"account_group": "Robinhood", "action": "CONV",  "normalized": "Conversion"},
    {"account_group": "Robinhood", "action": "ROC",   "normalized": "Return of Capital"},
    # FUTSWP = "Event Contracts Inter-Entity Cash Transfer" — internal
    # cash move to/from the prediction-markets entity; fully excluded
    # from performance (see actions.py "Event Contract Transfer").
    {"account_group": "Robinhood", "action": "FUTSWP", "normalized": "Event Contract Transfer"},
    # MISC = promotional cash rewards (e.g. the prediction-markets
    # learning bonus) — count as reward income.
    {"account_group": "Robinhood", "action": "MISC",  "normalized": "Reward"},

    # ── Coinbase (regular) ───────────────────────────────────────────────
    {"account_group": "Coinbase", "action": "Buy",                          "normalized": "Buy"},
    {"account_group": "Coinbase", "action": "Sell",                         "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "Advanced Trade Sell",          "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "Convert Out",                    "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "Convert In",                     "normalized": "Buy"},
    # ETH↔ETH2 conversions are the same underlying asset — treat as neutral
    {"account_group": "Coinbase", "action": "Convert Out Neutral",            "normalized": "Neutral"},
    {"account_group": "Coinbase", "action": "Unwrap Out",                     "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "Unwrap In",                      "normalized": "Buy"},
    {"account_group": "Coinbase", "action": "Wrap Asset Out",                 "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "Wrap Asset In",                  "normalized": "Buy"},
    {"account_group": "Coinbase", "action": "Retail Eth2 Deprecation",      "normalized": "Neutral"},
    {"account_group": "Coinbase", "action": "Send",                         "normalized": "Transfer Out"},
    {"account_group": "Coinbase", "action": "Receive",                      "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Deposit",                      "normalized": "Deposit"},
    {"account_group": "Coinbase", "action": "Staking Income",               "normalized": "Reward"},
    {"account_group": "Coinbase", "action": "Reward Income",                "normalized": "Reward"},
    {"account_group": "Coinbase", "action": "Subscription Rebates (24 Hours)", "normalized": "Reward"},
    {"account_group": "Coinbase", "action": "Retail Staking Transfer Out",  "normalized": "Transfer Out"},
    {"account_group": "Coinbase", "action": "Retail Staking Transfer In",   "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Retail Unstaking Transfer Out", "normalized": "Transfer Out"},
    {"account_group": "Coinbase", "action": "Retail Unstaking Transfer In",  "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Transfer In",                  "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Transfer Out",                 "normalized": "Transfer Out"},
    # Vault withdrawal is an internal Coinbase vault movement with no matching
    # vault-deposit leg in exports; treat as neutral bookkeeping.
    {"account_group": "Coinbase", "action": "Vault Withdrawal",             "normalized": "Neutral"},

    # Coinbase withdrawals: crypto = Transfer Out, USD = Withdrawal
    {"account_group": "Coinbase", "action": "Withdrawal", "symbol": "USD",  "normalized": "Withdrawal"},
    {"account_group": "Coinbase", "action": "Withdrawal",                   "normalized": "Transfer Out"},

    # Pro/Exchange internal transfers — names are from Pro's perspective,
    # but entries are in Coinbase's ledger so directions are inverted:
    #   "Pro Withdrawal"      = withdrew FROM Pro  → arrives in Coinbase  = Transfer In
    #   "Pro Deposit"         = deposited INTO Pro → leaves Coinbase      = Transfer Out
    #   "Exchange Withdrawal" = withdrew FROM GDAX → arrives in Coinbase  = Transfer In
    #   "Exchange Deposit"    = deposited INTO GDAX→ leaves Coinbase      = Transfer Out
    {"account_group": "Coinbase", "action": "Pro Withdrawal",              "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Pro Deposit",                  "normalized": "Transfer Out"},
    {"account_group": "Coinbase", "action": "Exchange Withdrawal",          "normalized": "Transfer In"},
    {"account_group": "Coinbase", "action": "Exchange Deposit",             "normalized": "Transfer Out"},

    # Coinbase Pro (lowercase actions from GDAX CSV format)
    {"account_group": "Coinbase", "action": "buy",                          "normalized": "Buy"},
    {"account_group": "Coinbase", "action": "sell",                         "normalized": "Sell"},
    {"account_group": "Coinbase", "action": "fee",                          "normalized": "Fee"},
    {"account_group": "Coinbase", "action": "deposit",                      "normalized": "Deposit"},
    {"account_group": "Coinbase", "action": "withdrawal", "symbol": "USD",  "normalized": "Withdrawal"},
    {"account_group": "Coinbase", "action": "withdrawal",                   "normalized": "Transfer Out"},
    # Cash legs of trade matches (parser-emitted) — see parsers/coinbase.py
    {"account_group": "Coinbase", "action": "trade_settle_in",              "normalized": "Trade Settle In"},
    {"account_group": "Coinbase", "action": "trade_settle_out",             "normalized": "Trade Settle Out"},

    # ── Schwab / Roth IRA ────────────────────────────────────────────────
    {"account_group": "Roth IRA", "action": "Buy",                          "normalized": "Buy"},
    {"account_group": "Roth IRA", "action": "Reinvest Dividend",            "normalized": "Reinvest"},
    {"account_group": "Roth IRA", "action": "Reinvest Shares",              "normalized": "Reinvest"},
    {"account_group": "Roth IRA", "action": "Long Term Cap Gain Reinvest",  "normalized": "Reinvest"},
    {"account_group": "Roth IRA", "action": "Short Term Cap Gain Reinvest", "normalized": "Reinvest"},
    {"account_group": "Roth IRA", "action": "Dividend",                     "normalized": "Dividend"},
    {"account_group": "Roth IRA", "action": "Bank Interest",                "normalized": "Interest"},
    {"account_group": "Roth IRA", "action": "MoneyLink Transfer",           "normalized": "Contribution"},
    {"account_group": "Roth IRA", "action": "Security Transfer",            "normalized": "Transfer In"},
    {"account_group": "Roth IRA", "action": "Stock Split",                  "normalized": "Split"},
    {"account_group": "Roth IRA", "action": "Fee",                          "normalized": "Fee"},
    {"account_group": "Roth IRA", "action": "Synthetic Transfer Out",       "normalized": "Transfer Out"},
    {"account_group": "Roth IRA", "action": "Transfer Reconcile",           "normalized": "Neutral"},

    # ── Schwab / Rollover IRA (from Voya) ────────────────────────────────
    {"account_group": "Rollover IRA", "action": "Buy",                      "normalized": "Buy"},
    {"account_group": "Rollover IRA", "action": "CONTRIBUTION",             "normalized": "Contribution"},
    {"account_group": "Rollover IRA", "action": "CONTRIBUTION REVERSAL",    "normalized": "Contribution Reversal"},
    {"account_group": "Rollover IRA", "action": "DIVIDEND",                 "normalized": "Dividend"},
    {"account_group": "Rollover IRA", "action": "DIVIDEND REVERSAL",        "normalized": "Fee"},
    {"account_group": "Rollover IRA", "action": "WITHDRAWAL",               "normalized": "Distribution"},
    {"account_group": "Rollover IRA", "action": "TRANSFER IN",              "normalized": "Transfer In"},
    {"account_group": "Rollover IRA", "action": "TRANSFER OUT",             "normalized": "Transfer Out"},
    {"account_group": "Rollover IRA", "action": "OTHER",                    "normalized": "Other"},
    {"account_group": "Rollover IRA", "action": "Reinvest Dividend",        "normalized": "Reinvest"},
    {"account_group": "Rollover IRA", "action": "Reinvest Shares",          "normalized": "Reinvest"},
    {"account_group": "Rollover IRA", "action": "Bank Interest",            "normalized": "Interest"},
    {"account_group": "Rollover IRA", "action": "Funds Received",           "normalized": "Transfer In"},
    {"account_group": "Rollover IRA", "action": "Stock Split",              "normalized": "Split"},

    # ── Vanguard 401K ────────────────────────────────────────────────────
    {"account_group": "401K", "action": "Buy",                              "normalized": "Contribution"},

    # ── Apple Savings ────────────────────────────────────────────────────
    {"account_group": "Apple Savings", "action": "Buy",                     "normalized": "Deposit"},
    {"account_group": "Apple Savings", "action": "Sell",                    "normalized": "Withdrawal"},
    {"account_group": "Apple Savings", "action": "Interest",                "normalized": "Interest"},
]


def _amount_sign(txn: dict) -> str:
    """Return '+', '-', or '0' for the amount field."""
    try:
        val = float(txn.get("amount", 0) or 0)
    except (ValueError, TypeError):
        return "0"
    if val > 0:
        return "+"
    if val < 0:
        return "-"
    return "0"


def _matches(rule: dict, txn: dict) -> bool:
    """Check whether every matcher key in the rule matches the transaction."""
    if "account_group" in rule and txn.get("account_group") != rule["account_group"]:
        return False
    if "action" in rule and txn.get("action", "").lower() != rule["action"].lower():
        return False
    if "symbol" in rule and txn.get("symbol") != rule["symbol"]:
        return False
    if "amount_sign" in rule and _amount_sign(txn) != rule["amount_sign"]:
        return False
    if "description" in rule:
        desc = (txn.get("description") or "").lower()
        if rule["description"].lower() not in desc:
            return False
    return True


def normalize_action(txn: dict) -> str:
    """Return the normalized action for a transaction.

    Checks RULES top-to-bottom; first match wins.
    Falls back to the raw action (lowercased, stripped) if nothing matches.
    """
    for rule in RULES:
        if _matches(rule, txn):
            return rule["normalized"]
    # No match — keep raw action as-is, title-cased for consistency
    return (txn.get("action") or "Other").strip().title()
