from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

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


def _navigate_to_print_page(driver, article_url: str) -> bool:
    """Try to navigate to the print-friendly version of the article.

    Strategy:
    1. Click print button on the page (handles JS popups)
    2. Try URL manipulation (newsview → NewsPrint, ArticleView → ArticlePrint)
    3. Return False if no print page found
    """
    original_window = driver.current_window_handle

    # Strategy 1: Find and click print button (handles onclick popups)
    print_selectors = [
        (By.CSS_SELECTOR, '.btn_print'),
        (By.CSS_SELECTOR, '#btn_print'),
        (By.CSS_SELECTOR, 'a.print'),
        (By.CSS_SELECTOR, 'a[href*="print" i]'),
        (By.CSS_SELECTOR, 'a[onclick*="print" i]'),
        (By.CSS_SELECTOR, 'a[onclick*="Print" i]'),
        (By.CSS_SELECTOR, 'button[onclick*="print" i]'),
        (By.XPATH, '//a[contains(text(),"프린트")]'),
        (By.XPATH, '//a[contains(text(),"인쇄")]'),
        (By.XPATH, '//button[contains(text(),"프린트")]'),
        (By.XPATH, '//img[@alt="프린트"]/..'),
        (By.XPATH, '//img[contains(@src,"print")]/..'),
    ]

    for by, sel in print_selectors:
        try:
            els = driver.find_elements(by, sel)
            if not els:
                continue

            el = els[0]
            el.click()
            time.sleep(1.5)

            # Check if a new window/tab was opened (JS popup)
            all_windows = driver.window_handles
            if len(all_windows) > 1:
                new_window = [w for w in all_windows if w != original_window][0]
                driver.switch_to.window(new_window)
                time.sleep(1)
                logger.info(f"프린트 팝업 감지 | url={driver.current_url}")
                return True

            # Check if current page changed to a print page
            if "print" in driver.current_url.lower():
                logger.info(f"프린트 페이지 이동 | url={driver.current_url}")
                return True

        except Exception:
            continue

    # Strategy 2: URL manipulation for TheBell
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
                driver.get(print_url)
                time.sleep(1.5)
                if not _is_error_page_simple(driver):
                    logger.info(f"프린트 URL 직접 접근 | url={print_url}")
                    return True
                # Error page — go back to article
                driver.get(article_url)
                time.sleep(1)
            except Exception:
                driver.get(article_url)
                time.sleep(1)
            break

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

    try:
        driver.set_page_load_timeout(settings.CRAWL_TIMEOUT_MS / 1000)
        driver.get(article.url)
        time.sleep(2)

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

        # Generate PDF — try print-friendly page first
        filename = sanitize_filename(article.title) + ".pdf"
        pdf_path = output_dir / filename

        used_print_page = _navigate_to_print_page(driver, article.url)
        if used_print_page:
            logger.info(f"프린트 페이지에서 PDF 생성: {article.title[:40]}")

        # Generate PDF using Chrome DevTools Protocol
        pdf_result = driver.execute_cdp_cmd("Page.printToPDF", PDF_PARAMS)
        pdf_data = base64.b64decode(pdf_result["data"])
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)

        result.pdf_path = str(pdf_path)
        logger.info(f"Saved PDF: {filename}")

        # Clean up: close popup windows and return to original window
        _close_extra_windows(driver, original_window)

    except TimeoutException:
        logger.error(f"Timeout fetching article: {article.title}")
        _close_extra_windows(driver, original_window)
    except Exception as e:
        logger.error(f"Error fetching article '{article.title}': {e}", exc_info=True)
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
    for i, article in enumerate(articles):
        if on_progress:
            on_progress(f"기사 수집 중: {i + 1}/{len(articles)} - {article.title[:30]}...")
        result = _fetch_article_sync(driver, article, output_dir)
        results.append(result)
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
