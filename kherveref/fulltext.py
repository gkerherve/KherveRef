"""The text of attached PDFs, page by page, and passage search over it.

Text is extracted once per PDF (by content hash) into the library's
cache, so asking questions about a library of hundreds of papers does
not re-read every PDF. Search is BM25 over page-sized passages: no
embedding model needed, so it works with any local AI or none.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .library import Library
from .model import Entry
from .pdf_meta import PDF_LOCK

_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
_STOP = set("""the a an and or of to in on for with by from at as is are was were be been
this that these those it its we our their they which using used use can may also than
into such between both each more most other some only not no all any per via""".split())


@dataclass
class Passage:
    key: str
    page: int           # 1-based
    text: str


def _pdf(lib: Library, e: Entry) -> tuple[Path, str] | None:
    for a in e.files:
        if a.path.lower().endswith(".pdf"):
            return lib.root / a.path, a.sha1 or a.path.replace("/", "_")
    return None


def pages(lib: Library, e: Entry) -> list[str]:
    """The PDF's text, one string per page ([] without a PDF or text)."""
    found = _pdf(lib, e)
    if found is None:
        return []
    pdf, ident = found
    cache = lib.cache_dir / "text" / f"{ident}.json"
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not pdf.exists():
        return []
    import pymupdf
    try:
        with PDF_LOCK, pymupdf.open(str(pdf)) as doc:
            out = [" ".join(p.get_text().split()) for p in doc]
    except Exception:
        return []
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def text(lib: Library, e: Entry, limit: int | None = None) -> str:
    """The whole text with page markers, cut at *limit* characters."""
    out, n = [], 0
    for i, p in enumerate(pages(lib, e), 1):
        if not p:
            continue
        chunk = f"[page {i}]\n{p}\n"
        if limit is not None and n + len(chunk) > limit:
            out.append(chunk[: max(0, limit - n)])
            break
        out.append(chunk)
        n += len(chunk)
    return "".join(out)


def tokens(s: str) -> list[str]:
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(s))
            if w not in _STOP]


def passages(lib: Library, entries, size: int = 1500) -> list[Passage]:
    """Page-sized passages (long pages split) of every entry's PDF."""
    out = []
    for e in entries:
        for i, p in enumerate(pages(lib, e), 1):
            for start in range(0, len(p), size):
                piece = p[start:start + size + 200]       # small overlap
                if piece.strip():
                    out.append(Passage(e.key, i, piece))
    return out


def search(query: str, items: list[Passage], top: int = 8) -> list[Passage]:
    """The *top* passages for *query* by BM25."""
    q = tokens(query)
    if not q or not items:
        return []
    docs = [Counter(tokens(p.text)) for p in items]
    avg = sum(sum(d.values()) for d in docs) / len(docs) or 1
    df = Counter(w for d in docs for w in set(d) if w in q)
    n = len(docs)
    k1, b = 1.5, 0.75
    scored = []
    for p, d in zip(items, docs):
        length = sum(d.values()) or 1
        s = 0.0
        for w in set(q):
            f = d.get(w, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg))
        if s > 0:
            scored.append((s, p))
    scored.sort(key=lambda sp: sp[0], reverse=True)
    return [p for _s, p in scored[:top]]
