"""The tools KherveRef offers Claude over MCP, and what each one does.

Qt-free like mcp_server.py, which speaks the protocol: these work on the
library files directly and commit each change. When a KherveRef window
is open they also reach it (ipc.query) to see what the user has selected,
show references and open PDFs, and they work on the library it shows.
"""
from __future__ import annotations

import base64
import difflib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import (__version__, bibtex, cite, fulltext, git_backend, importer, ipc,
               library, state, store)
from .model import ENTRY_TYPES, TEXT_FIELDS, Entry, parse_names

#: A result holding this key is sent to Claude as an image it can look at.
IMAGE_KEY = "image_png_base64"


def _obj(props: dict, required: list[str] = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _i(desc: str) -> dict:
    return {"type": "integer", "description": desc}


_KEY = _s("Citation key exactly as returned by search_references, "
          "e.g. smith2020surface.")
_KEYS = {"type": "array", "items": {"type": "string"},
         "description": "Citation keys exactly as returned by search_references."}
_COLL = _s("Collection name (case-insensitive). Optional.")
_NEW_COLL = _s("Collection to file the new references in; created if "
               "missing. Optional.")

_READ = {"readOnlyHint": True, "openWorldHint": False}
_ADD = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True}
_EDIT = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
_WINDOW = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
           "openWorldHint": False}


def _tool(name: str, title: str, description: str, schema: dict,
          hints: dict) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": schema, "annotations": {"title": title, **hints}}


