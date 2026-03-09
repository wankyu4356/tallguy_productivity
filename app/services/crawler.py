from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import urlencode

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementNotInteractableException,
)

from app.config import settings
from app.models.schemas import ArticleInfo
from app.services.browser import SeleniumContext
from app.utils.logging import get_logger

logger = get_logger(__name__)

THEBELL_BASE = "https://www.thebell.co.kr"
THEBELL_LOGIN_URL = f"{THEBELL_BASE}/LoginCert/Login.asp"
THEBELL_LOGIN_PROC_URL = f"{THEBELL_BASE}/LoginCert/LoginProc.asp"

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

# Login URL candidates ordered by likelihood (based on MHTML analysis)
_LOGIN_URLS = [
    f"{THEBELL_BASE}/LoginCert/Login.asp",
    f"{THEBELL_BASE}/front/member/login.asp",
    f"{THEBELL_BASE}/free/login/loginForm.asp",
]



def _is_error_page(driver) -> bool:
    """Check whether the current page is a 404 or error page."""
    page_text = driver.page_source[:3000].lower()
    current_url = driver.current_url.lower()

    title = driver.title.lower() if driver.title else ""

    error_indicators = [
        "찾을 수 없습니다",
        "찾을 수가 없습니다",
        "페이지를 찾을 수",
        "존재하지 않는 페이지",
        "요청하신 페이지",
        "404",
        "not found",
        "page not found",
        "error page",
    ]

    for indicator in error_indicators:
        if indicator in page_text or indicator in title or indicator in current_url:
            return True

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body_text = body.text.strip()
        if len(body_text) < 20:
            return True
    except Exception:
        pass

    return False


def _check_logged_in(driver) -> bool:
    """Check if the user is currently logged in by page indicators.

    NOTE: ASP session cookies (ASPSESSIONID) are set on ANY page visit,
    so we cannot rely on cookies alone. Instead, check for UI elements
    that only appear after successful login.
    """
    try:
        page_source = driver.page_source

        # Positive indicators: elements visible only when logged in
        logged_in_indicators = [
            "로그아웃",
            "logout",
            "Logout",
            "mypage",
            "마이페이지",
            "LogOut.asp",
        ]
        for indicator in logged_in_indicators:
            if indicator in page_source:
                logger.info(f"로그인 감지: '{indicator}' 발견")
                return True

        # Negative indicators: still on login page
        login_page_indicators = [
            "login_form",
            "LoginProc",
            "로그인",
        ]
        # If login form is present, definitely not logged in
        if any(ind in page_source for ind in login_page_indicators[:2]):
            return False

    except Exception:
        pass

    return False


def _find_login_form_and_fill(driver, user_id: str, password: str) -> bool:
    """Find login form fields, fill them, and submit.

    Returns True if the form was found and submitted successfully.
    Based on MHTML analysis:
    - Main form: id="login_form", fields: input#id, input#pw
    - Modal form: id="modal_login_form", fields: input#id, input#pw
    - Submit button: a#btn1.btn_login (JS event, not form submit)
    """
    # Selectors to try for the ID field (ordered by specificity)
    id_selectors = [
        "form#login_form input#id",
        "form#login_form input[name='id']",
        "input#id[type='text']",
        "input[name='id'][type='text']",
        # Modal form fallback
        "form#modal_login_form input#id",
        "form#modal_login_form input[name='id']",
    ]

    pw_selectors = [
        "form#login_form input#pw",
        "form#login_form input[name='pw']",
        "input#pw[type='password']",
        "input[name='pw'][type='password']",
        "form#modal_login_form input#pw",
        "form#modal_login_form input[name='pw']",
    ]

    submit_selectors = [
        "a#btn1",
        "a.btn_login",
        "form#login_form a.btn_login",
        "button[type='submit']",
        "input[type='submit']",
    ]

    id_field = None
    pw_field = None

    # Find ID field
    for sel in id_selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                id_field = el
                logger.debug(f"ID 필드 발견: {sel}")
                break
        except (NoSuchElementException, ElementNotInteractableException):
            continue

    # Find PW field
    for sel in pw_selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                pw_field = el
                logger.debug(f"PW 필드 발견: {sel}")
                break
        except (NoSuchElementException, ElementNotInteractableException):
            continue

    if not id_field or not pw_field:
        logger.warning("로그인 폼 필드를 찾을 수 없습니다.")
        return False

    try:
        # Clear and fill ID
        id_field.clear()
        id_field.send_keys(user_id)
        time.sleep(0.3)

        # Clear and fill PW
        pw_field.clear()
        pw_field.send_keys(password)
        time.sleep(0.3)

        # Try to click the login button
        for sel in submit_selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed():
                    btn.click()
                    logger.info(f"로그인 버튼 클릭: {sel}")
                    return True
            except (NoSuchElementException, ElementNotInteractableException):
                continue

        # Fallback: submit the form via JS
        logger.info("로그인 버튼을 못 찾아 JS로 폼 제출 시도")
        try:
            driver.execute_script(
                "document.getElementById('login_form').submit();"
            )
            return True
        except Exception:
            pass

        # Last resort: press Enter in the password field
        from selenium.webdriver.common.keys import Keys
        pw_field.send_keys(Keys.RETURN)
        logger.info("PW 필드에서 Enter 키로 로그인 시도")
        return True

    except Exception as e:
        logger.warning(f"로그인 폼 입력 중 오류: {e}")
        return False


