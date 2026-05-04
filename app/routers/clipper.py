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
    import time as _time
    from app.services.crawler import login, crawl_all_categories

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.CRAWLING
    task_stage = "[태스크:크롤링]"
    t0 = _time.time()

    bm = _get_browser_manager()
    ctx = None

    try:
        logger.info(f"{task_stage} 시작 | session={session_id[:8]} | date_range={session.date_from}~{session.date_to}")

        # Step 1: Create browser context
        logger.info(f"{task_stage} 브라우저 컨텍스트 생성 중...")
        ctx = await bm.new_context(headless=False)
        logger.info(f"{task_stage} 브라우저 컨텍스트 생성 완료 | elapsed={_time.time()-t0:.1f}s")

        # Manual login — opens visible browser for user to log in
        session.progress_messages.append("브라우저에서 딜사이트플러스 로그인을 완료하세요...")
        # Step 2: Login
        session.progress_messages.append("브라우저에서 딜사이트플러스 로그인을 완료하세요...")
        t_login = _time.time()
        login_ok = await login(ctx)
        login_elapsed = _time.time() - t_login
        if not login_ok:
            error_msg = f"더벨 로그인 타임아웃 (5분). 브라우저에서 로그인하세요. (소요: {login_elapsed:.0f}초)"
            logger.error(f"{task_stage} 로그인 실패 | elapsed={login_elapsed:.1f}s")
            session.status = SessionStatus.ERROR
            session.error = "딜사이트플러스 로그인 타임아웃. 브라우저에서 5분 내에 로그인하세요."
            session.error = error_msg
            session.progress_messages.append(f"⚠ {error_msg}")
            return

        session.progress_messages.append(f"로그인 성공! ({login_elapsed:.0f}초)")
        logger.info(f"{task_stage} 로그인 성공 | elapsed={login_elapsed:.1f}s")

        # Step 3: Crawl
        def on_progress(msg: str):
            session.progress_messages.append(msg)

        t_crawl = _time.time()
        articles = await crawl_all_categories(
            ctx, session.date_from, session.date_to, on_progress
        )
        crawl_elapsed = _time.time() - t_crawl

        session.articles = articles
        session.status = SessionStatus.CRAWL_DONE
        total_elapsed = _time.time() - t0
        session.progress_messages.append(
            f"크롤링 완료: {len(articles)}개 기사 (크롤링 {crawl_elapsed:.0f}초 / 전체 {total_elapsed:.0f}초)"
        )
        logger.info(
            f"{task_stage} 완료 | articles={len(articles)} | "
            f"crawl_time={crawl_elapsed:.1f}s | total_time={total_elapsed:.1f}s"
        )

    except Exception as e:
        total_elapsed = _time.time() - t0
        error_detail = (
            f"{task_stage} 치명적 오류 | session={session_id[:8]} | "
            f"error={type(e).__name__}: {str(e)[:300]} | total_elapsed={total_elapsed:.1f}s"
        )
        logger.error(error_detail, exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = f"크롤링 오류: {type(e).__name__}: {str(e)[:200]}"
        session.progress_messages.append(f"⚠ {session.error}")
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


class RecommendRequest(BaseModel):
    max_count: int | None = None


@router.post("/api/recommend/{session_id}")
async def start_recommend(session_id: str, body: RecommendRequest, background_tasks: BackgroundTasks):
    """Start LLM recommendation on crawled articles."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    background_tasks.add_task(_recommend_task, session_id, body.max_count)
    return {"status": "recommending"}


async def _recommend_task(session_id: str, max_count: int | None = None):
    """Background task for LLM recommendation."""
    import time as _time
    from app.services.llm_classifier import recommend_articles

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.RECOMMENDING
    task_stage = "[태스크:추천]"
    t0 = _time.time()

    try:
        count_msg = f" (목표: 약 {max_count}개)" if max_count else ""
        logger.info(f"{task_stage} 시작 | session={session_id[:8]} | articles={len(session.articles)}개{count_msg}")
        session.progress_messages.append(f"LLM 기사 추천 분석 중...{count_msg}")
        recommendations = await recommend_articles(session.articles, max_count=max_count)
        session.recommendations = recommendations
        session.status = SessionStatus.RECOMMEND_DONE
        recommended_count = sum(1 for r in recommendations if r.recommended)
        elapsed = _time.time() - t0
        session.progress_messages.append(
            f"추천 완료: {recommended_count}/{len(recommendations)}개 기사 추천됨 ({elapsed:.0f}초)"
        )
        logger.info(
            f"{task_stage} 완료 | recommended={recommended_count}/{len(recommendations)} | elapsed={elapsed:.1f}s"
        )
    except Exception as e:
        elapsed = _time.time() - t0
        logger.error(
            f"{task_stage} 오류 | session={session_id[:8]} | "
            f"error={type(e).__name__}: {str(e)[:300]} | elapsed={elapsed:.1f}s",
            exc_info=True,
        )
        session.status = SessionStatus.ERROR
        session.error = f"추천 오류: {type(e).__name__}: {str(e)[:200]}"
        session.progress_messages.append(f"⚠ {session.error}")


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
    """Background task: fetch articles + AI classification, then pause for review."""
    import time as _time
    from app.services.article_fetcher import fetch_articles
    from app.services.llm_classifier import classify_articles

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.GENERATING
    task_stage = "[태스크:생성]"
    t0 = _time.time()

    bm = _get_browser_manager()
    ctx = None

    try:
        logger.info(f"{task_stage} 시작 | session={session_id[:8]} | selected={len(session.selected_ids)}개")

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
            logger.error(f"{task_stage} 선택된 기사 없음 | session={session_id[:8]}")
            return

        def on_progress(msg: str):
            session.progress_messages.append(msg)

        # Step 1: Fetch articles and generate individual PDFs
        on_progress("Step 1/5: 브라우저에서 딜사이트플러스 로그인을 완료하세요...")
        ctx = await bm.new_context(headless=False)

        from app.services.crawler import login
        t_login = _time.time()
        login_ok = await login(ctx)
        login_elapsed = _time.time() - t_login
        if not login_ok:
            error_msg = f"더벨 로그인 타임아웃 (소요: {login_elapsed:.0f}초)"
            session.status = SessionStatus.ERROR
            session.error = "딜사이트플러스 로그인 타임아웃."
            session.error = error_msg
            logger.error(f"{task_stage} 로그인 실패 | elapsed={login_elapsed:.1f}s")
            on_progress(f"⚠ {error_msg}")
            return
        on_progress(f"로그인 성공! ({login_elapsed:.0f}초) 기사 본문 수집 및 PDF 생성 중...")
        logger.info(f"{task_stage} 로그인 성공 | elapsed={login_elapsed:.1f}s")

        t_fetch = _time.time()
        articles_with_content = await fetch_articles(ctx, selected, pdfs_dir, on_progress)
        fetch_elapsed = _time.time() - t_fetch
        session.articles_with_content = articles_with_content
        logger.info(
            f"{task_stage} 기사 수집 완료 | articles={len(articles_with_content)} | elapsed={fetch_elapsed:.1f}s"
        )

        await ctx.close()
        ctx = None

        # Step 2: Classify with LLM
        on_progress("Step 2/5: AI 분류 중...")
        t_classify = _time.time()
        classification = await classify_articles(articles_with_content)
        classify_elapsed = _time.time() - t_classify
        session.classification = classification
        logger.info(
            f"{task_stage} AI 분류 완료 | categories={len(classification.categories)} | elapsed={classify_elapsed:.1f}s"
        )

        total_elapsed = _time.time() - t0
        on_progress(f"분류 완료! 검수 페이지로 이동합니다... (전체 {total_elapsed:.0f}초)")
        logger.info(f"{task_stage} 완료 → 검수 대기 | total_elapsed={total_elapsed:.1f}s")

        # Pause here — wait for user review
        session.status = SessionStatus.REVIEW_READY

    except Exception as e:
        total_elapsed = _time.time() - t0
        error_detail = (
            f"{task_stage} 치명적 오류 | session={session_id[:8]} | "
            f"error={type(e).__name__}: {str(e)[:300]} | total_elapsed={total_elapsed:.1f}s"
        )
        logger.error(error_detail, exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = f"생성 오류: {type(e).__name__}: {str(e)[:200]}"
        session.progress_messages.append(f"⚠ {session.error}")
    finally:
        if ctx:
            await ctx.close()


@router.post("/api/reclassify/{session_id}")
async def reclassify(session_id: str, background_tasks: BackgroundTasks):
    """Re-run AI classification in strict mode without re-fetching articles."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not session.articles_with_content:
        raise HTTPException(400, "기사 본문 데이터가 없습니다. 처음부터 다시 진행하세요.")

    background_tasks.add_task(_reclassify_task, session_id)
    return {"status": "reclassifying"}


async def _reclassify_task(session_id: str):
    """Background task: re-classify articles in strict mode."""
    import time as _time
    from app.services.llm_classifier import classify_articles

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.GENERATING
    task_stage = "[태스크:재분류]"
    t0 = _time.time()

    try:
        articles = session.articles_with_content
        logger.info(f"{task_stage} 시작 (strict mode) | session={session_id[:8]} | articles={len(articles)}개")
        session.progress_messages.append("AI 재분류 중 (엄밀 모드)...")

        # Identify issues from previous classification
        prev_issues = ""
        if session.classification:
            from app.services.llm_classifier import _find_empty_categories
            empty = _find_empty_categories(session.classification)
            if empty:
                prev_issues = f"이전 분류에서 다음 카테고리가 비어있었습니다: {', '.join(empty)}"

        t_classify = _time.time()
        classification = await classify_articles(
            articles, strict=True, previous_issues=prev_issues
        )
        classify_elapsed = _time.time() - t_classify

        session.classification = classification
        session.status = SessionStatus.REVIEW_READY
        total_elapsed = _time.time() - t0

        session.progress_messages.append(
            f"재분류 완료! ({classify_elapsed*1000:.0f}ms) 검수 페이지로 이동합니다..."
        )
        logger.info(
            f"{task_stage} 완료 | categories={len(classification.categories)} | "
            f"classify_time={classify_elapsed*1000:.0f}ms | total={total_elapsed*1000:.0f}ms"
        )

    except Exception as e:
        total_elapsed = _time.time() - t0
        logger.error(
            f"{task_stage} 오류 | session={session_id[:8]} | "
            f"error={type(e).__name__}: {str(e)[:300]} | elapsed={total_elapsed*1000:.0f}ms",
            exc_info=True,
        )
        session.status = SessionStatus.ERROR
        session.error = f"재분류 오류: {type(e).__name__}: {str(e)[:200]}"
        session.progress_messages.append(f"⚠ {session.error}")


async def _finalize_task(session_id: str):
    """Background task: merge PDFs, generate DOCX, package ZIP (after review)."""
    import time as _time
    from app.services.pdf_merger import merge_pdfs
    from app.services.docx_generator import generate_docx
    from app.services.packager import create_zip

    sessions = _get_sessions()
    session = sessions[session_id]
    session.status = SessionStatus.FINALIZING
    task_stage = "[태스크:최종화]"
    t0 = _time.time()

    try:
        logger.info(f"{task_stage} 시작 | session={session_id[:8]} | articles={len(session.articles_with_content)}개")
        session_dir = settings.OUTPUT_DIR / session_id
        classification = session.classification
        articles_with_content = session.articles_with_content

        def on_progress(msg: str):
            session.progress_messages.append(msg)

        date_str = session.date_to.strftime("%Y.%m.%d") if session.date_to else datetime.now().strftime("%Y.%m.%d")

        # Step 3: Merge PDFs
        on_progress("Step 3/5: PDF 합본 중...")
        merged_pdf_path = session_dir / f"(딜사이트플러스) Daily News Clipping {date_str}.pdf"
        t_step = _time.time()
        merged_pdf_path = session_dir / f"(더벨) Daily News Clipping {date_str}.pdf"
        merge_pdfs(classification, articles_with_content, merged_pdf_path, on_progress)
        logger.info(f"{task_stage} Step 3 PDF 합본 완료 | elapsed={_time.time()-t_step:.1f}s")

        # Step 4: Generate DOCX
        on_progress("Step 4/5: DOCX 목차 생성 중...")
        docx_path = session_dir / f"(딜사이트플러스) Daily News Clipping {date_str}.docx"
        on_progress("Step 4/5: DOCX 목차 생성 ���...")
        t_step = _time.time()
        docx_path = session_dir / f"(더벨) Daily News Clipping {date_str}.docx"
        generate_docx(classification, articles_with_content, docx_path, date_str)
        logger.info(f"{task_stage} Step 4 DOCX 생성 완료 | elapsed={_time.time()-t_step:.1f}s")
        on_progress("DOCX 생성 완료!")

        # Step 5: Package ZIP
        on_progress("Step 5/5: ZIP 파일 생성 중...")
        zip_path = session_dir / f"(딜사이트플러스) Daily News Clipping {date_str}.zip"
        t_step = _time.time()
        zip_path = session_dir / f"(더벨) Daily News Clipping {date_str}.zip"
        create_zip(articles_with_content, merged_pdf_path, docx_path, zip_path, date_str)
        logger.info(f"{task_stage} Step 5 ZIP 패키징 ���료 | elapsed={_time.time()-t_step:.1f}s")

        session.zip_path = str(zip_path)
        session.status = SessionStatus.DONE
        total_elapsed = _time.time() - t0
        on_progress(f"모든 작업 완료! (최종화 {total_elapsed:.0f}초)")
        logger.info(f"{task_stage} 완료 | zip={zip_path} | total_elapsed={total_elapsed:.1f}s")

    except Exception as e:
        total_elapsed = _time.time() - t0
        error_detail = (
            f"{task_stage} 오류 | session={session_id[:8]} | "
            f"error={type(e).__name__}: {str(e)[:300]} | total_elapsed={total_elapsed:.1f}s"
        )
        logger.error(error_detail, exc_info=True)
        session.status = SessionStatus.ERROR
        session.error = f"최종화 오류: {type(e).__name__}: {str(e)[:200]}"
        session.progress_messages.append(f"⚠ {session.error}")


class RewindRequest(BaseModel):
    target_status: str  # "crawl_done" or "review_ready"


@router.post("/api/rewind/{session_id}")
async def rewind_session(session_id: str, body: RewindRequest):
    """Rewind session to a previous stage without losing data."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    target = body.target_status
    if target == "crawl_done":
        # Go back to article selection — keep articles, clear downstream
        session.status = SessionStatus.CRAWL_DONE
        session.selected_ids = []
        # Keep recommendations (expensive LLM call) and articles_with_content
        # so user doesn't have to redo everything
        logger.info(f"Session {session_id}: rewound to crawl_done (article selection)")
        return {"status": "crawl_done"}
    elif target == "review_ready":
        # Go back to classification review — keep classification
        if not session.classification:
            raise HTTPException(400, "분류 결과가 없어 Index 검수로 돌아갈 수 없습니다.")
        session.status = SessionStatus.REVIEW_READY
        logger.info(f"Session {session_id}: rewound to review_ready (index review)")
        return {"status": "review_ready"}
    else:
        raise HTTPException(400, f"Invalid target status: {target}")


@router.get("/api/classification/{session_id}")
async def get_classification(session_id: str):
    """Get current classification tree with article summaries for review."""
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not session.classification:
        raise HTTPException(400, "Classification not ready")

    import re as _re
    articles_map = {a.info.id: a for a in session.articles_with_content}
    # 숫자/hex만 추출한 역매핑 (LLM이 괄호/공백 포함해서 반환할 때 대비)
    id_by_digits = {}
    for a in session.articles_with_content:
        digits = _re.sub(r'[^0-9a-fA-F]', '', a.info.id)
        if digits:
            id_by_digits[digits] = a.info.id

    def resolve_id(aid: str) -> str | None:
        """LLM이 반환한 ID를 실제 article ID로 변환."""
        aid = str(aid).strip().strip('"').strip("'")
        if aid in articles_map:
            return aid
        cleaned = aid.strip('[]').strip()
        if cleaned in articles_map:
            return cleaned
        digits = _re.sub(r'[^0-9a-fA-F]', '', aid)
        if digits and digits in id_by_digits:
            return id_by_digits[digits]
        for vid in articles_map:
            if vid in aid or aid in vid:
                return vid
        return None

    # 디버그: ID 매칭 로그
    all_cls_ids = []
    for cat in session.classification.categories:
        all_cls_ids.extend(cat.articles)
        for sub in cat.subcategories:
            all_cls_ids.extend(sub.articles)
            for si in sub.sub_items:
                all_cls_ids.extend(si.articles)

    resolved = [resolve_id(aid) for aid in all_cls_ids]
    matched_count = sum(1 for r in resolved if r)
    unmatched_raw = [aid for aid, r in zip(all_cls_ids, resolved) if not r]
    if unmatched_raw:
        logger.warning(
            f"Classification ID mismatch: {len(unmatched_raw)}/{len(all_cls_ids)} unmatched. "
            f"Unmatched: {unmatched_raw[:5]}. "
            f"Available: {list(articles_map.keys())[:5]}"
        )
    logger.info(f"Classification ID resolve: {matched_count}/{len(all_cls_ids)} matched")

    def article_detail(aid: str):
        real_id = resolve_id(aid)
        if not real_id:
            return None
        a = articles_map[real_id]
        summary = a.info.summary or ""
        if not summary and a.content:
            summary = a.content[:200].replace("\n", " ").strip()
            if len(a.content) > 200:
                summary += "..."
        return {
            "id": a.info.id,
            "title": a.info.title,
            "summary": summary,
            "url": a.info.url,
            "category": a.info.category,
        }

    tree = []
    total_articles = 0
    for cat in session.classification.categories:
        cat_articles = [d for aid in cat.articles if (d := article_detail(aid))]
        cat_data = {
            "name": cat.name,
            "articles": cat_articles,
            "subcategories": [],
        }
        total_articles += len(cat_articles)
        for sub in cat.subcategories:
            sub_articles = [d for aid in sub.articles if (d := article_detail(aid))]
            sub_data = {
                "name": sub.name,
                "articles": sub_articles,
                "sub_items": [],
            }
            total_articles += len(sub_articles)
            for si in sub.sub_items:
                si_articles = [d for aid in si.articles if (d := article_detail(aid))]
                si_data = {
                    "name": si.name,
                    "articles": si_articles,
                }
                total_articles += len(si_articles)
                sub_data["sub_items"].append(si_data)
            cat_data["subcategories"].append(sub_data)
        tree.append(cat_data)

    logger.info(f"Classification tree: {total_articles} articles resolved out of {len(all_cls_ids)} classified IDs")

    # 안전장치: 분류된 기사가 0개면 모든 기사를 첫 번째 카테고리에 직접 배치
    if total_articles == 0 and session.articles_with_content:
        logger.warning(f"No articles in classification tree! Injecting all {len(session.articles_with_content)} articles")
        all_article_details = []
        for a in session.articles_with_content:
            summary = a.info.summary or ""
            if not summary and a.content:
                summary = a.content[:200].replace("\n", " ").strip()
                if len(a.content) > 200:
                    summary += "..."
            all_article_details.append({
                "id": a.info.id,
                "title": a.info.title,
                "summary": summary,
                "url": a.info.url,
                "category": a.info.category,
            })
        total_articles = len(all_article_details)

        # 기존 트리 구조가 있으면 첫 카테고리에 넣기
        if tree:
            # subcategory가 있으면 마지막 서브카테고리(기타)에
            if tree[0].get("subcategories"):
                target_sub = tree[0]["subcategories"][-1]
                if target_sub.get("sub_items"):
                    target_sub["sub_items"][-1]["articles"] = all_article_details
                else:
                    target_sub["articles"] = all_article_details
            else:
                tree[0]["articles"] = all_article_details
        else:
            tree = [{"name": "전체 기사", "articles": all_article_details, "subcategories": []}]

    return {
        "status": session.status.value,
        "tree": tree,
        "is_fallback": session.classification.is_fallback if session.classification else False,
        "fallback_reason": session.classification.fallback_reason if session.classification else "",
        "debug": {
            "classified_ids_total": len(all_cls_ids),
            "matched": matched_count,
            "unmatched_sample": unmatched_raw[:10],
            "available_ids_sample": list(articles_map.keys())[:10],
            "articles_with_content_count": len(session.articles_with_content),
            "total_in_tree": total_articles,
        }
    }


class ConfirmIndexRequest(BaseModel):
    tree: list[dict]


@router.post("/api/confirm-index/{session_id}")
async def confirm_index(session_id: str, body: ConfirmIndexRequest, background_tasks: BackgroundTasks):
    """Accept reviewed classification and proceed to finalize."""
    from app.models.schemas import (
        ClassifiedOutput, ClassificationCategory,
        ClassificationSubcategory, ClassificationSubItem,
    )

    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status != SessionStatus.REVIEW_READY:
        raise HTTPException(400, f"Cannot confirm in status: {session.status.value}")

    # Rebuild ClassifiedOutput from reviewed tree
    categories = []
    article_order = []
    for cat_data in body.tree:
        cat_articles = [a["id"] for a in cat_data.get("articles", [])]
        article_order.extend(cat_articles)

        subcategories = []
        for sub_data in cat_data.get("subcategories", []):
            sub_articles = [a["id"] for a in sub_data.get("articles", [])]
            article_order.extend(sub_articles)

            sub_items = []
            for si_data in sub_data.get("sub_items", []):
                si_articles = [a["id"] for a in si_data.get("articles", [])]
                article_order.extend(si_articles)
                sub_items.append(ClassificationSubItem(
                    name=si_data["name"],
                    articles=si_articles,
                ))
            subcategories.append(ClassificationSubcategory(
                name=sub_data["name"],
                sub_items=sub_items,
                articles=sub_articles,
            ))
        categories.append(ClassificationCategory(
            name=cat_data["name"],
            subcategories=subcategories,
            articles=cat_articles,
        ))

    session.classification = ClassifiedOutput(
        categories=categories,
        article_order=article_order,
    )

    background_tasks.add_task(_finalize_task, session_id)
    return {"status": "finalizing"}


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
                                  SessionStatus.CRAWL_DONE, SessionStatus.RECOMMEND_DONE,
                                  SessionStatus.REVIEW_READY):
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

    return templates.TemplateResponse(request, "review.html", {
        "session": session,
        "categories": categories,
        "rec_map": rec_map,
    })


@router.get("/review-index/{session_id}", response_class=HTMLResponse)
async def review_index_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return templates.TemplateResponse(request, "review_index.html", {
        "session": session,
    })


@router.get("/progress/{session_id}", response_class=HTMLResponse)
async def progress_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return templates.TemplateResponse(request, "progress.html", {
        "session": session,
    })


@router.get("/result/{session_id}", response_class=HTMLResponse)
async def result_page(request: Request, session_id: str):
    sessions = _get_sessions()
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return templates.TemplateResponse(request, "result.html", {
        "session": session,
    })
