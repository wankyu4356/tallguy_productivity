# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 더벨 News Clipper.

Build with:  python build_exe.py       (or: pyinstaller thebell_clipper.spec)
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

# --- Bundled assets -------------------------------------------------------
datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
]

# Third-party packages that load files from disk at runtime.
#   selenium  → the Selenium Manager binary that downloads the driver
#   reportlab → built-in font metrics
#   docx      → default.docx used by Document()
for pkg in ("selenium", "reportlab", "docx"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# --- Imports PyInstaller can't see through dynamic dispatch ---------------
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    "app.routers.health",
    "app.routers.clipper",
    "app.routers.setup",
]
for pkg in ("pydantic", "pydantic_settings", "anthropic", "selenium", "pypdf", "docx"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

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
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "IPython"],
    noarchive=False,
)

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
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # keep the console: it shows progress and errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
