import json

import pytest

from kherveref import library


def test_create_lays_out_folder(tmp_path):
    root = tmp_path / "Refs"
    lib = library.create_library(root, "My refs")
    assert lib.name == "My refs" and lib.dialect == "biblatex"
    assert (root / "entries").is_dir() and (root / "files").is_dir()
    manifest = json.loads((root / "library.json").read_text())
    assert manifest == {"dialect": "biblatex", "format": 1, "name": "My refs"}
    assert json.loads((root / "collections.json").read_text()) == {
        "collections": []}
    assert ".kherveref/" in (root / ".gitignore").read_text()
    assert library.is_library(root)
    assert lib.entry_count() == 0


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


def test_open_round_trip(tmp_path):
    library.create_library(tmp_path, "Round")
    lib = library.open_library(tmp_path)
    assert lib.name == "Round" and lib.root == tmp_path


def test_open_rejects_plain_folder(tmp_path):
    with pytest.raises(library.LibraryError):
        library.open_library(tmp_path)


def test_open_rejects_newer_format(tmp_path):
    library.create_library(tmp_path)
    (tmp_path / "library.json").write_text(json.dumps({"format": 99}))
    with pytest.raises(library.LibraryError, match="newer"):
        library.open_library(tmp_path)


def test_open_recreates_missing_folders(tmp_path):
    library.create_library(tmp_path)
    (tmp_path / "files" / ".gitkeep").unlink()
    (tmp_path / "files").rmdir()
    assert library.open_library(tmp_path).files_dir.is_dir()
