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


OBF_DIR = ROOT / "build" / "obf"
_ALLOW_TRIAL = False

# PyArmor's trial license has a ~40KB cumulative code budget, so a trial build
# can only protect the crown-jewel module rather than the whole tree. This is
# the file with the real IP — the classification prompts and logic. With a paid
# license, expand this to the full package (or obfuscate `app` recursively).
OBF_FILES = ["app/services/llm_classifier.py"]


def obfuscate() -> Path:
    """Obfuscate the app package + launcher with PyArmor, returning the build root.

    Refuses to proceed on a trial license: trial-obfuscated code is watermarked,
    time-limited and not licensed for distribution, so shipping it would hand the
    user a legal and reliability problem. A purchased license lifts both.
    """
    try:
        out = subprocess.run(["pyarmor", "--version"], capture_output=True, text=True)
        banner = (out.stdout + out.stderr)
    except FileNotFoundError:
        print("PyArmor가 설치되어 있지 않습니다.  pip install pyarmor  후 라이선스를 등록하세요.")
        raise SystemExit(1)

    if "trial" in banner.lower():
        if not _ALLOW_TRIAL:
            print("=" * 60)
            print("  PyArmor가 체험판(trial) 상태입니다.")
            print("  체험판 난독화 코드는 워터마크가 찍히고 배포가 금지됩니다.")
            print("  개인용/테스트용으로 진행하려면 --allow-trial 을 붙이세요.")
            print("  배포하려면 정식 라이선스 등록:  pyarmor reg <license>")
            print("=" * 60)
            raise SystemExit(1)
        print("=" * 60)
        print("  ⚠  PyArmor 체험판으로 난독화합니다 (개인용/테스트용).")
        print("     이 빌드는 배포하지 마세요 — 체험판은 배포 라이선스가 없습니다.")
        print("=" * 60)

    if OBF_DIR.exists():
        shutil.rmtree(OBF_DIR, ignore_errors=True)
    OBF_DIR.mkdir(parents=True, exist_ok=True)

    # A full plain copy of the app to build against — obfuscated files are then
    # swapped in over the top, so unprotected modules still ship as bytecode.
    shutil.copytree(ROOT / "app", OBF_DIR / "app")
    shutil.copy2(ROOT / "launcher.py", OBF_DIR / "launcher.py")

    stage = OBF_DIR / "_pa"
    runtime_copied = False
    for rel in OBF_FILES:
        print(f"PyArmor 난독화: {rel}")
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        subprocess.check_call([
            "pyarmor", "gen", "--output", str(stage), str(ROOT / rel),
        ])
        # Swap the obfuscated module in over the plain copy.
        name = Path(rel).name
        shutil.copy2(stage / name, OBF_DIR / rel)
        # The PyArmor runtime is shared across all obfuscated modules.
        if not runtime_copied:
            for rt in stage.glob("pyarmor_runtime_*"):
                shutil.copytree(rt, OBF_DIR / rt.name)
                runtime_copied = True
    shutil.rmtree(stage, ignore_errors=True)

    print(f"난독화 완료: {len(OBF_FILES)}개 모듈 → {OBF_DIR}")
    return OBF_DIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onedir", action="store_true",
                    help="폴더 형태로 빌드 (실행이 빠름, 배포는 폴더 통째로)")
    ap.add_argument("--clean", action="store_true", help="빌드 캐시 삭제 후 진행")
    ap.add_argument("--obfuscate", action="store_true",
                    help="PyArmor로 코드를 난독화한 뒤 빌드 (PyArmor 라이선스 필요)")
    ap.add_argument("--allow-trial", action="store_true",
                    help="PyArmor 체험판으로도 난독화 진행 (개인용/테스트용, 배포 금지)")
    args = ap.parse_args()

    check_requirements()
    ensure_pyinstaller()

    if args.clean:
        for path in (BUILD, DIST):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                print(f"삭제: {path}")

    obf_root = None
    if args.obfuscate:
        global _ALLOW_TRIAL
        _ALLOW_TRIAL = args.allow_trial
        obf_root = obfuscate()

    if obf_root is not None:
        # Build the obfuscated tree. The PyArmor runtime package must ride along.
        sep = ";" if sys.platform == "win32" else ":"
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
               "--name", "TheBellNewsClipper",
               "--icon", str(ROOT / "app" / "static" / "img" / "thebell.ico"),
               "--distpath", str(DIST), "--workpath", str(BUILD / "pyi"),
               "--paths", str(obf_root),
               "--add-data", f"{ROOT / 'app' / 'templates'}{sep}app/templates",
               "--add-data", f"{ROOT / 'app' / 'static'}{sep}app/static",
               "--collect-data", "selenium", "--collect-data", "reportlab", "--collect-data", "docx",
               # selenium resolves webdriver.Edge lazily via importlib, so its
               # submodules are invisible to static analysis. Same for the
               # pydantic/anthropic plugin lookups.
               "--collect-submodules", "selenium",
               "--collect-submodules", "anthropic",
               "--collect-submodules", "pydantic",
               "--collect-submodules", "pydantic_settings",
               "--collect-all", "pyarmor_runtime_000000",
               "--hidden-import", "uvicorn.loops.auto",
               "--hidden-import", "uvicorn.protocols.http.auto",
               "--hidden-import", "uvicorn.lifespan.on",
               "--hidden-import", "app.routers.setup",
               "--strip", "--console",
               str(obf_root / "launcher.py")]
        print("빌드 시작 (난독화)... (몇 분 걸립니다)\n")
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print("\n빌드 실패."); return result.returncode
        _report(DIST / EXE_NAME, obfuscated=True)
        return 0

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
               "--collect-submodules", "selenium",
               "--collect-submodules", "anthropic",
               "--collect-submodules", "pydantic",
               "--collect-submodules", "pydantic_settings",
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
    _report(target, obfuscated=False)
    return 0


def _report(target: Path, obfuscated: bool) -> None:
    print("\n" + "=" * 56)
    print("  빌드 완료!" + ("  (난독화됨)" if obfuscated else ""))
    print(f"  결과: {target}")
    if target.exists() and target.is_file():
        print(f"  크기: {target.stat().st_size / 1_000_000:.0f} MB")
    if not obfuscated:
        print("  보호: 소스(.py) 미포함 · 바이트코드 -OO 컴파일 · 심볼 제거")
    print("=" * 56)
    print("\n실행하면 브라우저가 열리고, 첫 화면에서 Claude API 키를 입력하면 됩니다.")


if __name__ == "__main__":
    raise SystemExit(main())
