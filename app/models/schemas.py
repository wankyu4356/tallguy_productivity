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
    error: str = ""
    zip_path: str = ""
    date_from: datetime | None = None
    date_to: datetime | None = None
