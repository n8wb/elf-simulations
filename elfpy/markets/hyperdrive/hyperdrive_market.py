"""Market simulators store state information when interfacing AMM pricing models with users."""
from __future__ import annotations  # types will be strings by default in 3.11

import logging
import copy
from dataclasses import dataclass, field
from typing import Union

import numpy as np

import elfpy.utils.price as price_utils
import elfpy.time as time
import elfpy.agents.wallet as wallet
import elfpy.types as types
import elfpy.markets.base as base_market
import elfpy.markets.hyperdrive.hyperdrive_actions as hyperdrive_actions
import elfpy.pricing_models.hyperdrive as hyperdrive_pm
import elfpy.pricing_models.yieldspace as yieldspace_pm

# TODO: for now...
# pylint: disable=duplicate-code


@types.freezable(frozen=False, no_new_attribs=False)
@dataclass
class MarketState(base_market.BaseMarketState):
    r"""The state of an AMM

    Attributes
    ----------
    lp_total_supply: float
        Amount of lp tokens
    share_reserves: float
        Quantity of shares stored in the market
    bond_reserves: float
        Quantity of bonds stored in the market
    base_buffer: float
        Base amount set aside to account for open longs
    bond_buffer: float
        Bond amount set aside to account for open shorts
    variable_apr: float
        apr of underlying yield-bearing source
    share_price: float
        ratio of value of base & shares that are stored in the underlying vault,
        i.e. share_price = base_value / share_value
    init_share_price: float
        share price at pool initialization
    trade_fee_percent : float
        The percentage of the difference between the amount paid without
        slippage and the amount received that will be added to the input
        as a fee.
    redemption_fee_percent : float
        A flat fee applied to the output.  Not used in this equation for Yieldspace.
    """

    # dataclasses can have many attributes
    # pylint: disable=too-many-instance-attributes

    # lp reserves
    lp_total_supply: float = field(default=0.0)

    # trading reserves
    share_reserves: float = field(default=0.0)
    bond_reserves: float = field(default=0.0)

    # trading buffers
    base_buffer: float = field(default=0.0)
    bond_buffer: float = field(default=0.0)

    # share price
    variable_apr: float = field(default=0.0)
    share_price: float = field(default=1.0)
    init_share_price: float = field(default=1.0)

    # fee percents
    trade_fee_percent: float = field(default=0.0)
    redemption_fee_percent: float = field(default=0.0)

    # The amount of longs that are still open.
    longs_outstanding: float = field(default=0.0)
    # the amount of shorts that are still open.
    shorts_outstanding: float = field(default=0.0)
    # the average maturity time of long positions.
    long_average_maturity_time: float = field(default=0.0)
    # the average maturity time of short positions.
    short_average_maturity_time: float = field(default=0.0)
    # the amount of base paid by outstanding longs.
    long_base_volume: float = field(default=0.0)
    # the amount of base paid to outstanding shorts.
    short_base_volume: float = field(default=0.0)

    # the amount of long withdrawal shares that haven't been paid out.
    long_withdrawal_shares_outstanding: float = field(default=0.0)
    # the amount of short withdrawal shares that haven't been paid out.
    short_withdrawal_shares_outstanding: float = field(default=0.0)
    # the proceeds that have accrued to the long withdrawal shares.
    long_withdrawal_share_proceeds: float = field(default=0.0)
    # the proceeds that have accrued to the short withdrawal shares.
    short_withdrawal_share_proceeds: float = field(default=0.0)

    def apply_delta(self, delta: hyperdrive_actions.MarketDeltas) -> None:
        r"""Applies a delta to the market state."""
        self.share_reserves += delta.d_base_asset / self.share_price
        self.bond_reserves += delta.d_bond_asset
        self.base_buffer += delta.d_base_buffer
        self.bond_buffer += delta.d_bond_buffer
        self.lp_total_supply += delta.d_lp_total_supply
        self.share_price += delta.d_share_price

        self.longs_outstanding += delta.longs_outstanding
        self.shorts_outstanding += delta.shorts_outstanding
        self.long_average_maturity_time += delta.long_average_maturity_time
        self.short_average_maturity_time += delta.short_average_maturity_time
        self.long_base_volume += delta.long_base_volume
        self.short_base_volume += delta.short_base_volume

        self.long_withdrawal_shares_outstanding += delta.long_withdrawal_shares_outstanding
        self.short_withdrawal_shares_outstanding += delta.short_withdrawal_shares_outstanding
        self.long_withdrawal_share_proceeds += delta.long_withdrawal_share_proceeds
        self.short_withdrawal_share_proceeds += delta.short_withdrawal_share_proceeds

    def copy(self) -> MarketState:
        """Returns a new copy of self"""
        return MarketState(**copy.deepcopy(self.__dict__))


