import asyncio
import json
import logging
import time
from typing import Dict

import websockets

from .state import latest_prices, message_queue

logger = logging.getLogger("binance-relay.binance")


async def binance_listener(symbol: str):
    """Connect to Binance WebSocket for a single symbol and push parsed messages to message_queue."""
    uri = f"wss://stream.binance.com:9443/ws/{symbol}@ticker"
    backoff = 1
    while True:
        try:
            logger.info("Connecting to Binance stream %s", uri)
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                backoff = 1
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        logger.debug("Non-JSON message: %s", raw)
                        continue

                    symbol_name = data.get("s")
                    last_price = data.get("c")
                    change_pct = data.get("P")
                    timestamp = data.get("E") or int(time.time() * 1000)

                    payload = {
                        "symbol": symbol_name,
                        "last_price": last_price,
                        "change_pct": change_pct,
                        "timestamp": timestamp,
                    }

                    # update latest
                    if symbol_name:
                        latest_prices[symbol_name] = payload

                    # push to queue for broadcasting
                    await message_queue.put(payload)

        except Exception as e:
            logger.warning("Binance listener for %s error: %s", symbol, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
