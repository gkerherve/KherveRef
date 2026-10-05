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
- **Paste or drop** DOIs, arXiv ids, ISBNs or BibTeX text, or import
  `.bib` and CSL-JSON files (PDFs named in JabRef/Zotero `file` fields
  come along).
- Drag references onto a collection to file them; drag them into a text
  editor to get `\cite{key}`.

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

A library is a plain folder, versioned with Git and synced through a Git
remote (GitHub or any other host):

```
MyLibrary/
  library.json        name, format, BibTeX dialect
  library.bib         generated after every change (classic BibTeX, for KherveTeX)
  collections.json    collections
  entries/<key>.json  one reference per file
  files/<key>.pdf     attached PDFs
  .kherveref/         local search index (not committed)
```

One file per reference keeps diffs readable and lets two machines edit
different references without merge conflicts.

## Roadmap

1. Application shell — themed window, new/open library, Git history,
   remote, pull/push. *(v0.1)*
2. Core model: entries, BibLaTeX / BibTeX / CSL-JSON import and export,
   citation keys. *(v0.2)*
3. Library view: collections, entry editor, drag-and-drop. *(v0.2)*
4. Metadata lookup from DOI, arXiv, ISBN and dropped PDFs. *(v0.2)*
5. KherveTeX: linked `.bib` kept up to date, citation picker, missing-key
   checks, `.bib` bundled inside `.ktexz` files.
6. KhervePDF: attachments open in KhervePDF; KhervePDF's *Add to KherveRef*
   and *Show in KherveRef* reach the running window; PDF highlights and
   comments can be copied into a reference's notes. *(v0.3)*
7. MCP server for Claude, installers. Zotero import.

## Run

```
pip install -r requirements.txt
python KherveRef.py
```

Python 3.12+, PySide6, PyMuPDF, pygit2, qtawesome.

## License

GPL-3.0.
