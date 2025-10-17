import asyncio
import logging
from typing import List
import time
from typing import Dict

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
    async def get_price(symbols: str = None):
        """Return latest prices.

        - `symbols` optional comma-separated list (e.g. BTCUSDT,ETHUSDT)
        - Returns a dict keyed by symbol with payloads matching the websocket format:
          {symbol: {symbol, last_price, change_pct, timestamp}}
        """

        # build requested symbol list
        if symbols:
            req = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            # if caller doesn't specify, return defaults (either cached or latest_prices keys)
            req = []

        result: Dict[str, Dict] = {}

        # helper to normalize symbol keys used in latest_prices (Binance uses uppercase symbols like BTCUSDT)
        def norm(sym: str) -> str:
            return sym.upper()

        # first, try to populate from in-memory websocket cache
        if latest_prices:
            for k, v in latest_prices.items():
                ks = norm(k)
                if not req or ks in req:
                    result[ks] = v

        # module-level short-lived fallback cache (to make cold starts faster)
        # key: SYMBOL (upper) -> {payload, ts}
        # TTL seconds
        CACHE_TTL = 5
        try:
            cache = getattr(app.state, "fallback_cache")
        except Exception:
            cache = {}
            app.state.fallback_cache = cache

        now = int(time.time() * 1000)

        # symbols still needed
        needed = []
        if req:
            for s in req:
                if s in result:
                    continue
                entry = cache.get(s)
                if entry and (now - entry["ts"]) <= CACHE_TTL * 1000:
                    result[s] = entry["payload"]
                else:
                    needed.append(s)
        else:
            # if no specific req provided, but result empty, we can try to return cached defaults
            if not result:
                # try to use cached entries for any symbols present
                for s, entry in list(cache.items()):
                    if (now - entry["ts"]) <= CACHE_TTL * 1000:
                        result[s] = entry["payload"]
                # if still empty, allow fetching defaults
                if not result:
                    needed = ["BTCUSDT", "ETHUSDT"]

        # if nothing needed, return result
        if not needed:
            return result

        # For any needed symbols, try to fetch from CoinGecko in one call (fast public API)
        try:
            import httpx

            # build CoinGecko ids mapping dynamically for common symbols
            mapping = {
                "BTC": "bitcoin",
                "ETH": "ethereum",
                "BNB": "binancecoin",
                "ADA": "cardano",
                "DOGE": "dogecoin",
            }

            # determine which coin ids we can query
            id_map = {}
            for s in needed:
                base = s.upper()
                if base.endswith("USDT"):
                    base = base[:-4]
                cid = mapping.get(base)
                if cid:
                    id_map[s] = cid

            if id_map:
                ids = ",".join(sorted(set(id_map.values())))
                cg_url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(cg_url)
                    if r.status_code == 200:
                        data = r.json()
                        for sym, cid in id_map.items():
                            obj = data.get(cid)
                            if obj and "usd" in obj:
                                payload = {
                                    "symbol": sym,
                                    "last_price": str(obj.get("usd")),
                                    "change_pct": None,
                                    "timestamp": now,
                                }
                                result[sym] = payload
                                cache[sym] = {"payload": payload, "ts": now}

        except Exception as e:
            logger.debug("/price fallback: CoinGecko fetch failed: %s", e)

        # Any remaining needed symbols: try Binance REST quickly (short timeout)
        remaining = [s for s in needed if s not in result]
        if remaining:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as client:
                    for sym in remaining:
                        url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
                        try:
                            r = await client.get(url)
                            if r.status_code == 200:
                                data = r.json()
                                payload = {
                                    "symbol": sym,
                                    "last_price": data.get("price"),
                                    "change_pct": None,
                                    "timestamp": now,
                                }
                                result[sym] = payload
                                cache[sym] = {"payload": payload, "ts": now}
                        except Exception:
                            continue
            except Exception:
                pass

        # Return whatever we have (could be partial). Ensure values follow the payload shape.
        return result


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
