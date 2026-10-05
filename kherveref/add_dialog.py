"""File ▸ Add by DOI / arXiv / ISBN: paste identifiers (or links, or a
whole reference list containing them) and see, as you type, what each
one was recognised as before anything is looked up."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QVBoxLayout)

from . import fetch

_KIND = {"doi": "DOI", "arxiv": "arXiv", "isbn": "ISBN"}

EXAMPLES = """10.1038/nature14539
https://arxiv.org/abs/1706.03762
978-0-262-03561-3"""


def find_identifiers(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """([(kind, value)...] in order without repeats, [unrecognised lines]).
    A line may hold a bare id, a link, or a whole citation with a DOI."""
    found: list[tuple[str, str]] = []
    unknown: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        hits = []
        kind, value = fetch.classify(line)
        if kind:
            hits.append((kind, value))
        else:
            for token in re.split(r"[\s,;]+", line):
                kind, value = fetch.classify(token)
                if kind:
                    hits.append((kind, value))
            if not hits:
                doi = fetch.find_doi(line) or ""
                arx = fetch.find_arxiv(line) or ""
                hits += [("doi", doi)] if doi else []
                hits += [("arxiv", arx)] if arx else []
        if not hits:
            unknown.append(line)
        for h in hits:
            if h not in found:
                found.append(h)
    return found, unknown


def as_request(kind: str, value: str) -> str:
    """The text the importer classifies back to (kind, value)."""
    return f"arXiv:{value}" if kind == "arxiv" else value


class AddByIdentifierDialog(QDialog):
    def __init__(self, parent=None, text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add references by DOI, arXiv or ISBN")
        self.resize(560, 480)

        intro = QLabel(
            "Paste the identifier of each paper or book — one per line. "
            "KherveRef looks up the title, authors, journal and year for you.<br>"
            "<span style='color:gray'>A <b>DOI</b> is printed on almost every "
            "article (e.g. <i>10.1038/nature14539</i>); preprints have an "
            "<b>arXiv</b> number; books an <b>ISBN</b>. Links such as "
            "https://doi.org/… or arxiv.org/abs/… work too, and so does a "
            "copied reference list that contains DOIs.</span>")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText("For example:\n" + EXAMPLES)
        self._edit.textChanged.connect(self._update)

        self._found = QListWidget()
        self._found.setMaximumHeight(140)
        self._found_label = QLabel()

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok |
                                         QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self._edit, 1)
        lay.addWidget(self._found_label)
        lay.addWidget(self._found)
        lay.addWidget(self._buttons)

        if text is None:
            # Save a paste: start with whatever recognisable is on the clipboard.
            clip = QApplication.clipboard().text() or ""
            text = clip if find_identifiers(clip)[0] else ""
        self._edit.setPlainText(text)
        self._update()

    def _update(self):
        found, unknown = find_identifiers(self._edit.toPlainText())
        self._found.clear()
        for kind, value in found:
            QListWidgetItem(f"✓  {_KIND[kind]}  {value}", self._found)
        for line in unknown:
            it = QListWidgetItem(f"✗  not recognised: {line[:70]}", self._found)
            it.setForeground(Qt.gray)
        self._found_label.setText(
            "What will be looked up:" if found or unknown else
            "Recognised identifiers will be listed here.")
        ok = self._buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(found))
        n = len(found)
        ok.setText(f"Add {n} reference{'s' if n != 1 else ''}" if n else "Add")

    def requests(self) -> list[str]:
        return [as_request(k, v) for k, v in
                find_identifiers(self._edit.toPlainText())[0]]
