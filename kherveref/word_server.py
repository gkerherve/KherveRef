"""The local, read-only service the KherveRef panel in Word talks to.

The panel is a web page hosted over HTTPS (GitHub Pages, as Word
requires); it asks this server, on the loopback interface only, for
the open library's references and for citations / bibliographies
formatted in the chosen style. Nothing here writes to the library.

    GET  /api/status                 -> {"library", "count", "styles"}
    GET  /api/search?q=...&limit=n   -> {"items": [{key, title, authors, year, ...}]}
    POST /api/format {"clusters": [[key, ...], ...], "style": id}
                                     -> {"citations": [html], "bibliography": [html]}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import __version__, cite

PORT = 23120          # Zotero uses 23119
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

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        name, entries = self.provider()
        if url.path == "/api/status":
            return self._json({"app": "KherveRef", "version": __version__,
                               "library": name, "count": len(entries),
                               "styles": [{"id": k, "label": v}
                                          for k, v in cite.STYLES.items()],
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


def start(provider: Provider) -> WordServer | None:
    """Start on the usual port; None when another KherveRef holds it."""
    try:
        return WordServer(provider).start()
    except OSError:
        return None
