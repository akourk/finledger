"""Cost basis walker tests — verifies FIFO/LIFO/HIFO/Average produce
the right per-method totals for representative txn streams."""

from __future__ import annotations

import pytest


def _txns(*rows):
    """Build a list of post-normalization txns for the basis walker.

    Each row is (date, account_group, symbol, action, qty, price, amount).
    """
    out = []
    for d, ag, sym, act, qty, px, amt in rows:
        out.append({
            "date": d, "account_group": ag, "account_type": "Taxable",
            "account": ag, "symbol": sym, "action": act,
            "quantity": qty, "price": px, "fees": 0.0, "amount": amt,
            "description": "", "source": "test",
        })
    return out


class TestPerAccountLotMethod:
    def test_account_methods_override_realized_gain(self, isolated_workdir):
        """A per-account HIFO override (e.g. Coinbase) consumes the
        highest-cost lot first, changing realized gain — but not balances.
        Buy 1 @ $100, Buy 1 @ $300, Sell 1 @ $400.
          FIFO: realize 400 - 100 = $300
          HIFO: realize 400 - 300 = $100
        """
        from src.basis import compute_basis_default
        rows = (
            ("2024-01-01", "Coinbase", "BTC-USD", "Buy",  1, 100.0, 100.0),
            ("2024-02-01", "Coinbase", "BTC-USD", "Buy",  1, 300.0, 300.0),
            ("2024-03-01", "Coinbase", "BTC-USD", "Sell", 1, 400.0, 400.0),
        )
        fifo = compute_basis_default(_txns(*rows))
        assert fifo["realized_total"] == pytest.approx(300.0)

        hifo = compute_basis_default(_txns(*rows),
                                     account_methods={"Coinbase": "hifo"})
        assert hifo["realized_total"] == pytest.approx(100.0)

    def test_override_is_scoped_to_named_account(self, isolated_workdir):
        """The override only affects the named account; others stay FIFO."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Coinbase",  "BTC-USD", "Buy",  1, 100.0, 100.0),
            ("2024-02-01", "Coinbase",  "BTC-USD", "Buy",  1, 300.0, 300.0),
            ("2024-03-01", "Coinbase",  "BTC-USD", "Sell", 1, 400.0, 400.0),
            ("2024-01-01", "Robinhood", "FOO", "Buy",  1, 100.0, 100.0),
            ("2024-02-01", "Robinhood", "FOO", "Buy",  1, 300.0, 300.0),
            ("2024-03-01", "Robinhood", "FOO", "Sell", 1, 400.0, 400.0),
        )
        res = compute_basis_default(txns, account_methods={"Coinbase": "hifo"})
        # Coinbase HIFO (100) + Robinhood FIFO (300) = 400
        assert res["realized_total"] == pytest.approx(400.0)
        # avg override is unsupported for the annotated walk → falls back
        # to FIFO (no crash, no structural confusion).
        res_avg = compute_basis_default(txns, account_methods={"Coinbase": "avg"})
        assert res_avg["realized_total"] == pytest.approx(600.0)  # both FIFO


class TestFeeNoRealizedGain:
    def test_fee_removes_shares_without_realizing_gain(self, isolated_workdir):
        """A fee paid by redeeming shares (e.g. a fund maintenance fee) is
        an expense, not a trade: the shares + basis leave the lot queue
        (parity), but no realized capital gain is booked."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Roth IRA", "FOO", "Buy", 10, 10.0, 100.0),
            # Redeem 0.1 sh (basis $1.00) to pay a $2.50 fee — would
            # otherwise book +$1.50 realized.
            ("2024-06-01", "Roth IRA", "FOO", "Fee", 0.1, 25.0, 2.50),
        )
        res = compute_basis_default(txns)
        assert res["realized_total"] == pytest.approx(0.0)
        fee = next(t for t in txns if t["action"] == "Fee")
        assert not fee.get("realized_gain")
        # Parity: the 0.1 share + its basis still left the lot queue.
        lots = res["lots"][("Roth IRA", "FOO")]
        assert sum(l["qty"] for l in lots) == pytest.approx(9.9)


class TestWrapBasisCarry:
    def test_wrap_defers_gain_and_carries_basis(self, isolated_workdir):
        """Wrapping (ETH→CBETH) is basis-CARRYING, not a taxable disposal:
        no gain at the wrap, the ETH basis flows to CBETH (rescaled to the
        new quantity), and gain is realized only at the eventual real sale.
        Matches how brokers (Coinbase 1099-DA) report wrapping."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2022-01-01", "Coinbase", "ETH-USD",   "Buy",            10, 100.0, 1000.0),
            # Wrap 10 ETH → 9 CBETH same day (qty changes; basis carries).
            ("2022-06-01", "Coinbase", "ETH-USD",   "Wrap Asset Out", 10, 150.0, 1500.0),
            ("2022-06-01", "Coinbase", "CBETH-USD", "Wrap Asset In",   9, 166.7, 1500.0),
            # Sell the 9 CBETH for $1800.
            ("2022-12-01", "Coinbase", "CBETH-USD", "Sell",            9, 200.0, 1800.0),
        )
        res = compute_basis_default(txns)
        # Gain realized only at the sale: 1800 proceeds − 1000 carried basis.
        assert res["realized_total"] == pytest.approx(800.0)
        # The wrap legs must NOT realize any gain.
        wrap_out = next(t for t in txns if t["action"] == "Wrap Asset Out")
        assert not wrap_out.get("realized_gain")
        sell = next(t for t in txns if t["action"] == "Sell")
        assert sell["realized_gain"] == pytest.approx(800.0)

    def test_unwrap_then_sell_carries_basis(self, isolated_workdir):
        """Round trip: buy ETH, wrap to CBETH, unwrap back to ETH, sell ETH.
        Basis survives both conversions; gain only at the final sale."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2022-01-01", "Coinbase", "ETH-USD",   "Buy",            10, 100.0, 1000.0),
            ("2022-03-01", "Coinbase", "ETH-USD",   "Wrap Asset Out", 10, 150.0, 1500.0),
            ("2022-03-01", "Coinbase", "CBETH-USD", "Wrap Asset In",   9, 166.7, 1500.0),
            ("2022-09-01", "Coinbase", "CBETH-USD", "Unwrap Out",      9, 200.0, 1800.0),
            ("2022-09-01", "Coinbase", "ETH-USD",   "Unwrap In",      10, 180.0, 1800.0),
            ("2022-12-01", "Coinbase", "ETH-USD",   "Sell",           10, 200.0, 2000.0),
        )
        res = compute_basis_default(txns)
        # 2000 proceeds − 1000 original basis = 1000, only at the sale.
        assert res["realized_total"] == pytest.approx(1000.0)


