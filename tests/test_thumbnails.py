import time

from PySide6.QtWidgets import QApplication

from kherveref import icons, library, store, thumbnails
from kherveref.model import Entry
from helpers import make_pdf


def _wait(cond, seconds=20):
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return cond()


def _entry_with_pdf(lib, tmp_path, key="k1"):
    e = Entry(key=key, title="A paper")
    store.attach_file(lib, e, make_pdf(tmp_path / f"{key}.pdf", ["front page"],
                                       big="A Paper Title"))
    store.save_entry(lib, e)
    return e


def test_render(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", ["hello"])
    out = tmp_path / "t" / "a.png"
    assert thumbnails.render(pdf, out) and out.exists()
    assert not thumbnails.render(tmp_path / "missing.pdf", tmp_path / "x.png")


def test_service_renders_in_background(qapp, tmp_path):
    lib = library.create_library(tmp_path / "lib")
    e = _entry_with_pdf(lib, tmp_path)
    svc = thumbnails.Thumbnails()
    svc.set_library(lib)
    ready = []
    svc.ready.connect(ready.append)
    try:
        assert svc.pixmap(e) is None                    # queued, not ready yet
        assert _wait(lambda: ready == ["k1"])
        pm = svc.pixmap(e)
        assert pm is not None and pm.width() == thumbnails.WIDTH
        png = svc.path(e)
        assert png is not None and png.parent == lib.cache_dir / "thumbs"
        assert svc.pixmap(Entry(key="nopdf")) is None   # no PDF, no request
    finally:
        svc.stop()


def test_cache_is_not_committed(tmp_path):
    lib = library.create_library(tmp_path / "lib")
    assert ".kherveref/cache/" in (lib.root / ".gitignore").read_text()


def test_window_views_and_cover(win, tmp_path):
    e = _entry_with_pdf(win.library, tmp_path)
    win._reload([e.key])
    assert _wait(lambda: not win._editor._cover.isHidden())   # cover in details
    win._set_view("covers")
    assert win._views.currentWidget() is win._covers
    assert win.act_view_covers.isChecked()
    assert win._covers.selectionModel() is win._table.selectionModel()
    win._set_view("list")
    ix = win._proxy.index(0, 4)
    tip = win._proxy.data(ix, 3)        # Qt.ToolTipRole
    assert "<img src='file:" in tip and "A paper" in tip


def test_icons_are_material_design_like_khervecad(qapp):
    assert not icons.icon("add_pdf").isNull()
    assert icons.icon_spec(icons.icon("add_pdf")) == ("add_pdf", None)
    assert all(g.startswith("mdi6.") for g in icons._GLYPHS.values())


def test_clicking_a_cover_works_like_the_list(win, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    for key in ("k1", "k2"):
        _entry_with_pdf(win.library, tmp_path, key)
    win._reload([])
    win.resize(1200, 800)
    win.show()
    win._set_view("covers", remember=False)
    QApplication.processEvents()
    cov = win._covers
    ix = cov.model().index(1, cov.modelColumn())
    rect = cov.visualRect(ix)
    QTest.mouseClick(cov.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QApplication.processEvents()
    key = win._entry_at(ix).key
    assert win.selected_keys() == [key]
    assert win._editor.entry is not None and win._editor.entry.key == key
    assert win.act_delete.isEnabled()

    # Ctrl/Cmd-click adds a second cover to the selection.
    other = cov.model().index(0, cov.modelColumn())
    QTest.mouseClick(cov.viewport(), Qt.LeftButton, Qt.ControlModifier,
                     cov.visualRect(other).center())
    assert len(win.selected_keys()) == 2

    # Right-click menu and Delete act on the cover under the mouse.
    monkeypatch.setattr(win, "_table_menu", lambda pos: None)
    win._context_menu(cov, cov.visualRect(ix).center())
    win._delete_selected()
    assert key not in win.entries


def test_drawn_cover_title_never_overlaps_type_label(qapp):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QFont, QFontMetrics

    from kherveref.covers import IMG_H, CARD_W, placeholder_layout
    img = QRect(0, 0, CARD_W, IMG_H)
    base = QFont()
    big = QFont(base)
    big.setPointSizeF(base.pointSizeF() * 1.05)
    big.setBold(True)
    long = ("A consistent and accurate ab initio parametrization of density "
            "functional dispersion correction (DFT-D) for the 94 elements H-Pu "
            "with many more words to make it far too long for any cover card")
    for label in ("JOURNAL ARTICLE", "CONFERENCE PAPER IN PROCEEDINGS"):
        lab, tit, font, text = placeholder_layout(img, base, label, big, long)
        assert tit.top() > lab.bottom()
        assert tit.bottom() <= img.bottom()
        used = QFontMetrics(font).boundingRect(
            QRect(0, 0, tit.width(), 10_000), Qt.AlignHCenter | Qt.TextWordWrap, text)
        assert used.height() <= tit.height()
    _, _, font, text = placeholder_layout(img, base, "BOOK", big, "The TeXbook")
    assert text == "The TeXbook" and font.pointSizeF() == big.pointSizeF()
