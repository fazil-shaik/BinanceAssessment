import asyncio
from app.server import create_app


# default app instance for ASGI servers (uvicorn can import this)
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)