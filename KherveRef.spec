# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KherveRef.

One-folder build (fast start, no temp extraction):

    dist/KherveRef/KherveRef(.exe) + _internal/      Windows / Linux
    dist/KherveRef.app                                macOS

Build:
    pip install -r requirements.txt pyinstaller pillow
    python packaging/generate_icon.py
    pyinstaller KherveRef.spec --noconfirm
    python packaging/smoke_test.py            # checks the result

The same executable is also the MCP server (`KherveRef --mcp-server`).
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from kherveref import __version__  # noqa: E402

ICO = ROOT / "build" / "KherveRef.ico"
PNG = ROOT / "build" / "KherveRef.png"
if not ICO.exists() or not PNG.exists():
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "packaging" / "generate_icon.py")],
                   check=True)

datas, binaries, hiddenimports = [], [], []
for pkg in ("qtawesome", "pymupdf", "pygit2", "certifi", "citeproc"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "KherveRef.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas + [(str(ROOT / "kherveref" / "styles"), "kherveref/styles")],
    hiddenimports=hiddenimports + [
        "kherveref.mcp_server", "kherveref.smoke", "kherveref.zotero",
        "PySide6.QtNetwork", "sqlite3",
    ],
    excludes=["tkinter", "PyQt5", "PyQt6", "matplotlib", "scipy", "pandas",
              "IPython", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="KherveRef",
    console=False,
    icon=str(PNG if sys.platform == "darwin" else ICO),
)
coll = COLLECT(exe, a.binaries, a.datas, name="KherveRef")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="KherveRef.app",
        icon=str(PNG),
        bundle_identifier="com.kherve.KherveRef",
        version=__version__,
        info_plist={
            "CFBundleName": "KherveRef",
            "CFBundleDisplayName": "KherveRef",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # Double-clicking a library's .kref opens it in KherveRef.
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "KherveRef library",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": ["com.kherve.kherveref.library"],
            }],
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "com.kherve.kherveref.library",
                "UTTypeDescription": "KherveRef library",
                "UTTypeConformsTo": ["public.json", "public.data"],
                "UTTypeTagSpecification": {"public.filename-extension": ["kref"]},
            }],
        },
    )
