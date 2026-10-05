from kherveref.model import (Attachment, Entry, Person, normalize_doi,
                             parse_name, parse_names)


def test_parse_name_forms():
    assert parse_name("Smith, John A.") == Person("Smith", "John A.")
    assert parse_name("John A. Smith") == Person("Smith", "John A.")
    assert parse_name("Ludwig van Beethoven") == Person("van Beethoven", "Ludwig")
    assert parse_name("van der Berg, Anna") == Person("van der Berg", "Anna")
    assert parse_name("Ford, Jr, Henry") == Person("Ford Jr", "Henry")
    assert parse_name("{World Health Organization}") == Person(
        literal="World Health Organization")
    assert parse_name("Plato") == Person("Plato")


def test_parse_names_list_and_lines():
    assert [p.family for p in parse_names("A. One and Two, B. and others")] \
        == ["One", "Two"]
    assert [p.family for p in parse_names("{Barnes and Noble} and C. Three")] \
        == ["", "Three"]
    assert [p.family for p in parse_names("One, A.\nTwo, B.\n")] == ["One", "Two"]


def test_round_trip_dict():
    e = Entry(key="k", title="T", date="2020-05", authors=[Person("A", "B")],
              keywords=["x"], extra={"zz": "1"},
              files=[Attachment("files/k.pdf", "abc")], collections=["c1"],
              needs_review=True, notes="n")
    assert Entry.from_dict(e.to_dict()) == e
    assert e.year == "2020"


def test_display_helpers():
    e = Entry(authors=[Person("A"), Person("B"), Person("C")])
    assert e.author_text() == "A et al."
    assert Entry(authors=[Person("A"), Person("B")]).author_text() == "A & B"
    assert Entry(eprint="2101.00001", eprinttype="arxiv").container() == \
        "arXiv:2101.00001"


def test_merge_missing_never_overwrites():
    a = Entry(title="Mine", volume="")
    a.merge_missing(Entry(title="Theirs", volume="3", authors=[Person("X")]))
    assert a.title == "Mine" and a.volume == "3" and a.authors[0].family == "X"


def test_replace_bibliographic_keeps_library_fields():
    a = Entry(key="k", title="Old", notes="n", collections=["c"],
              files=[Attachment("files/k.pdf")])
    a.replace_bibliographic(Entry(key="other", title="New", doi="10.1/x"))
    assert (a.key, a.title, a.doi, a.notes, a.collections) == \
        ("k", "New", "10.1/x", "n", ["c"])


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC.") == "10.1000/abc"
    assert normalize_doi("doi: 10.1/x") == "10.1/x"
