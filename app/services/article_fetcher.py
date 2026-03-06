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


def _fetch_article_sync(driver, article: ArticleInfo, output_dir: Path) -> ArticleWithContent:
    """Fetch a single article: extract content and save as PDF (synchronous)."""
    result = ArticleWithContent(info=article)

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
            # Fallback: get all text from body
            body_els = driver.find_elements(By.CSS_SELECTOR, "body")
            if body_els:
                content = body_els[0].text.strip()[:3000]

        result.content = content[:5000]

        # Generate PDF
        filename = sanitize_filename(article.title) + ".pdf"
        pdf_path = output_dir / filename

        # Try to use print-friendly view if available
        print_selectors_css = [
            '.btn_print', '#btn_print', 'a[href*="print"]', 'a.print',
        ]
        print_selectors_xpath = [
            '//a[contains(text(),"프린트")]', '//a[contains(text(),"인쇄")]',
            '//button[contains(text(),"프린트")]',
        ]

        for sel in print_selectors_css:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    href = els[0].get_attribute("href")
                    if href and "print" in href.lower():
                        print_url = href if href.startswith("http") else f"https://www.thebell.co.kr{href}"
                        driver.get(print_url)
                        time.sleep(1)
                    break
            except Exception:
                continue

        for sel in print_selectors_xpath:
            try:
                els = driver.find_elements(By.XPATH, sel)
                if els:
                    href = els[0].get_attribute("href")
                    if href and "print" in href.lower():
                        print_url = href if href.startswith("http") else f"https://www.thebell.co.kr{href}"
                        driver.get(print_url)
                        time.sleep(1)
                    break
            except Exception:
                continue

        # Generate PDF using Chrome DevTools Protocol
        pdf_result = driver.execute_cdp_cmd("Page.printToPDF", PDF_PARAMS)
        pdf_data = base64.b64decode(pdf_result["data"])
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)

        result.pdf_path = str(pdf_path)
        logger.info(f"Saved PDF: {filename}")

    except TimeoutException:
        logger.error(f"Timeout fetching article: {article.title}")
    except Exception as e:
        logger.error(f"Error fetching article '{article.title}': {e}", exc_info=True)

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
