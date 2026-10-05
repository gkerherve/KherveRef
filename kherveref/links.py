"""Opening attachments: in KhervePDF when it can be found, otherwise in
the system's default viewer.

KhervePDF is a separate app; it is found the same way KherveTeX finds
KhervePaint — a path the user chose, the usual install locations, then
a source checkout next to this one (run with its own venv).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QUrl
from PySide6.QtGui import QDesktopServices

SETTINGS = ("kherve", "KherveRef")
PATH_KEY = "khervepdf/path"
USE_KEY = "khervepdf/use"


def _source_checkout() -> Path:
    return Path(__file__).resolve().parents[2] / "KhervePDF" / "KhervePDF.py"


def _python_for(script: Path) -> str | None:
    for venv in (".venv", "venv"):
        for rel in ("bin/python", "Scripts/python.exe"):
            cand = script.parent / venv / rel
            if cand.exists():
                return str(cand)
    return None


def _command_for(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    if path.suffix == ".app":
        exe = path / "Contents" / "MacOS" / "KhervePDF"
        return [str(exe)] if exe.exists() else None
    if path.suffix == ".py":
        py = _python_for(path)
        return [py, str(path)] if py else None
    return [str(path)]


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    custom = QSettings(*SETTINGS).value(PATH_KEY, "", type=str)
    if custom:
        paths.append(Path(custom))
    if sys.platform == "darwin":
        paths += [Path("/Applications/KhervePDF.app"),
                  Path.home() / "Applications" / "KhervePDF.app"]
    elif os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                paths.append(Path(os.environ[env]) / "KhervePDF" / "KhervePDF.exe")
        if os.environ.get("LOCALAPPDATA"):
            paths.append(Path(os.environ["LOCALAPPDATA"]) / "Programs"
                         / "KhervePDF" / "KhervePDF.exe")
    paths.append(_source_checkout())
    return paths


def find_khervepdf() -> list[str] | None:
    for p in candidate_paths():
        cmd = _command_for(p)
        if cmd:
            return cmd
    return None


def set_custom_path(path: str) -> bool:
    if _command_for(Path(path)) is None:
        return False
    QSettings(*SETTINGS).setValue(PATH_KEY, path)
    return True


def use_khervepdf() -> bool:
    return QSettings(*SETTINGS).value(USE_KEY, True, type=bool)


def open_document(path: Path, parent=None) -> str:
    """Open *path*; returns "khervepdf" or "system"."""
    path = Path(path)
    if path.suffix.lower() == ".pdf" and use_khervepdf():
        cmd = find_khervepdf()
        if cmd:
            ok, _pid = QProcess.startDetached(cmd[0], cmd[1:] + [str(path)])
            if ok:
                return "khervepdf"
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    return "system"


def reveal(path: Path) -> None:
    """Show *path* selected in Finder / Explorer."""
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
