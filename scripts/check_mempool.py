"""Sanity-check: subscribe to pending Uniswap v2 router txs and print them for 60 s."""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

WS_URL = os.environ["ALCHEMY_WS"]

UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dacb4c659F2488D"
TIMEOUT_SECONDS = 60

SUBSCRIBE_MSG = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": [
            "alchemy_pendingTransactions",
            {
                "toAddress": [UNISWAP_V2_ROUTER],
                "hashesOnly": False,
            },
        ],
    }
)


async def listen() -> None:
    count = 0
    try:
        async with websockets.connect(WS_URL) as ws:
            await ws.send(SUBSCRIBE_MSG)
            ack = json.loads(await ws.recv())
            sub_id = ack.get("result", "?")
            print(f"Subscribed  id={sub_id}  watching {UNISWAP_V2_ROUTER}")
            print(f"Running for {TIMEOUT_SECONDS} s …\n")

            async def consume() -> None:
                nonlocal count
                async for raw in ws:
                    msg = json.loads(raw)
                    tx = msg.get("params", {}).get("result", {})
                    if not tx:
                        continue

                    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    tx_hash = tx.get("hash", "")[:10]
                    gas_price_wei = int(tx.get("gasPrice", 0), 16)
                    gas_price_gwei = gas_price_wei / 1e9
                    print(f"{ts}  {tx_hash}  {gas_price_gwei:.2f} gwei")
                    count += 1

            await asyncio.wait_for(consume(), timeout=TIMEOUT_SECONDS)

    except asyncio.TimeoutError:
        pass  # clean exit after the timeout
    except websockets.ConnectionClosed as exc:
        print(f"Connection closed: {exc}")

    print(f"\nDone. Saw {count} pending tx(s) in {TIMEOUT_SECONDS} s.")


if __name__ == "__main__":
    asyncio.run(listen())
