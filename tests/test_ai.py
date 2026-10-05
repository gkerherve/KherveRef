import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QToolBar

from kherveref import ai, fulltext, library, mcp_server, store
from kherveref.model import Entry, Person
from helpers import make_pdf


class FakeOllama:
    """Ollama's /api/version, /api/tags and streaming /api/chat."""

    def __init__(self, models=(("qwen3.5:4b", True), ("granite4:micro-h", False))):
        outer = self
        self.requests = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, d):
                b = json.dumps(d).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json({"version": "0.35.1"})
                return self._json({"models": [
                    {"name": n, "details": {"parameter_size": "4B"},
                     "capabilities": ["completion"] + (["thinking"] if t else [])}
                    for n, t in models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                self.send_response(200)
                self.end_headers()
                for piece in ("**In one sentence:** ", "A test ", "summary [demo2020, p. 1]."):
                    self.wfile.write(json.dumps({"message": {"content": piece},
                                                 "done": False}).encode() + b"\n")
                    self.wfile.flush()
                self.wfile.write(json.dumps({"done": True}).encode() + b"\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def ollama():
    f = FakeOllama()
    yield f
    f.close()


@pytest.fixture
def lib_with_pdf(tmp_path):
    lib = library.create_library(tmp_path / "papers")
    entries = {}
    for key, words in (("demo2020", "titania photocatalysis oxygen vacancies"),
                       ("other2019", "copper oxide thin films sputtering")):
        e = Entry(key=key, title=f"Paper about {words}", date="2020",
                  authors=[Person("Demo", "A")])
        store.add_entry(lib, e, entries)
        store.attach_file(lib, e, make_pdf(tmp_path / f"{key}.pdf",
                                           [f"Results on {words}."] * 3,
                                           big=f"Paper about {words}"))
        store.save_entry(lib, e)
    return lib, entries


def test_fulltext_pages_cache_and_search(lib_with_pdf):
    lib, entries = lib_with_pdf
    e = entries["demo2020"]
    pages = fulltext.pages(lib, e)
    assert len(pages) == 1 and "photocatalysis" in pages[0]
    assert list((lib.cache_dir / "text").glob("*.json"))
    assert fulltext.text(lib, e).startswith("[page 1]")
    hits = fulltext.search("oxygen vacancies in titania",
                           fulltext.passages(lib, entries.values()))
    assert hits[0].key == "demo2020" and hits[0].page == 1
    assert fulltext.pages(lib, Entry(key="nopdf")) == []


def test_ollama_client(ollama):
    c = ai.Ollama(ollama.url)
    assert c.version() == "0.35.1"
    assert c.default_model() == "qwen3.5:4b"
    out = "".join(c.chat("qwen3.5:4b", [{"role": "user", "content": "hi"}]))
    assert out.startswith("**In one sentence:**")
    assert ollama.requests[-1]["think"] is False        # no long reasoning
    "".join(c.chat("granite4:micro-h", [{"role": "user", "content": "hi"}]))
    assert "think" not in ollama.requests[-1]


def test_unreachable():
    with pytest.raises(ai.AIError, match="not reachable"):
        ai.Ollama("http://127.0.0.1:9").models()


def test_prompts(lib_with_pdf):
    lib, entries = lib_with_pdf
    msgs = ai.summary_messages(lib, entries["demo2020"])
    assert "[page 1]" in msgs[1]["content"] and "### Methods" in msgs[1]["content"]
    q = ai.question_messages(lib, entries["demo2020"], "What was studied?")
    assert "Question: What was studied?" in q[1]["content"]
    m, hits = ai.library_messages(lib, list(entries.values()), "copper sputtering")
    assert hits[0].key == "other2019" and "[other2019, p. 1]" in m[1]["content"]
    with pytest.raises(ai.AIError):
        ai.summary_messages(lib, Entry(key="x", title="No PDF"))
    with pytest.raises(ai.AIError):
        ai.library_messages(lib, list(entries.values()), "zzzz qqqq")


def _wait(cond, seconds=15):
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return cond()


def test_window_ai_tab_summarise_and_save(win, ollama, tmp_path):
    QSettings("kherve", "KherveRef").setValue("ai_url", ollama.url)
    QSettings("kherve", "KherveRef").setValue("ai_model", "")
    e = Entry(key="demo2020", title="Paper about titania", date="2020")
    store.add_entry(win.library, e, win.entries)
    store.attach_file(win.library, e, make_pdf(tmp_path / "d.pdf", ["titania"] * 3))
    store.save_entry(win.library, e)
    win._reload(["demo2020"])
    win._ai_summarise()
    panel = win._ai_panel
    assert win._side.currentWidget() is panel
    assert _wait(lambda: panel._job is None and "A test summary" in panel._out.text)
    assert panel._notes.isEnabled()
    panel._save_to_notes()
    assert "A test summary" in store.load_entries(win.library)["demo2020"].notes
    # Each answer is remembered while you look at other references.
    other = Entry(key="zz2001", title="Another", date="2001")
    store.add_entry(win.library, other, win.entries)
    win._reload(["zz2001"])
    assert "A test summary" not in panel._out.text
    win._select_keys(["demo2020"])
    assert "A test summary" in panel._out.text


def test_ai_tab_without_ollama(win, tmp_path):
    QSettings("kherve", "KherveRef").setValue("ai_url", "http://127.0.0.1:9")
    e = Entry(key="x2020", title="X", date="2020")
    store.add_entry(win.library, e, win.entries)
    store.attach_file(win.library, e, make_pdf(tmp_path / "x.pdf", ["text"] * 3))
    store.save_entry(win.library, e)
    win._reload(["x2020"])
    win._ai_panel.summarise()
    assert _wait(lambda: win._ai_panel._job is None)
    assert "ollama.com" in win._ai_panel._out.toHtml()


def test_ask_library_dialog_links(win, ollama, lib_with_pdf):
    from kherveref.ai_panel import AskLibraryDialog
    QSettings("kherve", "KherveRef").setValue("ai_url", ollama.url)
    lib, entries = lib_with_pdf
    dlg = AskLibraryDialog(lib, [("All", list(entries.values()))])
    revealed = []
    dlg.reveal.connect(revealed.append)
    dlg._question.setText("titania photocatalysis")
    dlg.ask()
    assert _wait(lambda: dlg._job is None and dlg._out.text)
    assert "kref:demo2020" in dlg._out.linkify(dlg._out.text)
    from PySide6.QtCore import QUrl
    dlg._link(QUrl("kref:demo2020"))
    assert revealed == ["demo2020"]


def test_mcp_fulltext_tools(lib_with_pdf):
    lib, _entries = lib_with_pdf
    tools = mcp_server.Tools(str(lib.root))
    r = tools.read_paper("demo2020")
    assert r["pages"] == 1 and r["text"].startswith("[page 1]")
    s = tools.search_fulltext("copper sputtering")
    assert s["passages"][0]["key"] == "other2019"


def test_toolbar_is_icon_only_like_khervecad(win):
    from PySide6.QtCore import Qt
    tb = win.findChild(QToolBar, "main_toolbar")
    assert tb.toolButtonStyle() == Qt.ToolButtonIconOnly
    assert tb.iconSize().width() == 28
    assert "Add PDFs" in win.act_add_pdfs.toolTip()
