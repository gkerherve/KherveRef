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


def test_icons_are_phosphor_with_accent(qapp):
    icons.set_accent_color("#ff0000")
    assert not icons.icon("add_pdf").isNull()
    assert icons.icon_spec(icons.icon("add_pdf")) == ("add_pdf", None)
    assert "add_pdf" in icons.ACCENTED and icons._PHOSPHOR["add_pdf"].startswith("ph.")
