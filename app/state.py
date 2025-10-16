import asyncio
from typing import Dict

# Shared runtime state
latest_prices: Dict[str, Dict] = {}
message_queue: asyncio.Queue = asyncio.Queue()
