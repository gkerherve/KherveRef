"""BibTeX / BibLaTeX import and export.

Import accepts either dialect (journal or journaltitle, year or date,
school or institution...) and folds it into the model. Export writes:

* "biblatex" — the default for File ▸ Export: UTF-8, `date`,
  `journaltitle`, `@thesis`, `@online`...
* "bibtex" — classic BibTeX for natbib documents, which is what
  KherveTeX compiles with (tectonic has no biber): ASCII with accent
  macros, `year`/`month`, `journal`, `@phdthesis`, `@techreport`...

Output is deterministic (fixed field order, sorted by key) so a library
export diffs cleanly in Git.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .latex import latex_to_unicode, protect_case, strip_braces, unicode_to_latex
from .model import Entry, Person, parse_names

DIALECTS = ("biblatex", "bibtex")

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
           "oct", "nov", "dec"]
_MONTH_NAMES = {m: i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_NAMES.update({
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12})

# BibTeX entry type -> (model type, thesis_type)
_TYPE_IN = {
    "article": ("article", ""), "inproceedings": ("inproceedings", ""),
    "conference": ("inproceedings", ""), "proceedings": ("book", ""),
    "book": ("book", ""), "mvbook": ("book", ""), "booklet": ("book", ""),
    "inbook": ("inbook", ""), "bookinbook": ("inbook", ""),
    "incollection": ("incollection", ""), "collection": ("book", ""),
    "phdthesis": ("thesis", "phd"), "mastersthesis": ("thesis", "master"),
    "thesis": ("thesis", ""), "techreport": ("report", ""),
    "report": ("report", ""), "manual": ("report", ""),
    "online": ("online", ""), "electronic": ("online", ""),
    "www": ("online", ""), "webpage": ("online", ""),
    "dataset": ("dataset", ""), "data": ("dataset", ""),
    "software": ("software", ""), "patent": ("patent", ""),
    "unpublished": ("unpublished", ""), "misc": ("misc", ""),
}

# BibTeX field -> model field (direct copies, LaTeX decoded).
_FIELD_IN = {
    "title": "title", "subtitle": "subtitle", "journal": "journal",
    "journaltitle": "journal", "booktitle": "booktitle",
    "publisher": "publisher", "institution": "institution",
    "school": "institution", "organization": "institution",
    "volume": "volume", "number": "number", "issue": "number",
    "pages": "pages", "edition": "edition", "series": "series",
    "address": "location", "location": "location", "doi": "doi",
    "url": "url", "isbn": "isbn", "issn": "issn", "eprint": "eprint",
    "eprinttype": "eprinttype", "archiveprefix": "eprinttype",
    "abstract": "abstract", "note": "note", "language": "language",
    "langid": "language",
}
# Fields that are identifiers or URLs: never LaTeX-decoded.
_VERBATIM = {"doi", "url", "eprint", "isbn", "issn", "file"}
# Read but not stored as `extra` (rebuilt on export, or app-specific).
_DROPPED = {"year", "month", "date", "author", "editor", "keywords",
            "type", "timestamp", "owner", "urldate"}


@dataclass
class ParseResult:
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# Lexing                                                               #
# ------------------------------------------------------------------ #

class _Reader:
    def __init__(self, text: str):
        self.s, self.i = text, 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def ident(self) -> str:
        m = re.compile(r"[^\s,={}()\"#%'@]+").match(self.s, self.i)
        if not m:
            return ""
        self.i = m.end()
        return m.group(0)

    def braced(self) -> str:
        assert self.s[self.i] == "{"
        depth, start = 0, self.i + 1
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "\\":
                self.i += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.s[start:self.i - 1]
            self.i += 1
        raise ValueError("unbalanced braces")

    def quoted(self) -> str:
        assert self.s[self.i] == '"'
        self.i += 1
        depth, start = 0, self.i
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "\\":
                self.i += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == '"' and depth == 0:
                self.i += 1
                return self.s[start:self.i - 1]
            self.i += 1
        raise ValueError("unterminated quoted value")

    def value(self, strings: dict[str, str]) -> str:
        """A field value: pieces joined by #, macros expanded."""
        out = []
        while True:
            self.ws()
            ch = self.peek()
            if ch == "{":
                out.append(self.braced())
            elif ch == '"':
                out.append(self.quoted())
            else:
                word = self.ident()
                if not word:
                    raise ValueError("expected a value")
                out.append(word if word.isdigit()
                           else strings.get(word.lower(), word))
            self.ws()
            if self.peek() == "#":
                self.i += 1
                continue
            return "".join(out)


