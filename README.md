# backrun-scanner

A live Ethereum mainnet scanner that identifies cross-venue backrun arbitrage opportunities across Uniswap v2 and SushiSwap v2 pools.

---

## Why I built this

When I started my first job in crypto, my intro project was related to AMMs. It was an open-ended research role, where I could work on projects that interested me, and one of my recommended readings to start was the Uniswap V2 whitepaper. This paper is what sparked my curiosity in crypto that has defined the first few years of my career. My intro project, chosen by me, was to answer the question: given a pool of arbitrage capital and a typical trade size, what's the optimal curvature parameter for a vAMM?"

This take-home was a fun opportunity to return to the world of AMM math. In this project, we use the parameters returned in the ABI to construct the current state of the pool, utilizing the same formulas I used in that first project years ago. This project, which identifies backrun opportunities between Uniswap v2 and SushiSwap v2 pools, also has a connection to my current work.

While my responsibilities have shifted in the last 6 months, a considerable portion of my career so far has, and continues to be, centered around the SERVO auction. This auction sells MetaMask and external orderflow to searchers, who bid for the right to attach their arb (backrun) transactions to this orderflow onchain. These searchers run exactly this type of system, but one that covers *several* more pools, arb strategies, and chains. Working on this project allowed me to better understand these systems, allowing me to develop important insight into the searchers who bid into the auction I analyze essentially every day.

---

## What this is

The scanner watches the Ethereum mempool for pending Uniswap v2 router transactions. For each pending swap on a monitored token pair, it simulates the swap to compute the post-swap state of the victim pool, then checks the same pair on a different venue (SushiSwap v2) for a profitable round-trip arbitrage. Profitable opportunities are logged as JSONL to `data/opportunities.jsonl` with full economic context.

The system runs end-to-end against live mainnet data: state reads via Alchemy HTTPS, pending transactions via Alchemy WebSocket, no synthetic inputs.

Out of scope by design: transaction execution, multi-hop swaps, sandwich detection, v3 / v4, fee-on-transfer tokens, exact-output swap variants.

---

## The finding

Over a 5.5-hour live run (Ethereum blocks 25092688 → 25094354), the scanner observed:

- **833** pending transactions to the Uniswap v2 router
- **508** of those (61%) were one of the three supported exact-input swap variants
- **0** were profitable backrun opportunities

This is consistent with where Uniswap v2 sits in 2026. Flow is sparse, as most swap volume has migrated to v3 and v4, and the v2 swaps that do still route there are mostly small, generally below the trade size needed to displace a major pool past the 0.6% round-trip fee threshold required to overcome two LP fees.

Additionally, this finding represents a shift in Ethereum's orderflow pipeline over the past three years. The share of all transactions that hit the public mempools has dramatically fallen over this time, and the value that our naive system is searching for is being captured in orderflow auctions similar to the one I'm currently working on.

 A natural extension (deferred for time, noted below) is a parallel historical-mode analyzer that processes every confirmed swap rather than only mempool-visible ones, which would give a denser dataset for characterizing the opportunity distribution.

---

## How it works

```
pending tx (Alchemy WS)
       │
       ▼
  decode calldata     ── src/decoder.py
       │
       ▼
  resolve victim pool ── src/pools.py (registry + reserve cache)
       │
       ▼
  simulate victim swap on victim pool   ── src/amm.py
       │
       ▼
  find counterparty pool (same pair, other venue)
       │
       ▼
  optimize backrun trade size           ── src/amm.py (closed-form)
       │
       ▼
  if profitable: append to opportunities.jsonl
```

A separate `newHeads` subscription on the same WebSocket triggers a reserve refresh on every new block, so the cache is at most one block stale when pending txs arrive.

---

## Module overview

### `src/amm.py`

Pure-function Uniswap v2 AMM math. No I/O, no web3 calls. All integer arithmetic matches on-chain Solidity semantics exactly (truncating division, 997/1000 fee, ceiling-rounding in `getAmountIn`).

Public functions:
- `get_amount_out`, `get_amount_in` — standard swap math, matching `UniswapV2Library`
- `simulate_swap` — applies a swap and returns the new pool state
- `price_after_swap` — float, for display only
- `find_arb_direction` — given two pools holding the same pair, returns +1, -1, or 0 indicating which direction is profitable (or none). Uses pure integer cross-multiplication; exact at any reserve magnitude.
- `optimal_two_pool_arb` — closed-form solution for the optimal input amount that maximizes profit on a two-pool round-trip arb. Derivation in the docstring.

### `src/decoder.py`

Decodes Uniswap v2 router calldata into a structured dict. Handles three exact-input variants (`swapExactTokensForTokens`, `swapExactETHForTokens`, `swapExactTokensForETH`). Returns `None` for any other selector or malformed input — defensive against the adversarial nature of mempool data.

### `src/pools.py`

Pool registry. Loads `config/pools.yaml` and maintains a cache of current reserves. Builds a secondary pair-index keyed on `(token0, token1)` for O(1) counterparty lookup. Refreshes all 12 pools' reserves on each new block.

Key methods: `find_pool_by_router_path`, `get_counterparty_pools`, `refresh_all`.

### `src/opportunity.py`

The integration layer. Given a decoded pending swap and the current registry state, returns an opportunity dict if a profitable backrun exists. Handles direction resolution (which token is `token_in`), reserve orientation for the post-swap state, and counterparty selection. No I/O.

