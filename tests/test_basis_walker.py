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


class TestIntraGroupRebase:
    """An intra-group transfer pair whose Transfer In carries a user
    basis_override is a REBASE: consume the carried lots with no gain,
    push one lot at the override basis.  Matches how Coinbase's tax
    engine treats Pro→regular arrivals as receives with
    customer-provided basis."""

    def _rebase_txns(self):
        txns = _txns(
            # Cheap early buy (the low-basis lots fin reconstructs).
            ("2021-01-01", "Coinbase", "ETH-USD", "Buy", 10, 100.0, 1000.0),
            # Internal Pro→regular shuffle (same group, same day, same
            # qty → intra-group pair).  Increases sort before decreases.
            ("2021-09-02", "Coinbase", "ETH-USD", "Transfer In",  10, 0.0, 0.0),
            ("2021-09-02", "Coinbase", "ETH-USD", "Transfer Out", 10, 0.0, 0.0),
            # Later sale of half.
            ("2024-01-15", "Coinbase", "ETH-USD", "Sell", 5, 4000.0, 20000.0),
        )
        # Customer-provided basis for the 10 ETH receive.
        txns[1]["basis_override"] = 38000.0
        return txns

    def test_rebase_replaces_carried_basis(self, isolated_workdir):
        from src.basis import compute_basis_default, state_to_holdings
        txns = self._rebase_txns()
        state = compute_basis_default(txns)
        # Sale realizes against the override basis (38000/10 = 3800/unit),
        # not the cheap carried basis (100/unit).
        assert state["realized_total"] == pytest.approx(20000.0 - 19000.0)
        holdings = state_to_holdings(state, "fifo")
        eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
        assert len(eth) == 1
        assert eth[0]["quantity"] == pytest.approx(5.0)
        assert eth[0]["cost_basis"] == pytest.approx(19000.0)
        # No gain booked at the rebase itself.
        tin, tout = txns[1], txns[2]
        assert tin["basis_effect"] == "rebase_in"
        assert tin["cost_basis"] == pytest.approx(38000.0)
        assert tin.get("realized_gain") in (None, 0)
        assert tout["basis_effect"] == "rebase_out"
        assert tout["cost_basis"] == pytest.approx(1000.0)

    def test_rebase_survives_hifo_consume_order(self, isolated_workdir):
        """Consume-then-push ordering: under HIFO a push-first rebase
        would let the out leg consume the fresh (highest-basis) override
        lot itself.  The Coinbase account IS HIFO in production."""
        from src.basis import compute_basis_default, state_to_holdings
        txns = self._rebase_txns()
        state = compute_basis_default(txns,
                                      account_methods={"Coinbase": "hifo"})
        assert state["realized_total"] == pytest.approx(1000.0)
        holdings = state_to_holdings(state, "hifo")
        eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
        assert eth[0]["cost_basis"] == pytest.approx(19000.0)

    def test_rebase_annotations_reconstruct(self, isolated_workdir):
        """derive_basis_by_key_from_txns must net rebase_in − rebase_out
        so the refresh path and lot-queue parity check stay in sync."""
        from src.basis import compute_basis_default, derive_basis_by_key_from_txns
        txns = self._rebase_txns()
        compute_basis_default(txns)
        by_key = derive_basis_by_key_from_txns(txns)
        # +1000 (buy) +38000 (rebase_in) −1000 (rebase_out) −19000 (sell)
        assert by_key[("Coinbase", "ETH-USD")] == pytest.approx(19000.0)

    def test_intra_pair_without_override_stays_noop(self, isolated_workdir):
        from src.basis import compute_basis_default, state_to_holdings
        txns = self._rebase_txns()
        del txns[1]["basis_override"]
        state = compute_basis_default(txns)
        # Carried basis intact: sale realizes against the cheap lots.
        assert state["realized_total"] == pytest.approx(20000.0 - 500.0)
        assert txns[1]["basis_effect"] == "intra_group_noop"
        holdings = state_to_holdings(state, "fifo")
        eth = [h for h in holdings if h["symbol"] == "ETH-USD"]
        assert eth[0]["cost_basis"] == pytest.approx(500.0)


