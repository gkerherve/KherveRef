"""On-disk layout of a KherveRef library.

A library is a folder, versioned with Git and synced through a Git
remote. Only three things are meant for the user:

    <Name>.kref         the library itself: double-click to open it
    PDFs/               attached papers, named by citation key
    library.bib         every reference as classic BibTeX, for LaTeX
                        (KherveTeX reads it); regenerated on each change
    .kherveref/         KherveRef's own data, hidden:
        references/<key>.json   one reference per file, so two machines
                                editing different references merge cleanly
        collections.json        the collection tree
        cache/                  rebuildable, never committed

Format 1 (library.json, entries/, files/, collections.json at the top)
is upgraded in place by `open_library`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = 2
SUFFIX = ".kref"
DATA_DIR = ".kherveref"
ENTRIES_DIR = "references"      # inside DATA_DIR
FILES_DIR = "PDFs"
COLLECTIONS = "collections.json"  # inside DATA_DIR
CACHE_DIR = "cache"             # inside DATA_DIR

_GITIGNORE = f"{DATA_DIR}/{CACHE_DIR}/\n.DS_Store\nThumbs.db\n"
_V1_MANIFEST = "library.json"


class LibraryError(Exception):
    pass


@dataclass
class Library:
    root: Path
    name: str
    dialect: str = "biblatex"
    migrated: bool = False      # set when open_library upgraded the layout
    key_style: str = "author_year_word"     # see keys.KEY_STYLES

    @property
    def manifest(self) -> Path:
        return find_manifest(self.root) or self.root / manifest_name(self.name)

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIR

    @property
    def entries_dir(self) -> Path:
        return self.data_dir / ENTRIES_DIR

    @property
    def files_dir(self) -> Path:
        return self.root / FILES_DIR

    @property
    def collections_path(self) -> Path:
        return self.data_dir / COLLECTIONS

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / CACHE_DIR

    def entry_count(self) -> int:
        return sum(1 for _ in self.entries_dir.glob("*.json"))


def manifest_name(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "Library"
    return safe + SUFFIX


def find_manifest(root: Path) -> Path | None:
    root = Path(root)
    if not root.is_dir():
        return None
    found = sorted(root.glob("*" + SUFFIX))
    for p in found:     # prefer the one named after the folder
        if p.stem == root.name:
            return p
    return found[0] if found else None


def library_root(path: Path) -> Path:
    """The library folder for *path*: the folder itself, or the folder
    holding a .kref file the user double-clicked."""
    path = Path(path)
    return path.parent if path.suffix.lower() == SUFFIX and path.is_file() else path


def is_library(path: Path) -> bool:
    root = library_root(path)
    return find_manifest(root) is not None or (root / _V1_MANIFEST).is_file()


def _write_json(path: Path, data) -> None:
    # Sorted keys and a trailing newline keep Git diffs stable.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def _hide(path: Path) -> None:
    """Dot-folders are hidden on macOS/Linux; Windows needs the flag."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass


def _make_dirs(root: Path) -> None:
    for d in (root / DATA_DIR / ENTRIES_DIR, root / FILES_DIR):
        d.mkdir(parents=True, exist_ok=True)
        (d / ".gitkeep").touch()    # Git does not track empty folders
    _hide(root / DATA_DIR)


def create_library(path: Path, name: str | None = None) -> Library:
    """Lay out a new, empty library in *path* (created if missing; must
    otherwise be empty, ignoring a .git folder)."""
    root = Path(path)
    if is_library(root):
        raise LibraryError(f"{root} already holds a KherveRef library")
    if root.exists() and any(p.name not in (".git", ".DS_Store")
                             for p in root.iterdir()):
        raise LibraryError(f"{root} is not empty")
    name = name or root.name
    root.mkdir(parents=True, exist_ok=True)
    _make_dirs(root)
    _write_json(root / manifest_name(name),
                {"format": FORMAT_VERSION, "name": name, "dialect": "biblatex",
                 "app": "KherveRef"})
    _write_json(root / DATA_DIR / COLLECTIONS, {"collections": []})
    (root / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    return Library(root=root, name=name)


def open_library(path: Path) -> Library:
    root = library_root(path)
    migrated = False
    if find_manifest(root) is None and (root / _V1_MANIFEST).is_file():
        _upgrade_v1(root)
        migrated = True
    manifest = find_manifest(root)
    if manifest is None:
        raise LibraryError(f"{root} is not a KherveRef library")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LibraryError(f"Cannot read {manifest}: {e}") from None
    if data.get("format", 0) > FORMAT_VERSION:
        raise LibraryError(
            f"{root} was written by a newer KherveRef — update the app")
    _make_dirs(root)
    if not (root / DATA_DIR / COLLECTIONS).exists():
        _write_json(root / DATA_DIR / COLLECTIONS, {"collections": []})
    return Library(root=root, name=data.get("name") or manifest.stem,
                   dialect=data.get("dialect", "biblatex"), migrated=migrated,
                   key_style=data.get("key_style", "author_year_word"))


def set_key_style(lib: Library, style: str) -> None:
    """Remember the library's key style in its .kref, so every computer
    syncing it names new references the same way."""
    path = lib.manifest
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"format": FORMAT_VERSION, "name": lib.name}
    data["key_style"] = style
    _write_json(path, data)
    lib.key_style = style


def _upgrade_v1(root: Path) -> None:
    """Format 1 -> 2: KherveRef's files go into the hidden .kherveref
    folder, files/ becomes PDFs/, library.json becomes <Name>.kref."""
    old = json.loads((root / _V1_MANIFEST).read_text(encoding="utf-8"))
    name = old.get("name") or root.name
    data = root / DATA_DIR
    refs = data / ENTRIES_DIR
    refs.mkdir(parents=True, exist_ok=True)
    pdfs = root / FILES_DIR
    pdfs.mkdir(exist_ok=True)

    old_files = root / "files"
    if old_files.is_dir():
        for f in old_files.iterdir():
            if f.name != ".gitkeep":
                shutil.move(str(f), str(pdfs / f.name))
        shutil.rmtree(old_files, ignore_errors=True)

    old_entries = root / "entries"
    if old_entries.is_dir():
        for f in old_entries.glob("*.json"):
            entry = json.loads(f.read_text(encoding="utf-8"))
            for att in entry.get("files", []):
                if isinstance(att, dict) and att.get("path", "").startswith("files/"):
                    att["path"] = f"{FILES_DIR}/" + att["path"][len("files/"):]
            (refs / f.name).write_text(
                json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            f.unlink()
        shutil.rmtree(old_entries, ignore_errors=True)

    if (root / COLLECTIONS).exists():
        os.replace(root / COLLECTIONS, data / COLLECTIONS)
    _write_json(root / manifest_name(name),
                {"format": FORMAT_VERSION, "name": name,
                 "dialect": old.get("dialect", "biblatex"), "app": "KherveRef"})
    (root / _V1_MANIFEST).unlink()
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    lines = [l for l in lines if l.strip() != f"{DATA_DIR}/"]
    if f"{DATA_DIR}/{CACHE_DIR}/" not in lines:
        lines.insert(0, f"{DATA_DIR}/{CACHE_DIR}/")
    ignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _make_dirs(root)
