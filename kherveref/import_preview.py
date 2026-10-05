"""What a folder import will bring in, before anything happens.

Lists every subfolder and importable file (PDFs and reference exports)
in a tree with checkboxes; unticking a folder leaves out everything in
it. Nothing is imported until the user presses "Import N files".
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import importer
from .icons import icon

PATH_ROLE = Qt.UserRole


def needs_preview(paths: list[Path], threshold: int = 15) -> bool:
    """Folders always get a preview; loose files only when there are many."""
    return any(Path(p).is_dir() for p in paths) or len(paths) > threshold


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class ImportPreviewDialog(QDialog):
    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import — check what will be added")
        self.resize(720, 560)
        self._files = importer.expand_paths(paths)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        hint = QLabel("Untick folders or files you don't want. Each PDF becomes "
                      "a reference (its details are looked up online); "
                      "reference files (.bib, .ris…) add every reference they "
                      "contain. You can undo the whole import afterwards "
                      "(Edit ▸ Undo).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Size"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._build(paths)
        self._tree.itemChanged.connect(lambda *_: self._update())

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok |
                                         QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self._summary)
        lay.addWidget(hint)
        lay.addWidget(self._tree, 1)
        lay.addWidget(self._buttons)
        self._update()

    # ----- tree -----

    def _build(self, paths: list[Path]) -> None:
        self._tree.blockSignals(True)
        folders: dict[Path, QTreeWidgetItem] = {}
        roots = [Path(p) for p in paths]

        def folder_item(d: Path, root: Path) -> QTreeWidget | QTreeWidgetItem:
            if d in folders:
                return folders[d]
            # The dropped folder is a top-level row; its subfolders nest.
            parent = self._tree if d == root else folder_item(d.parent, root)
            it = QTreeWidgetItem([d.name or str(d)])
            it.setIcon(0, icon("collection"))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            it.setCheckState(0, Qt.Checked)
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(it)
            else:
                parent.addChild(it)
            folders[d] = it
            return it

        for f in self._files:
            root = next((r for r in roots if r == f or r in f.parents), f)
            parent = folder_item(f.parent, root) if root.is_dir() else self._tree
            it = QTreeWidgetItem([f.name, _human(f.stat().st_size)])
            it.setIcon(0, icon("pdf" if f.suffix.lower() == ".pdf" else "import_bib"))
            it.setData(0, PATH_ROLE, str(f))
            it.setToolTip(0, str(f))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(it)
            else:
                parent.addChild(it)
        self._tree.expandToDepth(1)
        self._tree.blockSignals(False)

    def _file_items(self):
        stack = [self._tree.topLevelItem(i)
                 for i in range(self._tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, PATH_ROLE):
                yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    # ----- result -----

    def selected_files(self) -> list[Path]:
        chosen = {it.data(0, PATH_ROLE) for it in self._file_items()
                  if it.checkState(0) == Qt.Checked}
        return [f for f in self._files if str(f) in chosen]

    def _update(self) -> None:
        sel = self.selected_files()
        pdfs = sum(1 for f in sel if f.suffix.lower() == ".pdf")
        refs = len(sel) - pdfs
        folders = len({f.parent for f in self._files})
        total_pdfs = sum(1 for f in self._files if f.suffix.lower() == ".pdf")
        parts = [f"{total_pdfs} PDF{'s' * (total_pdfs != 1)}"]
        if len(self._files) - total_pdfs:
            n = len(self._files) - total_pdfs
            parts.append(f"{n} reference file{'s' * (n != 1)}")
        found = " and ".join(parts) + (
            f" in {folders} folder{'s' * (folders != 1)}" if folders > 1 else "")
        self._summary.setText(
            f"<b>Found {found}.</b>" if self._files else
            "<b>Nothing to import here</b> — no PDFs or reference files "
            "(.bib, .ris, EndNote .xml, .nbib) were found.")
        ok = self._buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(sel))
        what = []
        if pdfs:
            what.append(f"{pdfs} PDF{'s' * (pdfs != 1)}")
        if refs:
            what.append(f"{refs} reference file{'s' * (refs != 1)}")
        ok.setText("Import " + " and ".join(what) if what else "Import")
