"""Background work (imports, lookups) and the result dialog.

Workers get their own copy of the entries; the main window reloads the
library from disk when they finish, so the GUI never reads a dict a
worker is writing.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import importer, store
from .fetch import LookupError_, NetworkError
from .importer import Importer, Outcome, Summary
from .library import Library


class ImportJob(QThread):
    progress = Signal(int, int, str)
    finished_with = Signal(object)      # Summary

    def __init__(self, lib: Library, paths: list[Path] = (),
                 identifiers: list[str] = (), bib_text: str = "",
                 collection: str = "", online: bool = True,
                 zotero_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.lib, self.paths, self.identifiers = lib, list(paths), list(identifiers)
        self.online = online
        self.zotero_dir = zotero_dir
        self.bib_text, self.collection = bib_text, collection
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        imp = Importer(self.lib, online=self.online, collection=self.collection)
        try:
            total = len(self.identifiers)
            for i, ident in enumerate(self.identifiers):
                if self._cancel:
                    break
                self.progress.emit(i, total, ident)
                imp.import_identifier(ident)
            if self.zotero_dir is not None:
                imp.import_zotero(self.zotero_dir, progress=self.progress.emit,
                                  cancelled=lambda: self._cancel)
            if self.bib_text:
                imp.import_text(self.bib_text, "Pasted text")
            if self.paths:
                imp.run(self.paths, progress=self.progress.emit,
                        cancelled=lambda: self._cancel)
            elif imp.summary.changed:
                store.write_library_bib(self.lib, imp.entries.values())
        except Exception as e:      # never die silently in a thread
            imp.summary.outcomes.append(Outcome("import", importer.FAILED,
                                                message=str(e)))
        self.finished_with.emit(imp.summary)


class LookupJob(QThread):
    """Re-runs the online lookup for references that need checking."""
    progress = Signal(int, int, str)
    finished_with = Signal(object)

    def __init__(self, lib: Library, keys: list[str], parent=None):
        super().__init__(parent)
        self.lib, self.keys = lib, keys
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        summary = Summary()
        entries = store.load_entries(self.lib)
        for i, key in enumerate(self.keys):
            if self._cancel:
                break
            e = entries.get(key)
            if e is None:
                continue
            self.progress.emit(i, len(self.keys), key)
            try:
                msg = importer.refresh_from_identifiers(e)
                store.save_entry(self.lib, e)
                summary.outcomes.append(Outcome(key, importer.ADDED, key, msg))
            except (LookupError_, NetworkError) as err:
                summary.outcomes.append(Outcome(key, importer.FAILED, key, str(err)))
        self.progress.emit(len(self.keys), len(self.keys), "")
        if summary.changed:
            store.write_library_bib(self.lib, entries.values())
        self.finished_with.emit(summary)


class SummaryDialog(QDialog):
    def __init__(self, title: str, summary: Summary, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 420)
        head = QLabel(f"<b>{summary.headline()}</b>")
        tree = QTreeWidget()
        tree.setHeaderLabels(["Result", "Key", "Source", "Details"])
        tree.setRootIsDecorated(False)
        order = {importer.FAILED: 0, importer.REVIEW: 1, importer.ADDED: 2,
                 importer.ATTACHED: 3, importer.DUPLICATE: 4}
        for o in sorted(summary.outcomes, key=lambda o: order.get(o.status, 9)):
            src = o.source
            if len(src) > 60:
                src = "…" + src[-59:]
            it = QTreeWidgetItem([o.status, o.key, src, o.message])
            it.setToolTip(2, o.source)
            it.setToolTip(3, o.message)
            tree.addTopLevelItem(it)
        tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        for c in range(3):
            tree.resizeColumnToContents(c)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        if summary.count(importer.REVIEW):
            note = QLabel("References marked “needs checking” were created from "
                          "what the PDF itself says. Find them under Needs checking "
                          "in the left pane.")
            note.setWordWrap(True)
            lay.addWidget(note)
        lay.addWidget(tree)
        lay.addWidget(buttons)
