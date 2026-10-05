"""The reference table: a model over the library's entries and a proxy
that filters by collection and search text."""
from __future__ import annotations

import html

from PySide6.QtCore import (QAbstractTableModel, QMimeData, QModelIndex,
                            QSortFilterProxyModel, Qt)

from .icons import icon
from .model import Entry

KEYS_MIME = "application/x-kherveref-keys"

COLUMNS = ["", "Key", "Authors", "Year", "Title", "Journal / Publisher", "Added"]
COL_STATUS, COL_KEY, COL_AUTHORS, COL_YEAR, COL_TITLE, COL_CONTAINER, COL_ADDED = \
    range(len(COLUMNS))

ALL, UNFILED, REVIEW = "all", "unfiled", "review"


class RefTableModel(QAbstractTableModel):
    def __init__(self, parent=None, thumb_path=None):
        super().__init__(parent)
        self._rows: list[Entry] = []
        # Entry -> rendered front-page PNG (or None), for hover previews.
        self._thumb_path = thumb_path or (lambda e: None)

    def set_entries(self, entries) -> None:
        self.beginResetModel()
        self._rows = sorted(entries, key=lambda e: e.key.lower())
        self.endResetModel()

    def entry(self, row: int) -> Entry:
        return self._rows[row]

    def row_of(self, key: str) -> int:
        for i, e in enumerate(self._rows):
            if e.key == key:
                return i
        return -1

    def update_entry(self, e: Entry) -> None:
        row = self.row_of(e.key)
        if row < 0:
            self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
            self._rows.append(e)
            self.endInsertRows()
        else:
            self._rows[row] = e
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, len(COLUMNS) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        e, col = self._rows[index.row()], index.column()
        if role == Qt.DisplayRole:
            return {COL_KEY: e.key, COL_AUTHORS: e.author_text(),
                    COL_YEAR: e.year, COL_TITLE: e.title,
                    COL_CONTAINER: e.container(),
                    COL_ADDED: e.added[:10]}.get(col)
        if role == Qt.DecorationRole and col == COL_STATUS:
            if e.needs_review:
                return icon("review")
            if e.files:
                return icon("pdf")
        if role == Qt.ToolTipRole:
            if col == COL_STATUS:
                if e.needs_review:
                    return "Needs checking: the details were guessed"
                return "PDF attached" if e.files else None
            if col in (COL_TITLE, COL_KEY, COL_AUTHORS):
                png = self._thumb_path(e)
                head = (f"<b>{html.escape(e.title)}</b><br>"
                        f"{html.escape(e.author_text(3))} {e.year}")
                if png is not None:
                    return (f"<img src='{png.as_uri()}' width='180'><br>" + head)
                return head if col == COL_TITLE else None
            if col == COL_AUTHORS:
                return "; ".join(p.display() for p in e.authors or e.editors)
        if role == Qt.UserRole:     # sort key
            if col == COL_STATUS:
                return (2 if e.needs_review else 1 if e.files else 0)
            return (self.data(index, Qt.DisplayRole) or "").lower()
        return None

    def flags(self, index):
        f = super().flags(index)
        return f | Qt.ItemIsDragEnabled if index.isValid() else f

    def mimeTypes(self):
        return [KEYS_MIME, "text/plain"]

    def mimeData(self, indexes):
        keys = []
        for ix in indexes:
            k = self._rows[ix.row()].key
            if k not in keys:
                keys.append(k)
        md = QMimeData()
        md.setData(KEYS_MIME, "\n".join(keys).encode())
        # Dropped into a text editor (e.g. KherveTeX) it is a citation.
        md.setText("\\cite{" + ",".join(keys) + "}")
        return md

    def supportedDragActions(self):
        return Qt.CopyAction | Qt.LinkAction


class RefFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._words: list[str] = []
        self._scope = ALL
        self._collections: set[str] = set()
        self.setSortRole(Qt.UserRole)

    def set_search(self, text: str) -> None:
        self._words = text.lower().split()
        self.invalidateFilter()

    def set_scope(self, scope: str, collections: set[str] | None = None) -> None:
        """ALL, UNFILED, REVIEW, or a collection id with its descendants."""
        self._scope = scope
        self._collections = collections or set()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        e = self.sourceModel().entry(row)
        if self._scope == UNFILED and e.collections:
            return False
        if self._scope == REVIEW and not e.needs_review:
            return False
        if self._scope not in (ALL, UNFILED, REVIEW) and \
                not self._collections.intersection(e.collections):
            return False
        if not self._words:
            return True
        hay = " ".join([e.key, e.title, e.subtitle, e.container(), e.date,
                        e.doi, e.eprint, e.isbn, e.note, e.notes,
                        " ".join(e.keywords),
                        " ".join(p.display() for p in e.authors + e.editors)]
                       ).lower()
        return all(w in hay for w in self._words)
