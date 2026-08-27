import asyncio
import shutil
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models.schemas import SessionState
from app.routers import health, clipper, setup as setup_router
from app.services.browser import BrowserManager
from app.utils.logging import setup_logging, get_logger
from app.utils.paths import app_dir, resource_path
from app.utils.templating import create_templates

logger = get_logger(__name__)

# In-memory session store
sessions: dict[str, SessionState] = {}

# Browser manager singleton
browser_manager = BrowserManager()


def cleanup_old_sessions():
    """Remove output directories older than CLEANUP_HOURS."""
    if not settings.OUTPUT_DIR.exists():
        return
    cutoff = time.time() - settings.CLEANUP_HOURS * 3600
    for p in settings.OUTPUT_DIR.iterdir():
        if p.is_dir() and p.name != ".gitkeep" and p.stat().st_mtime < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            logger.info(f"Cleaned up old session: {p.name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.LOG_LEVEL)

    # A missing API key is recoverable: the app boots and sends the user to the
    # /setup page instead of dying before the window opens (which, in the
    # packaged executable, would just flash a console and vanish).
    for err in settings.validate_required():
        logger.warning(f"Configuration incomplete: {err} — /setup 페이지로 안내합니다.")

    logger.info(f"작업 폴더: {app_dir()}")
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_sessions()

    await browser_manager.start()
    logger.info("Application started")

    url = f"http://localhost:{settings.PORT}"
    logger.info(f"Opening browser: {url}")
    webbrowser.open(url)

    yield
    await browser_manager.stop()
    logger.info("Application stopped")


app = FastAPI(title="더벨 News Clipper", lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=resource_path("app", "static")),
    name="static",
)
templates = create_templates()

app.include_router(health.router)
app.include_router(setup_router.router)
app.include_router(clipper.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not settings.is_configured:
        return RedirectResponse("/setup", status_code=307)

    from app.services.business_day import get_clipping_window
    date_from, date_to = get_clipping_window()
    return templates.TemplateResponse(request, "index.html", {
        "date_from": date_from,
        "date_to": date_to,
        "date_from_str": date_from.strftime("%Y-%m-%dT%H:%M"),
        "date_to_str": date_to.strftime("%Y-%m-%dT%H:%M"),
        "sessions": sessions,
    })
