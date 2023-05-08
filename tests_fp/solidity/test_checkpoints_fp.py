"""Testing Hyperdrive Checkpointing"""

import unittest

import elfpy.agents.agent as agent
import elfpy.markets.hyperdrive.hyperdrive_market as hyperdrive_markets
import elfpy.pricing_models.hyperdrive as hyperdrive_pm
import elfpy.time as time
from elfpy import types
from elfpy.errors import errors
from elfpy.math import FixedPoint

# TODO: refactor solidity tests as a separate PR to consolidate setUps
# pylint:disable=duplicate-code


class TestCheckpoint(unittest.TestCase):
    """Test adding liquidity to hyperdrive"""

    contribution = FixedPoint("500_000_000.0")
    target_apr = FixedPoint("0.05")
    alice: agent.AgentFP
    bob: agent.AgentFP
    celine: agent.AgentFP
    hyperdrive: hyperdrive_markets.MarketFP
    block_time: time.BlockTimeFP

    def setUp(self):
        self.alice = agent.AgentFP(wallet_address=0, budget=self.contribution)
        self.bob = agent.AgentFP(wallet_address=1, budget=self.contribution)
        self.celine = agent.AgentFP(wallet_address=1, budget=self.contribution)
        self.block_time = time.BlockTimeFP()

        pricing_model = hyperdrive_pm.HyperdrivePricingModelFP()
        market_state = hyperdrive_markets.MarketStateFP()

        self.hyperdrive = hyperdrive_markets.MarketFP(
            pricing_model=pricing_model,
            market_state=market_state,
            block_time=self.block_time,
            position_duration=time.StretchedTimeFP(
                days=FixedPoint("365.0"),
                time_stretch=pricing_model.calc_time_stretch(self.target_apr),
                normalizing_constant=FixedPoint("365.0"),
            ),
        )
        _, wallet_deltas = self.hyperdrive.initialize(self.alice.wallet.address, self.contribution, self.target_apr)
        self.alice.wallet.update(wallet_deltas)

    def test_checkpoint_failure_future_checkpoint(self):
        """Test that creating a checkpoint in the future fails"""
        with self.assertRaises(errors.InvalidCheckpointTime):
            self.hyperdrive.checkpoint(self.block_time.time + self.hyperdrive.market_state.checkpoint_duration)

    def test_checkpoint_failure_invalid_checkpoint(self):
        """Test that creating checkpoint not evenly divisible by the checkpoint duration fails"""
        with self.assertRaises(errors.InvalidCheckpointTime):
            self.hyperdrive.checkpoint(
                self.block_time.time + self.hyperdrive.market_state.checkpoint_duration * FixedPoint("1.1")
            )

    def test_checkpoint_preset_checkpoint(self):
        """Test that checkpoints don't change amm values and capture values at time of creation"""
        share_price_before = self.hyperdrive.market_state.share_price
        # open a long and a short
        long_amount = FixedPoint("10_000_000.0")
        short_amount = FixedPoint("50_000.0")
        self.bob.budget = long_amount
        self.bob.wallet.balance = types.QuantityFP(amount=long_amount, unit=types.TokenType.BASE)
        _, long_wallet_deltas = self.hyperdrive.open_long(self.bob.wallet, long_amount)
        self.celine.budget = short_amount
        self.celine.wallet.balance = types.QuantityFP(amount=short_amount, unit=types.TokenType.PT)
        self.hyperdrive.open_short(self.celine.wallet, short_amount)
        # Update the share price. Since the long and short were opened in this checkpoint, the
        # checkpoint should be of the old checkpoint price.
        self.hyperdrive.market_state.share_price = FixedPoint("1.5")
        apr_before = self.hyperdrive.pricing_model.calc_apr_from_reserves(
            self.hyperdrive.market_state, self.hyperdrive.position_duration
        )
        # create a checkpoint
        checkpoint_time = self.hyperdrive.latest_checkpoint_time
        self.hyperdrive.checkpoint(checkpoint_time)
        apr_after = self.hyperdrive.pricing_model.calc_apr_from_reserves(
            self.hyperdrive.market_state, self.hyperdrive.position_duration
        )
        # Ensure that the pool's APR wasn't changed by the checkpoint.
        self.assertAlmostEqual(
            float(apr_before),
            float(apr_after),
            places=8,
        )
        checkpoint = self.hyperdrive.market_state.checkpoints[int(checkpoint_time)]
        # Ensure that the checkpoint contains the share price prior to the share price update.
        self.assertEqual(share_price_before, checkpoint.share_price)
        # Ensure that the long and short balance wasn't effected by the checkpoint (the long and
        # short haven't matured yet).
        self.assertEqual(
            self.hyperdrive.market_state.longs_outstanding, long_wallet_deltas.longs[int(checkpoint_time)].balance
        )
        self.assertEqual(self.hyperdrive.market_state.shorts_outstanding, short_amount)

    def test_checkpoint_latest_checkpoint(self):
        """Test that the latest checkpoint has updated share price"""
        # Advance a checkpoint.
        self.block_time.step()
        # Update the share price. Since the long and short were opened in this checkpoint, the
        # checkpoint should be of the old checkpoint price.
        self.hyperdrive.market_state.share_price = FixedPoint("1.5")
        # Create a checkpoint.
        apr_before = self.hyperdrive.pricing_model.calc_apr_from_reserves(
            self.hyperdrive.market_state, self.hyperdrive.position_duration
        )
        self.hyperdrive.checkpoint(self.hyperdrive.latest_checkpoint_time)
        # Ensure that the pool's APR wasn't changed by the checkpoint.
        apr_after = self.hyperdrive.pricing_model.calc_apr_from_reserves(
            self.hyperdrive.market_state, self.hyperdrive.position_duration
        )
        self.assertEqual(apr_after, apr_before)
        # Ensure that the checkpoint contains the latest share price.
        checkpoint = self.hyperdrive.market_state.checkpoints[int(self.hyperdrive.latest_checkpoint_time)]
        self.assertEqual(checkpoint.share_price, self.hyperdrive.market_state.share_price)

    def test_checkpoint_in_the_past(self):
        """Test that checkpoints created in the past work as expected"""
        # Open a long and a short.
        print(f"{self.hyperdrive.latest_checkpoint_time=}")
        long_amount = FixedPoint("10_000_000.0")
        self.hyperdrive.open_long(self.bob.wallet, long_amount)
        short_amount = FixedPoint("50_000.0")
        self.hyperdrive.open_short(self.celine.wallet, short_amount)
        # Advance a term by the position duration.
        self.block_time.tick(self.hyperdrive.position_duration.days / FixedPoint("365.0"))
        # Create a checkpoint
        self.hyperdrive.checkpoint(self.hyperdrive.latest_checkpoint_time)
        # Create a checkpoint in the past
        previous_checkpoint_time = (
            self.hyperdrive.latest_checkpoint_time * FixedPoint("365.0")
            - self.hyperdrive.market_state.checkpoint_duration_days
        ) / FixedPoint("365.0")
        self.hyperdrive.checkpoint(previous_checkpoint_time)

        # TODO: This should be either removed or uncommented when we decide
        # whether or not the flat+curve invariant should have an impact on
        # the market rate.
        #
        # Ensure that the pool's APR wasn't changed by the checkpoint.
        # assertEqual(self.hyperdrive.pricing_model.calculate_apr_from_reserves(hyperdrive), apr_before)

        # Ensure that the checkpoint contains the share price prior to the
        # share price update.
        last_checkpoint = self.hyperdrive.market_state.checkpoints[int(self.hyperdrive.latest_checkpoint_time)]
        self.assertEqual(last_checkpoint.share_price, self.hyperdrive.market_state.share_price)
        # Ensure that the previous checkpoint contains the closest share price.
        previous_checkpoint = self.hyperdrive.market_state.checkpoints[int(previous_checkpoint_time)]
        self.assertEqual(previous_checkpoint.share_price, self.hyperdrive.market_state.share_price)
        # Ensure that the long and short balance has gone to zero (all of the
        # matured positions have been closed).
        self.assertEqual(int(self.hyperdrive.market_state.longs_outstanding), 0)
        self.assertEqual(int(self.hyperdrive.market_state.shorts_outstanding), 0)