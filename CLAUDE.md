# backrun-scanner

A system that identifies backrun arbitrage opportunities across Uniswap v2 pools on Ethereum mainnet, operating in two modes simultaneously:
- **Live mode**: subscribes to pending mempool txs, identifies upcoming swaps that will move pool price meaningfully, computes the optimal backrun on another v2 pool.
- **Historical mode**: ingests every confirmed v2 swap (via newHeads + log subscription) and runs the same opportunity-detection logic post-hoc to compute realized backrun profitability.

The live mode is the real-time signal; the historical mode provides the dense dataset needed to characterize the v2 backrun opportunity surface.

## Stack
- Python 3.11+
- `web3.py` for RPC and event log decoding
- `websockets` for the mempool + newHeads subscriptions
- Alchemy as the sole RPC provider (HTTPS + WS, same API key)
- Pure-function AMM math, no I/O in `src/amm.py`

## Layout
- `src/` — library code
- `scripts/` — entrypoints and sanity checks
- `tests/` — pytest, unit tests for the math
- `config/pools.yaml` — hardcoded list of Uniswap v2 pools to monitor
- `data/` — JSONL output, gitignored except for a sample
- `analysis/` — post-hoc notebook for the findings writeup

## Conventions
- All AMM math lives in `src/amm.py` as pure functions; unit tests live in `tests/test_amm.py`
- Web3 / WebSocket I/O lives in `src/pools.py`, `src/mempool.py`, `src/swaps.py` (confirmed swap ingestion)
- Opportunities are logged as one JSON object per line:
  - `data/opportunities_live.jsonl` — mempool-detected, before confirmation
  - `data/opportunities_historical.jsonl` — confirmed-swap-detected, post-hoc
- Commit often with real messages; this repo is being read end-to-end

## Out of scope
- Actual transaction execution (we compute theoretical profit only)
- Uniswap v3, Curve, Balancer
- Multi-hop or sandwich detection
- Anything that would prevent shipping in <10 hours of work