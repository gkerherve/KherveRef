"""Other managers' export formats -> model.

* RIS (.ris)            EndNote, Mendeley, Papers, Web of Science, Scopus,
                        PubMed, publishers' "Download citation"
* EndNote XML (.xml)    EndNote's own export, with its PDF links
* PubMed MEDLINE (.nbib, .medline, .txt from PubMed "Save > PubMed")

Each parser returns `Record`s: an Entry plus any PDFs the export points
at that exist on this computer.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .model import Entry, Person, parse_name


@dataclass
class Record:
    entry: Entry
    pdfs: list[Path] = field(default_factory=list)


@dataclass
class Parsed:
    records: list[Record] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _local_pdf(ref: str, base_dir: Path | None) -> Path | None:
    """A PDF path from an export: file:// URL, absolute or relative path."""
    ref = ref.strip()
    if not ref.lower().split("?")[0].endswith(".pdf"):
        return None
    if ref.lower().startswith("file:"):
        ref = urllib.parse.unquote(urllib.parse.urlparse(ref).path)
        if re.match(r"^/[A-Za-z]:/", ref):        # file:///C:/...
            ref = ref[1:]
    elif re.match(r"^[a-z][a-z0-9+.-]*://", ref, re.I):
        return None                                # a web link
    p = Path(ref)
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    return p if p.is_file() else None


def _date(year: str = "", rest: str = "") -> str:
    """'2020', '2020/05/17/', '2020 May 17', '2020-05' -> ISO-ish."""
    s = f"{year} {rest}".strip()
    m = re.search(r"(\d{4})(?:[/\-](\d{1,2}))?(?:[/\-](\d{1,2}))?", s)
    if not m:
        return ""
    y, mo, d = m.groups()
    if not mo:
        mon = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
                        s, re.I)
        if mon:
            mo = str(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                      "sep", "oct", "nov", "dec"].index(mon.group(1).lower()) + 1)
            dm = re.search(rf"{mon.group(1)}\w*\s+(\d{{1,2}})\b", s, re.I)
            d = dm.group(1) if dm else None
    if not mo or not 1 <= int(mo) <= 12:
        return y
    return "-".join([y] + [f"{int(x):02d}" for x in (mo, d) if x and int(x) > 0])


def _isbn_or_issn(e: Entry, value: str) -> None:
    v = value.strip()
    digits = re.sub(r"[^0-9Xx]", "", v)
    if len(digits) == 8:
        e.issn = e.issn or v
    elif len(digits) in (10, 13):
        e.isbn = e.isbn or v
    elif v:
        e.issn = e.issn or v


def _clean_doi(v: str) -> str:
    v = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", v.strip(), flags=re.I)
    return re.sub(r"^doi:\s*", "", v, flags=re.I).split()[0] if v else ""


# ------------------------------------------------------------------ #
# RIS                                                                  #
# ------------------------------------------------------------------ #

_RIS_TYPES = {
    "JOUR": "article", "JFULL": "article", "MGZN": "article",
    "NEWS": "article", "EJOUR": "article", "INPR": "article",
    "ABST": "article", "BOOK": "book", "EBOOK": "book", "EDBOOK": "book",
    "CHAP": "incollection", "ECHAP": "incollection", "CONF": "inproceedings",
    "CPAPER": "inproceedings", "THES": "thesis", "RPRT": "report",
    "GOVDOC": "report", "STAND": "report", "ELEC": "online", "WEB": "online",
    "BLOG": "online", "DATA": "dataset", "DBASE": "dataset",
    "COMP": "software", "PAT": "patent", "UNPB": "unpublished",
    "MANSCPT": "unpublished",
}
_RIS_LINE = re.compile(r"^([A-Z][A-Z0-9])  -(?: (.*))?$")


def looks_like_ris(text: str) -> bool:
    return bool(re.search(r"^TY  - ", text, re.M))


def parse_ris(text: str, base_dir: Path | None = None) -> Parsed:
    out = Parsed()
    tags: list[tuple[str, str]] = []
    for raw in text.lstrip("\ufeff").splitlines():
        m = _RIS_LINE.match(raw.rstrip())
        if m:
            tag, value = m.group(1), (m.group(2) or "").strip()
            if tag == "ER":
                if tags:
                    out.records.append(_ris_record(tags, base_dir))
                tags = []
            else:
                tags.append((tag, value))
        elif raw.strip() and tags:          # wrapped continuation line
            tag, value = tags[-1]
            tags[-1] = (tag, f"{value} {raw.strip()}")
    if tags:
        out.records.append(_ris_record(tags, base_dir))
        out.warnings.append("The last RIS record had no ER line.")
    return out


