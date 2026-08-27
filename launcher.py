"""Entry point for the packaged 더벨 News Clipper executable.

Double-clicking the .exe starts a local web server, picks a port that is
actually free, and opens the browser on it. Everything the app writes (.env,
output/, browser_profile/) lands next to the executable.
"""
from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import traceback

BANNER = r"""
  ┌──────────────────────────────────────────────┐
  │   더벨 News Clipper                          │
  │   Daily News Clipping · Gateway to Capital   │
  └──────────────────────────────────────────────┘
"""


def find_free_port(preferred: int = 8000, attempts: int = 60) -> int:
    """Return a bindable localhost port, starting from `preferred`."""
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # Nothing in the range was free — let the OS assign one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pause_on_exit(message: str = "") -> None:
    """Keep the console window open so the user can read the error."""
    if message:
        print(message)
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\n종료하려면 Enter 키를 누르세요...")
        except (EOFError, KeyboardInterrupt):
            pass


def main() -> int:
    print(BANNER)

    # Resolve the port before importing the app: Settings reads PORT at import
    # time and the lifespan hook uses it to open the browser.
    preferred = int(os.environ.get("PORT", "8000") or 8000)
    port = find_free_port(preferred)
    os.environ["PORT"] = str(port)
    if port != preferred:
        print(f"  포트 {preferred}번이 사용 중이라 {port}번으로 시작합니다.")

    try:
        import uvicorn

        from app.main import app
        from app.utils.paths import app_dir, is_frozen
    except Exception:
        traceback.print_exc()
        _pause_on_exit("\n[오류] 프로그램을 불러오지 못했습니다.")
        return 1

    print(f"  작업 폴더 : {app_dir()}")
    print(f"  주소      : http://localhost:{port}")
    print(f"  실행 방식 : {'실행 파일' if is_frozen() else '소스'}")
    print("\n  브라우저가 자동으로 열립니다. 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.\n")

    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            log_level=os.environ.get("LOG_LEVEL", "info").lower(),
            access_log=False,
        )
    except KeyboardInterrupt:
        print("\n종료합니다.")
    except Exception:
        traceback.print_exc()
        _pause_on_exit("\n[오류] 서버 실행 중 문제가 발생했습니다.")
        return 1
    return 0


if __name__ == "__main__":
    # Required so PyInstaller-built binaries don't re-run main() in subprocesses.
    multiprocessing.freeze_support()
    sys.exit(main())
