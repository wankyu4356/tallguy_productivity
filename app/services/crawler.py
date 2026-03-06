from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import urlencode

from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

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


LOGIN_TIMEOUT = 300  # 5 minutes max wait for manual login


def _manual_login_sync(driver, timeout: int = LOGIN_TIMEOUT) -> bool:
    """Open TheBell login page and wait for user to log in manually."""
    driver.get(THEBELL_LOGIN_URL)
    logger.info("브라우저에서 더벨 로그인을 완료하세요 (최대 5분 대기)...")

    start = time.time()
    while time.time() - start < timeout:
        try:
            current_url = driver.current_url
            # Left the login page → success
            if "Login.asp" not in current_url and "login" not in current_url.lower():
                logger.info(f"로그인 감지! URL: {current_url}")
                return True
            # Session cookie appeared → success
            cookies = driver.get_cookies()
            cookie_names = [c["name"] for c in cookies]
            if any("sess" in c.lower() or "member" in c.lower() or "auth" in c.lower()
                    for c in cookie_names):
                logger.info(f"세션 쿠키로 로그인 감지! cookies: {cookie_names}")
                return True
        except Exception:
            pass  # browser may be navigating
        time.sleep(1)

    logger.error("로그인 타임아웃 (5분)")
    return False


async def login(context: SeleniumContext) -> bool:
    """Open TheBell login page for manual login. Returns True on success."""
    try:
        return await asyncio.to_thread(_manual_login_sync, context.driver)
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
