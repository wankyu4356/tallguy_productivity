# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 더벨 News Clipper.

Hardened so the shipped binary does not carry readable Python source:

* optimize=2   — bytecode is compiled with -OO; docstrings and asserts are
                 stripped out of every module.
* our package is stored pyc-only (module_collection_mode below), so no .py
  files for the app's own code ever land in the bundle.
* noarchive stays False, so modules live inside the packed archive rather than
  as loose files next to the executable.
* strip=True removes symbols from the bundled shared libraries.

This stops "open the exe and read the code". It does NOT make the program
un-reverse-engineerable — nothing pure-Python can. For real obfuscation build
with:  python build_exe.py --obfuscate  (requires a PyArmor license).

Build with:  python build_exe.py       (or: pyinstaller thebell_clipper.spec)
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

# --- Bundled assets (these are meant to be readable: HTML, CSS, fonts) -----
datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
]
for pkg in ("selenium", "reportlab", "docx"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# --- Imports PyInstaller can't see through dynamic dispatch ----------------
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    "app.routers.health", "app.routers.clipper", "app.routers.setup",
    # selenium >= 4.28 resolves webdriver.Edge lazily through importlib, so
    # these never show up in the import graph. Named explicitly so a failure
    # of collect_submodules below can't silently drop the browser.
    "selenium.webdriver.edge.webdriver",
    "selenium.webdriver.edge.options",
    "selenium.webdriver.edge.service",
    "selenium.webdriver.chromium.webdriver",
    "selenium.webdriver.chromium.options",
    "selenium.webdriver.chromium.service",
    "selenium.webdriver.remote.webdriver",
    "selenium.webdriver.common.keys",
    "selenium.webdriver.common.by",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
]
for pkg in ("pydantic", "pydantic_settings", "anthropic", "selenium", "pypdf", "docx"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception as exc:  # keep building, but say what was skipped
        print(f"[spec] WARNING: collect_submodules({pkg!r}) failed: {exc}")

icon_path = ROOT / "app" / "static" / "img" / "thebell.ico"

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "IPython", "test", "tests"],
    noarchive=False,       # keep modules inside the packed archive
    optimize=2,            # -OO: strip docstrings + asserts from bytecode
)

# Ship the app's own code as bytecode only — never source.
a.module_collection_mode = {"app": "pyz", "launcher": "pyz"}

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TheBellNewsClipper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,             # strip symbols from bundled libs
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=True,   # don't dump tracebacks/paths to viewers
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
