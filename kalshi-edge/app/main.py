"""FastAPI entry point: serves the dashboard, the JSON API, and starts the scheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
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
