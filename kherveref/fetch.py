"""Online metadata lookups: DOI, arXiv, ISBN, and title search.

* DOI   — doi.org content negotiation (CSL-JSON), which covers Crossref,
          DataCite (datasets, theses) and mEDRA; Crossref's REST API as a
          fallback.
* arXiv — the arXiv Atom API; when the preprint has a published DOI the
          journal version is used, keeping the arXiv id.
* ISBN  — OpenLibrary.
* Title — Crossref bibliographic search, accepted only on a near-exact
          title match, for PDFs that carry no identifier.

Every network call goes through `http_get`, which tests replace.
"""
from __future__ import annotations

import difflib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import __version__
from .csl import from_csl
from .model import Entry, Person, normalize_doi, normalize_title, parse_name

USER_AGENT = (f"KherveRef/{__version__} "
              "(+https://github.com/gkerherve/KherveRef)")
TIMEOUT = 20

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
ARXIV_NEW_RE = re.compile(
    r"(?:arxiv[:\s/]*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_OLD_RE = re.compile(
    r"(?:arxiv[:\s/]*|arxiv\.org/(?:abs|pdf)/)([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?",
    re.I)
ISBN_RE = re.compile(r"\bISBN(?:-1[03])?:?\s*((?:97[89][\s\-]?)?(?:\d[\s\-]?){9}[\dXx])")


class LookupError_(Exception):
    """The service answered that it has no such record."""


class NetworkError(Exception):
    """No answer at all (offline, DNS, timeout, server error)."""


def _ssl_context():
    # python.org builds on macOS ship without a CA store; certifi fixes
    # CERTIFICATE_VERIFY_FAILED there.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT,
                                    context=_ssl_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 400, 410):
            raise LookupError_(f"not found ({e.code})") from None
        raise NetworkError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}") \
            from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(str(getattr(e, "reason", e))) from None


# ------------------------------------------------------------------ #
# Identifier detection                                                 #
# ------------------------------------------------------------------ #

def clean_doi(raw: str) -> str:
    """Trim what regexes over running text drag along: trailing
    punctuation and an unmatched closing bracket."""
    pairs = {")": "(", "]": "["}
    d = raw.strip().rstrip(".,;:'\"")
    while d and d[-1] in pairs and d.count(d[-1]) > d.count(pairs[d[-1]]):
        d = d[:-1].rstrip(".,;:")
    return d


def find_doi(text: str) -> str:
    m = DOI_RE.search(text)
    return clean_doi(m.group(1)) if m else ""


def find_arxiv(text: str) -> str:
    m = ARXIV_NEW_RE.search(text) or ARXIV_OLD_RE.search(text)
    return m.group(1) if m else ""


def find_isbn(text: str) -> str:
    for m in ISBN_RE.finditer(text):
        digits = re.sub(r"[\s\-]", "", m.group(1)).upper()
        if _isbn_ok(digits):
            return digits
    return ""


def _isbn_ok(s: str) -> bool:
    if len(s) == 10:
        total = sum((10 - i) * (10 if c == "X" else int(c))
                    for i, c in enumerate(s))
        return total % 11 == 0
    if len(s) == 13 and s.isdigit():
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
        return total % 10 == 0
    return False


def classify(text: str) -> tuple[str, str]:
    """("doi"|"arxiv"|"isbn"|"", value) for something the user typed or
    pasted: a bare id, "doi:...", "arXiv:...", or a URL."""
    s = text.strip()
    if not s:
        return "", ""
    if re.search(r"arxiv", s, re.I) or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", s):
        a = find_arxiv(s if "arxiv" in s.lower() else f"arXiv:{s}")
        if a:
            return "arxiv", a
    d = find_doi(s)
    if d:
        return "doi", d
    digits = re.sub(r"[\s\-]", "", re.sub(r"^isbn[:\s]*", "", s, flags=re.I)).upper()
    if _isbn_ok(digits):
        return "isbn", digits
    return "", ""


# ------------------------------------------------------------------ #
# Lookups                                                              #
# ------------------------------------------------------------------ #

def lookup_doi(doi: str) -> Entry:
    doi = normalize_doi(doi)
    quoted = urllib.parse.quote(doi, safe="/:;()")
    try:
        data = json.loads(http_get(f"https://doi.org/{quoted}",
                                   "application/vnd.citationstyles.csl+json"))
    except (LookupError_, ValueError):
        try:
            msg = json.loads(http_get(
                f"https://api.crossref.org/works/{quoted}"))["message"]
        except ValueError:
            raise LookupError_(f"no record for DOI {doi}") from None
        data = msg
    e = from_csl(data)
    e.key = ""
    e.doi = e.doi or doi
    if not e.title:
        raise LookupError_(f"no record for DOI {doi}")
    return e


