"""Import a whole Zotero library: references, PDFs, collections, tags
and notes, read from Zotero's data folder (zotero.sqlite + storage/).

The database is copied first and read from the copy, so a running
Zotero (which keeps it locked) is never disturbed and never written.
"""
from __future__ import annotations

import html
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .model import Entry, Person

# Zotero item type -> (model type, thesis_type)
_TYPES = {
    "journalArticle": "article", "magazineArticle": "article",
    "newspaperArticle": "article", "preprint": "unpublished",
    "conferencePaper": "inproceedings", "book": "book",
    "bookSection": "incollection", "thesis": "thesis", "report": "report",
    "webpage": "online", "blogPost": "online", "forumPost": "online",
    "dataset": "dataset", "computerProgram": "software", "patent": "patent",
    "manuscript": "unpublished", "encyclopediaArticle": "incollection",
    "dictionaryEntry": "incollection", "presentation": "misc",
    "document": "misc", "letter": "misc", "standard": "report",
}

# Zotero field -> model field
_FIELDS = {
    "title": "title", "publicationTitle": "journal", "bookTitle": "booktitle",
    "proceedingsTitle": "booktitle", "encyclopediaTitle": "booktitle",
    "dictionaryTitle": "booktitle", "websiteTitle": "journal",
    "blogTitle": "journal", "volume": "volume", "issue": "number",
    "number": "number", "reportNumber": "number", "patentNumber": "number",
    "pages": "pages", "edition": "edition", "series": "series",
    "place": "location", "publisher": "publisher", "university": "institution",
    "institution": "institution", "DOI": "doi", "url": "url", "ISBN": "isbn",
    "ISSN": "issn", "abstractNote": "abstract", "language": "language",
    "repository": "publisher", "company": "publisher", "label": "publisher",
}

_AUTHOR_ROLES = {"author", "inventor", "programmer", "artist", "presenter",
                 "director", "podcaster", "cartographer", "sponsor"}


@dataclass
class ZoteroItem:
    entry: Entry
    pdfs: list[Path] = field(default_factory=list)
    collection_keys: list[str] = field(default_factory=list)


@dataclass
class ZoteroLibrary:
    items: list[ZoteroItem]
    # (zotero collection key, name, parent key)
    collections: list[tuple[str, str, str]]
    warnings: list[str] = field(default_factory=list)


def default_data_dir() -> Path:
    """Zotero's default data folder (the same on every platform)."""
    return Path.home() / "Zotero"


def is_data_dir(path: Path) -> bool:
    return (Path(path) / "zotero.sqlite").is_file()