def _skip_to_next_entry(r: _Reader) -> None:
    j = r.s.find("@", r.i)
    r.i = len(r.s) if j < 0 else j


def parse(text: str) -> ParseResult:
    """Parse BibTeX/BibLaTeX source. Malformed entries are skipped with a
    warning instead of failing the whole file."""
    res = ParseResult()
    strings = {m: m for m in _MONTHS}
    r = _Reader(text)
    while True:
        _skip_to_next_entry(r)
        if r.i >= len(r.s):
            break
        start = r.i
        r.i += 1
        kind = r.ident().lower()
        r.ws()
        if r.peek() not in "{(":
            continue
        close = "}" if r.peek() == "{" else ")"
        r.i += 1
        try:
            if kind == "comment":
                r.i -= 1
                if close == "}":
                    r.braced()
                continue
            if kind == "preamble":
                r.value(strings)
                r.ws()
                r.i += 1
                continue
            if kind == "string":
                r.ws()
                name = r.ident().lower()
                r.ws()
                r.i += 1    # '='
                strings[name] = r.value(strings)
                r.ws()
                r.i += 1
                continue
            r.ws()
            key = r.ident()
            r.ws()
            if r.peek() == ",":
                r.i += 1
            raw: dict[str, str] = {}
            while True:
                r.ws()
                if r.peek() in (close, ""):
                    r.i += 1
                    break
                name = r.ident().lower()
                r.ws()
                if r.peek() != "=":
                    raise ValueError(f"expected '=' after {name!r}")
                r.i += 1
                raw[name] = r.value(strings)
                r.ws()
                if r.peek() == ",":
                    r.i += 1
            if not key:
                res.warnings.append(f"Skipped a @{kind} entry with no key")
                continue
            res.entries.append(entry_from_fields(kind, key, raw))
        except (ValueError, AssertionError, IndexError) as e:
            line = r.s.count("\n", 0, start) + 1
            res.warnings.append(f"Line {line}: skipped a malformed entry ({e})")
            r.i = start + 1
    return res


# ------------------------------------------------------------------ #
# Fields -> model                                                      #
# ------------------------------------------------------------------ #

def _clean(value: str) -> str:
    return " ".join(strip_braces(latex_to_unicode(value)).split())


def _date_from(raw: dict[str, str]) -> str:
    if raw.get("date"):
        m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?",
                     raw["date"])
        if m:
            return "-".join(f"{int(g):02d}" if i else g
                            for i, g in enumerate(m.groups()) if g)
    year = re.search(r"\d{4}", raw.get("year", ""))
    if not year:
        return ""
    month = raw.get("month", "").strip().lower().rstrip(".")
    if month.isdigit():
        mnum = int(month) if 1 <= int(month) <= 12 else None
    else:
        mnum = _MONTH_NAMES.get(month) or _MONTH_NAMES.get(month[:3])
    return f"{year.group(0)}-{mnum:02d}" if mnum else year.group(0)


def entry_from_fields(kind: str, key: str, raw: dict[str, str]) -> Entry:
    typ, thesis = _TYPE_IN.get(kind, ("misc", ""))
    e = Entry(key=key, type=typ, thesis_type=thesis)
    e.authors = [_clean_person(p) for p in parse_names(raw.get("author", ""))]
    e.editors = [_clean_person(p) for p in parse_names(raw.get("editor", ""))]
    e.date = _date_from(raw)
    for name, value in raw.items():
        if name in _FIELD_IN:
            target = _FIELD_IN[name]
            if getattr(e, target):
                continue    # journal and journaltitle both present
            v = value.strip() if name in _VERBATIM else _clean(value)
            setattr(e, target, v)
        elif name not in _DROPPED:
            e.extra[name] = value.strip()
    if e.eprinttype:
        e.eprinttype = e.eprinttype.lower()
    if raw.get("type"):
        if kind == "thesis":
            t = raw["type"].lower()
            e.thesis_type = "phd" if "phd" in t or "doctor" in t else (
                "master" if "master" in t or t == "mathesis" else raw["type"])
        elif typ != "thesis":
            e.extra["type"] = raw["type"]
    kw = raw.get("keywords", "")
    if kw:
        e.keywords = [_clean(k) for k in re.split(r"[;,]", kw) if k.strip()]
    if e.doi:
        e.doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", e.doi, flags=re.I)
    return e


def _clean_person(p: Person) -> Person:
    return Person(family=_clean(p.family), given=_clean(p.given),
                  literal=_clean(p.literal))


# ------------------------------------------------------------------ #
# Model -> BibTeX                                                      #
# ------------------------------------------------------------------ #

