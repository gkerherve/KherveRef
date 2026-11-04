import io
import json
import os
import socket
import sys
import threading

import pytest

from kherveref import fetch, git_backend, library, mcp_server, state
from helpers import FakeNet, make_pdf


@pytest.fixture
def lib(tmp_path, monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "T"), ("GIT_COMMITTER_NAME", "T"),
                 ("GIT_AUTHOR_EMAIL", "t@x"), ("GIT_COMMITTER_EMAIL", "t@x")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(fetch, "http_get", FakeNet())
    lb = library.create_library(tmp_path / "lib", "Thesis")
    state.remember_library(lb.root)
    return lb


def rpc(messages, library_path=None):
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    stdout = io.BytesIO()
    mcp_server.serve(library_path, stdin, stdout)
    return [json.loads(l) for l in stdout.getvalue().splitlines()]


def call(name, args=None, library_path=None):
    [reply] = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": args or {}}}],
                  library_path)
    res = reply["result"]
    return json.loads(res["content"][0]["text"]), res["isError"]


def test_handshake_and_tools_list(lib):
    replies = rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "bogus"},
    ])
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "kherveref"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"search_references", "add_reference", "export_bibtex"} <= names
    assert replies[2]["error"]["code"] == -32601


def test_add_search_get_update_export(lib):
    out, err = call("add_reference", {"identifier": "10.1016/j.apsusc.2020.145000",
                                      "collection": "Chapter 2"})
    assert not err and out["results"][0]["key"] == "smith2020surface"
    out, _ = call("search_references", {"query": "titania"})
    assert out["total"] == 1 and out["references"][0]["key"] == "smith2020surface"
    out, _ = call("search_references", {"collection": "chapter 2"})
    assert out["total"] == 1
    out, _ = call("get_reference", {"key": "smith2020surface"})
    assert "journaltitle" in out["biblatex"]
    out, err = call("update_reference", {"key": "smith2020surface",
                                         "fields": {"notes": "important",
                                                    "keywords": "xps, tio2"}})
    assert not err and out["changed"] == ["notes", "keywords"]
    out, _ = call("export_bibtex", {"keys": ["smith2020surface", "nope"],
                                    "dialect": "bibtex"})
    assert out["missing"] == ["nope"] and "year" in out["bibtex"]
    subjects = [h[3] for h in git_backend.history(lib.root)]
    assert subjects[0] == "Edit smith2020surface via Claude"
    assert "smith2020surface" in (lib.root / "library.bib").read_text()


def test_add_pdf_and_collections(lib, tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", ["arXiv:2101.00001"])
    out, err = call("add_pdf", {"path": str(pdf)})
    assert not err and out["results"][0]["key"] == "martin2021preprint"
    out, _ = call("add_to_collection", {"keys": ["martin2021preprint", "zz"],
                                        "collection": "Reading"})
    assert out == {"added": ["martin2021preprint"], "missing": ["zz"]}
    out, _ = call("list_collections")
    assert out["collections"] == [{"name": "Reading", "parent": "", "references": 1}]


def test_errors_are_tool_errors(lib):
    out, err = call("get_reference", {"key": "missing"})
    assert err and "missing" in out["error"]
    out, err = call("update_reference", {"key": "x", "fields": {}, "bogus": 1})
    assert err


def test_explicit_library_and_none(tmp_path):
    out, err = call("search_references", {}, library_path=str(tmp_path / "none"))
    assert err and "No KherveRef library" in out["error"]


def test_tools_are_described_for_claude(lib):
    [_, reply] = rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    for t in reply["result"]["tools"]:
        assert t["title"] and len(t["description"]) > 40
        assert "readOnlyHint" in t["annotations"]
        for name, prop in t["inputSchema"]["properties"].items():
            assert prop.get("description"), (t["name"], name)


def test_filters_styles_and_collections(lib):
    call("add_reference", {"identifier": "10.1016/j.apsusc.2020.145000",
                           "collection": "Chapter 2"})
    out, _ = call("search_references", {"has_pdf": True})
    assert out["total"] == 0
    out, _ = call("search_references", {"has_pdf": False})
    assert out["total"] == 1
    out, _ = call("list_citation_styles")
    assert any(s["id"] == "apa" for s in out["styles"])
    out, err = call("format_citation", {"keys": ["smith2020surface"],
                                        "style": "apa"})
    assert not err and "2020" in out["citation"] and out["references"]
    out, _ = call("remove_from_collection", {"keys": ["smith2020surface"],
                                             "collection": "Chapter 2"})
    assert out["removed"] == ["smith2020surface"]
    out, _ = call("search_references", {"collection": "chapter 2"})
    assert out["total"] == 0


def test_helpful_errors(lib):
    call("add_reference", {"identifier": "10.1016/j.apsusc.2020.145000"})
    out, err = call("get_reference", {"key": "smith2020surfce"})
    assert err and "smith2020surface" in out["error"]
    out, err = call("search_references", {"qurey": "x"})
    assert err and "query" in out["error"]
    out, err = call("update_reference", {"key": "smith2020surface",
                                         "fields": {"jornal": "x"}})
    assert err and "journal" in out["error"]


def test_find_duplicates_tool(lib):
    call("add_reference", {"identifier": "10.1016/j.apsusc.2020.145000"})
    out, err = call("find_duplicates")
    assert not err and out == {"groups": 0, "duplicates": []}


def test_status_without_window(lib):
    out, err = call("get_status")
    assert not err and out["library"]["name"] == "Thesis"
    assert out["window"]["open"] is False
    out, err = call("show_in_window", {"keys": ["x"]})
    assert err


def test_view_page_is_an_image(lib, tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", ["arXiv:2101.00001"])
    call("add_pdf", {"path": str(pdf)})
    [reply] = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "view_page",
                               "arguments": {"key": "martin2021preprint"}}}])
    image, text = reply["result"]["content"]
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    import base64
    assert base64.b64decode(image["data"])[:4] == b"\x89PNG"
    assert json.loads(text["text"])["page"] == 1
    out, err = call("view_page", {"key": "martin2021preprint", "page": 99})
    assert err and "pages 1-" in out["error"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Unix socket")
def test_tools_reach_the_window(lib, tmp_path):
    asked = []

    def window(conn):
        with conn:
            req = json.loads(conn.makefile("rb").readline())
            asked.append(req)
            answer = ({"library": str(lib.root), "selected": ["k1"],
                       "collection": ""} if req["what"] == "status"
                      else {"shown": req["keys"]})
            conn.sendall(json.dumps(answer).encode() + b"\n")

    path = f"/tmp/kref-test-{os.getpid()}.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen()

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            window(conn)
    threading.Thread(target=loop, daemon=True).start()
    state.set_window_socket(path)
    try:
        call("add_reference", {"identifier": "10.1016/j.apsusc.2020.145000"})
        out, err = call("get_status")
        assert not err and out["window"]["open"]
        assert out["window"]["showing_this_library"]
        out, err = call("show_in_window", {"keys": ["smith2020surface"]})
        assert not err and out["shown"] == ["smith2020surface"]
        assert asked[-1]["cmd"] == "query" and asked[-1]["library"] == str(lib.root)
    finally:
        state.set_window_socket(None)
        srv.close()
        os.unlink(path)
