import pytest

from kherveref import formats, importer, library, store
from helpers import make_pdf

RIS = """TY  - JOUR
AU  - Smith, Jane A.
AU  - Müller, Anna
TI  - XPS study of TiO2 surfaces under
      ultra-high vacuum
JO  - Applied Surface Science
PY  - 2020/05/17/
VL  - 512
IS  - 3
SP  - 145
EP  - 150
SN  - 0169-4332
DO  - https://doi.org/10.1016/j.apsusc.2020.145000
UR  - https://www.sciencedirect.com/science/article/pii/S0169
L1  - file://{pdf}
KW  - XPS; titania
KW  - surfaces
AB  - We studied surfaces.
N1  - Important paper
ER  - 

TY  - CHAP
AU  - Doe, John
A2  - Editor, Ed
T1  - A chapter
T2  - The Big Book
PB  - Springer
CY  - Berlin
PY  - 2018
SN  - 978-3-16-148410-0
ER  - 

TY  - THES
AU  - Kerherve, Gwilherm
TI  - My doctoral work
PB  - Imperial College London
M3  - PhD Thesis
PY  - 2008
ER  -
"""

ENDNOTE = """<?xml version="1.0" encoding="UTF-8"?>
<xml><records>
<record>
  <ref-type name="Journal Article">17</ref-type>
  <contributors><authors>
    <author><style face="normal">Smith, J. A.</style></author>
    <author>Lee, K.</author>
  </authors></contributors>
  <titles><title><style>Photoemission of copper</style></title>
    <secondary-title>Surface Science</secondary-title></titles>
  <periodical><full-title>Surface Science</full-title></periodical>
  <pages>1-10</pages><volume>7</volume><number>2</number>
  <keywords><keyword>XPS</keyword><keyword>Cu</keyword></keywords>
  <dates><year>2019</year><pub-dates><date>Mar</date></pub-dates></dates>
  <isbn>0039-6028</isbn>
  <electronic-resource-num>10.1016/j.susc.2019.01</electronic-resource-num>
  <urls><pdf-urls><url>internal-pdf://0123456789/Smith-2019.pdf</url></pdf-urls>
  <related-urls><url>https://example.org/paper</url></related-urls></urls>
  <label>smith2019cu</label>
</record>
<record>
  <ref-type name="Book Section">5</ref-type>
  <contributors><authors><author>Doe, J.</author></authors>
  <secondary-authors><author>Editor, E.</author></secondary-authors></contributors>
  <titles><title>Chapter title here</title><secondary-title>Handbook</secondary-title></titles>
  <dates><year>2015</year></dates><publisher>Wiley</publisher>
  <urls><pdf-urls><url>internal-pdf://999/missing.pdf</url></pdf-urls></urls>
</record>
</records></xml>
"""

NBIB = """PMID- 31234567
OWN - NLM
STAT- MEDLINE
DP  - 2019 Jun 15
TI  - Titanium dioxide nanoparticles and their effect on cells: a
      systematic review.
PG  - 1123-9
LID - 10.1000/jnano.2019.42 [doi]
AB  - Background text.
FAU - Smith, Jane A
AU  - Smith JA
FAU - van der Berg, Piet
AU  - van der Berg P
LA  - eng
PT  - Journal Article
PT  - Review
TA  - J Nano
JT  - Journal of nanoscience
IS  - 1234-5678 (Print)
VI  - 12
IP  - 4
MH  - *Titanium/toxicity
OT  - nanoparticles
PMC - PMC654321

PMID- 30000001
TI  - Second record.
DP  - 2018
AU  - Doe J
TA  - Short J
"""


def test_ris(tmp_path):
    pdf = make_pdf(tmp_path / "Smith 2020.pdf", ["x"])
    parsed = formats.parse_ris(RIS.replace("{pdf}", str(pdf).replace(" ", "%20")))
    assert len(parsed.records) == 3 and not parsed.warnings[1:]
    a = parsed.records[0]
    e = a.entry
    assert (e.type, e.title) == ("article", "XPS study of TiO2 surfaces under ultra-high vacuum")
    assert [p.display() for p in e.authors] == ["Smith, Jane A.", "Müller, Anna"]
    assert (e.journal, e.date, e.volume, e.number, e.pages) == (
        "Applied Surface Science", "2020-05-17", "512", "3", "145–150")
    assert (e.issn, e.doi) == ("0169-4332", "10.1016/j.apsusc.2020.145000")
    assert e.url.startswith("https://www.sciencedirect.com")
    assert e.keywords == ["XPS", "titania", "surfaces"]
    assert a.pdfs == [pdf]
    ch = parsed.records[1].entry
    assert (ch.type, ch.booktitle, ch.publisher, ch.location, ch.isbn) == (
        "incollection", "The Big Book", "Springer", "Berlin", "978-3-16-148410-0")
    assert ch.editors[0].family == "Editor"
    th = parsed.records[2].entry
    assert (th.type, th.thesis_type, th.institution) == (
        "thesis", "phd", "Imperial College London")


