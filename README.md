# KherveRef

A reference manager that ties [KherveTeX](https://github.com/gkerherve/kherveTeX)
and [KhervePDF](https://github.com/gkerherve/KhervePDF) together, with
BibLaTeX export.

Same look and themes as the rest of the Kherve suite.

## Adding references

- **Drop PDFs** (or a whole folder) onto the window, or use *Add PDFs…* /
  *Import folder…*. KherveRef reads each PDF's DOI, arXiv id or ISBN and
  fetches the full details online (doi.org / Crossref, arXiv,
  OpenLibrary). Without an identifier it searches Crossref by the title
  and only accepts a near-exact match.
- When nothing is found (or you are offline) the reference is still
  created from what the PDF says and listed under **Needs checking**;
  *Look up details* tries again later.
- The same PDF, DOI, arXiv id or ISBN is never added twice; a PDF for a
  reference you already have is attached to it.
- **Paste or drop** DOIs, arXiv ids, ISBNs or exported reference text.
- **Import from other reference managers** (*File ▸ Import file…*, or
  drop the file):

  | Format | From | PDFs come along |
  |---|---|---|
  | Zotero library (*Import from Zotero…*) | Zotero | yes, plus collections, tags, notes |
  | BibTeX / BibLaTeX `.bib` | JabRef, BibDesk, Mendeley, Citavi, Paperpile… | when a `file` field points at them |
  | RIS `.ris` | EndNote, Mendeley, Papers, Web of Science, Scopus, journals | when `L1` / `file://` links point at them |
  | EndNote XML `.xml` | EndNote | yes, from the library's `.Data/PDF` folder next to the export |
  | PubMed `.nbib` / MEDLINE | PubMed "Save → PubMed format" | — |
  | CSL-JSON `.json` | Zotero, Paperpile, others | — |
- Drag references onto a collection to file them; drag them into a text
  editor to get `\cite{key}`.

## Seeing your papers

*View ▸ Covers* (Ctrl+2) shows the library as the front pages of its PDFs,
with title, authors and year; *View ▸ List* (Ctrl+1) is the table. The
selected reference's front page sits above its details (click it to open
the PDF), and hovering a title in the list previews it. Thumbnails are
rendered once in the background and cached in `.kherveref/cache/`.

## Importing from Zotero

*File ▸ Import from Zotero…* copies every reference of a Zotero library
with its PDFs, collections (nested), tags and notes. Zotero's database is
read from a copy, so Zotero can stay open and is never changed. Importing
again only adds what is new.

## Claude (MCP)

*Help ▸ Use with Claude (MCP)…* shows the command to register KherveRef's
MCP server. Claude can then search, add (DOI / arXiv / ISBN / PDF / BibTeX),
edit, file and export references. The server works on the library folder
directly; an open KherveRef window picks up its changes.

```
claude mcp add kherveref -- python -m kherveref.mcp_server
```

## Working with KhervePDF

PDFs open in KhervePDF when it is installed (or checked out next to this
repo); *Reference ▸ Locate KhervePDF…* points at another copy. KhervePDF's
*Tools ▸ Add to KherveRef* and *Show in KherveRef* run

```
KherveRef --add paper.pdf
KherveRef --reveal paper.pdf
```

which hand the request to the KherveRef window already open.
*Reference ▸ Copy PDF annotations to notes* turns highlights and comments
into notes on the reference.

## Libraries

A library is a folder, versioned with Git and synced through a Git
remote (GitHub or any other host). Inside you see only:

```
My References/
  My References.kref    the library: double-click to open it in KherveRef
  PDFs/                 your papers, named by citation key
  library.bib           every reference as BibTeX, for LaTeX / KherveTeX
```

KherveRef keeps its own data in a hidden `.kherveref/` folder: one small
file per reference (so two computers editing different references merge
cleanly in Git) and the collections. Libraries made by KherveRef 0.1–0.5
are tidied into this layout the first time they are opened.

## Roadmap

1. Application shell — themed window, new/open library, Git history,
   remote, pull/push. *(v0.1)*
2. Core model: entries, BibLaTeX / BibTeX / CSL-JSON import and export,
   citation keys. *(v0.2)*
3. Library view: collections, entry editor, drag-and-drop. *(v0.2)*
4. Metadata lookup from DOI, arXiv, ISBN and dropped PDFs. *(v0.2)*
5. KherveTeX (v0.217): *Insert ▸ Citation* picks from your libraries,
   cited entries travel inside the `.ktex`, *Check citations* lists
   unknown keys.
6. KhervePDF: attachments open in KhervePDF; KhervePDF's *Add to KherveRef*
   and *Show in KherveRef* reach the running window; PDF highlights and
   comments can be copied into a reference's notes. *(v0.3)*
7. MCP server for Claude and Zotero import *(v0.4)*; Windows installer and
   macOS DMGs built and smoke-tested by CI on a `v*` tag.

## Run

```
pip install -r requirements.txt
python KherveRef.py
```

Python 3.12+, PySide6, PyMuPDF, pygit2, qtawesome.

## Building installers

```
pip install -r requirements.txt pyinstaller pillow
python packaging/generate_icon.py
pyinstaller KherveRef.spec --noconfirm
python packaging/smoke_test.py dist/KherveRef.app/Contents/MacOS/KherveRef   # or dist/KherveRef/KherveRef.exe
```

Windows: then `ISCC.exe /DMyAppVersion=<version> KherveRef_setup.iss`.
CI does all of this when a `v<version>` tag is pushed and uploads the
installers as workflow artifacts.

## License

GPL-3.0.