class Market(
    base_market.Market[
        MarketState,
        hyperdrive_actions.MarketDeltas,
        Union[hyperdrive_pm.HyperdrivePricingModel, yieldspace_pm.YieldspacePricingModel],
    ]
):
    r"""Market state simulator

    Holds state variables for market simulation and executes trades.
    The Market class executes trades by updating market variables according to the given pricing model.
    It also has some helper variables for assessing pricing model values given market conditions.
    """

    def __init__(
        self,
        pricing_model: hyperdrive_pm.HyperdrivePricingModel | yieldspace_pm.YieldspacePricingModel,
        market_state: MarketState,
        position_duration: time.StretchedTime,
        block_time: time.BlockTime,
    ):
        # market state variables
        assert (
            position_duration.days == position_duration.normalizing_constant
        ), "position_duration argument term length (days) should normalize to 1"
        self.position_duration = time.StretchedTime(
            position_duration.days, position_duration.time_stretch, position_duration.normalizing_constant
        )
        # NOTE: lint error false positives: This message may report object members that are created dynamically,
        # but exist at the time they are accessed.
        self.position_duration.freeze()  # pylint: disable=no-member # type: ignore
        super().__init__(pricing_model=pricing_model, market_state=market_state, block_time=block_time)

    @property
    def annualized_position_duration(self) -> float:
        r"""Returns the position duration in years"""
        return self.position_duration.days / 365

    @property
    def fixed_apr(self) -> float:
        """Returns the current market apr"""
        # calc_apr_from_spot_price will throw an error if share_reserves <= zero
        # TODO: Negative values should never happen, but do because of rounding errors.
        #       Write checks to remedy this in the market.
        # issue #146

        if self.market_state.share_reserves <= 0:  # market is empty; negative value likely due to rounding error
            return np.nan
        return price_utils.calc_apr_from_spot_price(price=self.spot_price, time_remaining=self.position_duration)

    @property
    def spot_price(self) -> float:
        """Returns the current market price of the share reserves"""
        # calc_spot_price_from_reserves will throw an error if share_reserves is zero
        if self.market_state.share_reserves == 0:  # market is empty
            return np.nan
        return self.pricing_model.calc_spot_price_from_reserves(
            market_state=self.market_state,
            time_remaining=self.position_duration,
        )

    def check_action(self, agent_action: hyperdrive_actions.MarketAction) -> None:
        r"""Ensure that the agent action is an allowed action for this market

        Parameters
        ----------
        action_type : MarketActionType
            See MarketActionType for all acceptable actions that can be performed on this market

        Returns
        -------
        None
        """
        if (
            agent_action.action_type
            in [
                hyperdrive_actions.MarketActionType.CLOSE_LONG,
                hyperdrive_actions.MarketActionType.CLOSE_SHORT,
            ]
            and agent_action.mint_time is None
        ):
            raise ValueError("ERROR: agent_action.mint_time must be provided when closing a short or long")

    def perform_action(
        self, action_details: tuple[int, hyperdrive_actions.MarketAction]
    ) -> tuple[int, wallet.Wallet, hyperdrive_actions.MarketDeltas]:
        r"""Execute a trade in the simulated market

        Checks which of 6 action types are being executed, and handles each case:

        Open & close a long
            When a trader opens a long, they put up base and are given long tokens.
            As time passes, an amount of the longs proportional to the time that has
            passed are considered to be “mature” and can be redeemed one-to-one.
            The remaining amount of longs are sold on the internal AMM. The trader
            does not receive any variable interest from their long positions,
            so the only money they make on closing is from the long maturing and the
            fixed rate changing.

        Open & close a short
            When a trader opens a short, they put up base (typically much smaller
            than the amount that longs have to pay) and are given short tokens. As
            time passes, an amount of the shorts proportional to the time that has
            passed are considered to be “mature” and must be paid for one-to-one by
            the short. The remaining amount of shorts are sold on the internal AMM
            and the short has to pay the price. The short receives any variable
            interest collected on the full amount of base that underlies the short
            (equal to the face value of the short) as well as the spread between the
            money the AMM committed at the beginning of the trade and the money the
            short has to pay when closing the trade.

        Add liquidity
            When a trader adds liquidity, they put up base tokens and receive LP shares.
            If there are not any open positions, the LP receives the same amount of LP shares
            that they would if they were supplying liquidity to uniswap.
            To make sure that new LPs do not get rugged (or rug previous LPs),
            we modify the share reserves used in the LP share calculation by subtracting
            the present value of open longs and adding the present value of the open shorts.

        Remove liquidity
            When a trader redeems their Liquidity Provider (LP) shares, they will receive a combination of base
            tokens and “withdrawal shares”. Since some of the LPs capital may be backing
            open positions, we give them withdrawal shares which pay out when the positions
            are closed. These withdrawal shares receive a steady stream of proceeds from long
            and short positions when they are closed or mature.
        """
        agent_id, agent_action = action_details
        # TODO: add use of the Quantity type to enforce units while making it clear what units are being used
        # issue 216
        self.check_action(agent_action)
        # for each position, specify how to forumulate trade and then execute
        market_deltas = hyperdrive_actions.MarketDeltas()
        agent_deltas = wallet.Wallet(address=0)
        # TODO: Related to #57. When we handle failed transactions, remove this try-catch.  We
        # should handle these in the simulator, not in the market.  The market should throw errors.
        try:
            if agent_action.action_type == hyperdrive_actions.MarketActionType.OPEN_LONG:  # buy to open long
                market_deltas, agent_deltas = self.open_long(
                    agent_wallet=agent_action.wallet,
                    base_amount=agent_action.trade_amount,  # in base: that's the thing in your wallet you want to sell
                )
            elif agent_action.action_type == hyperdrive_actions.MarketActionType.CLOSE_LONG:  # sell to close long
                # TODO: python 3.10 includes TypeGuard which properly avoids issues when using Optional type
                mint_time = float(agent_action.mint_time or 0)
                market_deltas, agent_deltas = self.close_long(
                    agent_wallet=agent_action.wallet,
                    bond_amount=agent_action.trade_amount,  # in bonds: that's the thing in your wallet you want to sell
                    mint_time=mint_time,
                )
            elif agent_action.action_type == hyperdrive_actions.MarketActionType.OPEN_SHORT:  # sell PT to open short
                market_deltas, agent_deltas = self.open_short(
                    agent_wallet=agent_action.wallet,
                    bond_amount=agent_action.trade_amount,  # in bonds: that's the thing you want to short
                )
            elif agent_action.action_type == hyperdrive_actions.MarketActionType.CLOSE_SHORT:  # buy PT to close short
                # TODO: python 3.10 includes TypeGuard which properly avoids issues when using Optional type
                mint_time = float(agent_action.mint_time or 0)
                open_share_price = agent_action.wallet.shorts[mint_time].open_share_price
                market_deltas, agent_deltas = self.close_short(
                    agent_wallet=agent_action.wallet,
                    bond_amount=agent_action.trade_amount,  # in bonds: that's the thing you owe, and need to buy back
                    mint_time=mint_time,
                    open_share_price=open_share_price,
                )
            elif agent_action.action_type == hyperdrive_actions.MarketActionType.ADD_LIQUIDITY:
                market_deltas, agent_deltas = self.add_liquidity(
                    agent_wallet=agent_action.wallet,
                    trade_amount=agent_action.trade_amount,
                )
            elif agent_action.action_type == hyperdrive_actions.MarketActionType.REMOVE_LIQUIDITY:
                market_deltas, agent_deltas = self.remove_liquidity(
                    agent_wallet=agent_action.wallet,
                    bond_amount=agent_action.trade_amount,
                )
            else:
                raise ValueError(f'ERROR: Unknown trade type "{agent_action.action_type}".')
        except AssertionError:
            logging.debug(
                "TRADE FAILED %s\npre_trade_market = %s",
                agent_action,
                self.market_state,
            )

        logging.debug(
            "%s\n%s\nagent_deltas = %s\npre_trade_market = %s",
            agent_action,
            market_deltas,
            agent_deltas,
            self.market_state,
        )
        return (agent_id, agent_deltas, market_deltas)

    def initialize(
        self,
        wallet_address: int,
        contribution: float,
        target_apr: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Market Deltas so that an LP can initialize the market"""
        if self.market_state.share_reserves > 0 or self.market_state.bond_reserves > 0:
            raise AssertionError("The market appears to already be initialized.")
        share_reserves = contribution / self.market_state.share_price
        bond_reserves = self.pricing_model.calc_bond_reserves(
            target_apr=target_apr,
            time_remaining=self.position_duration,
            market_state=MarketState(
                share_reserves=share_reserves,
                init_share_price=self.market_state.init_share_price,
                share_price=self.market_state.share_price,
            ),
        )
        market_deltas = hyperdrive_actions.MarketDeltas(
            d_base_asset=contribution,
            d_bond_asset=bond_reserves,
            d_lp_total_supply=self.market_state.share_price * share_reserves + bond_reserves,
        )
        agent_deltas = wallet.Wallet(
            address=wallet_address,
            balance=-types.Quantity(amount=contribution, unit=types.TokenType.BASE),
            lp_tokens=self.market_state.share_price * share_reserves + bond_reserves,
        )
        self.update_market(market_deltas)
        return market_deltas, agent_deltas

    def open_short(
        self,
        agent_wallet: wallet.Wallet,
        bond_amount: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Calculates the deltas from opening a short and then updates the agent wallet & market state"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_open_short(
            agent_wallet.address,
            bond_amount,
            self,
        )
        self.update_market(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas

    def close_short(
        self,
        agent_wallet: wallet.Wallet,
        open_share_price: float,
        bond_amount: float,
        mint_time: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Calculate the deltas from closing a short and then update the agent wallet & market state"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_close_short(
            wallet_address=agent_wallet.address,
            bond_amount=bond_amount,
            market=self,
            mint_time=mint_time,
            open_share_price=open_share_price,
        )
        self.market_state.apply_delta(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas

    def open_long(
        self,
        agent_wallet: wallet.Wallet,
        base_amount: float,  # in base
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Calculate the deltas from opening a long and then update the agent wallet & market state"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_open_long(
            wallet_address=agent_wallet.address,
            base_amount=base_amount,
            market=self,
        )
        self.market_state.apply_delta(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas

    def close_long(
        self,
        agent_wallet: wallet.Wallet,
        bond_amount: float,  # in bonds
        mint_time: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Calculate the deltas from closing a long and then update the agent wallet & market state"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_close_long(
            wallet_address=agent_wallet.address,
            bond_amount=bond_amount,
            market=self,
            mint_time=mint_time,
        )
        self.market_state.apply_delta(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas

    def add_liquidity(
        self,
        agent_wallet: wallet.Wallet,
        trade_amount: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Computes new deltas for bond & share reserves after liquidity is added"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_add_liquidity(
            wallet_address=agent_wallet.address,
            bond_amount=trade_amount,
            market=self,
        )
        self.market_state.apply_delta(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas

    def remove_liquidity(
        self,
        agent_wallet: wallet.Wallet,
        bond_amount: float,
    ) -> tuple[hyperdrive_actions.MarketDeltas, wallet.Wallet]:
        """Computes new deltas for bond & share reserves after liquidity is removed"""
        market_deltas, agent_deltas = hyperdrive_actions.calc_remove_liquidity(
            wallet_address=agent_wallet.address,
            bond_amount=bond_amount,
            market=self,
        )
        self.market_state.apply_delta(market_deltas)
        agent_wallet.update(agent_deltas)
        return market_deltas, agent_deltas