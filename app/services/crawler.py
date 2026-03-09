from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

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

# Target sections: (display_label, section_code)
# URL pattern: /front/NewsList.asp?Code={code}
# Code mapping: 01xx=Deal, 02xx=Finance, 03xx=Invest, 04xx=Industry
SECTION_CODES = [
    ("Deal",                "0100"),  # Deal 전체
    ("Finance",             "0200"),  # Finance 전체
    ("Invest",              "0300"),  # Invest 전체
    ("Industry - 헬스바이오",  "0406"),
    ("Industry - 건설부동산",  "0407"),
    ("Industry - 중소기업",    "0408"),
]


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
            logger.warning(f"에러 페이지 감지 | url={current_url} | title={driver.title} | match='{indicator}'")
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
    """Check if the user is currently logged in.

    Strategy (in order):
    1. Check cookies for session tokens (most reliable)
    2. Check for visible logout/mypage elements
    3. Check body text for '로그아웃'
    """
    current_url = driver.current_url
    title = driver.title or "(no title)"

    # 1) Cookie-based check — only login-specific cookies, NOT generic session cookies
    # ASPSESSIONID is created for ALL visitors, so it must NOT be used here
    try:
        cookies = driver.get_cookies()
        login_cookie_names = ["theloginid", "thebellid", "loginchk",
                              "LOGINCHK", "LoginCheck", "loginid", "LOGINID"]
        for cookie in cookies:
            name = cookie.get("name", "")
            if any(name.upper() == sc.upper() for sc in login_cookie_names):
                value = cookie.get("value", "")
                if value and value not in ("", "0", "false"):
                    logger.info(f"로그인 확인됨 (쿠키) | cookie={name} | url={current_url}")
                    return True
    except Exception:
        pass

    # 2) Try to detect via JS — check if login-related JS variables exist
    try:
        result = driver.execute_script(
            "return document.querySelector(\"a[href*='LogOut'], a[href*='logout'], "
            "a[href*='Logout'], a[href*='mypage'], a[href*='MyPage']\") !== null;"
        )
        if result:
            logger.info(f"로그인 확인됨 (JS셀렉터) | url={current_url}")
            return True
    except Exception:
        pass

    # 3) CSS selectors for elements that only APPEAR when logged in
    logged_in_selectors = [
        "a[href*='LogOut']",
        "a[href*='logout']",
        "a[href*='Logout']",
        "a[href*='mypage']",
        "a[href*='MyPage']",
        ".logout", "#logout",
        "a.btn_logout",
    ]

    for sel in logged_in_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                # Check existence even if not displayed (some sites hide in dropdown)
                href = el.get_attribute("href") or ""
                text = el.text.strip() or ""
                if href or text:
                    logger.info(f"로그인 확인됨 | url={current_url} | 요소='{sel}' → href='{href}' text='{text}'")
                    return True
        except Exception:
            continue

    # 4) Check body text for '로그아웃' (last resort)
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        visible_text = body.text
        if "로그아웃" in visible_text:
            logger.info(f"로그인 확인됨 | url={current_url} | '로그아웃' 텍스트 표시")
            return True
    except Exception:
        pass

    logger.info(f"로그인 안됨 | url={current_url} | title={title}")
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
    """Open TheBell and wait for user to log in manually.

    IMPORTANT: Do NOT navigate or use driver.get/back during the wait loop.
    The user is controlling the browser — we just poll _check_logged_in.
    """
    # Load login page once to start
    _load_login_page(driver)

    logger.info("브라우저에서 더벨 로그인을 완료하세요 (최대 5분 대기)...")

    last_url = driver.current_url
    start = time.time()
    while time.time() - start < timeout:
        try:
            current_url = driver.current_url

            if current_url != last_url:
                logger.info(f"페이지 이동 감지 | {last_url} → {current_url}")
                last_url = current_url
                time.sleep(2)  # Wait for new page to fully load

            if _check_logged_in(driver):
                logger.info("수동 로그인 성공!")
                return True

        except Exception:
            pass
        time.sleep(3)

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
                                int(groups[3]), int(groups[4]), tzinfo=KST)
            else:
                return datetime(int(groups[0]), int(groups[1]), int(groups[2]),
                                tzinfo=KST)
    return None


def _make_article_id(url: str, title: str) -> str:
    """Generate a stable ID for an article."""
    raw = f"{url}:{title}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]




