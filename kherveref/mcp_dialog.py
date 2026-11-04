"""AI ▸ Connect to Claude…: wires Claude Desktop, Claude Code and their
relatives up to KherveRef. Laid out like KherveCAD's dialog.

Nothing here says "MCP" unless the user has to type it somewhere: the
protocol is our problem, not theirs. What a person needs to know is
which application to connect, that it must be restarted afterwards, and
that they then say "KherveRef" in the chat — that last step is
invisible, and skipping it makes a correctly connected assistant look
broken.

Editing claude_desktop_config.json by hand is the step that stops people
connecting at all, so the dialog detects the applications on this
machine and writes the entry itself (mcp_hosts.py). The snippet and its
Copy button stay for anyone configuring a machine by hand.
"""
from __future__ import annotations

import datetime
import json
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from . import git_backend
from .icons import icon
from .mcp_hosts import HOSTS, Host, cli_command, host_config

TITLE = "Connect to Claude"
GREEN, RED, GREY = "#2e7d32", "#b71c1c", "#888888"


class McpDialog(QDialog):
    def __init__(self, parent=None, library=None):
        super().__init__(parent)
        self._library = library
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(620)
        self._build_ui()
        self._refresh_hosts()
        self._refresh_snippet()
        self._refresh_activity()

    # ── UI ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        blurb = QLabel(
            "Let Claude work with your references — no API key, it uses "
            "the login you already have. Works with <b>Claude Desktop</b> "
            "and <b>Claude Code</b>, and with Cursor, Cline, VS Code and "
            "LM Studio.<br><br>Claude gets the whole library rather than a "
            "chat reply: search, adding papers by DOI or PDF, reading and "
            "looking at their pages, fixing details, finding duplicates, "
            "BibTeX and formatted citations — and it sees the references "
            "you select here. Every change is a commit in the library's "
            "history, so Edit ▸ Undo takes it back.")
        blurb.setWordWrap(True)
        lay.addWidget(blurb)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self._status)

        host_box = QGroupBox("Connect an application")
        host_lay = QVBoxLayout(host_box)
        pick = QLabel("Pick yours and press Connect — KherveRef writes itself "
                      "into that application's own settings, so there is no "
                      "config file to edit by hand.")
        pick.setWordWrap(True)
        host_lay.addWidget(pick)
        self._hosts = QListWidget()
        self._hosts.setMaximumHeight(120)
        self._hosts.currentRowChanged.connect(self._refresh_host_buttons)
        host_lay.addWidget(self._hosts)
        hrow = QHBoxLayout()
        self._connect_btn = QPushButton(icon("claude"), "Connect")
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn = QPushButton(icon("close"), "Disconnect")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        other = QPushButton(icon("open_library"), "Other application…")
        other.setToolTip("Point at another assistant's JSON config file and "
                         "add KherveRef to it.")
        other.clicked.connect(self._on_other_host)
        hrow.addWidget(self._connect_btn)
        hrow.addWidget(self._disconnect_btn)
        hrow.addWidget(other)
        hrow.addStretch(1)
        host_lay.addLayout(hrow)
        self._host_hint = QLabel()
        self._host_hint.setWordWrap(True)
        self._host_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_lay.addWidget(self._host_hint)
        lay.addWidget(host_box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Or set it up by hand:"))
        self._flavour = QComboBox()
        self._flavour.addItems(["Claude Desktop / Cursor / Zed (JSON)",
                                "Claude Code (command line)"])
        self._flavour.currentIndexChanged.connect(self._refresh_snippet)
        row.addWidget(self._flavour, 1)
        lay.addLayout(row)

        self._snippet = QPlainTextEdit()
        self._snippet.setReadOnly(True)
        self._snippet.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        mono = QFont("Consolas" if sys.platform.startswith("win") else "Menlo")
        mono.setStyleHint(QFont.TypeWriter)
        mono.setPointSize(10)
        self._snippet.setFont(mono)
        self._snippet.setMinimumHeight(130)
        lay.addWidget(self._snippet)

        hint = QLabel(
            "<b>Then say &quot;KherveRef&quot; in the chat.</b> Claude only "
            "reaches for your library when you point it here &mdash; "
            "<i>&quot;in KherveRef, add 10.1038/nature12373 to my Chapter 2 "
            "collection&quot;</i> or <i>&quot;summarise the paper I have "
            "selected in KherveRef&quot;</i>. After that it carries on in "
            "the library.<br><br>Restart the application after connecting "
            "&mdash; it reads its tool list once at startup.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        act_box = QGroupBox("Recent activity")
        act_lay = QVBoxLayout(act_box)
        self._activity = QListWidget()
        self._activity.setMaximumHeight(110)
        act_lay.addWidget(self._activity)
        lay.addWidget(act_box)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        copy = QPushButton(icon("copy"), "Copy")
        copy.setToolTip("Copy the set-up text shown above")
        copy.clicked.connect(self._copy)
        btns.addButton(copy, QDialogButtonBox.ActionRole)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    # ── Status and activity ──────────────────────────────────────────

    def _refresh_status(self) -> None:
        if any(h.connected() for h in HOSTS):
            names = ", ".join(h.label for h in HOSTS if h.connected())
            self._status.setText(
                f"<b style='color:{GREEN}'>Connected</b> to {names}. "
                "Claude works on the library open here.")
        else:
            self._status.setText(
                f"<b style='color:{RED}'>Not connected</b> — no assistant "
                "can reach your library yet.")

    def _refresh_activity(self) -> None:
        """Claude's own changes, from the library's Git history."""
        self._activity.clear()
        if self._library is None:
            self._activity.addItem("Open a library to see what Claude changed.")
            return
        for _sha, _author, when, subject in git_backend.history(
                self._library.root):
            if subject.endswith("via Claude") or "via Claude:" in subject:
                stamp = datetime.datetime.fromtimestamp(when).strftime(
                    "%Y-%m-%d %H:%M")
                self._activity.addItem(f"{stamp}  {subject}")
            if self._activity.count() >= 50:
                break
        if not self._activity.count():
            self._activity.addItem("Nothing yet — Claude's changes will "
                                   "appear here.")

    # ── Host wiring ──────────────────────────────────────────────────

    def _selected_host(self) -> Host | None:
        row = self._hosts.currentRow()
        return HOSTS[row] if 0 <= row < len(HOSTS) else None

    def _refresh_hosts(self) -> None:
        """Rebuild the host list, keeping the current selection."""
        row = max(self._hosts.currentRow(), 0)
        self._hosts.blockSignals(True)
        self._hosts.clear()
        for host in HOSTS:
            status = host.status()
            item = QListWidgetItem(f"{host.label} — {status}")
            if status.startswith("connected"):
                item.setForeground(QColor(GREEN))
            elif status == "not installed":
                item.setForeground(QColor(GREY))
            self._hosts.addItem(item)
        self._hosts.setCurrentRow(min(row, len(HOSTS) - 1))
        self._hosts.blockSignals(False)
        self._refresh_host_buttons()
        self._refresh_status()

    def _refresh_host_buttons(self, *_) -> None:
        host = self._selected_host()
        if host is None:
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(False)
            self._host_hint.clear()
            return
        connected = host.connected()
        self._connect_btn.setEnabled(not host.manual)
        self._connect_btn.setText("Update entry" if connected and
                                  not host.up_to_date() else "Connect")
        self._disconnect_btn.setEnabled(connected and not host.manual)
        bits = []
        if host.manual:
            bits.append("Cannot be edited automatically.")
        bits.append(host.note)
        if host.path():
            bits.append(f"<i>{host.path()}</i>")
        self._host_hint.setText("  ".join(b for b in bits if b))

    def _report(self, result: dict) -> None:
        """Show what a connect/disconnect actually did."""
        host = result.get("host", "host")
        if not result.get("ok"):
            QMessageBox.warning(self, TITLE, result.get(
                "error", f"Could not configure {host}."))
            self._refresh_hosts()
            return
        lines = [f"{host}: KherveRef {result.get('action', 'updated')}.",
                 f"File: {result.get('path', '')}"]
        if result.get("backup"):
            lines.append(f"Previous version saved as {result['backup']}")
        if result.get("restart"):
            lines.append(f"\nRestart {host} for it to take effect.")
        if result.get("action") != "removed":
            # The step with no visible cue: a correctly connected
            # assistant sits there doing nothing until a chat names us.
            lines.append(f"\nThen mention KherveRef in your chat with {host} "
                         "— say \"in KherveRef, find my papers on XPS\" — "
                         "and it will work in your library.")
        QMessageBox.information(self, TITLE, "\n".join(lines))
        self._refresh_hosts()

    def _on_connect(self) -> None:
        host = self._selected_host()
        if host is None:
            return
        if host.is_running():
            # It keeps its settings in memory and would write the file
            # back without us — so quit it, write, and reopen it.
            if QMessageBox.question(
                    self, TITLE,
                    f"{host.label} is open and would undo this change the "
                    "next time it saves its settings. Quit it, connect, and "
                    "open it again now?") != QMessageBox.Yes:
                return
            self._report(host.connect_restarting())
            return
        self._report(host.connect())

    def _on_other_host(self) -> None:
        """Add KherveRef to a config file the user points us at."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the application's MCP config file", "",
            "JSON config (*.json);;All files (*)")
        if not path:
            return
        # Match the file's own convention: most clients copied Claude
        # Desktop's "mcpServers", VS Code and its relatives use "servers".
        shape = "mcpServers"
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            if isinstance(doc, dict) and "servers" in doc \
                    and "mcpServers" not in doc:
                shape = "servers"
        except (OSError, ValueError):
            pass          # empty or unreadable: connect() reports it
        host = Host.for_file(path, shape=shape)
        if shape == "servers":
            host.entry_extra = {"type": "stdio"}
        self._report(host.connect())

    def _on_disconnect(self) -> None:
        host = self._selected_host()
        if host is None:
            return
        if QMessageBox.question(
                self, TITLE,
                f"Remove KherveRef from {host.label}'s configuration?\n"
                "Other servers are left untouched.") != QMessageBox.Yes:
            return
        self._report(host.disconnect())

    # ── Snippet ──────────────────────────────────────────────────────

    def _refresh_snippet(self, *_) -> None:
        self._snippet.setPlainText(host_config() if self._flavour.currentIndex()
                                   == 0 else cli_command())

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._snippet.toPlainText())
