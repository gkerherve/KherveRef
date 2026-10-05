"""KherveRef application entry: QApplication + theme bootstrap + crash log."""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import themes
from .icons import app_icon
from .mainwindow import MainWindow


def _install_crash_log() -> None:
    log_path = Path(tempfile.gettempdir()) / "kherveref_crash.log"

    def _hook(exc_type, exc, tb):
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("--- KherveRef crash ---\n")
            traceback.print_exception(exc_type, exc, tb, file=fh)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def main() -> int:
    _install_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("KherveRef")
    app.setOrganizationName("kherve")
    app.setWindowIcon(app_icon())

    settings = QSettings("kherve", "KherveRef")
    theme_name = settings.value("theme_name", "Light") or "Light"
    themes.apply_theme(app, theme_name)

    win = MainWindow(theme_name=theme_name)
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and Path(args[0]).is_dir():
        win.open_library(Path(args[0]))
    else:
        win.reopen_last_library()
    win.show()
    screen = app.primaryScreen()
    if screen is not None:
        fg = win.frameGeometry()
        fg.moveCenter(screen.availableGeometry().center())
        win.move(fg.topLeft())
    return app.exec()
