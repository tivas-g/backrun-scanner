"""Sanity-check: print current price for every pool in config/pools.yaml. Basically just testing to make sure the API key works."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from web3 import Web3

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

HTTPS_URL = os.environ["ALCHEMY_HTTPS"]

#only pulling the getReserves function from the uni ABI because its the only function we will need for this limited project.
GET_RESERVES_ABI = [
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


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(HTTPS_URL))

    with open(ROOT / "config" / "pools.yaml") as f:
        pools = yaml.safe_load(f)["pools"]

    for pool in pools:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(pool["address"]),
            abi=GET_RESERVES_ABI,
        )
        reserve0, reserve1, _ = contract.functions.getReserves().call()

        # Adjust raw reserves for token decimals to get human-readable amounts.
        r0 = reserve0 / 10 ** pool["token0_decimals"]
        r1 = reserve1 / 10 ** pool["token1_decimals"]

        # Price = ratio of the other reserve (constant-product AMM).
        #float division only used here for display, will use integer arithmetic in our analysis script to match whats onchain
        price_1_per_0 = r1 / r0  # token1 per token0
        price_0_per_1 = r0 / r1  # token0 per token1

        sym0 = pool["token0_symbol"]
        sym1 = pool["token1_symbol"]

        dex = pool["dex"]
        print(
            f"[{dex}] {sym0}/{sym1}: "
            f"1 {sym0} = {price_1_per_0:.6g} {sym1}  "
            f"(1 {sym1} = {price_0_per_1:.6g} {sym0})"
        )


if __name__ == "__main__":
    main()
