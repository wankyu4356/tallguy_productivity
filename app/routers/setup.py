"""First-run setup: let the user supply credentials from the web UI.

The packaged executable ships without a .env file, so rather than failing at
startup the app sends the user here and writes the file for them.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import DEFAULT_CLAUDE_MODEL, reload_settings, settings
from app.utils.logging import get_logger
from app.utils.paths import app_dir, env_file
from app.utils.templating import create_templates

logger = get_logger(__name__)
router = APIRouter()
templates = create_templates()

# Only these keys are writable from the UI.
_EDITABLE = ("ANTHROPIC_API_KEY", "THEBELL_ID", "THEBELL_PW", "CLAUDE_MODEL")


class SetupRequest(BaseModel):
    ANTHROPIC_API_KEY: str = ""
    THEBELL_ID: str = ""
    THEBELL_PW: str = ""
    CLAUDE_MODEL: str = ""


def _read_env() -> dict[str, str]:
    path = env_file()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _write_env(updates: dict[str, str]) -> None:
    """Merge `updates` into .env, keeping any keys we don't manage."""
    values = _read_env()
    values.update({k: v for k, v in updates.items() if v})

    lines = [
        "# 더벨 News Clipper 설정",
        "# 이 파일은 설정 화면에서 자동으로 생성됩니다.",
        "",
        f"ANTHROPIC_API_KEY={values.get('ANTHROPIC_API_KEY', '')}",
        "",
        "# 더벨 로그인 (입력하면 자동 로그인, 비우면 브라우저에서 직접 로그인)",
        f"THEBELL_ID={values.get('THEBELL_ID', '')}",
        f"THEBELL_PW={values.get('THEBELL_PW', '')}",
        "",
        f"CLAUDE_MODEL={values.get('CLAUDE_MODEL', DEFAULT_CLAUDE_MODEL)}",
    ]
    for key, val in values.items():
        if key not in _EDITABLE:
            lines.append(f"{key}={val}")

    path = env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"설정 저장: {path}")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "•" * len(value)
    return f"{value[:7]}{'•' * 12}{value[-4:]}"


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html", {
        "configured": settings.is_configured,
        "api_key_masked": _mask(settings.ANTHROPIC_API_KEY),
        "thebell_id": settings.THEBELL_ID,
        "has_thebell_pw": bool(settings.THEBELL_PW),
        "claude_model": settings.CLAUDE_MODEL,
        "env_path": str(env_file()),
        "app_dir": str(app_dir()),
    })


@router.post("/api/setup")
async def save_setup(body: SetupRequest):
    updates = {k: getattr(body, k).strip() for k in _EDITABLE}
    _write_env(updates)
    reload_settings()
    return {
        "status": "ok",
        "configured": settings.is_configured,
        "env_path": str(env_file()),
    }


class TestRequest(BaseModel):
    """A key/model pair to verify — falls back to what is already saved."""
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = ""


def _probe(api_key: str, model: str) -> tuple[bool, str]:
    """Send the cheapest possible request to confirm the credentials work."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return True, f"연결됐어요 · {model}"
    except anthropic.AuthenticationError:
        return False, "API 키가 올바르지 않아요. 다시 확인해 주세요."
    except anthropic.PermissionDeniedError:
        return False, "이 API 키로는 접근할 수 없어요. 권한을 확인해 주세요."
    except anthropic.NotFoundError:
        return False, f"모델을 찾을 수 없어요: {model}"
    except anthropic.RateLimitError:
        return True, "키는 정상이지만 지금 요청이 몰려 있어요. 잠시 후 사용하세요."
    except anthropic.APIConnectionError:
        return False, "인터넷에 연결되지 않았어요."
    except Exception as e:  # noqa: BLE001 - surface anything else verbatim
        return False, f"확인 실패: {type(e).__name__}"


@router.post("/api/setup/test")
async def test_connection(body: TestRequest):
    api_key = body.ANTHROPIC_API_KEY.strip() or settings.ANTHROPIC_API_KEY
    model = body.CLAUDE_MODEL.strip() or settings.CLAUDE_MODEL
    if not api_key:
        return {"ok": False, "message": "먼저 API 키를 입력해 주세요."}

    ok, message = await asyncio.to_thread(_probe, api_key, model)
    logger.info(f"연결 테스트: {'성공' if ok else '실패'} — {message}")
    return {"ok": ok, "message": message}
