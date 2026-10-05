from kherveref import library
from kherveref.mainwindow import MainWindow


def test_starts_on_start_page(qapp):
    win = MainWindow()
    assert win._stack.currentIndex() == 0
    assert not win.act_push.isEnabled()
    assert "no library" in win.windowTitle()


def test_open_library_shows_it_and_remembers(qapp, tmp_path):
    library.create_library(tmp_path, "Thesis refs")
    win = MainWindow()
    assert win.open_library(tmp_path)
    assert win._stack.currentIndex() == 1
    assert win.windowTitle().endswith("Thesis refs")
    assert win.act_push.isEnabled()
    assert "0 references" in win._status.text()

    again = MainWindow()
    again.reopen_last_library()
    assert again.library is not None and again.library.root == tmp_path

    again.close_library()
    third = MainWindow()
    third.reopen_last_library()
    assert third.library is None


def test_theme_switch_keeps_icons(qapp):
    win = MainWindow()
    win._set_theme("Dark")
    assert not win.act_open.icon().isNull()
