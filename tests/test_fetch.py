import pytest

from kherveref import fetch
from helpers import FakeNet


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(fetch, "http_get", fake)
    return fake


def test_find_identifiers():
    assert fetch.find_doi("see https://doi.org/10.1000/xyz123). Next") == "10.1000/xyz123"
    assert fetch.find_doi("doi:10.1002/(SICI)1097-4636(199905)45:2<1::AID>") \
        .startswith("10.1002/(SICI)1097-4636(199905)45:2")
    assert fetch.find_doi("(doi: 10.1000/abc)") == "10.1000/abc"
    assert fetch.find_arxiv("arXiv:2101.00001v2 [cs.LG] 1 Jan 2021") == "2101.00001"
    assert fetch.find_arxiv("https://arxiv.org/abs/hep-th/9901001") == "hep-th/9901001"
    assert fetch.find_isbn("ISBN 978-0-306-40615-7") == "9780306406157"
    assert fetch.find_isbn("ISBN 978-0-306-40615-8") == ""


def test_classify():
    assert fetch.classify("10.1000/abc") == ("doi", "10.1000/abc")
    assert fetch.classify("https://doi.org/10.1000/abc") == ("doi", "10.1000/abc")
    assert fetch.classify("2101.00001") == ("arxiv", "2101.00001")
    assert fetch.classify("arXiv:2101.00001v3") == ("arxiv", "2101.00001")
    assert fetch.classify("0-306-40615-2") == ("isbn", "0306406152")
    assert fetch.classify("hello") == ("", "")


def test_lookup_doi(net):
    e = fetch.lookup_doi("https://doi.org/10.1016/J.APSUSC.2020.145000")
    assert e.title == "Surface chemistry of titania films"
    assert e.journal == "Applied Surface Science" and e.date == "2020-06-01"


def test_lookup_doi_not_found(net):
    with pytest.raises(fetch.LookupError_):
        fetch.lookup_doi("10.9999/none")


def test_lookup_arxiv(net):
    e = fetch.lookup_arxiv("2101.00001v2")
    assert e.title == "A preprint about graphene oxide"
    assert [p.display() for p in e.authors] == ["Martin, Alice", "van Dijk, Bob"]
    assert (e.eprint, e.eprinttype, e.date, e.type) == \
        ("2101.00001", "arxiv", "2021-01-01", "unpublished")


def test_lookup_isbn(net):
    e = fetch.lookup_isbn("978-0-306-40615-7")
    assert (e.type, e.title, e.date, e.publisher) == ("book", "A Book", "1980", "Pub")


def test_search_title_needs_close_match(net):
    e = fetch.search_title("Photoemission study of oxidised copper surfaces")
    assert e is not None and e.doi == "10.1/cu.2018"
    assert fetch.search_title("A completely different paper about bees") is None


def test_person_list():
    assert [p.family for p in fetch.person_list("A. One; B. Two")] == ["One", "Two"]
    assert [p.family for p in fetch.person_list("A One, B Two, C Three")] == \
        ["One", "Two", "Three"]