TOOLS: list[dict] = [
    _tool("get_status", "Library status",
          "Call this FIRST. The library the tools work on (name, folder, "
          "how many references, with PDFs, needing checking, collections), "
          "whether the KherveRef window is open, and the references the "
          "user has SELECTED there — what 'this paper' means.",
          _obj({}), _READ),
    _tool("list_libraries", "List libraries",
          "Libraries the user has opened in KherveRef, and the one (in_use) "
          "every other tool works on.", _obj({}), _READ),
    _tool("search_references", "Search references",
          "Find references by words in their key, authors, title, journal, "
          "year, DOI, keywords or notes; every word must match. An empty "
          "query lists the most recently added. Returns key, authors, year, "
          "title, where published, DOI, has_pdf and needs_checking.",
          _obj({"query": _s("Words to match, e.g. 'smith titania 2020'. "
                            "Empty lists everything."),
                "collection": _COLL,
                "needs_checking": {"type": "boolean", "description":
                                   "Only references flagged for checking."},
                "has_pdf": {"type": "boolean", "description":
                            "Only references with (true) or without (false) "
                            "an attached PDF."},
                "limit": _i("Most results to return (default 20).")}), _READ),
    _tool("get_reference", "Get reference",
          "Every field of one reference (authors, date, journal, volume, "
          "pages, DOI, abstract, notes, attachments, collections) and its "
          "BibLaTeX.", _obj({"key": _KEY}, ["key"]), _READ),
    _tool("add_reference", "Add by DOI / arXiv / ISBN",
          "Add a reference from a DOI (10.xxxx/...), arXiv id (2101.00001) "
          "or ISBN; its details are looked up online. Returns the citation "
          "key, or the existing key if it is already in the library.",
          _obj({"identifier": _s("DOI, arXiv id or ISBN; a doi.org or "
                                 "arxiv.org URL also works."),
                "collection": _NEW_COLL}, ["identifier"]), _ADD),
    _tool("add_bibtex", "Add from BibTeX / RIS text",
          "Add references from exported text: BibTeX/BibLaTeX, RIS, "
          "EndNote XML, PubMed MEDLINE (.nbib) or CSL-JSON (the format is "
          "detected). Returns a key and status per reference.",
          _obj({"bibtex": _s("The text to import, in any of those formats."),
                "collection": _NEW_COLL}, ["bibtex"]), _ADD),
    _tool("add_pdf", "Add PDF",
          "Add a PDF file, or every PDF in a folder, from this computer. "
          "The PDF is copied into the library and its details are found "
          "from its DOI/arXiv id or text; uncertain ones are flagged "
          "needs_checking.",
          _obj({"path": _s("Absolute path to a .pdf file or a folder."),
                "collection": _NEW_COLL}, ["path"]), _ADD),
    _tool("update_reference", "Edit reference",
          "Change fields of a reference; fields not passed are left as "
          "they are. The key itself cannot change.",
          _obj({"key": _KEY,
                "fields": {"type": "object", "description":
                           "Field -> new value. Text fields: " +
                           ", ".join(TEXT_FIELDS) + ", notes. "
                           "authors / editors: a list of 'Family, Given' "
                           "strings. keywords: a list or comma-separated "
                           "string. type: one of " + ", ".join(ENTRY_TYPES) +
                           ". needs_review: false once checked. date: "
                           "YYYY, YYYY-MM or YYYY-MM-DD."}},
               ["key", "fields"]), _EDIT),
    _tool("export_bibtex", "Export BibTeX",
          "BibLaTeX (default) or classic BibTeX text for the given keys, a "
          "collection, or (neither given) the whole library.",
          _obj({"keys": _KEYS, "collection": _COLL,
                "dialect": {"type": "string", "enum": list(bibtex.DIALECTS),
                            "description": "biblatex (default) or bibtex "
                                           "(classic, for natbib)."}}), _READ),
    _tool("list_citation_styles", "List citation styles",
          "Citation styles format_citation accepts: bundled ones by field "
          "and the journal styles the user added.", _obj({}), _READ),
    _tool("format_citation", "Format citation",
          "A formatted in-text citation and reference-list entries for the "
          "given keys, as plain text — for Word, e-mail or any non-LaTeX "
          "text. In LaTeX use \\cite{key} instead.",
          _obj({"keys": _KEYS,
                "style": _s("Style id from list_citation_styles "
                            "(default apa).")}, ["keys"]), _READ),
    _tool("read_paper", "Read paper",
          "The text of a reference's attached PDF with [page N] markers — "
          "to summarise it, answer questions or quote it. Ask for a page "
          "range for long papers.",
          _obj({"key": _KEY, "first_page": _i("First page (default 1)."),
                "last_page": _i("Last page (default: the end).")}, ["key"]),
          _READ),
    _tool("search_fulltext", "Search inside PDFs",
          "Find the passages in the library's PDFs (or one collection's) "
          "that best match a question or phrase; returns each passage with "
          "its citation key and page.",
          _obj({"query": _s("A question or phrase, e.g. 'band gap of "
                            "anatase TiO2'."),
                "collection": _COLL,
                "limit": _i("Most passages (default 8, max 30).")},
               ["query"]), _READ),
    _tool("find_duplicates", "Find duplicates",
          "Groups of references that look like the same work (same DOI, "
          "arXiv id, ISBN or PDF, same title and year, or a near-identical "
          "title by the same first author), with the reasons and a summary "
          "of each. Read-only: show the groups to the user and let them "
          "merge or delete in KherveRef — never cite both keys of a group.",
          _obj({"collection": _s("Only look within this collection. "
                                 "Optional.")}), _READ),
    _tool("view_page", "View PDF page",
          "One page of a reference's PDF as an IMAGE you can look at — for "
          "figures, tables, plots and equations that read_paper's text "
          "loses.",
          _obj({"key": _KEY, "page": _i("Page number, from 1 (default 1)."),
                "zoom": {"type": "number", "description":
                         "Magnification, 0.5-3 (default 1.5). Higher to read "
                         "small print in a figure."}}, ["key"]), _READ),
    _tool("show_in_window", "Show in KherveRef",
          "Select references in the open KherveRef window (All references "
          "view) and bring it to the front, so the user sees what you "
          "mean. Needs the window open on this library.",
          _obj({"keys": _KEYS}, ["keys"]), _WINDOW),
    _tool("open_pdf", "Open PDF for the user",
          "Open a reference's PDF for the user to read, in KhervePDF when "
          "installed, else the system viewer.",
          _obj({"key": _KEY}, ["key"]), _WINDOW),
    _tool("list_collections", "List collections",
          "Collections (with their parent) and how many references each "
          "holds.", _obj({}), _READ),
    _tool("add_to_collection", "Add to collection",
          "File references in a collection, created if missing. A reference "
          "can be in several collections.",
          _obj({"keys": _KEYS, "collection": _s("Collection name.")},
               ["keys", "collection"]), _EDIT),
    _tool("remove_from_collection", "Remove from collection",
          "Take references out of a collection. They stay in the library.",
          _obj({"keys": _KEYS, "collection": _s("Collection name.")},
               ["keys", "collection"]), _EDIT),
]


