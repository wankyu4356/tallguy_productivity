"""Shared Jinja2 template factory."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.utils.paths import resource_path


class _NoOpCache:
    """Disable Jinja2's template cache.

    On Python 3.14 Jinja2 builds a cache key of ``(name, globals_dict)``, which
    is unhashable and raises on every render. Skipping the cache sidesteps it.
    """

    def get(self, key, default=None):
        return default

    def __setitem__(self, key, value):
        pass

    def __contains__(self, key):
        return False

    def clear(self):
        pass


# Raw status values are implementation detail — never show them to the user.
STATUS_LABELS = {
    "idle": "대기 중",
    "crawling": "기사 수집 중",
    "crawl_done": "수집 완료",
    "recommending": "AI 추천 중",
    "recommend_done": "추천 완료",
    "selected": "기사 선택됨",
    "generating": "PDF 생성 중",
    "review_ready": "목차 검수 대기",
    "finalizing": "마무리 중",
    "done": "완료",
    "error": "오류",
}


def status_label(value) -> str:
    """Human-readable Korean label for a SessionStatus."""
    key = getattr(value, "value", value)
    return STATUS_LABELS.get(str(key), str(key))


def templates_dir() -> Path:
    return resource_path("app", "templates")


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=templates_dir())
    templates.env.cache = _NoOpCache()  # type: ignore[assignment]
    templates.env.filters["status_label"] = status_label
    return templates
