"""AI ▸ Connect to Claude…: puts KherveRef's MCP server into Claude
Desktop, Claude Code and the other MCP apps found on this computer, in
one click each (mcp_hosts.py does the careful file editing)."""
from __future__ import annotations

from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                               QGridLayout, QGroupBox, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from . import mcp_hosts
from .icons import icon

_SHOWN_ALWAYS = ("claude-desktop", "claude-code")


class McpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Claude")
        self.resize(620, 480)
        intro = QLabel(
            "<b>Let Claude work with your references.</b> Once connected, "
            "Claude can search the library, add papers by DOI or PDF, read "
            "and look at their pages, fix details, find duplicates, and give "
            "you BibTeX or formatted citations. It sees the references you "
            "select here, and can show you the ones it means.<br><br>"
            "Every change Claude makes is a commit in the library's history, "
            "so Edit ▸ Undo takes it back. KherveRef need not be open.")
        intro.setWordWrap(True)

        box = QGroupBox("Apps on this computer")
        self._grid = QGridLayout(box)
        self._rows: list[tuple[mcp_hosts.Host, QLabel, QPushButton]] = []
        hosts = [h for h in mcp_hosts.HOSTS
                 if h.key in _SHOWN_ALWAYS or h.exists()]
        for row, host in enumerate(hosts):
            status = QLabel()
            button = QPushButton()
            button.clicked.connect(lambda _=False, h=host: self._toggle(h))
            self._grid.addWidget(QLabel(f"<b>{host.label}</b>"), row, 0)
            self._grid.addWidget(status, row, 1)
            self._grid.addWidget(button, row, 2)
            self._rows.append((host, status, button))
        self._grid.setColumnStretch(1, 1)

        other = QGroupBox("Any other MCP app")
        snippet = QPlainTextEdit(mcp_hosts.host_config())
        snippet.setReadOnly(True)
        snippet.setMaximumHeight(130)
        copy = QPushButton(icon("copy"), "Copy")
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(snippet.toPlainText()))
        lay_other = QVBoxLayout(other)
        lay_other.addWidget(QLabel("Add this to its MCP servers file, or run "
                                   "<code>" + mcp_hosts.cli_command()
                                   .replace("&", "&amp;").replace("<", "&lt;")
                                   + "</code> for Claude Code by hand:"))
        lay_other.addWidget(snippet)
        lay_other.addWidget(copy)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(box)
        lay.addWidget(other)
        lay.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        for host, status, button in self._rows:
            text = host.status()
            status.setText(text)
            connected = text == "connected"
            button.setText("Disconnect" if connected else "Connect")
            button.setEnabled(host.cli is not None or text != "not installed")

    def _toggle(self, host: mcp_hosts.Host) -> None:
        if host.status() == "connected":
            out = host.disconnect()
        elif host.mac_app and host.is_running():
            if QMessageBox.question(
                    self, "Connect to Claude",
                    f"{host.label} is open and would overwrite the change. "
                    f"Quit {host.label}, connect, and open it again?"
                    ) != QMessageBox.Yes:
                return
            out = host.connect_restarting()
        else:
            out = host.connect()
        self.refresh()
        if not out.get("ok"):
            QMessageBox.warning(self, "Connect to Claude", out.get("error", ""))
        elif out.get("action") in ("added", "updated") and host.note:
            QMessageBox.information(
                self, "Connect to Claude",
                f"KherveRef is connected to {host.label}. {host.note}")
