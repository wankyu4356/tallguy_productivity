from __future__ import annotations

import asyncio

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class SeleniumContext:
    """Wraps a WebDriver instance, mirroring Playwright's BrowserContext interface."""

    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver

    async def close(self):
        await asyncio.to_thread(self.driver.quit)


class BrowserManager:
    def __init__(self):
        self._chrome_options: Options | None = None
        self._started: bool = False

    async def start(self):
        opts = Options()
        if settings.BROWSER_HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--lang=ko-KR")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        self._chrome_options = opts

        # Warm-up: trigger Selenium Manager download on first run
        try:
            driver = await asyncio.to_thread(self._create_driver)
            await asyncio.to_thread(driver.quit)
            logger.info("Browser warm-up complete (Chrome + ChromeDriver ready)")
        except Exception as e:
            logger.warning(f"Browser warm-up failed: {e}")

        self._started = True
        logger.info("BrowserManager started")

    async def stop(self):
        self._started = False
        logger.info("BrowserManager stopped")

    async def new_context(self, headless: bool | None = None) -> SeleniumContext:
        """Create a new browser context.

        Args:
            headless: Override headless setting. None uses config default.
                      False forces visible GUI (for manual login).
        """
        if not self._started:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        driver = await asyncio.to_thread(self._create_driver, headless)
        driver.set_page_load_timeout(settings.NAVIGATION_TIMEOUT_MS / 1000)
        return SeleniumContext(driver)

    def _create_driver(self, headless: bool | None = None) -> webdriver.Chrome:
        if headless is None or headless == settings.BROWSER_HEADLESS:
            return webdriver.Chrome(options=self._chrome_options)
        # Build new options with overridden headless setting
        opts = Options()
        for arg in self._chrome_options.arguments:
            if arg.startswith("--headless"):
                continue
            opts.add_argument(arg)
        if headless:
            opts.add_argument("--headless=new")
        return webdriver.Chrome(options=opts)

    @property
    def is_running(self) -> bool:
        return self._started
