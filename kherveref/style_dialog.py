"""Edit ▸ Citation style ▸ Find a journal style…: search the CSL
repository's journal styles and add one (see journal_styles)."""
from __future__ import annotations

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout)

from . import cite, journal_styles
from .icons import icon

NAME_ROLE = Qt.UserRole


class _IndexJob(QThread):
    done = Signal(object, str)          # index or None, error text

    def run(self):
        try:
            self.done.emit(journal_styles.load_index(), "")
        except journal_styles.StyleError as e:
            self.done.emit(None, str(e))


class JournalStyleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find a journal style")
        self.resize(620, 560)
        self.chosen = ""            # style id added and picked, for the caller
        self._index: list[dict] | None = None

        intro = QLabel(
            "Type the name of the journal you are writing for (or its publisher). "
            "These are the official styles Zotero and Mendeley use — about 10,000 "
            "journals. <b>Add and use</b> downloads it once; it then works offline, "
            "in Copy formatted citation, drag-and-drop and the Word panel.")
        intro.setWordWrap(True)
        self._search = QLineEdit(placeholderText="e.g. Applied Surface Science, "
                                                 "Journal of Chemical Physics, Elsevier…")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icon("find"), QLineEdit.LeadingPosition)
        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self._run_search)
        self._search.textChanged.connect(lambda _t: self._timer.start())
        self._results = QListWidget()
        self._results.itemDoubleClicked.connect(lambda _i: self._add())
        self._results.currentItemChanged.connect(lambda *_: self._update_buttons())
        self._status = QLabel("Loading the list of styles…")
        self._status.setWordWrap(True)

        self._added = QListWidget()
        self._added.setMaximumHeight(110)
        self._added.currentItemChanged.connect(lambda *_: self._update_buttons())
        self._remove = QPushButton(icon("delete"), "Remove")
        self._remove.setToolTip("Remove the selected journal style from this computer")
        self._remove.clicked.connect(self._remove_selected)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._add_btn = buttons.addButton("Add and use", QDialogButtonBox.AcceptRole)
        self._add_btn.setToolTip("Download the selected journal's style and make it "
                                 "the citation style")
        self._add_btn.setDefault(True)
        self._add_btn.clicked.connect(self._add)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self._search)
        lay.addWidget(self._results, 1)
        lay.addWidget(self._status)
        lay.addWidget(QLabel("<b>Added on this computer</b>"))
        row = QHBoxLayout()
        row.addWidget(self._added, 1)
        row.addWidget(self._remove, 0, Qt.AlignTop)
        lay.addLayout(row)
        lay.addWidget(buttons)

        self._fill_added()
        self._update_buttons()
        self._job = _IndexJob(self)
        self._job.done.connect(self._index_loaded)
        self._job.start()
        self._search.setFocus()

    def _index_loaded(self, index, error: str) -> None:
        if index is None:
            self._status.setText(error)
            return
        self._index = index
        self._status.setText(f"{len(index):,} styles available.")
        self._run_search()

    def _run_search(self) -> None:
        self._results.clear()
        if self._index is None:
            return
        q = self._search.text().strip()
        hits = journal_styles.search(q, self._index) if q else []
        have = journal_styles.installed()
        for it in hits:
            label = journal_styles.describe(it)
            if it["name"] in have or it["name"] in cite.STYLES:
                label += "   ✓ added"
            row = QListWidgetItem(label)
            row.setData(NAME_ROLE, it["name"])
            fields = ", ".join((it.get("categories") or {}).get("fields", []))
            row.setToolTip(f"{it['title']}\nField: {fields or '—'}\nId: {it['name']}")
            self._results.addItem(row)
        if q:
            n = len(hits)
            self._status.setText(
                f"{n} match{'es' * (n != 1)}" + (" (first 200 shown)" if n >= 200 else "")
                + ("" if n else " — try fewer words. Some journals aren't in the "
                   "collection: most follow their publisher's house style, so search "
                   "for the publisher (Elsevier, Springer, Wiley, ACS, RSC, IOP…) or "
                   "pick it under Edit ▸ Citation style."))
        if hits:
            self._results.setCurrentRow(0)
        self._update_buttons()

    def _fill_added(self) -> None:
        self._added.clear()
        for name, rec in sorted(journal_styles.installed().items(),
                                key=lambda kv: kv[1]["title"].lower()):
            row = QListWidgetItem(rec["title"])
            row.setData(NAME_ROLE, name)
            self._added.addItem(row)
        if not self._added.count():
            self._added.addItem("(none yet)")

    def _update_buttons(self) -> None:
        self._add_btn.setEnabled(self._results.currentItem() is not None)
        cur = self._added.currentItem()
        self._remove.setEnabled(bool(cur and cur.data(NAME_ROLE)))

    def _add(self) -> None:
        item = self._results.currentItem()
        if item is None:
            return
        name = item.data(NAME_ROLE)
        if name in cite.STYLES:
            self.chosen = name
            self.accept()
            return
        self._status.setText(f"Downloading {item.text()}…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            rec = journal_styles.install(name)
        except journal_styles.StyleError as e:
            self._status.setText(str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._status.setText(f"Added {rec['title']}.")
        self.chosen = name
        self.accept()

    def _remove_selected(self) -> None:
        cur = self._added.currentItem()
        if cur and cur.data(NAME_ROLE):
            journal_styles.remove(cur.data(NAME_ROLE))
            self._fill_added()
            self._run_search()
            self._update_buttons()
