import asyncio
import logging
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

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
        # If we have cached prices, return them immediately
        if latest_prices:
            return latest_prices

        # On serverless platforms the background Binance websocket listeners
        # may not be running. Fall back to querying Binance REST API for a
        # small set of symbols so /price still works in production.
        try:
            import httpx

            symbols = ["BTCUSDT", "ETHUSDT"]
            results = {}
            logger.info("/price fallback: querying Binance REST for symbols: %s", symbols)

            # small retry loop (2 attempts)
            attempt = 0
            timeout_seconds = 20.0
            while attempt < 2 and not results:
                attempt += 1
                logger.info("/price fallback: attempt %s", attempt)
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    for sym in symbols:
                        url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
                        try:
                            r = await client.get(url)
                        except Exception as e:
                            logger.warning("/price fallback: request error for %s: %s", url, e)
                            continue

                        logger.info("/price fallback: %s -> status %s", url, r.status_code)
                        if r.status_code == 200:
                            data = r.json()
                            results[data.get("symbol")] = {"last_price": data.get("price")}

                if not results:
                    await asyncio.sleep(0.5)

            if results:
                logger.info("/price fallback: returning results: %s", list(results.keys()))
                return results
            # nothing returned from Binance REST fallback — try CoinGecko as a public alternative
            logger.info("/price fallback: trying CoinGecko as alternative")
            try:
                # map target symbols to CoinGecko ids
                mapping = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}
                ids = ",".join(mapping.values())
                cg_url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(cg_url)
                    logger.info("/price fallback: CoinGecko -> status %s", r.status_code)
                    if r.status_code == 200:
                        data = r.json()
                        cg_results = {}
                        for sym, cid in mapping.items():
                            price = None
                            obj = data.get(cid)
                            if obj:
                                # use the usd price and format as string to match other responses
                                price = str(obj.get("usd"))
                            if price is not None:
                                cg_results[sym] = {"last_price": price}
                        if cg_results:
                            logger.info("/price fallback: returning CoinGecko results: %s", list(cg_results.keys()))
                            return cg_results
            except Exception as e:
                logger.warning("/price fallback: CoinGecko attempt failed: %s", e)

            # still nothing; return an informative error
            logger.warning("/price fallback: no results after all fallbacks; returning error")
            return JSONResponse(status_code=502, content={"error": "no prices available (all fallbacks failed)"})
        except Exception as e:
            logger.exception("/price fallback: unexpected exception: %s", e)
            return JSONResponse(status_code=502, content={"error": "fallback exception", "details": str(e)})


    @app.get("/price_debug")
    async def price_debug():
        """Diagnostic endpoint to show what the fallback does in production.

        Returns a detailed JSON with whether cached prices exist and the results
        or errors from attempting to query Binance REST.
        """
        info = {"latest_prices_present": bool(latest_prices), "latest_prices_keys": list(latest_prices.keys())}

        # attempt single REST fetch and capture detailed results
        attempts = []
        try:
            import httpx

            symbols = ["BTCUSDT", "ETHUSDT"]
            async with httpx.AsyncClient(timeout=5.0) as client:
                for sym in symbols:
                    url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
                    try:
                        r = await client.get(url)
                        attempts.append({"symbol": sym, "url": url, "status_code": r.status_code, "text": r.text[:500]})
                    except Exception as e:
                        attempts.append({"symbol": sym, "url": url, "error": str(e)})

                # also attempt CoinGecko for diagnostics
                try:
                    mapping = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}
                    ids = ",".join(mapping.values())
                    cg_url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
                    try:
                        r = await client.get(cg_url)
                        info["coingecko"] = {"status_code": r.status_code, "text": r.text[:500]}
                    except Exception as e:
                        info["coingecko_error"] = str(e)
                except Exception:
                    pass
        except Exception as e:
            info["import_error"] = str(e)

        info["attempts"] = attempts
        return info


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
