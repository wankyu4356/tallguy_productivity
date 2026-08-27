"""Path resolution that works both from source and from a packaged executable.

PyInstaller unpacks bundled read-only files into a temporary directory
(``sys._MEIPASS``) that disappears when the process exits, so anything the app
needs to *keep* — the .env file, generated output, the browser profile — has to
live next to the executable instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repository root when running from source (…/app/utils/paths.py → …/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Directory holding bundled read-only assets (templates, static, fonts)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def app_dir() -> Path:
    """Writable directory that survives restarts — sits next to the executable."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def resource_path(*parts: str) -> Path:
    """Path to a bundled asset, e.g. resource_path('app', 'templates')."""
    return resource_dir().joinpath(*parts)


def data_path(*parts: str) -> Path:
    """Path to a writable file/directory, e.g. data_path('output')."""
    return app_dir().joinpath(*parts)


def env_file() -> Path:
    """Location of the .env file the user edits."""
    return app_dir() / ".env"
