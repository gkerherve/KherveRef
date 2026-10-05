"""One KherveRef per user: later launches hand their request to the
running window instead of opening a second one.

Other Kherve apps (KhervePDF's "Add to KherveRef" / "Show in KherveRef")
simply run KherveRef with arguments:

    KherveRef --add FILE [FILE...]     import files into the open library
    KherveRef --reveal FILE            select the reference holding FILE
    KherveRef LIBRARY_DIR              open that library

If KherveRef is already running the request travels over a local socket
as one JSON line: {"cmd": "add"|"reveal"|"open", "paths": [...]}.
"""
from __future__ import annotations

import getpass
import json
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    user = re.sub(r"[^A-Za-z0-9_]", "_", getpass.getuser() or "user")
    return f"kherveref-{user}"


def parse_args(argv: list[str]) -> dict:
    """{"cmd": ..., "paths": [...]} from the command line, or {}."""
    args = [a for a in argv if a != "--"]
    for flag, cmd in (("--add", "add"), ("--reveal", "reveal")):
        if flag in args:
            i = args.index(flag)
            paths = [a for a in args[i + 1:] if not a.startswith("-")]
            return {"cmd": cmd, "paths": paths} if paths else {}
    paths = [a for a in args if not a.startswith("-")]
    return {"cmd": "open", "paths": paths[:1]} if paths else {}


def send_to_running(request: dict, timeout_ms: int = 800) -> bool:
    """True when a running KherveRef took the request."""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((json.dumps(request or {"cmd": "raise"}) + "\n").encode())
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class Server(QObject):
    request = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffers: dict[int, bytearray] = {}
        self._server = QLocalServer(self)
        name = server_name()
        if not self._server.listen(name):
            # A crashed instance leaves the socket file behind on Unix.
            QLocalServer.removeServer(name)
            self._server.listen(name)
        self._server.newConnection.connect(self._accept)

    def close(self) -> None:
        self._server.close()

    def _accept(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[id(sock)] = bytearray()
            sock.readyRead.connect(self._read)
            sock.disconnected.connect(self._dropped)
            # On Windows the whole request can arrive before readyRead
            # is connected, and then it is never signalled.
            self._consume(sock)

    def _read(self):
        self._consume(self.sender())

    def _consume(self, sock) -> None:
        buf = self._buffers.setdefault(id(sock), bytearray())
        if sock.bytesAvailable():
            buf.extend(bytes(sock.readAll()))
        if b"\n" in buf:
            line = bytes(buf).partition(b"\n")[0]
            buf.clear()
            try:
                self.request.emit(json.loads(line.decode()))
            except ValueError:
                pass

    def _dropped(self):
        sock = self.sender()
        self._consume(sock)
        self._buffers.pop(id(sock), None)
        sock.deleteLater()
