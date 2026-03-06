from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BrowserManager:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.BROWSER_HEADLESS,
        )
        logger.info("Browser launched")

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    async def new_context(self) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")
        ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        ctx.set_default_timeout(settings.NAVIGATION_TIMEOUT_MS)
        return ctx

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()
