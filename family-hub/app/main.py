import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import auth, calendar, chores, finances, home, hub, members, push, records
from .services import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler.run_forever()) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Family Hub", version="0.1.0", lifespan=lifespan)
for r in (auth, members, chores, calendar, records, finances, home, hub, push):
    app.include_router(r.router)


@app.get("/api/healthz", include_in_schema=False)
def healthcheck():
    return {"ok": True}


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Served from the root so its scope covers the whole app.
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
