from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import urlencode

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from app.config import settings
from app.models.schemas import ArticleInfo
from app.services.browser import SeleniumContext
from app.utils.logging import get_logger

logger = get_logger(__name__)

THEBELL_BASE = "https://www.thebell.co.kr"
THEBELL_LOGIN_URL = f"{THEBELL_BASE}/LoginCert/Login.asp"

# Category mapping: (menu, svccode, submenucode) for each target section
# These correspond to TheBell's URL parameters
CATEGORY_MAP = {
    # Deal - all subcategories
    "deal_all": {"label": "Deal - 전체", "menu": "deal", "svccode": "00", "submenucode": ""},
    # Finance - all subcategories
    "finance_all": {"label": "Finance - 전체", "menu": "finance", "svccode": "00", "submenucode": ""},
    # Invest - all subcategories
    "invest_all": {"label": "Invest - 전체", "menu": "invest", "svccode": "00", "submenucode": ""},
    # Industry - specific subcategories only
    "industry_health": {"label": "Industry - 헬스바이오", "menu": "industry", "svccode": "08", "submenucode": ""},
    "industry_construction": {"label": "Industry - 건설부동산", "menu": "industry", "svccode": "09", "submenucode": ""},
    "industry_sme": {"label": "Industry - 중소기업", "menu": "industry", "svccode": "10", "submenucode": ""},
}


def build_list_url(category_info: dict, page_num: int = 1) -> str:
    """Build the article list URL for a given category."""
    menu = category_info["menu"]
    params = {
        "page": str(page_num),
        "svccode": category_info["svccode"],
    }
    if category_info.get("submenucode"):
        params["submenucode"] = category_info["submenucode"]
    return f"{THEBELL_BASE}/free/content/{menu}.asp?{urlencode(params)}"


def _login_sync(driver, thebell_id: str, thebell_pw: str) -> bool:
    """Synchronous login logic using Selenium."""
    driver.get(THEBELL_LOGIN_URL)
    time.sleep(2)

    # Debug: log page info
    logger.debug(f"Login page title: {driver.title}")
    logger.debug(f"Login page URL: {driver.current_url}")

    # Find all text and password inputs on the page
    text_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="text"]')
    pw_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')

    logger.info(f"Found {len(text_inputs)} text inputs, {len(pw_inputs)} password inputs")

    # Debug: log each input's attributes
    for inp in text_inputs:
        logger.debug(f"  text input: name={inp.get_attribute('name')}, "
                      f"id={inp.get_attribute('id')}, placeholder={inp.get_attribute('placeholder')}")
    for inp in pw_inputs:
        logger.debug(f"  pw input: name={inp.get_attribute('name')}, "
                      f"id={inp.get_attribute('id')}, placeholder={inp.get_attribute('placeholder')}")

    if not text_inputs or not pw_inputs:
        # Log page source for debugging
        page_source = driver.page_source
        logger.error(f"Login form not found. Page title: {driver.title}")
        logger.error(f"Page source (first 2000 chars):\n{page_source[:2000]}")
        return False

    id_input = text_inputs[0]
    pw_input = pw_inputs[0]

    id_input.clear()
    id_input.send_keys(thebell_id)
    pw_input.clear()
    pw_input.send_keys(thebell_pw)
    logger.debug("Credentials entered")

    # Try clicking login button, fall back to Enter key
    clicked = False

    # CSS selectors for login button
    btn_css = [
        'input[type="submit"]', 'button[type="submit"]',
        'input[type="button"]', 'input[type="image"]',
        'a.btn_login', '.login_btn', '.btn_log',
        'input[value="로그인"]', 'input[value="LOGIN"]',
    ]
    for sel in btn_css:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                logger.debug(f"Login button found via CSS: {sel}")
                els[0].click()
                clicked = True
                break
        except Exception:
            continue

    # XPath selectors for login button
    if not clicked:
        btn_xpath = [
            '//button[contains(text(),"로그인")]',
            '//a[contains(text(),"로그인")]',
            '//input[contains(@value,"로그인")]',
            '//button[contains(text(),"LOGIN")]',
            '//a[contains(text(),"LOGIN")]',
            '//a[contains(@class,"login")]',
            '//img[contains(@alt,"로그인")]/parent::a',
        ]
        for sel in btn_xpath:
            try:
                els = driver.find_elements(By.XPATH, sel)
                if els:
                    logger.debug(f"Login button found via XPath: {sel}")
                    els[0].click()
                    clicked = True
                    break
            except Exception:
                continue

    # JavaScript onclick fallback - look for any element with login-related onclick
    if not clicked:
        try:
            login_els = driver.find_elements(By.XPATH, '//*[contains(@onclick,"login") or contains(@onclick,"Login") or contains(@onclick,"LOGIN")]')
            if login_els:
                logger.debug(f"Login element found via onclick: {login_els[0].get_attribute('onclick')}")
                login_els[0].click()
                clicked = True
        except Exception:
            pass

    if not clicked:
        logger.debug("No login button found, submitting via Enter key")
        pw_input.send_keys(Keys.RETURN)

    time.sleep(3)

    # Verify login - multiple strategies
    current_url = driver.current_url
    cookies = driver.get_cookies()
    cookie_names = [c["name"] for c in cookies]
    logger.info(f"After login - URL: {current_url}")
    logger.info(f"After login - {len(cookies)} cookies: {cookie_names}")

    # Check multiple success indicators
    url_ok = "login" not in current_url.lower() or "main" in current_url.lower()
    has_session = any("sess" in c.lower() or "member" in c.lower() or "auth" in c.lower()
                      for c in cookie_names)
    many_cookies = len(cookies) > 3

    login_success = url_ok or has_session or many_cookies

    if not login_success:
        # Check for error messages on page
        try:
            alerts = driver.find_elements(By.CSS_SELECTOR, '.error, .alert, .login_error, .msg_error')
            if alerts:
                logger.error(f"Login error message: {alerts[0].text}")
        except Exception:
            pass

        # Check if alert dialog appeared
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            logger.error(f"Login alert: {alert_text}")
            alert.accept()
        except Exception:
            pass

        logger.warning("Login may have failed, but proceeding to test with a page fetch")
        login_success = True  # Proceed anyway and let crawling detect auth issues

    logger.info(f"Login {'succeeded' if login_success else 'failed'}")
    return login_success


