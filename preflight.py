#!/usr/bin/env python3
"""더벨 News Clipper - Preflight Check

서버 실행 전 필수 환경을 사전 검증합니다.
Usage: python preflight.py
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

# pip 패키지명 → import 모듈명 매핑
PACKAGE_IMPORT_MAP = {
    "fastapi": "fastapi",
    "uvicorn[standard]": "uvicorn",
    "jinja2": "jinja2",
    "python-multipart": "multipart",
    "playwright": "playwright",
    "anthropic": "anthropic",
    "pypdf": "pypdf",
    "reportlab": "reportlab",
    "python-docx": "docx",
    "holidays": "holidays",
    "python-dateutil": "dateutil",
    "pydantic-settings": "pydantic_settings",
    "aiofiles": "aiofiles",
    "python-dotenv": "dotenv",
    "httpx": "httpx",
    "tzdata": "tzdata",
}

REQUIRED_ENV_VARS = ["THEBELL_ID", "THEBELL_PW", "ANTHROPIC_API_KEY"]


def print_result(name, passed, fix_hint=None):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status} {name}")
    if not passed and fix_hint:
        print(f"         -> {fix_hint}")
    return passed


def check_python_version():
    ver = sys.version_info
    ok = ver >= (3, 10)
    return print_result(
        f"Python 버전 (현재: {ver.major}.{ver.minor}.{ver.micro})",
        ok,
        "Python 3.10 이상을 설치하세요: https://www.python.org/downloads/",
    )


def check_packages():
    missing = []
    for pip_name, import_name in PACKAGE_IMPORT_MAP.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        return print_result(
            f"필수 패키지 ({len(missing)}개 누락: {', '.join(missing)})",
            False,
            "pip install -r requirements.txt",
        )
    return print_result(f"필수 패키지 ({len(PACKAGE_IMPORT_MAP)}개 모두 설치됨)", True)


def check_timezone():
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Seoul")
        return print_result("Timezone 데이터 (Asia/Seoul)", True)
    except Exception:
        return print_result(
            "Timezone 데이터 (Asia/Seoul)",
            False,
            "pip install tzdata",
        )


def check_env_file():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        return print_result(".env 파일", True)
    hint = "copy .env.example .env" if sys.platform == "win32" else "cp .env.example .env"
    return print_result(".env 파일", False, f"{hint}  후 값을 입력하세요")


def check_env_vars():
    # .env 파일 로드
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass

    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        return print_result(
            f"환경변수 ({', '.join(missing)} 미설정)",
            False,
            ".env 파일에 값을 입력하세요",
        )
    return print_result(f"환경변수 ({len(REQUIRED_ENV_VARS)}개 모두 설정됨)", True)


def check_playwright_browsers():
    # Playwright 브라우저 캐시 디렉토리에서 chromium 확인
    try:
        if sys.platform == "win32":
            cache_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        elif sys.platform == "darwin":
            cache_dir = Path.home() / "Library" / "Caches" / "ms-playwright"
        else:
            cache_dir = Path.home() / ".cache" / "ms-playwright"

        chromium_dirs = list(cache_dir.glob("chromium-*")) if cache_dir.exists() else []
        if chromium_dirs:
            return print_result("Playwright Chromium 브라우저", True)
    except Exception:
        pass

    return print_result(
        "Playwright Chromium 브라우저",
        False,
        "python -m playwright install chromium",
    )


def main():
    print("=" * 55)
    print("  더벨 News Clipper - Preflight Check")
    print("=" * 55)
    print()

    checks = [
        check_python_version,
        check_packages,
        check_timezone,
        check_env_file,
        check_env_vars,
        check_playwright_browsers,
    ]

    results = [check() for check in checks]
    all_passed = all(results)

    print()
    print("=" * 55)

    if all_passed:
        print("  모든 체크 통과! (All checks passed)")
        print("=" * 55)
        answer = input("\n  서버를 시작할까요? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            print()
            import uvicorn

            uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
    else:
        failed = results.count(False)
        print(f"  {failed}개 항목 실패. 위의 안내를 따라 수정 후 다시 실행하세요.")
        print("=" * 55)
        sys.exit(1)


if __name__ == "__main__":
    main()
