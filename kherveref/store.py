"""Reading and writing references and collections inside a library.

Each reference is .kherveref/references/<key>.json; its attachments
live in PDFs/.
`library.bib` at the library root is regenerated (classic BibTeX) after
every change — it is the contract KherveTeX reads, so it must always
match the entries.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import bibtex
from .keys import is_valid_key, unique_key
from .library import FILES_DIR, Library
from .model import (Attachment, Entry, normalize_arxiv, normalize_doi,
                    normalize_title, now_iso)

LIBRARY_BIB = "library.bib"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    # Windows refuses to replace a file someone holds open for a moment
    # (antivirus, the search indexer, a sync client): retry briefly.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ #
# Entries                                                              #
# ------------------------------------------------------------------ #

def load_entries(lib: Library) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for p in sorted(lib.entries_dir.glob("*.json")):
        try:
            e = Entry.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        e.key = e.key or p.stem
        out[e.key] = e
    return out


def entry_path(lib: Library, key: str) -> Path:
    if not is_valid_key(key):
        raise ValueError(f"invalid citation key {key!r}")
    return lib.entries_dir / f"{key}.json"


def save_entry(lib: Library, e: Entry) -> None:
    if not e.added:
        e.added = now_iso()
    e.modified = now_iso()
    _atomic_write(entry_path(lib, e.key), _json(e.to_dict()))


def delete_entry(lib: Library, e: Entry) -> None:
    """Remove the reference and the attachments only it uses. (The Git
    history keeps both, so nothing is lost for good.)"""
    entry_path(lib, e.key).unlink(missing_ok=True)
    for a in e.files:
        if a.path.startswith(FILES_DIR + "/"):
            (lib.root / a.path).unlink(missing_ok=True)


def add_entry(lib: Library, e: Entry, existing: dict[str, Entry]) -> Entry:
    """Give *e* a fresh unique key (keeping an imported key when it is
    valid and free), save it and register it in *existing*."""
    if not e.key or not is_valid_key(e.key) or e.key.lower() in {
            k.lower() for k in existing}:
        e.key = unique_key(e, existing.keys(), lib.key_style)
    save_entry(lib, e)
    existing[e.key] = e
    return e


def rename_keys(lib: Library, entries: dict[str, Entry],
                mapping: dict[str, str]) -> None:
    """Give references new keys ({old: new}) and rename the PDFs named
    after the old key to match. Two passes through temporary names, so
    keys may swap or chain (a->b, b->c) safely."""
    mapping = {o: n for o, n in mapping.items() if o in entries and o != n}
    if not mapping:
        return
    moved: list[tuple[Path, Path]] = []
    for old, new in mapping.items():
        e = entries[old]
        entry_path(lib, old).unlink(missing_ok=True)
        for att in e.files:
            name = att.path.rsplit("/", 1)[-1]
            if not att.path.startswith(FILES_DIR + "/") or not name.startswith(old):
                continue
            rest = name[len(old):]
            if rest and rest[0] not in ".-":
                continue                # another key that merely starts the same
            src = lib.root / att.path
            tmp = src.with_name(f".renaming-{new}{rest}")
            if src.exists():
                os.replace(src, tmp)
                moved.append((tmp, src.with_name(new + rest)))
            att.path = f"{FILES_DIR}/{new}{rest}"
    for tmp, final in moved:
        os.replace(tmp, final)
    renamed = {new: entries.pop(old) for old, new in mapping.items()}
    for new, e in renamed.items():
        e.key = new
        entries[new] = e
    for new in mapping.values():
        save_entry(lib, entries[new])


def keys_in_style(entries: dict[str, Entry], style: str) -> dict[str, str]:
    """{old key: key in *style*} for every reference, unique together."""
    taken: list[str] = []
    out = {}
    for old in sorted(entries, key=lambda k: (entries[k].added or "", k)):
        new = unique_key(entries[old], taken, style)
        taken.append(new)
        out[old] = new
    return out


def attach_file(lib: Library, e: Entry, src: Path, copy: bool = True) -> Attachment:
    """Copy *src* into files/ as <key>.<ext> (or <key>-2.<ext>...) and
    record it on the entry. The entry itself is not saved here."""
    src = Path(src)
    ext = src.suffix.lower() or ".bin"
    lib.files_dir.mkdir(exist_ok=True)
    dest = lib.files_dir / f"{e.key}{ext}"
    n = 2
    while dest.exists():
        dest = lib.files_dir / f"{e.key}-{n}{ext}"
        n += 1
    if copy:
        shutil.copy2(src, dest)
    else:
        shutil.move(src, dest)
    att = Attachment(path=f"{FILES_DIR}/{dest.name}", sha1=sha1_of(dest))
    e.files.append(att)
    return att


def file_path(lib: Library, att: Attachment) -> Path:
    return lib.root / att.path


# ------------------------------------------------------------------ #
# Duplicates                                                           #
# ------------------------------------------------------------------ #

@dataclass
class DuplicateIndex:
    """Finds a reference already in the library by DOI, arXiv id, ISBN,
    attached-file hash, or title + year."""
    doi: dict[str, str] = field(default_factory=dict)
    arxiv: dict[str, str] = field(default_factory=dict)
    isbn: dict[str, str] = field(default_factory=dict)
    sha1: dict[str, str] = field(default_factory=dict)
    title: dict[str, str] = field(default_factory=dict)
    # Short titles ("Notes", "My thesis") only count with the first author.
    title_author: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, entries) -> "DuplicateIndex":
        idx = cls()
        for e in entries:
            idx.add(e)
        return idx

    def add(self, e: Entry) -> None:
        if e.doi:
            self.doi[normalize_doi(e.doi)] = e.key
        if e.eprint and e.eprinttype == "arxiv":
            self.arxiv[normalize_arxiv(e.eprint)] = e.key
        if e.isbn:
            self.isbn[_isbn(e.isbn)] = e.key
        for a in e.files:
            if a.sha1:
                self.sha1[a.sha1] = e.key
        t = normalize_title(e.title)
        if len(t) > 12:
            self.title[f"{t}|{e.year}"] = e.key
        if t and _first_family(e):
            self.title_author[f"{t}|{e.year}|{_first_family(e)}"] = e.key

    def find(self, e: Entry, sha1: str = "") -> str | None:
        if sha1 and sha1 in self.sha1:
            return self.sha1[sha1]
        if e.doi and normalize_doi(e.doi) in self.doi:
            return self.doi[normalize_doi(e.doi)]
        if e.eprint and e.eprinttype == "arxiv" and \
                normalize_arxiv(e.eprint) in self.arxiv:
            return self.arxiv[normalize_arxiv(e.eprint)]
        if e.isbn and _isbn(e.isbn) in self.isbn:
            return self.isbn[_isbn(e.isbn)]
        t = normalize_title(e.title)
        if len(t) > 12 and f"{t}|{e.year}" in self.title:
            return self.title[f"{t}|{e.year}"]
        if t and _first_family(e):
            return self.title_author.get(f"{t}|{e.year}|{_first_family(e)}")
        return None


def _first_family(e: Entry) -> str:
    people = e.authors or e.editors
    return normalize_title(people[0].short()) if people else ""


def _isbn(s: str) -> str:
    return "".join(ch for ch in s.upper() if ch.isdigit() or ch == "X")


# ------------------------------------------------------------------ #
# Collections                                                          #
# ------------------------------------------------------------------ #

@dataclass
class Collection:
    id: str
    name: str
    parent: str = ""


def load_collections(lib: Library) -> list[Collection]:
    try:
        data = json.loads(lib.collections_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Collection(id=c["id"], name=c.get("name", ""),
                       parent=c.get("parent", ""))
            for c in data.get("collections", []) if c.get("id")]


def save_collections(lib: Library, cols: list[Collection]) -> None:
    data = {"collections": [
        {"id": c.id, "name": c.name, **({"parent": c.parent} if c.parent else {})}
        for c in cols]}
    _atomic_write(lib.collections_path,
                  json.dumps(data, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n")


def new_collection_id() -> str:
    return uuid.uuid4().hex[:8]


def collection_and_descendants(cols: list[Collection], cid: str) -> set[str]:
    out, todo = set(), [cid]
    while todo:
        c = todo.pop()
        if c in out:
            continue
        out.add(c)
        todo.extend(x.id for x in cols if x.parent == c)
    return out


# ------------------------------------------------------------------ #
# The KherveTeX contract                                               #
# ------------------------------------------------------------------ #

def write_library_bib(lib: Library, entries) -> Path:
    """Regenerate library.bib (classic BibTeX: KherveTeX compiles with
    natbib + bibtex). Only rewritten when its content changes."""
    path = lib.root / LIBRARY_BIB
    text = (f"% {lib.name} — generated by KherveRef. "
            "Do not edit: changes are overwritten.\n\n"
            + bibtex.to_bibtex(list(entries), "bibtex"))
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    _atomic_write(path, text)
    return path
