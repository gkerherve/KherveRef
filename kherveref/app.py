"""KherveRef application entry: QApplication + theme bootstrap + crash log."""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings
from PySide6.QtWidgets import QApplication

from . import ipc, themes
from .icons import app_icon


def _install_crash_log() -> None:
    log_path = Path(tempfile.gettempdir()) / "kherveref_crash.log"

    def _hook(exc_type, exc, tb):
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("--- KherveRef crash ---\n")
            traceback.print_exception(exc_type, exc, tb, file=fh)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


class _FileOpenFilter(QObject):
    """macOS hands a double-clicked .kref (or a PDF dropped on the Dock
    icon) to the running app as a FileOpen event, not as arguments."""

    def __init__(self, win):
        super().__init__(win)
        self._win = win

    def eventFilter(self, obj, event):  # noqa: N802 — Qt override
        if event.type() == QEvent.FileOpen and event.file():
            path = event.file()
            cmd = "add" if path.lower().endswith(".pdf") else "open"
            self._win.handle_request({"cmd": cmd, "paths": [path]})
            return True
        return False


def main() -> int:
    if "--version" in sys.argv:
        from . import version_string
        print(f"KherveRef {version_string()[1:]}", flush=True)
        return 0
    if "--mcp-server" in sys.argv:
        # The installed app doubles as its MCP server (no window).
        from .mcp_server import main as mcp_main
        return mcp_main([a for a in sys.argv[1:] if a != "--mcp-server"])
    if "--smoke-test" in sys.argv:
        from .smoke import run
        return run()
    _install_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("KherveRef")
    app.setOrganizationName("kherve")
    app.setWindowIcon(app_icon())

    request = ipc.parse_args(sys.argv[1:])
    if ipc.send_to_running(request):
        return 0

    from .mainwindow import MainWindow
    settings = QSettings("kherve", "KherveRef")
    theme_name = settings.value("theme_name", "Light") or "Light"
    themes.apply_theme(app, theme_name)

    win = MainWindow(theme_name=theme_name)
    server = ipc.Server(app)
    server.request.connect(win.handle_request)
    app.installEventFilter(_FileOpenFilter(win))
    from . import library
    if request.get("cmd") == "open" and library.is_library(request["paths"][0]):
        win.open_library(Path(request["paths"][0]))
    else:
        win.reopen_last_library()
    win.show()
    screen = app.primaryScreen()
    if screen is not None:
        fg = win.frameGeometry()
        fg.moveCenter(screen.availableGeometry().center())
        win.move(fg.topLeft())
    if request.get("cmd") in ("add", "reveal"):
        win.handle_request(request)
    return app.exec()
