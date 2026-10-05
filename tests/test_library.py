import json
import os

import pytest

from kherveref import library, store
from kherveref.model import Attachment, Entry


def test_create_lays_out_folder(tmp_path):
    root = tmp_path / "Refs"
    lib = library.create_library(root, "My refs")
    assert lib.name == "My refs" and lib.dialect == "biblatex"
    # What the user sees: the .kref to open, PDFs/, and (once written)
    # library.bib. Everything else is hidden in .kherveref/.
    visible = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
    assert visible == ["My refs.kref", "PDFs"]
    manifest = json.loads((root / "My refs.kref").read_text())
    assert manifest == {"app": "KherveRef", "dialect": "biblatex", "format": 2,
                        "name": "My refs"}
    assert lib.entries_dir == root / ".kherveref" / "references"
    assert json.loads(lib.collections_path.read_text()) == {"collections": []}
    assert ".kherveref/cache/" in (root / ".gitignore").read_text()
    assert library.is_library(root) and library.is_library(root / "My refs.kref")
    assert lib.entry_count() == 0


def test_manifest_name_is_file_safe(tmp_path):
    library.create_library(tmp_path / "x", 'A/B: "refs"?')
    assert (tmp_path / "x" / "A_B_ _refs__.kref").exists()


def test_name_defaults_to_folder(tmp_path):
    assert library.create_library(tmp_path / "Thesis").name == "Thesis"


def test_refuses_non_empty_folder(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(library.LibraryError):
        library.create_library(tmp_path)


def test_allows_existing_git_folder(tmp_path):
    (tmp_path / ".git").mkdir()
    library.create_library(tmp_path)


def test_refuses_existing_library(tmp_path):
    library.create_library(tmp_path)
    with pytest.raises(library.LibraryError):
        library.create_library(tmp_path)


def test_open_by_folder_or_kref(tmp_path):
    library.create_library(tmp_path / "L", "Round")
    for p in (tmp_path / "L", tmp_path / "L" / "Round.kref"):
        lib = library.open_library(p)
        assert lib.name == "Round" and lib.root == tmp_path / "L" and not lib.migrated


def test_open_rejects_plain_folder(tmp_path):
    with pytest.raises(library.LibraryError):
        library.open_library(tmp_path)


def test_open_rejects_newer_format(tmp_path):
    library.create_library(tmp_path, "N")
    (tmp_path / "N.kref").write_text(json.dumps({"format": 99}))
    with pytest.raises(library.LibraryError, match="newer"):
        library.open_library(tmp_path)


def test_open_recreates_missing_folders(tmp_path):
    lib = library.create_library(tmp_path)
    (lib.files_dir / ".gitkeep").unlink()
    lib.files_dir.rmdir()
    assert library.open_library(tmp_path).files_dir.is_dir()


def _v1_library(root):
    """A library as KherveRef 0.1-0.5 wrote it."""
    (root / "entries").mkdir(parents=True)
    (root / "files").mkdir()
    (root / "entries" / ".gitkeep").touch()
    (root / "files" / ".gitkeep").touch()
    (root / "library.json").write_text(json.dumps(
        {"format": 1, "name": "REF Library", "dialect": "biblatex"}))
    (root / "collections.json").write_text(json.dumps(
        {"collections": [{"id": "c1", "name": "Thesis"}]}))
    (root / ".gitignore").write_text(".kherveref/\n.DS_Store\nThumbs.db\n")
    (root / "files" / "smith2020.pdf").write_bytes(b"%PDF-1.4")
    e = Entry(key="smith2020", title="T", collections=["c1"],
              files=[Attachment("files/smith2020.pdf", "abc")])
    (root / "entries" / "smith2020.json").write_text(json.dumps(e.to_dict()))


def test_upgrade_from_format_1(tmp_path):
    root = tmp_path / "REF Library"
    _v1_library(root)
    assert library.is_library(root)
    lib = library.open_library(root)
    assert lib.migrated and lib.name == "REF Library"
    visible = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
    assert visible == ["PDFs", "REF Library.kref"]
    e = store.load_entries(lib)["smith2020"]
    assert e.files[0].path == "PDFs/smith2020.pdf"
    assert (root / e.files[0].path).read_bytes() == b"%PDF-1.4"
    assert [c.name for c in store.load_collections(lib)] == ["Thesis"]
    ignore = (root / ".gitignore").read_text().splitlines()
    assert ".kherveref/cache/" in ignore and ".kherveref/" not in ignore
    assert not library.open_library(root).migrated
    assert os.path.isdir(root / ".kherveref" / "references")