def _ris_record(tags: list[tuple[str, str]], base_dir) -> Record:
    get = lambda *names: [v for t, v in tags if t in names and v]  # noqa: E731
    first = lambda *names: (get(*names) or [""])[0]  # noqa: E731
    typ = first("TY").upper()
    e = Entry(type=_RIS_TYPES.get(typ, "misc"))
    e.authors = [parse_name(v) for v in get("AU", "A1")]
    e.editors = [parse_name(v) for v in get("ED", "A2")]
    e.title = first("TI", "T1", "CT")
    container = first("T2", "JF", "JO", "BT", "JA", "J2")
    if e.type in ("incollection", "inproceedings"):
        e.booktitle = container
    elif e.type in ("article", "online"):
        e.journal = first("JF", "JO", "T2", "JA", "J2")
    e.series = first("T3")
    e.date = _date(first("PY", "Y1", "DA"), first("DA", "Y2"))
    e.volume = first("VL")
    e.number = first("IS", "CP") or (first("M1") if e.type == "report" else "")
    sp, ep = first("SP"), first("EP")
    e.pages = (f"{sp}–{ep}" if sp and ep and sp != ep else sp).replace("-", "–")
    e.publisher = first("PB") if e.type not in ("thesis", "report") else ""
    if e.type in ("thesis", "report"):
        e.institution = first("PB")
        e.thesis_type = ("phd" if re.search(r"ph\.?d|doctor", first("M3"), re.I)
                         else "master" if re.search(r"master", first("M3"), re.I)
                         else "")
    e.location = first("CY", "PP")
    e.edition = first("ET")
    for v in get("SN"):
        _isbn_or_issn(e, v)
    e.doi = _clean_doi(first("DO", "DI") or
                       (first("M3") if first("M3").startswith("10.") else ""))
    urls = get("UR", "L2", "LK")
    if not e.doi:
        for u in urls:
            if "doi.org/" in u:
                e.doi = _clean_doi(u)
    e.url = next((u for u in urls if "doi.org/" not in u), "")
    e.abstract = first("AB", "N2")
    e.note = " ".join(get("N1"))
    e.language = first("LA")
    e.keywords = [k for v in get("KW") for k in re.split(r"\s*;\s*", v) if k]
    arx = re.search(r"arxiv[:./abs ]*(\d{4}\.\d{4,5})", " ".join(urls + [e.note]),
                    re.I)
    if arx:
        e.eprint, e.eprinttype = arx.group(1), "arxiv"
    pdfs = []
    for v in get("L1", "L4", "UR"):
        for part in v.split(";"):
            p = _local_pdf(part, base_dir)
            if p and p not in pdfs:
                pdfs.append(p)
    return Record(e, pdfs)


# ------------------------------------------------------------------ #
# EndNote XML                                                          #
# ------------------------------------------------------------------ #

_EN_TYPES = {
    "journal article": "article", "electronic article": "article",
    "magazine article": "article", "newspaper article": "article",
    "book": "book", "edited book": "book", "electronic book": "book",
    "book section": "incollection", "electronic book section": "incollection",
    "conference proceedings": "inproceedings", "conference paper": "inproceedings",
    "thesis": "thesis", "report": "report", "government document": "report",
    "web page": "online", "blog": "online", "dataset": "dataset",
    "computer program": "software", "patent": "patent",
    "unpublished work": "unpublished", "manuscript": "unpublished",
}


def looks_like_endnote(text: str) -> bool:
    head = text[:4000].lower()
    return "<records>" in head and "<record>" in head


def _t(node) -> str:
    """Text of an element, through EndNote's <style> wrappers."""
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_endnote_xml(text: str, base_dir: Path | None = None) -> Parsed:
    out = Parsed()
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as err:
        out.warnings.append(f"Not readable as EndNote XML: {err}")
        return out
    for rec in root.iter("record"):
        rt = rec.find("ref-type")
        typ = (rt.get("name", "") if rt is not None else "").lower()
        e = Entry(type=_EN_TYPES.get(typ, "misc"))
        contrib = rec.find("contributors")
        if contrib is not None:
            e.authors = [parse_name(_t(a)) for a in contrib.findall("authors/author")]
            e.editors = [parse_name(_t(a)) for a in
                         contrib.findall("secondary-authors/author")]
        titles = rec.find("titles")
        if titles is not None:
            e.title = _t(titles.find("title"))
            second = _t(titles.find("secondary-title"))
            if e.type in ("incollection", "inproceedings"):
                e.booktitle = second
            elif e.type in ("article", "online"):
                e.journal = second
            elif second:
                e.series = second
            e.series = e.series or _t(titles.find("tertiary-title"))
        if not e.journal and e.type == "article":
            e.journal = _t(rec.find("periodical/full-title")) or \
                _t(rec.find("periodical/abbr-1"))
        e.date = _date(_t(rec.find("dates/year")),
                       _t(rec.find("dates/pub-dates/date")))
        e.volume = _t(rec.find("volume"))
        e.number = _t(rec.find("number")) or _t(rec.find("issue"))
        e.pages = _t(rec.find("pages")).replace("-", "–")
        e.edition = _t(rec.find("edition"))
        pub = _t(rec.find("publisher"))
        if e.type in ("thesis", "report"):
            e.institution = pub
            wt = _t(rec.find("work-type")).lower()
            e.thesis_type = ("phd" if re.search(r"ph\.?d|doctor", wt)
                             else "master" if "master" in wt else "")
        else:
            e.publisher = pub
        e.location = _t(rec.find("pub-location"))
        _isbn_or_issn(e, _t(rec.find("isbn")))
        e.doi = _clean_doi(_t(rec.find("electronic-resource-num")))
        e.url = _t(rec.find("urls/related-urls/url"))
        e.abstract = _t(rec.find("abstract"))
        e.note = _t(rec.find("notes"))
        e.language = _t(rec.find("language"))
        e.keywords = [_t(k) for k in rec.findall("keywords/keyword") if _t(k)]
        label = _t(rec.find("label"))
        if label and re.fullmatch(r"[A-Za-z][\w:\-]*", label):
            e.key = label
        pdfs = []
        for u in rec.findall("urls/pdf-urls/url"):
            p = _endnote_pdf(_t(u), base_dir)
            if p is not None:
                pdfs.append(p)
            elif _t(u):
                out.warnings.append(f"PDF not found for “{e.title[:50]}”: {_t(u)}")
        out.records.append(Record(e, pdfs))
    return out


