"""Main window: collections on the left, references in the middle,
details on the right; a start page when no library is open."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from PySide6.QtCore import (QFileSystemWatcher, QItemSelectionModel, QSettings,
                            QSize, Qt, QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QDesktopServices, QIcon,
                           QKeySequence)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QTableView, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import (bibtex, csl, git_backend, icons, importer, library, links,
               state, store, themes)
from . import version_string
from .covers import CoversView
from .editor import EntryEditor
from .icons import icon
from .jobs import ImportJob, LookupJob, SummaryDialog
from .keys import is_valid_key
from .model import Entry
from .table_model import (ALL, COL_KEY, COL_STATUS, COL_TITLE, KEYS_MIME,
                          REVIEW, UNFILED, RefFilterProxy, RefTableModel)
from .thumbnails import Thumbnails

SETTINGS = ("kherve", "KherveRef")
MAX_RECENT = 8
SCOPE_ROLE = Qt.UserRole


class _GitJob(QThread):
    """Runs a push or pull off the GUI thread (they can take a while on
    a slow network or when the credential helper prompts)."""
    done = Signal(bool, str)

    def __init__(self, fn, root: Path, parent=None):
        super().__init__(parent)
        self._fn, self._root = fn, root

    def run(self):
        ok, msg = self._fn(self._root)
        self.done.emit(ok, msg)


class HistoryDialog(QDialog):
    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library history")
        self.resize(640, 420)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Commit", "Date", "Author", "Change"])
        tree.setRootIsDecorated(False)
        for sha, author, when, subject in git_backend.history(root):
            stamp = datetime.datetime.fromtimestamp(when).strftime(
                "%Y-%m-%d %H:%M")
            tree.addTopLevelItem(QTreeWidgetItem([sha, stamp, author, subject]))
        tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tree)
        lay.addWidget(buttons)


class CollectionTree(QTreeWidget):
    """Accepts references dragged from the table (adds them to the
    collection) and files from outside (imports them into it)."""
    keys_dropped = Signal(list, str)
    paths_dropped = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def _scope_at(self, pos) -> str:
        it = self.itemAt(pos)
        return it.data(0, SCOPE_ROLE) if it is not None else ""

    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if md.hasFormat(KEYS_MIME) or md.hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        scope = self._scope_at(ev.position().toPoint())
        md = ev.mimeData()
        ok = (md.hasFormat(KEYS_MIME) and scope not in ("", ALL, UNFILED, REVIEW)) \
            or (md.hasUrls() and scope != "")
        ev.setAccepted(ok)
        if ok:
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        scope = self._scope_at(ev.position().toPoint())
        md = ev.mimeData()
        if md.hasFormat(KEYS_MIME):
            keys = bytes(md.data(KEYS_MIME)).decode().split("\n")
            self.keys_dropped.emit([k for k in keys if k], scope)
        elif md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            target = scope if scope not in (ALL, UNFILED, REVIEW) else ""
            self.paths_dropped.emit(paths, target)
        ev.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, theme_name: str = "Light"):
        super().__init__()
        self._theme_name = theme_name
        self._theme = themes.THEMES.get(theme_name, themes.THEMES["Light"])
        icons.set_icon_color(themes.icon_color(self._theme))
        icons.set_accent_color(self._theme["accent"])
        self.library: library.Library | None = None
        self._thumbs = Thumbnails(self)
        self._thumbs.ready.connect(self._thumbnail_ready)
        self.entries: dict[str, Entry] = {}
        self.collections: list[store.Collection] = []
        self._git_job: _GitJob | None = None
        self._job: QThread | None = None
        self._progress: QProgressDialog | None = None
        self._scope = ALL

        # Changes made outside this window (the MCP server, a git pull in
        # a terminal) are picked up from the library folder.
        self._watcher = QFileSystemWatcher(self)
        self._watch_timer = QTimer(self, singleShot=True, interval=600)
        self._watcher.directoryChanged.connect(lambda _p: self._watch_timer.start())
        self._watcher.fileChanged.connect(lambda _p: self._watch_timer.start())
        self._watch_timer.timeout.connect(self._external_change)

        self.setAcceptDrops(True)
        self.resize(1360, 820)
        self._build_central()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._status = QLabel()
        self.statusBar().addPermanentWidget(self._status)
        self._apply_theme_qss()
        self._set_view(QSettings(*SETTINGS).value("view_mode", "list"),
                       remember=False)
        self._refresh()

    # ------------------------------------------------------------------ #
    # Layout                                                               #
    # ------------------------------------------------------------------ #

    def _build_central(self) -> None:
        self._stack = QStackedWidget()

        start = QWidget()
        lay = QVBoxLayout(start)
        lay.addStretch(2)
        title = QLabel("<h1>KherveRef</h1>"
                       "<p>Open a reference library, or start a new one.</p>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        for text, slot, name in (("New library…", self._new_library, "new_library"),
                                 ("Open library…", self._open_library_dialog,
                                  "open_library")):
            btn = QPushButton(icon(name), text)
            btn.setIconSize(QSize(20, 20))
            btn.setMinimumWidth(220)
            btn.clicked.connect(slot)
            lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addStretch(3)
        self._stack.addWidget(start)

        self._tree = CollectionTree()
        self._tree.setMinimumWidth(190)
        self._tree.currentItemChanged.connect(self._scope_changed)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_menu)
        self._tree.keys_dropped.connect(self._add_keys_to_collection)
        self._tree.paths_dropped.connect(
            lambda paths, cid: self.import_paths([Path(p) for p in paths], cid))
        self._tree.itemChanged.connect(self._collection_renamed)

        self._model = RefTableModel(self, thumb_path=self._thumbs.path)
        self._proxy = RefFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(COL_KEY, Qt.AscendingOrder)
        self._table.setAlternatingRowColors(True)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragOnly)
        self._table.verticalHeader().hide()
        self._table.verticalHeader().setDefaultSectionSize(24)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        hdr.resizeSection(COL_STATUS, 28)
        hdr.resizeSection(COL_KEY, 140)
        hdr.resizeSection(2, 120)
        hdr.resizeSection(3, 46)
        hdr.resizeSection(5, 140)
        hdr.resizeSection(6, 80)
        self._table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._table.doubleClicked.connect(lambda _ix: self._open_first_file())
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(
            lambda pos: self._table_menu(self._table.viewport().mapToGlobal(pos)))

        self._covers = CoversView(self._thumbs, self._entry_at)
        self._covers.setModel(self._proxy)
        self._covers.setSelectionModel(self._table.selectionModel())
        self._covers.doubleClicked.connect(lambda _ix: self._open_first_file())
        self._covers.setContextMenuPolicy(Qt.CustomContextMenu)
        self._covers.customContextMenuRequested.connect(
            lambda pos: self._table_menu(self._covers.viewport().mapToGlobal(pos)))
        self._views = QStackedWidget()
        self._views.addWidget(self._table)
        self._views.addWidget(self._covers)

        self._editor = EntryEditor()
        self._editor.setMinimumWidth(340)
        self._editor.save_requested.connect(self._save_entry)
        self._editor.lookup_requested.connect(self._lookup_in_editor)
        self._editor.open_file_requested.connect(self._open_file)
        self._editor.attach_requested.connect(self._attach_to)
        self._editor.remove_file_requested.connect(self._remove_file)

        split = QSplitter()
        split.addWidget(self._tree)
        split.addWidget(self._views)
        split.addWidget(self._editor)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 760, 390])
        self._stack.addWidget(split)
        self.setCentralWidget(self._stack)

    def _act(self, text, slot, name=None, shortcut=None, tip=None) -> QAction:
        a = QAction(icon(name) if name else QIcon(), text, self)
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.act_new = A("&New library…", self._new_library, "new_library")
        self.act_open = A("&Open library…", self._open_library_dialog,
                          "open_library", QKeySequence.Open)
        self.act_close = A("&Close library", self.close_library, "close")
        self.act_quit = A("&Quit", self.close, None, QKeySequence.Quit)

        self.act_add_pdfs = A("Add &PDFs…", self._add_pdfs_dialog, "add_pdf",
                              "Ctrl+Shift+P",
                              "Add PDFs; their details are looked up online")
        self.act_import_folder = A("Import &folder…", self._import_folder_dialog,
                                   "add_folder", None,
                                   "Add every PDF and .bib file in a folder "
                                   "and its subfolders")
        self.act_add_id = A("Add by &DOI / arXiv / ISBN…", self._add_identifiers,
                            "add_doi", "Ctrl+Shift+D")
        self.act_import_bib = A("&Import file (BibTeX, RIS, EndNote, PubMed)…",
                                self._import_bib_dialog, "import_bib", None,
                                "Exports from other reference managers: .bib, "
                                ".ris, EndNote .xml, PubMed .nbib, CSL .json")
        self.act_import_zotero = A("Import from &Zotero…", self._import_zotero,
                                   "zotero", None,
                                   "Every reference, PDF, collection, tag and note "
                                   "of a Zotero library")
        self.act_new_ref = A("New &empty reference", self._new_reference,
                             "add_ref", QKeySequence.New)
        self.act_export_biblatex = A("Export &BibLaTeX…",
                                     lambda: self._export("biblatex"), "export_bib",
                                     "Ctrl+E")
        self.act_export_bibtex = A("Export classic Bib&TeX…",
                                   lambda: self._export("bibtex"))
        self.act_export_csl = A("Export CSL-&JSON…", lambda: self._export("csl"))

        self.act_copy_key = A("Copy citation &key", self._copy_keys, "copy",
                              "Ctrl+Shift+C")
        self.act_copy_cite = A("Copy \\&cite{…}", self._copy_cite)
        self.act_copy_bib = A("Copy Bib&LaTeX", lambda: self._copy_bib("biblatex"))
        self.act_paste = A("&Paste DOIs / BibTeX", self._paste, None,
                           QKeySequence.Paste)
        self.act_delete = A("&Delete reference…", self._delete_selected, "delete",
                            QKeySequence.Delete)
        self.act_find = A("&Find", lambda: (self._search.setFocus(),
                                            self._search.selectAll()),
                          "find", QKeySequence.Find)
        self.act_open_file = A("Open &PDF", self._open_first_file, "open_pdf")
        self.act_show_file = A("Show in &folder", self._show_in_folder, "folder")
        self.act_lookup = A("&Look up details", self._lookup_selected, "lookup",
                            "Ctrl+L")
        self.act_lookup_review = A("Look up all that &need checking",
                                   self._lookup_all_review, "review")
        self.act_rename_key = A("&Rename key…", self._rename_key, "rename")
        self.act_annotations = A("Copy PDF &annotations to notes",
                                 self._annotations_to_notes, "notes", None,
                                 "Highlights and comments made in KhervePDF "
                                 "(or any PDF viewer) become notes")
        self.act_new_collection = A("New &collection…",
                                    lambda: self._new_collection(""), "collection_new")
        self.act_online = QAction("Look up details &online", self, checkable=True)
        self.act_online.setChecked(
            QSettings(*SETTINGS).value("online", True, type=bool))
        self.act_online.toggled.connect(
            lambda on: QSettings(*SETTINGS).setValue("online", on))

        self.act_use_khervepdf = QAction("Open PDFs in &KhervePDF", self,
                                         checkable=True)
        self.act_use_khervepdf.setChecked(links.use_khervepdf())
        self.act_use_khervepdf.toggled.connect(
            lambda on: QSettings(*SETTINGS).setValue(links.USE_KEY, on))
        self.act_locate_pdf = A("Locate KhervePDF…", self._locate_khervepdf)

        self.act_view_list = A("&List", lambda: self._set_view("list"), "view_list",
                               "Ctrl+1", "References as a list")
        self.act_view_covers = A("&Covers", lambda: self._set_view("covers"),
                                 "view_covers", "Ctrl+2",
                                 "References as their PDFs' front pages")
        view_group = QActionGroup(self)
        for a in (self.act_view_list, self.act_view_covers):
            a.setCheckable(True)
            view_group.addAction(a)

        self.act_history = A("&History…", self._show_history, "history")
        self.act_remote = A("Set &remote…", self._set_remote, "remote")
        self.act_pull = A("&Pull", lambda: self._run_git(git_backend.pull, "Pull"),
                          "pull", None, "Get changes from the library's Git remote")
        self.act_push = A("P&ush", lambda: self._run_git(git_backend.push, "Push"),
                          "push", None, "Send this library's changes to its Git remote")
        self.act_about = A("&About KherveRef", self._about, "about")
        self.act_claude = A("Use with &Claude (MCP)…", self._show_mcp_help, "claude")

        self._library_actions = [
            self.act_close, self.act_add_pdfs, self.act_import_folder,
            self.act_add_id, self.act_import_bib, self.act_import_zotero,
            self.act_new_ref,
            self.act_export_biblatex, self.act_export_bibtex, self.act_export_csl,
            self.act_paste, self.act_find, self.act_lookup_review,
            self.act_new_collection, self.act_history, self.act_remote,
            self.act_pull, self.act_push]
        self._selection_actions = [
            self.act_copy_key, self.act_copy_cite, self.act_copy_bib,
            self.act_delete, self.act_open_file, self.act_show_file,
            self.act_lookup, self.act_rename_key, self.act_annotations]

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.act_new, self.act_open])
        self._recent_menu = m.addMenu(icon("recent"), "Open &recent")
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addAction(self.act_close)
        m.addSeparator()
        m.addActions([self.act_add_pdfs, self.act_import_folder, self.act_add_id,
                      self.act_import_bib, self.act_import_zotero, self.act_new_ref])
        m.addSeparator()
        m.addActions([self.act_export_biblatex, self.act_export_bibtex,
                      self.act_export_csl])
        m.addSeparator()
        m.addAction(self.act_quit)

        m = mb.addMenu("&Edit")
        m.addActions([self.act_copy_key, self.act_copy_cite, self.act_copy_bib,
                      self.act_paste])
        m.addSeparator()
        m.addActions([self.act_find, self.act_rename_key, self.act_delete])

        m = mb.addMenu("&Reference")
        m.addActions([self.act_open_file, self.act_show_file,
                      self.act_annotations])
        m.addSeparator()
        m.addActions([self.act_lookup, self.act_lookup_review])
        m.addSeparator()
        m.addActions([self.act_online, self.act_use_khervepdf, self.act_locate_pdf])

        m = mb.addMenu("&View")
        m.addActions([self.act_view_list, self.act_view_covers])
        m.addSeparator()
        m_theme = m.addMenu("&Theme")
        group = QActionGroup(self)
        for name in themes.THEME_NAMES:
            a = QAction(name, self, checkable=True)
            a.setChecked(name == self._theme_name)
            a.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            group.addAction(a)
            m_theme.addAction(a)

        m = mb.addMenu("&Library")
        m.addAction(self.act_new_collection)
        m.addSeparator()
        m.addAction(self.act_history)
        m.addSeparator()
        m.addActions([self.act_remote, self.act_pull, self.act_push])

        self._help_menu = mb.addMenu("&Help")
        self._help_menu.addAction(self.act_claude)
        self._help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.act_add_pdfs, self.act_import_folder, self.act_add_id):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_export_biblatex)
        tb.addSeparator()
        tb.addActions([self.act_pull, self.act_push])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        for a in (self.act_view_list, self.act_view_covers):
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.addSeparator()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search authors, titles, keys, DOIs…")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icon("find"), QLineEdit.LeadingPosition)
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._search_changed)
        tb.addWidget(self._search)
        self.addToolBar(tb)

    # ------------------------------------------------------------------ #
    # Library lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def _new_library(self) -> None:
        start = Path.home() / "Documents" / "My References"
        path, _ = QFileDialog.getSaveFileName(
            self, "New library — give it a name and choose where it goes",
            str(start), f"KherveRef library (*{library.SUFFIX})")
        if not path:
            return
        # "…/Thesis.kref" becomes the folder …/Thesis holding Thesis.kref.
        folder = Path(path)
        if folder.suffix.lower() == library.SUFFIX:
            folder = folder.with_suffix("")
        try:
            lib = library.create_library(folder, folder.name)
        except library.LibraryError as e:
            QMessageBox.warning(self, "New library", str(e))
            return
        store.write_library_bib(lib, [])
        git_backend.commit_all(lib.root, f"Create library {lib.name}")
        self.open_library(lib.root)

    def _open_library_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a library", str(Path.home()),
            f"KherveRef library (*{library.SUFFIX} library.json)")
        if path:
            self.open_library(Path(path))

    def open_library(self, path: Path) -> bool:
        self._flush_editor()
        try:
            lib = library.open_library(path)
        except library.LibraryError as e:
            QMessageBox.warning(self, "Open library", str(e))
            return False
        self.library = lib
        self._thumbs.set_library(lib)
        self._remember(lib.root)
        self._scope = ALL
        self._watch(lib)
        self._reload()
        store.write_library_bib(lib, self.entries.values())
        if lib.migrated:
            git_backend.commit_all(lib.root, "Tidy the library folder: "
                                   f"{lib.manifest.name}, PDFs/, library.bib")
            self.statusBar().showMessage(
                f"Library folder tidied: open it with {lib.manifest.name}; "
                "papers are in PDFs/", 8000)
        self._refresh()
        return True

    def reopen_last_library(self) -> None:
        last = QSettings(*SETTINGS).value("last_library", "")
        if last and library.is_library(Path(last)):
            self.open_library(Path(last))

    def close_library(self) -> None:
        self._flush_editor()
        self._watch(None)
        self.library = None
        self._thumbs.set_library(None)
        self.entries = {}
        self.collections = []
        self._model.set_entries([])
        self._editor.set_entry(None)
        QSettings(*SETTINGS).setValue("last_library", "")
        self._refresh()

    def _recent(self) -> list[str]:
        val = QSettings(*SETTINGS).value("recent_libraries", []) or []
        return [val] if isinstance(val, str) else list(val)

    def _remember(self, root: Path) -> None:
        state.remember_library(root)
        s = QSettings(*SETTINGS)
        recent = [p for p in self._recent() if p != str(root)]
        s.setValue("recent_libraries", [str(root)] + recent[:MAX_RECENT - 1])
        s.setValue("last_library", str(root))

    def _fill_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = [p for p in self._recent() if library.is_library(Path(p))]
        for p in paths:
            a = self._recent_menu.addAction(p)
            a.triggered.connect(lambda _=False, p=p: self.open_library(Path(p)))
        if not paths:
            self._recent_menu.addAction("(none)").setEnabled(False)

    # ------------------------------------------------------------------ #
    # Loading / saving                                                     #
    # ------------------------------------------------------------------ #

    def _reload(self, select: list[str] | None = None) -> None:
        """Re-read the library from disk (after imports, pulls...)."""
        if self.library is None:
            return
        keep = select if select is not None else self.selected_keys()
        self.entries = store.load_entries(self.library)
        self.collections = store.load_collections(self.library)
        self._model.set_entries(self.entries.values())
        self._rebuild_tree()
        self._select_keys(keep)
        if not keep:
            self._editor.set_entry(None)

    def _watch(self, lib) -> None:
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if lib is not None:
            # Folders, not files: on Windows a watched file is held open,
            # and replacing collections.json then fails ("Access denied").
            self._watcher.addPaths([str(lib.entries_dir), str(lib.data_dir)])

    def _signature(self, entries) -> dict[str, str]:
        return {k: e.modified for k, e in entries.items()}

    def _external_change(self) -> None:
        if self.library is None or self._job is not None:
            return
        if self._editor.is_dirty():
            self._watch_timer.start(2000)   # never discard edits in progress
            return
        on_disk = store.load_entries(self.library)
        cols = store.load_collections(self.library)
        if self._signature(on_disk) == self._signature(self.entries) and \
                cols == self.collections:
            return      # our own save
        self._reload()
        self.statusBar().showMessage("Library updated from outside the window",
                                     4000)

    def _commit(self, message: str) -> None:
        store.write_library_bib(self.library, self.entries.values())
        git_backend.commit_all(self.library.root, message)
        self._update_status()

    def _flush_editor(self) -> None:
        """Save pending edits before the editor shows something else."""
        if self._editor.is_dirty():
            self._editor.save()

    def _save_entry(self, e: Entry) -> None:
        lib = self.library
        if lib is None:
            return
        if not e.key:
            store.add_entry(lib, e, self.entries)
            msg = f"Add {e.key}"
        else:
            store.save_entry(lib, e)
            self.entries[e.key] = e
            msg = f"Edit {e.key}"
        self._model.update_entry(e)
        self._commit(msg)
        if self._editor.entry is None or self._editor.entry.key in ("", e.key):
            self._editor.set_entry(e)
        self._rebuild_tree()

    # ------------------------------------------------------------------ #
    # Collections tree                                                     #
    # ------------------------------------------------------------------ #

    def _rebuild_tree(self) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        n_review = sum(1 for e in self.entries.values() if e.needs_review)
        n_unfiled = sum(1 for e in self.entries.values() if not e.collections)
        current = None
        for scope, text, ic in ((ALL, f"All references ({len(self.entries)})",
                                 "all_refs"),
                                (UNFILED, f"Unfiled ({n_unfiled})", "unfiled"),
                                (REVIEW, f"Needs checking ({n_review})", "review")):
            if scope == REVIEW and not n_review:
                continue
            it = QTreeWidgetItem([text])
            it.setIcon(0, icon(ic))
            it.setData(0, SCOPE_ROLE, scope)
            self._tree.addTopLevelItem(it)
            if scope == self._scope:
                current = it
        items: dict[str, QTreeWidgetItem] = {}
        pending = list(self.collections)
        # Parents before children, whatever order they were saved in.
        while pending:
            progressed = False
            for c in list(pending):
                if c.parent and c.parent not in items and any(
                        p.id == c.parent for p in pending):
                    continue
                parent = items.get(c.parent)
                it = QTreeWidgetItem([c.name])
                it.setIcon(0, icon("collection"))
                it.setData(0, SCOPE_ROLE, c.id)
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                (parent.addChild(it) if parent else self._tree.addTopLevelItem(it))
                items[c.id] = it
                pending.remove(c)
                progressed = True
                if c.id == self._scope:
                    current = it
            if not progressed:
                break
        self._tree.expandAll()
        if current is None:
            self._scope = ALL
            current = self._tree.topLevelItem(0)
        self._tree.setCurrentItem(current)
        self._tree.blockSignals(False)
        self._apply_scope()

    def _scope_changed(self, item, _prev=None) -> None:
        if item is None:
            return
        self._scope = item.data(0, SCOPE_ROLE)
        self._apply_scope()

    def _apply_scope(self) -> None:
        if self._scope in (ALL, UNFILED, REVIEW):
            self._proxy.set_scope(self._scope)
        else:
            self._proxy.set_scope(self._scope, store.collection_and_descendants(
                self.collections, self._scope))
        self._update_status()

    def _tree_menu(self, pos) -> None:
        if self.library is None:
            return
        it = self._tree.itemAt(pos)
        scope = it.data(0, SCOPE_ROLE) if it else ""
        menu = QMenu(self)
        menu.addAction(icon("collection_new"), "New collection…",
                       lambda: self._new_collection(""))
        if scope not in ("", ALL, UNFILED, REVIEW):
            menu.addAction(icon("collection_new"), "New subcollection…",
                           lambda: self._new_collection(scope))
            menu.addAction(icon("rename"), "Rename",
                           lambda: self._tree.editItem(it, 0))
            menu.addAction(icon("delete"), "Delete collection…",
                           lambda: self._delete_collection(scope))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _new_collection(self, parent: str) -> None:
        name, ok = QInputDialog.getText(self, "New collection", "Name:")
        if not ok or not name.strip():
            return
        c = store.Collection(store.new_collection_id(), name.strip(), parent)
        self.collections.append(c)
        store.save_collections(self.library, self.collections)
        self._commit(f"New collection {c.name}")
        self._scope = c.id
        self._rebuild_tree()

    def _collection_renamed(self, item, _col) -> None:
        cid = item.data(0, SCOPE_ROLE)
        for c in self.collections:
            if c.id == cid and item.text(0).strip() and c.name != item.text(0).strip():
                old, c.name = c.name, item.text(0).strip()
                store.save_collections(self.library, self.collections)
                self._commit(f"Rename collection {old} to {c.name}")

    def _delete_collection(self, cid: str) -> None:
        ids = store.collection_and_descendants(self.collections, cid)
        name = next((c.name for c in self.collections if c.id == cid), "")
        if QMessageBox.question(
                self, "Delete collection",
                f"Delete the collection “{name}”"
                f"{' and its subcollections' if len(ids) > 1 else ''}?\n\n"
                "The references stay in the library.") != QMessageBox.Yes:
            return
        self.collections = [c for c in self.collections if c.id not in ids]
        store.save_collections(self.library, self.collections)
        for e in self.entries.values():
            if ids.intersection(e.collections):
                e.collections = [c for c in e.collections if c not in ids]
                store.save_entry(self.library, e)
        self._commit(f"Delete collection {name}")
        self._scope = ALL
        self._reload()

    def _add_keys_to_collection(self, keys: list[str], cid: str) -> None:
        name = next((c.name for c in self.collections if c.id == cid), "")
        changed = 0
        for k in keys:
            e = self.entries.get(k)
            if e is not None and cid not in e.collections:
                e.collections.append(cid)
                store.save_entry(self.library, e)
                changed += 1
        if changed:
            self._commit(f"Add {changed} reference{'s' * (changed > 1)} to {name}")
            self._reload(keys)
            self.statusBar().showMessage(f"Added {changed} to {name}", 4000)

    def _remove_from_collection(self) -> None:
        cid = self._scope
        ids = store.collection_and_descendants(self.collections, cid)
        keys = self.selected_keys()
        for k in keys:
            e = self.entries[k]
            e.collections = [c for c in e.collections if c not in ids]
            store.save_entry(self.library, e)
        name = next((c.name for c in self.collections if c.id == cid), "")
        self._commit(f"Remove {len(keys)} from {name}")
        self._reload([])

    # ------------------------------------------------------------------ #
    # Table / selection                                                    #
    # ------------------------------------------------------------------ #

    def selected_keys(self) -> list[str]:
        rows = self._table.selectionModel().selectedRows()
        return [self._model.entry(self._proxy.mapToSource(ix).row()).key
                for ix in rows]

    def selected_entries(self) -> list[Entry]:
        return [self.entries[k] for k in self.selected_keys() if k in self.entries]

    def visible_entries(self) -> list[Entry]:
        return [self._model.entry(self._proxy.mapToSource(
            self._proxy.index(r, 0)).row()) for r in range(self._proxy.rowCount())]

    def _select_keys(self, keys: list[str]) -> None:
        sel = self._table.selectionModel()
        sel.clearSelection()
        first = None
        for k in keys:
            row = self._model.row_of(k)
            if row < 0:
                continue
            ix = self._proxy.mapFromSource(self._model.index(row, 0))
            if not ix.isValid():
                continue
            sel.select(ix, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            first = first or ix
        if first is not None:
            self._table.scrollTo(first)
            self._covers.scrollTo(first)
            sel.setCurrentIndex(first, QItemSelectionModel.NoUpdate)

    def _selection_changed(self, *_):
        self._flush_editor()
        entries = self.selected_entries()
        self._editor.set_entry(entries[0] if len(entries) == 1 else None)
        if len(entries) == 1:
            self._editor.set_cover(self._thumbs.pixmap(entries[0]))
        for a in self._selection_actions:
            a.setEnabled(bool(entries))
        self.act_rename_key.setEnabled(len(entries) == 1)
        self._update_status()

    def _entry_at(self, proxy_index) -> Entry | None:
        if not proxy_index.isValid():
            return None
        return self._model.entry(self._proxy.mapToSource(proxy_index).row())

    def _set_view(self, mode: str, remember: bool = True) -> None:
        covers = mode == "covers"
        self._views.setCurrentIndex(1 if covers else 0)
        (self.act_view_covers if covers else self.act_view_list).setChecked(True)
        if remember:
            QSettings(*SETTINGS).setValue("view_mode", "covers" if covers else "list")

    def _thumbnail_ready(self, key: str) -> None:
        self._covers.viewport().update()
        e = self._editor.entry
        if e is not None and e.key == key and not self._editor.is_dirty():
            self._editor.set_cover(self._thumbs.pixmap(e))

    def _search_changed(self, text: str) -> None:
        self._proxy.set_search(text)
        self._update_status()

    def _table_menu(self, global_pos) -> None:
        if not self.selected_keys():
            return
        menu = QMenu(self)
        menu.addActions([self.act_open_file, self.act_show_file])
        menu.addSeparator()
        menu.addActions([self.act_copy_key, self.act_copy_cite, self.act_copy_bib])
        menu.addSeparator()
        if self.collections:
            sub = menu.addMenu(icon("collection"), "Add to collection")
            for c in self.collections:
                sub.addAction(c.name, lambda cid=c.id: self._add_keys_to_collection(
                    self.selected_keys(), cid))
        if self._scope not in (ALL, UNFILED, REVIEW):
            menu.addAction("Remove from this collection", self._remove_from_collection)
        menu.addSeparator()
        menu.addActions([self.act_lookup, self.act_rename_key])
        menu.addSeparator()
        exp = menu.addMenu(icon("export_bib"), "Export selected")
        for label, d in (("BibLaTeX…", "biblatex"), ("Classic BibTeX…", "bibtex"),
                         ("CSL-JSON…", "csl")):
            exp.addAction(label, lambda d=d: self._export(d, selected_only=True))
        menu.addSeparator()
        menu.addAction(self.act_delete)
        menu.exec(global_pos)

    # ------------------------------------------------------------------ #
    # Adding references                                                    #
    # ------------------------------------------------------------------ #

    def _target_collection(self) -> str:
        return self._scope if self._scope not in (ALL, UNFILED, REVIEW) else ""

    def _add_pdfs_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add PDFs", "",
                                                "PDF files (*.pdf)")
        if files:
            self.import_paths([Path(f) for f in files], self._target_collection())

    def _import_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Import every PDF and .bib in "
                                                   "a folder")
        if d:
            self.import_paths([Path(d)], self._target_collection())

    def _import_bib_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import bibliography", "",
            f"Reference files ({importer.IMPORT_PATTERNS});;All files (*)")
        if files:
            self.import_paths([Path(f) for f in files], self._target_collection())

    def _import_zotero(self) -> None:
        from . import zotero
        d = zotero.default_data_dir()
        if not zotero.is_data_dir(d):
            chosen = QFileDialog.getExistingDirectory(
                self, "Zotero data folder (the one holding zotero.sqlite)",
                str(Path.home()))
            if not chosen:
                return
            d = Path(chosen)
            if not zotero.is_data_dir(d):
                QMessageBox.warning(self, "Import from Zotero",
                                    f"There is no zotero.sqlite in {d}.\n\n"
                                    "Zotero ▸ Settings ▸ Advanced ▸ Files and "
                                    "Folders shows where it is.")
                return
        elif QMessageBox.question(
                self, "Import from Zotero",
                f"Import the Zotero library in {d}?\n\nReferences, PDFs, "
                "collections, tags and notes are copied; Zotero itself is "
                "not changed.") != QMessageBox.Yes:
            return
        self._start_import(ImportJob(self.library, zotero_dir=d))

    def _add_identifiers(self) -> None:
        from .add_dialog import AddByIdentifierDialog
        dlg = AddByIdentifierDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.requests():
            self._start_import(ImportJob(self.library, identifiers=dlg.requests(),
                                         collection=self._target_collection()))

    def _import_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        from . import formats
        if ("@" in text and "{" in text and "=" in text) or \
                formats.looks_like_ris(text) or formats.looks_like_nbib(text) or \
                formats.looks_like_endnote(text):
            self._start_import(ImportJob(self.library, bib_text=text,
                                         collection=self._target_collection()))
            return
        idents = [l.strip() for l in text.splitlines() if l.strip()]
        self._start_import(ImportJob(self.library, identifiers=idents,
                                     collection=self._target_collection()))

    def _paste(self) -> None:
        if self.library is None:
            return
        md = QApplication.clipboard().mimeData()
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self.import_paths([Path(u.toLocalFile()) for u in md.urls()
                               if u.isLocalFile()], self._target_collection())
        elif md.hasText():
            self._import_text(md.text())

    def import_paths(self, paths: list[Path], collection: str = "") -> None:
        if self.library is None or not paths:
            return
        self._start_import(ImportJob(self.library, paths=paths,
                                     collection=collection))

    def _new_reference(self) -> None:
        if self.library is None:
            return
        self._flush_editor()
        self._table.selectionModel().clearSelection()
        e = Entry(type="article")
        cid = self._target_collection()
        if cid:
            e.collections.append(cid)
        self._editor.set_entry(e)

    def _start_import(self, job) -> None:
        if self._job is not None:
            QMessageBox.information(self, "Busy", "An import is still running.")
            return
        self._flush_editor()
        if isinstance(job, ImportJob):
            job.online = self.act_online.isChecked()
        self._job = job
        self._progress = QProgressDialog("Starting…", "Cancel", 0, 0, self)
        self._progress.setWindowTitle("Importing")
        self._progress.setMinimumDuration(400)
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.canceled.connect(job.cancel)
        job.progress.connect(self._job_progress)
        job.finished_with.connect(self._job_done)
        job.start()

    def _job_progress(self, i: int, n: int, name: str) -> None:
        if self._progress is None:
            return
        self._progress.setMaximum(max(n, 1))
        self._progress.setValue(i)
        if name:
            self._progress.setLabelText(f"{i + 1} of {n}: {name}")

    def _job_done(self, summary: importer.Summary) -> None:
        job, self._job = self._job, None
        job.wait()
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        new_keys = [o.key for o in summary.outcomes if o.key and o.status in (
            importer.ADDED, importer.REVIEW, importer.ATTACHED)]
        if summary.changed:
            what = ("Look up details" if isinstance(job, LookupJob)
                    else f"Import: {summary.headline()}")
            self.entries = store.load_entries(self.library)
            self._commit(what)
        self._reload(new_keys or [o.key for o in summary.outcomes if o.key])
        title = "Look up details" if isinstance(job, LookupJob) else "Import"
        simple = len(summary.outcomes) == 1 and summary.outcomes[0].status in (
            importer.ADDED, importer.ATTACHED)
        if simple:
            o = summary.outcomes[0]
            self.statusBar().showMessage(f"Added {o.key}" + (
                f" — {o.message}" if o.message else ""), 6000)
        elif summary.outcomes:
            SummaryDialog(title, summary, self).exec()
        else:
            self.statusBar().showMessage("Nothing to import", 4000)

    # ----- drag & drop onto the window -----

    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if self.library is not None and not md.hasFormat(KEYS_MIME) and (
                md.hasUrls() or md.hasText()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        md = ev.mimeData()
        paths = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
        if paths:
            self.import_paths(paths, self._target_collection())
        elif md.hasText():
            self._import_text(md.text())
        ev.acceptProposedAction()

    # ----- requests from other apps (see ipc.py) -----

    def handle_request(self, req: dict) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        cmd, paths = req.get("cmd"), [Path(p) for p in req.get("paths", [])]
        if cmd == "open" and paths and library.is_library(paths[0]):
            self.open_library(paths[0])
            return
        if cmd not in ("add", "reveal") or not paths:
            return
        if self.library is None:
            QMessageBox.information(
                self, "KherveRef",
                "Open or create a library first, then try again.")
            return
        if cmd == "add":
            self.import_paths(paths, self._target_collection())
            return
        key = self.find_by_file(paths[0])
        if key:
            self._scope = ALL
            self._search.clear()
            self._rebuild_tree()
            self._select_keys([key])
        elif QMessageBox.question(
                self, "KherveRef",
                f"{paths[0].name} is not in “{self.library.name}”. "
                "Add it?") == QMessageBox.Yes:
            self.import_paths(paths[:1], self._target_collection())

    def find_by_file(self, path: Path) -> str | None:
        """The reference whose attachment is *path* (or an identical copy)."""
        path = Path(path)
        try:
            rel = path.resolve().relative_to(self.library.root.resolve()).as_posix()
        except ValueError:
            rel = None
        for e in self.entries.values():
            if rel and any(a.path == rel for a in e.files):
                return e.key
        try:
            sha1 = store.sha1_of(path)
        except OSError:
            return None
        return store.DuplicateIndex.build(self.entries.values()).find(Entry(), sha1)

    # ------------------------------------------------------------------ #
    # Reference actions                                                    #
    # ------------------------------------------------------------------ #

    def _copy_keys(self) -> None:
        keys = self.selected_keys()
        if keys:
            QApplication.clipboard().setText(", ".join(keys))
            self.statusBar().showMessage(f"Copied {', '.join(keys)}", 3000)

    def _copy_cite(self) -> None:
        keys = self.selected_keys()
        if keys:
            QApplication.clipboard().setText("\\cite{" + ",".join(keys) + "}")

    def _copy_bib(self, dialect: str) -> None:
        entries = self.selected_entries()
        if entries:
            QApplication.clipboard().setText(bibtex.to_bibtex(entries, dialect))
            self.statusBar().showMessage(
                f"Copied {len(entries)} entr{'ies' if len(entries) > 1 else 'y'}",
                3000)

    def _delete_selected(self) -> None:
        entries = self.selected_entries()
        if not entries:
            return
        n = len(entries)
        what = entries[0].key if n == 1 else f"{n} references"
        if QMessageBox.question(
                self, "Delete",
                f"Delete {what} and the attached files?\n\n"
                "The library's Git history keeps a copy.") != QMessageBox.Yes:
            return
        self._editor.set_entry(None)
        for e in entries:
            store.delete_entry(self.library, e)
            self.entries.pop(e.key, None)
        self._commit(f"Delete {what}")
        self._reload([])

    def _rename_key(self) -> None:
        entries = self.selected_entries()
        if len(entries) != 1:
            return
        e = entries[0]
        new, ok = QInputDialog.getText(
            self, "Rename key",
            "New citation key.\n\nDocuments that already cite "
            f"“{e.key}” will need updating.", text=e.key)
        new = new.strip()
        if not ok or not new or new == e.key:
            return
        if not is_valid_key(new):
            QMessageBox.warning(self, "Rename key",
                                "Use letters, digits and - _ : . + only.")
            return
        if new.lower() in {k.lower() for k in self.entries if k != e.key}:
            QMessageBox.warning(self, "Rename key", f"“{new}” is already used.")
            return
        old = e.key
        store.entry_path(self.library, old).unlink(missing_ok=True)
        self.entries.pop(old, None)
        e.key = new
        store.save_entry(self.library, e)
        self.entries[new] = e
        self._commit(f"Rename key {old} to {new}")
        self._reload([new])

    def _open_first_file(self) -> None:
        entries = self.selected_entries()
        if entries and entries[0].files:
            self._open_file(entries[0], 0)
        elif entries:
            e = entries[0]
            url = f"https://doi.org/{e.doi}" if e.doi else e.url
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                self.statusBar().showMessage("No file or link for this reference",
                                             3000)

    def _open_file(self, e: Entry, index: int) -> None:
        path = store.file_path(self.library, e.files[index])
        if not path.exists():
            QMessageBox.warning(self, "Open", f"{path} is missing.")
            return
        links.open_document(path, self)

    def _annotations_to_notes(self) -> None:
        from .pdf_meta import annotations_as_notes
        changed = []
        for e in self.selected_entries():
            for att in e.files:
                path = store.file_path(self.library, att)
                if path.suffix.lower() != ".pdf" or not path.exists():
                    continue
                text = annotations_as_notes(path)
                if text and text not in e.notes:
                    e.notes = (e.notes.rstrip() + "\n\n" + text).strip()
                    store.save_entry(self.library, e)
                    changed.append(e.key)
        if changed:
            self._commit(f"Notes from PDF annotations: {', '.join(changed)}")
            self._reload(changed)
        self.statusBar().showMessage(
            f"Copied annotations of {len(changed)} PDF(s)" if changed
            else "No new annotations found", 5000)

    def _locate_khervepdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate KhervePDF", "",
            "KhervePDF (KhervePDF.app KhervePDF.exe KhervePDF.py KhervePDF);;"
            "All files (*)")
        if not path:
            return
        if path.endswith(".app/Contents/MacOS/KhervePDF"):
            path = path[:-len("/Contents/MacOS/KhervePDF")]
        if links.set_custom_path(path):
            self.statusBar().showMessage(f"PDFs open in {path}", 5000)
        else:
            QMessageBox.warning(self, "Locate KhervePDF",
                                f"{path} cannot be run as KhervePDF.")

    def _show_in_folder(self) -> None:
        entries = self.selected_entries()
        if entries and entries[0].files:
            links.reveal(store.file_path(self.library, entries[0].files[0]))

    def _attach_to(self, e: Entry) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, f"Attach to {e.key}")
        if not files or e.key not in self.entries:
            return
        current = self.entries[e.key]
        for f in files:
            store.attach_file(self.library, current, Path(f))
        store.save_entry(self.library, current)
        self._commit(f"Attach {len(files)} file(s) to {e.key}")
        self._editor.set_entry(current)
        self._model.update_entry(current)

    def _remove_file(self, e: Entry, index: int) -> None:
        current = self.entries.get(e.key)
        if current is None or index >= len(current.files):
            return
        att = current.files[index]
        if QMessageBox.question(self, "Remove file",
                                f"Remove {att.path} from {e.key}?") != QMessageBox.Yes:
            return
        current.files.pop(index)
        if att.path.startswith(library.FILES_DIR + "/"):
            (self.library.root / att.path).unlink(missing_ok=True)
        store.save_entry(self.library, current)
        self._commit(f"Remove {att.path} from {e.key}")
        self._editor.set_entry(current)
        self._model.update_entry(current)

    def _lookup_in_editor(self, e: Entry) -> None:
        if not e.key:
            QMessageBox.information(self, "Look up", "Save the reference first.")
            return
        store.save_entry(self.library, e)
        self.entries[e.key] = e
        self._start_import(LookupJob(self.library, [e.key]))

    def _lookup_selected(self) -> None:
        keys = self.selected_keys()
        if keys:
            self._start_import(LookupJob(self.library, keys))

    def _lookup_all_review(self) -> None:
        keys = [k for k, e in self.entries.items() if e.needs_review]
        if not keys:
            self.statusBar().showMessage("No reference needs checking", 3000)
            return
        self._start_import(LookupJob(self.library, keys))

    def _export(self, dialect: str, selected_only: bool = False) -> None:
        if self.library is None:
            return
        sel = self.selected_entries()
        entries = sel if selected_only or len(sel) > 1 else self.visible_entries()
        if not entries:
            QMessageBox.information(self, "Export", "No references to export.")
            return
        ext = "json" if dialect == "csl" else "bib"
        label = {"biblatex": "BibLaTeX", "bibtex": "BibTeX", "csl": "CSL-JSON"}[dialect]
        last = QSettings(*SETTINGS).value("export_dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {len(entries)} references as {label}",
            str(Path(last) / f"{self.library.name}.{ext}"),
            f"{label} (*.{ext})")
        if not path:
            return
        if dialect == "csl":
            text = json.dumps([csl.to_csl(e) for e in sorted(
                entries, key=lambda e: e.key.lower())], indent=2,
                ensure_ascii=False) + "\n"
        else:
            text = bibtex.to_bibtex(entries, dialect)
        Path(path).write_text(text, encoding="utf-8")
        QSettings(*SETTINGS).setValue("export_dir", str(Path(path).parent))
        self.statusBar().showMessage(f"Exported {len(entries)} references to {path}",
                                     6000)

    # ------------------------------------------------------------------ #
    # Git                                                                  #
    # ------------------------------------------------------------------ #

    def _show_history(self) -> None:
        if self.library:
            HistoryDialog(self.library.root, self).exec()

    def _set_remote(self) -> None:
        if not self.library:
            return
        current = git_backend.get_remote(self.library.root) or ""
        url, ok = QInputDialog.getText(
            self, "Library remote",
            "Git remote URL (e.g. https://github.com/you/my-library.git):",
            text=current)
        if ok and url.strip():
            if not git_backend.set_remote(self.library.root, url.strip()):
                QMessageBox.warning(self, "Library remote",
                                    "Could not set the remote.")
            self._update_status()

    def _run_git(self, fn, label: str) -> None:
        if not self.library or self._git_job is not None:
            return
        self._flush_editor()
        if git_backend.get_remote(self.library.root) is None:
            QMessageBox.information(self, label,
                                    "Set a remote first (Library ▸ Set remote…).")
            return
        self.statusBar().showMessage(f"{label}…")
        self.act_pull.setEnabled(False)
        self.act_push.setEnabled(False)
        job = _GitJob(fn, self.library.root, self)
        job.done.connect(lambda ok, msg: self._git_done(label, ok, msg))
        self._git_job = job
        job.start()

    def _git_done(self, label: str, ok: bool, msg: str) -> None:
        self._git_job.wait()
        self._git_job = None
        self.statusBar().showMessage(f"{label}: {'done' if ok else 'failed'}",
                                     5000)
        if not ok:
            QMessageBox.warning(self, label, msg)
        if label == "Pull" and ok:
            self._reload()
            store.write_library_bib(self.library, self.entries.values())
        self._refresh()

    # ------------------------------------------------------------------ #
    # View                                                                 #
    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        lib = self.library
        for a in self._library_actions:
            a.setEnabled(lib is not None)
        self._search.setEnabled(lib is not None)
        has_sel = lib is not None and bool(self.selected_keys())
        for a in self._selection_actions:
            a.setEnabled(has_sel)
        name = lib.name if lib else "no library"
        self.setWindowTitle(f"KherveRef {version_string()} — {name}")
        self._stack.setCurrentIndex(0 if lib is None else 1)
        if lib is not None and self._tree.topLevelItemCount() == 0:
            self._rebuild_tree()
        self._update_status()

    def _update_status(self) -> None:
        lib = self.library
        if lib is None:
            self._status.setText("")
            return
        n, shown = len(self.entries), self._proxy.rowCount()
        sel = len(self.selected_keys())
        branch = git_backend.current_branch(lib.root) or "no commits"
        remote = git_backend.get_remote(lib.root) or "no remote"
        parts = [f"{n} reference{'s' if n != 1 else ''}"]
        if shown != n:
            parts.append(f"{shown} shown")
        if sel > 1:
            parts.append(f"{sel} selected")
        parts += [branch, remote]
        self._status.setText("  ·  ".join(parts))

    def _apply_theme_qss(self) -> None:
        t = self._theme
        self._status.setStyleSheet(themes.status_label_stylesheet(t))
        # Flat, rounded, airy: the look of current desktop apps.
        self.setStyleSheet(f"""
            QToolBar#main_toolbar {{
                background: {t['surface']}; border: none;
                border-bottom: 1px solid {t['page_border']};
                padding: 6px 10px; spacing: 4px;
            }}
            QToolBar#main_toolbar QToolButton {{
                border: none; border-radius: 7px; padding: 6px 10px;
                color: {t['text']};
            }}
            QToolBar#main_toolbar QToolButton:hover {{ background: {t['tab_hover']}; }}
            QToolBar#main_toolbar QToolButton:pressed,
            QToolBar#main_toolbar QToolButton:checked {{ background: {t['alt_base']}; }}
            QToolBar#main_toolbar QLineEdit {{
                border: 1px solid {t['page_border']}; border-radius: 15px;
                padding: 5px 10px; background: {t['base']}; color: {t['text']};
            }}
            QToolBar#main_toolbar QLineEdit:focus {{ border: 1px solid {t['accent']}; }}
            QTreeWidget, QTableView, QListView {{
                border: none; background: {t['base']};
                selection-background-color: {t['highlight']};
                selection-color: {t['highlight_text']};
            }}
            QTreeWidget {{ outline: 0; show-decoration-selected: 0; }}
            QTreeWidget::item {{
                padding: 5px 4px; border-radius: 6px; color: {t['text']};
            }}
            QTreeWidget::item:hover {{ background: {t['tab_hover']}; }}
            QTreeWidget::item:selected {{
                background: {t['highlight']}; color: {t['highlight_text']};
            }}
            QTreeWidget::branch:selected {{ background: transparent; }}
            QTableView {{ gridline-color: transparent; }}
            QHeaderView::section {{
                background: {t['base']}; color: {t['text_muted']};
                border: none; border-bottom: 1px solid {t['page_border']};
                padding: 6px 6px; font-weight: 600;
            }}
            QSplitter::handle {{ background: {t['page_border']}; }}
            QSplitter::handle:horizontal {{ width: 1px; }}
        """)
        if hasattr(self, "_table"):
            self._table.setShowGrid(False)
            self._table.verticalHeader().setDefaultSectionSize(30)

    def _set_theme(self, name: str) -> None:
        self._theme_name = name
        self._theme = themes.apply_theme(QApplication.instance(), name)
        icons.set_icon_color(themes.icon_color(self._theme))
        icons.set_accent_color(self._theme["accent"])
        self._apply_theme_qss()
        self._retint_icons()
        QSettings(*SETTINGS).setValue("theme_name", name)

    def _retint_icons(self) -> None:
        from PySide6.QtWidgets import QAbstractButton
        targets = list(self.findChildren(QAction)) + \
            list(self.findChildren(QAbstractButton))
        todo = [(o, s) for o in targets
                if (s := icons.icon_spec(o.icon())) is not None]
        icons.forget_icon_specs()
        for obj, (name, color) in todo:
            obj.setIcon(icon(name, color=color))
        if self.library is not None:
            self._rebuild_tree()
            self._model.layoutChanged.emit()

    def _show_mcp_help(self) -> None:
        import sys as _sys
        if getattr(_sys, "frozen", False):
            cmd = [_sys.executable, "--mcp-server"]
        else:
            cmd = [_sys.executable, "-m", "kherveref.mcp_server"]
        lib = ["--library", str(self.library.root)] if self.library else []
        cfg = json.dumps({"mcpServers": {"kherveref": {
            "command": cmd[0], "args": cmd[1:] + lib}}}, indent=2)
        cli = "claude mcp add kherveref -- " + " ".join(
            f'"{c}"' if " " in c else c for c in cmd + lib)
        box = QMessageBox(self)
        box.setWindowTitle("Use KherveRef with Claude")
        box.setTextFormat(Qt.RichText)
        box.setText(
            "Claude can search, add, edit and export your references through "
            "KherveRef's MCP server. It works on the library folder directly, "
            "so this window need not be open.<br><br>"
            "<b>Claude Code</b> — run:<br><code>" + cli.replace("&", "&amp;")
            .replace("<", "&lt;") + "</code><br><br>"
            "<b>Claude Desktop</b> — add to claude_desktop_config.json:"
            "<pre>" + cfg.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        copy = box.addButton("Copy Claude Code command", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is copy:
            QApplication.clipboard().setText(cli)

    def _about(self) -> None:
        QMessageBox.about(
            self, "About KherveRef",
            f"<b>KherveRef</b> {version_string()}<br><br>"
            "A reference manager for KherveTeX and KhervePDF, with "
            "BibLaTeX export and Git-synced libraries.<br><br>"
            "© 2026 Gwilherm Kerherve — GPL-3.0")

    def closeEvent(self, event) -> None:
        self._flush_editor()
        self._thumbs.stop()
        if self._job is not None:
            self._job.cancel()
            self._job.wait(10000)
        if self._git_job is not None:
            self._git_job.wait(5000)
        super().closeEvent(event)
