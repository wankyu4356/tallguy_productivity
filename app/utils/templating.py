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


def templates_dir() -> Path:
    return resource_path("app", "templates")


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=templates_dir())
    templates.env.cache = _NoOpCache()  # type: ignore[assignment]
    return templates
