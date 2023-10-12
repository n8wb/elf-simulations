"""Agent policy for arbitrade trading on the fixed rate"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent0.hyperdrive.state import HyperdriveActionType, HyperdriveMarketAction
from elfpy.types import MarketType, Trade
from fixedpointmath import FixedPoint
from elfpy import WEI

from .hyperdrive_policy import HyperdrivePolicy

if TYPE_CHECKING:
    from agent0.hyperdrive.state import HyperdriveWallet
    from ethpy.hyperdrive import HyperdriveInterface
    from numpy.random._generator import Generator as NumpyGenerator


class SmartShort(HyperdrivePolicy):

    @dataclass
    class Config(HyperdrivePolicy.Config):
        """Custom config arguments for this policy

        Attributes
        ----------
        risk_threshold: FixedPoint
           The risk threshold for opening a short
        """
        risk_threshold: FixedPoint = FixedPoint("0.02")
        only_one_short: bool = False

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
        self.policy_config = policy_config

        super().__init__(budget, rng, slippage_tolerance)

    def action(
        self, market: HyperdriveInterface, wallet: HyperdriveWallet
    ) -> tuple[list[Trade[HyperdriveMarketAction]], bool]:
        """Specify actions.

        Arguments
        ---------
        market : HyperdriveMarketState
            the trading market
        wallet : HyperdriveWallet
            agent's wallet

        Returns
        -------
        tuple[list[MarketAction], bool]
            A tuple where the first element is a list of actions,
            and the second element defines if the agent is done trading
        """
        # Get fixed rate

        action_list = []

        for short_time in wallet.shorts:  # loop over shorts # pylint: disable=consider-using-dict-items
            # if any short is mature
            if (market.current_block_time - FixedPoint(short_time)) >= market.position_duration_in_years:
                trade_amount = wallet.shorts[short_time].balance  # close the whole thing
                action_list += [
                    Trade(
                        market_type=MarketType.HYPERDRIVE,
                        market_action=HyperdriveMarketAction(
                            action_type=HyperdriveActionType.CLOSE_SHORT,
                            trade_amount=trade_amount,
                            slippage_tolerance=self.slippage_tolerance,
                            wallet=wallet,
                            mint_time=short_time,
                        ),
                    )
                ]
            # TODO if any short is underwater, close it

        short_balances = [short.balance for short in wallet.shorts.values()]
        has_opened_short = bool(any(short_balance > FixedPoint(0) for short_balance in short_balances))
        # only open a short if the fixed rate is 0.02 or more lower than variable rate
        can_open_short = not self.policy_config.only_one_short or not has_opened_short
        print(f"fixed rate: {market.fixed_rate}, variable rate: {market.variable_rate}")
        if can_open_short and market.fixed_rate - market.variable_rate < self.policy_config.risk_threshold:
            # maximum amount the agent can short given the market and the agent's wallet
            trade_amount = market.get_max_short(wallet.balance.amount)
            if trade_amount > WEI and wallet.balance.amount > WEI:
                action_list += [
                    Trade(
                        market_type=MarketType.HYPERDRIVE,
                        market_action=HyperdriveMarketAction(
                            action_type=HyperdriveActionType.OPEN_SHORT,
                            trade_amount=trade_amount,
                            slippage_tolerance=self.slippage_tolerance,
                            wallet=wallet,
                            #mint_time=market.current_block_time,
                        ),
                    )
                ]
        return action_list, False
