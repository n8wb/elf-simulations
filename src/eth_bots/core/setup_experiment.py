"""Setup helper function for running eth bot experiments."""
from __future__ import annotations

import os
from http import HTTPStatus

import numpy as np
import requests
from elfpy.utils import logs
from web3 import Web3
from web3.contract.contract import Contract

from src import eth, hyperdrive
from src.eth.accounts import EthAgent
from src.eth_bots.core import DEFAULT_USERNAME, EnvironmentConfig, crash_report
from src.eth_bots.core.get_agent_accounts import get_agent_accounts
from src.eth_bots.eth_bots_config import get_eth_bots_config


def setup_experiment() -> tuple[Web3, Contract, Contract, EnvironmentConfig, list[EthAgent]]:
    """Get agents according to provided config, provide eth, base token and approve hyperdrive.

    Returns
    -------
    tuple[Web3, Contract, Contract, EnvironmentConfig, list[EthAgent]]
        A tuple containing:
            - The web3 container
            - The hyperdrive contract
            - The base token contract
            - The environment configuration
            - A list of EthAgent objects that contain a wallet address and Elfpy Agent for determining trades

    """
    # get the user defined config variables
    environment_config, agent_config = get_eth_bots_config()
    # this random number generator should be used everywhere so that the experiment is repeatable
    # rng stores the state of the random number generator, so that we can pause and restart experiments from any point
    rng = np.random.default_rng(environment_config.random_seed)
    # setup logging
    logs.setup_logging(
        log_filename=environment_config.log_filename,
        max_bytes=environment_config.max_bytes,
        log_level=environment_config.log_level,
        delete_previous_logs=environment_config.delete_previous_logs,
        log_stdout=environment_config.log_stdout,
        log_format_string=environment_config.log_formatter,
    )
    crash_report.setup_hyperdrive_crash_report_logging()
    # Check for default name and exit if is default
    if environment_config.username == DEFAULT_USERNAME:
        raise ValueError("Default username detected, please update 'username' in eth_bots_config.py")
    # point to chain env
    web3 = eth.web3_setup.initialize_web3_with_http_provider(environment_config.rpc_url, reset_provider=False)
    # setup base contract interface
    hyperdrive_abis = eth.abi.load_all_abis(environment_config.abi_folder)
    addresses = hyperdrive.contract_interface.fetch_hyperdrive_address_from_url(
        os.path.join(environment_config.artifacts_url, "addresses.json")
    )
    # set up the ERC20 contract for minting base tokens
    base_token_contract: Contract = web3.eth.contract(
        abi=hyperdrive_abis[environment_config.base_abi], address=web3.to_checksum_address(addresses.base_token)
    )
    # set up hyperdrive contract
    hyperdrive_contract: Contract = web3.eth.contract(
        abi=hyperdrive_abis[environment_config.hyperdrive_abi],
        address=web3.to_checksum_address(addresses.mock_hyperdrive),
    )
    # load agent policies
    # rng is shared by the agents and can be accessed via `agent_accounts[idx].policy.rng`
    agent_accounts = get_agent_accounts(agent_config, web3, base_token_contract, hyperdrive_contract.address, rng)
    # Set up postgres to write username to agent wallet addr
    # initialize the postgres session
    wallet_addrs = [str(agent.checksum_address) for agent in agent_accounts]
    register_username(environment_config.username_register_url, wallet_addrs, environment_config.username)
    return web3, hyperdrive_contract, base_token_contract, environment_config, agent_accounts


def register_username(register_url: str, wallet_addrs: list[str], username: str) -> None:
    """Registers the username with the flask server."""
    # TODO: use the json schema from the server.
    json_data = {"wallet_addrs": wallet_addrs, "username": username}
    result = requests.post(f"{register_url}/register_bots", json=json_data, timeout=3)
    if result.status_code != HTTPStatus.OK:
        raise ConnectionError(result)