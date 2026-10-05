from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from kherveref import git_backend, store
from kherveref.import_preview import ImportPreviewDialog, needs_preview
from helpers import make_pdf


def wait_for_job(w):
    while w._job is not None:
        QApplication.processEvents()


def _folder(tmp_path):
    f = tmp_path / "Papers"
    (f / "Chapter 1").mkdir(parents=True)
    (f / "Old stuff").mkdir()
    make_pdf(f / "a.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    make_pdf(f / "Chapter 1" / "b.pdf", ["arXiv:2101.00001"])
    make_pdf(f / "Old stuff" / "c.pdf", ["nothing", "here"], big="Old Unwanted Report Title")
    (f / "Chapter 1" / "refs.ris").write_text("TY  - BOOK\nTI  - A Book\nPY  - 1999\nER  -\n")
    (f / "notes.txt").write_text("ignored")
    return f


def test_preview_lists_and_filters(qapp, tmp_path):
    f = _folder(tmp_path)
    assert needs_preview([f]) and not needs_preview([f / "a.pdf"])
    dlg = ImportPreviewDialog([f])
    top = dlg._tree.topLevelItem(0)
    assert top.text(0) == "Papers"
    names = sorted(top.child(i).text(0) for i in range(top.childCount()))
    assert names == ["Chapter 1", "Old stuff", "a.pdf"]
    assert "Found 3 PDFs and 1 reference file in 3 folders" in dlg._summary.text()
    ok = dlg._buttons.button(dlg._buttons.StandardButton.Ok)
    assert ok.text() == "Import 3 PDFs and 1 reference file"
    old = next(top.child(i) for i in range(top.childCount())
               if top.child(i).text(0) == "Old stuff")
    old.setCheckState(0, Qt.Unchecked)
    assert sorted(p.name for p in dlg.selected_files()) == ["a.pdf", "b.pdf", "refs.ris"]
    assert ok.text() == "Import 2 PDFs and 1 reference file"


def test_folder_import_undo_redo(win, tmp_path):
    f = _folder(tmp_path)
    win.import_paths([f])
    wait_for_job(win)
    assert len(win.entries) == 4
    assert win.act_undo.isEnabled() and win.act_undo.text().startswith("&Undo Import")
    pdfs = sorted(p.name for p in win.library.files_dir.glob("*.pdf"))
    assert len(pdfs) == 3

    win._undo_last()
    assert win.entries == {} and not list(win.library.files_dir.glob("*.pdf"))
    assert win.act_redo.isEnabled() and not win.act_undo.isEnabled()
    assert git_backend.history(win.library.root)[0][3].startswith("Undo: Import")

    win._redo_last()
    assert len(win.entries) == 4
    assert sorted(p.name for p in win.library.files_dir.glob("*.pdf")) == pdfs
    assert git_backend.history(win.library.root)[0][3].startswith("Redo: Import")


def test_undo_edit_then_new_action_clears_redo(win):
    win._import_text("10.1016/j.apsusc.2020.145000")
    wait_for_job(win)
    win._editor._widgets["title"].setPlainText("Changed title")
    win._editor.save()
    win._undo_last()
    assert win.entries["smith2020surface"].title == "Surface chemistry of titania files" \
        or win.entries["smith2020surface"].title == "Surface chemistry of titania films"
    assert win.act_redo.isEnabled()
    win._import_text("arXiv:2101.00001")
    wait_for_job(win)
    assert not win.act_redo.isEnabled()


def test_undo_refused_when_changed_elsewhere(win, monkeypatch):
    win._import_text("10.1016/j.apsusc.2020.145000")
    wait_for_job(win)
    e = store.load_entries(win.library)["smith2020surface"]
    e.notes = "edited by Claude"
    store.save_entry(win.library, e)
    git_backend.commit_all(win.library.root, "Edit smith2020surface via Claude")
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    win._undo_last()
    assert shown and "can't be undone" in shown[0]
    assert "smith2020surface" in store.load_entries(win.library)
    assert not win.act_undo.isEnabled()


def test_delete_in_toolbar_and_right_click_selects(win, monkeypatch):
    win._import_text("10.1016/j.apsusc.2020.145000\narXiv:2101.00001")
    wait_for_job(win)
    from PySide6.QtWidgets import QToolBar
    assert win.findChildren(QToolBar)[0].widgetForAction(win.act_delete) is not None
    win._table.selectionModel().clearSelection()
    monkeypatch.setattr(win, "_table_menu", lambda pos: None)
    row = win._proxy.mapFromSource(win._model.index(win._model.row_of("martin2021preprint"), 1))
    pos = win._table.visualRect(row).center()
    win._context_menu(win._table, pos)
    assert win.selected_keys() == ["martin2021preprint"]
    win._delete_selected()
    assert "martin2021preprint" not in win.entries
    win._undo_last()
    assert "martin2021preprint" in win.entries


def test_folder_dropped_on_a_text_field_is_imported(win, tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(win, "import_paths", lambda paths, *a, **k: got.append(paths))
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    field = win._editor._widgets["doi"]
    ev = QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier,
                    QDropEvent.Type.Drop)
    assert win.eventFilter(field, ev) is True
    assert got == [[tmp_path]]
    # The collection tree keeps its own drop handling.
    assert win.eventFilter(win._tree.viewport(), ev) is False