_TYPE_OUT_BIBTEX = {
    "article": "article", "inproceedings": "inproceedings", "book": "book",
    "inbook": "inbook", "incollection": "incollection",
    "report": "techreport", "online": "misc", "dataset": "misc",
    "software": "misc", "patent": "misc", "unpublished": "unpublished",
    "misc": "misc",
}


def _name_out(p: Person, ascii_only: bool) -> str:
    enc = lambda s: unicode_to_latex(s, ascii_only)  # noqa: E731
    if p.literal:
        return "{" + enc(p.literal) + "}"
    if not p.given:
        return enc(p.family)
    family = enc(p.family)
    # A family name with a space is braced unless it starts with a
    # particle, which BibTeX handles itself ("van der Berg").
    if " " in p.family and not p.family.split()[0].islower():
        family = "{" + family + "}"
    return f"{family}, {enc(p.given)}"


def _brace(v: str) -> str:
    return "{" + v + "}"


def entry_to_bibtex(e: Entry, dialect: str = "biblatex") -> str:
    bl = dialect == "biblatex"
    ascii_only = not bl
    enc = lambda s: unicode_to_latex(s, ascii_only)  # noqa: E731
    fields: list[tuple[str, str]] = []

    def add(name: str, value: str, raw: bool = False):
        if value:
            fields.append((name, value if raw else enc(value)))

    if bl:
        kind = e.type
    elif e.type == "thesis":
        kind = "mastersthesis" if e.thesis_type == "master" else "phdthesis"
    else:
        kind = _TYPE_OUT_BIBTEX.get(e.type, "misc")

    if e.authors:
        add("author", " and ".join(_name_out(p, ascii_only) for p in e.authors),
            raw=True)
    if e.editors:
        add("editor", " and ".join(_name_out(p, ascii_only) for p in e.editors),
            raw=True)
    if e.title:
        add("title", protect_case(enc(e.title)), raw=True)
    if e.subtitle:
        if bl:
            add("subtitle", protect_case(enc(e.subtitle)), raw=True)
        elif e.title:
            fields[-1] = ("title", protect_case(enc(f"{e.title}: {e.subtitle}")))
    if e.journal:
        add("journaltitle" if bl else "journal", e.journal)
    add("booktitle", e.booktitle)
    if bl:
        add("date", e.date, raw=True)
    else:
        add("year", e.year, raw=True)
        m = re.match(r"\d{4}-(\d{2})", e.date)
        if m and 1 <= int(m.group(1)) <= 12:
            fields.append(("month", _MONTHS[int(m.group(1)) - 1]))
    add("volume", e.volume)
    add("number", e.number)
    add("pages", e.pages.replace("–", "--").replace("—", "--"))
    add("edition", e.edition)
    add("series", e.series)
    add("publisher", e.publisher)
    if e.institution:
        if e.type == "thesis" and not bl:
            add("school", e.institution)
        elif e.type in ("inproceedings", "misc", "online") and not bl:
            add("organization", e.institution)
        else:
            add("institution", e.institution)
    if e.type == "thesis" and bl and e.thesis_type:
        add("type", {"phd": "phdthesis", "master": "mathesis"}.get(
            e.thesis_type, e.thesis_type))
    add("location" if bl else "address", e.location)
    add("doi", e.doi, raw=True)
    add("url", e.url, raw=True)
    add("isbn", e.isbn, raw=True)
    add("issn", e.issn, raw=True)
    if e.eprint:
        add("eprint", e.eprint, raw=True)
        if e.eprinttype:
            add("eprinttype" if bl else "archiveprefix",
                e.eprinttype if bl else e.eprinttype.replace("arxiv", "arXiv"),
                raw=True)
    if not bl and e.type in ("online", "dataset", "software") and e.url:
        add("howpublished", "\\url{" + e.url + "}", raw=True)
    if not bl and e.type == "unpublished" and not e.note and e.eprint:
        add("note", f"arXiv:{e.eprint}")
    add("note", e.note)
    add("language" if not bl else "langid", e.language)
    if e.keywords:
        add("keywords", ", ".join(e.keywords))
    add("abstract", e.abstract)
    taken = {n for n, _ in fields}
    for name, value in sorted(e.extra.items()):
        if name not in taken:
            fields.append((name, value))

    width = max((len(n) for n, _ in fields), default=0)
    body = ",\n".join(f"  {n.ljust(width)} = {_brace(v)}" for n, v in fields)
    return f"@{kind}{{{e.key},\n{body}\n}}\n"


def to_bibtex(entries, dialect: str = "biblatex") -> str:
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}")
    return "\n".join(entry_to_bibtex(e, dialect)
                     for e in sorted(entries, key=lambda e: e.key.lower()))
