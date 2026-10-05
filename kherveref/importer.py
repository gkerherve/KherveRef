"""Turning things the user drops in into references.

PDFs, folders of PDFs, .bib / .json (CSL) files and identifiers (DOI,
arXiv, ISBN) all go through `Importer`, which the GUI drives from a
worker thread and the MCP server calls directly. It never commits: the
caller commits once per batch, so a folder import is one Git commit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import bibtex, csl, fetch, formats, store
from .fetch import LookupError_, NetworkError
from .library import Library
from .model import Entry
from .pdf_meta import inspect_pdf

PDF_EXT = ".pdf"
BIB_EXT = {".bib", ".bibtex"}
CSL_EXT = {".json"}
RIS_EXT = {".ris"}
NBIB_EXT = {".nbib", ".medline"}
XML_EXT = {".xml"}               # EndNote XML (checked by content)
# Everything File ▸ Import accepts, for its file dialog.
IMPORT_PATTERNS = "*.bib *.bibtex *.ris *.xml *.nbib *.medline *.json"

ADDED = "added"
ATTACHED = "attached"           # PDF of a reference already in the library
DUPLICATE = "duplicate"
REVIEW = "needs checking"       # added, but the metadata is a guess
FAILED = "failed"
NOTE = "note"                   # nothing imported; something to know


@dataclass
class Outcome:
    source: str
    status: str
    key: str = ""
    message: str = ""


@dataclass
class Summary:
    outcomes: list[Outcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def changed(self) -> bool:
        return any(o.status in (ADDED, ATTACHED, REVIEW) for o in self.outcomes)

    def headline(self) -> str:
        parts = []
        for status, label in ((ADDED, "added"), (REVIEW, "added, need checking"),
                              (ATTACHED, "PDFs attached to existing references"),
                              (DUPLICATE, "already in the library"),
                              (FAILED, "failed"), (NOTE, "notes")):
            n = self.count(status)
            if n:
                parts.append(f"{n} {label}")
        return ", ".join(parts) or "Nothing to import"


def expand_paths(paths: Iterable[Path]) -> list[Path]:
    """Files to import: folders are searched recursively for PDFs and
    bibliography files; hidden folders are skipped."""
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if any(part.startswith(".") for part in f.relative_to(p).parts):
                    continue
                ext = f.suffix.lower()
                if not f.is_file():
                    continue
                if ext in {PDF_EXT, *BIB_EXT, *RIS_EXT, *NBIB_EXT} or (
                        ext in XML_EXT and _is_endnote_file(f)):
                    out.append(f)
        elif p.is_file():
            out.append(p)
    return out


class Importer:
    def __init__(self, lib: Library, entries: dict[str, Entry] | None = None,
                 online: bool = True, collection: str = ""):
        self.lib = lib
        self.entries = entries if entries is not None else store.load_entries(lib)
        self.dups = store.DuplicateIndex.build(self.entries.values())
        self.online = online
        self.collection = collection
        self.summary = Summary()

    # ----- shared -----

    def _record(self, o: Outcome) -> Outcome:
        self.summary.outcomes.append(o)
        return o

    def _add(self, e: Entry, source: str, review: bool, message: str = "",
             pdf: Path | None = None) -> Outcome:
        if self.collection and self.collection not in e.collections:
            e.collections.append(self.collection)
        e.needs_review = e.needs_review or review
        store.add_entry(self.lib, e, self.entries)
        if pdf is not None:
            store.attach_file(self.lib, e, pdf)
            store.save_entry(self.lib, e)
        self.dups.add(e)
        return self._record(Outcome(source, REVIEW if e.needs_review else ADDED,
                                    e.key, message))

    def _existing(self, e: Entry, sha1: str = "") -> Entry | None:
        key = self.dups.find(e, sha1)
        return self.entries.get(key) if key else None

    # ----- PDFs -----

    def import_pdf(self, path: Path) -> Outcome:
        source = str(path)
        try:
            sha1 = store.sha1_of(path)
        except OSError as e:
            return self._record(Outcome(source, FAILED, message=str(e)))
        dup = self._existing(Entry(), sha1)
        if dup:
            return self._record(Outcome(source, DUPLICATE, dup.key,
                                        "same file already attached"))
        info = inspect_pdf(path)
        if info.warnings and not info.pages:
            return self._record(Outcome(source, FAILED,
                                        message="; ".join(info.warnings)))
        entry, review, note = self._identify(info, path)

        dup = self._existing(entry)
        if dup:
            if not any(a.path.lower().endswith(".pdf") for a in dup.files):
                store.attach_file(self.lib, dup, path)
                store.save_entry(self.lib, dup)
                self.dups.add(dup)
                return self._record(Outcome(source, ATTACHED, dup.key))
            return self._record(Outcome(source, DUPLICATE, dup.key))
        return self._add(entry, source, review, note, pdf=path)

    def _identify(self, info, path: Path) -> tuple[Entry, bool, str]:
        """(entry, needs_review, message) for a PDF."""
        offline_note = ""
        if self.online:
            for kind, value in (("doi", info.doi), ("arxiv", info.arxiv),
                                ("isbn", info.isbn)):
                if not value:
                    continue
                try:
                    e = fetch.lookup(kind, value)
                    if kind == "arxiv":
                        e.eprint, e.eprinttype = value, "arxiv"
                    return e, False, f"found by {kind.upper()} {value}"
                except LookupError_:
                    continue
                except NetworkError as err:
                    offline_note = f"lookup failed: {err}"
                    break
            if info.title and not offline_note:
                try:
                    e = fetch.search_title(info.title, info.authors)
                    if e is not None:
                        return e, False, "found by title"
                except (LookupError_, NetworkError) as err:
                    offline_note = f"title search failed: {err}"
        e = Entry(type="article" if (info.doi or info.arxiv) else "misc")
        e.title = info.title or path.stem.replace("_", " ")
        e.authors = fetch.person_list(info.authors) if info.authors else []
        e.date = info.year
        e.doi = info.doi
        if info.arxiv:
            e.eprint, e.eprinttype = info.arxiv, "arxiv"
        e.isbn = info.isbn
        why = offline_note or ("no identifier found; details guessed from the PDF"
                               if info.has_text else
                               "no text in the PDF (scanned?); please fill in")
        return e, True, why

    # ----- bibliography files -----

    def import_bib_text(self, text: str, source: str = "BibTeX",
                        base_dir: Path | None = None) -> None:
        res = bibtex.parse(text)
        for w in res.warnings:
            self._record(Outcome(source, FAILED, message=w))
        for e in res.entries:
            pdf = _bib_file_field(e, base_dir)
            self._import_record(e, f"{source}: {e.key}", pdf)

    def import_csl_text(self, text: str, source: str = "CSL-JSON") -> None:
        try:
            data = json.loads(text)
        except ValueError as err:
            self._record(Outcome(source, FAILED, message=f"not JSON: {err}"))
            return
        items = data if isinstance(data, list) else data.get("items", [data])
        for item in items:
            if isinstance(item, dict):
                e = csl.from_csl(item)
                self._import_record(e, f"{source}: {e.key or e.title[:40]}")

    def import_records(self, parsed: "formats.Parsed", source: str) -> None:
        """RIS / EndNote XML / MEDLINE records, with the PDFs they link."""
        for w in parsed.warnings:
            self._record(Outcome(source, NOTE, message=w))
        for rec in parsed.records:
            e = rec.entry
            label = f"{source}: {e.title[:50] or e.key or '(untitled)'}"
            try:
                sha1 = store.sha1_of(rec.pdfs[0]) if rec.pdfs else ""
            except OSError:
                sha1 = ""
            dup = self._existing(e, sha1)
            if dup:
                if rec.pdfs and not dup.files:
                    for pdf in rec.pdfs:
                        store.attach_file(self.lib, dup, pdf)
                    store.save_entry(self.lib, dup)
                    self.dups.add(dup)
                    self._record(Outcome(label, ATTACHED, dup.key))
                else:
                    self._record(Outcome(label, DUPLICATE, dup.key))
                continue
            o = self._add(e, label, review=not e.title,
                          pdf=rec.pdfs[0] if rec.pdfs else None)
            for extra in rec.pdfs[1:]:
                store.attach_file(self.lib, e, extra)
                store.save_entry(self.lib, e)
            if rec.pdfs:
                o.message = f"{len(rec.pdfs)} PDF(s)"

    def import_text(self, text: str, source: str = "Pasted text",
                    base_dir: Path | None = None) -> None:
        """Any supported format, recognised from the text itself."""
        stripped = text.lstrip("\ufeff \n\r\t")
        if formats.looks_like_ris(text):
            self.import_records(formats.parse_ris(text, base_dir), source)
        elif formats.looks_like_nbib(text):
            self.import_records(formats.parse_nbib(text), source)
        elif stripped.startswith("<") and formats.looks_like_endnote(text):
            self.import_records(formats.parse_endnote_xml(text, base_dir), source)
        elif stripped.startswith(("[", "{")):
            self.import_csl_text(text, source)
        else:
            self.import_bib_text(text, source, base_dir)

    def _import_record(self, e: Entry, source: str,
                       pdf: Path | None = None) -> Outcome:
        dup = self._existing(e)
        if dup:
            return self._record(Outcome(source, DUPLICATE, dup.key))
        return self._add(e, source, review=not e.title, pdf=pdf)

    # ----- Zotero -----

    def import_zotero(self, data_dir: Path,
                      progress: Callable[[int, int, str], None] | None = None,
                      cancelled: Callable[[], bool] | None = None) -> Summary:
        """Every reference of a Zotero library, with its PDFs, collections
        (recreated, or matched by name on a second import), tags and
        notes."""
        from . import zotero
        zlib = zotero.read_library(data_dir)
        cols = store.load_collections(self.lib)
        by_name = {(c.name, c.parent): c.id for c in cols}
        ids: dict[str, str] = {}
        pending = list(zlib.collections)
        # Parents before children, whatever order Zotero stored them in.
        while pending:
            progressed = False
            for item in list(pending):
                zkey, name, zparent = item
                if zparent and zparent not in ids and \
                        any(p[0] == zparent for p in pending):
                    continue
                parent = ids.get(zparent, "")
                cid = by_name.get((name, parent))
                if cid is None:
                    cid = store.new_collection_id()
                    cols.append(store.Collection(cid, name, parent))
                    by_name[(name, parent)] = cid
                ids[zkey] = cid
                pending.remove(item)
                progressed = True
            if not progressed:      # a parent loop: file the rest at top level
                for zkey, name, _ in pending:
                    ids[zkey] = by_name.setdefault((name, ""), store.new_collection_id())
                    if not any(c.id == ids[zkey] for c in cols):
                        cols.append(store.Collection(ids[zkey], name))
                break
        store.save_collections(self.lib, cols)

        total = len(zlib.items)
        for i, item in enumerate(zlib.items):
            if cancelled and cancelled():
                break
            e = item.entry
            if progress:
                progress(i, total, e.title[:60] or e.key)
            source = f"Zotero: {e.title[:60]}"
            try:
                sha1 = store.sha1_of(item.pdfs[0]) if item.pdfs else ""
            except OSError:
                sha1 = ""
            dup = self._existing(e, sha1)
            if dup:
                self._record(Outcome(source, DUPLICATE, dup.key))
                continue
            e.collections = [ids[k] for k in item.collection_keys if k in ids]
            store.add_entry(self.lib, e, self.entries)
            for pdf in item.pdfs:
                try:
                    store.attach_file(self.lib, e, pdf)
                except OSError as err:
                    self._record(Outcome(str(pdf), NOTE, e.key, str(err)))
            store.save_entry(self.lib, e)
            self.dups.add(e)
            self._record(Outcome(source, ADDED, e.key,
                                 f"{len(e.files)} PDF(s)" if e.files else ""))
        for w in zlib.warnings:
            self._record(Outcome("Zotero", NOTE, message=w))
        if progress:
            progress(total, total, "")
        if self.summary.changed:
            store.write_library_bib(self.lib, self.entries.values())
        return self.summary

    # ----- identifiers -----

    def import_identifier(self, text: str) -> Outcome:
        source = text.strip()
        kind, value = fetch.classify(source)
        if not kind:
            return self._record(Outcome(source, FAILED,
                                        message="not a DOI, arXiv id or ISBN"))
        probe = Entry(doi=value if kind == "doi" else "",
                      eprint=value if kind == "arxiv" else "",
                      eprinttype="arxiv" if kind == "arxiv" else "",
                      isbn=value if kind == "isbn" else "")
        dup = self._existing(probe)
        if dup:
            return self._record(Outcome(source, DUPLICATE, dup.key))
        try:
            e = fetch.lookup(kind, value)
        except (LookupError_, NetworkError) as err:
            return self._record(Outcome(source, FAILED, message=str(err)))
        if kind == "arxiv":
            e.eprint, e.eprinttype = value, "arxiv"
        return self._import_record(e, source)

    # ----- dispatch -----

    def import_path(self, path: Path) -> None:
        ext = path.suffix.lower()
        try:
            if ext == PDF_EXT:
                self.import_pdf(path)
            elif ext in BIB_EXT:
                self.import_bib_text(path.read_text(encoding="utf-8",
                                                    errors="replace"),
                                     path.name, path.parent)
            elif ext in CSL_EXT:
                self.import_csl_text(path.read_text(encoding="utf-8"), path.name)
            elif ext in RIS_EXT | NBIB_EXT | XML_EXT or ext == ".txt":
                self.import_text(_read_text(path), path.name, path.parent)
            else:
                self._record(Outcome(str(path), FAILED,
                                     message="not a PDF or a bibliography file "
                                             "(.bib, .ris, EndNote .xml, .nbib, "
                                             "CSL .json)"))
        except Exception as err:     # one bad file must not stop a batch
            self._record(Outcome(str(path), FAILED, message=str(err)))

    def run(self, paths: Iterable[Path],
            progress: Callable[[int, int, str], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> Summary:
        files = expand_paths(paths)
        for i, f in enumerate(files):
            if cancelled and cancelled():
                break
            if progress:
                progress(i, len(files), f.name)
            self.import_path(f)
        if progress:
            progress(len(files), len(files), "")
        if self.summary.changed:
            store.write_library_bib(self.lib, self.entries.values())
        return self.summary


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_endnote_file(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return formats.looks_like_endnote(fh.read(4000).decode("utf-8", "ignore"))
    except OSError:
        return False


def _bib_file_field(e: Entry, base_dir: Path | None) -> Path | None:
    """The PDF named in a JabRef/Zotero `file` field, if it exists."""
    raw = e.extra.pop("file", "")
    if not raw or base_dir is None:
        return None
    for part in raw.split(";"):
        bits = part.split(":")
        # JabRef: "desc:path:type"; Zotero: "path" or "desc:path:type";
        # a Windows drive letter adds a colon of its own.
        candidates = [part] + [":".join(bits[1:-1])] + bits
        for c in candidates:
            c = c.strip().replace("\\:", ":")
            if not c.lower().endswith(".pdf"):
                continue
            p = Path(c)
            if not p.is_absolute():
                p = base_dir / p
            if p.is_file():
                return p
    return None


def refresh_from_identifiers(e: Entry) -> str:
    """Re-run the lookup for an entry the user is fixing. Returns a
    message; raises LookupError_/NetworkError on failure."""
    for kind, value in (("doi", e.doi),
                        ("arxiv", e.eprint if e.eprinttype == "arxiv" else ""),
                        ("isbn", e.isbn)):
        if value:
            found = fetch.lookup(kind, value)
            e.replace_bibliographic(found)
            if kind == "arxiv":
                e.eprint, e.eprinttype = value, "arxiv"
            e.needs_review = False
            return f"Updated from {kind.upper()} {value}"
    if e.title:
        found = fetch.search_title(e.title, e.authors[0].family if e.authors else "")
        if found is not None:
            e.replace_bibliographic(found)
            e.needs_review = False
            return "Updated from a Crossref title match"
    raise LookupError_("No DOI, arXiv id or ISBN, and no exact title match")

