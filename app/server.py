import asyncio
import logging
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import LOG_LEVEL
from .manager import manager
from .state import latest_prices, message_queue
from .binance import binance_listener


logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("binance-relay")


def create_app(symbols: List[str] = None) -> FastAPI:
    app = FastAPI()

    if symbols is None:
        symbols = ["btcusdt", "ethusdt"]


    @app.get("/")
    async def root():
        return {"message": "Binance relay running"}


    @app.get("/price")
    async def get_price():
        return latest_prices


    @app.on_event("startup")
    async def startup_event():
        app.state.tasks = []

        # broadcaster
        async def broadcaster_task():
            while True:
                msg = await message_queue.get()
                if msg is None:
                    continue
                await manager.broadcast(msg)

        t = asyncio.create_task(broadcaster_task())
        app.state.tasks.append(t)

        # start listeners
        for sym in symbols:
            t = asyncio.create_task(binance_listener(sym))
            app.state.tasks.append(t)


    @app.on_event("shutdown")
    async def shutdown_event():
        tasks = getattr(app.state, "tasks", [])
        for t in tasks:
            t.cancel()


    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            if latest_prices:
                for p in latest_prices.values():
                    await websocket.send_json(p)

            while True:
                try:
                    await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                except Exception:
                    await asyncio.sleep(0.1)
        finally:
            await manager.disconnect(websocket)

    return app