def _diagnose_page(driver) -> str:
    """Diagnose current page state for troubleshooting."""
    current_url = driver.current_url
    title = driver.title or "(no title)"
    body_text = ""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text[:300]
    except Exception:
        pass

    if any(kw in current_url.lower() for kw in ["login", "logincert", "loginform"]):
        return f"세션 만료 (로그인 리다이렉트) | url={current_url}"

    bot_indicators = ["접근이 차단", "차단", "비정상", "자동화", "bot", "blocked", "denied", "captcha"]
    for ind in bot_indicators:
        if ind in body_text.lower() or ind in title.lower():
            return f"봇 감지/접근 차단 | match='{ind}' | url={current_url}"

    if _is_error_page(driver):
        return f"에러 페이지 | url={current_url} | title={title}"

    if len(body_text.strip()) < 50:
        return f"빈 페이지 | url={current_url} | body_len={len(body_text.strip())}"

    return f"셀렉터 매칭 실패 | url={current_url} | title={title} | body={body_text[:150]}"


def _navigate_to_main(driver):
    """Navigate to TheBell main page."""
    driver.get(THEBELL_BASE)
    time.sleep(2)


def _navigate_to_section(driver, section_code: str) -> bool:
    """Navigate directly to a section page by its code.

    URL pattern: /front/NewsList.asp?Code={section_code}
    """
    url = f"{THEBELL_BASE}/front/NewsList.asp?Code={section_code}"
    logger.info(f"섹션 이동: Code={section_code} | url={url}")
    driver.get(url)
    time.sleep(2)

    if _is_error_page(driver):
        logger.warning(f"섹션 페이지 에러 | Code={section_code} | url={driver.current_url}")
        return False

    current_url = driver.current_url.lower()
    if any(kw in current_url for kw in ["login", "logincert", "loginform"]):
        logger.error(f"세션 만료: 로그인 리다이렉트 | url={driver.current_url}")
        return False

    return True


def _click_next_page(driver) -> bool:
    """Click the next page link in pagination. Returns False if no more pages."""
    # Common pagination patterns
    paging_selectors = [
        ".paging", ".pagination", ".page_num", ".page_nav",
        ".pageNum", "#paging", ".board_paging",
    ]

    # First, try to find a "next" or "다음" button
    for sel in paging_selectors:
        try:
            container = driver.find_elements(By.CSS_SELECTOR, sel)
            if not container:
                continue
            next_links = container[0].find_elements(By.CSS_SELECTOR, "a")
            for link in next_links:
                text = link.text.strip()
                title_attr = (link.get_attribute("title") or "").lower()
                if text in ["다음", "›", "»", ">", "Next"] or "다음" in title_attr or "next" in title_attr:
                    if link.is_displayed():
                        logger.info(f"다음 페이지 클릭: '{text}'")
                        link.click()
                        time.sleep(1.5)
                        return True
        except Exception:
            continue

    # Fallback: find page number links and click the next number
    try:
        # Look for the currently active page number
        active_selectors = [
            ".paging strong", ".paging .on", ".pagination .active",
            ".page_num strong", ".page_num .on",
        ]
        for sel in active_selectors:
            actives = driver.find_elements(By.CSS_SELECTOR, sel)
            if actives:
                current_num = actives[0].text.strip()
                if current_num.isdigit():
                    next_num = str(int(current_num) + 1)
                    # Find a link with the next page number
                    parent = actives[0].find_element(By.XPATH, "./..")
                    sibling_links = parent.find_elements(By.TAG_NAME, "a") if parent else []
                    # Also check the paging container
                    if not sibling_links:
                        paging_container = actives[0].find_element(By.XPATH, "./../..")
                        sibling_links = paging_container.find_elements(By.TAG_NAME, "a")
                    for link in sibling_links:
                        if link.text.strip() == next_num and link.is_displayed():
                            logger.info(f"페이지 {next_num} 클릭")
                            link.click()
                            time.sleep(1.5)
                            return True
                break
    except Exception:
        pass

    logger.info("더 이상 페이지 없음")
    return False


