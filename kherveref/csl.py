"""CSL-JSON <-> model.

CSL-JSON is what DOI content negotiation (Crossref, DataCite, mEDRA)
returns, and what Zotero exports, so one mapping serves both.
"""
from __future__ import annotations

import html
import re

from .model import Entry, Person

_TYPE_IN = {
    "article-journal": "article", "article": "article",
    "article-magazine": "article", "article-newspaper": "article",
    "review": "article", "journal-article": "article",
    "paper-conference": "inproceedings", "proceedings-article": "inproceedings",
    "book": "book", "monograph": "book", "edited-book": "book",
    "reference-book": "book", "chapter": "incollection",
    "book-chapter": "incollection", "entry-encyclopedia": "incollection",
    "entry-dictionary": "incollection", "thesis": "thesis",
    "dissertation": "thesis", "report": "report", "webpage": "online",
    "post-weblog": "online", "post": "online", "dataset": "dataset",
    "software": "software", "patent": "patent", "manuscript": "unpublished",
    "posted-content": "unpublished", "preprint": "unpublished",
}
_TYPE_OUT = {
    "article": "article-journal", "inproceedings": "paper-conference",
    "book": "book", "inbook": "chapter", "incollection": "chapter",
    "thesis": "thesis", "report": "report", "online": "webpage",
    "dataset": "dataset", "software": "software", "patent": "patent",
    "unpublished": "manuscript", "misc": "document",
}


def _text(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    s = html.unescape(str(v or ""))
    s = re.sub(r"</?(i|b|em|strong|sup|sub|scp|span|jats:[a-z]+)[^>]*>", "", s)
    return " ".join(s.split())


def _person(d: dict) -> Person:
    if d.get("literal") or d.get("name"):
        return Person(literal=_text(d.get("literal") or d.get("name")))
    family = _text(d.get("family", ""))
    particle = _text(d.get("non-dropping-particle", ""))
    if particle:
        family = f"{particle} {family}"
    given = _text(d.get("given", ""))
    if d.get("dropping-particle"):
        given = f"{given} {_text(d['dropping-particle'])}".strip()
    if d.get("suffix"):
        family = f"{family} {_text(d['suffix'])}"
    return Person(family=family, given=given)


def _date(d) -> str:
    if not isinstance(d, dict):
        return ""
    parts = (d.get("date-parts") or [[]])[0] or []
    parts = [p for p in parts if p not in (None, "")]
    if parts:
        y = str(parts[0])
        rest = [f"{int(p):02d}" for p in parts[1:3]]
        return "-".join([y] + rest)
    m = re.search(r"\d{4}", str(d.get("raw") or d.get("literal") or ""))
    return m.group(0) if m else ""


def from_csl(d: dict) -> Entry:
    typ = _TYPE_IN.get(str(d.get("type", "")).lower(), "misc")
    e = Entry(type=typ)
    e.authors = [_person(p) for p in d.get("author", []) if isinstance(p, dict)]
    e.editors = [_person(p) for p in d.get("editor", []) if isinstance(p, dict)]
    e.title = _text(d.get("title"))
    e.subtitle = _text(d.get("subtitle"))
    container = _text(d.get("container-title"))
    if typ in ("incollection", "inbook", "inproceedings"):
        e.booktitle = container or _text(d.get("event-title") or d.get("event"))
    elif container:
        e.journal = container
    for key in ("issued", "published-print", "published-online", "created"):
        e.date = _date(d.get(key))
        if e.date:
            break
    e.publisher = _text(d.get("publisher"))
    e.location = _text(d.get("publisher-place"))
    e.volume = _text(d.get("volume"))
    e.number = _text(d.get("issue") or d.get("number"))
    e.pages = _text(d.get("page")).replace("-", "–")
    e.edition = _text(d.get("edition"))
    e.series = _text(d.get("collection-title"))
    e.doi = _text(d.get("DOI"))
    e.url = _text(d.get("URL"))
    if e.doi and e.doi.lower() in e.url.lower():
        e.url = ""      # just the DOI again: the doi field already links it
    e.isbn = _text(d.get("ISBN"))
    e.issn = _text(d.get("ISSN"))
    e.abstract = _text(d.get("abstract"))
    e.language = _text(d.get("language"))
    e.note = _text(d.get("note")) if typ != "misc" else ""
    if typ == "thesis":
        e.institution = e.publisher
        e.publisher = ""
        genre = _text(d.get("genre")).lower()
        e.thesis_type = ("phd" if "phd" in genre or "doctor" in genre else
                         "master" if "master" in genre else "")
    if typ == "report" and e.publisher and not e.institution:
        e.institution, e.publisher = e.publisher, ""
    kw = d.get("keyword")
    if isinstance(kw, str) and kw:
        e.keywords = [k.strip() for k in re.split(r"[;,]", kw) if k.strip()]
    if typ == "unpublished" and "arxiv" in (e.doi + e.url).lower():
        m = re.search(r"(\d{4}\.\d{4,5})", e.doi + " " + e.url)
        if m:
            e.eprint, e.eprinttype = m.group(1), "arxiv"
    if d.get("id") and isinstance(d["id"], str) and re.match(
            r"^[A-Za-z][\w:\-]*$", d["id"]):
        e.key = d["id"]
    return e


def to_csl(e: Entry) -> dict:
    d: dict = {"id": e.key, "type": _TYPE_OUT.get(e.type, "document")}

    def person(p: Person) -> dict:
        return {"literal": p.literal} if p.literal else (
            {"family": p.family, "given": p.given} if p.given
            else {"family": p.family})

    if e.authors:
        d["author"] = [person(p) for p in e.authors]
    if e.editors:
        d["editor"] = [person(p) for p in e.editors]
    pairs = {"title": e.title, "container-title": e.journal or e.booktitle,
             "publisher": e.publisher or (e.institution if e.type in
                                          ("thesis", "report") else ""),
             "publisher-place": e.location, "volume": e.volume,
             "issue": e.number, "page": e.pages.replace("–", "-"),
             "edition": e.edition, "collection-title": e.series,
             "DOI": e.doi, "URL": e.url, "ISBN": e.isbn, "ISSN": e.issn,
             "abstract": e.abstract, "language": e.language,
             "note": e.note}
    d.update({k: v for k, v in pairs.items() if v})
    if e.date:
        d["issued"] = {"date-parts": [[int(p) for p in e.date.split("-")]]}
    if e.keywords:
        d["keyword"] = ", ".join(e.keywords)
    return d
