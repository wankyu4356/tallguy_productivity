from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException, WebDriverException

from app.config import settings
from app.models.schemas import ArticleInfo, ArticleWithContent
from app.services.browser import SeleniumContext
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Chrome DevTools Protocol parameters for A4 PDF
PDF_PARAMS = {
    "landscape": False,
    "printBackground": True,
    "paperWidth": 8.27,      # A4 width in inches
    "paperHeight": 11.69,    # A4 height in inches
    "marginTop": 0.787,      # 20mm
    "marginBottom": 0.787,   # 20mm
    "marginLeft": 0.591,     # 15mm
    "marginRight": 0.591,    # 15mm
}


def sanitize_filename(title: str) -> str:
    """Sanitize article title for use as filename."""
    name = re.sub(r'[<>:"/\\|?*]', '', title)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > 150:
        name = name[:150]
    return name


_BLOCK_PRINT_SCRIPT = "window.print = function() { /* blocked by crawler */ };"

# Track the CDP script identifier so we can remove it later
_cdp_script_id: str | None = None


def _block_print_dialog(driver):
    """Inject script via CDP to block window.print() on all future page loads."""
    global _cdp_script_id
    try:
        result = driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _BLOCK_PRINT_SCRIPT},
        )
        _cdp_script_id = result.get("identifier")
    except Exception:
        pass


def _unblock_print_dialog(driver):
    """Remove the injected print-blocking script."""
    global _cdp_script_id
    if _cdp_script_id:
        try:
            driver.execute_cdp_cmd(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": _cdp_script_id},
            )
        except Exception:
            pass
        _cdp_script_id = None


def _navigate_to_print_page(driver, article_url: str) -> bool:
    """Try to navigate to the print-friendly version of the article.

    Strategy:
    1. Try URL manipulation first (safest — no side effects)
    2. Click print button on the page (handles JS popups)
    3. Return False if no print page found
    """
    original_window = driver.current_window_handle

    # Strategy 1: URL manipulation for TheBell (safest — try first)
    url_replacements = [
        ("newsview.asp", "NewsPrint.asp"),
        ("NewsView.asp", "NewsPrint.asp"),
        ("newsView.asp", "NewsPrint.asp"),
        ("ArticleView.asp", "ArticlePrint.asp"),
    ]
    for old, new in url_replacements:
        if old.lower() in article_url.lower():
            print_url = article_url.replace(old, new)
            # case-insensitive replacement fallback
            if print_url == article_url:
                import re as _re
                print_url = _re.sub(_re.escape(old), new, article_url, flags=_re.IGNORECASE)
            try:
                # Block window.print() BEFORE navigating — print pages auto-call it
                # which opens edge://print/ and blocks for ~22 seconds
                _block_print_dialog(driver)
                driver.get(print_url)
                time.sleep(0.5)
                # Clean up: remove the injected script so it doesn't affect other pages
                _unblock_print_dialog(driver)
                if not _is_error_page_simple(driver):
                    return True
                # Error page — go back to article
                driver.get(article_url)
                time.sleep(0.5)
            except Exception:
                _unblock_print_dialog(driver)
                driver.get(article_url)
                time.sleep(0.5)
            # URL replacement tried — skip button clicking for known sites
            return False

    # Strategy 2: Find and click print button (only for non-TheBell sites)
    # Override window.print() to prevent native print dialog
    try:
        driver.execute_script("window.print = function() {};")
    except Exception:
        pass

    # Use a single fast CSS query to find any print-related element
    _PRINT_CSS = (
        '.btn_print, #btn_print, a.print, '
        'a[href*="print" i], a[onclick*="print" i], '
        'button[onclick*="print" i]'
    )
    try:
        els = driver.find_elements(By.CSS_SELECTOR, _PRINT_CSS)
        if not els:
            # Fallback: XPath for Korean text
            for xp in [
                '//a[contains(text(),"프린트")]',
                '//a[contains(text(),"인쇄")]',
            ]:
                els = driver.find_elements(By.XPATH, xp)
                if els:
                    break
        if not els:
            return False

        el = els[0]
        el.click()
        time.sleep(0.5)

        # Check if a new window/tab was opened (JS popup)
        all_windows = driver.window_handles
        if len(all_windows) > 1:
            new_window = [w for w in all_windows if w != original_window][0]
            driver.switch_to.window(new_window)
            time.sleep(0.3)

            try:
                current_url = driver.current_url
            except Exception:
                _close_extra_windows(driver, original_window)
                return False

            if current_url.startswith(("edge://", "chrome://", "about:")):
                logger.warning(f"브라우저 내부 페이지 감지, 닫기 | url={current_url}")
                _close_extra_windows(driver, original_window)
                return False

            return True

        if "print" in driver.current_url.lower():
            return True

    except Exception:
        pass

    return False


