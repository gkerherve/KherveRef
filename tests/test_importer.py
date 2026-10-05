import pytest

from kherveref import fetch, importer, library, store
from helpers import FakeNet, make_pdf


@pytest.fixture
def lib(tmp_path):
    return library.create_library(tmp_path / "lib")


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(fetch, "http_get", fake)
    return fake


def test_pdf_with_doi_becomes_full_reference(lib, net, tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", [
        "Abstract. We did things.",
        "https://doi.org/10.1016/j.apsusc.2020.145000"])
    imp = importer.Importer(lib)
    o = imp.import_pdf(pdf)
    assert o.status == importer.ADDED and o.key == "smith2020surface"
    e = store.load_entries(lib)["smith2020surface"]
    assert e.journal == "Applied Surface Science" and not e.needs_review
    assert e.files[0].path == "files/smith2020surface.pdf"
    assert (lib.root / e.files[0].path).exists()


def test_same_pdf_twice_is_duplicate(lib, net, tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    imp = importer.Importer(lib)
    imp.import_pdf(pdf)
    assert imp.import_pdf(pdf).status == importer.DUPLICATE


def test_pdf_of_existing_reference_is_attached(lib, net, tmp_path):
    imp = importer.Importer(lib)
    assert imp.import_identifier("10.1016/j.apsusc.2020.145000").status == \
        importer.ADDED
    pdf = make_pdf(tmp_path / "p.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    o = imp.import_pdf(pdf)
    assert o.status == importer.ATTACHED and o.key == "smith2020surface"
    assert store.load_entries(lib)["smith2020surface"].files


def test_arxiv_stamp(lib, net, tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", ["arXiv:2101.00001v2 [cond-mat] 1 Jan 2021"])
    o = importer.Importer(lib).import_pdf(pdf)
    assert o.status == importer.ADDED and o.key == "martin2021preprint"


def test_title_search_when_no_identifier(lib, net, tmp_path):
    pdf = make_pdf(tmp_path / "t.pdf", ["K. Lee, University of Somewhere"],
                   big="Photoemission study of oxidised copper surfaces")
    o = importer.Importer(lib).import_pdf(pdf)
    assert o.status == importer.ADDED and o.key == "lee2018photoemission"


def test_unknown_pdf_needs_checking(lib, net, tmp_path):
    pdf = make_pdf(tmp_path / "u.pdf", ["Some body text without identifiers " * 3],
                   big="An Obscure Internal Report Title")
    o = importer.Importer(lib).import_pdf(pdf)
    assert o.status == importer.REVIEW
    e = store.load_entries(lib)[o.key]
    assert e.title == "An Obscure Internal Report Title" and e.needs_review
    assert e.files


def test_offline_keeps_identifier_for_later(lib, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "http_get", FakeNet(offline=True))
    pdf = make_pdf(tmp_path / "p.pdf", ["doi:10.1016/j.apsusc.2020.145000"],
                   big="Surface chemistry of titania films")
    o = importer.Importer(lib).import_pdf(pdf)
    assert o.status == importer.REVIEW and "offline" in o.message
    e = store.load_entries(lib)[o.key]
    assert e.doi == "10.1016/j.apsusc.2020.145000"

    monkeypatch.setattr(fetch, "http_get", FakeNet())
    msg = importer.refresh_from_identifiers(e)
    assert "DOI" in msg and e.journal == "Applied Surface Science"
    assert not e.needs_review


def test_folder_import_one_summary(lib, net, tmp_path):
    folder = tmp_path / "papers"
    (folder / "sub").mkdir(parents=True)
    (folder / ".hidden").mkdir()
    make_pdf(folder / "a.pdf", ["doi:10.1016/j.apsusc.2020.145000"])
    make_pdf(folder / "sub" / "b.pdf", ["arXiv:2101.00001"])
    make_pdf(folder / ".hidden" / "c.pdf", ["arXiv:2101.00001"])
    (folder / "notes.txt").write_text("x")
    (folder / "refs.bib").write_text(
        "@book{sagan, title={Cosmos}, author={Carl Sagan}, year={1980}}\n"
        "@article{dup, title={Surface chemistry of titania films}, year=2020,"
        " doi={10.1016/j.apsusc.2020.145000}}\n")
    seen = []
    s = importer.Importer(lib).run([folder], progress=lambda i, n, name: seen.append(n))
    assert s.count(importer.ADDED) == 3 and s.count(importer.DUPLICATE) == 1
    assert seen[-1] == 3
    assert "3 added" in s.headline()
    assert "@book{sagan," in (lib.root / "library.bib").read_text()


def test_bib_file_field_attaches_pdf(lib, tmp_path):
    (tmp_path / "pdfs").mkdir()
    make_pdf(tmp_path / "pdfs" / "x.pdf", ["hello"])
    bib = tmp_path / "x.bib"
    bib.write_text("@article{x2020, title={X paper long enough}, year=2020,"
                   " file={Full Text:pdfs/x.pdf:application/pdf}}")
    importer.Importer(lib, online=False).run([bib])
    e = store.load_entries(lib)["x2020"]
    assert e.files and "file" not in e.extra


def test_csl_json_import(lib, tmp_path):
    p = tmp_path / "zotero.json"
    p.write_text('[{"id": "doe2020", "type": "book", "title": "A Book",'
                 ' "author": [{"family": "Doe", "given": "J"}],'
                 ' "issued": {"date-parts": [[2020]]}}]')
    s = importer.Importer(lib, online=False).run([p])
    assert s.count(importer.ADDED) == 1
    assert store.load_entries(lib)["doe2020"].type == "book"


def test_bad_identifier(lib, net):
    o = importer.Importer(lib).import_identifier("not an id")
    assert o.status == importer.FAILED
