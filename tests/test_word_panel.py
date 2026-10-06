import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kherveref import word_addin, word_server
from kherveref.model import Entry, Person

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server():
    entries = {
        "smith2020": Entry(key="smith2020", type="article", title="XPS of TiO2",
                           authors=[Person("Smith", "J")], journal="Surf. Sci.",
                           date="2020", added="2026-01-02"),
        "doe2019": Entry(key="doe2019", type="book", title="A Book",
                         authors=[Person("Doe", "J")], publisher="Wiley",
                         date="2019", added="2026-01-01"),
    }
    srv = word_server.WordServer(lambda: ("Thesis", entries), port=0).start()
    yield f"http://127.0.0.1:{srv.port}/api"
    srv.stop()


def get(url, origin=None):
    req = urllib.request.Request(url, headers={"Origin": origin} if origin else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read()), dict(r.headers)


def test_status_and_search(server):
    s, headers = get(server + "/status", "https://gkerherve.github.io")
    assert (s["library"], s["count"]) == ("Thesis", 2)
    assert {st["id"] for st in s["styles"]} >= {"apa", "ieee"}
    assert headers["Access-Control-Allow-Origin"] == "https://gkerherve.github.io"
    r, _ = get(server + "/search?q=")
    assert [i["key"] for i in r["items"]] == ["smith2020", "doe2019"]
    r, _ = get(server + "/search?q=wiley")
    assert [i["key"] for i in r["items"]] == ["doe2019"]


def test_unknown_origin_gets_no_cors(server):
    _, headers = get(server + "/status", "https://evil.example")
    assert "Access-Control-Allow-Origin" not in headers


def test_private_network_preflight(server):
    req = urllib.request.Request(server + "/format", method="OPTIONS", headers={
        "Origin": "https://gkerherve.github.io",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Private-Network": "true"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Private-Network"] == "true"


def test_format(server):
    body = json.dumps({"clusters": [["smith2020"], ["doe2019", "smith2020"], ["x"]],
                       "style": "ieee"}).encode()
    req = urllib.request.Request(server + "/format", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        f = json.loads(r.read())
    assert f["citations"] == ["[1]", "[1], [2]", "?x"] and f["missing"] == ["x"]
    assert len(f["bibliography"]) == 2


def test_manifest_copies_match():
    assert (ROOT / "docs" / "word" / "manifest.xml").read_bytes() == \
        word_addin.MANIFEST.read_bytes()
    text = word_addin.MANIFEST.read_text()
    assert word_addin.ADDIN_ID in text
    assert "http://127.0.0.1:23120/word/taskpane.html" in text


def test_install_on_mac(tmp_path, monkeypatch):
    monkeypatch.setattr(word_addin.sys, "platform", "darwin")
    monkeypatch.setattr(word_addin, "mac_wef_dir", lambda: tmp_path / "wef")
    assert not word_addin.is_installed()
    res = word_addin.install()
    assert res.done and res.manifest.read_bytes() == word_addin.MANIFEST.read_bytes()
    assert word_addin.is_installed()
    word_addin.uninstall()
    assert not word_addin.is_installed()


def test_install_on_mac_when_word_folder_is_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(word_addin.sys, "platform", "darwin")
    wef = tmp_path / "Word" / "wef"
    monkeypatch.setattr(word_addin, "mac_wef_dir", lambda: wef)
    monkeypatch.setattr(word_addin, "staging_dir", lambda: tmp_path / "Docs")
    real_mkdir = Path.mkdir

    def guarded(self, *a, **k):
        if self == wef:
            raise PermissionError("Operation not permitted")
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", guarded)
    calls = []
    monkeypatch.setattr(word_addin.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd))
    res = word_addin.install()
    assert not res.done and res.folder == wef
    assert res.manifest == tmp_path / "Docs" / word_addin.FILENAME
    assert res.manifest.read_bytes() == word_addin.MANIFEST.read_bytes()
    assert calls[0][0] == "osascript" and ["open", str(wef)] in calls


def test_panel_page_served(server):
    with urllib.request.urlopen(server.replace("/api", "/word/taskpane.html"),
                                timeout=5) as r:
        assert r.status == 200 and b"KherveRef" in r.read()
    try:
        urllib.request.urlopen(server.replace("/api", "/word/../../KherveRef.spec"),
                               timeout=5)
        assert False, "path traversal must be refused"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_panel_errors_are_logged(server):
    word_server.log_path().unlink(missing_ok=True)
    req = urllib.request.Request(server + "/log", method="POST",
                                 data=json.dumps({"message": "update: GeneralException"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True}
    assert "update: GeneralException" in word_server.log_path().read_text()


def test_panel_adds_a_bibliography_once():
    js = (ROOT / "docs" / "word" / "taskpane.js").read_text()
    assert "AUTO_BIB_SETTING" in js and '"References"' in js
    assert "ensureBibliography(ctx, cites.length)" in js
