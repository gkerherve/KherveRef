"""The local, read-only service the KherveRef panel in Word talks to.

The panel is a web page hosted over HTTPS (GitHub Pages, as Word
requires); it asks this server, on the loopback interface only, for
the open library's references and for citations / bibliographies
formatted in the chosen style. Nothing here writes to the library.

    GET  /api/status                 -> {"library", "count", "styles"}
    GET  /api/search?q=...&limit=n   -> {"items": [{key, title, authors, year, ...}]}
    POST /api/format {"clusters": [[key, ...], ...], "style": id}
                                     -> {"citations": [html], "bibliography": [html]}
    POST /api/log {"message": ...}   -> appends to word-panel.log (Word's own
                                        errors, which the panel can't show in full)
"""
from __future__ import annotations

import json
import mimetypes
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

import datetime

from . import __version__, cite, state

LOG_NAME = "word-panel.log"
LOG_LIMIT = 200_000

PORT = 23120          # Zotero uses 23119
# The panel's own files, also served here: Word for Mac's web view won't
# let an HTTPS page call http://127.0.0.1, but a page loaded from this
# server calls it as its own origin.
PANEL_DIR = Path(__file__).resolve().parent.parent / "docs" / "word"
ALLOWED_ORIGINS = {"https://gkerherve.github.io", "https://localhost:3000",
                   "http://localhost:3000", "null"}

# () -> (library name or None, {key: Entry})
Provider = Callable[[], tuple]


def _summary(e) -> dict:
    return {"key": e.key, "title": e.title, "authors": e.author_text(3),
            "year": e.year, "container": e.container(), "doi": e.doi,
            "type": e.type}


class _Handler(BaseHTTPRequestHandler):
    provider: Provider = staticmethod(lambda: (None, {}))
    server_version = f"KherveRef/{__version__}"

    def log_message(self, *args):     # quiet
        pass

    # ----- CORS (the panel's page is on another origin) -----

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # Chromium / WebView2's Private Network Access preflight: a
            # public HTTPS page may reach this loopback server.
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, data, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str):
        base = panel_dir()
        path = (base / name).resolve()
        if base not in path.parents or not path.is_file():
            return self._json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0]
                         or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        name, entries = self.provider()
        if url.path.startswith("/word/"):
            return self._file(url.path[len("/word/"):])
        if url.path == "/api/status":
            return self._json({"app": "KherveRef", "version": __version__,
                               "library": name, "count": len(entries),
                               "styles": [{"id": k, "label": v, "group": g}
                                          for g, k, v in cite.grouped_styles()],
                               "default_style": cite.DEFAULT_STYLE})
        if url.path == "/api/search":
            words = (q.get("q", [""])[0]).lower().split()
            limit = min(int(q.get("limit", ["50"])[0] or 50), 500)
            hits = []
            for e in sorted(entries.values(), key=lambda e: e.added, reverse=True):
                hay = " ".join([e.key, e.title, e.container(), e.year, e.doi,
                                " ".join(p.display() for p in e.authors + e.editors)]
                               ).lower()
                if all(w in hay for w in words):
                    hits.append(_summary(e))
                    if len(hits) >= limit:
                        break
            return self._json({"library": name, "items": hits})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path == "/api/log":
            return self._log()
        if urlparse(self.path).path != "/api/format":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}")
            clusters = [[str(k) for k in c] for c in req.get("clusters", [])]
            style = str(req.get("style") or cite.DEFAULT_STYLE)
        except (ValueError, TypeError):
            return self._json({"error": "bad request"}, 400)
        _name, entries = self.provider()
        f = cite.format_document(clusters, entries, style)
        return self._json({"citations": f.citations, "bibliography": f.bibliography,
                           "missing": sorted({k for c in clusters for k in c
                                              if k not in entries})})


    def _log(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        try:
            msg = str(json.loads(self.rfile.read(min(n, 20_000)) or b"{}")
                      .get("message", ""))[:4000]
        except (ValueError, AttributeError):
            return self._json({"error": "bad request"}, 400)
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            path.write_text("", encoding="utf-8")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
        return self._json({"ok": True})


def log_path() -> Path:
    return state.state_dir() / LOG_NAME


class WordServer:
    """Runs the service on a daemon thread; `provider` is called on that
    thread, so it must return a snapshot (not a dict being mutated)."""

    def __init__(self, provider: Provider, port: int = PORT):
        handler = type("Handler", (_Handler,), {"provider": staticmethod(provider)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True, name="kherveref-word")

    def start(self) -> "WordServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def panel_dir() -> Path:
    """docs/word in a checkout; kherveref/word_panel in a frozen build."""
    frozen = Path(__file__).resolve().parent / "word_panel"
    return (frozen if frozen.is_dir() else PANEL_DIR).resolve()


def start(provider: Provider) -> WordServer | None:
    """Start on the usual port; None when another KherveRef holds it."""
    try:
        return WordServer(provider).start()
    except OSError:
        return None
