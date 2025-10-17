from main import app

# Vercel's Python runtime will look for an ASGI callable. Export both common names.
application = app
# also keep `app` symbol available directly

