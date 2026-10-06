# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KherveRef.

One-folder build (fast start, no temp extraction):

    dist/KherveRef/KherveRef(.exe) + _internal/      Windows / Linux
    dist/KherveRef.app                                macOS

Build:
    pip install -r requirements.txt pyinstaller pillow
    python packaging/generate_icon.py
    pyinstaller KherveRef.spec --noconfirm

Normally run through packaging/build_installer.py (Windows) or
packaging/build_macos.py (macOS), which add the installer / DMG; then
    python packaging/smoke_test.py <exe> --version <ver>

The same executable is also the MCP server (`KherveRef --mcp-server`).
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))
from build_info import stamp  # noqa: E402

# "<major>.<minor>.<commit count>": stamps kherveref/BUILD, which the
# frozen app reads for its title bar, About box and --version.
VERSION = stamp()

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
    datas=datas + [(str(ROOT / "kherveref" / "styles"), "kherveref/styles"),
                   (str(ROOT / "kherveref" / "word_manifest.xml"), "kherveref"),
                   (str(ROOT / "kherveref" / "BUILD"), "kherveref"),
                   # The Word panel's page, served by KherveRef itself.
                   (str(ROOT / "docs" / "word"), "kherveref/word_panel"),
                   (str(ROOT / "docs" / "guide"), "kherveref/guide")],
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
        version=VERSION,
        info_plist={
            "CFBundleName": "KherveRef",
            "CFBundleDisplayName": "KherveRef",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
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
