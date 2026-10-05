"""Fake network and generated PDFs for the import tests."""
import json

import pymupdf

from kherveref import fetch

CSL_PAPER = {
    "type": "article-journal", "title": "Surface chemistry of titania films",
    "author": [{"family": "Smith", "given": "Jane"}, {"family": "Doe", "given": "J."}],
    "container-title": "Applied Surface Science",
    "issued": {"date-parts": [[2020, 6, 1]]}, "volume": "512", "page": "145-150",
    "DOI": "10.1016/j.apsusc.2020.145000",
}

ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>A preprint about
      graphene oxide</title>
    <summary>We study things.</summary>
    <author><name>Alice Martin</name></author>
    <author><name>Bob van Dijk</name></author>
  </entry>
</feed>"""

CROSSREF_SEARCH = {"message": {"items": [
    {"type": "journal-article", "title": ["Something unrelated entirely"]},
    {"type": "journal-article",
     "title": ["Photoemission study of oxidised copper surfaces"],
     "author": [{"family": "Lee", "given": "K."}],
     "issued": {"date-parts": [[2018]]}, "DOI": "10.1/cu.2018",
     "container-title": ["Surf. Sci."]},
]}}


class FakeNet:
    """Answers the URLs the importer calls; records them."""

    def __init__(self, offline=False):
        self.calls = []
        self.offline = offline

    def __call__(self, url, accept="application/json"):
        self.calls.append(url)
        if self.offline:
            raise fetch.NetworkError("offline")
        if url.startswith("https://doi.org/10.1016/j.apsusc.2020.145000"):
            return json.dumps(CSL_PAPER).encode()
        if url.startswith("https://doi.org/") or "crossref.org/works/10." in url:
            raise fetch.LookupError_("not found (404)")
        if "export.arxiv.org" in url:
            return ARXIV_ATOM.encode()
        if "crossref.org/works?" in url:
            return json.dumps(CROSSREF_SEARCH).encode()
        if "openlibrary.org" in url:
            return json.dumps({"ISBN:9780306406157": {
                "title": "A Book", "authors": [{"name": "Carl Sagan"}],
                "publish_date": "1980", "publishers": [{"name": "Pub"}]}}).encode()
        raise fetch.LookupError_("unexpected URL " + url)


def make_pdf(path, lines, title_meta="", subject="", big=None):
    """A one-page PDF: optional big title line, then body lines."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    if big:
        page.insert_text((72, y), big, fontsize=20)
        y += 40
    for line in lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 14
    doc.set_metadata({"title": title_meta, "subject": subject,
                      "author": "", "creationDate": "D:20200101000000"})
    doc.save(str(path))
    doc.close()
    return path
