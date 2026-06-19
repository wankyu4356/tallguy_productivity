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
        logger.debug(f"[print_block] CDP 주입 성공 | id={_cdp_script_id}")
    except Exception as e:
        logger.warning(f"[print_block] CDP 주입 실패 — window.print() 차단 불가! | {type(e).__name__}: {e}")


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
    t0 = time.time()
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
                logger.debug(f"[print T+{time.time()-t0:.1f}s] print 차단 주입")
                _block_print_dialog(driver)
                logger.debug(f"[print T+{time.time()-t0:.1f}s] 프린트 페이지 로드 시작 | url={print_url}")
                driver.get(print_url)
                logger.debug(
                    f"[print T+{time.time()-t0:.1f}s] 프린트 페이지 로드 완료 | "
                    f"windows={len(driver.window_handles)} | url={driver.current_url}"
                )
                time.sleep(0.5)
                _unblock_print_dialog(driver)
                if not _is_error_page_simple(driver):
                    logger.debug(f"[print T+{time.time()-t0:.1f}s] 프린트 페이지 사용 OK")
                    return True
                # Error page — go back to article
                logger.debug(f"[print T+{time.time()-t0:.1f}s] 에러 페이지 → 기사로 복귀")
                driver.get(article_url)
                time.sleep(0.5)
            except Exception as e:
                logger.debug(
                    f"[print T+{time.time()-t0:.1f}s] 프린트 페이지 예외 | "
                    f"{type(e).__name__}: {str(e)[:200]}"
                )
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
    """Close real thebell popups opened by article/print pages, keeping keep_window.

    IMPORTANT: Edge spawns a background MSN new-tab prerender window
    (ntp.msn.com/edge/ntp?...&prerender=1) that CANNOT be closed via
    driver.close() — it hangs for 20 seconds and then throws
    "failed to close window in 20 seconds". Attempting to close it on every
    article wasted ~20-40s per article. We must skip any non-thebell window
    and only close genuine thebell popups.
    """
    try:
        handles = driver.window_handles
    except Exception:
        return
    if len(handles) <= 1:
        return

    for w in handles:
        if w == keep_window:
            continue
        try:
            driver.switch_to.window(w)
            url = ""
            try:
                url = driver.current_url
            except Exception:
                url = ""
            # Only close real thebell popups. Edge-internal / prerendered
            # new-tab windows (ntp.msn.com, edge://, about:blank) hang on
            # close — leave them in the background, they don't affect PDF gen.
            if "thebell" in url.lower():
                logger.debug(f"[close_win] 더벨 팝업 닫기 | url={url[:70]}")
                driver.close()
            else:
                logger.debug(f"[close_win] Edge 내부창 건너뜀 (닫기 시 20초 멈춤) | url={url[:70]}")
        except Exception as e:
            logger.debug(f"[close_win] 처리 실패 | {type(e).__name__}: {str(e)[:80]}")

    # Always return focus to the main window
    try:
        driver.switch_to.window(keep_window)
    except Exception:
        try:
            remaining = driver.window_handles
            if remaining:
                driver.switch_to.window(remaining[0])
        except Exception:
            pass


