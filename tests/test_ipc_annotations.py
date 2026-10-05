import pymupdf
from PySide6.QtWidgets import QApplication

from kherveref import ipc
from kherveref.pdf_meta import annotations_as_notes, extract_annotations


def test_parse_args():
    assert ipc.parse_args(["--add", "a.pdf", "b.pdf"]) == {
        "cmd": "add", "paths": ["a.pdf", "b.pdf"]}
    assert ipc.parse_args(["--reveal", "a.pdf"]) == {"cmd": "reveal",
                                                     "paths": ["a.pdf"]}
    assert ipc.parse_args(["/lib"]) == {"cmd": "open", "paths": ["/lib"]}
    assert ipc.parse_args([]) == {}
    assert ipc.parse_args(["--add"]) == {}


def test_request_reaches_running_instance(qapp, monkeypatch):
    monkeypatch.setattr(ipc, "server_name", lambda: "kherveref-test-ipc")
    server = ipc.Server(qapp)
    got = []
    server.request.connect(got.append)
    try:
        assert ipc.send_to_running({"cmd": "add", "paths": ["x.pdf"]})
        for _ in range(200):
            QApplication.processEvents()
            if got:
                break
        assert got == [{"cmd": "add", "paths": ["x.pdf"]}]
    finally:
        server.request.disconnect()
        server.close()
        for _ in range(20):
            QApplication.processEvents()


def test_no_running_instance(qapp, monkeypatch):
    monkeypatch.setattr(ipc, "server_name", lambda: "kherveref-test-nobody")
    assert not ipc.send_to_running({"cmd": "raise"}, timeout_ms=100)


def test_annotations(tmp_path):
    path = tmp_path / "a.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Titania is photocatalytic", fontsize=12)
    rect = page.search_for("photocatalytic")[0]
    hl = page.add_highlight_annot(rect)
    hl.set_info(content="key claim")
    hl.update()
    page.add_text_annot((72, 200), "check the band gap")
    doc.save(str(path))
    doc.close()
    ann = extract_annotations(path)
    assert ann == [(1, "highlight", "photocatalytic", "key claim"),
                   (1, "note", "", "check the band gap")]
    notes = annotations_as_notes(path)
    assert "p. 1 “photocatalytic” — key claim" in notes
    assert "p. 1 check the band gap" in notes
