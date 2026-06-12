import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app import config, database, scheduler
from app.routes import config_routes, sync_routes, files_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    database.init_db()
    cfg = config.load_config()
    scheduler.start_scheduler(
        hour=cfg.get("schedule_hour", 7),
        minute=cfg.get("schedule_minute", 0),
    )
    yield
    scheduler.stop_scheduler()


app = FastAPI(title="SEFAZ Downloader", lifespan=lifespan)

app.include_router(config_routes.router)
app.include_router(sync_routes.router)
app.include_router(files_routes.router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))
