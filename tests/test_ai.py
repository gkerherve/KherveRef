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
        self.pulled = []

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
                if self.path == "/api/pull":
                    for d in ({"status": "pulling manifest"},
                              {"status": "pulling 1a2b", "total": 2_000_000_000,
                               "completed": 1_000_000_000},
                              {"status": "pulling 1a2b", "total": 2_000_000_000,
                               "completed": 2_000_000_000},
                              {"status": "success"}):
                        self.wfile.write(json.dumps(d).encode() + b"\n")
                    outer.pulled.append(body["model"])
                    return
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


def _paper(win, tmp_path, key, words):
    e = Entry(key=key, title=f"Paper about {words}", date="2020",
              authors=[Person("Demo", "A")])
    store.add_entry(win.library, e, win.entries)
    store.attach_file(win.library, e, make_pdf(tmp_path / f"{key}.pdf",
                                               [f"Results on {words}."] * 3))
    store.save_entry(win.library, e)
    return e


def _use(ollama):
    QSettings("kherve", "KherveRef").setValue("ai_url", ollama.url)
    QSettings("kherve", "KherveRef").setValue("ai_model", "")


def test_summary_and_questions_are_kept(win, ollama, tmp_path):
    from kherveref import ai_store, git_backend
    _use(ollama)
    _paper(win, tmp_path, "demo2020", "titania")
    _paper(win, tmp_path, "zz2001", "copper")
    win._reload(["demo2020"])
    panel = win._ai_panel
    win._ai_summarise()
    assert win._side.currentWidget() is panel
    assert _wait(lambda: panel._run is None)
    assert "A test summary" in panel._out.text
    panel._question.setText("What was studied?")
    panel.ask()
    assert _wait(lambda: panel._run is None)
    rec = ai_store.load(win.library, "demo2020")
    assert rec["summary"]["model"] == "qwen3.5:4b"
    assert rec["qa"][0]["question"] == "What was studied?"
    assert git_backend.history(win.library.root)[0][3] == "AI answer for demo2020"
    # Coming back to the paper shows its summary and every question.
    win._select_keys(["zz2001"])
    assert "A test summary" not in panel._out.text
    win._select_keys(["demo2020"])
    assert "## Summary" in panel._out.text and "Q: What was studied?" in panel._out.text
    # ... also after the window is reopened.
    from kherveref.mainwindow import MainWindow
    again = MainWindow()
    again.open_library(win.library.root)
    again._select_keys(["demo2020"])
    assert "Q: What was studied?" in again._ai_panel._out.text
    again.close()
    panel._save_to_notes()
    assert "A test summary" in store.load_entries(win.library)["demo2020"].notes


def test_several_papers_at_once(win, ollama, tmp_path):
    from kherveref import ai_store
    _use(ollama)
    for k, w in (("aa2020", "titania"), ("bb2021", "copper"), ("cc2022", "zinc")):
        _paper(win, tmp_path, k, w)
    win._reload(["aa2020", "bb2021", "cc2022"])
    panel = win._ai_panel
    assert panel._summarise.text() == "Summarise each (3)"
    panel.summarise()
    assert _wait(lambda: panel._run is None)
    for k in ("aa2020", "bb2021", "cc2022"):
        assert ai_store.load(win.library, k)["summary"]
        assert f"[{k}]" in panel._out.text
    panel._question.setText("Which material?")
    panel.ask()
    assert _wait(lambda: panel._run is None)
    assert "## Overview" in panel._out.text
    assert ai_store.load(win.library, "bb2021")["qa"][0]["question"] == "Which material?"
    assert "kref:aa2020" in panel._out.linkify(panel._out.text)


def test_ai_notes_follow_rename_and_delete(win, ollama, tmp_path):
    from kherveref import ai_store
    _paper(win, tmp_path, "demo2020", "titania")
    ai_store.save_summary(win.library, "demo2020", "S", "m")
    store.rename_keys(win.library, win.entries, {"demo2020": "Demo2020"})
    assert ai_store.load(win.library, "Demo2020")["summary"]["text"] == "S"
    store.delete_entry(win.library, win.entries["Demo2020"])
    assert not ai_store.has_any(win.library, "Demo2020")


def test_ai_tab_without_ollama(win, tmp_path):
    QSettings("kherve", "KherveRef").setValue("ai_url", "http://127.0.0.1:9")
    _paper(win, tmp_path, "x2020", "text")
    win._reload(["x2020"])
    win._ai_panel.summarise()
    assert _wait(lambda: win._ai_panel._run is None)
    assert "kref-setup:" in win._ai_panel._out.toHtml()


def test_ask_library_quick_thorough_and_history(win, ollama, lib_with_pdf):
    from kherveref import ai_store
    from kherveref.ai_panel import AskLibraryDialog
    _use(ollama)
    lib, entries = lib_with_pdf
    dlg = AskLibraryDialog(lib, [("All", list(entries.values()))])
    saved, revealed = [], []
    dlg.saved.connect(saved.append)
    dlg.reveal.connect(revealed.append)
    dlg._question.setText("titania photocatalysis")
    dlg.ask()
    assert _wait(lambda: dlg._run is None)
    assert "kref:demo2020" in dlg._out.linkify(dlg._out.text)
    dlg._thorough.setChecked(True)
    assert "Read every paper (2)" in dlg._thorough.text()
    dlg._question.setText("Which material?")
    dlg.ask()
    assert _wait(lambda: dlg._run is None)
    assert "## Overview" in dlg._out.text
    assert ai_store.load(lib, "other2019")["qa"][0]["question"] == "Which material?"
    hist = ai_store.history(lib)
    assert [h["mode"] for h in hist] == ["quick", "every paper"] and len(saved) == 2
    assert dlg._history.count() == 2
    dlg._history.setCurrentRow(1)           # the older, quick one
    assert "titania photocatalysis" in dlg._out.text
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


