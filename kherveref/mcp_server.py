"""MCP (Model Context Protocol) stdio server: lets Claude search, add,
edit and export references.

A library is plain files, so unlike KherveTeX's server this one needs
no running window and no Qt; it works on the library folder directly
and commits each change, which an open KherveRef window then picks up
(it watches the folder). Launched by the MCP host as:

    python -m kherveref.mcp_server [--library PATH]   # from a checkout
    KherveRef --mcp-server [--library PATH]           # installed app

Without --library it uses the library most recently opened in KherveRef.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, bibtex, git_backend, importer, library, state, store
from .model import TEXT_FIELDS, Entry, parse_names

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "kherveref"

INSTRUCTIONS = (
    "KherveRef is the user's reference manager. Use search_references "
    "before citing, cite with the returned keys (\\cite{key}), and add "
    "missing papers with add_reference (DOI, arXiv id or ISBN) or add_pdf. "
    "Citation keys never change once created. Every change is committed "
    "to the library's Git history.")


def _obj(props: dict, required: list[str] = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


_STR = {"type": "string"}
_KEYS = {"type": "array", "items": {"type": "string"}}

TOOLS: list[dict] = [
    {"name": "list_libraries",
     "description": "Libraries this user has opened in KherveRef, and which "
                    "one the other tools use.",
     "inputSchema": _obj({})},
    {"name": "search_references",
     "description": "Find references by words in their key, authors, title, "
                    "journal, year, DOI, keywords or notes. Empty query lists "
                    "the most recently added.",
     "inputSchema": _obj({"query": _STR, "limit": {"type": "integer"},
                          "collection": {"type": "string",
                                         "description": "collection name"}})},
    {"name": "get_reference",
     "description": "All details of one reference, with its BibLaTeX.",
     "inputSchema": _obj({"key": _STR}, ["key"])},
    {"name": "add_reference",
     "description": "Add a reference by DOI, arXiv id or ISBN (details are "
                    "looked up online). Returns its citation key.",
     "inputSchema": _obj({"identifier": _STR, "collection": _STR},
                         ["identifier"])},
    {"name": "add_bibtex",
     "description": "Add references from BibTeX / BibLaTeX source text.",
     "inputSchema": _obj({"bibtex": _STR, "collection": _STR}, ["bibtex"])},
    {"name": "add_pdf",
     "description": "Add a PDF file (or every PDF in a folder) from this "
                    "computer; its details are found from the PDF.",
     "inputSchema": _obj({"path": _STR, "collection": _STR}, ["path"])},
    {"name": "update_reference",
     "description": "Change fields of a reference. `fields` may hold title, "
                    "authors (\"Family, Given\" per line), date, journal, "
                    "volume, number, pages, doi, url, notes, keywords "
                    "(comma separated), type, needs_review (bool)...",
     "inputSchema": _obj({"key": _STR, "fields": {"type": "object"}},
                         ["key", "fields"])},
    {"name": "export_bibtex",
     "description": "BibLaTeX (default) or classic BibTeX text for the given "
                    "keys, a collection, or the whole library.",
     "inputSchema": _obj({"keys": _KEYS, "collection": _STR,
                          "dialect": {"type": "string",
                                      "enum": list(bibtex.DIALECTS)}})},
    {"name": "list_collections",
     "description": "Collections and how many references each holds.",
     "inputSchema": _obj({})},
    {"name": "add_to_collection",
     "description": "File references in a collection (created if missing).",
     "inputSchema": _obj({"keys": _KEYS, "collection": _STR},
                         ["keys", "collection"])},
]


class ToolError(Exception):
    pass


def _summary(e: Entry) -> dict:
    return {"key": e.key, "authors": e.author_text(3), "year": e.year,
            "title": e.title, "published_in": e.container(), "doi": e.doi,
            "has_pdf": bool(e.files), "needs_checking": e.needs_review}


class Tools:
    def __init__(self, library_path: str | None = None):
        self._explicit = Path(library_path) if library_path else None

    # ----- library access -----

    def _lib(self) -> library.Library:
        candidates = [self._explicit] if self._explicit else state.recent_libraries()
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

    def list_libraries(self) -> dict:
        libs = [str(p) for p in state.recent_libraries() if library.is_library(p)]
        try:
            current = str(self._lib().root)
        except ToolError:
            current = None
        return {"libraries": libs, "in_use": current}

    def search_references(self, query: str = "", limit: int = 20,
                          collection: str = "") -> dict:
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
        hits = [e for e in entries if all(w in hay(e) for w in words)]
        hits.sort(key=lambda e: e.added, reverse=True)
        return {"total": len(hits),
                "references": [_summary(e) for e in hits[:max(1, int(limit))]]}

    def get_reference(self, key: str) -> dict:
        e = store.load_entries(self._lib()).get(key)
        if e is None:
            raise ToolError(f"No reference with key {key!r}")
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
        return self._run_import(lib, lambda imp: imp.import_bib_text(bibtex, "BibTeX"),
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
            raise ToolError(f"No reference with key {key!r}")
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
                raise ToolError(f"Unknown field {name!r}")
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

    def call(self, name: str, args: dict) -> Any:
        if name not in {t["name"] for t in TOOLS}:
            raise ToolError(f"Unknown tool {name!r}")
        return getattr(self, name)(**(args or {}))


class McpServer:
    def __init__(self, tools: Tools):
        self.tools = tools

    def handle(self, msg: dict) -> dict | None:
        method, msg_id = msg.get("method"), msg.get("id")
        if method is None:
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                result = {
                    "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS
                    else SUPPORTED_PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False},
                                     "resources": {}, "prompts": {}},
                    "serverInfo": {"name": SERVER_NAME, "title": "KherveRef",
                                   "version": __version__},
                    "instructions": INSTRUCTIONS}
            elif method.startswith("notifications/") or method == "initialized":
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = self._call(params.get("name"), params.get("arguments"))
            elif method in ("resources/list", "resources/templates/list"):
                key = ("resourceTemplates" if method.endswith("templates/list")
                       else "resources")
                result = {key: []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                if msg_id is None:
                    return None
                return _error(msg_id, -32601, f"Unknown method: {method}")
        except Exception as exc:
            if msg_id is None:
                return None
            return _error(msg_id, -32603, f"Internal error: {exc}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _call(self, name, args) -> dict:
        try:
            result, is_error = self.tools.call(name, args or {}), False
        except (ToolError, TypeError) as exc:
            result, is_error = {"error": str(exc)}, True
        return {"content": [{"type": "text",
                             "text": json.dumps(result, indent=2,
                                                ensure_ascii=False)}],
                "isError": is_error}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def serve(library_path: str | None = None, stdin=None, stdout=None) -> int:
    server = McpServer(Tools(library_path))
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        batch = msg if isinstance(msg, list) else [msg]
        for reply in (server.handle(m) for m in batch):
            if reply is not None:
                stdout.write((json.dumps(reply) + "\n").encode("utf-8"))
        stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = None
    if "--library" in args:
        i = args.index("--library")
        path = args[i + 1] if i + 1 < len(args) else None
    return serve(path)


if __name__ == "__main__":
    sys.exit(main())