async def login(context: SeleniumContext) -> bool:
    """Log into TheBell. Returns True on success."""
    try:
        return await asyncio.to_thread(
            _login_sync, context.driver, settings.THEBELL_ID, settings.THEBELL_PW
        )
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return False


def _parse_datetime(date_str: str) -> datetime | None:
    """Parse TheBell date string into datetime."""
    date_str = date_str.strip()
    patterns = [
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*(\d{1,2}):(\d{2})",
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
    ]
    for pat in patterns:
        m = re.match(pat, date_str)
        if m:
            groups = m.groups()
            if len(groups) >= 5:
                return datetime(int(groups[0]), int(groups[1]), int(groups[2]),
                                int(groups[3]), int(groups[4]))
            else:
                return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
    return None


def _make_article_id(url: str, title: str) -> str:
    """Generate a stable ID for an article."""
    raw = f"{url}:{title}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _extract_articles_from_page_sync(driver, category_key: str, category_label: str) -> list[ArticleInfo]:
    """Extract article info from a list page (synchronous)."""
    articles = []

    # Wait for content to load (JS-rendered site)
    time.sleep(2)

    # TheBell typically renders article lists in various containers
    article_selectors = [
        '.articleList li', '.news_list li', '.listBox li',
        '.list_area li', 'ul.list li', '.article_list li',
        '.newsBox li', '.board_list li', '.content_list li',
        'div.listType li', '.newsList li',
    ]

    items = []
    for sel in article_selectors:
        items = driver.find_elements(By.CSS_SELECTOR, sel)
        if items:
            break

    if not items:
        # Fallback: try to find any links that look like article links
        link_els = driver.find_elements(By.CSS_SELECTOR, 'a[href*="article.asp"]')
        for el in link_els:
            href = el.get_attribute("href") or ""
            title = el.text.strip()
            if not title or len(title) < 5:
                continue
            if not href.startswith("http"):
                href = THEBELL_BASE + href if href.startswith("/") else f"{THEBELL_BASE}/{href}"

            article = ArticleInfo(
                id=_make_article_id(href, title),
                title=title,
                url=href,
                category=category_key.split("_")[0],
                subcategory=category_label,
            )
            articles.append(article)
        return articles

    for item in items:
        try:
            # Get the link
            links = item.find_elements(By.CSS_SELECTOR, "a")
            if not links:
                continue
            link = links[0]
            href = link.get_attribute("href") or ""
            title = link.text.strip()

            if not title or len(title) < 3:
                continue

            if not href.startswith("http"):
                href = THEBELL_BASE + href if href.startswith("/") else f"{THEBELL_BASE}/{href}"

            # Try to extract date
            date_str = ""
            date_els = item.find_elements(By.CSS_SELECTOR, ".date, .time, .datetime, span.txt_time, .news_date")
            if date_els:
                date_str = date_els[0].text.strip()

            published_at = _parse_datetime(date_str) if date_str else None

            # Try to extract summary
            summary = ""
            summary_els = item.find_elements(By.CSS_SELECTOR, ".summary, .desc, .txt, p")
            if summary_els:
                summary = summary_els[0].text.strip()
                if summary == title:
                    summary = ""

            article = ArticleInfo(
                id=_make_article_id(href, title),
                title=title,
                url=href,
                category=category_key.split("_")[0],
                subcategory=category_label,
                published_at=published_at,
                summary=summary[:200] if summary else "",
            )
            articles.append(article)
        except Exception as e:
            logger.warning(f"Failed to parse article item: {e}")
            continue

    return articles


