"""Front-page thumbnails of attached PDFs, so a reference can be
recognised at a glance.

Rendered once per PDF on a background thread into the library's cache
(.kherveref/cache/thumbs/<sha1>.png — never committed, rebuilt when
missing), keyed by the file's hash so renaming a key costs nothing.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from .library import Library
from .model import Entry
from .pdf_meta import PDF_LOCK

WIDTH = 360     # px; sharp in the covers grid and the details pane


def thumb_path(lib: Library, e: Entry) -> Path | None:
    """Where *e*'s thumbnail lives (whether rendered yet or not), or None
    when it has no PDF."""
    for a in e.files:
        if a.path.lower().endswith(".pdf"):
            name = a.sha1 or a.path.replace("/", "_")
            return lib.cache_dir / "thumbs" / f"{name}.png"
    return None


def pdf_of(lib: Library, e: Entry) -> Path | None:
    for a in e.files:
        if a.path.lower().endswith(".pdf"):
            return lib.root / a.path
    return None


def render(pdf: Path, out: Path, width: int = WIDTH) -> bool:
    import pymupdf
    try:
        with PDF_LOCK:
            with pymupdf.open(str(pdf)) as doc:
                if doc.needs_pass or doc.page_count == 0:
                    return False
                page = doc[0]
                zoom = width / max(page.rect.width, 1)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                      alpha=False)
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_name(out.name + ".tmp.png")
                pix.save(str(tmp))
        os.replace(tmp, out)
        return True
    except Exception:
        return False


class _Worker(QObject):
    """One daemon thread rendering queued thumbnails in turn. A plain
    thread rather than a QThread: it must never keep the app from
    exiting, and Qt aborts on a QThread still running at exit."""
    done = Signal(str, str)         # key, png path (delivered queued)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: "queue.Queue[tuple[str, Path, Path] | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="kherveref-thumbnails")

    def start(self):
        self._thread.start()

    def wait(self, ms: int):
        self._thread.join(ms / 1000)

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            key, pdf, out = job
            if out.exists() or render(pdf, out):
                self.done.emit(key, str(out))


class Thumbnails(QObject):
    """`pixmap(entry)` returns the cached thumbnail or None and queues
    the render; `ready(key)` fires when one becomes available."""
    ready = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lib: Library | None = None
        self._pixmaps: dict[str, QPixmap] = {}
        self._queued: set[str] = set()
        self._worker = _Worker(self)
        self._worker.done.connect(self._done)
        self._worker.start()

    def set_library(self, lib: Library | None) -> None:
        self.lib = lib
        self._pixmaps.clear()
        self._queued.clear()

    def path(self, e: Entry) -> Path | None:
        """The thumbnail file if it has been rendered (for tooltips)."""
        if self.lib is None:
            return None
        p = thumb_path(self.lib, e)
        return p if p is not None and p.exists() else None

    def pixmap(self, e: Entry) -> QPixmap | None:
        if self.lib is None:
            return None
        out = thumb_path(self.lib, e)
        if out is None:
            return None
        key = str(out)
        if key in self._pixmaps:
            return self._pixmaps[key]
        if out.exists():
            pm = QPixmap(str(out))
            if not pm.isNull():
                self._pixmaps[key] = pm
                return pm
        pdf = pdf_of(self.lib, e)
        if pdf is not None and pdf.exists() and key not in self._queued:
            self._queued.add(key)
            self._worker.jobs.put((e.key, pdf, out))
        return None

    def _done(self, key: str, png: str) -> None:
        self._queued.discard(png)
        self.ready.emit(key)

    def stop(self) -> None:
        self._worker.jobs.put(None)
        self._worker.wait(5000)
