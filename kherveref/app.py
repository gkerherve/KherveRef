"""KherveRef application entry: QApplication + theme bootstrap + crash log."""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings
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


def main() -> int:
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
    if request.get("cmd") == "open" and Path(request["paths"][0]).is_dir():
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
