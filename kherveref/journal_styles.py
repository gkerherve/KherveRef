"""Any journal's citation style, added on demand.

The Citation Style Language project keeps ~10,000 journal styles (the
ones Zotero and Mendeley use). Zotero publishes the list as one JSON
index; most entries are "dependent" styles, a journal's name pointing
at its publisher's house style ("Applied Surface Science" → Elsevier,
numbered, with titles). Adding a journal downloads its file and that
parent, checks citeproc-py can format with it, and keeps both in
<state dir>/styles/, so citations keep working offline.

Footnote ("note") styles are left out: KherveRef, the Word panel and
drag-and-drop put citations in the text.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import state

INDEX_URL = "https://www.zotero.org/styles-files/styles.json"
STYLE_URL = "https://www.zotero.org/styles/{}"
INDEX_MAX_AGE = 30 * 24 * 3600
INSTALLED = "installed.json"
FORMATS = {"numeric": "numbered", "author-date": "author–date",
           "author": "author", "label": "label"}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
# "Surface and Interface Analysis" is "Surface & Interface Analysis" in
# the index; "Journal of the …" is often abbreviated.
_SMALL_WORDS = {"and", "of", "the", "for", "in", "on"}


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[&:,.()/–-]", " ", text.lower()).split())


class StyleError(Exception):
    pass


def user_dir() -> Path:
    return state.state_dir() / "styles"


def _fetch(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "KherveRef"})
    try:
        from .fetch import _ssl_context
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise StyleError(f"Couldn't reach the style repository ({e}). "
                         "Check the internet connection.") from None


def load_index(refresh: bool = False) -> list[dict]:
    """The list of every style (cached for a month; the cached copy is
    used when offline)."""
    path = user_dir() / "index.json"
    fresh = path.exists() and time.time() - path.stat().st_mtime < INDEX_MAX_AGE
    if not fresh or refresh:
        try:
            data = _fetch(INDEX_URL, timeout=60)
            json.loads(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except (StyleError, ValueError):
            if not path.exists():
                raise
    return json.loads(path.read_text(encoding="utf-8"))


def describe(item: dict) -> str:
    fmt = (item.get("categories") or {}).get("format", "")
    return f"{item['title']} ({FORMATS.get(fmt, fmt)})" if fmt else item["title"]


def search(query: str, index: list[dict], limit: int = 200) -> list[dict]:
    """In-text styles whose title, short title or id contain every word
    of *query*; whole-title and starts-with matches first."""
    q = _plain(query)
    words = [w for w in q.split() if w not in _SMALL_WORDS] or q.split()
    if not words:
        return []
    found = []
    for it in index:
        if (it.get("categories") or {}).get("format") == "note":
            continue
        title = _plain(it.get("title", ""))
        hay = " ".join((title, _plain(it.get("titleShort", "")), it.get("name", "")))
        if all(w in hay for w in words):
            rank = 0 if title == q else 1 if title.startswith(q) else 2
            found.append((rank, len(title), title, it))
    found.sort(key=lambda t: t[:3])
    return [t[3] for t in found[:limit]]


def installed() -> dict[str, dict]:
    """{style id: {"title", "format", "file", "parent"}}"""
    try:
        return json.loads((user_dir() / INSTALLED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_installed(data: dict) -> None:
    user_dir().mkdir(parents=True, exist_ok=True)
    (user_dir() / INSTALLED).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def style_path(style_id: str) -> Path | None:
    rec = installed().get(style_id)
    if not rec:
        return None
    path = user_dir() / rec["file"]
    return path if path.exists() else None


def _info(xml: str) -> tuple[str, str, str]:
    """(title, citation format, independent parent id or "")"""
    title = re.search(r"<title>(.*?)</title>", xml, re.S)
    fmt = re.search(r'citation-format="([^"]+)"', xml)
    parent = re.search(r'href="https?://www\.zotero\.org/styles/([^"]+)"\s+'
                       r'rel="independent-parent"', xml)
    import html
    return (html.unescape(title.group(1).strip()) if title else "",
            fmt.group(1) if fmt else "", parent.group(1) if parent else "")


def install(name: str) -> dict:
    """Download style *name* (and its house style when it is a journal
    pointing at one); returns its record. Raises StyleError."""
    if not _NAME_RE.match(name):
        raise StyleError(f"Not a style name: {name!r}")
    xml = _fetch(STYLE_URL.format(name)).decode("utf-8", errors="replace")
    if "<style" not in xml:
        raise StyleError(f"No style called {name} in the repository.")
    title, fmt, parent = _info(xml)
    if fmt == "note":
        raise StyleError(f"{title} puts citations in footnotes, which KherveRef "
                         "doesn't do. Choose an in-text style.")
    user_dir().mkdir(parents=True, exist_ok=True)
    file = f"{name}.csl"
    if parent:
        if not _NAME_RE.match(parent):
            raise StyleError(f"{title} names an unusual house style: {parent!r}")
        file = f"{parent}.csl"
        if not (user_dir() / file).exists():
            pxml = _fetch(STYLE_URL.format(parent))
            (user_dir() / file).write_bytes(pxml)
        if not fmt:
            fmt = _info((user_dir() / file).read_text(encoding="utf-8"))[1]
    else:
        (user_dir() / file).write_text(xml, encoding="utf-8")
    try:
        _check(user_dir() / file, title)
    except StyleError:
        if not any(r["file"] == file for r in installed().values()):
            (user_dir() / file).unlink(missing_ok=True)
        raise
    rec = {"title": f"{title} ({FORMATS.get(fmt, fmt)})" if fmt else title,
           "format": fmt, "file": file, "parent": parent}
    data = installed()
    data[name] = rec
    _save_installed(data)
    return rec


def _check(path: Path, title: str) -> None:
    """citeproc-py doesn't support every CSL feature: try the style on
    a sample before offering it."""
    import warnings
    from citeproc import (Citation, CitationItem, CitationStylesBibliography,
                          CitationStylesStyle, formatter)
    from citeproc.source.json import CiteProcJSON
    item = {"id": "a", "type": "article-journal", "title": "Surface analysis",
            "author": [{"family": "Smith", "given": "John"}],
            "container-title": "Applied Surface Science", "volume": "512",
            "page": "145-150", "issued": {"date-parts": [[2020]]}}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bib = CitationStylesBibliography(
                CitationStylesStyle(str(path), validate=False),
                CiteProcJSON([item]), formatter.html)
            c = Citation([CitationItem("a")])
            bib.register(c)
            str(bib.cite(c, lambda _i: None))
            [str(b) for b in bib.bibliography()]
    except Exception as e:      # noqa: BLE001 — any failure means "unusable"
        raise StyleError(f"{title} uses features KherveRef's formatter can't "
                         f"handle yet ({type(e).__name__}). Try its publisher's "
                         "general style instead.") from None


def remove(name: str) -> None:
    data = installed()
    rec = data.pop(name, None)
    _save_installed(data)
    if rec and not any(r["file"] == rec["file"] for r in data.values()):
        (user_dir() / rec["file"]).unlink(missing_ok=True)
