from __future__ import annotations

import asyncio

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service

from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

# JavaScript to remove webdriver fingerprint after page load
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.navigator.chrome = {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


class SeleniumContext:
    """Wraps a WebDriver instance."""

    def __init__(self, driver: webdriver.Edge):
        self.driver = driver

    async def close(self):
        await asyncio.to_thread(self.driver.quit)


class BrowserManager:
    def __init__(self):
        self._edge_options: Options | None = None
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

        # Anti-bot detection
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        )

        self._edge_options = opts

        # Warm-up: trigger Selenium Manager download on first run
        try:
            driver = await asyncio.to_thread(self._create_driver)
            await asyncio.to_thread(driver.quit)
            logger.info("Browser warm-up complete (Edge + EdgeDriver ready)")
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

    def _create_driver(self, headless: bool | None = None) -> webdriver.Edge:
        if headless is None or headless == settings.BROWSER_HEADLESS:
            opts = self._edge_options
        else:
            # Build new options with overridden headless setting
            opts = Options()
            for arg in self._edge_options.arguments:
                if arg.startswith("--headless"):
                    continue
                opts.add_argument(arg)
            # Copy experimental options
            for key, val in self._edge_options.experimental_options.items():
                opts.add_experimental_option(key, val)
            if headless:
                opts.add_argument("--headless=new")

        driver = webdriver.Edge(options=opts)

        # Inject stealth script to hide webdriver fingerprint
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": _STEALTH_JS},
            )
        except Exception as e:
            logger.debug(f"CDP stealth injection skipped: {e}")

        return driver

    @property
    def is_running(self) -> bool:
        return self._started
