# backrun-scanner

A live Ethereum mainnet scanner that identifies backrun arbitrage opportunities across Uniswap v2 pools.

## What it does
Subscribes to the public mempool, identifies pending swaps that move a monitored pool's price meaningfully, and computes the optimal backrun trade on a different venue. Logs profitable opportunities with full economic context (gross profit, gas cost, net profit).

## Status
🚧 Work in progress.

## Setup
1. `uv sync` (or `pip install -e .`)
2. `cp .env.example .env` and add your Alchemy API key
3. `make run`

## Findings
*To be added after a live run.*