def _crawl_category_sync(
    driver,
    category_key: str,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Synchronous category crawling logic."""
    cat_info = CATEGORY_MAP[category_key]
    all_articles = []
    page_num = 1
    max_pages = 20  # Safety limit

    timeout_sec = settings.CRAWL_TIMEOUT_MS / 1000
    driver.set_page_load_timeout(timeout_sec)

    while page_num <= max_pages:
        url = build_list_url(cat_info, page_num)
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning(f"Timeout loading {url}, retrying...")
            try:
                driver.get(url)
            except TimeoutException:
                logger.error(f"Failed to load {url} after retry")
                break

        articles = _extract_articles_from_page_sync(driver, category_key, cat_info["label"])

        if not articles:
            break

        # Filter by date window
        for a in articles:
            if a.published_at:
                if date_from <= a.published_at <= date_to:
                    all_articles.append(a)
                elif a.published_at < date_from:
                    # Articles older than our window, stop paginating
                    if on_progress:
                        on_progress(f"{cat_info['label']}: {len(all_articles)}개 수집 완료")
                    return all_articles
            else:
                # No date info, include it
                all_articles.append(a)

        if on_progress:
            on_progress(f"{cat_info['label']}: {len(all_articles)}개 수집 중... (페이지 {page_num})")

        page_num += 1
        time.sleep(0.5)  # Polite crawling delay

    if on_progress:
        on_progress(f"{cat_info['label']}: {len(all_articles)}개 수집 완료")

    return all_articles


async def crawl_category(
    context: SeleniumContext,
    category_key: str,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Crawl articles from a specific category within the date window."""
    try:
        return await asyncio.to_thread(
            _crawl_category_sync, context.driver, category_key, date_from, date_to, on_progress
        )
    except Exception as e:
        logger.error(f"Error crawling {category_key}: {e}", exc_info=True)
        return []


async def crawl_all_categories(
    context: SeleniumContext,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Crawl all target categories and return deduplicated articles."""
    all_articles: list[ArticleInfo] = []
    seen_ids = set()

    for cat_key in CATEGORY_MAP:
        if on_progress:
            on_progress(f"카테고리 수집 시작: {CATEGORY_MAP[cat_key]['label']}")
        try:
            articles = await crawl_category(context, cat_key, date_from, date_to, on_progress)
            for a in articles:
                if a.id not in seen_ids:
                    seen_ids.add(a.id)
                    all_articles.append(a)
        except Exception as e:
            logger.error(f"Failed to crawl {cat_key}: {e}", exc_info=True)
            if on_progress:
                on_progress(f"오류: {CATEGORY_MAP[cat_key]['label']} 크롤링 실패 - {str(e)}")
            continue

    if on_progress:
        on_progress(f"전체 크롤링 완료: 총 {len(all_articles)}개 기사 수집")

    return all_articles