### `scripts/run_scanner.py`

The main entrypoint. Multiplexes two subscriptions (`newHeads` + `alchemy_pendingTransactions`) on a single Alchemy WebSocket connection. Routes incoming messages by subscription ID. Wraps the blocking `refresh_all` call in `asyncio.to_thread` so the event loop stays responsive during reserve refresh. Two-tier exception handling (`ConnectionClosed` + bare `Exception`) for the reconnect loop.

Per-block status line printed to stderr with running counters. Opportunities written to `data/opportunities.jsonl` and also logged to stderr on detection.

### `scripts/check_pools.py`, `scripts/check_mempool.py`

Sanity-check scripts used during development to verify the Alchemy connection, pool state reads, and mempool subscription. Both still work; useful for confirming a fresh environment is correctly configured.

---

## Design decisions

A few choices in the codebase worth flagging explicitly:

- **Integer math throughout `amm.py`, including for arb-direction checks.** Float division of 10^21-scale numbers loses precision in the low digits. Cross-multiplication (`997² · R1_A · R0_B > 1000² · R0_A · R1_B`) is exact at any reserve magnitude.

- **Pure functions vs. I/O are strictly separated.** `amm.py`, `decoder.py`, and `opportunity.py` have no web3 or WebSocket calls. All I/O lives in `pools.py` and `run_scanner.py`. This is why the tests can cover the logic end-to-end without touching the chain.

- **Single router subscription (Uniswap only).** See "Known limitations" below.

---

## Known limitations

1. **Asymmetric router subscription.** The scanner subscribes only to pending transactions hitting the Uniswap v2 router. Swaps going to SushiSwap's router are not observed, even though SushiSwap pools are used as arbitrage counterparties. A symmetric design would subscribe to both routers and use the tx's `to` field to disambiguate which pool is the victim. 

3. **No transaction execution.** The system identifies opportunities; it does not execute them. A production version would require a transaction-builder layer, a bundle-relay client (Flashbots, bloXroute, MEV-Share), and competitive priority-fee bidding against other searchers. All three are out of scope here.

4. **v2 only.** Most modern swap volume routes through Uniswap v3 (concentrated liquidity) and v4 (hooks). v3 math requires tick-aware simulation and is meaningfully harder; v4 adds a hook-execution layer on top. The architectural patterns in this codebase (registry + pure math + integration layer) generalize cleanly but the math modules would need substantial extension.

5. **Pool set is small.** Six WETH-paired token pairs across two venues. Choosing these specific pairs was a tradeoff between observable flow and quick verification; a production scanner would monitor hundreds.

6. **Sparse v2 mempool.** As the finding section documents, modern v2 router traffic is thin. This is structural, not a configuration issue.


## Running it

### Setup

```bash
git clone https://github.com/tivas-g/backrun-scanner.git
cd backrun-scanner
python3 -m pip install -e .
cp .env.example .env
# edit .env to add your Alchemy API key (used for both HTTPS and WS endpoints)
```

### Sanity checks

```bash
# read current reserves from all 12 pools, print prices
python3 scripts/check_pools.py

# subscribe to v2 router pending txs for 60 seconds, print whatever comes
python3 scripts/check_mempool.py
```

### Tests

```bash
python3 -m pytest -v
# 45 tests across amm, decoder, and opportunity
```

### Live scanner

```bash
python3 scripts/run_scanner.py
# prints per-block status to stderr
# appends profitable opportunities to data/opportunities.jsonl
# Ctrl+C for graceful shutdown with summary line
```

For a long-running session, background it:

```bash
nohup python3 scripts/run_scanner.py > scanner.out 2>&1 &
```

---

## Repo layout

```
backrun-scanner/
├── README.md
├── CLAUDE.md                  # design constraints, used to ground AI-assisted edits
├── LICENSE                    # MIT
├── pyproject.toml
├── .env.example
├── .gitignore
├── .claude/settings.json      # Claude Code permissions config
│
├── config/
│   └── pools.yaml             # 12 pools: 6 token pairs × 2 venues (Uni v2 + Sushi v2)
│
├── src/
│   ├── amm.py                 # pure Uniswap v2 math + two-pool arb optimizer
│   ├── decoder.py             # router calldata parser
│   ├── pools.py               # PoolRegistry + reserve cache
│   └── opportunity.py         # detect_opportunity()
│
├── scripts/
│   ├── check_pools.py         # sanity check: pool state reads
│   ├── check_mempool.py       # sanity check: mempool subscription
│   └── run_scanner.py         # the live scanner
│
├── tests/
│   ├── test_amm.py            # 25 tests
│   ├── test_decoder.py        # 9 tests
│   └── test_opportunity.py    # 10 tests, FakeRegistry stub
│
└── data/
    └── opportunities.jsonl    # output (gitignored)
```

---

## Tooling note

This project was built with substantial AI assistance. Claude Code was used for scaffolding modules and tests, and I iterated with a long Claude.ai conversation for scoping and architecture decisions. The opening of every Claude Code session was grounded in `CLAUDE.md`, which encodes the project's scope constraints (no v3, no execution, exact-input variants only). I reviewed every commit, and commented in files to ensure understanding and accuracy. I also asked other LLMs to review work when I was unsure as to whether something was correct. 