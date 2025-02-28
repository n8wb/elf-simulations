"""Agent policy for leveraged long positions"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent0.hyperdrive.state import HyperdriveActionType, HyperdriveMarketAction
from elfpy import WEI
from elfpy.types import MarketType, Trade
from fixedpointmath import FixedPoint, FixedPointMath

from .hyperdrive_policy import HyperdrivePolicy

if TYPE_CHECKING:
    from agent0.hyperdrive.state import HyperdriveWallet
    from ethpy.hyperdrive import HyperdriveInterface
    from numpy.random._generator import Generator as NumpyGenerator
# pylint: disable=too-few-public-methods


class SmartLong2(HyperdrivePolicy):
    """Agent that opens longs to push the fixed-rate towards the variable-rate."""

    @classmethod
    def description(cls) -> str:
        """Describe the policy in a user friendly manner that allows newcomers to decide whether to use it.

        Returns
        -------
        str
            A description of the policy.
        """

        raw_description = """
        My strategy:
            - I'm not willing to open a long if it will cause the fixed-rate apr to go below the variable rate
                - I simulate the outcome of my trade, and only execute on this condition
            - I only close if the position has matured
            - I only open one long at a time
            - I do not take into account fees when targeting the fixed rate
        """
        return super().describe(raw_description)

    @dataclass
    class Config(HyperdrivePolicy.Config):
        """Custom config arguments for this policy

        Attributes
        ----------
        trade_chance: FixedPoint
            The percent chance to open a trade.
        risk_threshold: FixedPoint
            The upper threshold of the fixed rate minus the variable rate to open a long.
        """

        trade_chance: FixedPoint = FixedPoint("0.5")
        risk_threshold: FixedPoint = FixedPoint("0.0001")
        always_trade: bool = True
        only_one_long: bool = False

    # pylint: disable=too-many-arguments

    def __init__(
        self,
        budget: FixedPoint,
        rng: NumpyGenerator | None = None,
        slippage_tolerance: FixedPoint | None = None,
        policy_config: Config | None = None,
    ):
        """Initializes the bot

        Arguments
        ---------
        budget: FixedPoint
            The budget of this policy
        rng: NumpyGenerator | None
            Random number generator
        slippage_tolerance: FixedPoint | None
            Slippage tolerance of trades
        policy_config: Config | None
            The custom arguments for this policy
        """

        # Defaults
        if policy_config is None:
            policy_config = self.Config()
        self.trade_chance = policy_config.trade_chance
        self.risk_threshold = policy_config.risk_threshold
        self.always_trade = policy_config.always_trade
        self.only_one_long = policy_config.only_one_long

        super().__init__(budget, rng, slippage_tolerance)

    def action(
        self, interface: HyperdriveInterface, wallet: HyperdriveWallet
    ) -> tuple[list[Trade[HyperdriveMarketAction]], bool]:
        """Implement a Long Louie user strategy

        Arguments
        ---------
        interface : HyperdriveInterface
            The trading market.
        wallet : HyperdriveWallet
            The agent's wallet.

        Returns
        -------
        action_list : list[MarketAction]
        """
        # Any trading at all is based on a weighted coin flip -- they have a trade_chance% chance of executing a trade
        gonna_trade = self.rng.choice([True, False], p=[float(self.trade_chance), 1 - float(self.trade_chance)])
        if not gonna_trade and not self.always_trade:
            return ([], False)
        action_list = []
        for long_time in wallet.longs:  # loop over longs # pylint: disable=consider-using-dict-items
            # if any long is mature
            # TODO: should we make this less time? they dont close before the agent runs out of money
            # how to intelligently pick the length? using PNL I guess.
            if (interface.current_block_time - FixedPoint(long_time)) >= interface.pool_config["positionDuration"]:
                trade_amount = wallet.longs[long_time].balance  # close the whole thing
                action_list += [
                    Trade(
                        market_type=MarketType.HYPERDRIVE,
                        market_action=HyperdriveMarketAction(
                            action_type=HyperdriveActionType.CLOSE_LONG,
                            trade_amount=trade_amount,
                            slippage_tolerance=self.slippage_tolerance,
                            wallet=wallet,
                            maturity_time=long_time,
                        ),
                    )
                ]
        long_balances = [long.balance for long in wallet.longs.values()]
        has_opened_long = bool(any(long_balance > 0 for long_balance in long_balances))

        can_open_long = not has_opened_long or not self.only_one_long
        # only open a long if the fixed rate is higher than variable rate
        if (interface.fixed_rate - interface.variable_rate) > self.risk_threshold and can_open_long:
            # calculate the total number of bonds we want to see in the pool
            total_bonds_to_match_variable_apr = interface.bonds_given_shares_and_rate(
                target_rate=interface.variable_rate
            )
            # get the delta bond amount & convert units
            bond_reserves: FixedPoint = interface.pool_info["bondReserves"]
            # calculate how many bonds we take out of the pool
            new_bonds_to_match_variable_apr = (bond_reserves - total_bonds_to_match_variable_apr) * interface.spot_price
            # calculate how much base we pay for the new bonds
            new_base_to_match_variable_apr = interface.get_out_for_in(new_bonds_to_match_variable_apr, shares_in=False)
            # get the maximum amount the agent can long given the market and the agent's wallet
            max_base = interface.get_max_long(wallet.balance.amount)
            # don't want to trade more than the agent has or more than the market can handle
            trade_amount = FixedPointMath.minimum(max_base, new_base_to_match_variable_apr)
            if trade_amount > WEI and wallet.balance.amount > WEI:
                action_list += [
                    Trade(
                        market_type=MarketType.HYPERDRIVE,
                        market_action=HyperdriveMarketAction(
                            action_type=HyperdriveActionType.OPEN_LONG,
                            trade_amount=trade_amount,
                            slippage_tolerance=self.slippage_tolerance,
                            wallet=wallet,
                        ),
                    )
                ]
        return action_list, False
