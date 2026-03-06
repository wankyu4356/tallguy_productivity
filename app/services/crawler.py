from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models.schemas import ArticleInfo
from app.utils.logging import get_logger

logger = get_logger(__name__)

THEBELL_BASE = "https://www.thebell.co.kr"
THEBELL_LOGIN_URL = f"{THEBELL_BASE}/free/login/loginForm.asp"

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


async def login(context: BrowserContext) -> bool:
    """Log into TheBell. Returns True on success."""
    page = await context.new_page()
    try:
        await page.goto(THEBELL_LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)

        # Try to find and fill login form fields
        # TheBell uses typical ID/PW form fields
        id_selectors = ['input[name="USER_ID"]', 'input[name="user_id"]', '#USER_ID', '#user_id',
                        'input[type="text"][name*="id" i]', 'input[type="text"]']
        pw_selectors = ['input[name="USER_PW"]', 'input[name="user_pw"]', '#USER_PW', '#user_pw',
                        'input[type="password"]']

        id_input = None
        for sel in id_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    id_input = el
                    break
            except Exception:
                continue

        pw_input = None
        for sel in pw_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    pw_input = el
                    break
            except Exception:
                continue

        if not id_input or not pw_input:
            logger.error("Login form fields not found")
            return False

        await id_input.fill(settings.THEBELL_ID)
        await pw_input.fill(settings.THEBELL_PW)

        # Click login button
        login_btn_selectors = ['input[type="submit"]', 'button[type="submit"]',
                               'a.btn_login', '.login_btn', 'button:has-text("로그인")',
                               'input[value="로그인"]', 'a:has-text("로그인")']
        clicked = False
        for sel in login_btn_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Try submitting the form directly
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(2000)
        # Verify login by checking if we're redirected or if login form is gone
        current_url = page.url
        login_success = "login" not in current_url.lower() or "main" in current_url.lower()

        if not login_success:
            # Check for any login error messages
            error_text = await page.locator('.error, .alert, .login_error').text_content() if await page.locator('.error, .alert, .login_error').count() > 0 else ""
            if error_text:
                logger.error(f"Login failed: {error_text}")
            else:
                logger.warning("Login status uncertain, proceeding anyway")
                login_success = True

        logger.info(f"Login {'succeeded' if login_success else 'failed'}")
        return login_success
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return False
    finally:
        await page.close()


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


async def _extract_articles_from_page(page: Page, category_key: str, category_label: str) -> list[ArticleInfo]:
    """Extract article info from a list page."""
    articles = []

    # Wait for content to load (JS-rendered site)
    await page.wait_for_timeout(2000)

    # TheBell typically renders article lists in various containers
    # Try multiple selectors to find article entries
    article_selectors = [
        '.articleList li', '.news_list li', '.listBox li',
        '.list_area li', 'ul.list li', '.article_list li',
        '.newsBox li', '.board_list li', '.content_list li',
        'div.listType li', '.newsList li',
    ]

    items = []
    for sel in article_selectors:
        items = await page.locator(sel).all()
        if items:
            break

    if not items:
        # Fallback: try to find any links that look like article links
        items = await page.locator('a[href*="article.asp"]').all()
        for item in items:
            href = await item.get_attribute("href") or ""
            title = (await item.text_content() or "").strip()
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
            link = item.locator("a").first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href") or ""
            title = (await link.text_content() or "").strip()

            if not title or len(title) < 3:
                continue

            if not href.startswith("http"):
                href = THEBELL_BASE + href if href.startswith("/") else f"{THEBELL_BASE}/{href}"

            # Try to extract date
            date_el = item.locator(".date, .time, .datetime, span.txt_time, .news_date")
            date_str = ""
            if await date_el.count() > 0:
                date_str = (await date_el.first.text_content() or "").strip()

            published_at = _parse_datetime(date_str) if date_str else None

            # Try to extract summary
            summary_el = item.locator(".summary, .desc, .txt, p")
            summary = ""
            if await summary_el.count() > 0:
                summary = (await summary_el.first.text_content() or "").strip()
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


async def crawl_category(
    context: BrowserContext,
    category_key: str,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Crawl articles from a specific category within the date window."""
    cat_info = CATEGORY_MAP[category_key]
    all_articles = []
    page_num = 1
    max_pages = 20  # Safety limit

    page = await context.new_page()
    try:
        while page_num <= max_pages:
            url = build_list_url(cat_info, page_num)
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=settings.CRAWL_TIMEOUT_MS)
            except PlaywrightTimeout:
                logger.warning(f"Timeout loading {url}, retrying...")
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=settings.CRAWL_TIMEOUT_MS)
                except PlaywrightTimeout:
                    logger.error(f"Failed to load {url} after retry")
                    break

            articles = await _extract_articles_from_page(page, category_key, cat_info["label"])

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
                    # No date info, include it (will be filtered later if possible)
                    all_articles.append(a)

            if on_progress:
                on_progress(f"{cat_info['label']}: {len(all_articles)}개 수집 중... (페이지 {page_num})")

            page_num += 1
            await page.wait_for_timeout(500)  # Polite crawling delay

    except Exception as e:
        logger.error(f"Error crawling {cat_info['label']}: {e}", exc_info=True)
    finally:
        await page.close()

    if on_progress:
        on_progress(f"{cat_info['label']}: {len(all_articles)}개 수집 완료")

    return all_articles


async def crawl_all_categories(
    context: BrowserContext,
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
