from __future__ import annotations

import asyncio
import re
from pathlib import Path

from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models.schemas import ArticleInfo, ArticleWithContent
from app.utils.logging import get_logger

logger = get_logger(__name__)


def sanitize_filename(title: str) -> str:
    """Sanitize article title for use as filename."""
    # Remove or replace invalid filename characters
    name = re.sub(r'[<>:"/\\|?*]', '', title)
    name = re.sub(r'\s+', ' ', name).strip()
    # Limit length
    if len(name) > 150:
        name = name[:150]
    return name


async def fetch_article(
    context: BrowserContext,
    article: ArticleInfo,
    output_dir: Path,
) -> ArticleWithContent:
    """Fetch a single article: extract content and save as PDF."""
    page = await context.new_page()
    result = ArticleWithContent(info=article)

    try:
        await page.goto(article.url, wait_until="domcontentloaded",
                        timeout=settings.CRAWL_TIMEOUT_MS)
        await page.wait_for_timeout(2000)

        # Extract article content
        content_selectors = [
            '.article_content', '.articleContent', '.news_content',
            '.view_content', '.article_body', '.newsContent',
            '#article_content', '#newsContent', '.content_area',
            '.view_area', '.article_view', 'article',
        ]

        content = ""
        for sel in content_selectors:
            el = page.locator(sel).first
            if await el.count() > 0:
                content = (await el.text_content() or "").strip()
                if content:
                    break

        if not content:
            # Fallback: get all text from body
            content = (await page.locator("body").text_content() or "").strip()
            content = content[:3000]

        result.content = content[:5000]  # Limit content for LLM

        # Generate PDF
        filename = sanitize_filename(article.title) + ".pdf"
        pdf_path = output_dir / filename

        # Try to use print-friendly view if available
        print_selectors = [
            'a:has-text("프린트")', 'a:has-text("인쇄")',
            'button:has-text("프린트")', '.btn_print', '#btn_print',
            'a[href*="print"]', 'a.print',
        ]

        used_print_view = False
        for sel in print_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    # Get the print URL if it's a link
                    href = await btn.get_attribute("href")
                    if href and "print" in href.lower():
                        print_url = href if href.startswith("http") else f"https://www.thebell.co.kr{href}"
                        await page.goto(print_url, wait_until="domcontentloaded",
                                        timeout=settings.CRAWL_TIMEOUT_MS)
                        await page.wait_for_timeout(1000)
                        used_print_view = True
                    break
            except Exception:
                continue

        # Generate PDF using Playwright's built-in PDF generation
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
        )

        result.pdf_path = str(pdf_path)
        logger.info(f"Saved PDF: {filename}")

    except PlaywrightTimeout:
        logger.error(f"Timeout fetching article: {article.title}")
    except Exception as e:
        logger.error(f"Error fetching article '{article.title}': {e}", exc_info=True)
    finally:
        await page.close()

    return result


async def fetch_articles(
    context: BrowserContext,
    articles: list[ArticleInfo],
    output_dir: Path,
    on_progress: callable | None = None,
) -> list[ArticleWithContent]:
    """Fetch multiple articles with concurrency control."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ArticleWithContent] = []
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_PAGES)

    async def fetch_one(idx: int, article: ArticleInfo):
        async with semaphore:
            if on_progress:
                on_progress(f"기사 수집 중: {idx + 1}/{len(articles)} - {article.title[:30]}...")
            result = await fetch_article(context, article, output_dir)
            return result

    tasks = [fetch_one(i, a) for i, a in enumerate(articles)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions
    valid_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Failed to fetch article {articles[i].title}: {r}")
            if on_progress:
                on_progress(f"오류: {articles[i].title[:30]}... - {str(r)}")
            valid_results.append(ArticleWithContent(info=articles[i]))
        else:
            valid_results.append(r)

    return valid_results