def _fetch_article_sync(driver, article: ArticleInfo, output_dir: Path) -> ArticleWithContent:
    """Fetch a single article: extract content and save as PDF (synchronous)."""
    t0 = time.time()
    result = ArticleWithContent(info=article)
    original_window = driver.current_window_handle

    # Clean up stale popup windows from previous articles
    if len(driver.window_handles) > 1:
        logger.debug(
            f"[T+{time.time()-t0:.1f}s] 팝업 정리 시작 | windows={len(driver.window_handles)} | "
            f"{article.title[:30]}"
        )
        _close_extra_windows(driver, original_window)
        logger.debug(f"[T+{time.time()-t0:.1f}s] 팝업 정리 완료")

    try:
        driver.set_page_load_timeout(settings.CRAWL_TIMEOUT_MS / 1000)

        logger.debug(f"[T+{time.time()-t0:.1f}s] print 차단 주입")
        _block_print_dialog(driver)

        logger.debug(f"[T+{time.time()-t0:.1f}s] 기사 페이지 로드 시작 | url={article.url}")
        driver.get(article.url)
        logger.debug(
            f"[T+{time.time()-t0:.1f}s] 기사 페이지 로드 완료 | "
            f"windows={len(driver.window_handles)} | url={driver.current_url}"
        )

        time.sleep(0.5)
        _unblock_print_dialog(driver)

        # Extract article content
        logger.debug(f"[T+{time.time()-t0:.1f}s] 본문 추출 시작")
        content_selectors = [
            '.article_content', '.articleContent', '.news_content',
            '.view_content', '.article_body', '.newsContent',
            '#article_content', '#newsContent', '.content_area',
            '.view_area', '.article_view', 'article',
        ]

        content = ""
        matched_sel = ""
        for sel in content_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                content = els[0].text.strip()
                if content:
                    matched_sel = sel
                    break

        if not content:
            body_els = driver.find_elements(By.CSS_SELECTOR, "body")
            if body_els:
                content = body_els[0].text.strip()[:3000]
                matched_sel = "body(fallback)"

        result.content = content[:5000]
        logger.debug(
            f"[T+{time.time()-t0:.1f}s] 본문 추출 완료 | "
            f"selector={matched_sel} | len={len(content)}"
        )

        # Generate PDF — try print-friendly page first
        filename = sanitize_filename(article.title) + ".pdf"
        pdf_path = output_dir / filename

        logger.debug(f"[T+{time.time()-t0:.1f}s] 프린트 페이지 이동 시작")
        used_print_page = _navigate_to_print_page(driver, article.url)
        logger.debug(
            f"[T+{time.time()-t0:.1f}s] 프린트 페이지 이동 완료 | "
            f"used_print={used_print_page} | windows={len(driver.window_handles)} | "
            f"url={driver.current_url}"
        )

        # Generate PDF using Chrome DevTools Protocol
        logger.debug(f"[T+{time.time()-t0:.1f}s] CDP printToPDF 시작")
        pdf_result = driver.execute_cdp_cmd("Page.printToPDF", PDF_PARAMS)
        pdf_data = base64.b64decode(pdf_result["data"])
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)

        result.pdf_path = str(pdf_path)
        logger.debug(f"[T+{time.time()-t0:.1f}s] PDF 저장 완료 | {filename}")
        logger.info(f"Saved PDF ({time.time()-t0:.1f}s): {filename}")

        # Clean up: close popup windows and return to original window
        if len(driver.window_handles) > 1:
            logger.debug(
                f"[T+{time.time()-t0:.1f}s] 최종 팝업 정리 | "
                f"windows={len(driver.window_handles)}"
            )
        _close_extra_windows(driver, original_window)
        logger.debug(f"[T+{time.time()-t0:.1f}s] 기사 처리 완료")

    except InvalidSessionIdException as e:
        _log_browser_state(driver, "InvalidSessionId")
        logger.error(
            f"[T+{time.time()-t0:.1f}s] 브라우저 세션 사망 | "
            f"article={article.title[:50]} | url={article.url} | {e}",
            exc_info=True,
        )
        raise
    except TimeoutException as e:
        _log_browser_state(driver, "Timeout")
        logger.error(
            f"[T+{time.time()-t0:.1f}s] 페이지 로드 타임아웃 | "
            f"article={article.title[:50]} | url={article.url} | "
            f"timeout={settings.CRAWL_TIMEOUT_MS}ms",
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
            f"[T+{time.time()-t0:.1f}s] WebDriver 오류 | "
            f"article={article.title[:50]} | url={article.url} | "
            f"session_dead={is_session_dead}",
            exc_info=True,
        )
        if is_session_dead:
            raise
        _close_extra_windows(driver, original_window)
    except Exception as e:
        logger.error(
            f"[T+{time.time()-t0:.1f}s] 기사 수집 오류 | "
            f"article={article.title[:50]} | url={article.url} | "
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
