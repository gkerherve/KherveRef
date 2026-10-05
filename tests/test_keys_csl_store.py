import json

from kherveref import csl, library, store
from kherveref.keys import base_key, is_valid_key, unique_key
from kherveref.model import Entry, Person


def test_keys():
    e = Entry(authors=[Person("Müller", "A")], date="2020",
              title="The deep structure of things")
    assert base_key(e) == "muller2020deep"
    assert unique_key(e, ["muller2020deep"]) == "muller2020deepb"
    assert unique_key(e, ["Muller2020Deep", "muller2020deepb"]) == "muller2020deepc"
    assert base_key(Entry(title="On")) == "anon"
    assert is_valid_key("smith2020") and not is_valid_key("a b") \
        and not is_valid_key("a/b")


CROSSREF = {
    "type": "journal-article", "title": "A <i>new</i> method &amp; more",
    "author": [{"given": "Anna", "family": "Berg",
                "non-dropping-particle": "van den"},
               {"name": "The Consortium"}],
    "container-title": "Nature", "issued": {"date-parts": [[2021, 3]]},
    "volume": "590", "issue": "7", "page": "100-105",
    "DOI": "10.1038/xyz", "publisher": "Springer", "ISSN": ["1476-4687"],
}


def test_from_csl():
    e = csl.from_csl(CROSSREF)
    assert e.type == "article"
    assert e.title == "A new method & more"
    assert [p.display() for p in e.authors] == ["van den Berg, Anna",
                                                "The Consortium"]
    assert (e.journal, e.date, e.pages, e.issn) == ("Nature", "2021-03",
                                                    "100–105", "1476-4687")


def test_csl_round_trip():
    e = csl.from_csl(CROSSREF)
    e.key = "berg2021new"
    back = csl.from_csl(csl.to_csl(e))
    assert (back.key, back.title, back.journal, back.date, back.doi) == \
        (e.key, e.title, e.journal, e.date, e.doi)


def test_chapter_goes_to_booktitle():
    e = csl.from_csl({"type": "chapter", "container-title": "Big Book",
                      "title": "Ch"})
    assert (e.type, e.booktitle, e.journal) == ("incollection", "Big Book", "")


def test_store_round_trip(tmp_path):
    lib = library.create_library(tmp_path / "lib")
    entries = {}
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    e = Entry(authors=[Person("Smith", "J")], date="2020", title="Deep things in the lab",
              doi="10.1/A")
    store.add_entry(lib, e, entries)
    assert e.key == "smith2020deep" and e.added
    att = store.attach_file(lib, e, pdf)
    store.save_entry(lib, e)
    assert att.path == "files/smith2020deep.pdf" and len(att.sha1) == 40
    assert store.file_path(lib, att).read_bytes() == b"%PDF-1.4 test"
    assert pdf.exists()

    again = store.load_entries(lib)
    assert again["smith2020deep"].files[0].sha1 == att.sha1

    twin = Entry(authors=[Person("Smith")], date="2020", title="Deep other")
    store.add_entry(lib, twin, entries)
    assert twin.key == "smith2020deepb"

    idx = store.DuplicateIndex.build(entries.values())
    assert idx.find(Entry(doi="https://doi.org/10.1/a")) == "smith2020deep"
    assert idx.find(Entry(), sha1=att.sha1) == "smith2020deep"
    assert idx.find(Entry(title="Deep things in the lab!", date="2020")) == "smith2020deep"
    assert idx.find(Entry(title="Something else entirely", date="2020")) is None

    store.delete_entry(lib, e)
    assert not (lib.entries_dir / "smith2020deep.json").exists()
    assert not store.file_path(lib, att).exists()


def test_imported_key_kept_when_free(tmp_path):
    lib = library.create_library(tmp_path / "lib")
    entries = {}
    store.add_entry(lib, Entry(key="MyKey2020", title="x"), entries)
    store.add_entry(lib, Entry(key="mykey2020", title="y long title", date="2001"),
                    entries)
    assert sorted(entries) == ["MyKey2020", "anon2001y"]


def test_collections_and_library_bib(tmp_path):
    lib = library.create_library(tmp_path / "lib")
    cols = [store.Collection("a", "Thesis"), store.Collection("b", "Ch1", "a"),
            store.Collection("c", "Other")]
    store.save_collections(lib, cols)
    assert store.load_collections(lib) == cols
    assert store.collection_and_descendants(cols, "a") == {"a", "b"}

    e = Entry(key="k1", title="Über", date="2020")
    p = store.write_library_bib(lib, [e])
    text = p.read_text()
    assert "@article{k1," in text and r'{\"{U}}ber' in text
    mtime = p.stat().st_mtime_ns
    store.write_library_bib(lib, [e])
    assert p.stat().st_mtime_ns == mtime
    json.loads((lib.root / "collections.json").read_text())


def test_doi_link_url_dropped():
    e = csl.from_csl({"type": "article-journal", "title": "T", "DOI": "10.1/X",
                      "URL": "http://dx.doi.org/10.1/x"})
    assert e.url == "" and e.doi == "10.1/X"