def _crawl_current_page(driver, category_label: str) -> list[ArticleInfo]:
    """Extract articles from the currently loaded page."""
    articles = []
    time.sleep(1)

    # Check for problems first
    current_url = driver.current_url.lower()
    if any(kw in current_url for kw in ["login", "logincert", "loginform"]):
        logger.error(f"세션 만료: 로그인 리다이렉트 | url={driver.current_url}")
        return []

    if _is_error_page(driver):
        logger.warning(f"에러 페이지 | url={driver.current_url} | title={driver.title}")
        return []

    # Find article links — newsview.asp is the TheBell article page
    link_selectors = [
        'a[href*="newsview.asp"]',
        'a[href*="NewsView.asp"]',
        'a[href*="newsView.asp"]',
    ]

    seen_urls = set()
    for sel in link_selectors:
        try:
            link_els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in link_els:
                try:
                    href = el.get_attribute("href") or ""
                    title = el.text.strip()

                    if not title or len(title) < 5:
                        continue
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    if not href.startswith("http"):
                        href = THEBELL_BASE + href if href.startswith("/") else f"{THEBELL_BASE}/{href}"

                    # Try to get date from parent/sibling elements
                    date_str = ""
                    published_at = None
                    try:
                        parent = el.find_element(By.XPATH, "./..")
                        grandparent = parent.find_element(By.XPATH, "./..")
                        container = grandparent
                        date_els = container.find_elements(
                            By.CSS_SELECTOR,
                            ".date, .time, .datetime, span.txt_time, .news_date, .txt_date"
                        )
                        if date_els:
                            date_str = date_els[0].text.strip()
                            published_at = _parse_datetime(date_str) if date_str else None
                    except Exception:
                        pass

                    # Try to get summary
                    summary = ""
                    try:
                        parent = el.find_element(By.XPATH, "./..")
                        summary_els = parent.find_elements(
                            By.CSS_SELECTOR, ".summary, .desc, .txt, p"
                        )
                        if summary_els:
                            summary = summary_els[0].text.strip()
                            if summary == title:
                                summary = ""
                    except Exception:
                        pass

                    article = ArticleInfo(
                        id=_make_article_id(href, title),
                        title=title,
                        url=href,
                        category=category_label.split(" - ")[0] if " - " in category_label else category_label,
                        subcategory=category_label,
                        published_at=published_at,
                        summary=summary[:200] if summary else "",
                    )
                    articles.append(article)
                except Exception:
                    continue
        except Exception:
            continue

    logger.info(f"페이지 기사 수집: {len(articles)}개 | url={driver.current_url}")
    return articles


def _crawl_section_sync(
    driver,
    category_label: str,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Crawl articles from the currently navigated section with pagination."""
    all_articles = []
    page_num = 1
    max_pages = 20

    while page_num <= max_pages:
        articles = _crawl_current_page(driver, category_label)

        if not articles:
            if page_num == 1:
                diag = _diagnose_page(driver)
                logger.warning(f"기사 0개 | {category_label} | {diag}")
                if on_progress:
                    on_progress(f"⚠ {category_label}: {diag}")
            break

        # Filter by date window
        found_old = False
        for a in articles:
            if a.published_at:
                if date_from <= a.published_at <= date_to:
                    all_articles.append(a)
                elif a.published_at < date_from:
                    found_old = True
            else:
                # No date info, include it
                all_articles.append(a)

        if on_progress:
            on_progress(f"{category_label}: {len(all_articles)}개 수집 중... (페이지 {page_num})")

        if found_old:
            break

        # Try to go to next page
        if not _click_next_page(driver):
            break

        page_num += 1

    if on_progress:
        on_progress(f"{category_label}: {len(all_articles)}개 수집 완료")

    return all_articles


async def crawl_all_categories(
    context: SeleniumContext,
    date_from: datetime,
    date_to: datetime,
    on_progress: callable | None = None,
) -> list[ArticleInfo]:
    """Crawl all target categories by navigating directly to section URLs."""

    def _crawl_sync():
        driver = context.driver
        all_articles: list[ArticleInfo] = []
        seen_ids = set()

        for label, code in SECTION_CODES:
            if on_progress:
                on_progress(f"카테고리 수집 시작: {label}")

            if not _navigate_to_section(driver, code):
                if on_progress:
                    on_progress(f"⚠ '{label}' 섹션 이동 실패 (Code={code})")
                continue

            articles = _crawl_section_sync(driver, label, date_from, date_to, on_progress)
            for a in articles:
                if a.id not in seen_ids:
                    seen_ids.add(a.id)
                    all_articles.append(a)

        if len(all_articles) == 0:
            msg = "전체 크롤링 결과 0개 — 로그인 만료, 봇 차단, 또는 사이트 구조 변경 가능성"
            logger.error(msg)
            if on_progress:
                on_progress(f"⚠ {msg}")
        else:
            if on_progress:
                on_progress(f"전체 크롤링 완료: 총 {len(all_articles)}개 기사 수집")

        return all_articles

    try:
        return await asyncio.to_thread(_crawl_sync)
    except Exception as e:
        logger.error(f"크롤링 오류: {e}", exc_info=True)
        return []
