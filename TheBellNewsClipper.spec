# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = [('/home/user/tallguy_productivity/app/templates', 'app/templates'), ('/home/user/tallguy_productivity/app/static', 'app/static')]
binaries = []
hiddenimports = ['uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.lifespan.on', 'app.routers.setup']
datas += collect_data_files('selenium')
datas += collect_data_files('reportlab')
datas += collect_data_files('docx')
tmp_ret = collect_all('pyarmor_runtime_000000')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['/home/user/tallguy_productivity/build/obf/launcher.py'],
    pathex=['/home/user/tallguy_productivity/build/obf'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TheBellNewsClipper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/home/user/tallguy_productivity/app/static/img/thebell.ico'],
)
