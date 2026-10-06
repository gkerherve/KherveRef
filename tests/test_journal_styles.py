"""Bundled citation styles and journal styles added on demand."""
import json
from pathlib import Path

import pytest

from kherveref import cite, journal_styles
from kherveref.model import Entry, Person

STYLES = Path(cite.__file__).parent / "styles"
ENTRIES = {
    "smith2020": Entry(key="smith2020", type="article", title="XPS of TiO2",
                       authors=[Person("Smith", "J"), Person("Doe", "J")],
                       journal="Applied Surface Science", volume="512",
                       pages="145-150", date="2020"),
    "lee2019": Entry(key="lee2019", type="book", title="Surface Analysis",
                     authors=[Person("Lee", "K")], publisher="Wiley", date="2019"),
}


@pytest.mark.parametrize("sid", list(cite.STYLES))
def test_every_bundled_style_formats(sid):
    assert (STYLES / f"{sid}.csl").exists()
    f = cite.format_document([["smith2020"], ["lee2019", "smith2020"]], ENTRIES, sid)
    assert all(f.citations) and len(f.bibliography) == 2
    assert "smith" in (f.bibliography_text[0] + f.bibliography_text[1]).lower()


def test_groups_cover_every_style_once():
    ids = [sid for g in cite.GROUPS.values() for sid in g]
    assert len(ids) == len(set(ids)) == len(cite.STYLES) >= 35
    assert cite.is_numeric("nature") and not cite.is_numeric("apa")


INDEX = [
    {"title": "Applied Surface Science", "name": "applied-surface-science", "dependent": 1,
     "categories": {"format": "numeric", "fields": ["chemistry"]}},
    {"title": "Surface Science", "name": "surface-science", "dependent": 1,
     "categories": {"format": "numeric", "fields": ["physics"]}},
    {"title": "Some History Journal", "name": "some-history", "dependent": 0,
     "categories": {"format": "note", "fields": ["history"]}},
]
DEPENDENT = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" default-locale="en-US">
  <info><title>Applied Surface Science</title>
    <link href="http://www.zotero.org/styles/elsevier-with-titles" rel="independent-parent"/>
    <category citation-format="numeric"/></info></style>"""


@pytest.fixture
def repo(monkeypatch):
    """The style repository, served from memory."""
    served = {
        journal_styles.INDEX_URL: json.dumps(INDEX).encode(),
        journal_styles.STYLE_URL.format("applied-surface-science"): DEPENDENT.encode(),
        journal_styles.STYLE_URL.format("elsevier-with-titles"):
            (STYLES / "elsevier-with-titles.csl").read_bytes(),
        journal_styles.STYLE_URL.format("some-history"):
            b'<style><info><title>Some History Journal</title>'
            b'<category citation-format="note"/></info></style>',
        journal_styles.STYLE_URL.format("broken"):
            b'<?xml version="1.0"?><style xmlns="http://purl.org/net/xbiblio/csl">'
            b'<info><title>Broken</title><category citation-format="numeric"/></info>'
            b'<citation><layout><text variable="title"/></layout></citation></style>',
    }
    calls = []

    def fetch(url, timeout=30):
        calls.append(url)
        if url not in served:
            raise journal_styles.StyleError("not found")
        return served[url]
    monkeypatch.setattr(journal_styles, "_fetch", fetch)
    for f in ("installed.json", "index.json"):
        (journal_styles.user_dir() / f).unlink(missing_ok=True)
    return calls


def test_search_finds_journals_and_skips_footnote_styles(repo):
    index = journal_styles.load_index()
    assert [i["name"] for i in journal_styles.search("surface science", index)] == [
        "surface-science", "applied-surface-science"]
    assert journal_styles.search("history", index) == []
    assert journal_styles.describe(index[0]) == "Applied Surface Science (numbered)"


def test_adding_a_journal_brings_its_house_style(repo):
    rec = journal_styles.install("applied-surface-science")
    assert rec == {"title": "Applied Surface Science (numbered)", "format": "numeric",
                   "file": "elsevier-with-titles.csl", "parent": "elsevier-with-titles"}
    assert "applied-surface-science" in cite.all_styles()
    assert cite.is_numeric("applied-surface-science")
    assert ("Your journals", "applied-surface-science",
            "Applied Surface Science (numbered)") in cite.grouped_styles()
    f = cite.format_document([["smith2020"]], ENTRIES, "applied-surface-science")
    assert f.citations_text == ["[1]"] and "XPS of TiO2" in f.bibliography_text[0]
    # Numbered styles have no number outside a document: author–date instead.
    assert cite.format_citation([ENTRIES["smith2020"]], "applied-surface-science") \
        == "(Smith & Doe, 2020)"
    journal_styles.remove("applied-surface-science")
    assert "applied-surface-science" not in cite.all_styles()
    assert not (journal_styles.user_dir() / "elsevier-with-titles.csl").exists()


def test_footnote_and_unusable_styles_are_refused(repo):
    with pytest.raises(journal_styles.StyleError, match="footnotes"):
        journal_styles.install("some-history")
    with pytest.raises(journal_styles.StyleError, match="can't"):
        journal_styles.install("broken")
    assert journal_styles.installed() == {}
    assert not (journal_styles.user_dir() / "broken.csl").exists()
    with pytest.raises(journal_styles.StyleError):
        journal_styles.install("../etc/passwd")


def test_unknown_style_falls_back_to_apa():
    f = cite.format_document([["smith2020"]], ENTRIES, "no-such-style")
    assert f.citations_text == ["(Smith & Doe, 2020)"]


def test_style_menu_lists_groups_and_added_journals(win, repo):
    journal_styles.install("applied-surface-science")
    win._rebuild_style_menu()
    titles = [a.text() for a in win._style_menu.actions()]
    assert "Applied Surface Science (numbered)" in titles
    assert any(t.endswith("Chemistry & materials") for t in titles)
    assert titles[-1].startswith("&Find a journal style")
    win._set_style("applied-surface-science")
    assert win.citation_style() == "applied-surface-science"
