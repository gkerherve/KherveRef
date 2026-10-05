"""`KherveRef --smoke-test`: prove a frozen build works end to end.

Runs offscreen in a throwaway library: builds the window, imports a
generated PDF (offline), edits and exports BibTeX, then prints
"SMOKE OK". Catches what a freeze can drop — PyMuPDF, qtawesome's font
files, pygit2, Qt plugins — without a display.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pymupdf
    from PySide6.QtWidgets import QApplication

    from . import bibtex, git_backend, importer, library, store
    from .mainwindow import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    tmp = Path(tempfile.mkdtemp(prefix="kherveref-smoke-"))
    lib = library.create_library(tmp / "lib", "Smoke")
    pdf = tmp / "paper.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "A Smoke Test Of The Frozen Build", fontsize=20)
    page.insert_text((72, 130), "arXiv:2101.00001 and doi:10.1234/smoke.1", fontsize=10)
    doc.save(str(pdf))
    doc.close()

    summary = importer.Importer(lib, online=False).run([pdf])
    entries = store.load_entries(lib)
    checks = {
        "import": summary.count(importer.REVIEW) == 1 and len(entries) == 1,
        "attachment": all((lib.root / a.path).exists()
                          for e in entries.values() for a in e.files),
        "bibtex": "@article" in bibtex.to_bibtex(entries.values())
                  or "@misc" in bibtex.to_bibtex(entries.values()),
        "library.bib": (lib.root / store.LIBRARY_BIB).exists(),
        "git": git_backend.commit_all(lib.root, "smoke") is not None,
    }
    # The throwaway library must not become the user's "last library".
    MainWindow._remember = lambda self, root: None
    win = MainWindow()
    win.open_library(lib.root)
    app.processEvents()
    checks["window"] = win._proxy.rowCount() == 1
    checks["icons"] = not win.act_add_pdfs.icon().isNull()
    win.close()
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if all(checks.values()):
        print("SMOKE OK")
        return 0
    return 1
