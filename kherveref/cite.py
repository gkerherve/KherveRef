"""Formatted citations and reference lists in real journal styles.

Uses citeproc-py with Citation Style Language files shipped in
kherveref/styles/ (the same style files Zotero and Mendeley use). Shared
by drag-and-drop into Word, "Copy formatted reference" and the Word
add-in's citations and bibliography.
"""
from __future__ import annotations

import html
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

from . import csl
from .model import Entry

STYLES_DIR = Path(__file__).resolve().parent / "styles"

# Bundled styles (they work offline), by field, in menu order:
# {group: {id: label}}. Any other journal's style can be added with
# journal_styles (Edit ▸ Citation style ▸ Find a journal style…).
GROUPS: dict[str, dict[str, str]] = {
    "General": {
        "apa": "APA 7th (author–date)",
        "harvard-cite-them-right": "Harvard (Cite Them Right)",
        "chicago-author-date": "Chicago (author–date)",
        "vancouver": "Vancouver (numbered)",
        "iso690-author-date-en": "ISO 690 (author–date)",
    },
    "Publishers": {
        "elsevier-with-titles": "Elsevier (numbered)",
        "elsevier-harvard": "Elsevier Harvard (author–date)",
        "springer-basic-brackets": "Springer (numbered)",
        "springer-basic-author-date": "Springer (author–date)",
        "taylor-and-francis-chicago-author-date": "Taylor & Francis (author–date)",
        "sage-harvard": "SAGE Harvard (author–date)",
        "multidisciplinary-digital-publishing-institute": "MDPI (numbered)",
        "frontiers": "Frontiers (author–date)",
        "plos": "PLOS (numbered)",
        "biomed-central": "BioMed Central (numbered)",
        "copernicus-publications": "Copernicus (author–date)",
    },
    "Multidisciplinary journals": {
        "nature": "Nature (numbered)",
        "science": "Science (numbered)",
        "pnas": "PNAS (numbered)",
        "cell": "Cell (numbered)",
    },
    "Chemistry & materials": {
        "american-chemical-society": "ACS (numbered)",
        "royal-society-of-chemistry": "RSC (numbered)",
        "angewandte-chemie": "Angewandte Chemie (numbered)",
        "advanced-materials": "Advanced Materials / Wiley-VCH (numbered)",
    },
    "Physics & earth sciences": {
        "american-institute-of-physics": "AIP (numbered)",
        "american-physics-society": "APS – Physical Review (numbered)",
        "institute-of-physics-numeric": "IOP (numbered)",
        "american-geophysical-union": "AGU (author–date)",
    },
    "Medicine & life sciences": {
        "american-medical-association": "AMA (numbered)",
        "bmj": "BMJ (numbered)",
        "the-lancet": "The Lancet (numbered)",
    },
    "Engineering & computing": {
        "ieee": "IEEE (numbered)",
        "association-for-computing-machinery": "ACM (numbered)",
        "springer-lecture-notes-in-computer-science": "Springer LNCS (numbered)",
    },
    "Humanities & social sciences": {
        "modern-language-association": "MLA 9th (author)",
        "american-sociological-association": "ASA (author–date)",
        "american-political-science-association": "APSA (author–date)",
    },
}
STYLES: dict[str, str] = {sid: label for g in GROUPS.values() for sid, label in g.items()}
DEFAULT_STYLE = "apa"
NUMERIC = {sid for sid, label in STYLES.items() if "(numbered)" in label}

_STYLE_CACHE: dict[str, object] = {}
# citeproc-py glues the number to the entry in "[1]J. Smith" layouts.
_LABEL_RE = re.compile(r"^(\[\d+\]|\(\d+\)|\d+\.?)(?=[^\s\d.\])])")


def all_styles() -> dict[str, str]:
    """Bundled styles, then the journal styles added on this computer."""
    from . import journal_styles
    out = dict(STYLES)
    for sid, rec in journal_styles.installed().items():
        out.setdefault(sid, rec["title"])
    return out


def grouped_styles() -> list[tuple[str, str, str]]:
    """(group, id, label) for every style: the journals added here
    first, then the bundled ones by field."""
    from . import journal_styles
    added = sorted(journal_styles.installed().items(),
                   key=lambda kv: kv[1]["title"].lower())
    return ([("Your journals", sid, rec["title"]) for sid, rec in added
             if sid not in STYLES]
            + [(g, sid, label) for g, styles in GROUPS.items()
               for sid, label in styles.items()])