_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def lookup_arxiv(arxiv_id: str, follow_doi: bool = True) -> Entry:
    q = urllib.parse.quote(re.sub(r"v\d+$", "", arxiv_id))
    try:
        root = ET.fromstring(http_get(
            f"https://export.arxiv.org/api/query?id_list={q}",
            "application/atom+xml"))
    except ET.ParseError:
        raise NetworkError("arXiv returned an unreadable answer") from None
    entry = root.find(f"{_ATOM}entry")
    title = entry.findtext(f"{_ATOM}title") if entry is not None else None
    if entry is None or not title or entry.findtext(f"{_ATOM}id", "").endswith(
            "api/errors"):
        raise LookupError_(f"no arXiv record {arxiv_id}")
    doi = entry.findtext(f"{_ARXIV}doi", "").strip()
    if doi and follow_doi:
        try:
            e = lookup_doi(doi)
            e.eprint, e.eprinttype = arxiv_id, "arxiv"
            return e
        except (LookupError_, NetworkError):
            pass
    e = Entry(type="unpublished", eprint=re.sub(r"v\d+$", "", arxiv_id),
              eprinttype="arxiv")
    e.title = " ".join(title.split())
    e.abstract = " ".join(entry.findtext(f"{_ATOM}summary", "").split())
    e.authors = [parse_name(a.findtext(f"{_ATOM}name", ""))
                 for a in entry.findall(f"{_ATOM}author")]
    e.date = entry.findtext(f"{_ATOM}published", "")[:10]
    e.url = f"https://arxiv.org/abs/{e.eprint}"
    e.doi = doi
    jref = entry.findtext(f"{_ARXIV}journal_ref", "")
    if jref:
        e.note = " ".join(jref.split())
    return e


def lookup_isbn(isbn: str) -> Entry:
    isbn = re.sub(r"[\s\-]", "", isbn)
    data = json.loads(http_get(
        "https://openlibrary.org/api/books?format=json&jscmd=data"
        f"&bibkeys=ISBN:{urllib.parse.quote(isbn)}"))
    book = data.get(f"ISBN:{isbn}")
    if not book:
        raise LookupError_(f"no book with ISBN {isbn}")
    e = Entry(type="book", isbn=isbn, title=book.get("title", ""),
              subtitle=book.get("subtitle", ""))
    e.authors = [parse_name(a.get("name", "")) for a in book.get("authors", [])]
    m = re.search(r"\d{4}", book.get("publish_date", ""))
    e.date = m.group(0) if m else ""
    pubs = book.get("publishers") or []
    e.publisher = pubs[0].get("name", "") if pubs else ""
    places = book.get("publish_places") or []
    e.location = places[0].get("name", "") if places else ""
    return e


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_title(a),
                                   normalize_title(b)).ratio()


def search_title(title: str, author: str = "", threshold: float = 0.92
                 ) -> Entry | None:
    """The Crossref record whose title matches *title* almost exactly,
    or None. A loose match is worse than none: the user would get the
    wrong paper's metadata without noticing."""
    if len(normalize_title(title)) < 15:
        return None
    q = urllib.parse.urlencode({"query.bibliographic": f"{title} {author}".strip(),
                                "rows": 5})
    data = json.loads(http_get(f"https://api.crossref.org/works?{q}"))
    best, score = None, 0.0
    for item in data.get("message", {}).get("items", []):
        t = item.get("title") or [""]
        s = title_similarity(title, t[0] if isinstance(t, list) else t)
        if s > score:
            best, score = item, s
    if best is None or score < threshold:
        return None
    e = from_csl(best)
    e.key = ""
    return e


def lookup(kind: str, value: str) -> Entry:
    if kind == "doi":
        return lookup_doi(value)
    if kind == "arxiv":
        return lookup_arxiv(value)
    if kind == "isbn":
        return lookup_isbn(value)
    raise LookupError_(f"cannot look up {kind!r}")


def entry_from_identifier(text: str) -> Entry:
    kind, value = classify(text)
    if not kind:
        raise LookupError_(f"{text!r} is not a DOI, arXiv id or ISBN")
    return lookup(kind, value)


def person_list(names: str) -> list[Person]:
    """Authors from a PDF's Author field: "A; B", "A, B and C"."""
    parts = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", names.strip())
    if len(parts) == 1 and parts[0].count(",") > 1:
        parts = parts[0].split(",")
    return [parse_name(p) for p in parts if p.strip()]
