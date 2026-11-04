"""Library ▸ Find duplicates: groups of references that look like the
same work, to show in the table or merge into one."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from . import store
from .icons import icon
from .model import Entry


class DuplicatesDialog(QDialog):
    show_keys = Signal(list)
    merge = Signal(str, list)

    def __init__(self, entries: dict[str, Entry], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find duplicates")
        self.resize(780, 460)
        self._entries = entries
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Key", "Year", "Authors", "Title", "PDF"])
        self._tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self._tree.currentItemChanged.connect(self._sync_buttons)
        self._tree.itemDoubleClicked.connect(lambda *_: self._show())
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        self._btn_show = QPushButton(icon("find"), "Show in library")
        self._btn_show.setToolTip("Select this group's references in the table "
                                  "to compare them side by side.")
        self._btn_show.clicked.connect(self._show)
        self._btn_merge = QPushButton(icon("merge"), "Merge into this one…")
        self._btn_merge.setToolTip(
            "Keep the highlighted reference and its key; fill its empty "
            "fields, PDFs, collections and notes from the others, then "
            "delete them.")
        self._btn_merge.clicked.connect(self._merge)
        buttons.addButton(self._btn_show, QDialogButtonBox.ActionRole)
        buttons.addButton(self._btn_merge, QDialogButtonBox.ActionRole)
        lay = QVBoxLayout(self)
        lay.addWidget(self._label)
        lay.addWidget(self._tree)
        lay.addWidget(buttons)
        self.refresh(entries)

    def refresh(self, entries: dict[str, Entry]) -> None:
        self._entries = entries
        self._tree.clear()
        groups = store.find_duplicates(entries.values())
        for g in groups:
            top = QTreeWidgetItem([f"{len(g['keys'])} references — "
                                   + "; ".join(g["reasons"])])
            top.setFirstColumnSpanned(True)
            top.setIcon(0, icon("duplicates"))
            for k in g["keys"]:
                e = entries[k]
                child = QTreeWidgetItem([k, e.year, e.author_text(2), e.title,
                                         "✓" if e.files else ""])
                child.setData(0, Qt.UserRole, k)
                top.addChild(child)
            self._tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)
        self._tree.expandAll()
        self._tree.resizeColumnToContents(0)
        self._label.setText(
            "No duplicates found." if not groups else
            f"{len(groups)} group(s) of references that look like the same "
            "work. Highlight the one to keep and click Merge, or Show in "
            "library to compare them.")
        self._sync_buttons()

    def _group(self) -> tuple[str | None, list[str]]:
        it = self._tree.currentItem()
        if it is None:
            return None, []
        top = it.parent() or it
        keys = [top.child(i).data(0, Qt.UserRole) for i in range(top.childCount())]
        return it.data(0, Qt.UserRole), keys

    def _sync_buttons(self, *_):
        chosen, keys = self._group()
        self._btn_show.setEnabled(bool(keys))
        self._btn_merge.setEnabled(chosen is not None)

    def _show(self) -> None:
        _chosen, keys = self._group()
        if keys:
            self.show_keys.emit(keys)

    def _merge(self) -> None:
        keep, keys = self._group()
        if keep is None:
            return
        others = [k for k in keys if k != keep]
        if QMessageBox.question(
                self, "Merge duplicates",
                f"Keep “{keep}” and merge {', '.join(others)} into it?\n\n"
                "Documents citing the merged-away keys will need "
                f"\\cite{{{keep}}} instead (their keys are noted in its notes). "
                "Edit ▸ Undo puts them back.") != QMessageBox.Yes:
            return
        self.merge.emit(keep, others)
