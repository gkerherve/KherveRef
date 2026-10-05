import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from kherveref import fetch, git_backend, jobs, library, store
from kherveref.mainwindow import MainWindow
from kherveref.table_model import ALL, REVIEW
from helpers import FakeNet, make_pdf


def wait_for_job(w):
    while w._job is not None:
        QApplication.processEvents()


def test_starts_on_start_page(qapp):
    w = MainWindow()
    assert w._stack.currentIndex() == 0
    assert not w.act_push.isEnabled() and not w.act_add_pdfs.isEnabled()
    assert "no library" in w.windowTitle()


def test_open_library_and_reopen(win):
    assert win._stack.currentIndex() == 1
    assert win.windowTitle().endswith("Thesis refs")
    assert win.act_add_pdfs.isEnabled()
    assert (win.library.root / "library.bib").exists()
    again = MainWindow()
    again.reopen_last_library()
    assert again.library.root == win.library.root


def test_drop_pdf_becomes_reference(win, tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    win.import_paths([pdf])
    wait_for_job(win)
    assert list(win.entries) == ["smith2020surface"]
    assert win._proxy.rowCount() == 1
    assert win.selected_keys() == ["smith2020surface"]
    assert win._editor.entry.title == "Surface chemistry of titania films"
    assert git_backend.history(win.library.root)[0][3].startswith("Import: 1 added")
    assert "smith2020surface" in (win.library.root / "library.bib").read_text()


def test_folder_import_and_review_scope(win, tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    make_pdf(folder / "a.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    make_pdf(folder / "b.pdf", ["plain text with no identifiers at all " * 3],
             big="Unknown Internal Memo About Things")
    win.import_paths([folder])
    wait_for_job(win)
    assert len(win.entries) == 2
    win._scope = REVIEW
    win._apply_scope()
    assert win._proxy.rowCount() == 1


def test_identifiers_and_bibtex_text(win):
    win._import_text("arXiv:2101.00001\n978-0-306-40615-7")
    wait_for_job(win)
    win._import_text("@article{mine2020, title={Pasted entry title}, year=2020}")
    wait_for_job(win)
    assert {"martin2021preprint", "sagan1980book", "mine2020"} <= set(win.entries)


def test_edit_and_save(win):
    win._import_text("10.1016/j.apsusc.2020.145000")
    wait_for_job(win)
    ed = win._editor
    ed._widgets["title"].setPlainText("Edited title")
    assert ed.is_dirty()
    ed.save()
    assert store.load_entries(win.library)["smith2020surface"].title == "Edited title"
    assert git_backend.history(win.library.root)[0][3] == "Edit smith2020surface"


def test_new_reference_gets_key_on_save(win):
    win._new_reference()
    ed = win._editor
    ed._widgets["title"].setPlainText("Handmade reference")
    ed._widgets["authors"].setPlainText("Curie, Marie")
    ed._widgets["date"].setText("1903")
    ed.save()
    assert "curie1903handmade" in win.entries


def test_collections_and_search(win):
    win._import_text("10.1016/j.apsusc.2020.145000\narXiv:2101.00001")
    wait_for_job(win)
    c = store.Collection(store.new_collection_id(), "Chapter 1")
    win.collections.append(c)
    store.save_collections(win.library, win.collections)
    win._rebuild_tree()
    win._add_keys_to_collection(["smith2020surface"], c.id)
    win._scope = c.id
    win._apply_scope()
    assert win._proxy.rowCount() == 1
    win._scope = ALL
    win._apply_scope()
    win._search.setText("graphene")
    assert win._proxy.rowCount() == 1
    win._search.setText("")
    assert win._proxy.rowCount() == 2


def test_export_biblatex(win, tmp_path, monkeypatch):
    win._import_text("10.1016/j.apsusc.2020.145000")
    wait_for_job(win)
    out = tmp_path / "out.bib"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    win._table.selectionModel().clearSelection()
    win._export("biblatex")
    text = out.read_text()
    assert "journaltitle" in text and "@article{smith2020surface," in text


def test_delete(win):
    win._import_text("10.1016/j.apsusc.2020.145000")
    wait_for_job(win)
    win._select_keys(["smith2020surface"])
    win._delete_selected()
    assert win.entries == {} and win._proxy.rowCount() == 0
    assert git_backend.history(win.library.root)[0][3] == "Delete smith2020surface"


def test_theme_switch_keeps_icons(win):
    win._set_theme("Dark")
    assert not win.act_open.icon().isNull()


def test_reveal_request_selects_reference(win, tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    win.import_paths([pdf])
    wait_for_job(win)
    win._table.selectionModel().clearSelection()
    win.handle_request({"cmd": "reveal", "paths": [str(pdf)]})
    assert win.selected_keys() == ["smith2020surface"]
    stored = win.library.root / win.entries["smith2020surface"].files[0].path
    assert win.find_by_file(stored) == "smith2020surface"


def test_add_request_imports(win, tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", ["arXiv:2101.00001"])
    win.handle_request({"cmd": "add", "paths": [str(pdf)]})
    wait_for_job(win)
    assert "martin2021preprint" in win.entries


def test_ai_menu_and_guide(win, monkeypatch):
    menus = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}
    assert win.act_claude in menus["AI"].actions()
    assert win.act_claude not in menus["Help"].actions()
    assert win.act_guide in menus["Help"].actions()
    opened = []
    from kherveref import mainwindow
    monkeypatch.setattr(mainwindow.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url)))
    win._open_guide()
    assert opened and opened[0].toLocalFile().endswith("docs/guide/index.html")
