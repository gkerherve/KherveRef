# KherveRef

A reference manager that ties [KherveTeX](https://github.com/gkerherve/kherveTeX)
and [KhervePDF](https://github.com/gkerherve/KhervePDF) together, with
BibLaTeX export.

Same look and themes as the rest of the Kherve suite.

## Libraries

A library is a plain folder, versioned with Git and synced through a Git
remote (GitHub or any other host):

```
MyLibrary/
  library.json        name, format, BibTeX dialect
  collections.json    collections
  entries/<key>.json  one reference per file
  files/<key>.pdf     attached PDFs
  .kherveref/         local search index (not committed)
```

One file per reference keeps diffs readable and lets two machines edit
different references without merge conflicts.

## Roadmap

1. **Application shell** — themed window, new/open library, Git history,
   remote, pull/push. *(v0.1)*
2. Core model: entries, BibLaTeX import/export, citation keys, search index.
3. Library view: collections, entry editor, drag-and-drop PDFs / `.bib` / DOIs.
4. Metadata lookup from DOI (Crossref), arXiv, ISBN, and from dropped PDFs.
5. KherveTeX: linked `.bib` kept up to date, citation picker, missing-key
   checks, `.bib` bundled inside `.ktexz` files.
6. KhervePDF: open attachments, "Add to KherveRef", annotations as notes.
7. MCP server for Claude, installers. Zotero import.

## Run

```
pip install -r requirements.txt
python KherveRef.py
```

Python 3.12+, PySide6, PyMuPDF, pygit2, qtawesome.

## License

GPL-3.0.
