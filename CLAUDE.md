# backrun-scanner

A live Ethereum mainnet scanner that subscribes to the mempool, identifies pending swaps on monitored Uniswap v2 pools that move price meaningfully, and computes the optimal backrun trade on another v2 pool. Logs profitable opportunities with full economic context.

## Stack
- Python 3.11+
- `web3.py` for RPC
- `websockets` for the mempool subscription
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
- Web3 calls only happen in `src/pools.py` and `src/mempool.py`
- Opportunities are logged as one JSON object per line in `data/opportunities.jsonl`
- Commit often with real messages; this repo is being read end-to-end

## Out of scope
- Actual transaction execution (we compute theoretical profit only)
- Uniswap v3, Curve, Balancer
- Multi-hop or sandwich detection