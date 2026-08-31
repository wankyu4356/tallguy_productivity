from __future__ import annotations

import asyncio
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options

# selenium >= 4.28 resolves `webdriver.Edge` lazily through importlib, so a
# frozen build's static analysis never sees the concrete module and the exe
# dies with "No module named 'selenium.webdriver.edge.webdriver'". Importing
# it by name here makes the dependency visible to PyInstaller. Do not remove.
from selenium.webdriver.edge.webdriver import WebDriver as _EdgeWebDriver  # noqa: F401
from selenium.webdriver.edge.service import Service as _EdgeService  # noqa: F401

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

# thebell's security program (thebellCertSetup.exe) runs a service on the
# machine, and the site talks to it to certify the device. Reaching a local
# address from a public page needs the browser's Local Network Access
# permission, which is the 차단/허용 bar that drops under the address bar:
#
#   www.thebell.co.kr이(가) 하려고 합니다.
#   로컬 네트워크의 모든 장치를 찾아서 연결합니다.   [차단] [허용]
#
# That bar is browser chrome, not page content — Selenium can neither see nor
# click it — and while it goes unanswered the login fails with thebell's own
# "로그인에 문제가 발생했습니다 / 권한설정 문제" dialog. So the permission is
# granted before the page can ask.
THEBELL_ORIGINS = (
    "https://www.thebell.co.kr",
    "https://thebell.co.kr",
)

# `localNetworkAccess` is the permission behind that bar (verified against
# Edge: without it navigator.permissions reports "prompt", with it "granted",
# and the grant also overrides a 차단 the user clicked earlier). notifications
# rides along because it is the other prompt a news site commonly raises and
# this grant is scoped to thebell's origins only.
_GRANTED_PERMISSIONS = [
    "localNetworkAccess",
    "notifications",
]

# Records what the page actually asked for, so the log can say which permission
# was in play if the flow ever changes.
_PERMISSION_PROBE_JS = """
(function () {
  window.__permAsks = [];
  try {
    var orig = Notification && Notification.requestPermission;
    if (orig) {
      Notification.requestPermission = function () {
        window.__permAsks.push('notifications');
        return orig.apply(Notification, arguments);
      };
    }
  } catch (e) {}
  try {
    var q = navigator.permissions && navigator.permissions.query;
    if (q) {
      navigator.permissions.query = function (d) {
        try { window.__permAsks.push('query:' + (d && d.name)); } catch (e) {}
        return q.apply(navigator.permissions, arguments);
      };
    }
  } catch (e) {}
})();
"""


def grant_thebell_permissions(driver) -> bool:
    """Pre-approve thebell's permission prompt for this browser.

    Returns True if at least one origin was granted. Failure is not fatal —
    the user can still click 허용 themselves — so this only warns.
    """
    granted = False
    for origin in THEBELL_ORIGINS:
        try:
            driver.execute_cdp_cmd(
                "Browser.grantPermissions",
                {"origin": origin, "permissions": _GRANTED_PERMISSIONS},
            )
            granted = True
        except Exception as e:
            logger.debug(f"권한 사전 허용 실패 | origin={origin} | {type(e).__name__}: {e}")
    if granted:
        logger.info(f"더벨 권한 사전 허용 완료 | {', '.join(_GRANTED_PERMISSIONS)}")
    else:
        logger.warning(
            "권한 사전 허용에 실패했습니다 — 브라우저에 차단/허용 창이 뜨면 [허용]을 눌러 주세요."
        )
    return granted


def permission_requests(driver) -> list[str]:
    """What the current page asked permission for, if anything."""
    try:
        return driver.execute_script("return window.__permAsks || [];") or []
    except Exception:
        return []


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
            # Fallback for the CDP grant below, verified to work on its own.
            # Unlike the CDP grant this is a profile-wide default rather than
            # per-origin — acceptable because this profile exists only to drive
            # thebell, and the alternative is a 차단/허용 bar parked in front of
            # a login that no code can click past. 1 = allow, 2 = block, 0 = ask.
            # The key is snake_case; the camelCase spelling silently does
            # nothing (it leaves the permission at "prompt").
            "prefs": {
                "profile.default_content_setting_values.local_network_access": 1,
            },
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
        opts.page_load_strategy = "eager"
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

        # Answer thebell's device-permission prompt before it can be asked.
        if use_profile:
            grant_thebell_permissions(driver)
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": _PERMISSION_PROBE_JS},
                )
            except Exception as e:
                logger.debug(f"권한 프로브 주입 생략: {e}")

        return driver

    @property
    def is_running(self) -> bool:
        return self._started
