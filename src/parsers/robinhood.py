"""Robinhood CSV parser — by far the most complex.

Handles option contracts (BTO/STC/OEXP/OEXCS pairing with OCC),
stock mergers (MRGS/MRGC cash + stock-for-stock), cash in lieu (CIL),
spinoffs (SOFF/SPR), conversions (CONV, incl. 2018 Apex→RHS migration),
and cash liquidations (LIQ).  See src/reorgs.py for the pooling
helpers this parser calls into.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._helpers import read_csv_rows
from ._helpers import (
    Transaction, _date_dmy, _date_iso, _date_mdy, _date_ymd,
    _num, _txn,
)


# ---------------------------------------------------------------------------
# Robinhood
# ---------------------------------------------------------------------------
# Headers: Activity Date, Process Date, Settle Date, Instrument, Description,
#          Trans Code, Quantity, Price, Amount

_ROBINHOOD_COLUMNS = (
    "Activity Date", "Process Date", "Settle Date", "Instrument", "Description",
    "Trans Code", "Quantity", "Price", "Amount",
)


def _is_informational_footer(row: dict) -> bool:
    """Recognize Robinhood's disclaimer in an extra, tenth CSV column.

    All nine transaction cells must be blank. Never discard an oversized
    transaction or an unrecognized extra cell, even at the end of a file.
    """
    if set(row) != {*_ROBINHOOD_COLUMNS, None}:
        return False
    if any((row[column] or "").strip() for column in _ROBINHOOD_COLUMNS):
        return False
    extra = row[None]
    return len(extra) == 1 and " ".join(extra[0].split()).startswith(
        "The data provided is for informational purposes only.")


def _parse_option_qty(raw: str, default: float = 1.0) -> float:
    """Parse an option contract quantity from a Robinhood CSV Quantity field.

    Robinhood writes things like ``"1S"`` (1 contract, short leg marker)
    for OEXCS rows, and empty strings for OEXP rows.  Neither is
    parseable by plain ``float()``. Accept only the recognized ``S`` suffix
    after a valid number. Only an entirely blank field uses ``default``;
    a marker without a number and other malformed values must fail.
    """
    s = (raw or "").strip()
    if not s:
        return default
    # Keep the known short/surrender marker distinct from arbitrary numeric
    # prefixes such as '1typo'. A marker alone remains invalid, not blank.
    if s.endswith("S") and s[:-1].strip():
        s = s[:-1]
    from ._helpers import CSVValue
    checked = CSVValue(s, "Robinhood", 0, "Quantity")
    checked.location = getattr(raw, "location", "Robinhood Quantity")
    return abs(_num(checked))


_OPTION_ACTIONS = {"BTO", "STC", "OEXP", "OEXCS"}

# A REC quantity printed with fewer than this many decimals carries no
# useful evidence of rounding — snapping it would move the figure by more
# than half a milli-share, which is a guess rather than a recovery.
_RECEIVE_SNAP_MIN_DECIMALS = 3


def _decimals(raw: str) -> int:
    """Count digits after the decimal point in a CSV numeric field."""
    s = (raw or "").strip().replace(",", "").replace("$", "").lstrip("+-")
    return len(s.split(".", 1)[1]) if "." in s else 0


def _resolve_receive(qty: float, qty_raw: str, companions: list[dict]) -> tuple[float, float]:
    """Recover the true quantity and FMV price of a Robinhood REC row.

    ``REC`` ("received") is shares credited to the account with no cash
    outlay — in practice a promotional credit.  Robinhood writes these
    rows with an empty Price and Amount, and rounds the Quantity to
    fewer decimals than it actually tracks.  Both gaps are recoverable
    when the receive accompanies a same-day purchase of the same
    security, which is the shape a purchase-topping credit takes::

        Buy  0.984963 @ $402.00   ($397.00 cash)
        REC  0.0150               (no price, no amount)   ← a $5 credit
                ↑ true value 0.015037 — the two legs sum to 1.000000

    - **price**: the companion Buy's execution price is the security's
      FMV at receipt, which the cost-basis walker turns into the reward
      lot's basis (``zero_basis`` → ``qty × price``).  Without it the
      free shares get $0 basis and the whole value is realized as gain
      at the eventual sale.
    - **quantity**: when the legs sum to a whole share within the REC
      row's *own* printed precision, snap to it.  The correction is
      bounded by half of the last printed decimal place, so it can
      never move the figure further from the truth than the broker's
      own rounding already did.

    Returns ``(quantity, price)`` — unchanged quantity and ``0.0`` price
    when there's no same-day companion (e.g. a standalone free-stock
    reward), which leaves the pre-existing zero-basis behaviour intact.
    """
    price = 0.0
    for row in companions:
        px = abs(_num(row.get("Price", "")))
        if px > 0:
            price = px
            break

    places = _decimals(qty_raw)
    tol = 0.5 * 10 ** -places
    if places >= _RECEIVE_SNAP_MIN_DECIMALS and qty > 0:
        bought = [abs(_num(r.get("Quantity", ""))) for r in companions]
        bought = [q for q in bought if q > 0]
        # Try each same-day order on its own first (a credit tops up one
        # order), then their total in case the day's orders were split.
        for base in bought + ([sum(bought)] if len(bought) > 1 else []):
            whole = round(base + qty)
            if whole > 0 and abs(base + qty - whole) <= tol:
                return round(whole - base, 10), price

    return qty, price


def _option_contract_symbol(action: str, description: str, underlying: str) -> str:
    """Derive a per-contract symbol for an option row so each contract is
    tracked independently from the underlying stock.

    Robinhood BTO/STC/OEXCS descriptions are already in the form
    ``"{UNDERLYING} {MM/DD/YYYY} {Call|Put} ${strike}"``; OEXP descriptions
    are prefixed with ``"Option Expiration for "`` — stripping that prefix
    gives a consistent key so BTO and OEXP for the same contract share a
    lot queue.  If the description is missing or non-standard, fall back
    to a generic "{UNDERLYING} OPTION" marker so the row still lands on a
    key distinct from the stock symbol.
    """
    s = (description or "").strip()
    if action == "OEXP":
        prefix = "Option Expiration for "
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    if s and (" Call " in s or " Put " in s):
        return s
    return f"{underlying} OPTION" if underlying else "OPTION"


def parse_robinhood(filepath: Path) -> list[Transaction]:
    # First pass: read everything so we can pair OEXCS with OCC.
    rows: list[tuple[str, dict]] = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = read_csv_rows(
            f, filepath.name, nonblank=('Trans Code',),
            required=('Activity Date', 'Trans Code', 'Instrument', 'Quantity',
                      'Price', 'Amount'),
            is_informational=_is_informational_footer,
        )
        for row in reader:
            try:
                date = _date_mdy(row["Activity Date"])
            except (ValueError, KeyError):
                continue
            action = (row.get("Trans Code") or "").strip()
            if not action:
                continue
            rows.append((date, row))

    # Build cross-row pooling indexes used by the corp-action handlers.
    # See src/reorgs.py for what each one does.
    from ..reorgs import (
        build_mrgc_pool, build_occ_pool, build_mrgs_receive_dates,
        build_held_at_some_point, is_cil_from_recent_merger,
        is_option_description, parse_cil_description,
        parse_liq_description,
    )
    occ_pool           = build_occ_pool(rows)
    mrgc_pool          = build_mrgc_pool(rows)
    # Same-day purchases per (date, instrument) — the FMV / whole-share
    # evidence a REC row needs.  See _resolve_receive.
    buy_pool: dict[tuple[str, str], list[dict]] = {}
    for _d, _r in rows:
        if (_r.get("Trans Code") or "").strip() == "Buy":
            buy_pool.setdefault(
                (_d, (_r.get("Instrument") or "").strip()), []).append(_r)
    mrgs_receive_dates = build_mrgs_receive_dates(rows)
    held_at_some_point = build_held_at_some_point(rows)
    consumed_occ:  set[int] = set()
    consumed_mrgc: set[int] = set()

    txns: list[dict] = []
    for date, row in rows:
        action = (row.get("Trans Code") or "").strip()
        underlying = (row.get("Instrument") or "").strip()
        description = (row.get("Description") or "").strip()

        # Paired OCC: skip; its amount is rolled into the OEXCS row.
        if action == "OCC" and id(row) in consumed_occ:
            continue

        # OEXCS: option exercise (cash-settled or physical).  Pair with
        # the matching OCC on (date, underlying ticker) so the cash
        # proceeds attach to the contract being disposed of.  The OCC
        # pool is keyed on the underlying because OCC rows have a generic
        # "Option Maturity: Cash Component" description and only the
        # Instrument field to go on.
        if action == "OEXCS":
            qty = _parse_option_qty(row.get("Quantity", ""), default=1.0)
            proceeds = 0.0
            pending = occ_pool.get((date, underlying), [])
            for occ_row in pending:
                if id(occ_row) in consumed_occ:
                    continue
                proceeds = abs(_num(occ_row.get("Amount", "")))
                consumed_occ.add(id(occ_row))
                break
            txns.append(_txn(
                date=date,
                account="Robinhood",
                symbol=_option_contract_symbol(action, description, underlying),
                action=action,
                quantity=qty,
                price=0.0,
                fees=0.0,
                amount=proceeds,
                description=description,
                source=filepath.name,
            ))
            continue

        # OEXP: option expired worthless.  Robinhood writes qty blank;
        # default to 1 so the contract actually leaves the lot queue.
        if action == "OEXP":
            qty = _parse_option_qty(row.get("Quantity", ""), default=1.0)
            txns.append(_txn(
                date=date,
                account="Robinhood",
                symbol=_option_contract_symbol(action, description, underlying),
                action=action,
                quantity=qty,
                price=0.0,
                fees=0.0,
                amount=0.0,
                description=description,
                source=filepath.name,
            ))
            continue

        qty_raw = (row.get("Quantity", "") or "").strip()
        qty = (_parse_option_qty(row.get("Quantity", ""), default=0.0)
               if action in {"MRGS", "SOFF", "SPR", "SXCH", "CIL", "LIQ"}
               else _num(row.get("Quantity", "")))
        amount = _num(row.get("Amount", ""))

        # ── Merger / spinoff / CIL handling ────────────────────────────
        # Robinhood emits several action codes for corporate actions that
        # the default parser drops on the floor (quantity "{N}S" is not
        # float-parseable → qty=0 → no balance change → phantom shares
        # linger forever after a ticker merges or delists).  Canonicalise
        # to existing Buy/Sell so the rest of the pipeline handles them.
        #
        # - MRGS with "{N}S" suffix = shares SURRENDERED
        #    • Paired with MRGC same (date, symbol): cash-only merger.
        #      Emit one Sell at MRGC's price (proper proceeds, proper
        #      realized gain against prior basis).  e.g. TWTR cash-out.
        #    • No paired MRGC: stock-for-stock merger surrender side.
        #      Emit Sell at $0 — realizes full basis as loss.  The
        #      matching MRGS-without-S on the receive side (see below)
        #      adds shares at $0 cost, so the net unrealized gain on
        #      the new symbol offsets this loss.  Tax reporting
        #      treatment isn't strictly correct (a tax-free reorg has
        #      0 realized gain, with basis carried to the new shares),
        #      but portfolio balances are accurate.  Add a
        #      manual-adjustment row if you need tax precision.
        # - MRGS without "S" suffix = shares RECEIVED in stock-for-stock
        #   merger.  Emit Buy at $0.  e.g. AMD 1 share received from
        #   XLNX → AMD merger.
        # - MRGC paired with MRGS: already handled above; skip here.
        # - MRGC unpaired: emit as Dividend on USD (rare).
        # - CIL = cash in lieu of fractional shares.  Description like
        #   "CIL on 0.723 @ $100.00 - AMD".  Emit a Sell of that fraction
        #   at the stated price (partial realization on the fractional).
        # - SPR = stock split / spinoff.  Same S-vs-no-S convention as
        #   MRGS.  Treated as Sell-at-$0 + Buy-at-$0 for now (basis
        #   continuity lost); balances are correct.
        # - SXCH = stock exchange. Use the same directional stock-for-stock
        #   representation and existing basis limitation, without assuming a
        #   same-day MRGC cash receipt belongs to the exchange.
        # SXCH uses the same surrender/receipt marker for stock exchanges.
        # Resolve direction here: its generic Merger normalization only adds
        # shares. Reuse the validated quantity rather than reparsing with float,
        # which would silently turn grouped quantities into zero.
        if action in ("MRGS", "SPR", "SXCH"):
            mrg_qty, is_surrender = qty, qty_raw.endswith("S")
            if is_surrender:
                # Look for paired MRGC cash receipt (cash-only merger)
                proceeds = 0.0
                cash_rows = (mrgc_pool.get((date, underlying), [])
                             if action != "SXCH" else [])
                for mrgc_row in cash_rows:
                    if id(mrgc_row) in consumed_mrgc:
                        continue
                    proceeds = abs(_num(mrgc_row.get("Amount", "")))
                    consumed_mrgc.add(id(mrgc_row))
                    break
                price = (proceeds / mrg_qty) if (mrg_qty > 0 and proceeds > 0) else 0.0
                kind = ("Stock exchange surrender" if action == "SXCH" else
                        "Cash merger" if action == "MRGS" and proceeds > 0 else
                        "Merger surrender" if action == "MRGS" else "Split/spinoff out")
                txns.append(_txn(
                    date=date, account="Robinhood", symbol=underlying,
                    action="Sell", quantity=mrg_qty,
                    price=price, fees=0.0, amount=proceeds,
                    description=(description or "") + f" | {kind}",
                    source=filepath.name,
                ))
            else:
                kind = ("Stock exchange receipt" if action == "SXCH" else
                        "Merger receipt" if action == "MRGS" else "Split/spinoff in")
                txns.append(_txn(
                    date=date, account="Robinhood", symbol=underlying,
                    action="Buy", quantity=mrg_qty,
                    price=0.0, fees=0.0, amount=0.0,
                    description=(description or "") + f" | {kind}",
                    source=filepath.name,
                ))
            continue

        if action == "MRGC":
            if id(row) in consumed_mrgc:
                continue   # already rolled into paired MRGS as Sell proceeds
            # Unpaired MRGC — emit as dividend on USD so the cash lands
            # in the income ledger rather than disappearing.  Unusual:
            # MRGC usually comes paired.
            txns.append(_txn(
                date=date, account="Robinhood", symbol="USD",
                action="Dividend", quantity=abs(amount), price=1.0,
                fees=0.0, amount=abs(amount),
                description=(description or "") + " | Unpaired merger cash",
                source=filepath.name,
            ))
            continue

        if action == "LIQ":
            liq_qty, liq_px = parse_liq_description(description or "")
            txns.append(_txn(
                date=date, account="Robinhood", symbol=underlying,
                action="Sell", quantity=liq_qty,
                price=liq_px, fees=0.0, amount=abs(amount),
                description=(description or "") + " | Cash liquidation",
                source=filepath.name,
            ))
            continue

        if action == "CIL":
            cil_qty, cil_px = parse_cil_description(description or "")
            is_merger_fractional = is_cil_from_recent_merger(
                underlying, date, mrgs_receive_dates,
            )
            never_held = underlying not in held_at_some_point
            if is_merger_fractional or never_held:
                # Fractional share that was never issued as a whole share
                # (stock-for-stock merger remainder, or a spin-off on a
                # ticker we never held).  The IRS — and Robinhood's
                # 1099-B — treat the cash as PROCEEDS from selling that
                # fractional share: a (usually tiny) capital gain, NOT
                # dividend income.  Model it as a $0-basis Buy of the
                # fraction immediately followed by a Sell at the cash
                # proceeds — net-zero share balance, realized gain = cash.
                # (Basis continuity through mergers/spinoffs is already
                # lost in fin's model, so the fraction's basis is $0,
                # the same simplification as the MRGS/SPR receive legs.)
                tag = ("Stock-for-stock CIL (fractional, never issued)"
                       if is_merger_fractional
                       else "Spin-off CIL (no held shares)")
                txns.append(_txn(
                    date=date, account="Robinhood", symbol=underlying,
                    action="Buy", quantity=cil_qty, price=0.0,
                    fees=0.0, amount=0.0,
                    description=(description or "") + f" | {tag} (basis leg)",
                    source=filepath.name,
                ))
                txns.append(_txn(
                    date=date, account="Robinhood", symbol=underlying,
                    action="Sell", quantity=cil_qty, price=cil_px,
                    fees=0.0, amount=abs(amount),
                    description=(description or "") + f" | {tag}",
                    source=filepath.name,
                ))
            else:
                txns.append(_txn(
                    date=date, account="Robinhood", symbol=underlying,
                    action="Sell", quantity=cil_qty,
                    price=cil_px, fees=0.0, amount=abs(amount),
                    description=(description or "") + " | Cash in lieu",
                    source=filepath.name,
                ))
            continue
        # ── end merger handling ────────────────────────────────────────

        # REC = shares received with no cash outlay — a promotional
        # credit, NOT a corporate action (it used to normalize to
        # "Merger", which was both wrong in the ledger and wrong in the
        # Tax tab).  It normalizes to "Reward" now; recover the FMV and
        # the rounded-off quantity from the same-day companion buy.
        if action == "REC":
            rec_qty, rec_px = _resolve_receive(
                abs(qty), qty_raw, buy_pool.get((date, underlying), []))
            txns.append(_txn(
                date=date, account="Robinhood", symbol=underlying,
                action=action, quantity=rec_qty, price=rec_px, fees=0.0,
                amount=rec_qty * rec_px,
                description=(description or "") + " | Share receipt (no cash outlay)",
                source=filepath.name,
            ))
            continue

        # Split ACH by direction before abs()
        if action == "ACH":
            action = "ACH Deposit" if amount >= 0 else "ACH Withdrawal"

        # FUTSWP and ROC are directional under a single raw code — cash
        # moves BOTH ways to the event-contracts entity, and Robinhood
        # reverses a mis-paid return-of-capital with a second ROC row
        # ("REVERT: ..." description, negative amount).  Split before
        # abs() or the direction is unrecoverable downstream, which
        # silently inflates the reconstructed cash balance the history
        # bridge relies on (see cash_bridge.py).
        if action == "FUTSWP" and amount < 0:
            action = "FUTSWP OUT"
        if action == "ROC" and amount < 0:
            action = "ROC REVERT"

        # BTO/STC operate on a specific option contract, not the
        # underlying — use the description-derived symbol so their basis
        # and balance stay separate from the stock's.
        # Option-contract treatment: BTO/STC always target a specific
        # contract.  CONV (Apex→RHS account migrations) routed via the
        # option-contract symbol when the description identifies an
        # option, so the migration doesn't inflate the underlying
        # stock's share count.  See reorgs.is_option_description.
        if action in ("BTO", "STC") or (action == "CONV" and is_option_description(description)):
            symbol = _option_contract_symbol(action, description, underlying)
        else:
            symbol = underlying

        txns.append(_txn(
            date=date,
            account="Robinhood",
            symbol=symbol,
            action=action,
            quantity=abs(qty),
            price=abs(_num(row.get("Price", ""))),
            fees=0.0,
            amount=abs(amount),
            description=description,
            source=filepath.name,
        ))
    return txns