def _load_login_page(driver) -> bool:
    """Try login URLs until one loads successfully. Returns True if loaded."""
    for url in _LOGIN_URLS:
        logger.debug(f"로그인 URL 시도: {url}")
        try:
            driver.get(url)
        except TimeoutException:
            logger.debug(f"로그인 URL 타임아웃: {url}")
            continue

        time.sleep(3)

        if _is_error_page(driver):
            logger.debug(f"로그인 URL 에러: {url}")
            time.sleep(2)
            if _is_error_page(driver):
                continue

        logger.info(f"로그인 페이지 로드 성공: {url}")
        return True

    # Fallback: open main page
    logger.warning("모든 로그인 URL 실패. 메인 페이지를 엽니다.")
    driver.get(THEBELL_BASE)
    time.sleep(2)
    return False


def _auto_login_sync(driver) -> bool:
    """Attempt automatic login using configured credentials.

    Strategy:
    1. Load login page
    2. Fill in ID/PW from config
    3. Click login button
    4. Verify login success via cookies / page state
    """
    user_id = settings.THEBELL_ID
    password = settings.THEBELL_PW

    if not user_id or not password:
        logger.info("자동 로그인 자격증명 미설정 — 수동 로그인으로 전환")
        return False

    _load_login_page(driver)

    # Wait for login form to be ready
    time.sleep(2)

    # Attempt to fill and submit login form
    form_submitted = _find_login_form_and_fill(driver, user_id, password)
    if not form_submitted:
        logger.warning("자동 로그인 폼 제출 실패 — 수동 로그인으로 전환")
        return False

    # Wait for login to process
    time.sleep(3)

    # Check for security module blocking (보안 프로그램 설치 요구)
    try:
        page_source = driver.page_source
        security_indicators = ["보안프로그램", "보안 프로그램", "install.asp", "CERTTEXT"]
        if any(ind in page_source for ind in security_indicators):
            logger.warning("보안 프로그램 설치 요구 감지 — 수동 로그인으로 전환")
            return False
    except Exception:
        pass

    # Check for login error messages
    try:
        page_source = driver.page_source
        error_indicators = ["아이디 또는 비밀번호", "로그인 실패", "입력해 주세요", "확인해 주세요"]
        if any(ind in page_source for ind in error_indicators):
            logger.warning("로그인 실패 메시지 감지 — 자격증명을 확인하세요")
            return False
    except Exception:
        pass

    # Verify login success — ONLY trust page UI indicators, not URL changes
    # Navigate to main page to check for logout button
    driver.get(THEBELL_BASE)
    time.sleep(2)

    if _check_logged_in(driver):
        logger.info("자동 로그인 성공!")
        return True

    logger.warning("자동 로그인 결과 불확실 — 수동 로그인으로 전환")
    return False


def _manual_login_sync(driver, timeout: int = LOGIN_TIMEOUT) -> bool:
    """Open TheBell and wait for user to log in manually."""
    # Always reload login page for manual login attempt
    _load_login_page(driver)

    logger.info("브라우저에서 더벨 로그인을 완료하세요 (최대 5분 대기)...")

    start = time.time()
    while time.time() - start < timeout:
        try:
            # ONLY trust UI indicator check — not URL changes
            if _check_logged_in(driver):
                logger.info("수동 로그인 성공!")
                return True
        except Exception:
            pass
        time.sleep(2)

    logger.error("로그인 타임아웃 (5분)")
    return False


def _login_sync(driver) -> bool:
    """Combined login: try auto-login first, then fall back to manual.

    Flow:
    1. Try auto-login with configured THEBELL_ID/PW
    2. If auto-login fails, open browser for manual login
    """
    # Step 1: Try auto-login
    try:
        if _auto_login_sync(driver):
            return True
    except Exception as e:
        logger.warning(f"자동 로그인 중 예외 발생: {e}")

    # Step 2: Fall back to manual login
    logger.info("수동 로그인 모드로 전환합니다.")
    return _manual_login_sync(driver)


async def login(context: SeleniumContext) -> bool:
    """Login to TheBell. Tries auto-login first, then manual. Returns True on success."""
    try:
        return await asyncio.to_thread(_login_sync, context.driver)
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