class TestLotProvenance:
    """Lots carry an inert `origin` field stamped at push time:
    "broker" (basis_override / report-stamped), "fmv" (fin estimated
    FMV), "reconstructed" (normal txn-derived).  Carried lots preserve
    it through transfers and wraps."""

    def _origins(self, state, key):
        return [l.get("origin") for l in state["lots"][key]]

    def test_buy_is_reconstructed_override_is_broker(self, isolated_workdir):
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Robinhood", "AAPL", "Buy", 1, 100.0, 100.0),
            ("2024-02-01", "Robinhood", "AAPL", "Buy", 1, 120.0, 120.0),
        )
        txns[1]["basis_override"] = 90.0
        state = compute_basis_default(txns)
        assert self._origins(state, ("Robinhood", "AAPL")) == [
            "reconstructed", "broker"]

    def test_unpaired_transfer_in_is_fmv(self, isolated_workdir):
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Coinbase", "ETH-USD", "Transfer In",
             1, 2000.0, 0.0),
        )
        state = compute_basis_default(txns)
        assert self._origins(state, ("Coinbase", "ETH-USD")) == ["fmv"]

    def test_zero_basis_priceless_is_fmv_priced_is_reconstructed(
            self, isolated_workdir):
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Coinbase", "ETH-USD", "Reward",
             0.5, 2000.0, 1000.0),
            ("2024-02-01", "Coinbase", "ETH-USD", "Reward", 0.1, 0.0, 0.0),
        )
        state = compute_basis_default(txns)
        assert self._origins(state, ("Coinbase", "ETH-USD")) == [
            "reconstructed", "fmv"]

    def test_origin_survives_cross_group_transfer(self, isolated_workdir):
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Robinhood", "AAPL", "Buy", 1, 100.0, 100.0),
            ("2024-03-01", "Robinhood", "AAPL", "Transfer Out",
             1, 0.0, 0.0),
            ("2024-03-01", "Roth IRA", "AAPL", "Transfer In", 1, 0.0, 0.0),
        )
        txns[0]["basis_override"] = 95.0     # broker-stamped source lot
        state = compute_basis_default(txns)
        assert self._origins(state, ("Roth IRA", "AAPL")) == ["broker"]

    def test_origin_survives_wrap(self, isolated_workdir):
        from src.basis import compute_basis_default
        txns = _txns(
            ("2024-01-01", "Coinbase", "ETH-USD", "Buy", 2, 2000.0, 4000.0),
            ("2024-02-01", "Coinbase", "ETH-USD", "Wrap Asset Out",
             2, 0.0, 0.0),
            ("2024-02-01", "Coinbase", "CBETH-USD", "Wrap Asset In",
             1.9, 0.0, 0.0),
        )
        state = compute_basis_default(txns)
        assert self._origins(state, ("Coinbase", "CBETH-USD")) == [
            "reconstructed"]


class TestWalkOrderIndependence:
    """The walk must depend on the transactions, not on the order the
    caller happened to hand them over in.

    ``_sort_key`` groups by ``(date, account_group, symbol, direction)``
    and several same-day rows in one symbol tie under it.  Python's sort
    is stable, so before ``seq`` existed those ties resolved to the
    incoming list order — and fin's two pipeline paths supply different
    ones (``main()`` walks in parse order, ``--refresh-prices`` in the
    order the JSON export was written in).  Same ledger, different lots
    relieved, different realized gains.  See
    ``pipeline_stages.assign_ingest_seq``, and
    ``tests/test_pipeline_path_parity.py`` for the end-to-end version.
    """

    @staticmethod
    def _seq(txns):
        for i, t in enumerate(txns):
            t["seq"] = i
        return txns

    @staticmethod
    def _annotations(txns):
        return {t["seq"]: (t.get("cost_basis"), t.get("realized_gain"),
                           t.get("basis_effect"))
                for t in txns}

    def _rows(self):
        """Two same-day buys at different prices, then a sell.  Under
        HIFO the sell's basis depends entirely on which buy landed in
        the pool first — exactly the tie that used to leak."""
        return (
            ("2024-01-01", "Coinbase", "BTC-USD", "Buy",  1, 100.0, 100.0),
            ("2024-02-01", "Coinbase", "BTC-USD", "Buy",  1, 300.0, 300.0),
            ("2024-02-01", "Coinbase", "BTC-USD", "Buy",  1, 500.0, 500.0),
            ("2024-03-01", "Coinbase", "BTC-USD", "Sell", 2, 600.0, 1200.0),
        )

    @pytest.mark.parametrize("method", ["fifo", "lifo", "hifo"])
    def test_reordered_input_gives_identical_annotations(self, method,
                                                         isolated_workdir):
        from src.basis import compute_basis_default
        methods = {"Coinbase": method}

        forward = self._seq(_txns(*self._rows()))
        forward_state = compute_basis_default(forward, account_methods=methods)
        expected = self._annotations(forward)

        # Separate txn dicts so the second walk can't just overwrite the
        # first walk's annotations.
        reversed_ = self._seq(_txns(*self._rows()))
        reversed_.reverse()
        reversed_state = compute_basis_default(reversed_,
                                               account_methods=methods)

        assert self._annotations(reversed_) == expected
        assert reversed_state["realized_total"] == pytest.approx(
            forward_state["realized_total"])

    def test_wrap_out_ties_with_a_same_day_buy(self, isolated_workdir):
        """The case that hit production: ``Wrap Asset Out`` SUBTRACTS in
        the balance walker but CARRIES basis in the lot walker, so the
        two sorts disagree about whether it precedes a same-day Buy.
        Whichever order arrives, the wrap must carry the same basis."""
        from src.basis import compute_basis_default
        rows = (
            ("2024-01-01", "Coinbase", "ETH-USD", "Buy", 2, 500.0, 1000.0),
            ("2024-02-05", "Coinbase", "ETH-USD", "Wrap Asset Out",
             2, 0.0, 0.0),
            ("2024-02-05", "Coinbase", "CBETH-USD", "Wrap Asset In",
             2, 0.0, 0.0),
            ("2024-02-05", "Coinbase", "ETH-USD", "Buy", 2, 1500.0, 3000.0),
        )
        methods = {"Coinbase": "hifo"}
        a = self._seq(_txns(*rows))
        b = list(a)
        b[1], b[3] = b[3], b[1]          # buy before the wrap-out

        compute_basis_default(a, account_methods=methods)
        wrap_in_basis = next(t["cost_basis"] for t in a
                             if t["action"] == "Wrap Asset In")
        compute_basis_default(b, account_methods=methods)
        assert next(t["cost_basis"] for t in b
                    if t["action"] == "Wrap Asset In") == pytest.approx(
            wrap_in_basis)