def _isolate_article_for_print(driver, article_title: str) -> bool:
    """Hide everything except the article content for clean PDF output.

    Injects CSS/JS to hide headers, sidebars, ads, footers, etc.
    Returns True if article content was found and isolated.
    """
    # CSS selectors for article content (same as content extraction)
    content_selectors = [
        '.article_content', '.articleContent', '.news_content',
        '.view_content', '.article_body', '.newsContent',
        '#article_content', '#newsContent', '.content_area',
        '.view_area', '.article_view', 'article',
    ]

    js_isolate = """
    (function(title, selectors) {
        // Find the article content element
        var contentEl = null;
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (el && el.innerText.trim().length > 100) {
                contentEl = el;
                break;
            }
        }
        if (!contentEl) return false;

        // Get the article HTML
        var articleHTML = contentEl.innerHTML;

        // Build a clean page
        document.head.innerHTML = '<meta charset="utf-8">' +
            '<style>' +
            'body { font-family: "Malgun Gothic", "맑은 고딕", sans-serif; ' +
            '       max-width: 700px; margin: 40px auto; padding: 0 20px; ' +
            '       font-size: 14px; line-height: 1.8; color: #222; }' +
            'h1 { font-size: 20px; line-height: 1.4; margin-bottom: 24px; ' +
            '     border-bottom: 2px solid #333; padding-bottom: 12px; }' +
            '.article-body { font-size: 14px; line-height: 1.8; }' +
            '.article-body img { max-width: 100%; height: auto; margin: 12px 0; }' +
            '.article-body table { width: 100%; border-collapse: collapse; margin: 12px 0; }' +
            '.article-body td, .article-body th { border: 1px solid #ddd; padding: 6px 8px; font-size: 13px; }' +
            '@media print { body { margin: 0; padding: 0; } }' +
            '</style>';
        document.body.innerHTML =
            '<h1>' + title.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</h1>' +
            '<div class="article-body">' + articleHTML + '</div>';

        return true;
    })(arguments[0], arguments[1]);
    """

    try:
        result = driver.execute_script(js_isolate, article_title, content_selectors)
        if result:
            logger.debug(f"기사 본문 분리 성공 | article={article_title[:40]}")
            return True
        else:
            logger.warning(f"기사 본문 요소 못찾음, 전체 페이지 PDF 생성 | article={article_title[:40]}")
            return False
    except Exception as e:
        logger.warning(f"기사 본문 분리 실패 | article={article_title[:40]} | error={e}")
        return False


def _is_error_page_simple(driver) -> bool:
    """Quick check if the current page is an error page."""
    try:
        title = driver.title.lower()
        if any(kw in title for kw in ["error", "404", "오류", "not found"]):
            return True
        body = driver.find_elements(By.CSS_SELECTOR, "body")
        if body and len(body[0].text.strip()) < 50:
            return True
    except Exception:
        pass
    return False


def _log_browser_state(driver, context: str):
    """Log current browser state for troubleshooting."""
    try:
        url = driver.current_url
        title = driver.title[:60]
        windows = len(driver.window_handles)
        logger.error(f"[브라우저 상태] {context} | url={url} | title={title} | windows={windows}")
    except Exception:
        logger.error(f"[브라우저 상태] {context} | 브라우저 응답 불가 (세션 사망 가능)")


def _close_extra_windows(driver, keep_window: str):
    """Close all windows except the one to keep."""
    for w in driver.window_handles:
        if w != keep_window:
            try:
                driver.switch_to.window(w)
                driver.close()
            except Exception:
                pass
    driver.switch_to.window(keep_window)


