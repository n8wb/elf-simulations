"""Main script for running agents on Hyperdrive."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NoReturn

from agent0.base import Quantity, TokenType
from agent0.hyperdrive.state import (
    HyperdriveActionType,
    HyperdriveMarketAction,
    HyperdriveWalletDeltas,
    Long,
    Short,
    TradeResult,
    TradeStatus,
)
from elfpy import types
from ethpy.base import retry_call
from ethpy.hyperdrive import HyperdriveInterface
from web3.types import Nonce

if TYPE_CHECKING:
    from agent0.hyperdrive.agents import HyperdriveAgent


def assert_never(arg: NoReturn) -> NoReturn:
    """Helper function for exhaustive matching on ENUMS.

    .. note::
        This ensures that all ENUM values are checked, via an exhaustive match:
        https://github.com/microsoft/pyright/issues/2569
    """
    assert False, f"Unhandled type: {type(arg).__name__}"


async def async_execute_single_agent_trade(
    agent: HyperdriveAgent, hyperdrive: HyperdriveInterface, liquidate: bool
) -> list[TradeResult]:
    """Executes a single agent's trade. This function is async as
    `match_contract_call_to_trade` waits for a transaction receipt.

    Arguments
    ---------
    agent: HyperdriveAgent
        The HyperdriveAgent that is conducting the trade
    hyperdrive : HyperdriveInterface
        The Hyperdrive API interface object
    liquidate: bool
        If set, will ignore all policy settings and liquidate all open positions

    Returns
    -------
    list[TradeResult]
        Returns a list of TradeResult objects, one for each trade made by the agent
        TradeResult handles any information about the trade, as well as any errors that the trade resulted in
    """
    if liquidate:
        trades: list[types.Trade[HyperdriveMarketAction]] = agent.get_liquidation_trades()
    else:
        trades: list[types.Trade[HyperdriveMarketAction]] = agent.get_trades(interface=hyperdrive)

    # Make trades async for this agent. This way, an agent can submit multiple trades for a single block
    # To do this, we need to manually set the nonce, so we get the base transaction count here
    # and pass in an incrementing nonce per call
    # TODO figure out which exception here to retry on
    base_nonce = retry_call(5, None, hyperdrive.web3.eth.get_transaction_count, agent.checksum_address)

    # TODO preliminary search shows async tasks has very low overhead:
    # https://stackoverflow.com/questions/55761652/what-is-the-overhead-of-an-asyncio-task
    # However, should probably test what the limit number of trades an agent can make in one block
    wallet_deltas_or_exception: list[HyperdriveWalletDeltas | BaseException] = await asyncio.gather(
        *[
            async_match_contract_call_to_trade(agent, hyperdrive, trade_object, nonce=Nonce(base_nonce + i))
            for i, trade_object in enumerate(trades)
        ],
        # Instead of throwing exception, return the exception to the caller here
        return_exceptions=True,
    )

    # TODO Here, gather returns results based on original order of trades, but this order isn't guaranteed
    # because of async. Ideally, we should return results based on the order of trades. Can we use nonce here
    # to see order?

    # Sanity check
    if len(wallet_deltas_or_exception) != len(trades):
        raise AssertionError(
            "The number of wallet deltas should match the number of trades, but does not."
            f"\n{wallet_deltas_or_exception=}\n{trades=}"
        )

    # The wallet update after should be fine, since we can see what trades went through
    # and only apply those wallet deltas. Wallet deltas are also invariant to order
    # as long as the transaction went through.
    trade_results = []
    for result, trade_object in zip(wallet_deltas_or_exception, trades):
        if isinstance(result, HyperdriveWalletDeltas):
            agent.wallet.update(result)
            trade_result = TradeResult(status=TradeStatus.SUCCESS, agent=agent, trade_object=trade_object)
        elif isinstance(result, Exception):
            # We log pool config and pool info here
            # However, this is a best effort attempt to get this information
            # due to async conditions. If debugging this crash, ensure the agent is running
            # in isolation and doing one trade per call.
            pool_config = hyperdrive.pool_config
            pool_info = hyperdrive.pool_info
            checkpoint_info = hyperdrive.latest_checkpoint
            # add additional information to the exception
            additional_info = {
                "spot_price": hyperdrive.spot_price,
                "fixed_rate": hyperdrive.fixed_rate,
                "variable_rate": hyperdrive.variable_rate,
                "vault_shares": hyperdrive.vault_shares,
            }
            trade_result = TradeResult(
                status=TradeStatus.FAIL,
                agent=agent,
                trade_object=trade_object,
                exception=result,
                pool_config=pool_config,
                pool_info=pool_info,
                checkpoint_info=checkpoint_info,
                additional_info=additional_info,
            )
        else:  # Should never get here
            # TODO: use match statement and assert_never(result)
            raise AssertionError("invalid result type")
        trade_results.append(trade_result)

    return trade_results


async def async_execute_agent_trades(
    hyperdrive: HyperdriveInterface,
    agents: list[HyperdriveAgent],
    liquidate: bool,
) -> list[TradeResult]:
    """Hyperdrive forever into the sunset.

    Arguments
    ---------
    hyperdrive : HyperdriveInterface
        The Hyperdrive API interface object
    agents : list[HyperdriveAgent]
        A list of HyperdriveAgent that are conducting the trades
    liquidate: bool
        If set, will ignore all policy settings and liquidate all open positions

    Returns
    -------
    list[TradeResult]
        Returns a list of TradeResult objects, one for each trade made by the agent
        TradeResult handles any information about the trade, as well as any errors that the trade resulted in
    """
    # Make calls per agent to execute_single_agent_trade
    # Await all trades to finish before continuing
    gathered_trade_results: list[list[TradeResult]] = await asyncio.gather(
        *[async_execute_single_agent_trade(agent, hyperdrive, liquidate) for agent in agents if not agent.done_trading]
    )
    # Flatten list of lists, since agent information is already in TradeResult
    trade_results = [item for sublist in gathered_trade_results for item in sublist]
    return trade_results


async def async_match_contract_call_to_trade(
    agent: HyperdriveAgent,
    hyperdrive: HyperdriveInterface,
    trade_envelope: types.Trade[HyperdriveMarketAction],
    nonce: Nonce,
) -> HyperdriveWalletDeltas:
    """Match statement that executes the smart contract trade based on the provided type.

    Arguments
    ---------
    agent : HyperdriveAgent
        Object containing a wallet address and Elfpy Agent for determining trades
    hyperdrive : HyperdriveInterface
        The Hyperdrive API interface object
    trade_object : Trade
        A specific trade requested by the given agent

    Returns
    -------
    HyperdriveWalletDeltas
        Deltas to be applied to the agent's wallet
    """
    # TODO: figure out fees paid
    trade = trade_envelope.market_action
    match trade.action_type:
        case HyperdriveActionType.INITIALIZE_MARKET:
            raise ValueError(f"{trade.action_type} not supported!")

        case HyperdriveActionType.OPEN_LONG:
            trade_result = await hyperdrive.async_open_long(
                agent, trade.trade_amount, trade.slippage_tolerance, nonce=nonce
            )
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=-trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                longs={trade_result.maturity_time_seconds: Long(trade_result.bond_amount)},
            )

        case HyperdriveActionType.CLOSE_LONG:
            if not trade.maturity_time:
                raise ValueError("Maturity time was not provided, can't close long position.")
            trade_result = await hyperdrive.async_close_long(
                agent, trade.trade_amount, trade.maturity_time, trade.slippage_tolerance, nonce=nonce
            )
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                longs={trade.maturity_time: Long(-trade_result.bond_amount)},
            )

        case HyperdriveActionType.OPEN_SHORT:
            trade_result = await hyperdrive.async_open_short(
                agent, trade.trade_amount, trade.slippage_tolerance, nonce=nonce
            )
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=-trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                shorts={trade_result.maturity_time_seconds: Short(balance=trade_result.bond_amount)},
            )

        case HyperdriveActionType.CLOSE_SHORT:
            if not trade.maturity_time:
                raise ValueError("Maturity time was not provided, can't close long position.")
            trade_result = await hyperdrive.async_close_short(
                agent, trade.trade_amount, trade.maturity_time, trade.slippage_tolerance, nonce=nonce
            )
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                shorts={trade.maturity_time: Short(balance=-trade_result.bond_amount)},
            )

        case HyperdriveActionType.ADD_LIQUIDITY:
            min_apr = trade.min_apr
            assert min_apr, "min_apr is required for ADD_LIQUIDITY"
            max_apr = trade.max_apr
            assert max_apr, "max_apr is required for ADD_LIQUIDITY"
            trade_result = await hyperdrive.async_add_liquidity(
                agent, trade.trade_amount, min_apr, max_apr, nonce=nonce
            )
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=-trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                lp_tokens=trade_result.lp_amount,
            )

        case HyperdriveActionType.REMOVE_LIQUIDITY:
            trade_result = await hyperdrive.async_remove_liquidity(agent, trade.trade_amount, nonce=nonce)
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                lp_tokens=-trade_result.lp_amount,
                withdraw_shares=trade_result.withdrawal_share_amount,
            )

        case HyperdriveActionType.REDEEM_WITHDRAW_SHARE:
            trade_result = await hyperdrive.async_redeem_withdraw_shares(agent, trade.trade_amount, nonce=nonce)
            wallet_deltas = HyperdriveWalletDeltas(
                balance=Quantity(
                    amount=trade_result.base_amount,
                    unit=TokenType.BASE,
                ),
                withdraw_shares=-trade_result.withdrawal_share_amount,
            )

        case _:
            assert_never(trade.action_type)
    return wallet_deltas
