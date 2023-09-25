"""Run the dashboard."""
# pylint: disable=invalid-name
# Streamlit gets the name of the sidebar tab from the name of the file
# hence, this file is capitalized

from __future__ import annotations

import gc
import time

import matplotlib.pyplot as plt
import mplfinance as mpf
import streamlit as st
from chainsync.dashboard import (
    build_fixed_rate,
    build_leaderboard,
    build_ohlcv,
    build_outstanding_positions,
    build_ticker,
    get_user_lookup,
    plot_fixed_rate,
    plot_ohlcv,
    plot_outstanding_positions,
)
from chainsync.db.base import get_user_map, initialize_session
from chainsync.db.hyperdrive import (
    get_all_traders,
    get_pool_analysis,
    get_pool_config,
    get_pool_info,
    get_ticker,
    get_wallet_pnl,
)
from ethpy import build_eth_config

# pylint: disable=invalid-name

plt.close("all")
gc.collect()

st.set_page_config(page_title="Trading Competition Dashboard", layout="wide")
st.set_option("deprecation.showPyplotGlobalUse", False)

# Load and connect to postgres
session = initialize_session()

# TODO remove this connection and add in process to periodically calculate closing pnl
eth_config = build_eth_config()

# pool config data is static, so just read once
config_data = get_pool_config(session, coerce_float=False)

config_data = config_data.iloc[0]

max_live_blocks = 5000
# Live ticker
ticker_placeholder = st.empty()
# OHLCV
main_placeholder = st.empty()

main_fig = mpf.figure(style="mike", figsize=(10, 10))
# matplotlib doesn't play nice with types
(ax_ohlcv, ax_fixed_rate, ax_positions) = main_fig.subplots(3, 1, sharex=True)  # type: ignore

while True:
    # Wallet addr to username mapping
    agents = get_all_traders(session)
    user_map = get_user_map(session)
    user_lookup = get_user_lookup(agents, user_map)

    pool_info = get_pool_info(session, start_block=-max_live_blocks, coerce_float=False)
    pool_analysis = get_pool_analysis(session, start_block=-max_live_blocks, coerce_float=False)
    ticker = get_ticker(session, start_block=-max_live_blocks, coerce_float=False)
    # Adds user lookup to the ticker
    display_ticker = build_ticker(ticker, user_lookup)

    # get wallet pnl and calculate leaderboard
    # Get the latest updated block
    latest_wallet_pnl = get_wallet_pnl(session, start_block=-1, coerce_float=False)
    comb_rank, ind_rank = build_leaderboard(latest_wallet_pnl, user_lookup)

    # build ohlcv and volume
    ohlcv = build_ohlcv(pool_analysis, freq="5T")
    # build fixed rate
    fixed_rate = build_fixed_rate(pool_analysis)
    # build outstanding positions plots
    outstanding_positions = build_outstanding_positions(pool_info)

    with ticker_placeholder.container():
        st.header("Ticker")
        st.dataframe(display_ticker, height=200, use_container_width=True)
        st.header("Total Leaderboard")
        st.dataframe(comb_rank, height=500, use_container_width=True)
        st.header("Wallet Leaderboard")
        st.dataframe(ind_rank, height=500, use_container_width=True)

    with main_placeholder.container():
        # Clears all axes
        ax_ohlcv.clear()
        ax_fixed_rate.clear()
        ax_positions.clear()

        plot_ohlcv(ohlcv, ax_ohlcv)
        plot_fixed_rate(fixed_rate, ax_fixed_rate)
        plot_outstanding_positions(outstanding_positions, ax_positions)

        ax_ohlcv.tick_params(axis="both", which="both")
        ax_fixed_rate.tick_params(axis="both", which="both")
        # Fix axes labels
        main_fig.autofmt_xdate()
        # streamlit doesn't play nice with types
        st.pyplot(fig=main_fig)  # type: ignore

    time.sleep(1)