def _fetch_article_sync(driver, article: ArticleInfo, output_dir: Path) -> ArticleWithContent:
    """Fetch a single article: extract content and save as PDF (synchronous)."""
    result = ArticleWithContent(info=article)
    original_window = driver.current_window_handle

    # Clean up stale popup windows from previous articles
    if len(driver.window_handles) > 1:
        _close_extra_windows(driver, original_window)

    try:
        driver.set_page_load_timeout(settings.CRAWL_TIMEOUT_MS / 1000)
        # Block window.print() before any page navigation to prevent
        # edge://print/ popup that blocks for ~22 seconds
        _block_print_dialog(driver)
        driver.get(article.url)
        time.sleep(0.5)
        _unblock_print_dialog(driver)

        # Extract article content
        content_selectors = [
            '.article_content', '.articleContent', '.news_content',
            '.view_content', '.article_body', '.newsContent',
            '#article_content', '#newsContent', '.content_area',
            '.view_area', '.article_view', 'article',
        ]

        content = ""
        for sel in content_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                content = els[0].text.strip()
                if content:
                    break

        if not content:
            body_els = driver.find_elements(By.CSS_SELECTOR, "body")
            if body_els:
                content = body_els[0].text.strip()[:3000]

        result.content = content[:5000]

        # Generate PDF — try print-friendly page first, then isolate content
        filename = sanitize_filename(article.title) + ".pdf"
        pdf_path = output_dir / filename

        used_print_page = _navigate_to_print_page(driver, article.url)
        if used_print_page:
            logger.debug(f"프린트 페이지에서 PDF 생성 | url={driver.current_url} | article={article.title[:40]}")

        # Isolate article content: strip headers, sidebars, ads, etc.
        _isolate_article_for_print(driver, article.title)
        time.sleep(0.3)

        # Generate PDF using Chrome DevTools Protocol
        logger.debug(f"CDP printToPDF 호출 | url={driver.current_url} | windows={len(driver.window_handles)}")
        pdf_result = driver.execute_cdp_cmd("Page.printToPDF", PDF_PARAMS)
        pdf_data = base64.b64decode(pdf_result["data"])
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)

        result.pdf_path = str(pdf_path)
        logger.info(f"Saved PDF: {filename}")

        # Clean up: close popup windows and return to original window
        _close_extra_windows(driver, original_window)

    except InvalidSessionIdException as e:
        # Browser session is dead — cannot continue fetching any articles
        _log_browser_state(driver, "InvalidSessionId")
        logger.error(
            f"브라우저 세션 사망 | article={article.title[:50]} | url={article.url} | {e}",
            exc_info=True,
        )
        raise
    except TimeoutException as e:
        _log_browser_state(driver, "Timeout")
        logger.error(
            f"페이지 로드 타임아웃 | article={article.title[:50]} | "
            f"url={article.url} | timeout={settings.CRAWL_TIMEOUT_MS}ms",
            exc_info=True,
        )
        _close_extra_windows(driver, original_window)
    except WebDriverException as e:
        err_msg = str(e).lower()
        is_session_dead = (
            "invalid session" in err_msg
            or "disconnected" in err_msg
            or "session deleted" in err_msg
        )
        _log_browser_state(driver, "SessionDeath" if is_session_dead else "WebDriverError")
        logger.error(
            f"WebDriver 오류 | article={article.title[:50]} | url={article.url} | "
            f"session_dead={is_session_dead}",
            exc_info=True,
        )
        if is_session_dead:
            raise
        _close_extra_windows(driver, original_window)
    except Exception as e:
        logger.error(
            f"기사 수집 오류 | article={article.title[:50]} | url={article.url} | "
            f"type={type(e).__name__}",
            exc_info=True,
        )
        _close_extra_windows(driver, original_window)

    return result


def _fetch_articles_sync(
    driver,
    articles: list[ArticleInfo],
    output_dir: Path,
    on_progress: callable | None = None,
) -> list[ArticleWithContent]:
    """Fetch multiple articles sequentially (synchronous)."""
    results = []
    pdf_ok = 0
    pdf_fail = 0
    start_time = time.time()
    for i, article in enumerate(articles):
        if on_progress:
            on_progress(f"기사 수집 중: {i + 1}/{len(articles)} - {article.title[:30]}...")
        try:
            result = _fetch_article_sync(driver, article, output_dir)
            results.append(result)
            if result.pdf_path:
                pdf_ok += 1
            else:
                pdf_fail += 1
        except (InvalidSessionIdException, WebDriverException) as e:
            elapsed = time.time() - start_time
            logger.error(
                f"브라우저 세션 사망 — 수집 중단 | "
                f"완료={len(results)}/{len(articles)} | "
                f"PDF성공={pdf_ok} PDF실패={pdf_fail} | "
                f"실패기사={article.title[:40]} | "
                f"경과={elapsed:.1f}초 | "
                f"error={type(e).__name__}"
            )
            if on_progress:
                on_progress(
                    f"⚠ 브라우저 오류로 중단: {len(results)}/{len(articles)}개만 수집됨"
                )
            break

    elapsed = time.time() - start_time
    logger.info(
        f"기사 수집 완료 | {len(results)}/{len(articles)}개 | "
        f"PDF성공={pdf_ok} PDF실패={pdf_fail} | {elapsed:.1f}초"
    )
    return results


async def fetch_article(
    context: SeleniumContext,
    article: ArticleInfo,
    output_dir: Path,
) -> ArticleWithContent:
    """Fetch a single article: extract content and save as PDF."""
    return await asyncio.to_thread(_fetch_article_sync, context.driver, article, output_dir)


async def fetch_articles(
    context: SeleniumContext,
    articles: list[ArticleInfo],
    output_dir: Path,
    on_progress: callable | None = None,
) -> list[ArticleWithContent]:
    """Fetch multiple articles with sequential processing."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(
        _fetch_articles_sync, context.driver, articles, output_dir, on_progress
    )
