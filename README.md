# Binance to WebSocket Relay

This small project listens to Binance's public WebSocket for live ticker updates and relays them to connected local WebSocket clients.

Files added/modified:

- `main.py` - FastAPI application with background Binance listener(s), a `/ws` WebSocket endpoint for clients, and a `/price` REST endpoint returning the latest cached prices.
- `requirements_clean.txt` - A clean list of dependencies to install (use this file). The original `requirements.txt` had malformed content and was left untouched.

Install dependencies (recommended inside a virtualenv):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_clean.txt
```

Run the app using uvicorn (recommended via the virtualenv's python to avoid system Python conflicts):

```bash
# from project root, after activating your venv
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

If pip failed installing `websockets==11.2.3`, I pinned `websockets==11.0.3` in `requirements_clean.txt` which is available on PyPI.

Try it:

- Open `http://localhost:8000` to see a welcome message.
- Connect a WebSocket client to `ws://localhost:8000/ws` to receive live updates.
- Visit `http://localhost:8000/price` to get the latest cached prices as JSON.

Using the included test client:

```bash
# run this in another terminal while the app is running
python ws_client.py
```

Docker (optional):

Build and run with docker-compose:

```bash
docker compose up --build
```

Or build + run with docker:

```bash
docker build -t binance-relay .
docker run -p 8000:8000 binance-relay
```

Notes:

- By default the app starts listeners for `BTCUSDT` and `ETHUSDT`. You can modify `main.py` to add more symbols.
- The project uses an asyncio `Queue` to buffer incoming Binance messages and broadcasts them to all connected clients.