def test_pull_reports_progress(ollama):
    seen = []
    ai.Ollama(ollama.url).pull("gemma3:4b", lambda f, t: seen.append((f, t)))
    assert ollama.pulled == ["gemma3:4b"]
    assert seen[0][0] == -1 and seen[1] == (0.5, "pulling 1a2b — 1.0 of 2.0 GB")
    assert seen[-1][1] == "success"


def test_setup_dialog(qapp, ollama, monkeypatch):
    from kherveref import ai_panel
    QSettings("kherve", "KherveRef").setValue("ai_url", ollama.url)
    QSettings("kherve", "KherveRef").setValue("ai_model", "")
    monkeypatch.setattr(ai_panel, "memory_gb", lambda: 16.0)
    dlg = ai_panel.AISetupDialog()
    assert "is running" in dlg._status.text() and "qwen3.5:4b" in dlg._status.text()
    assert "16 GB" in dlg._step2.text()
    assert "qwen3.5:4b</a> ★" in "".join(
        dlg._table.cellWidget(r, 0).text() for r in range(dlg._table.rowCount()))
    big = next(r for r in range(dlg._table.rowCount())
               if "qwen3.5:27b" in dlg._table.cellWidget(r, 0).text())
    assert "too big here" in dlg._table.item(big, 2).text()
    names = [dlg._table.cellWidget(r, 0).text() for r in range(dlg._table.rowCount())]
    assert any("qwen3.5:4b" in n for n in names)
    buttons = {dlg._table.cellWidget(r, 0).text().split("'>")[1].split("<")[0]:
               dlg._table.cellWidget(r, 4).text() for r in range(dlg._table.rowCount())}
    assert buttons["qwen3.5:4b"] == "In use ✓"
    assert buttons["granite4:micro-h"] == "Use this"
    assert buttons["gemma3:4b"] == "Install"
    dlg._install("gemma3:4b")
    assert _wait(lambda: dlg._job is None)
    assert ollama.pulled == ["gemma3:4b"]
    assert QSettings("kherve", "KherveRef").value("ai_model") == "gemma3:4b"
    dlg._use("granite4:micro-h")
    assert QSettings("kherve", "KherveRef").value("ai_model") == "granite4:micro-h"


def test_setup_dialog_without_ollama(qapp, monkeypatch):
    from kherveref import ai_panel
    QSettings("kherve", "KherveRef").setValue("ai_url", "http://127.0.0.1:9")
    monkeypatch.setattr(ai_panel, "ollama_installed", lambda: False)
    monkeypatch.setattr(ai_panel, "memory_gb", lambda: 8.0)
    dlg = ai_panel.AISetupDialog()
    assert "not installed" in dlg._status.text()
    assert "granite4:micro-h</b> suits it best" in dlg._step2.text()
    install = dlg._table.cellWidget(0, 4)
    assert install.text() == "Install" and not install.isEnabled()


def test_memory_is_detected():
    from kherveref.ai_panel import memory_gb
    m = memory_gb()
    assert m is None or m > 1


def test_model_suggestions_and_names():
    from kherveref.ai_panel import RECOMMENDED, suggested
    assert suggested(8) == "granite4:micro-h" and suggested(16) == "qwen3.5:4b"
    assert suggested(32) == "qwen3.5:9b" and suggested(96) == "qwen3.5:27b"
    assert all(n in {r[0] for r in RECOMMENDED} for n in
               (suggested(8), suggested(16), suggested(32), suggested(96)))
    assert ai.same_model("phi4-mini", "phi4-mini:latest")
    assert not ai.same_model("qwen3.5:4b", "qwen3.5:9b")


def test_gpt_oss_reasons_low(qapp):
    f = FakeOllama(models=(("gpt-oss:20b", True), ("phi4-mini:latest", False)))
    try:
        c = ai.Ollama(f.url)
        "".join(c.chat("gpt-oss:20b", [{"role": "user", "content": "hi"}]))
        assert f.requests[-1]["think"] == "low"
        "".join(c.chat("phi4-mini", [{"role": "user", "content": "hi"}]))
        assert "think" not in f.requests[-1]
    finally:
        f.close()


def test_refresh_button_finds_newly_installed_model(qapp, monkeypatch):
    from kherveref import ai_panel
    models = [("qwen3.5:4b", True)]
    f = FakeOllama(models=models)
    try:
        QSettings("kherve", "KherveRef").setValue("ai_url", f.url)
        QSettings("kherve", "KherveRef").setValue("ai_model", "")
        monkeypatch.setattr(ai_panel, "memory_gb", lambda: 16.0)
        dlg = ai_panel.AISetupDialog()
        assert dlg._others.count() == 0
        models.append(("mistral-nemo:12b", False))        # installed from Terminal
        dlg._refresh.click()
        assert dlg._others.count() == 1 and dlg._others.itemData(0) == "mistral-nemo:12b"
    finally:
        f.close()
