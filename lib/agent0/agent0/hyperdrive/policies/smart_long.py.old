"""Agent policy for arbitrade trading on the fixed rate"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent0.hyperdrive.state import HyperdriveActionType, HyperdriveMarketAction
from elfpy.markets.hyperdrive import  HyperdrivePricingModel, HyperdriveMarketState

from elfpy.types import MarketType, Trade
from fixedpointmath import FixedPoint, FixedPointMath
from elfpy import WEI

from .hyperdrive_policy import HyperdrivePolicy

if TYPE_CHECKING:
    from agent0.hyperdrive.state import HyperdriveWallet
    from ethpy.hyperdrive import HyperdriveInterface
    from numpy.random._generator import Generator as NumpyGenerator


class SmartLong(HyperdrivePolicy):

    @dataclass
    class Config(HyperdrivePolicy.Config):
        """Custom config arguments for this policy

        Attributes
        ----------
        risk_threshold: FixedPoint
           The risk threshold for opening a short
        """
        risk_threshold: FixedPoint = FixedPoint("0.02")
        only_one_long: bool = False

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
        self.pricing_model = HyperdrivePricingModel()

        super().__init__(budget, rng, slippage_tolerance)

# info unused
# 'withdrawalSharesProceeds'
# 'shareAdjustment'
# 'lpSharePrice'
# longExposure


# conf unused
# 'baseToken'
# minimumTransactionAmount
# positionDuration
# timeStretch
# fees
# oracleSize
# updateGap
# contractAddress

    # base_buffer: FixedPoint = FixedPoint(0)
    # bond_buffer: FixedPoint = FixedPoint(0)
    # gov_fees_accrued: FixedPoint = FixedPoint(0)
    # long_base_volume: FixedPoint = FixedPoint(0)
    # short_base_volume: FixedPoint = FixedPoint(0)
    # checkpoints: dict[FixedPoint, Checkpoint] = field(default_factory=dict)
    # total_supply_longs: dict[FixedPoint, FixedPoint] = field(default_factory=dict)
    # total_supply_shorts: dict[FixedPoint, FixedPoint] = field(default_factory=dict)
    # withdraw_capital: FixedPoint = FixedPoint(0)
    # withdraw_interest: FixedPoint = FixedPoint(0)
    # def get_state(self, market: HyperdriveInterface) -> HyperdriveMarketState:
    #     """Get the state of the market"""
    #     info = market.pool_info()
    #     conf = market.pool_config()
    #     return HyperdriveMarketState(
    #         lp_total_supply=info["lpTotalSupply"],
    #         share_reserves=info["shareReserves"],
    #         bond_reserves=info["bondReserves"],
    #         share_price=info["sharePrice"],
    #         longs_outstanding=info["longsOutstanding"], 
    #         long_average_maturity_time=info["longAverageMaturityTime"],
    #         shorts_outstanding=info["shortsOutstanding"],
    #         short_average_maturity_time=info["shortAverageMaturityTime"],
    #         withdraw_shares_ready_to_withdraw=info["withdrawalSharesReadyToWithdraw"],
    #         total_supply_withdraw_shares=info["totalSupplyWithdrawalShares"],
    #         variable_apr=market.variable_rate,
    #         init_share_price=conf["initialSharePrice"],
    #         minimum_share_reserves=conf["minimumShareReserves"],
    #         checkpoint_duration=conf["checkpointDuration"], ## TODO convert to years
    #         checkpoint_duration_days= FixedPoint(0), # TODO
    #         curve_fee_multiple=conf["curveFee"],
    #         flat_fee_multiple=conf["flatFee"],
    #         governance_fee_multiple=conf["governanceFee"],


    #     )
    def action(
        self, market: HyperdriveInterface, wallet: HyperdriveWallet
    ) -> tuple[list[Trade[HyperdriveMarketAction]], bool]:
        """Specify actions.

        Arguments

        Returns
        -------
        tuple[list[MarketAction], bool]
            A tuple where the first element is a list of actions,
            and the second element defines if the agent is done trading
        """
        # Get fixed rate

        action_list = []

        for long_time in wallet.longs:  # loop over longs # pylint: disable=consider-using-dict-items
            # if any long is mature
            # TODO: should we make this less time? they dont close before the bot runs out of money
            # how to intelligently pick the length? using PNL I guess.
            if (market.current_block_time - FixedPoint(long_time)) >= market.position_duration_in_years:
                trade_amount = wallet.longs[long_time].balance  # close the whole thing
                action_list += [
                    Trade(
                        market_type=MarketType.HYPERDRIVE,
                        market_action=HyperdriveMarketAction(
                            action_type=HyperdriveActionType.CLOSE_LONG,
                            trade_amount=trade_amount,
                            slippage_tolerance=self.slippage_tolerance,
                            wallet=wallet,
                            mint_time=long_time,
                        ),
                    )
                ]
        long_balances = [long.balance for long in wallet.longs.values()]
        has_opened_long = bool(any(long_balance > 0 for long_balance in long_balances))
        # only open a long if the fixed rate is higher than variable rate
        can_open_long = not has_opened_long or not self.policy_config.only_one_long
        if not can_open_long:
            return action_list, False
        if market.fixed_rate - market.variable_rate <= self.policy_config.risk_threshold:
            return action_list, False

        total_bonds_to_match_variable_apr = self.pricing_model.calc_bond_reserves(
            target_apr=market.variable_rate,  # fixed rate targets the variable rate
            time_remaining=market.position_duration,
            market_state=market.market_state,
        )
        # get the delta bond amount & convert units
        new_bonds_to_match_variable_apr = (
            market.market_state.bond_reserves - total_bonds_to_match_variable_apr
        ) * market.spot_price
        new_base_to_match_variable_apr = market.pricing_model.calc_shares_out_given_bonds_in(
            share_reserves=market.market_state.share_reserves,
            bond_reserves=market.market_state.bond_reserves,
            lp_total_supply=market.market_state.lp_total_supply,
            d_bonds=new_bonds_to_match_variable_apr,
            time_elapsed=FixedPoint(1),  # opening a short, so no time has elapsed
            share_price=market.market_state.share_price,
            init_share_price=market.market_state.init_share_price,
        )
        # get the maximum amount the agent can long given the market and the agent's wallet
        max_base = market.get_max_long_for_account(wallet.balance.amount)
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
                        mint_time=market.block_time.time,
                    ),
                )
            ]
        return action_list, False



