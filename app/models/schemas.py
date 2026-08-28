from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class CrawlCategory(str, Enum):
    DEAL = "deal"
    FINANCE = "finance"
    INVEST = "invest"
    INDUSTRY = "industry"


class ArticleInfo(BaseModel):
    id: str = ""
    title: str
    url: str
    category: str
    subcategory: str = ""
    published_at: datetime | None = None
    summary: str = ""


class ArticleWithContent(BaseModel):
    info: ArticleInfo
    content: str = ""
    pdf_path: str = ""


class ArticleRecommendation(BaseModel):
    article_id: str
    recommended: bool = False
    reason: str = ""


class ClassificationCategory(BaseModel):
    name: str
    subcategories: list[ClassificationSubcategory] = []
    articles: list[str] = []  # article IDs


class ClassificationSubcategory(BaseModel):
    name: str
    sub_items: list[ClassificationSubItem] = []
    articles: list[str] = []  # article IDs


class ClassificationSubItem(BaseModel):
    name: str
    articles: list[str] = []  # article IDs


class ClassifiedOutput(BaseModel):
    categories: list[ClassificationCategory] = []
    article_order: list[str] = []  # ordered article IDs
    is_fallback: bool = False  # True if LLM classification failed
    fallback_reason: str = ""  # 실패 원인


class SessionStatus(str, Enum):
    IDLE = "idle"
    CRAWLING = "crawling"
    CRAWL_DONE = "crawl_done"
    RECOMMENDING = "recommending"
    RECOMMEND_DONE = "recommend_done"
    SELECTED = "selected"
    GENERATING = "generating"
    REVIEW_READY = "review_ready"
    FINALIZING = "finalizing"
    DONE = "done"
    ERROR = "error"


class ProgressState(BaseModel):
    """Machine-readable progress so the UI can show a real bar and an ETA.

    The message log tells the user *what* is happening; this tells them
    *how far along* it is, which is what makes a multi-minute wait bearable.
    """
    phase: str = ""          # crawl | recommend | fetch | classify | finalize
    label: str = ""          # short human label for the phase
    detail: str = ""         # what is being worked on right now
    current: int = 0
    total: int = 0
    started_at: float = 0.0  # epoch seconds, for elapsed/ETA on the client

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, round(self.current / self.total * 100))


class SessionState(BaseModel):
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    created_at: datetime = Field(default_factory=datetime.now)
    articles: list[ArticleInfo] = []
    recommendations: list[ArticleRecommendation] = []
    selected_ids: list[str] = []
    articles_with_content: list[ArticleWithContent] = []
    classification: ClassifiedOutput | None = None
    progress_messages: list[str] = []
    progress: ProgressState = Field(default_factory=ProgressState)
    error: str = ""
    zip_path: str = ""
    date_from: datetime | None = None
    date_to: datetime | None = None
