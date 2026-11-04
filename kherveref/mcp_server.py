"""MCP (Model Context Protocol) stdio server for KherveRef.

It speaks JSON-RPC 2.0 over stdin/stdout — the transport every MCP host
(Claude Desktop, Claude Code, Cursor, …) knows how to launch. The tools
themselves live in mcp_tools.py.

A library is plain files, so unlike KherveCAD's server this one does not
need the window: it works on the library folder and commits each change,
which an open window picks up (it watches the folder). When the window
IS open the tools also reach it, to see what the user has selected and
to show references there. No Qt is imported, so it starts in
milliseconds. Launched by the MCP host as:

    python -m kherveref.mcp_server [--library PATH]   # from a checkout
    KherveRef --mcp-server [--library PATH]           # installed app

Without --library it uses the library open in KherveRef, else the one
opened most recently. AI ▸ Connect to Claude… writes the host's config.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .mcp_tools import IMAGE_KEY, TOOLS, ToolError, Tools

#: Spec revisions we know how to speak. We echo the client's choice
#: when it is one of these, otherwise we answer with our newest.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "kherveref"

#: Sent to the client on initialize. The host writes its own system
#: prompt, so everything a model must know before touching someone's
#: library has to travel with the connection.
INSTRUCTIONS = """\
These tools work on the user's KherveRef library — their reference \
manager: the papers, books, theses and web pages they cite, each with a \
citation KEY (e.g. smith2020surface) that their documents cite. Every \
change is committed to the library's Git history and appears at once in \
the KherveRef window if it is open; it can be undone there (Edit ▸ Undo, \
Library ▸ History).

START WITH get_status. It says which library you are working on, how \
big it is, whether the KherveRef window is open, and which references \
the user has SELECTED there. When the user says "this paper", "these \
references" or "the one I'm looking at", they mean the selection — use \
those keys rather than guessing.

SEARCH BEFORE YOU CITE OR ADD. Call search_references with a few words \
(author, title words, year, DOI) before citing anything or adding a \
paper, and try a synonym if nothing comes back. Cite ONLY keys these \
tools returned — never invent, guess or "fix" a key, even if it looks \
like it follows a pattern. In LaTeX / KherveTeX cite as \\cite{key}; for \
Word, e-mail or plain text use format_citation in the user's style.

ADDING REFERENCES. Prefer add_reference with a DOI, arXiv id or ISBN: \
the details are fetched from Crossref / arXiv / the ISBN registry and \
are reliable. Use add_bibtex for pasted BibTeX, RIS, EndNote XML, \
PubMed or CSL-JSON, and add_pdf for a PDF on this computer. Each result \
lists a status per item: "added" with the new key, or "duplicate" with \
the key already in the library — use that key, do not add it again. \
Pass `collection` to file new references where the user is working \
(e.g. their chapter). Never type a reference's details in by hand from \
memory when an identifier exists: memory gets years, pages and author \
spellings wrong.

READING PAPERS. search_fulltext finds the passages across the library's \
PDFs that answer a question ("which paper measured the band gap?"); \
read_paper returns one paper's text with [page N] markers — ask for a \
page range for long papers. view_page shows you a page as an IMAGE: use \
it for figures, tables, equations and plots that text extraction \
mangles. When you quote or summarise, give the key and page.

EDITING. get_reference shows every field; update_reference changes only \
the fields you pass. References made from a PDF may be marked \
needs_checking — find them with search_references(needs_checking=true), \
correct them (add_reference with their DOI is the reliable fix) and \
clear the flag. Citation keys NEVER change once created, and there is \
no delete tool: deleting and merging are the user's decisions, made in \
the window.

TIDYING. find_duplicates lists references that look like the same work, \
with the reason. Show the groups to the user; they merge them in \
KherveRef (Library ▸ Find duplicates…). Never cite two keys of one group.

THE WINDOW. show_in_window selects references in the open KherveRef \
window so the user can see what you mean — use it after adding or \
finding references they will want to look at. open_pdf opens a \
reference's PDF in KhervePDF (or the system viewer) for the user.

COLLECTIONS group references (a chapter, a project, a reading list): \
list_collections, add_to_collection, remove_from_collection. A \
reference can be in several.

OUTPUT. export_bibtex gives BibLaTeX (default) or classic BibTeX for \
keys, a collection or the whole library. The library's own library.bib \
is regenerated after every change and is what KherveTeX compiles with. \
format_citation gives a formatted citation and reference list in any \
style from list_citation_styles (APA, Nature, IEEE, … and the journal \
styles the user added).

Finish by telling the user what you added or changed, with the keys."""


def tool_content(result: Any) -> list[dict]:
    """MCP content blocks for a tool result. A result carrying a PNG
    becomes an image block (plus the rest as text), so the model can
    actually look at the page."""
    if isinstance(result, dict) and result.get(IMAGE_KEY):
        rest = {k: v for k, v in result.items() if k != IMAGE_KEY}
        return [{"type": "image", "data": result[IMAGE_KEY],
                 "mimeType": "image/png"},
                {"type": "text", "text": json.dumps(rest, indent=2,
                                                    ensure_ascii=False)}]
    return [{"type": "text",
             "text": json.dumps(result, indent=2, ensure_ascii=False,
                                default=str)}]


def _log(msg: str) -> None:
    """Diagnostics go to stderr — stdout carries the protocol."""
    sys.stderr.write(f"[kherveref-mcp] {msg}\n")
    sys.stderr.flush()


class McpServer:
    """Minimal, dependency-free MCP server over stdio."""

    def __init__(self, tools: Tools):
        self.tools = tools

    def handle(self, msg: dict) -> dict | None:
        """Handle one JSON-RPC message; None means 'no reply'."""
        method, msg_id = msg.get("method"), msg.get("id")
        if method is None:                    # a response — ignore
            return None
        try:
            if method == "initialize":
                result = self._initialize(msg.get("params") or {})
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
                # Declared empty rather than unsupported so hosts that
                # probe every capability do not surface an error.
                key = ("resourceTemplates" if method.endswith("templates/list")
                       else "resources")
                result = {key: []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                if msg_id is None:
                    return None
                return _error(msg_id, -32601, f"Unknown method: {method}")
        except Exception as exc:              # never take the loop down
            _log(f"{method} failed: {exc}")
            if msg_id is None:
                return None
            return _error(msg_id, -32603, f"Internal error: {exc}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS
            else SUPPORTED_PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {}, "prompts": {}},
            "serverInfo": {"name": SERVER_NAME, "title": "KherveRef",
                           "version": __version__},
            "instructions": INSTRUCTIONS}

    def _call(self, name, args) -> dict:
        try:
            result, is_error = self.tools.call(name, args or {}), False
        except (ToolError, TypeError) as exc:
            result, is_error = {"error": str(exc)}, True
        return {"content": tool_content(result), "isError": is_error}


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
            _log("ignored a line that is not JSON")
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
