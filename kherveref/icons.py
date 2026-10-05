"""Runtime icon factory.

No PNG/SVG files are shipped for UI chrome: every icon is a qtawesome
glyph. The set is Phosphor (thin, rounded, one stroke weight — the look
of current desktop apps), drawn in one quiet colour taken from the
theme; only the main "add" actions use the theme's accent colour.
Material Design glyphs are the fallback for older qtawesome builds.

  * icon(name)            -> QIcon in the theme's icon colour
  * icon(name, color=...) -> QIcon with an explicit override
  * set_icon_color(c)     -> retarget new icons to a theme's colour
  * set_accent_color(c)   -> the colour of the ACCENTED actions
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QIcon

import qtawesome as qta

_PHOSPHOR: dict[str, str] = {
    "new_library":  "ph.books",
    "open_library": "ph.folder-open",
    "close":        "ph.x",
    "recent":       "ph.clock-counter-clockwise",
    "add_ref":      "ph.plus",
    "add_doi":      "ph.barcode",
    "import_bib":   "ph.file-arrow-down",
    "export_bib":   "ph.export",
    "delete":       "ph.trash",
    "find":         "ph.magnifying-glass",
    "pdf":          "ph.file-pdf",
    "add_pdf":      "ph.file-plus",
    "add_folder":   "ph.folder-plus",
    "open_pdf":     "ph.book-open",
    "folder":       "ph.folder-open",
    "copy":         "ph.copy",
    "lookup":       "ph.globe",
    "review":       "ph.warning-circle",
    "rename":       "ph.pencil-simple",
    "attach":       "ph.paperclip",
    "save":         "ph.floppy-disk",
    "revert":       "ph.arrow-counter-clockwise",
    "redo":         "ph.arrow-clockwise",
    "collection_new": "ph.folder-simple-plus",
    "notes":        "ph.note",
    "zotero":       "ph.download-simple",
    "all_refs":     "ph.books",
    "unfiled":      "ph.tray",
    "collection":   "ph.folder",
    "commit":       "ph.git-commit",
    "history":      "ph.clock-counter-clockwise",
    "remote":       "ph.cloud",
    "push":         "ph.cloud-arrow-up",
    "pull":         "ph.cloud-arrow-down",
    "about":        "ph.info",
    "claude":       "ph.sparkle",
    "view_list":    "ph.list-bullets",
    "view_covers":  "ph.squares-four",
}

# The few actions that matter most wear the accent colour.
ACCENTED = {"add_pdf", "add_folder", "add_doi"}

# Material Design: the fallback where qtawesome predates Phosphor (< 1.3).
_GLYPHS: dict[str, str] = {
    # Library
    "new_library":  "mdi6.bookshelf",
    "open_library": "mdi6.folder-open-outline",
    "close":        "mdi6.close",
    "recent":       "mdi6.history",

    # References
    "add_ref":      "mdi6.book-plus-outline",
    "add_doi":      "mdi6.identifier",
    "import_bib":   "mdi6.import",
    "export_bib":   "mdi6.export",
    "delete":       "mdi6.delete-outline",
    "find":         "mdi6.magnify",
    "pdf":          "mdi6.file-pdf-box",
    "add_pdf":      "mdi6.file-document-plus-outline",
    "add_folder":   "mdi6.folder-plus-outline",
    "open_pdf":     "mdi6.file-eye-outline",
    "folder":       "mdi6.folder-search-outline",
    "copy":         "mdi6.content-copy",
    "lookup":       "mdi6.cloud-search-outline",
    "review":       "mdi6.alert-circle-outline",
    "rename":       "mdi6.rename-outline",
    "attach":       "mdi6.paperclip",
    "save":         "mdi6.content-save-outline",
    "revert":       "mdi6.undo",
    "redo":         "mdi6.redo",
    "collection_new": "mdi6.folder-plus-outline",
    "notes":        "mdi6.note-text-outline",
    "zotero":       "mdi6.alpha-z-box-outline",

    # Collections tree
    "all_refs":     "mdi6.book-multiple-outline",
    "unfiled":      "mdi6.book-outline",
    "collection":   "mdi6.folder-outline",

    # Git
    "commit":       "mdi6.source-commit",
    "history":      "mdi6.history",
    "remote":       "mdi6.cloud-outline",
    "push":         "mdi6.cloud-upload-outline",
    "pull":         "mdi6.cloud-download-outline",

    # Help
    "about":        "mdi6.information-outline",
    "claude":       "mdi6.robot-outline",
    "view_list":    "mdi6.format-list-bulleted",
    "view_covers":  "mdi6.view-grid-outline",
}

# Older qtawesome builds lack some mdi6 glyphs.
_FALLBACK_GLYPHS: dict[str, str] = {
    "new_library":  "fa5s.book",
    "open_library": "fa5s.folder-open",
    "close":        "fa5s.times",
    "recent":       "fa5s.history",
    "add_ref":      "fa5s.plus",
    "add_doi":      "fa5s.fingerprint",
    "import_bib":   "fa5s.file-import",
    "export_bib":   "fa5s.file-export",
    "delete":       "fa5s.trash",
    "find":         "fa5s.search",
    "pdf":          "fa5s.file-pdf",
    "add_pdf":      "fa5s.file-medical",
    "add_folder":   "fa5s.folder-plus",
    "open_pdf":     "fa5s.eye",
    "folder":       "fa5s.folder",
    "copy":         "fa5s.copy",
    "lookup":       "fa5s.search",
    "review":       "fa5s.exclamation-circle",
    "rename":       "fa5s.i-cursor",
    "attach":       "fa5s.paperclip",
    "save":         "fa5s.save",
    "revert":       "fa5s.undo",
    "redo":         "fa5s.redo",
    "collection_new": "fa5s.folder-plus",
    "notes":        "fa5s.sticky-note",
    "zotero":       "fa5s.file-import",
    "all_refs":     "fa5s.book",
    "unfiled":      "fa5s.book-open",
    "collection":   "fa5s.folder",
    "commit":       "fa5s.code-branch",
    "history":      "fa5s.history",
    "remote":       "fa5s.cloud",
    "push":         "fa5s.cloud-upload-alt",
    "pull":         "fa5s.cloud-download-alt",
    "about":        "fa5s.info-circle",
    "claude":       "fa5s.robot",
}

DEFAULT_COLOR = "#444444"
ACCENT_COLOR = "#1a6dd8"

# cacheKey -> (name, explicit colour), so a live theme switch can find
# and rebuild every icon (see MainWindow._retint_icons).
_ICON_SPECS: dict[int, tuple[str, object]] = {}


def set_icon_color(color: QColor | str) -> None:
    global DEFAULT_COLOR
    DEFAULT_COLOR = QColor(color).name()


def set_accent_color(color: QColor | str) -> None:
    global ACCENT_COLOR
    ACCENT_COLOR = QColor(color).name()


def icon_spec(ic: QIcon):
    """(name, explicit colour) an icon was built with, or None."""
    return _ICON_SPECS.get(ic.cacheKey())


def forget_icon_specs() -> None:
    _ICON_SPECS.clear()


def icon(name: str, color: QColor | str | None = None) -> QIcon:
    c = color if color is not None else (
        ACCENT_COLOR if name in ACCENTED else DEFAULT_COLOR)
    # An unknown glyph name must never take a toolbar down with it.
    for spec in (_PHOSPHOR.get(name), _GLYPHS.get(name),
                 _FALLBACK_GLYPHS.get(name), "fa5s.question"):
        if not spec:
            continue
        try:
            # Under the mouse (QIcon.Active) every icon takes the accent,
            # so it is obvious which button the pointer is on.
            ic = qta.icon(spec, color=c, color_active=ACCENT_COLOR)
        except Exception:
            continue
        _ICON_SPECS[ic.cacheKey()] = (name, color)
        return ic
    return QIcon()


def app_icon() -> QIcon:
    from . import appmark
    ic = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(appmark.paint(sz))
    return ic
