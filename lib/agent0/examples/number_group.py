"""Script to showcase running default implemented agents."""
from __future__ import annotations

import logging

from agent0 import initialize_accounts
from agent0.base.config import AgentConfig, EnvironmentConfig
from agent0.hyperdrive.exec import run_agents
from agent0.hyperdrive.policies import Zoo
from ethpy import EthConfig
from fixedpointmath import FixedPoint

# Define the unique env filename to use for this script
ENV_FILE = "hyperdrive_agents.account.env"
# Host of docker services
HOST = "3.13.94.236"
# Username binding of bots
USERNAME = "timmy"
# Run this file with this flag set to true to close out all open positions
LIQUIDATE = False

# Build configuration
#eth_config = EthConfig(artifacts_uri="http://" + HOST + ":8080", rpc_uri="http://" + HOST + ":8545")

env_config = EnvironmentConfig(
    delete_previous_logs=True,
    halt_on_errors=True,
    log_filename=".logging/agent0_logs.log",
    log_level=logging.INFO,
    log_stdout=True,
    random_seed=1234,
    #database_api_uri="http://" + HOST + ":5002",
    username=USERNAME,
)

agent_config: list[AgentConfig] = [
    AgentConfig(
        policy=Zoo.arbitrage,
        number_of_agents=1,
        slippage_tolerance=None,  # No slippage tolerance for arb bot
        # Fixed budgets
        base_budget_wei=FixedPoint(500).scaled_value,  # 50k base
        eth_budget_wei=FixedPoint(1).scaled_value,  # 1 base
        policy_config=Zoo.arbitrage.Config(
            trade_amount=FixedPoint(1000),  # Open 1k in base or short 1k bonds
            high_fixed_rate_thresh=FixedPoint(0.1),  # Upper fixed rate threshold
            low_fixed_rate_thresh=FixedPoint(0.02),  # Lower fixed rate threshold
        ),
    ),
    AgentConfig(
        policy=Zoo.smart_short,
        number_of_agents=1,
        slippage_tolerance=FixedPoint("0.0001"),
        # Fixed budget
        base_budget_wei=FixedPoint(100).scaled_value,  # 5k base
        eth_budget_wei=FixedPoint(1).scaled_value,  # 1 base
        policy_config=Zoo.smart_short.Config(
            only_one_short=False),
    ),
    AgentConfig(
        policy=Zoo.smart_long2,
        number_of_agents=1,
        slippage_tolerance=FixedPoint("0.0001"),
        # Fixed budget
        base_budget_wei=FixedPoint(100).scaled_value,  # 5k base
        eth_budget_wei=FixedPoint(1).scaled_value,  # 1 base
        policy_config=Zoo.smart_long2.Config(
            only_one_long=True,
            risk_threshold=FixedPoint("0.001")),
    ),
]


# Build accounts env var
# This function writes a user defined env file location.
# If it doesn't exist, create it based on agent_config
# (If develop is False, will clean exit and print instructions on how to fund agent)
# If it does exist, read it in and use it
account_key_config = initialize_accounts(agent_config, env_file=ENV_FILE, random_seed=env_config.random_seed)

# Run agents
run_agents(env_config, agent_config, account_key_config,  liquidate=LIQUIDATE)