class TestAnnotationReconstructionIsApproximate:
    """`derive_basis_by_key_from_txns` re-sums the per-txn `cost_basis`
    annotations, which are rounded to cents for readability.  On a
    position built from many sub-cent fills that lands a couple of cents
    away from the walker's own lot state.

    This is fine for its one consumer — the data-health parity check,
    which carries a tolerance — and NOT fine for anything that publishes
    a basis figure.  `main._refresh_prices_only` used to publish from
    it, which is how the two pipeline paths came to disagree on holdings
    basis for identical transactions.
    """

    def _fills(self, n=10):
        """`n` fractional-share buys whose per-txn basis rounds UP by
        $0.003 each — the shape a fractional-share or crypto broker
        actually reports."""
        return _txns(*[
            (f"2024-02-{i + 1:02d}", "Broker", "DUST", "Buy", 3.0, 10.0023,
             30.007)
            for i in range(n)
        ])

    def test_reconstruction_drifts_from_the_walker(self, isolated_workdir):
        from src.basis import (compute_basis_default,
                               derive_basis_by_key_from_txns,
                               state_to_holdings)
        txns = self._fills()
        state = compute_basis_default(txns)
        walked = {(r["account_group"], r["symbol"]): r["cost_basis"]
                  for r in state_to_holdings(state, "fifo")}
        rebuilt = derive_basis_by_key_from_txns(txns)
        key = ("Broker", "DUST")

        # The walker sums remaining lots at full precision, rounding
        # once: 10 × 30.007 = 300.07.
        assert walked[key] == pytest.approx(300.07)
        # The reconstruction sums ten values already rounded to 30.01.
        assert round(rebuilt[key], 2) == pytest.approx(300.10)
        assert walked[key] != round(rebuilt[key], 2), (
            "premise gone: without real drift here, the parity test that "
            "depends on this shape stops guarding anything"
        )

    def test_drift_stays_inside_the_data_health_tolerance(self,
                                                          isolated_workdir):
        """The parity check's $0.05 band has to absorb this, or every
        real run would report a spurious high-severity integrity issue."""
        from src.basis import (compute_basis_default,
                               derive_basis_by_key_from_txns,
                               state_to_holdings)
        txns = self._fills()
        state = compute_basis_default(txns)
        walked = {(r["account_group"], r["symbol"]): r["cost_basis"]
                  for r in state_to_holdings(state, "fifo")}
        rebuilt = derive_basis_by_key_from_txns(txns)
        key = ("Broker", "DUST")
        assert abs(walked[key] - rebuilt[key]) <= 0.05
