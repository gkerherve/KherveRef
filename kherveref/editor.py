"""The details pane: edits one reference."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .icons import icon
from .model import ENTRY_TYPES, Entry, Person, parse_names

# Fields shown for each type (anything non-empty is always shown too).
_COMMON = ("title", "authors", "date", "doi", "url")
_BY_TYPE = {
    "article": ("journal", "volume", "number", "pages", "issn", "eprint"),
    "inproceedings": ("booktitle", "editors", "pages", "publisher", "location",
                      "series", "volume", "institution"),
    "book": ("subtitle", "editors", "publisher", "location", "edition",
             "series", "volume", "isbn"),
    "inbook": ("booktitle", "editors", "publisher", "location", "pages",
               "edition", "isbn"),
    "incollection": ("booktitle", "editors", "publisher", "location", "pages",
                     "isbn"),
    "thesis": ("thesis_type", "institution", "location"),
    "report": ("institution", "number", "location"),
    "online": ("institution",),
    "dataset": ("publisher", "edition"),
    "software": ("publisher", "edition"),
    "patent": ("number", "location", "institution"),
    "unpublished": ("eprint", "institution"),
    "misc": ("publisher", "institution", "eprint", "isbn"),
}

_LABELS = {
    "title": "Title", "subtitle": "Subtitle", "authors": "Authors",
    "editors": "Editors", "date": "Date", "journal": "Journal",
    "booktitle": "Book / proceedings", "publisher": "Publisher",
    "institution": "Institution", "volume": "Volume", "number": "Number",
    "pages": "Pages", "edition": "Edition", "series": "Series",
    "location": "Place", "doi": "DOI", "url": "URL", "isbn": "ISBN",
    "issn": "ISSN", "eprint": "arXiv id", "thesis_type": "Thesis type",
    "keywords": "Keywords", "abstract": "Abstract", "note": "Note",
    "language": "Language",
}
_MULTILINE = {"title": 2, "authors": 4, "editors": 2, "abstract": 5}
_ORDER = ("title", "subtitle", "authors", "editors", "date", "journal",
          "booktitle", "publisher", "institution", "thesis_type", "volume",
          "number", "pages", "edition", "series", "location", "doi", "url",
          "isbn", "issn", "eprint", "language", "keywords", "note", "abstract")


def _names_text(people: list[Person]) -> str:
    return "\n".join(p.display() if not p.literal else "{" + p.literal + "}"
                     for p in people)


class _Cover(QLabel):
    """The PDF's front page above the details; a click opens the PDF."""
    clicked = Signal()

    def mousePressEvent(self, ev):  # noqa: N802 — Qt override
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class EntryEditor(QWidget):
    """Emits `save_requested` with the edited entry; the main window
    owns saving and committing."""
    save_requested = Signal(object)
    lookup_requested = Signal(object)
    open_file_requested = Signal(object, int)
    attach_requested = Signal(object)
    rename_requested = Signal()
    copy_cite_requested = Signal()
    remove_file_requested = Signal(object, int)
    dirty_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry: Entry | None = None
        self._loading = False
        self._dirty = False
        self._widgets: dict[str, QWidget] = {}
        self._rows: dict[str, tuple[QLabel, QWidget]] = {}

        self._empty = QLabel("Select a reference, or drop PDFs, folders, .bib "
                             "files or DOIs onto the window to add some.")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setMargin(24)

        form_host = QWidget()
        host_lay = QVBoxLayout(form_host)
        self._cover = _Cover()
        self._cover.setAlignment(Qt.AlignHCenter)
        self._cover.setCursor(Qt.PointingHandCursor)
        self._cover.setToolTip("Open the PDF")
        self._cover.clicked.connect(
            lambda: self._entry and self._entry.files and
            self.open_file_requested.emit(self._entry, 0))
        self._cover.hide()
        host_lay.addWidget(self._cover)
        self._form = QFormLayout()
        host_lay.addLayout(self._form)
        host_lay.addStretch(1)
        self._form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self._form.setLabelAlignment(Qt.AlignRight)

        # The citation key: the short name LaTeX cites (\cite{key}).
        self._key = QLabel()
        self._key.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._key.setStyleSheet("font-weight: bold;")
        self._key.setToolTip("The citation key: cite it in LaTeX as \\cite{key}")
        key_row = QWidget()
        kl = QHBoxLayout(key_row)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(self._key, 1)
        self._key_buttons = []
        for text, tip, sig in (("Copy \\cite", "Copy \\cite{key} for LaTeX",
                                self.copy_cite_requested),
                               ("Rename…", "Give this reference another key",
                                self.rename_requested)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(sig.emit)
            kl.addWidget(b)
            self._key_buttons.append(b)
        self._form.addRow("Cite key", key_row)

        self._type = QComboBox()
        for t, label in ENTRY_TYPES.items():
            self._type.addItem(label, t)
        self._type.currentIndexChanged.connect(self._type_changed)
        self._form.addRow("Type", self._type)

        self._review = QCheckBox("Needs checking")
        self._review.setToolTip("Set when the details were guessed from the PDF.")
        self._review.toggled.connect(self._mark_dirty)
        self._form.addRow("", self._review)

        for name in _ORDER:
            if name == "thesis_type":
                w = QComboBox()
                w.setEditable(True)
                w.addItems(["", "phd", "master"])
                w.currentTextChanged.connect(self._mark_dirty)
            elif name in _MULTILINE:
                w = QPlainTextEdit()
                w.setTabChangesFocus(True)
                w.setFixedHeight(10 + 20 * _MULTILINE[name])
                w.textChanged.connect(self._mark_dirty)
                if name in ("authors", "editors"):
                    w.setPlaceholderText("One per line: Family, Given")
            else:
                w = QLineEdit()
                w.textEdited.connect(self._mark_dirty)
                if name == "date":
                    w.setPlaceholderText("YYYY or YYYY-MM-DD")
                if name == "keywords":
                    w.setPlaceholderText("comma separated")
            self._widgets[name] = w
            label = QLabel(_LABELS[name])
            self._form.addRow(label, w)
            self._rows[name] = (label, w)

        self._files = QListWidget()
        self._files.setMaximumHeight(90)
        self._files.itemDoubleClicked.connect(
            lambda it: self._entry and self.open_file_requested.emit(
                self._entry, self._files.row(it)))
        files_row = QWidget()
        fl = QVBoxLayout(files_row)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self._files)
        fb = QHBoxLayout()
        for text, name, slot in (
                ("Open", "open_pdf", lambda: self._file_action(self.open_file_requested)),
                ("Attach…", "attach", lambda: self._entry and
                 self.attach_requested.emit(self._entry)),
                ("Remove", "delete", lambda: self._file_action(
                    self.remove_file_requested))):
            b = QToolButton()
            b.setText(text)
            b.setIcon(icon(name))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.clicked.connect(slot)
            fb.addWidget(b)
        fb.addStretch(1)
        fl.addLayout(fb)
        self._form.addRow("Files", files_row)

        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Your notes (not exported)")
        self._notes.setFixedHeight(90)
        self._notes.textChanged.connect(self._mark_dirty)
        self._form.addRow("Notes", self._notes)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)

        self._save = QPushButton(icon("save"), "Save")
        self._save.setShortcut("Ctrl+S")
        self._save.clicked.connect(self.save)
        self._revert = QPushButton(icon("revert"), "Revert")
        self._revert.clicked.connect(lambda: self.set_entry(self._entry))
        self._lookup = QPushButton(icon("lookup"), "Look up details")
        self._lookup.setToolTip("Fetch the details again from the DOI, arXiv "
                                "id, ISBN or an exact title match")
        self._lookup.clicked.connect(
            lambda: self._entry and self.lookup_requested.emit(self._current()))
        buttons = QHBoxLayout()
        buttons.addWidget(self._lookup)
        buttons.addStretch(1)
        buttons.addWidget(self._revert)
        buttons.addWidget(self._save)

        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(scroll)
        bl.addLayout(buttons)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._empty)
        lay.addWidget(self._body)
        self.set_entry(None)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    # ----- state -----

    @property
    def entry(self) -> Entry | None:
        return self._entry

    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self, *_):
        if not self._loading and not self._dirty and self._entry is not None:
            self._dirty = True
            self._save.setEnabled(True)
            self._revert.setEnabled(True)
            self.dirty_changed.emit(True)

    def _set_clean(self):
        self._dirty = False
        self._save.setEnabled(False)
        self._revert.setEnabled(False)
        self.dirty_changed.emit(False)

    def set_entry(self, e: Entry | None) -> None:
        self._entry = e
        self._empty.setVisible(e is None)
        self._body.setVisible(e is not None)
        if e is None or not e.files:
            self._cover.hide()
        if e is None:
            self._set_clean()
            return
        self._loading = True
        self._key.setText(e.key or "(assigned when saved)")
        for b in self._key_buttons:
            b.setEnabled(bool(e.key))
        self._type.setCurrentIndex(max(0, self._type.findData(e.type)))
        self._review.setChecked(e.needs_review)
        for name, w in self._widgets.items():
            if name in ("authors", "editors"):
                w.setPlainText(_names_text(getattr(e, name)))
            elif name == "keywords":
                w.setText(", ".join(e.keywords))
            elif name == "thesis_type":
                w.setCurrentText(e.thesis_type)
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText(getattr(e, name))
            else:
                w.setText(getattr(e, name))
        self._notes.setPlainText(e.notes)
        self._files.clear()
        for a in e.files:
            QListWidgetItem(icon("pdf"), a.path.rsplit("/", 1)[-1], self._files)
        self._loading = False
        self._update_visible_rows()
        self._set_clean()

    def set_cover(self, pm: QPixmap | None) -> None:
        if pm is None or pm.isNull():
            self._cover.hide()
            return
        ratio = self.devicePixelRatioF()
        scaled = pm.scaled(int(230 * ratio), int(320 * ratio), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self._cover.setPixmap(scaled)
        self._cover.show()

    def _type_changed(self, *_):
        self._update_visible_rows()
        self._mark_dirty()

    def _update_visible_rows(self):
        typ = self._type.currentData() or "misc"
        wanted = set(_COMMON) | set(_BY_TYPE.get(typ, ())) | {"keywords",
                                                              "abstract", "note"}
        for name, (label, w) in self._rows.items():
            show = name in wanted or bool(self._value(name))
            label.setVisible(show)
            w.setVisible(show)

    def _value(self, name: str):
        w = self._widgets[name]
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText().strip()
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        return w.text().strip()

    def _current(self) -> Entry:
        """A copy of the entry with the form's values applied."""
        e = Entry.from_dict(self._entry.to_dict())
        e.type = self._type.currentData() or "misc"
        e.needs_review = self._review.isChecked()
        for name in _ORDER:
            v = self._value(name)
            if name in ("authors", "editors"):
                setattr(e, name, parse_names(v + "\n") if v else [])
            elif name == "keywords":
                e.keywords = [k.strip() for k in re.split(r"[;,]", v) if k.strip()]
            elif name == "title":
                e.title = " ".join(v.split())
            else:
                setattr(e, name, v)
        if e.eprint and not e.eprinttype and re.match(
                r"^(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", e.eprint):
            e.eprinttype = "arxiv"
        e.notes = self._notes.toPlainText()
        return e

    def save(self) -> None:
        if self._entry is not None and self._dirty:
            self.save_requested.emit(self._current())

    def _file_action(self, signal) -> None:
        row = self._files.currentRow()
        if self._entry is not None and row < 0 and self._entry.files:
            row = 0
        if self._entry is not None and row >= 0:
            signal.emit(self._entry, row)