def is_numeric(style_id: str) -> bool:
    if style_id in STYLES:
        return style_id in NUMERIC
    from . import journal_styles
    rec = journal_styles.installed().get(style_id)
    return bool(rec and rec.get("format") == "numeric")


def style_file(style_id: str) -> Path:
    if style_id in STYLES:
        return STYLES_DIR / f"{style_id}.csl"
    from . import journal_styles
    path = journal_styles.style_path(style_id)
    return path if path else STYLES_DIR / f"{DEFAULT_STYLE}.csl"


def _style(style_id: str):
    from citeproc import CitationStylesStyle
    path = style_file(style_id)
    if str(path) not in _STYLE_CACHE:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _STYLE_CACHE[str(path)] = CitationStylesStyle(str(path), validate=False)
    return _STYLE_CACHE[str(path)]


def _item(e: Entry) -> dict:
    d = csl.to_csl(e)
    d["id"] = e.key
    return d


@dataclass
class Formatted:
    citations: list[str]        # one per citation cluster, HTML
    bibliography: list[str]     # one per entry, HTML, in style order

    @property
    def citations_text(self) -> list[str]:
        return [to_text(c) for c in self.citations]

    @property
    def bibliography_text(self) -> list[str]:
        return [to_text(b) for b in self.bibliography]


def format_document(clusters: list[list[str]], entries: dict[str, Entry],
                    style_id: str = DEFAULT_STYLE) -> Formatted:
    """Citations for *clusters* (each a list of keys, in document order)
    and the reference list of everything cited. Numbered styles number
    by first appearance; unknown keys show as "?key"."""
    from citeproc import Citation, CitationItem, CitationStylesBibliography, formatter
    from citeproc.source.json import CiteProcJSON

    known = {k for c in clusters for k in c if k in entries}
    source = CiteProcJSON([_item(entries[k]) for k in sorted(known)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bib = CitationStylesBibliography(_style(style_id), source, formatter.html)
        cites = []
        for keys in clusters:
            ok = [k for k in keys if k in known]
            c = Citation([CitationItem(k) for k in ok]) if ok else None
            if c is not None:
                bib.register(c)
            cites.append((c, [k for k in keys if k not in known]))
        out = []
        for c, missing in cites:
            text = str(bib.cite(c, lambda item: None)) if c is not None else ""
            if missing:
                text = (text + " " if text else "") + " ".join(f"?{k}" for k in missing)
            out.append(text)
        refs = [_LABEL_RE.sub(r"\1 ", str(b)) for b in bib.bibliography()]
    return Formatted(out, refs)


def format_citation(entries: list[Entry], style_id: str = DEFAULT_STYLE) -> str:
    """One citation of *entries*, as text, outside any document. Numbered
    styles have no number without a document, so they fall back to APA's
    author–date form, which reads correctly anywhere."""
    if is_numeric(style_id):
        style_id = DEFAULT_STYLE
    f = format_document([[e.key for e in entries]], {e.key: e for e in entries},
                        style_id)
    return to_text(f.citations[0])


def format_reference(entries: list[Entry], style_id: str = DEFAULT_STYLE
                     ) -> list[str]:
    """Reference-list entries (HTML) for *entries*."""
    f = format_document([[e.key] for e in entries], {e.key: e for e in entries},
                        style_id)
    return f.bibliography


def to_text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) > 127:
            out.append(f"\\u{ord(ch) if ord(ch) < 32768 else ord(ch) - 65536}?")
        else:
            out.append(ch)
    return "".join(out)


def html_to_rtf(fragment: str) -> str:
    """The <i>/<b>/<sup> HTML citeproc produces, as RTF runs."""
    parts = re.split(r"(</?(?:i|b|sup|sub)>)", fragment)
    codes = {"<i>": r"{\i ", "<b>": r"{\b ", "<sup>": r"{\super ",
             "<sub>": r"{\sub "}
    out = []
    for p in parts:
        if p in codes:
            out.append(codes[p])
        elif p in ("</i>", "</b>", "</sup>", "</sub>"):
            out.append("}")
        else:
            out.append(rtf_escape(to_text(p)))
    return "".join(out)
