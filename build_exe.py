#!/usr/bin/env python3
"""Build the standalone 더벨 News Clipper executable.

Usage:
    python build_exe.py            # one-file executable (easiest to hand out)
    python build_exe.py --onedir   # folder build (starts faster)
    python build_exe.py --clean    # wipe build caches first

The result lands in dist/. Ship it together with nothing else — the .env file,
output/ and browser_profile/ are created next to the executable on first run.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
EXE_NAME = "TheBellNewsClipper" + (".exe" if sys.platform == "win32" else "")


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("PyInstaller가 없어 설치합니다...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])


def check_requirements() -> None:
    """Fail early with a clear message rather than mid-build."""
    missing = []
    for mod, pkg in [
        ("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("jinja2", "jinja2"),
        ("pydantic_settings", "pydantic-settings"), ("selenium", "selenium"),
        ("anthropic", "anthropic"), ("pypdf", "pypdf"), ("reportlab", "reportlab"),
        ("docx", "python-docx"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("다음 패키지가 없습니다:", ", ".join(missing))
        print("먼저 실행하세요:  pip install -r requirements.txt")
        raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onedir", action="store_true",
                    help="폴더 형태로 빌드 (실행이 빠름, 배포는 폴더 통째로)")
    ap.add_argument("--clean", action="store_true", help="빌드 캐시 삭제 후 진행")
    args = ap.parse_args()

    check_requirements()
    ensure_pyinstaller()

    if args.clean:
        for path in (BUILD, DIST):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                print(f"삭제: {path}")

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           str(ROOT / "thebell_clipper.spec")]
    if args.onedir:
        # The spec builds one-file; --onedir needs the distpath split instead.
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "TheBellNewsClipper",
               "--icon", str(ROOT / "app" / "static" / "img" / "thebell.ico"),
               "--add-data", f"{ROOT / 'app' / 'templates'}{';' if sys.platform == 'win32' else ':'}app/templates",
               "--add-data", f"{ROOT / 'app' / 'static'}{';' if sys.platform == 'win32' else ':'}app/static",
               "--collect-data", "selenium", "--collect-data", "reportlab", "--collect-data", "docx",
               "--hidden-import", "uvicorn.loops.auto",
               "--hidden-import", "uvicorn.protocols.http.auto",
               "--hidden-import", "uvicorn.lifespan.on",
               "--hidden-import", "app.routers.setup",
               str(ROOT / "launcher.py")]

    print("빌드 시작... (몇 분 걸립니다)\n")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("\n빌드 실패.")
        return result.returncode

    target = DIST / ("TheBellNewsClipper" if args.onedir else EXE_NAME)
    print("\n" + "=" * 56)
    print("  빌드 완료!")
    print(f"  결과: {target}")
    if target.exists() and target.is_file():
        print(f"  크기: {target.stat().st_size / 1_000_000:.0f} MB")
    print("=" * 56)
    print("\n실행하면 브라우저가 열리고, 첫 화면에서 Claude API 키를 입력하면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
