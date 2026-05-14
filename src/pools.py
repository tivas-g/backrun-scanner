"""
Pool registry: loads config/pools.yaml, caches on-chain reserves, and answers
pair-lookup queries used by the opportunity detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from web3 import Web3


_GET_RESERVES_ABI = [
    {
        "name": "getReserves",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"name": "reserve0", "type": "uint112"},
            {"name": "reserve1", "type": "uint112"},
            {"name": "blockTimestampLast", "type": "uint32"},
        ],
    }
]


@dataclass
class Pool:
    address: str
    dex: str
    token0_address: str
    token0_symbol: str
    token0_decimals: int
    token1_address: str
    token1_symbol: str
    token1_decimals: int
    reserve0: int = field(default=0)
    reserve1: int = field(default=0)
    last_updated_block: int = field(default=0)


class PoolRegistry:
    """
    Loads monitored pools from YAML, caches reserves, and answers pair queries.

    All addresses are stored and compared in lowercase so callers don't need to
    worry about checksum casing.
    """

    def __init__(self, config_path: Path, w3: Web3) -> None:
        self._w3 = w3

        # Primary index: lowercased pool address → Pool
        self._pools: dict[str, Pool] = {}

        # Secondary index: (token0_lower, token1_lower) → [Pool, ...]
        # Always stored with token0 < token1 (address sort order from YAML),
        # so a single key covers both lookup directions.
        self._pair_index: dict[tuple[str, str], list[Pool]] = {}

        with open(config_path) as f:
            entries = yaml.safe_load(f)["pools"]

        for entry in entries:
            pool = Pool(
                address=entry["address"].lower(),
                dex=entry["dex"],
                token0_address=entry["token0_address"].lower(),
                token0_symbol=entry["token0_symbol"],
                token0_decimals=int(entry["token0_decimals"]),
                token1_address=entry["token1_address"].lower(),
                token1_symbol=entry["token1_symbol"],
                token1_decimals=int(entry["token1_decimals"]),
            )
            self._pools[pool.address] = pool

            key = (pool.token0_address, pool.token1_address)
            self._pair_index.setdefault(key, []).append(pool)

    # ── reserve management ────────────────────────────────────────────────────

    def refresh_all(self) -> None:
        """Fetch current reserves for every pool and update cached state."""
        block = self._w3.eth.block_number
        for pool in self._pools.values():
            contract = self._w3.eth.contract(
                address=Web3.to_checksum_address(pool.address),
                abi=_GET_RESERVES_ABI,
            )
            r0, r1, _ = contract.functions.getReserves().call()
            pool.reserve0 = r0
            pool.reserve1 = r1
            pool.last_updated_block = block

    # ── pair queries ──────────────────────────────────────────────────────────

    def find_pools_for_pair(self, token_a: str, token_b: str) -> list[Pool]:
        """
        Return all monitored pools that hold exactly this token pair.

        Checks both orderings so callers don't need to know which is token0.
        """
        a = token_a.lower()
        b = token_b.lower()
        return self._pair_index.get((a, b), []) or self._pair_index.get((b, a), [])

    def find_pool_by_router_path(self, path: list[str]) -> Pool | None:
        """
        Given a two-element router swap path, return the first monitored pool
        that holds that pair (YAML order), or None.

        Paths longer than two elements (multi-hop) are out of scope and return None.
        """
        if len(path) != 2:
            return None
        pools = self.find_pools_for_pair(path[0], path[1])
        return pools[0] if pools else None

    def get_counterparty_pools(self, pool: Pool) -> list[Pool]:
        """
        Return the other monitored pools holding the same token pair on a
        different DEX — the candidate venues to arb against after a victim
        swap on `pool`.
        """
        candidates = self.find_pools_for_pair(pool.token0_address, pool.token1_address)
        return [p for p in candidates if p.address != pool.address]
