import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.models.schemas import SessionState, SessionStatus, ArticleWithContent
from app.services.business_day import get_clipping_window
from app.utils.logging import get_logger

KST = ZoneInfo("Asia/Seoul")

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _get_sessions() -> dict[str, SessionState]:
    from app.main import sessions
    return sessions


def _get_browser_manager():
    from app.main import browser_manager
    return browser_manager


# --- API Endpoints ---

class CrawlRequest(BaseModel):
    date_from: str | None = None  # "2026-03-05T10:00"
    date_to: str | None = None    # "2026-03-06T10:00"


class SelectRequest(BaseModel):
    article_ids: list[str]


@router.post("/api/crawl")
async def start_crawl(body: CrawlRequest, background_tasks: BackgroundTasks):
    """Start crawling TheBell articles."""
    sessions = _get_sessions()
    session_id = str(uuid.uuid4())

    # Use user-provided dates or default business day window
    if body.date_from and body.date_to:
        date_from = datetime.fromisoformat(body.date_from).replace(tzinfo=KST)
        date_to = datetime.fromisoformat(body.date_to).replace(tzinfo=KST)
    else:
        date_from, date_to = get_clipping_window()

    session = SessionState(
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
    )
    sessions[session_id] = session

    background_tasks.add_task(_crawl_task, session_id)
    return {"session_id": session_id, "status": "crawling"}