class ToolError(Exception):
    pass


def _summary(e: Entry) -> dict:
    return {"key": e.key, "authors": e.author_text(3), "year": e.year,
            "title": e.title, "published_in": e.container(), "doi": e.doi,
            "has_pdf": bool(e.files), "needs_checking": e.needs_review}


def _no_key(key: str, entries) -> ToolError:
    close = difflib.get_close_matches(key, list(entries), n=3, cutoff=0.6)
    hint = f" Did you mean {', '.join(close)}?" if close else (
        " Use search_references to find the right key.")
    return ToolError(f"No reference with key {key!r}.{hint}")


class Tools:
    def __init__(self, library_path: str | None = None):
        self._explicit = Path(library_path) if library_path else None

    # ----- library access -----

    def _window(self) -> dict | None:
        """The open window's state, or None when KherveRef is not open."""
        status = ipc.query({"what": "status"}, timeout=3)
        return status if status and "error" not in status else None

    def _lib(self) -> library.Library:
        if self._explicit:
            candidates = [self._explicit]
        else:
            # The library on screen is the one the user means.
            win = self._window()
            shown = [Path(win["library"])] if win and win.get("library") else []
            candidates = shown + state.recent_libraries()
        for p in candidates:
            if p and library.is_library(p):
                lib = library.open_library(p)
                if lib.migrated:
                    store.write_library_bib(lib, store.load_entries(lib).values())
                    git_backend.commit_all(lib.root, "Tidy the library folder")
                return lib
        raise ToolError("No KherveRef library found. Open or create one in "
                        "KherveRef first, or start the server with "
                        "--library PATH.")

    def _commit(self, lib, entries, message: str) -> None:
        store.write_library_bib(lib, entries.values())
        git_backend.commit_all(lib.root, message)

    def _collection_id(self, lib, name: str, create: bool = False) -> str:
        if not name:
            return ""
        cols = store.load_collections(lib)
        for c in cols:
            if c.name.lower() == name.lower() or c.id == name:
                return c.id
        if not create:
            raise ToolError(f"No collection named {name!r}")
        c = store.Collection(store.new_collection_id(), name)
        store.save_collections(lib, cols + [c])
        return c.id

    # ----- tools -----

    def get_status(self) -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        win = self._window()
        same = bool(win and win.get("library") and
                    Path(win["library"]).resolve() == lib.root.resolve())
        selected = [_summary(entries[k]) for k in (win or {}).get("selected", [])
                    if same and k in entries]
        return {
            "kherveref_version": __version__,
            "library": {"name": lib.name, "folder": str(lib.root),
                        "references": len(entries),
                        "with_pdf": sum(1 for e in entries.values() if e.files),
                        "needs_checking": sum(1 for e in entries.values()
                                              if e.needs_review),
                        "collections": len(store.load_collections(lib))},
            "window": ({"open": True, "showing_this_library": same,
                        "collection_shown": win.get("collection", "") if same else "",
                        "selected": selected} if win else
                       {"open": False, "note": "KherveRef is not open; the "
                        "tools still work, but show_in_window cannot."})}

    def _ask_window(self, request: dict) -> dict:
        lib = self._lib()
        answer = ipc.query({**request, "library": str(lib.root)})
        if answer is None:
            raise ToolError("KherveRef is not open. Ask the user to start it "
                            "(the other tools work without it).")
        if "error" in answer:
            raise ToolError(answer["error"])
        return answer

    def show_in_window(self, keys: list[str]) -> dict:
        entries = store.load_entries(self._lib())
        missing = [k for k in keys if k not in entries]
        if missing and len(missing) == len(keys):
            raise _no_key(missing[0], entries)
        answer = self._ask_window({"what": "show", "keys": keys})
        return {"shown": answer.get("shown", []), "missing": missing}

    def open_pdf(self, key: str) -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        e = entries.get(key)
        if e is None:
            raise _no_key(key, entries)
        pdf = self._pdf_path(lib, e)
        try:
            return {"key": key, **self._ask_window({"what": "open_pdf", "key": key})}
        except ToolError:
            pass                        # no window: hand it to the system
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(pdf)])
        elif os.name == "nt":
            os.startfile(str(pdf))      # noqa: S606 — the user's own file
        else:
            subprocess.Popen(["xdg-open", str(pdf)])
        return {"key": key, "opened_in": "system"}

    def _pdf_path(self, lib, e: Entry) -> Path:
        att = next((a for a in e.files if a.path.lower().endswith(".pdf")), None)
        path = store.file_path(lib, att) if att else None
        if path is None or not path.exists():
            raise ToolError(f"{e.key} has no PDF attached. add_pdf can attach "
                            "one, or the user can drop it on the reference.")
        return path

    def view_page(self, key: str, page: int = 1, zoom: float = 1.5) -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        e = entries.get(key)
        if e is None:
            raise _no_key(key, entries)
        path = self._pdf_path(lib, e)
        import pymupdf
        from .pdf_meta import PDF_LOCK
        zoom = min(3.0, max(0.5, float(zoom)))
        with PDF_LOCK, pymupdf.open(str(path)) as doc:
            n = doc.page_count
            if not 1 <= int(page) <= n:
                raise ToolError(f"{key} has pages 1-{n}")
            p = doc[int(page) - 1]
            # Keep the image under ~2000 px wide whatever the page size.
            zoom = min(zoom, 2000 / max(p.rect.width, 1))
            png = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
        return {"key": key, "title": e.title, "page": int(page), "pages": n,
                IMAGE_KEY: base64.b64encode(png).decode("ascii")}

    def list_libraries(self) -> dict:
        libs = [str(p) for p in state.recent_libraries() if library.is_library(p)]
        try:
            current = str(self._lib().root)
        except ToolError:
            current = None
        return {"libraries": libs, "in_use": current}

    def search_references(self, query: str = "", limit: int = 20,
                          collection: str = "", needs_checking: bool | None = None,
                          has_pdf: bool | None = None) -> dict:
        lib = self._lib()
        entries = list(store.load_entries(lib).values())
        if collection:
            cid = self._collection_id(lib, collection)
            ids = store.collection_and_descendants(store.load_collections(lib), cid)
            entries = [e for e in entries if ids.intersection(e.collections)]
        words = query.lower().split()

        def hay(e: Entry) -> str:
            return " ".join([e.key, e.title, e.container(), e.date, e.doi,
                             e.eprint, " ".join(e.keywords), e.notes,
                             " ".join(p.display() for p in e.authors + e.editors)]
                            ).lower()
        hits = [e for e in entries if all(w in hay(e) for w in words)
                and (needs_checking is None or e.needs_review == needs_checking)
                and (has_pdf is None or bool(e.files) == has_pdf)]
        hits.sort(key=lambda e: e.added, reverse=True)
        return {"total": len(hits),
                "references": [_summary(e) for e in hits[:max(1, int(limit))]]}

    def get_reference(self, key: str) -> dict:
        entries = store.load_entries(self._lib())
        e = entries.get(key)
        if e is None:
            raise _no_key(key, entries)
        d = e.to_dict()
        d["biblatex"] = bibtex.entry_to_bibtex(e, "biblatex")
        return d

    def _run_import(self, lib, fn, collection: str, message: str) -> dict:
        imp = importer.Importer(lib, collection=self._collection_id(
            lib, collection, create=True))
        fn(imp)
        if imp.summary.changed:
            self._commit(lib, imp.entries, f"{message}: {imp.summary.headline()}")
        return {"summary": imp.summary.headline(),
                "results": [{"status": o.status, "key": o.key,
                             "source": o.source, "message": o.message}
                            for o in imp.summary.outcomes]}

    def add_reference(self, identifier: str, collection: str = "") -> dict:
        lib = self._lib()
        return self._run_import(lib, lambda imp: imp.import_identifier(identifier),
                                collection, "Add via Claude")

    def add_bibtex(self, bibtex: str, collection: str = "") -> dict:
        lib = self._lib()
        return self._run_import(lib, lambda imp: imp.import_text(bibtex, "Text"),
                                collection, "Add BibTeX via Claude")

    def add_pdf(self, path: str, collection: str = "") -> dict:
        p = Path(path).expanduser()
        if not p.exists():
            raise ToolError(f"{p} does not exist")
        lib = self._lib()
        return self._run_import(lib, lambda imp: imp.run([p]), collection,
                                "Add PDF via Claude")

    def update_reference(self, key: str, fields: dict) -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        e = entries.get(key)
        if e is None:
            raise _no_key(key, entries)
        changed = []
        for name, value in (fields or {}).items():
            if name in ("authors", "editors"):
                text = "\n".join(value) if isinstance(value, list) else str(value)
                setattr(e, name, parse_names(text + "\n"))
            elif name == "keywords":
                e.keywords = (list(value) if isinstance(value, list) else
                              [k.strip() for k in str(value).split(",") if k.strip()])
            elif name == "needs_review":
                e.needs_review = bool(value)
            elif name in TEXT_FIELDS or name in ("type", "notes"):
                setattr(e, name, str(value))
            else:
                raise ToolError(f"Unknown field {name!r}. Fields: "
                                + ", ".join((*TEXT_FIELDS, "notes", "authors",
                                             "editors", "keywords", "type",
                                             "needs_review")))
            changed.append(name)
        store.save_entry(lib, e)
        self._commit(lib, entries, f"Edit {key} via Claude")
        return {"key": key, "changed": changed}

    def export_bibtex(self, keys: list[str] | None = None, collection: str = "",
                      dialect: str = "biblatex") -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        if keys:
            missing = [k for k in keys if k not in entries]
            chosen = [entries[k] for k in keys if k in entries]
        else:
            missing = []
            chosen = list(entries.values())
            if collection:
                ids = store.collection_and_descendants(
                    store.load_collections(lib), self._collection_id(lib, collection))
                chosen = [e for e in chosen if ids.intersection(e.collections)]
        return {"count": len(chosen), "missing": missing,
                "bibtex": bibtex.to_bibtex(chosen, dialect)}

    def read_paper(self, key: str, first_page: int = 1,
                   last_page: int | None = None) -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        e = entries.get(key)
        if e is None:
            raise _no_key(key, entries)
        pages = fulltext.pages(lib, e)
        if not pages:
            raise ToolError(f"{key} has no PDF with readable text")
        first = max(1, int(first_page))
        last = min(len(pages), int(last_page or len(pages)))
        text = "".join(f"[page {i}]\n{pages[i - 1]}\n"
                       for i in range(first, last + 1))
        return {"key": key, "title": e.title, "pages": len(pages),
                "first_page": first, "last_page": last, "text": text[:200_000]}

    def search_fulltext(self, query: str, collection: str = "",
                        limit: int = 8) -> dict:
        lib = self._lib()
        entries = list(store.load_entries(lib).values())
        if collection:
            ids = store.collection_and_descendants(
                store.load_collections(lib), self._collection_id(lib, collection))
            entries = [e for e in entries if ids.intersection(e.collections)]
        hits = fulltext.search(query, fulltext.passages(lib, entries),
                               top=max(1, min(int(limit), 30)))
        titles = {e.key: e.title for e in entries}
        return {"passages": [{"key": p.key, "page": p.page, "title": titles[p.key],
                              "text": p.text} for p in hits]}

    def find_duplicates(self, collection: str = "") -> dict:
        lib = self._lib()
        entries = store.load_entries(lib)
        chosen = list(entries.values())
        if collection:
            ids = store.collection_and_descendants(
                store.load_collections(lib), self._collection_id(lib, collection))
            chosen = [e for e in chosen if ids.intersection(e.collections)]
        groups = store.find_duplicates(chosen)
        return {"groups": len(groups), "duplicates": [
            {"reasons": g["reasons"],
             "references": [_summary(entries[k]) for k in g["keys"]]}
            for g in groups]}

    def list_collections(self) -> dict:
        lib = self._lib()
        cols = store.load_collections(lib)
        entries = store.load_entries(lib).values()
        names = {c.id: c.name for c in cols}
        return {"collections": [
            {"name": c.name, "parent": names.get(c.parent, ""),
             "references": sum(1 for e in entries if c.id in e.collections)}
            for c in cols]}

    def add_to_collection(self, keys: list[str], collection: str) -> dict:
        lib = self._lib()
        cid = self._collection_id(lib, collection, create=True)
        entries = store.load_entries(lib)
        added = []
        for k in keys:
            e = entries.get(k)
            if e is not None and cid not in e.collections:
                e.collections.append(cid)
                store.save_entry(lib, e)
                added.append(k)
        missing = [k for k in keys if k not in entries]
        if added:
            self._commit(lib, entries, f"Add {len(added)} to {collection} via Claude")
        return {"added": added, "missing": missing}

    def remove_from_collection(self, keys: list[str], collection: str) -> dict:
        lib = self._lib()
        cid = self._collection_id(lib, collection)
        entries = store.load_entries(lib)
        removed = []
        for k in keys:
            e = entries.get(k)
            if e is not None and cid in e.collections:
                e.collections.remove(cid)
                store.save_entry(lib, e)
                removed.append(k)
        if removed:
            self._commit(lib, entries,
                         f"Remove {len(removed)} from {collection} via Claude")
        return {"removed": removed,
                "not_in_collection": [k for k in keys if k not in removed]}

    def list_citation_styles(self) -> dict:
        return {"default": cite.DEFAULT_STYLE,
                "styles": [{"id": sid, "name": label, "group": group}
                           for group, sid, label in cite.grouped_styles()]}

    def format_citation(self, keys: list[str], style: str = "") -> dict:
        style = style or cite.DEFAULT_STYLE
        if style not in cite.all_styles():
            raise ToolError(f"No citation style {style!r}; see "
                            "list_citation_styles.")
        entries = store.load_entries(self._lib())
        missing = [k for k in keys if k not in entries]
        if missing and len(missing) == len(keys):
            raise _no_key(missing[0], entries)
        chosen = [entries[k] for k in keys if k in entries]
        return {"style": style, "missing": missing,
                "citation": cite.format_citation(chosen, style),
                "references": [cite.to_text(r)
                               for r in cite.format_reference(chosen, style)]}

    def call(self, name: str, args: dict) -> Any:
        if name not in {t["name"] for t in TOOLS}:
            raise ToolError(f"Unknown tool {name!r}. Tools: "
                            + ", ".join(t["name"] for t in TOOLS))
        tool = next(t for t in TOOLS if t["name"] == name)
        allowed = tool["inputSchema"]["properties"]
        unknown = [a for a in (args or {}) if a not in allowed]
        if unknown:
            raise ToolError(f"{name} has no argument {', '.join(unknown)}; "
                            f"it takes: {', '.join(allowed) or 'nothing'}")
        missing = [a for a in tool["inputSchema"]["required"] if a not in args]
        if missing:
            raise ToolError(f"{name} needs {', '.join(missing)}")
        return getattr(self, name)(**(args or {}))
