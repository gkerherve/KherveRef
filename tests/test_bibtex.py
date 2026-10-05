from kherveref import bibtex
from kherveref.model import Entry, Person

SAMPLE = r"""
@string{jacs = "J. Am. Chem. Soc."}
@comment{ignored @article{nope, title={x}} }
@preamble{"\newcommand{\noop}[1]{}"}

@Article{smith2020,
  author    = {Smith, John and M{\"u}ller, Anna and {World Health Organization}},
  title     = {{XPS} study of {TiO2} surfaces},
  journal   = jacs,
  year      = 2020,
  month     = may,
  volume    = {142},
  pages     = {10--20},
  doi       = {https://doi.org/10.1021/JACS.0C00001},
  keywords  = {xps; titania},
  file      = {:smith.pdf:PDF},
  custom    = {kept}
}

@phdthesis{doe2019, author = "Jane Doe", title = "A thesis",
  school = "Imperial College London", year = "2019"}

@online{web, title={Page}, url={https://example.com/a_b}, date={2021-03-04},
  journaltitle={Ignored container}}

@article{broken, title = {missing close
"""


def test_parse_sample():
    res = bibtex.parse(SAMPLE)
    keys = [e.key for e in res.entries]
    assert keys[:3] == ["smith2020", "doe2019", "web"]
    s = res.entries[0]
    assert s.type == "article"
    assert [p.display() for p in s.authors] == [
        "Smith, John", "Müller, Anna", "World Health Organization"]
    assert s.title == "XPS study of TiO2 surfaces"
    assert s.journal == "J. Am. Chem. Soc."
    assert s.date == "2020-05"
    assert s.pages == "10–20"
    assert s.doi == "10.1021/JACS.0C00001"
    assert s.keywords == ["xps", "titania"]
    assert s.extra == {"custom": "kept", "file": ":smith.pdf:PDF"}
    d = res.entries[1]
    assert (d.type, d.thesis_type, d.institution, d.year) == \
        ("thesis", "phd", "Imperial College London", "2019")
    w = res.entries[2]
    assert (w.type, w.date, w.url) == ("online", "2021-03-04",
                                       "https://example.com/a_b")
    assert len(keys) == 3 and "malformed" in res.warnings[0]


def test_parenthesised_entry_and_concatenation():
    res = bibtex.parse('@string{a="Foo"} @misc(k1, title = a # " bar")')
    assert res.entries[0].title == "Foo bar"


def _entry():
    return Entry(key="smith2020", type="article",
                 authors=[Person("Müller", "Anna"), Person("van der Berg", "Jan"),
                          Person(literal="World Health Organization")],
                 title="XPS study of TiO2", date="2020-05", journal="J. Phys.",
                 volume="3", pages="10–20", doi="10.1/x_y",
                 eprint="2001.00001", eprinttype="arxiv", keywords=["a", "b"])


def test_biblatex_output():
    out = bibtex.entry_to_bibtex(_entry(), "biblatex")
    assert out.startswith("@article{smith2020,\n")
    assert "author       = {Müller, Anna and van der Berg, Jan and " \
           "{World Health Organization}}" in out
    assert "title        = {{XPS} study of {TiO2}}" in out
    assert "journaltitle = {J. Phys.}" in out
    assert "date         = {2020-05}" in out
    assert "pages        = {10--20}" in out
    assert "doi          = {10.1/x_y}" in out
    assert "eprinttype   = {arxiv}" in out


def test_bibtex_output():
    out = bibtex.entry_to_bibtex(_entry(), "bibtex")
    assert r'{M{\"{u}}ller}, Anna' in out or r'M{\"{u}}ller, Anna' in out
    assert "journal       = {J. Phys.}" in out
    assert "year          = {2020}" in out
    assert "month         = {may}" in out
    assert "archiveprefix = {arXiv}" in out


def test_thesis_and_online_mapping():
    t = Entry(key="t", type="thesis", thesis_type="master", title="T",
              institution="Uni", date="2019")
    assert bibtex.entry_to_bibtex(t, "bibtex").startswith("@mastersthesis{t,")
    assert "school" in bibtex.entry_to_bibtex(t, "bibtex")
    assert "type        = {mathesis}" in bibtex.entry_to_bibtex(t, "biblatex")
    o = Entry(key="o", type="online", title="Page", url="https://x.org")
    out = bibtex.entry_to_bibtex(o, "bibtex")
    assert out.startswith("@misc{o,") and r"\url{https://x.org}" in out


def test_round_trip_through_both_dialects():
    for dialect in bibtex.DIALECTS:
        back = bibtex.parse(bibtex.to_bibtex([_entry()], dialect)).entries[0]
        e = _entry()
        for name in ("key", "type", "title", "date", "journal", "volume",
                     "pages", "doi", "eprint", "eprinttype", "keywords"):
            assert getattr(back, name) == getattr(e, name), (dialect, name)
        assert [p.display() for p in back.authors] == \
            [p.display() for p in e.authors]


def test_output_is_sorted_and_deterministic():
    a, b = Entry(key="b", title="B"), Entry(key="A", title="A")
    assert bibtex.to_bibtex([a, b]) == bibtex.to_bibtex([b, a])
    assert bibtex.to_bibtex([a, b]).index("{A,") < bibtex.to_bibtex([a, b]).index("{b,")
