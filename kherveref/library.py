"""On-disk layout of a KherveRef library.

A library is a plain folder, versioned with Git and synced through a
Git remote:

    library.json        name, format version, BibTeX dialect
    collections.json    the collection tree
    entries/<key>.json  one reference per file, so two machines editing
                        different references merge without conflicts
    files/              attached PDFs, named by citation key
    .kherveref/         local caches (search index); never committed

The files are the data; anything under .kherveref/ can be deleted and
rebuilt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = 1
MANIFEST = "library.json"
COLLECTIONS = "collections.json"
ENTRIES_DIR = "entries"
FILES_DIR = "files"
CACHE_DIR = ".kherveref"

_GITIGNORE = f"{CACHE_DIR}/\n.DS_Store\nThumbs.db\n"


class LibraryError(Exception):
    pass


@dataclass
class Library:
    root: Path
    name: str
    dialect: str = "biblatex"

    @property
    def entries_dir(self) -> Path:
        return self.root / ENTRIES_DIR

    @property
    def files_dir(self) -> Path:
        return self.root / FILES_DIR

    @property
    def cache_dir(self) -> Path:
        return self.root / CACHE_DIR

    def entry_count(self) -> int:
        return sum(1 for _ in self.entries_dir.glob("*.json"))


def _write_json(path: Path, data) -> None:
    # Sorted keys and a trailing newline keep Git diffs stable.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def is_library(path: Path) -> bool:
    return (Path(path) / MANIFEST).is_file()


def create_library(path: Path, name: str | None = None) -> Library:
    """Lay out a new, empty library in *path* (created if missing; must
    otherwise be empty, ignoring a .git folder)."""
    root = Path(path)
    if is_library(root):
        raise LibraryError(f"{root} already holds a KherveRef library")
    if root.exists() and any(p.name != ".git" for p in root.iterdir()):
        raise LibraryError(f"{root} is not empty")
    name = name or root.name
    (root / ENTRIES_DIR).mkdir(parents=True, exist_ok=True)
    (root / FILES_DIR).mkdir(exist_ok=True)
    # Git does not track empty folders.
    for d in (ENTRIES_DIR, FILES_DIR):
        (root / d / ".gitkeep").touch()
    _write_json(root / MANIFEST, {"format": FORMAT_VERSION, "name": name,
                                  "dialect": "biblatex"})
    _write_json(root / COLLECTIONS, {"collections": []})
    (root / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    return Library(root=root, name=name)


def open_library(path: Path) -> Library:
    root = Path(path)
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"{root} is not a KherveRef library") from None
    except (OSError, ValueError) as e:
        raise LibraryError(f"Cannot read {root / MANIFEST}: {e}") from None
    if data.get("format", 0) > FORMAT_VERSION:
        raise LibraryError(
            f"{root} was written by a newer KherveRef — update the app")
    for d in (ENTRIES_DIR, FILES_DIR):
        (root / d).mkdir(exist_ok=True)
    return Library(root=root, name=data.get("name") or root.name,
                   dialect=data.get("dialect", "biblatex"))
