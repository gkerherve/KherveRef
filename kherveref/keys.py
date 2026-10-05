"""Citation keys — the short names LaTeX cites, e.g. \\cite{smith2020deep}.

A key is generated when a reference enters the library, in the style
the library chose (KEY_STYLES), and is not changed afterwards unless the
user renames it — documents cite it.
"""
from __future__ import annotations

import re
import unicodedata

from .model import Entry

_STOPWORDS = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "at",
              "by", "with", "from", "into", "via", "towards", "toward",
              "is", "are", "what", "how", "why", "do", "does", "le", "la",
              "les", "un", "une", "des", "der", "die", "das", "el", "los"}


def _ascii(s: str) -> str:
    s = s.replace("ß", "ss").replace("ø", "o").replace("Ø", "O") \
         .replace("æ", "ae").replace("œ", "oe").replace("ł", "l")
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode().lower())


# id -> example and description, as offered under Library ▸ Citation key style.
KEY_STYLES: dict[str, str] = {
    "author_year_word": "smith2020surface — author, year and first title word",
    "Author_Year": "Smith2020 — author and year",
    "author_year": "smith2020 — author and year, lower case",
}
DEFAULT_KEY_STYLE = "author_year_word"


def base_key(e: Entry, style: str = DEFAULT_KEY_STYLE) -> str:
    people = e.authors or e.editors
    if people:
        p = people[0]
        name = _ascii(p.literal.split()[0] if p.literal else p.family.split()[-1]
                      if p.family else "")
    else:
        name = ""
    name = name or "anon"
    if style == "Author_Year":
        return f"{name.capitalize()}{e.year}"[:40]
    if style == "author_year":
        return f"{name}{e.year}"[:40]
    word = ""
    for w in re.findall(r"[^\W_]+", e.title):
        if w.lower() not in _STOPWORDS:
            word = _ascii(w)
            if word:
                break
    return f"{name}{e.year}{word}"[:40]


def unique_key(e: Entry, taken, style: str = DEFAULT_KEY_STYLE) -> str:
    """base_key(e), with b, c, ... z, then 2, 3... appended on collision
    (case-insensitive, since some file systems are)."""
    used = {k.lower() for k in taken}
    base = base_key(e, style)
    if base.lower() not in used:
        return base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if (base + suffix).lower() not in used:
            return base + suffix
    n = 2
    while f"{base}{n}".lower() in used:
        n += 1
    return f"{base}{n}"


_VALID_KEY = re.compile(r"^[A-Za-z0-9_:\-./+]+$")


def is_valid_key(key: str) -> bool:
    """Keys that are safe both in \\cite{} and as a file name."""
    return bool(_VALID_KEY.match(key)) and key not in (".", "..") \
        and "/" not in key
