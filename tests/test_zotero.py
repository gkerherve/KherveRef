import sqlite3

import pytest

from kherveref import importer, library, store, zotero
from helpers import make_pdf

SCHEMA = """
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, key TEXT);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT,
                       lastName TEXT, fieldMode INT);
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT,
                           orderIndex INT);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE itemTags (itemID INT, tagID INT);
CREATE TABLE itemNotes (itemID INT, parentItemID INT, note TEXT, title TEXT);
CREATE TABLE itemAttachments (itemID INT, parentItemID INT, linkMode INT,
                              contentType TEXT, path TEXT);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT,
                          parentCollectionID INT, key TEXT);
CREATE TABLE collectionItems (collectionID INT, itemID INT);
CREATE TABLE deletedItems (itemID INT);
"""


@pytest.fixture
def zdir(tmp_path):
    d = tmp_path / "Zotero"
    (d / "storage" / "ATT1").mkdir(parents=True)
    make_pdf(d / "storage" / "ATT1" / "Smith 2020.pdf", ["zotero pdf"])
    con = sqlite3.connect(d / "zotero.sqlite")
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO itemTypes VALUES (?,?)", [
        (1, "journalArticle"), (2, "attachment"), (3, "note"), (4, "thesis"),
        (5, "book")])
    con.executemany("INSERT INTO items VALUES (?,?,?)", [
        (10, 1, "ITEM1"), (11, 2, "ATT1"), (12, 3, "NOTE1"), (13, 4, "ITEM2"),
        (14, 5, "GONE")])
    fields = {"title": 1, "publicationTitle": 2, "date": 3, "DOI": 4,
              "extra": 5, "university": 6, "thesisType": 7, "volume": 8}
    con.executemany("INSERT INTO fields VALUES (?,?)",
                    [(v, k) for k, v in fields.items()])
    values = [(10, "title", "Titania surfaces"), (10, "publicationTitle", "Surf. Sci."),
              (10, "date", "2020-05-00 May 2020"), (10, "DOI", "10.1/zz"),
              (10, "extra", "Citation Key: smithTitania2020\narXiv: 2001.00001"),
              (10, "volume", "7"),
              (13, "title", "My thesis"), (13, "date", "2019-00-00 2019"),
              (13, "university", "Imperial College London"),
              (13, "thesisType", "PhD Thesis"), (14, "title", "Deleted book")]
    for i, (iid, f, v) in enumerate(values, 1):
        con.execute("INSERT INTO itemDataValues VALUES (?,?)", (i, v))
        con.execute("INSERT INTO itemData VALUES (?,?,?)", (iid, fields[f], i))
    con.executemany("INSERT INTO creators VALUES (?,?,?,?)", [
        (1, "Jane", "Smith", 0), (2, "", "The Consortium", 1), (3, "Ed", "Itor", 0),
        (4, "Gwilherm", "Kerherve", 0)])
    con.executemany("INSERT INTO creatorTypes VALUES (?,?)",
                    [(1, "author"), (2, "editor")])
    con.executemany("INSERT INTO itemCreators VALUES (?,?,?,?)", [
        (10, 1, 1, 0), (10, 2, 1, 1), (10, 3, 2, 2), (13, 4, 1, 0)])
    con.executemany("INSERT INTO tags VALUES (?,?)", [(1, "xps"), (2, "tio2")])
    con.executemany("INSERT INTO itemTags VALUES (?,?)", [(10, 1), (10, 2)])
    con.execute("INSERT INTO itemNotes VALUES (12, 10, '<p>Read &amp; check</p>', '')")
    con.execute("INSERT INTO itemAttachments VALUES "
                "(11, 10, 0, 'application/pdf', 'storage:Smith 2020.pdf')")
    con.executemany("INSERT INTO collections VALUES (?,?,?,?)", [
        (2, "Chapter 1", 1, "COLB"), (1, "Thesis", None, "COLA")])
    con.executemany("INSERT INTO collectionItems VALUES (?,?)", [(2, 10), (1, 13)])
    con.execute("INSERT INTO deletedItems VALUES (14)")
    con.commit()
    con.close()
    return d


def test_read_library(zdir):
    zl = zotero.read_library(zdir)
    assert len(zl.items) == 2
    paper = next(i for i in zl.items if i.entry.title == "Titania surfaces")
    e = paper.entry
    assert (e.type, e.key, e.journal, e.date, e.doi, e.volume) == (
        "article", "smithTitania2020", "Surf. Sci.", "2020-05", "10.1/zz", "7")
    assert [p.display() for p in e.authors] == ["Smith, Jane", "The Consortium"]
    assert [p.display() for p in e.editors] == ["Itor, Ed"]
    assert e.keywords == ["tio2", "xps"] and e.notes == "Read & check"
    assert (e.eprint, e.eprinttype) == ("2001.00001", "arxiv")
    assert paper.pdfs[0].name == "Smith 2020.pdf"
    thesis = next(i for i in zl.items if i.entry.type == "thesis").entry
    assert (thesis.thesis_type, thesis.institution, thesis.date) == (
        "phd", "Imperial College London", "2019")


def test_import_into_library(zdir, tmp_path):
    lib = library.create_library(tmp_path / "lib")
    s = importer.Importer(lib, online=False).import_zotero(zdir)
    assert s.count(importer.ADDED) == 2
    entries = store.load_entries(lib)
    paper = entries["smithTitania2020"]
    assert paper.files and (lib.root / paper.files[0].path).exists()
    cols = {c.name: c for c in store.load_collections(lib)}
    assert cols["Chapter 1"].parent == cols["Thesis"].id
    assert paper.collections == [cols["Chapter 1"].id]

    again = importer.Importer(lib, online=False).import_zotero(zdir)
    assert again.count(importer.DUPLICATE) == 2 and again.count(importer.ADDED) == 0
    assert len(store.load_collections(lib)) == 2


def test_database_is_never_written(zdir, tmp_path):
    before = (zdir / "zotero.sqlite").read_bytes()
    importer.Importer(library.create_library(tmp_path / "l"),
                      online=False).import_zotero(zdir)
    assert (zdir / "zotero.sqlite").read_bytes() == before
