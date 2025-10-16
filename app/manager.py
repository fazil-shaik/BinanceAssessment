import asyncio
import logging
from typing import Dict, Set

from fastapi import WebSocket

logger = logging.getLogger("binance-relay.manager")


class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("Client connected: %s", id(websocket))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info("Client disconnected: %s", id(websocket))

    async def broadcast(self, message: Dict):
        async with self._lock:
            connections = list(self.active_connections)

        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(ws)


manager = ConnectionManager()
