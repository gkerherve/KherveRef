"""LaTeX <-> Unicode for BibTeX field values.

The library stores plain Unicode ("Müller", "–"), which is what the
editor shows and what CSL/Crossref deliver. BibTeX files often spell
accents as macros ({\\"u}, \\'{e}, \\c{c}); those are decoded on import.
The classic-BibTeX writer encodes them back, because BibTeX's sorting
and label code is not UTF-8 aware; the BibLaTeX writer keeps UTF-8.
"""
from __future__ import annotations

import re
import unicodedata

# accent macro -> Unicode combining mark
_ACCENTS = {
    "'": "́", "`": "̀", "^": "̂", '"': "̈",
    "~": "̃", "=": "̄", ".": "̇", "u": "̆",
    "v": "̌", "H": "̋", "c": "̧", "k": "̨",
    "r": "̊", "d": "̣", "b": "̱",
}
_COMBINING_TO_MACRO = {v: k for k, v in _ACCENTS.items()}

# Letter-like macros.
_SYMBOLS = {
    "ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ",
    "OE": "Œ", "aa": "å", "AA": "Å", "l": "ł", "L": "Ł", "i": "ı",
    "j": "ȷ", "dh": "ð", "DH": "Ð", "th": "þ", "TH": "Þ", "ng": "ŋ",
    "NG": "Ŋ", "S": "§", "P": "¶", "copyright": "©", "textregistered": "®",
    "texttrademark": "™", "textdegree": "°", "textendash": "–",
    "textemdash": "—", "textquoteleft": "‘", "textquoteright": "’",
    "textquotedblleft": "“", "textquotedblright": "”", "dag": "†",
    "ddag": "‡", "textellipsis": "…", "ldots": "…", "dots": "…",
    "textasciitilde": "~", "textbackslash": "\\", "pounds": "£",
    "euro": "€", "textmu": "µ",
}
_SYMBOL_TO_MACRO = {v: k for k, v in _SYMBOLS.items()
                    if k not in ("ldots", "dots", "textellipsis", "i", "j",
                                 "textasciitilde", "textbackslash",
                                 "textendash", "textemdash", "textquoteleft",
                                 "textquoteright", "textquotedblleft",
                                 "textquotedblright")}
_SYMBOL_TO_MACRO["…"] = "ldots"

_ESCAPED = {"&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_"}

_ACCENT_RE = re.compile(
    r"\{?\\([" + re.escape("'`^\"~=.") + r"]|[uvHckrdb](?=[\s{]))\s*"
    r"(?:\{\s*(\\[ij]|[A-Za-z])\s*\}|(\\[ij]|[A-Za-z]))\}?")
_SYMBOL_RE = re.compile(
    r"\{?\\(" + "|".join(sorted(map(re.escape, _SYMBOLS), key=len,
                                reverse=True)) + r")(?![A-Za-z])(?:\{\})?\s?\}?")


def _accent(m: re.Match) -> str:
    macro, base = m.group(1), m.group(2) or m.group(3)
    if base in ("\\i", "\\j"):
        base = base[1]
    return unicodedata.normalize("NFC", base + _ACCENTS[macro])


def latex_to_unicode(s: str) -> str:
    """Decode accent and symbol macros, escaped specials and dashes.
    Math ($...$) and unknown macros are left alone."""
    if "\\" not in s and "--" not in s and "~" not in s:
        return s
    parts = re.split(r"(\$[^$]*\$)", s)
    for i in range(0, len(parts), 2):
        p = parts[i]
        p = _ACCENT_RE.sub(_accent, p)
        p = _SYMBOL_RE.sub(lambda m: _SYMBOLS[m.group(1)], p)
        for ch, esc in _ESCAPED.items():
            p = p.replace(esc, ch)
        p = p.replace("---", "—").replace("--", "–")
        p = re.sub(r"(?<!\\)~", " ", p)
        p = p.replace("``", "“").replace("''", "”")
        parts[i] = p
    return "".join(parts)


def unicode_to_latex(s: str, ascii_only: bool = True) -> str:
    """Encode for a .bib value. Escapes BibTeX specials always; with
    *ascii_only* also turns accented letters into macros."""
    parts = re.split(r"(\$[^$]*\$)", s)
    for i in range(0, len(parts), 2):
        out = []
        for ch in parts[i]:
            if ch in _ESCAPED and not _already_escaped(out):
                out.append(_ESCAPED[ch])
            elif not ascii_only or ord(ch) < 128:
                out.append(ch)
            else:
                out.append(_encode_char(ch))
        parts[i] = "".join(out)
    return "".join(parts)


def _already_escaped(out: list[str]) -> bool:
    return bool(out) and out[-1].endswith("\\")


def _encode_char(ch: str) -> str:
    if ch == "–":
        return "--"
    if ch == "—":
        return "---"
    if ch == " ":
        return "~"
    if ch in "‘’":
        return "`" if ch == "‘" else "'"
    if ch in "“”":
        return "``" if ch == "“" else "''"
    if ch in _SYMBOL_TO_MACRO:
        return "{\\" + _SYMBOL_TO_MACRO[ch] + "}"
    decomposed = unicodedata.normalize("NFD", ch)
    base, marks = decomposed[0], decomposed[1:]
    if marks and all(m in _COMBINING_TO_MACRO for m in marks) and ord(base) < 128:
        # Always the braced form: {\"{o}}, {\'{\i}}, {\c{c}} — valid in
        # BibTeX names, sorting and every engine.
        out = "\\" + base if base in "ij" else base
        for m in marks:
            out = f"\\{_COMBINING_TO_MACRO[m]}{{{out}}}"
        return "{" + out + "}"
    return ch   # no ASCII spelling: leave it for the engine


def strip_braces(s: str) -> str:
    """Drop BibTeX case-protection braces ({DNA} -> DNA), keeping math."""
    parts = re.split(r"(\$[^$]*\$)", s)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(?<!\\)[{}]", "", parts[i])
    return "".join(parts)


def protect_case(title: str) -> str:
    """Brace words BibTeX styles must not lower-case: acronyms (XPS),
    mixed case (TiO2, pH, McDonald) and single capitals after the first
    word ("Fig. A"). Capitalised ordinary words are left to the style."""
    def fix(m: re.Match) -> str:
        w = m.group(0)
        if any(c.isupper() for c in w[1:]) or (len(w) == 1 and w.isupper()
                                               and m.start() > 0):
            return "{" + w + "}"
        return w
    parts = re.split(r"(\$[^$]*\$|\\[A-Za-z]+|\{[^{}]*\})", title)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"[^\W_][\w\-]*", fix, parts[i])
    return "".join(parts)
