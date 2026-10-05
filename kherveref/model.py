"""The reference model — the single source of truth.

Every importer (BibTeX, CSL-JSON, DOI/arXiv/ISBN lookups, PDFs, Zotero)
produces `Entry` objects; every exporter reads them. Field names follow
BibLaTeX where one exists, so the BibLaTeX writer is almost a straight
copy and the classic-BibTeX writer is a small mapping.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field, fields

# BibLaTeX entry types offered in the editor, with a readable label.
ENTRY_TYPES: dict[str, str] = {
    "article": "Journal article",
    "inproceedings": "Conference paper",
    "book": "Book",
    "inbook": "Book chapter",
    "incollection": "Chapter in edited book",
    "thesis": "Thesis",
    "report": "Report",
    "online": "Web page / online",
    "dataset": "Dataset",
    "software": "Software",
    "patent": "Patent",
    "unpublished": "Unpublished / preprint",
    "misc": "Other",
}

# Text fields of an Entry, in display / export order.
TEXT_FIELDS = (
    "title", "subtitle", "date", "journal", "booktitle", "publisher",
    "institution", "volume", "number", "pages", "edition", "series",
    "location", "doi", "url", "isbn", "issn", "eprint", "eprinttype",
    "thesis_type", "abstract", "note", "language",
)

_PARTICLES = {"von", "van", "der", "den", "de", "del", "della", "di", "da",
              "du", "dos", "das", "la", "le", "ter", "ten", "zu", "af", "al",
              "el", "bin", "ibn", "st.", "y"}


@dataclass
class Person:
    family: str = ""
    given: str = ""
    # An organisation ("World Health Organization") — never split.
    literal: str = ""

    def display(self) -> str:
        if self.literal:
            return self.literal
        return f"{self.family}, {self.given}" if self.given else self.family

    def short(self) -> str:
        return self.literal or self.family

    def to_dict(self) -> dict:
        if self.literal:
            return {"literal": self.literal}
        d = {"family": self.family}
        if self.given:
            d["given"] = self.given
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Person":
        return cls(family=d.get("family", ""), given=d.get("given", ""),
                   literal=d.get("literal", ""))


def parse_name(text: str) -> Person:
    """One name in any of the usual spellings: "Family, Given",
    "Family, Jr, Given", "Given Family", "Given von Family", or a
    braced organisation "{World Health Organization}"."""
    s = " ".join(text.split())
    if not s:
        return Person()
    if s.startswith("{") and s.endswith("}") and _balanced(s[1:-1]):
        return Person(literal=s[1:-1])
    parts = _split_top_level(s, ",")
    if len(parts) >= 2:
        family = parts[0].strip()
        if len(parts) >= 3:     # "Family, Jr, Given"
            family = f"{family} {parts[1].strip()}"
            given = ", ".join(p.strip() for p in parts[2:])
        else:
            given = parts[1].strip()
        return Person(family=_unbrace(family), given=_unbrace(given))
    words = s.split(" ")
    if len(words) == 1:
        return Person(family=_unbrace(words[0]))
    # The family name starts at the first lower-case particle ("van",
    # "de"...) after the given names, else it is the last word.
    start = len(words) - 1
    for i, w in enumerate(words[1:-1], start=1):
        if w.lower() in _PARTICLES and w[0].islower():
            start = i
            break
    return Person(family=_unbrace(" ".join(words[start:])),
                  given=_unbrace(" ".join(words[:start])))


def parse_names(text: str) -> list[Person]:
    """A BibTeX name list ("A and B and others") or one name per line."""
    if "\n" in text.strip():
        chunks = [line for line in text.splitlines()]
    else:
        chunks = _split_top_level(text, " and ", ignore_case=True)
    out = []
    for c in chunks:
        c = c.strip()
        if c and c.lower() != "others":
            out.append(parse_name(c))
    return out


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False
    return depth == 0


def _unbrace(s: str) -> str:
    return s.replace("{", "").replace("}", "").strip()


def _split_top_level(s: str, sep: str, ignore_case: bool = False) -> list[str]:
    """Split on *sep* outside braces."""
    hay = s.lower() if ignore_case else s
    needle = sep.lower() if ignore_case else sep
    out, depth, last, i = [], 0, 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0 and hay.startswith(needle, i):
            out.append(s[last:i])
            i += len(needle)
            last = i
            continue
        i += 1
    out.append(s[last:])
    return out


@dataclass
class Attachment:
    path: str           # relative to the library root, "/" separated
    sha1: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "sha1": self.sha1}

    @classmethod
    def from_dict(cls, d) -> "Attachment":
        if isinstance(d, str):
            return cls(path=d)
        return cls(path=d.get("path", ""), sha1=d.get("sha1", ""))


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


@dataclass
class Entry:
    key: str = ""
    type: str = "article"
    authors: list[Person] = field(default_factory=list)
    editors: list[Person] = field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    date: str = ""          # "2020", "2020-05" or "2020-05-17"
    journal: str = ""       # BibLaTeX journaltitle
    booktitle: str = ""
    publisher: str = ""
    institution: str = ""   # also the school of a thesis
    volume: str = ""
    number: str = ""
    pages: str = ""
    edition: str = ""
    series: str = ""
    location: str = ""
    doi: str = ""
    url: str = ""
    isbn: str = ""
    issn: str = ""
    eprint: str = ""        # e.g. an arXiv id
    eprinttype: str = ""    # e.g. "arxiv"
    thesis_type: str = ""   # "phd" / "master" for @thesis
    abstract: str = ""
    note: str = ""
    language: str = ""
    keywords: list[str] = field(default_factory=list)
    # Any other BibTeX field, kept verbatim so imports round-trip.
    extra: dict[str, str] = field(default_factory=dict)

    # Library bookkeeping — never exported to BibTeX.
    files: list[Attachment] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    notes: str = ""
    needs_review: bool = False
    added: str = ""
    modified: str = ""

    @property
    def year(self) -> str:
        m = re.match(r"\s*(\d{4})", self.date)
        return m.group(1) if m else ""

    def container(self) -> str:
        """Where it was published, for the table's Journal column."""
        return (self.journal or self.booktitle or self.publisher
                or self.institution or (f"arXiv:{self.eprint}"
                                        if self.eprinttype == "arxiv"
                                        and self.eprint else ""))

    def author_text(self, limit: int = 2) -> str:
        names = self.authors or self.editors
        if not names:
            return ""
        if len(names) > limit:
            return f"{names[0].short()} et al."
        return " & ".join(p.short() for p in names)

    def to_dict(self) -> dict:
        d: dict = {"key": self.key, "type": self.type}
        if self.authors:
            d["authors"] = [p.to_dict() for p in self.authors]
        if self.editors:
            d["editors"] = [p.to_dict() for p in self.editors]
        for name in TEXT_FIELDS:
            v = getattr(self, name)
            if v:
                d[name] = v
        if self.keywords:
            d["keywords"] = list(self.keywords)
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        if self.files:
            d["files"] = [a.to_dict() for a in self.files]
        if self.collections:
            d["collections"] = sorted(set(self.collections))
        if self.notes:
            d["notes"] = self.notes
        if self.needs_review:
            d["needs_review"] = True
        for name in ("added", "modified"):
            if getattr(self, name):
                d[name] = getattr(self, name)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        e = cls(key=d.get("key", ""), type=d.get("type", "misc"))
        e.authors = [Person.from_dict(p) for p in d.get("authors", [])]
        e.editors = [Person.from_dict(p) for p in d.get("editors", [])]
        for name in TEXT_FIELDS:
            setattr(e, name, str(d.get(name, "") or ""))
        e.keywords = list(d.get("keywords", []))
        e.extra = {str(k): str(v) for k, v in d.get("extra", {}).items()}
        e.files = [Attachment.from_dict(a) for a in d.get("files", [])]
        e.collections = list(d.get("collections", []))
        e.notes = d.get("notes", "")
        e.needs_review = bool(d.get("needs_review", False))
        e.added = d.get("added", "")
        e.modified = d.get("modified", "")
        return e

    def merge_missing(self, other: "Entry") -> None:
        """Fill fields this entry lacks from *other* (e.g. a looked-up
        record), never overwriting what the user already has."""
        if not self.authors and other.authors:
            self.authors = list(other.authors)
        if not self.editors and other.editors:
            self.editors = list(other.editors)
        for name in TEXT_FIELDS:
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if not self.keywords:
            self.keywords = list(other.keywords)
        for k, v in other.extra.items():
            self.extra.setdefault(k, v)

    def replace_bibliographic(self, other: "Entry") -> None:
        """Take every bibliographic field from *other* (a trusted lookup),
        keeping key, files, collections, notes and dates."""
        for f in fields(self):
            if f.name in ("key", "files", "collections", "notes", "added",
                          "modified", "needs_review"):
                continue
            val = getattr(other, f.name)
            if val:
                setattr(self, f.name, val if not isinstance(val, (list, dict))
                        else type(val)(val))


def normalize_doi(doi: str) -> str:
    s = doi.strip()
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.rstrip(".,;").lower()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def normalize_arxiv(eprint: str) -> str:
    s = re.sub(r"^arxiv:\s*", "", eprint.strip(), flags=re.I)
    return re.sub(r"v\d+$", "", s).lower()
