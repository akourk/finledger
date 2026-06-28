"""Single source of truth for the canonical action vocabulary.

Every canonical action has THREE orthogonal effects on the pipeline:

1. **balance**: how the running share-balance walker treats it
     - "add"      → ``balance += qty``
     - "subtract" → ``balance -= qty``
     - "neutral"  → no change

2. **basis**: how the FIFO/LIFO/HIFO/Avg cost-basis walker treats it
     - "add"          → push a new lot at the txn's per-share basis
     - "remove"       → consume the front lot(s), realize gain
     - "zero_basis"   → push a new lot at $0 basis (gifted/spinoff shares)
     - "transfer_out" → release lot(s) to a paired "transfer_in"
     - "transfer_in"  → receive lot(s) from a paired "transfer_out"
     - "split"        → adjust qty and basis-per-share by the split ratio
     - "ignore"       → not a share-level event (cash flows on USD, etc.)
     - "unknown"      → flag for manual review

3. **cash_flow**: how the TWR / Net-Contributed walkers treat it
     - "in"      → external money flowing INTO the account
                   (deposits, contributions, USAA-style buy-with-marker)
     - "out"     → external money flowing OUT
     - "neutral" → internal account activity
                   (dividends, reinvests, sells — already in current value)
     - "ignore"  → not user-attributable (fees, internal transfers)

Plus presentation metadata:
- **color**: hex code used in the dashboard's transactions ledger
- **description**: human-readable note used by docs

Per-action invariants the rest of the pipeline relies on are documented
on each row.  Adding a new action is a one-file change; downstream
modules pull their action sets from helper accessors at the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BalanceEffect = Literal["add", "subtract", "neutral"]
BasisEffect = Literal[
    "add", "remove", "zero_basis", "transfer_out", "transfer_in",
    "split", "ignore", "unknown",
    # Crypto wrap/unwrap (e.g. ETH ↔ CBETH): a basis-CARRYING conversion,
    # NOT a taxable disposal.  The walker processes each (account, date,
    # direction) wrap group atomically — consume the source lots with no
    # realized gain, carry the total basis (rescaled to the destination
    # quantity, dates preserved) to the destination symbol.  Matches how
    # brokers (Coinbase 1099-DA) report wrapping: non-taxable, basis flows
    # through, gain deferred to the eventual real sale.
    "wrap_out", "wrap_in",
]
CashFlowEffect = Literal["in", "out", "neutral", "ignore"]


@dataclass(frozen=True)
class Action:
    name: str
    balance: BalanceEffect
    basis: BasisEffect
    cash_flow: CashFlowEffect
    color: str = "#9ca3af"
    description: str = ""
    # Income bucket this action contributes to (dividends / interest /
    # rewards / lending), or None for non-income actions.  Single source
    # of truth for the Income tab + cash-summary income tally — the
    # consumer sets (basis._INCOME_ACTIONS, _shared.INCOME_ACTION_KINDS,
    # income_calendar._INCOME_ACTIONS) all derive from this field.
    income: str | None = None


# ---------------------------------------------------------------------------
# Canonical action catalog
# ---------------------------------------------------------------------------
# Order roughly groups: trades → cash flows → corporate actions → other.
# The dashboard's color palette is preserved by keying off `name`.

_ACTIONS: tuple[Action, ...] = (
    # ── Trades (share-level, basis-affecting) ──────────────────────────────
    Action("Buy",            "add",      "add",     "neutral", "#4ade80"),
    Action("Sell",           "subtract", "remove",  "neutral", "#f87171"),
    Action("Reinvest",       "add",      "add",     "neutral", "#4ade80",
           "Auto-reinvested distribution → more shares; no external cash."),

    # ── External cash flows ────────────────────────────────────────────────
    # Note on Deposit's basis effect: Coinbase "Deposit" on a crypto
    # symbol = wallet-arrival → transfer_in.  USD deposits get auto-
    # routed to "ignore" by the symbol-aware override in basis._basis_effect.
    Action("Deposit",        "add",      "transfer_in", "in",  "#60a5fa"),
    Action("Withdrawal",     "subtract", "ignore",  "out",     "#fb923c",
           "USD cash out; crypto wallet sends are normalized to "
           "Transfer Out by the parsers, so 'ignore' here is correct."),
    Action("Contribution",   "add",      "add",     "in",      "#a78bfa",
           "Retirement contribution — adds to lots AND counts as inflow."),
    Action("Distribution",   "subtract", "remove",  "out",     "#f97316",
           "Retirement distribution.  TWR walker special-cases this for "
           "Roth/Rollover IRA accounts (custodian rollover, not real "
           "withdrawal) — see analytics.net_cash_flow."),
    Action("Contribution Reversal", "subtract", "remove", "out", "#c084fc",
           "Voya-style negative-CONTRIBUTION row (employer/admin error "
           "reversal).  Negates a prior contribution end-to-end: shares, "
           "basis, and net-contributed all decrement."),

    # ── Income (cash flowing in via dividend/interest/etc.) ────────────────
    Action("Dividend",       "add",      "add",     "neutral", "#34d399",
           "Mutual-fund dividend reinvested as shares; for cash dividends "
           "(symbol=USD) the basis walker auto-routes to 'ignore'.",
           income="dividends"),
    Action("Interest",       "add",      "add",     "neutral", "#2dd4bf",
           income="interest"),
    Action("Reward",         "add",      "zero_basis", "neutral", "#fbbf24",
           "Stake/airdrop/loyalty reward — FMV-at-receipt if known, "
           "else 0 cost basis.",
           income="rewards"),
    Action("Lending",        "add",      "add",     "neutral", "#67e8f9",
           income="lending"),

    # ── Account-internal movements ─────────────────────────────────────────
    Action("Transfer In",    "add",      "transfer_in",  "ignore", "#818cf8",
           "Custodian-to-custodian move; basis carries via _pair_transfers."),
    Action("Transfer Out",   "subtract", "transfer_out", "ignore", "#c084fc"),
    Action("Synthetic Transfer Out", "subtract", "transfer_out", "ignore", "#c084fc",
           "Reconciliation row for USAA→Schwab transfers where USAA's "
           "CSV was missing the outbound leg — see "
           "main._reconcile_usaa_to_schwab_transfer."),
    Action("Trade Settle In",  "add",      "ignore",     "neutral", "#9ca3af",
           "Cash leg of a paired-trade match (e.g. Coinbase Pro: a Sell "
           "ETH match emits both the crypto leg AND a USD leg — this "
           "is the USD side).  Intra-account, NOT external money in.  "
           "cash_flow=neutral so it stays out of CASH_ADD_ACTIONS / "
           "net_contributed; balance still increments since USD shows "
           "up in the account.  basis=ignore (USD has no basis)."),
    Action("Trade Settle Out", "subtract", "ignore",     "neutral", "#9ca3af",
           "Cash leg of a paired-trade match — USD-out side of e.g. a "
           "Coinbase Pro Buy.  See Trade Settle In for the rationale "
           "around cash_flow=neutral."),

    # ── Corporate actions ──────────────────────────────────────────────────
    Action("Return of Capital", "neutral", "ignore",  "neutral", "#5eead4",
           "Non-dividend distribution (Robinhood ROC) — cash paid out of "
           "the company's capital, not earnings.  Shares unchanged.  "
           "Strictly it lowers cost basis, but we leave basis untouched "
           "(basis='ignore'), consistent with the app's deliberate "
           "non-tracking of taxable-account cash: sell proceeds and cash "
           "dividends aren't tracked either.  NOT income — excluded from "
           "the Income tab.  cash_flow='neutral' keeps it out of "
           "net_contributed / performance contribution accounting."),
    Action("Split",          "add",      "split",     "neutral", "#fbbf24",
           "Forward stock split — adds new shares, scales basis-per-share."),
    Action("Spinoff",        "add",      "zero_basis", "neutral", "#9ca3af",
           "Shares received in spin-off / SOFF / SPR-receive at $0 basis."),
    Action("Conversion",     "add",      "zero_basis", "neutral", "#e879f9",
           "Shares moved during account migration (e.g. Apex→RHS) — "
           "treated as zero-basis acquisition since prior basis is lost."),
    Action("Merger",         "add",      "zero_basis", "neutral", "#9ca3af"),

    # ── Crypto specifics ───────────────────────────────────────────────────
    # Convert (different underlying assets, e.g. BTC→ETH) stays a taxable
    # disposal per IRS — Convert Out realizes gain, Convert In gets FMV
    # basis.  (ETH↔ETH2 same-asset converts are caught upstream and marked
    # Neutral by the parser.)
    Action("Convert In",     "add",      "add",       "neutral", "#e879f9"),
    Action("Convert Out",    "subtract", "remove",    "neutral", "#e879f9"),
    # Wrap / Unwrap (same underlying, e.g. ETH↔CBETH) is basis-CARRYING,
    # not a taxable disposal — see the wrap_out/wrap_in note above.
    Action("Wrap Asset In",  "add",      "wrap_in",   "neutral", "#e879f9"),
    Action("Wrap Asset Out", "subtract", "wrap_out",  "neutral", "#e879f9"),
    Action("Unwrap In",      "add",      "wrap_in",   "neutral", "#e879f9"),
    Action("Unwrap Out",     "subtract", "wrap_out",  "neutral", "#e879f9"),

    # ── Options ────────────────────────────────────────────────────────────
    Action("Option Buy",     "add",      "add",     "neutral", "#86efac"),
    Action("Option Sell",    "subtract", "remove",  "neutral", "#fca5a5"),
    Action("Option Expire",  "subtract", "remove",  "neutral", "#9ca3af",
           "Contract expires worthless — proceeds=0, full basis realized."),
    Action("Option Exercise","subtract", "remove",  "neutral", "#fcd34d",
           "Cash-settled exercise — proceeds = OCC cash component."),

    # ── Other ──────────────────────────────────────────────────────────────
    Action("Event Contract Transfer", "neutral", "ignore", "ignore", "#94a3b8",
           "Robinhood FUTSWP — 'Event Contracts Inter-Entity Cash "
           "Transfer'.  Cash shuffled between the main brokerage and the "
           "separate event-contracts (prediction-markets / futures) "
           "entity.  Both sides are the user's own money, so it's NOT "
           "external cash flow — and the event-contracts world has no "
           "positions imported here.  Fully inert: balance='neutral', "
           "basis='ignore', cash_flow='ignore' so event-contract activity "
           "is excluded from net_contributed, TWR, Sharpe, FIRE, and every "
           "other performance metric (per user: not tracking these)."),
    Action("Fee",            "subtract", "remove",  "ignore",  "#f87171",
           "Share-level fee (rare); USD fees route to basis 'ignore'."),
    Action("Tax",            "subtract", "remove",  "ignore",  "#fb7185"),
    Action("Other",          "add",      "add",     "neutral", "#6b7280",
           "Voya residual rounding events (tiny fractional shares from "
           "fund accounting) and parser fallthroughs."),
    Action("Neutral",        "neutral",  "ignore",  "ignore",  "#9ca3af",
           "Bookkeeping rows that mustn't change balance or basis "
           "(e.g. ETH↔ETH2 wrapper relabels, intra-group transfer noise)."),
)


# ---------------------------------------------------------------------------
# Lookup helpers — DOWNSTREAM MODULES SHOULD IMPORT FROM HERE
# ---------------------------------------------------------------------------

ACTIONS: dict[str, Action] = {a.name: a for a in _ACTIONS}


def names_with_balance_effect(effect: BalanceEffect) -> frozenset[str]:
    return frozenset(a.name for a in _ACTIONS if a.balance == effect)


def names_with_basis_effect(effect: BasisEffect) -> frozenset[str]:
    return frozenset(a.name for a in _ACTIONS if a.basis == effect)


def names_with_cash_flow(effect: CashFlowEffect) -> frozenset[str]:
    return frozenset(a.name for a in _ACTIONS if a.cash_flow == effect)


# Pre-baked sets that match the names downstream modules already use.
# Keeping these as module-level constants lets imports stay terse.

SUBTRACT_ACTIONS = names_with_balance_effect("subtract")
NEUTRAL_ACTIONS  = names_with_balance_effect("neutral")
ADD_ACTIONS      = names_with_balance_effect("add")

CASH_ADD_ACTIONS = names_with_cash_flow("in")
CASH_SUB_ACTIONS = names_with_cash_flow("out")

BASIS_EFFECTS: dict[str, str] = {a.name: a.basis for a in _ACTIONS}

ACTION_COLORS: dict[str, str] = {a.name: a.color for a in _ACTIONS}

# Income classification — single source of truth derived from the
# `income` field.  `INCOME_ACTION_KINDS` maps action name → bucket;
# `INCOME_ACTIONS` is just the key set.  Downstream income consumers
# (basis.compute_cash_summary, analytics.income, income_calendar)
# import these instead of each maintaining their own hardcoded set.
INCOME_ACTION_KINDS: dict[str, str] = {
    a.name: a.income for a in _ACTIONS if a.income
}
INCOME_ACTIONS = frozenset(INCOME_ACTION_KINDS)


def to_json_dict() -> dict:
    """Serialize the catalog to a plain dict for embedding in the JSON
    export.  The dashboard JS reads this so the action vocabulary is
    truly single-source.
    """
    return {
        "actions": [
            {
                "name": a.name,
                "balance": a.balance,
                "basis": a.basis,
                "cash_flow": a.cash_flow,
                "color": a.color,
                "description": a.description,
                "income": a.income,
            }
            for a in _ACTIONS
        ],
    }