def _endnote_pdf(ref: str, base_dir: Path | None) -> Path | None:
    """internal-pdf://<n>/<name>.pdf lives in <Library>.Data/PDF/<n>/
    next to the .enl — look beside the XML for it."""
    if ref.startswith("internal-pdf://"):
        rel = urllib.parse.unquote(ref[len("internal-pdf://"):])
        if base_dir is None:
            return None
        for data in sorted(base_dir.glob("*.Data")):
            p = data / "PDF" / rel
            if p.is_file():
                return p
        name = Path(rel).name
        hits = list(base_dir.glob(f"*.Data/PDF/**/{name}"))
        return hits[0] if len(hits) == 1 else None
    return _local_pdf(ref, base_dir)


# ------------------------------------------------------------------ #
# PubMed / MEDLINE (.nbib)                                             #
# ------------------------------------------------------------------ #

_NBIB_LINE = re.compile(r"^([A-Z][A-Z0-9 ]{1,3})- (.*)$")


def looks_like_nbib(text: str) -> bool:
    return bool(re.search(r"^PMID- \d+", text, re.M))


def parse_nbib(text: str) -> Parsed:
    out = Parsed()
    tags: list[tuple[str, str]] = []

    def flush():
        if tags:
            out.records.append(Record(_nbib_entry(tags)))

    for raw in text.lstrip("\ufeff").splitlines():
        m = _NBIB_LINE.match(raw)
        if m:
            tag = m.group(1).strip()
            if tag == "PMID" and tags:
                flush()
                tags = []
            tags.append((tag, m.group(2).strip()))
        elif raw.startswith("      ") and tags:     # continuation
            tag, value = tags[-1]
            tags[-1] = (tag, f"{value} {raw.strip()}")
    flush()
    return out


def _expand_pages(pg: str) -> str:
    """MEDLINE abbreviates end pages: 1123-9 -> 1123–1129."""
    m = re.fullmatch(r"(\d+)-(\d+)", pg.strip())
    if not m:
        return pg.replace("-", "–")
    a, b = m.groups()
    if len(b) < len(a):
        b = a[:len(a) - len(b)] + b
    return f"{a}–{b}"


def _nbib_entry(tags: list[tuple[str, str]]) -> Entry:
    get = lambda name: [v for t, v in tags if t == name and v]  # noqa: E731
    first = lambda name: (get(name) or [""])[0]  # noqa: E731
    pts = " ".join(get("PT")).lower()
    e = Entry(type="book" if "book" in pts and not first("TA") else "article")
    full = get("FAU")
    e.authors = [parse_name(a) for a in full] if full else \
        [_short_author(a) for a in get("AU")]
    e.title = first("TI").rstrip(".") or first("BTI")
    e.journal = first("JT") or first("TA")
    e.date = _date(first("DP"), first("DP"))
    e.volume = first("VI")
    e.number = first("IP")
    e.pages = _expand_pages(first("PG"))
    for v in get("AID") + get("LID"):
        if v.endswith("[doi]") and not e.doi:
            e.doi = v[:-len("[doi]")].strip()
    e.issn = first("IS").split()[0] if first("IS") else ""
    e.abstract = first("AB")
    e.language = first("LA")
    e.keywords = get("OT") + [h.split("/")[0].lstrip("*") for h in get("MH")]
    pmid = first("PMID")
    if pmid:
        e.extra["pmid"] = pmid
        e.url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    pmc = first("PMC")
    if pmc:
        e.extra["pmcid"] = pmc
    return e


def _short_author(au: str) -> Person:
    """MEDLINE "Smith JA" -> Smith, J. A."""
    m = re.fullmatch(r"(.+?)\s+([A-Z]{1,4})", au.strip())
    if not m:
        return parse_name(au)
    return Person(family=m.group(1), given=" ".join(f"{c}." for c in m.group(2)))