async def _crawl_task(session_id: str):
    """Background task for crawling."""
    from app.services.crawler import login, crawl_all_categories

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.CRAWLING

    bm = _get_browser_manager()
    ctx = None

    try:
        ctx = await bm.new_context(headless=False)

        # Manual login — opens visible browser for user to log in
        session.progress_messages.append("브라우저에서 더벨 로그인을 완료하세요...")
        login_ok = await login(ctx)
        if not login_ok:
            session.status = SessionStatus.ERROR
            session.error = "더벨 로그인 타임아웃. 브라우저에서 5분 내에 로그인하세요."
            return

        session.progress_messages.append("로그인 성공!")

        # Crawl
        def on_progress(msg: str):
            session.progress_messages.append(msg)

        articles = await crawl_all_categories(
            ctx, session.date_from, session.date_to, on_progress
        )

        session.articles = articles
        session.status = SessionStatus.CRAWL_DONE
        session.progress_messages.append(f"크롤링 완료: {len(articles)}개 기사")

    except Exception as e:
        logger.error(f"Crawl task error: {e}", exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = str(e)
    finally:
        if ctx:
            await ctx.close()


@router.get("/api/crawl/{session_id}")
async def get_crawl_status(session_id: str):
    """Get crawl status and article list."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return {
        "status": session.status.value,
        "article_count": len(session.articles),
        "articles": [a.model_dump() for a in session.articles],
        "progress": session.progress_messages[-1] if session.progress_messages else "",
        "error": session.error,
    }


@router.post("/api/recommend/{session_id}")
async def start_recommend(session_id: str, background_tasks: BackgroundTasks):
    """Start LLM recommendation on crawled articles."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    background_tasks.add_task(_recommend_task, session_id)
    return {"status": "recommending"}


async def _recommend_task(session_id: str):
    """Background task for LLM recommendation."""
    from app.services.llm_classifier import recommend_articles

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.RECOMMENDING

    try:
        session.progress_messages.append("LLM 기사 추천 분석 중...")
        recommendations = await recommend_articles(session.articles)
        session.recommendations = recommendations
        session.status = SessionStatus.RECOMMEND_DONE
        recommended_count = sum(1 for r in recommendations if r.recommended)
        session.progress_messages.append(
            f"추천 완료: {recommended_count}/{len(recommendations)}개 기사 추천됨"
        )
    except Exception as e:
        logger.error(f"Recommend task error: {e}", exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = str(e)


@router.get("/api/recommend/{session_id}")
async def get_recommendations(session_id: str):
    """Get recommendation results."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return {
        "status": session.status.value,
        "recommendations": [r.model_dump() for r in session.recommendations],
    }


@router.post("/api/select/{session_id}")
async def select_articles(session_id: str, body: SelectRequest):
    """Confirm selected article IDs."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    session.selected_ids = body.article_ids
    session.status = SessionStatus.SELECTED
    return {"status": "selected", "count": len(body.article_ids)}


@router.post("/api/generate/{session_id}")
async def start_generate(session_id: str, background_tasks: BackgroundTasks):
    """Start PDF generation, classification, merge, and packaging."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    background_tasks.add_task(_generate_task, session_id)
    return {"status": "generating"}


async def _generate_task(session_id: str):
    """Background task for full generation pipeline."""
    from app.services.article_fetcher import fetch_articles
    from app.services.llm_classifier import classify_articles
    from app.services.pdf_merger import merge_pdfs
    from app.services.docx_generator import generate_docx
    from app.services.packager import create_zip

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.GENERATING

    bm = _get_browser_manager()
    ctx = None

    try:
        # Prepare output directory
        session_dir = settings.OUTPUT_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        pdfs_dir = session_dir / "individual"
        pdfs_dir.mkdir(exist_ok=True)

        # Get selected articles
        selected = [a for a in session.articles if a.id in session.selected_ids]
        if not selected:
            session.status = SessionStatus.ERROR
            session.error = "선택된 기사가 없습니다."
            return

        def on_progress(msg: str):
            session.progress_messages.append(msg)

        # Step 1: Fetch articles and generate individual PDFs
        on_progress("Step 1/5: 브라우저에서 더벨 로그인을 완료하세요...")
        ctx = await bm.new_context(headless=False)

        # Manual login for article fetching
        from app.services.crawler import login
        login_ok = await login(ctx)
        if not login_ok:
            session.status = SessionStatus.ERROR
            session.error = "더벨 로그인 타임아웃."
            return
        on_progress("로그인 성공! 기사 본문 수집 및 PDF 생성 중...")

        articles_with_content = await fetch_articles(ctx, selected, pdfs_dir, on_progress)
        session.articles_with_content = articles_with_content

        await ctx.close()
        ctx = None

        # Step 2: Classify with LLM
        on_progress("Step 2/5: AI 분류 중...")
        classification = await classify_articles(articles_with_content)
        session.classification = classification
        on_progress("분류 완료!")

        # Step 3: Merge PDFs
        on_progress("Step 3/5: PDF 합본 중...")
        date_str = session.date_to.strftime("%Y.%m.%d") if session.date_to else datetime.now().strftime("%Y.%m.%d")
        merged_pdf_path = session_dir / f"(더벨) Daily News Clipping {date_str}.pdf"
        merge_pdfs(classification, articles_with_content, merged_pdf_path, on_progress)

        # Step 4: Generate DOCX
        on_progress("Step 4/5: DOCX 목차 생성 중...")
        docx_path = session_dir / f"(더벨) Daily News Clipping {date_str}.docx"
        generate_docx(classification, articles_with_content, docx_path, date_str)
        on_progress("DOCX 생성 완료!")

        # Step 5: Package ZIP
        on_progress("Step 5/5: ZIP 파일 생성 중...")
        zip_path = session_dir / f"(더벨) Daily News Clipping {date_str}.zip"
        create_zip(articles_with_content, merged_pdf_path, docx_path, zip_path, date_str)

        session.zip_path = str(zip_path)
        session.status = SessionStatus.DONE
        on_progress("모든 작업 완료!")

    except Exception as e:
        logger.error(f"Generate task error: {e}", exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = str(e)
    finally:
        if ctx:
            await ctx.close()


@router.get("/api/progress/{session_id}")
async def progress_stream(session_id: str):
    """SSE endpoint for real-time progress updates."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    async def event_stream():
        last_idx = 0
        while True:
            if len(session.progress_messages) > last_idx:
                for msg in session.progress_messages[last_idx:]:
                    yield f"data: {msg}\n\n"
                last_idx = len(session.progress_messages)

            if session.status in (SessionStatus.DONE, SessionStatus.ERROR,
                                  SessionStatus.CRAWL_DONE, SessionStatus.RECOMMEND_DONE):
                yield f"event: done\ndata: {session.status.value}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/download/{session_id}")
async def download_zip(session_id: str):
    """Download the final ZIP file."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not session.zip_path or not Path(session.zip_path).exists():
        raise HTTPException(404, "ZIP file not found")

    return FileResponse(
        session.zip_path,
        media_type="application/zip",
        filename=Path(session.zip_path).name,
    )


# --- Page Routes ---

@router.get("/review/{session_id}", response_class=HTMLResponse)
async def review_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    # Group articles by category
    categories = {}
    for a in session.articles:
        cat = a.subcategory or a.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(a)

    # Build recommendation map
    rec_map = {r.article_id: r for r in session.recommendations}

    return templates.TemplateResponse("review.html", {
        "request": request,
        "session": session,
        "categories": categories,
        "rec_map": rec_map,
    })


@router.get("/progress/{session_id}", response_class=HTMLResponse)
async def progress_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return templates.TemplateResponse("progress.html", {
        "request": request,
        "session": session,
    })


@router.get("/result/{session_id}", response_class=HTMLResponse)
async def result_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return templates.TemplateResponse("result.html", {
        "request": request,
        "session": session,
    })
