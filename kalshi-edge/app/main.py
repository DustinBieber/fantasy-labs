"""FastAPI entry point: serves the dashboard, the JSON API, and starts the scheduler."""
from __future__ import annotations

import base64
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import router
from .services import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = config.ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_engine()
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(title="Kalshi Edge", version="1.0-phase1", lifespan=lifespan)


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """If APP_PASSWORD is set, every page and API call needs it (any username). The AI trader can't go live
    without it, so nobody else on your network can flip it to real money."""
    if config.APP_PASSWORD:
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = hmac.compare_digest(pw.encode(), config.APP_PASSWORD.encode())
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Password required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Kalshi Edge"'})
    return await call_next(request)


app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=config.APP_HOST, port=config.APP_PORT, log_level="info")


if __name__ == "__main__":
    run()