def _strip_html(s: str) -> str:
    s = re.sub(r"<\s*(br|/p|/div|/li|/h\d)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def _date(raw: str) -> str:
    """Zotero stores "2020-05-00 May 2020": a sortable part then the
    original text."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    if not m:
        y = re.search(r"\d{4}", raw or "")
        return y.group(0) if y else ""
    y, mo, d = m.groups()
    if mo == "00":
        return y
    return f"{y}-{mo}" if d == "00" else f"{y}-{mo}-{d}"


def read_library(data_dir: Path) -> ZoteroLibrary:
    data_dir = Path(data_dir)
    src = data_dir / "zotero.sqlite"
    tmp = Path(tempfile.mkdtemp(prefix="kherveref-zotero-"))
    try:
        copy = tmp / "zotero.sqlite"
        shutil.copy2(src, copy)
        for suffix in ("-wal", "-journal"):
            side = src.with_name(src.name + suffix)
            if side.exists():
                shutil.copy2(side, tmp / ("zotero.sqlite" + suffix))
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            return _read(con, data_dir)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read(con: sqlite3.Connection, data_dir: Path) -> ZoteroLibrary:
    q = con.execute
    deleted = {r[0] for r in q("SELECT itemID FROM deletedItems")} \
        if _has_table(con, "deletedItems") else set()
    types = dict(q("SELECT itemTypeID, typeName FROM itemTypes"))
    items = {iid: (types.get(tid, ""), key) for iid, tid, key in
             q("SELECT itemID, itemTypeID, key FROM items")}

    fields: dict[int, dict[str, str]] = {}
    for iid, name, value in q(
            "SELECT d.itemID, f.fieldName, v.value FROM itemData d "
            "JOIN fields f ON f.fieldID = d.fieldID "
            "JOIN itemDataValues v ON v.valueID = d.valueID"):
        fields.setdefault(iid, {})[name] = str(value)

    creators: dict[int, list[tuple[str, Person]]] = {}
    for iid, first, last, mode, role in q(
            "SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, "
            "ct.creatorType FROM itemCreators ic "
            "JOIN creators c ON c.creatorID = ic.creatorID "
            "JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID "
            "ORDER BY ic.itemID, ic.orderIndex"):
        p = Person(literal=last or "") if mode == 1 else \
            Person(family=last or "", given=first or "")
        creators.setdefault(iid, []).append((role, p))

    tags: dict[int, list[str]] = {}
    for iid, name in q("SELECT it.itemID, t.name FROM itemTags it "
                       "JOIN tags t ON t.tagID = it.tagID"):
        tags.setdefault(iid, []).append(name)

    notes: dict[int, list[str]] = {}
    for iid, parent, note in q("SELECT itemID, parentItemID, note FROM itemNotes"):
        if parent is not None and iid not in deleted:
            text = _strip_html(note or "")
            if text:
                notes.setdefault(parent, []).append(text)

    pdfs: dict[int, list[Path]] = {}
    warnings: list[str] = []
    for iid, parent, ctype, path, mode in q(
            "SELECT itemID, parentItemID, contentType, path, linkMode "
            "FROM itemAttachments"):
        if parent is None or iid in deleted or not path:
            continue
        if ctype != "application/pdf" and not str(path).lower().endswith(".pdf"):
            continue
        if path.startswith("storage:"):
            f = data_dir / "storage" / items[iid][1] / path[len("storage:"):]
        elif path.startswith("attachments:"):
            warnings.append(f"Linked file relative to Zotero's base folder "
                            f"skipped: {path[len('attachments:'):]}")
            continue
        else:
            f = Path(path)
        if f.is_file():
            pdfs.setdefault(parent, []).append(f)
        else:
            warnings.append(f"Missing PDF: {f}")

    col_keys = {cid: key for cid, key in q("SELECT collectionID, key FROM collections")}
    collections = [(key, name, col_keys.get(parent, "")) for _cid, name, parent, key in
                   q("SELECT collectionID, collectionName, parentCollectionID, key "
                     "FROM collections")]
    item_cols: dict[int, list[str]] = {}
    for cid, iid in q("SELECT collectionID, itemID FROM collectionItems"):
        if cid in col_keys:
            item_cols.setdefault(iid, []).append(col_keys[cid])

    out: list[ZoteroItem] = []
    for iid, (typ, _key) in items.items():
        if iid in deleted or typ in ("attachment", "note", "annotation", ""):
            continue
        f = fields.get(iid, {})
        e = Entry(type=_TYPES.get(typ, "misc"))
        for zname, mname in _FIELDS.items():
            if f.get(zname) and not getattr(e, mname):
                setattr(e, mname, f[zname].strip())
        e.date = _date(f.get("date", ""))
        if typ == "thesis":
            t = f.get("thesisType", "").lower()
            e.thesis_type = ("phd" if "phd" in t or "doctor" in t else
                             "master" if "master" in t else f.get("thesisType", ""))
        roles = creators.get(iid, [])
        e.authors = [p for r, p in roles if r in _AUTHOR_ROLES]
        e.editors = [p for r, p in roles if r in ("editor", "seriesEditor")]
        if not e.authors and roles and not e.editors:
            e.authors = [p for _r, p in roles]
        e.keywords = sorted(set(tags.get(iid, [])))
        e.notes = "\n\n".join(notes.get(iid, []))
        extra = f.get("extra", "")
        m = re.search(r"^Citation Key:\s*(\S+)", extra, re.M)
        if m or f.get("citationKey"):
            e.key = f.get("citationKey") or m.group(1)
        arx = re.search(r"arXiv:\s*([\w.\-/]+\d)", extra + " " + f.get("archiveID", ""))
        if arx:
            e.eprint, e.eprinttype = re.sub(r"v\d+$", "", arx.group(1)), "arxiv"
        if not e.doi:
            d = re.search(r"^DOI:\s*(\S+)", extra, re.M)
            if d:
                e.doi = d.group(1)
        out.append(ZoteroItem(e, pdfs.get(iid, []), item_cols.get(iid, [])))
    return ZoteroLibrary(out, collections, warnings)


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None
