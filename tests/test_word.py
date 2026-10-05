import xml.etree.ElementTree as ET

from PySide6.QtWidgets import QApplication

from kherveref import cite, word_sources
from kherveref.model import Entry, Person

B = word_sources.B


def _entries():
    a = Entry(key="smith2020", type="article", title="XPS study of TiO2",
              authors=[Person("Smith", "Jane Anne"), Person("Müller", "Anna")],
              journal="Appl. Surf. Sci.", date="2020-05", volume="512",
              number="3", pages="145–150", doi="10.1/x")
    b = Entry(key="doe2019", type="book", title="A Book",
              authors=[Person(literal="World Health Organization")],
              editors=[Person("Ed", "Itor")], publisher="Wiley", date="2019",
              isbn="978-0-262-03561-3")
    return a, b


def test_styles_all_load_and_format():
    a, b = _entries()
    for sid in cite.STYLES:
        f = cite.format_document([["smith2020"], ["doe2019"]],
                                 {"smith2020": a, "doe2019": b}, sid)
        assert len(f.citations) == 2 and len(f.bibliography) == 2, sid
        assert all(f.bibliography_text), sid


def test_citation_and_reference_text():
    a, b = _entries()
    assert cite.format_citation([a]) == "(Smith & Müller, 2020)"
    # Numbered styles have no number outside a document: author–date.
    assert cite.format_citation([a], "ieee") == "(Smith & Müller, 2020)"
    f = cite.format_document([["smith2020"], ["smith2020", "nope"]],
                             {"smith2020": a}, "ieee")
    assert f.citations_text == ["[1]", "[1] ?nope"]
    assert f.bibliography_text[0].startswith("[1] J. A. Smith")
    ref = cite.format_reference([a], "apa")[0]
    assert "<i>Appl. Surf. Sci.</i>" in ref
    rtf = cite.html_to_rtf(ref)
    assert r"{\i Appl. Surf. Sci.}" in rtf and r"M\u252?ller" in rtf


def test_word_source_xml():
    a, b = _entries()
    src = word_sources.source_element(a, "{GUID}")
    get = lambda tag: src.findtext(B + tag)  # noqa: E731
    assert (get("Tag"), get("SourceType"), get("JournalName"), get("Year"),
            get("Month"), get("Issue"), get("DOI")) == (
        "smith2020", "JournalArticle", "Appl. Surf. Sci.", "2020", "May", "3", "10.1/x")
    people = src.findall(f"{B}Author/{B}Author/{B}NameList/{B}Person")
    assert [(p.findtext(B + "Last"), p.findtext(B + "First"),
             p.findtext(B + "Middle")) for p in people] == [
        ("Smith", "Jane", "Anne"), ("Müller", "Anna", None)]
    book = word_sources.source_element(b, "{G2}")
    assert book.findtext(f"{B}Author/{B}Author/{B}Corporate") == "World Health Organization"
    assert book.findtext(f"{B}Author/{B}Editor/{B}NameList/{B}Person/{B}Last") == "Ed"
    assert book.findtext(B + "StandardNumber") == "978-0-262-03561-3"


def test_sync_keeps_user_sources_and_removes_stale(tmp_path):
    a, b = _entries()
    path = tmp_path / "Sources.xml"
    path.write_text(
        '<?xml version="1.0"?><b:Sources xmlns:b="' + word_sources.NS + '" '
        'SelectedStyle="\\APA.XSL"><b:Source><b:Tag>mine</b:Tag>'
        '<b:SourceType>Book</b:SourceType><b:Guid>{USER}</b:Guid>'
        '<b:Title>My own</b:Title></b:Source>'
        '<b:Source><b:Tag>doe2019</b:Tag><b:Guid>{USER2}</b:Guid></b:Source>'
        '</b:Sources>', encoding="utf-8")
    lib = tmp_path / "lib"
    res = word_sources.sync(lib, [a, b], path)
    assert (res["written"], res["skipped"]) == (1, 1)    # user's doe2019 wins
    tags = [s.findtext(B + "Tag") for s in ET.parse(path).getroot()]
    assert tags == ["mine", "doe2019", "smith2020"]
    assert ET.parse(path).getroot().get("SelectedStyle") == "\\APA.XSL"

    res = word_sources.sync(lib, [], path)              # smith2020 deleted
    assert res["removed"] == 1
    tags = [s.findtext(B + "Tag") for s in ET.parse(path).getroot()]
    assert tags == ["mine", "doe2019"]


def test_drag_carries_formatted_citation(win):
    from helpers import FakeNet  # noqa: F401  (win patches the network)
    win._import_text("10.1016/j.apsusc.2020.145000")
    while win._job is not None:
        QApplication.processEvents()
    md = win._model.mimeData([win._model.index(0, 1)])
    assert md.text() == "\\cite{smith2020surface}"
    assert "(Smith &amp; Doe, 2020)" in md.html() or "(Smith & Doe, 2020)" in md.html()
    assert b"Smith" in bytes(md.data("text/rtf"))


def test_word_sync_from_window(win, tmp_path, monkeypatch):
    target = tmp_path / "Sources.xml"
    monkeypatch.setattr(word_sources, "default_path", lambda: target)
    monkeypatch.setattr(word_sources, "word_running", lambda: False)
    win.act_word_sync.setChecked(True)
    win._import_text("10.1016/j.apsusc.2020.145000")
    while win._job is not None:
        QApplication.processEvents()
    tags = [s.findtext(B + "Tag") for s in ET.parse(target).getroot()]
    assert tags == ["smith2020surface"]
