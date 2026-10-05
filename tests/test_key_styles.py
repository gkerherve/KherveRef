import json

from PySide6.QtWidgets import QApplication, QMessageBox

from kherveref import library, store
from kherveref.keys import KEY_STYLES, base_key, unique_key
from kherveref.model import Entry, Person


def _e(**kw):
    return Entry(authors=[Person("Bagarinao", "K")], date="2023",
                 title="Nanostructured LSC thin films", **kw)


def test_styles():
    e = _e()
    assert base_key(e) == "bagarinao2023nanostructured"
    assert base_key(e, "Author_Year") == "Bagarinao2023"
    assert base_key(e, "author_year") == "bagarinao2023"
    assert unique_key(e, ["Bagarinao2023"], "Author_Year") == "Bagarinao2023b"
    assert set(KEY_STYLES) == {"author_year_word", "Author_Year", "author_year"}


def test_style_is_saved_in_kref_and_used(tmp_path):
    lib = library.create_library(tmp_path / "L", "L")
    library.set_key_style(lib, "Author_Year")
    data = json.loads((tmp_path / "L" / "L.kref").read_text())
    assert data["key_style"] == "Author_Year" and data["name"] == "L"
    lib = library.open_library(tmp_path / "L")
    assert lib.key_style == "Author_Year"
    entries = {}
    store.add_entry(lib, _e(), entries)
    store.add_entry(lib, _e(), entries)
    assert sorted(entries) == ["Bagarinao2023", "Bagarinao2023b"]


def test_rename_keys_moves_pdfs_and_allows_swaps(tmp_path):
    lib = library.create_library(tmp_path / "L")
    entries = {}
    for key in ("a", "b", "ab"):
        e = Entry(key=key, title=key)
        store.add_entry(lib, e, entries)
        src = tmp_path / f"{key}.pdf"
        src.write_bytes(key.encode())
        store.attach_file(lib, e, src)
        store.save_entry(lib, e)
    store.rename_keys(lib, entries, {"a": "b", "b": "a"})   # swap
    on_disk = store.load_entries(lib)
    assert sorted(on_disk) == ["a", "ab", "b"]
    assert (lib.root / on_disk["b"].files[0].path).read_bytes() == b"a"
    assert (lib.root / on_disk["a"].files[0].path).read_bytes() == b"b"
    assert on_disk["b"].files[0].path == "PDFs/b.pdf"
    # "ab.pdf" belongs to key "ab", not to "a": untouched.
    assert on_disk["ab"].files[0].path == "PDFs/ab.pdf"
    assert (lib.root / "PDFs/ab.pdf").read_bytes() == b"ab"


def test_keys_in_style_are_unique():
    entries = {f"k{i}": Entry(key=f"k{i}", authors=[Person("Smith")], date="2020",
                              title=f"T{i}", added=f"2026-01-0{i}")
               for i in range(1, 4)}
    m = store.keys_in_style(entries, "Author_Year")
    assert m == {"k1": "Smith2020", "k2": "Smith2020b", "k3": "Smith2020c"}


def test_window_rename_all_and_undo(win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    win._import_text("10.1016/j.apsusc.2020.145000\narXiv:2101.00001")
    while win._job is not None:
        QApplication.processEvents()
    assert sorted(win.entries) == ["martin2021preprint", "smith2020surface"]
    style_action = dict(win._key_style_actions)["Author_Year"]
    style_action.trigger()
    assert win.library.key_style == "Author_Year" and style_action.isChecked()
    win._rekey_all()
    assert sorted(win.entries) == ["Martin2021", "Smith2020"]
    bib = (win.library.root / "library.bib").read_text()
    assert "@article{Smith2020," in bib and "smith2020surface" not in bib
    win._undo_last()
    assert sorted(win.entries) == ["martin2021preprint", "smith2020surface"]


def test_editor_key_buttons(win):
    win._import_text("10.1016/j.apsusc.2020.145000")
    while win._job is not None:
        QApplication.processEvents()
    win._select_keys(["smith2020surface"])
    win._editor.copy_cite_requested.emit()
    assert QApplication.clipboard().text() == "\\cite{smith2020surface}"


def test_export_collection_bib(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win._import_text("10.1016/j.apsusc.2020.145000\narXiv:2101.00001")
    while win._job is not None:
        QApplication.processEvents()
    c = store.Collection(store.new_collection_id(), "Paper 1: XPS")
    win.collections.append(c)
    store.save_collections(win.library, win.collections)
    win._add_keys_to_collection(["smith2020surface"], c.id)
    asked = []

    def save_name(parent, title, start, filt):
        asked.append(start)
        return str(tmp_path / "paper1.bib"), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(save_name))
    win._export_collection(c.id)
    assert asked[0].endswith("Paper-1-XPS.bib")
    text = (tmp_path / "paper1.bib").read_text()
    assert "@article{smith2020surface," in text and "martin2021" not in text
    assert "year" in text      # classic BibTeX for \bibliography{}
