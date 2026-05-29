from __future__ import annotations

import asyncio
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options

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


def _clean_profile_locks(profile_dir: Path) -> None:
    """Remove stale Singleton lock files left by a previously crashed/closed
    Edge so the persistent profile isn't reported as 'already in use'.

    If a real Edge process still holds the lock, unlink raises and we leave it.
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = profile_dir / name
        try:
            if lock.is_symlink() or lock.exists():
                lock.unlink()
        except OSError:
            pass


class SeleniumContext:
    """Wraps a WebDriver instance."""

    def __init__(self, driver: webdriver.Edge):
        self.driver = driver

    async def close(self):
        await asyncio.to_thread(self.driver.quit)


class BrowserManager:
    def __init__(self):
        self._base_args: list[str] = []
        self._experimental: dict = {}
        self._started: bool = False

    async def start(self):
        self._base_args = [
            "--window-size=1280,900",
            "--lang=ko-KR",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            # Anti-bot detection
            "--disable-blink-features=AutomationControlled",
            (
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
        ]
        self._experimental = {
            "excludeSwitches": ["enable-automation"],
            "useAutomationExtension": False,
        }

        # Warm-up WITHOUT the persistent profile. The warm-up only triggers the
        # Selenium Manager / EdgeDriver download. Using the persistent profile
        # here would leave a lock that blocks the real (profiled) browser from
        # launching with "user data directory is already in use".
        try:
            driver = await asyncio.to_thread(
                self._create_driver, settings.BROWSER_HEADLESS, False
            )
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
                      False forces visible GUI (for manual/auto login).
        """
        if not self._started:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        eff_headless = settings.BROWSER_HEADLESS if headless is None else headless
        driver = await asyncio.to_thread(self._create_driver, eff_headless, True)
        driver.set_page_load_timeout(settings.NAVIGATION_TIMEOUT_MS / 1000)
        return SeleniumContext(driver)

    def _create_driver(self, headless: bool, use_profile: bool) -> webdriver.Edge:
        opts = Options()
        for arg in self._base_args:
            opts.add_argument(arg)
        for key, val in self._experimental.items():
            opts.add_experimental_option(key, val)
        if headless:
            opts.add_argument("--headless=new")

        # Persistent user profile so the user's "허용(Allow)" choice for
        # thebell's security-program launcher survives restarts — clicked once,
        # remembered thereafter. Only real contexts use it (not the warm-up).
        if use_profile:
            profile_dir = settings.BROWSER_PROFILE_DIR.resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            _clean_profile_locks(profile_dir)
            opts.add_argument(f"--user-data-dir={profile_dir}")
            opts.add_argument("--profile-directory=Default")
            opts.add_argument("--no-first-run")
            opts.add_argument("--no-default-browser-check")
            logger.info(f"Edge persistent profile: {profile_dir}")

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
