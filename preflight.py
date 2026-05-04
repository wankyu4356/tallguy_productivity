#!/usr/bin/env python3
"""더벨 News Clipper - Preflight Check

서버 실행 전 필수 환경을 사전 검증합니다.
Usage: python preflight.py [command]
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

HELP_TEXT = """\
=======================================================
  더벨 News Clipper - 사용 가이드
=======================================================

사용법: python preflight.py [command]

Commands:
  (없음)        환경 검증 후 서버 시작
  help          이 도움말 표시
  check         환경 검증만 실행 (서버 시작 안 함)
  install       필수 패키지 일괄 설치
  server        검증 없이 서버 바로 시작

-------------------------------------------------------
초기 설치 (처음 한 번만):
-------------------------------------------------------
  1. pip install -r requirements.txt
  2. copy .env.example .env        (Windows)
     cp .env.example .env          (Mac/Linux)
  3. .env 파일 열어서 값 입력:
     - ANTHROPIC_API_KEY=sk-ant-...  (필수)
     - THEBELL_ID=더벨_아이디        (선택, 자동 로그인용)
     - THEBELL_PW=더벨_비밀번호      (선택, 자동 로그인용)

-------------------------------------------------------
빠른 설치:
-------------------------------------------------------
  python preflight.py install

-------------------------------------------------------
서버 실행:
-------------------------------------------------------
  python preflight.py              검증 후 시작
  python preflight.py server       바로 시작

  서버 시작 후 브라우저에서 http://localhost:8000 접속

-------------------------------------------------------
환경 검증 항목:
-------------------------------------------------------
  [1] Python 버전    >= 3.10 필요
  [2] 필수 패키지    requirements.txt의 모든 패키지
  [3] Timezone       Asia/Seoul (Windows: tzdata 필요)
  [4] .env 파일      프로젝트 루트에 존재 여부
  [5] 환경변수       ANTHROPIC_API_KEY (필수), THEBELL_ID/PW (선택, 없으면 수동 로그인)
  [6] Selenium       Chrome 자동 관리 (Selenium Manager)

=======================================================
"""

PROJECT_ROOT = Path(__file__).parent

# pip 패키지명 → import 모듈명 매핑
PACKAGE_IMPORT_MAP = {
    "fastapi": "fastapi",
    "uvicorn[standard]": "uvicorn",
    "jinja2": "jinja2",
    "python-multipart": "multipart",
    "selenium": "selenium",
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

REQUIRED_ENV_VARS = ["ANTHROPIC_API_KEY"]
OPTIONAL_ENV_VARS = ["THEBELL_ID", "THEBELL_PW"]


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
    # 선택 변수 안내 (실패가 아닌 정보)
    optional_missing = [v for v in OPTIONAL_ENV_VARS if not os.getenv(v)]
    if optional_missing:
        print_result(
            f"환경변수 ({len(REQUIRED_ENV_VARS)}개 필수 설정됨, 수동 로그인 모드)",
            True,
        )
        print(f"         ℹ️  {', '.join(optional_missing)} 미설정 → 브라우저에서 직접 로그인")
        return True
    return print_result(f"환경변수 (모두 설정됨, 자동 로그인 모드)", True)


def check_selenium():
    """Selenium import 확인. Chrome/ChromeDriver는 Selenium Manager가 자동 관리."""
    try:
        import selenium
        return print_result(
            f"Selenium ({selenium.__version__}, Chrome 자동 관리)",
            True,
        )
    except ImportError:
        return print_result(
            "Selenium",
            False,
            "pip install -r requirements.txt",
        )


def run_checks():
    """모든 검증을 실행하고 결과를 반환한다."""
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
        check_selenium,
    ]

    results = [check() for check in checks]
    all_passed = all(results)

    print()
    print("=" * 55)

    if all_passed:
        print("  모든 체크 통과! (All checks passed)")
    else:
        failed = results.count(False)
        print(f"  {failed}개 항목 실패. 위의 안내를 따라 수정 후 다시 실행하세요.")
        print(f"  도움말: python preflight.py help")

    print("=" * 55)
    return all_passed


def cmd_install():
    """필수 패키지 일괄 설치."""
    print("=" * 55)
    print("  필수 패키지 설치 중...")
    print("=" * 55)
    print()

    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    if rc != 0:
        print("\n  pip install 실패. 위 오류를 확인하세요.")
        sys.exit(1)

    print()
    print("=" * 55)
    print("  설치 완료!")
    print("  다음 단계: .env 파일 설정 후 python preflight.py 실행")
    print("  (Chrome/ChromeDriver는 첫 실행 시 자동으로 다운로드됩니다)")
    print("=" * 55)


def start_server():
    """uvicorn 서버를 시작한다. 포트가 사용 중이면 자동으로 다른 포트 사용."""
    import socket
    import uvicorn

    port = 8000
    while port < 8100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                break
        print(f"포트 {port} 사용 중 → 다음 포트 시도...")
        port += 1

    os.environ["PORT"] = str(port)
    print(f"서버 시작: http://localhost:{port}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    if cmd in ("help", "-h", "--help", "/?"):
        print(HELP_TEXT)
        return

    if cmd == "install":
        cmd_install()
        return

    if cmd == "server":
        start_server()
        return

    if cmd == "check":
        ok = run_checks()
        sys.exit(0 if ok else 1)

    # 기본: 검증 후 서버 시작
    ok = run_checks()
    if not ok:
        sys.exit(1)

    answer = input("\n  서버를 시작할까요? (Y/n): ").strip().lower()
    if answer in ("", "y", "yes"):
        print()
        start_server()


if __name__ == "__main__":
    main()
