"""Keep Microsoft Word's own source list in step with the library.

Word's References ▸ Insert Citation / Bibliography reads a per-user
"master list" of sources (Sources.xml, Office's bibliography XML). Writing
the library there makes every reference available in Word's citation
tool, with Word's styles (APA, Chicago, Harvard, IEEE, MLA, ISO 690...).

Sources the user created in Word are kept: KherveRef only adds, updates
and removes the ones it wrote itself, recognised by their GUIDs (derived
from library + key, and listed in the state file).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import state
from .model import Entry, Person

NS = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"
B = f"{{{NS}}}"
ET.register_namespace("b", NS)

_TYPES = {
    "article": "JournalArticle", "inproceedings": "ConferenceProceedings",
    "book": "Book", "inbook": "BookSection", "incollection": "BookSection",
    "thesis": "Report", "report": "Report", "online": "InternetSite",
    "dataset": "ElectronicSource", "software": "ElectronicSource",
    "patent": "Patent", "unpublished": "Misc", "misc": "Misc",
}
_STATE = "word_sources.json"


def default_path() -> Path:
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Containers" / "com.microsoft.Word"
                / "Data" / "Library" / "Application Support" / "Microsoft"
                / "Office" / "Sources.xml")
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Bibliography" / "Sources.xml"


def word_installed() -> bool:
    if sys.platform == "darwin":
        return Path("/Applications/Microsoft Word.app").exists() or \
            (Path.home() / "Applications" / "Microsoft Word.app").exists()
    return default_path().parent.parent.exists()


def word_running() -> bool:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["pgrep", "-x", "Microsoft Word"],
                                  capture_output=True).returncode == 0
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE"],
                                 capture_output=True, text=True).stdout
            return "WINWORD.EXE" in out.upper()
    except OSError:
        pass
    return False


def guid_for(library_root: Path, key: str) -> str:
    u = uuid.uuid5(uuid.NAMESPACE_URL,
                   f"kherveref:{Path(library_root).resolve().as_posix()}:{key}")
    return "{" + str(u).upper() + "}"


def _sub(parent, tag: str, text: str | None = None):
    el = ET.SubElement(parent, B + tag)
    if text is not None:
        el.text = text
    return el


def _names(parent, role: str, people: list[Person]) -> None:
    if not people:
        return
    wrap = _sub(parent, role)
    if len(people) == 1 and people[0].literal:
        _sub(wrap, "Corporate", people[0].literal)
        return
    lst = _sub(wrap, "NameList")
    for p in people:
        person = _sub(lst, "Person")
        if p.literal:
            _sub(person, "Last", p.literal)
            continue
        _sub(person, "Last", p.family)
        given = p.given.split()
        if given:
            _sub(person, "First", given[0])
        if len(given) > 1:
            _sub(person, "Middle", " ".join(given[1:]))


def source_element(e: Entry, guid: str) -> ET.Element:
    src = ET.Element(B + "Source")
    _sub(src, "Tag", e.key)
    _sub(src, "SourceType", _TYPES.get(e.type, "Misc"))
    _sub(src, "Guid", guid)
    if e.authors or e.editors:
        author = _sub(src, "Author")
        _names(author, "Author", e.authors)
        _names(author, "Editor", e.editors)
    title = e.title + (f": {e.subtitle}" if e.subtitle else "")
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", e.date)
    fields = [("Title", title)]
    if m:
        months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
        fields += [("Year", m.group(1)),
                   ("Month", months[int(m.group(2)) - 1] if m.group(2)
                    and 1 <= int(m.group(2)) <= 12 else ""),
                   ("Day", str(int(m.group(3))) if m.group(3) else "")]
    if e.type == "article":
        fields.append(("JournalName", e.journal))
    elif e.type in ("inbook", "incollection"):
        fields.append(("BookTitle", e.booktitle))
    elif e.type == "inproceedings":
        fields.append(("ConferenceName", e.booktitle))
    elif e.type == "online":
        fields.append(("InternetSiteTitle", e.journal or e.publisher))
    fields += [
        ("Volume", e.volume), ("Issue", e.number), ("Pages", e.pages),
        ("Edition", e.edition), ("Publisher", e.publisher),
        ("City", e.location), ("Institution", e.institution),
        ("ThesisType", {"phd": "PhD thesis", "master": "Master's thesis"}.get(
            e.thesis_type, e.thesis_type) if e.type == "thesis" else ""),
        ("StandardNumber", e.isbn or e.issn), ("DOI", e.doi),
        ("URL", e.url or (f"https://doi.org/{e.doi}" if e.doi else "")),
    ]
    if e.type == "patent":
        fields.append(("PatentNumber", e.number))
    for tag, value in fields:
        if value:
            _sub(src, tag, value)
    return src


def _load_state() -> dict:
    try:
        return json.loads((state.state_dir() / _STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    d = state.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / _STATE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fallback_path() -> Path:
    """Where the list goes when macOS keeps Word's own folder closed:
    Word's Source Manager ▸ Browse… opens it from there."""
    return Path.home() / "Documents" / "KherveRef" / "Sources.xml"


def sync(library_root: Path, entries, path: Path | None = None) -> dict:
    """Merge the library into Word's source list. Returns counts, plus
    "fallback": True when it had to write fallback_path() instead."""
    if path is None:
        entries = list(entries)
        try:
            return _sync(library_root, entries, default_path())
        except PermissionError:
            res = _sync(library_root, entries, fallback_path())
            res["fallback"] = True
            return res
    return _sync(library_root, entries, Path(path))


def _sync(library_root: Path, entries, path: Path) -> dict:
    root_key = Path(library_root).resolve().as_posix()
    st = _load_state()
    managed = set(st.get(root_key, []))

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element(B + "Sources", {
            "SelectedStyle": "\\APASixthEditionOfficeOnline.xsl",
            "StyleName": "APA", "Version": "6"})
        tree = ET.ElementTree(root)

    wanted = {guid_for(library_root, e.key): e for e in entries}
    removed = 0
    for src in list(root.findall(B + "Source")):
        guid = (src.findtext(B + "Guid") or "").upper()
        if guid in managed or guid in wanted:
            root.remove(src)
            removed += guid not in wanted
    user_tags = {s.findtext(B + "Tag") for s in root.findall(B + "Source")}
    added = skipped = 0
    for guid, e in sorted(wanted.items(), key=lambda kv: kv[1].key.lower()):
        if e.key in user_tags:      # the user's own source with that tag wins
            skipped += 1
            continue
        root.append(source_element(e, guid))
        added += 1
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True,
               default_namespace=None)
    os.replace(tmp, path)
    st[root_key] = sorted(wanted)
    _save_state(st)
    return {"fallback": False, "written": added, "removed": removed,
            "kept_user_sources":
            len(root.findall(B + "Source")) - added, "skipped": skipped,
            "path": str(path)}
