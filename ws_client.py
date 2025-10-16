"""Simple WebSocket test client that connects to the local FastAPI /ws endpoint
and prints incoming JSON messages.
"""
import asyncio
import json
import websockets


async def run_client(uri: str = "ws://localhost:8000/ws"):
    async with websockets.connect(uri) as ws:
        print("Connected to", uri)
        try:
            while True:
                msg = await ws.recv()
                try:
                    data = json.loads(msg)
                except Exception:
                    data = msg
                print("Received:", data)
        except Exception as e:
            print("Connection closed or error:", e)


if __name__ == "__main__":
    asyncio.run(run_client())
