"""What a PDF says about itself: DOI, arXiv id, ISBN, and a best guess
at title / authors / year when it carries no identifier.

Sources, most reliable first: XMP metadata (prism:doi, dc:identifier),
the document info dictionary, then the text of the first pages (the DOI
line under the abstract, the arXiv stamp in the margin). The title
guess is the largest text near the top of page 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .fetch import clean_doi, find_arxiv, find_doi, find_isbn

_JUNK_TITLE = re.compile(
    r"(^microsoft (word|powerpoint)|\.(docx?|pdf|tex|dvi|indd)$|^untitled|"
    r"^document\d*$|^slide \d|^\s*$|^[\w\-]+\d{3,}[\w\-]*$)", re.I)
_HEADER_NOISE = re.compile(
    r"(journal|proceedings|contents lists available|elsevier|springer|"
    r"www\.|http|vol\.|volume|issue|pages?|received|accepted|published|"
    r"copyright|©|licen[sc]e|doi|issn|arxiv|preprint|article|research paper)",
    re.I)


@dataclass
class PdfInfo:
    doi: str = ""
    arxiv: str = ""
    isbn: str = ""
    title: str = ""
    authors: str = ""
    year: str = ""
    has_text: bool = False
    pages: int = 0
    warnings: list[str] = field(default_factory=list)


def _good_title(t: str) -> bool:
    t = t.strip()
    return 10 <= len(t) <= 300 and not _JUNK_TITLE.search(t) \
        and len(t.split()) >= 2


def _largest_text(page) -> str:
    """The run of lines in the biggest font in the top two thirds of the
    page — usually the title."""
    try:
        d = page.get_text("dict")
    except Exception:
        return ""
    height = page.rect.height or 1
    lines = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            y = line["bbox"][1]
            if y > height * 0.66:
                continue
            text = "".join(s["text"] for s in spans).strip()
            size = max(s["size"] for s in spans)
            lines.append((size, y, text))
    candidates = [l for l in lines if len(l[2]) > 3
                  and not _HEADER_NOISE.search(l[2])]
    if not candidates:
        return ""
    top = max(c[0] for c in candidates)
    picked = sorted((c for c in candidates if c[0] >= top - 0.6),
                    key=lambda c: c[1])
    # Only the first contiguous group (a title may wrap onto 2-4 lines).
    out, last_y = [], None
    for size, y, text in picked:
        if last_y is not None and y - last_y > size * 2.2:
            break
        out.append(text)
        last_y = y
    title = " ".join(" ".join(out).split())
    title = re.sub(r"(\w)- (\w)", r"\1\2", title)
    return title if _good_title(title) else ""


def inspect_pdf(path: Path, max_pages: int = 2) -> PdfInfo:
    import pymupdf
    info = PdfInfo()
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        info.warnings.append(f"cannot open PDF: {e}")
        return info
    with doc:
        info.pages = doc.page_count
        if doc.needs_pass:
            info.warnings.append("PDF is password protected")
            return info
        meta = doc.metadata or {}
        try:
            xmp = doc.get_xml_metadata() or ""
        except Exception:
            xmp = ""
        m = re.search(r"<prism:doi>\s*([^<]+)</prism:doi>|"
                      r"prism:doi=\"([^\"]+)\"", xmp)
        if m:
            info.doi = clean_doi(m.group(1) or m.group(2))
        if not info.doi:
            ident = re.search(r"<dc:identifier>(.*?)</dc:identifier>", xmp, re.S)
            info.doi = find_doi(ident.group(1)) if ident else ""
        if not info.doi:
            info.doi = find_doi(" ".join(meta.get(k, "") or "" for k in
                                         ("subject", "keywords", "title")))

        text = ""
        for i in range(min(max_pages, doc.page_count)):
            text += doc[i].get_text() + "\n"
        info.has_text = len(text.strip()) > 50
        if not info.doi:
            info.doi = find_doi(text)
        info.arxiv = find_arxiv(text) or find_arxiv(meta.get("subject", "") or "")
        info.isbn = find_isbn(text) if not info.doi else ""

        mt = (meta.get("title") or "").strip()
        info.title = mt if _good_title(mt) else (
            _largest_text(doc[0]) if doc.page_count else "")
        author = (meta.get("author") or "").strip()
        if author and not re.search(r"(user|admin|owner|^[a-z]+\d*$)", author, re.I):
            info.authors = author
        for src in (meta.get("creationDate") or "", text[:3000]):
            y = re.search(r"(?:D:)?((?:19|20)\d{2})", src)
            if y:
                info.year = y.group(1)
                break
        if not info.has_text:
            info.warnings.append("no text layer (scanned PDF?)")
    return info