class TestFIFOBasis:
    def test_simple_buy_sell(self, isolated_workdir):
        """One Buy, one full Sell — realized = proceeds - basis.

        compute_basis_default annotates each Sell txn in-place with
        `realized_gain`, and returns walker state with `realized_total`.
        """
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Robinhood", "FOO", "Buy",  10, 10.0, 100.0),
            ("2024-06-01", "Robinhood", "FOO", "Sell", 10, 15.0, 150.0),
        )
        result = compute_basis_default(txns)
        assert result["realized_total"] == pytest.approx(50.0)

    def test_partial_sell_fifo_order(self, isolated_workdir):
        """FIFO: sells consume oldest lots first.  Buy 10 @ $10, then
        10 @ $20, then sell 12 @ $25.  FIFO realized = (10×$25 - 10×$10)
        + (2×$25 - 2×$20) = $150 + $10 = $160."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Robinhood", "FOO", "Buy",  10, 10.0, 100.0),
            ("2024-02-01", "Robinhood", "FOO", "Buy",  10, 20.0, 200.0),
            ("2024-06-01", "Robinhood", "FOO", "Sell", 12, 25.0, 300.0),
        )
        result = compute_basis_default(txns)
        assert result["realized_total"] == pytest.approx(160.0)


class TestAllMethods:
    def test_lifo_vs_fifo_diverges(self, isolated_workdir):
        """Same txns, LIFO vs FIFO realized gain differs because the
        SELL consumes the most-recent lots first under LIFO."""
        from src.basis import compute_basis_all_methods
        txns = _txns(
            ("2024-01-01", "Robinhood", "FOO", "Buy",  10, 10.0, 100.0),
            ("2024-02-01", "Robinhood", "FOO", "Buy",  10, 20.0, 200.0),
            ("2024-06-01", "Robinhood", "FOO", "Sell", 12, 25.0, 300.0),
        )
        results = compute_basis_all_methods(txns)
        # FIFO realized: (10*25 - 10*10) + (2*25 - 2*20) = 150 + 10 = 160
        # LIFO realized: (10*25 - 10*20) + (2*25 - 2*10) = 50 + 30 = 80
        assert results["fifo"]["realized_total"] == pytest.approx(160.0)
        assert results["lifo"]["realized_total"] == pytest.approx(80.0)

    def test_hifo_minimizes_gain(self, isolated_workdir):
        """HIFO should never produce a higher realized gain than FIFO
        or LIFO for the same Sell — it's the tax-optimal lot selection."""
        from src.basis import compute_basis_all_methods
        txns = _txns(
            ("2024-01-01", "Robinhood", "FOO", "Buy",  5, 10.0, 50.0),
            ("2024-02-01", "Robinhood", "FOO", "Buy",  5, 30.0, 150.0),
            ("2024-03-01", "Robinhood", "FOO", "Buy",  5, 20.0, 100.0),
            ("2024-06-01", "Robinhood", "FOO", "Sell", 5, 25.0, 125.0),
        )
        results = compute_basis_all_methods(txns)
        fifo_rg = results["fifo"]["realized_total"]
        lifo_rg = results["lifo"]["realized_total"]
        hifo_rg = results["hifo"]["realized_total"]
        assert hifo_rg <= fifo_rg
        assert hifo_rg <= lifo_rg
        # HIFO should pick the $30 lot → 5*25 - 5*30 = -25 realized loss
        assert hifo_rg == pytest.approx(-25.0)


class TestUSDIgnored:
    def test_usd_txns_skipped_in_lot_queue(self, isolated_workdir):
        """USD events (deposits, dividends paying cash) shouldn't go
        into the FIFO lot queue — they're cash, not lots.  The walker
        ignores them and only tracks share lots."""
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Robinhood", "USD", "Deposit", 1000, 1.0, 1000.0),
            ("2024-01-15", "Robinhood", "FOO", "Buy",     10,   50.0, 500.0),
            ("2024-06-01", "Robinhood", "USD", "Dividend",  5,   1.0,    5.0),
            ("2024-06-15", "Robinhood", "FOO", "Sell",    10,   60.0, 600.0),
        )
        result = compute_basis_default(txns)
        # Realized = 600 - 500 = 100 (USD events ignored)
        assert result["realized_total"] == pytest.approx(100.0)
