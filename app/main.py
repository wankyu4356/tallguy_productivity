import asyncio
import shutil
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import SessionState
from app.routers import health, clipper
from app.services.browser import BrowserManager
from app.utils.logging import setup_logging, get_logger

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

    errors = settings.validate_required()
    if errors:
        for err in errors:
            logger.error(f"Configuration error: {err}")
        raise SystemExit(
            "필수 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.\n"
            "Run 'python preflight.py' for detailed diagnostics."
        )

    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_sessions()

    await browser_manager.start()
    logger.info("Application started")

    yield
    await browser_manager.stop()
    logger.info("Application stopped")


app = FastAPI(title="딜사이트플러스 News Clipper", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# Python 3.14: Jinja2 LRUCache creates cache_key = (name, globals_dict) which is
# unhashable. This no-op cache bypasses the issue entirely.
class _NoOpCache:
    def get(self, key, default=None):
        return default
    def __setitem__(self, key, value):
        pass
    def __contains__(self, key):
        return False
    def clear(self):
        pass

templates.env.cache = _NoOpCache()  # type: ignore[assignment]

app.include_router(health.router)
app.include_router(clipper.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from app.services.business_day import get_clipping_window
    date_from, date_to = get_clipping_window()
    return templates.TemplateResponse(request, "index.html", {
        "date_from": date_from,
        "date_to": date_to,
        "date_from_str": date_from.strftime("%Y-%m-%dT%H:%M"),
        "date_to_str": date_to.strftime("%Y-%m-%dT%H:%M"),
        "sessions": sessions,
    })
