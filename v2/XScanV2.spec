# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).resolve()
repo = root.parent

datas = [(str(repo / "app.ico"), ".")]
web_root = root / "xscan" / "web"
for source in web_root.rglob("*"):
    if not source.is_file():
        continue
    relative = source.relative_to(web_root)
    if relative.parts and relative.parts[0] in {"downloads", "screenshots"}:
        continue
    datas.append((str(source), str(Path("xscan/web") / relative.parent)))
binaries = []

a = Analysis(
    [str(root / "run_xscan.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XScanV2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(repo / "app.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="XScanV2")
