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

# id -> label, in menu order.
STYLES: dict[str, str] = {
    "apa": "APA 7th (author–date)",
    "harvard-cite-them-right": "Harvard (Cite Them Right)",
    "chicago-author-date": "Chicago (author–date)",
    "vancouver": "Vancouver (numbered)",
    "ieee": "IEEE (numbered)",
    "nature": "Nature (numbered)",
    "american-chemical-society": "ACS (numbered)",
    "royal-society-of-chemistry": "RSC (numbered)",
}
DEFAULT_STYLE = "apa"
NUMERIC = {"vancouver", "ieee", "nature", "american-chemical-society",
           "royal-society-of-chemistry"}

_STYLE_CACHE: dict[str, object] = {}
# citeproc-py glues the number to the entry in "[1]J. Smith" layouts.
_LABEL_RE = re.compile(r"^(\[\d+\]|\(\d+\)|\d+\.?)(?=[^\s\d.\])])")


def _style(style_id: str):
    from citeproc import CitationStylesStyle
    if style_id not in STYLES:
        style_id = DEFAULT_STYLE
    if style_id not in _STYLE_CACHE:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _STYLE_CACHE[style_id] = CitationStylesStyle(
                str(STYLES_DIR / f"{style_id}.csl"), validate=False)
    return _STYLE_CACHE[style_id]


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
    if style_id in NUMERIC:
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