def test_endnote_xml(tmp_path):
    pdf_dir = tmp_path / "My EndNote Library.Data" / "PDF" / "0123456789"
    pdf_dir.mkdir(parents=True)
    pdf = make_pdf(pdf_dir / "Smith-2019.pdf", ["x"])
    parsed = formats.parse_endnote_xml(ENDNOTE, tmp_path)
    assert len(parsed.records) == 2
    r = parsed.records[0]
    e = r.entry
    assert (e.type, e.key, e.title, e.journal) == (
        "article", "smith2019cu", "Photoemission of copper", "Surface Science")
    assert [p.family for p in e.authors] == ["Smith", "Lee"]
    assert (e.date, e.pages, e.issn, e.doi) == ("2019-03", "1–10", "0039-6028",
                                                "10.1016/j.susc.2019.01")
    assert e.keywords == ["XPS", "Cu"] and r.pdfs == [pdf]
    b = parsed.records[1].entry
    assert (b.type, b.booktitle, b.publisher) == ("incollection", "Handbook", "Wiley")
    assert any("missing.pdf" in w for w in parsed.warnings)


def test_nbib():
    parsed = formats.parse_nbib(NBIB)
    assert len(parsed.records) == 2
    e = parsed.records[0].entry
    assert e.title == ("Titanium dioxide nanoparticles and their effect on cells: "
                       "a systematic review")
    assert [p.display() for p in e.authors] == ["Smith, Jane A", "van der Berg, Piet"]
    assert (e.journal, e.date, e.volume, e.number, e.pages) == (
        "Journal of nanoscience", "2019-06-15", "12", "4", "1123–1129")
    assert (e.doi, e.issn) == ("10.1000/jnano.2019.42", "1234-5678")
    assert e.extra == {"pmid": "31234567", "pmcid": "PMC654321"}
    assert "Titanium" in e.keywords and "nanoparticles" in e.keywords
    d = parsed.records[1].entry
    assert d.authors[0].display() == "Doe, J." and d.journal == "Short J"


def test_sniffing():
    assert formats.looks_like_ris(RIS) and not formats.looks_like_ris(NBIB)
    assert formats.looks_like_nbib(NBIB) and formats.looks_like_endnote(ENDNOTE)


@pytest.fixture
def lib(tmp_path):
    return library.create_library(tmp_path / "lib")


def test_import_files_with_pdfs(lib, tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    pdf = make_pdf(exports / "paper.pdf", ["ris pdf"])
    (exports / "mendeley.ris").write_text(RIS.replace("file://{pdf}", "paper.pdf"),
                                          encoding="utf-8")
    pdf_dir = exports / "Lib.Data" / "PDF" / "0123456789"
    pdf_dir.mkdir(parents=True)
    make_pdf(pdf_dir / "Smith-2019.pdf", ["endnote pdf"])
    (exports / "endnote.xml").write_text(ENDNOTE, encoding="utf-8")
    (exports / "pubmed.nbib").write_text(NBIB, encoding="utf-8")
    (exports / "unrelated.xml").write_text("<svg></svg>")
    s = importer.Importer(lib, online=False).run([exports / "mendeley.ris",
                                                  exports / "endnote.xml",
                                                  exports / "pubmed.nbib"])
    assert s.count(importer.ADDED) == 7
    entries = store.load_entries(lib)
    assert entries["smith2019cu"].files
    with_pdf = [e for e in entries.values() if e.files]
    assert len(with_pdf) == 2
    assert pdf.exists()
    # A folder import picks up these formats too, and skips non-EndNote XML.
    assert {p.name for p in importer.expand_paths([exports])} >= {
        "mendeley.ris", "endnote.xml", "pubmed.nbib"}
    assert "unrelated.xml" not in {p.name for p in importer.expand_paths([exports])}
    again = importer.Importer(lib, online=False).run([exports / "pubmed.nbib"])
    assert again.count(importer.DUPLICATE) == 2


def test_pasted_text_is_recognised(lib):
    imp = importer.Importer(lib, online=False)
    imp.import_text(NBIB)
    imp.import_text(RIS.replace("L1  - file://{pdf}\n", ""))
    assert imp.summary.count(importer.ADDED) == 5
