"""Main window: collections on the left, references in the middle,
details on the right; a start page when no library is open."""
from __future__ import annotations

import datetime
import html
import json
import re
from pathlib import Path

from PySide6.QtCore import (QEvent, QFileSystemWatcher, QItemSelectionModel,
                            QSettings,
                            QSize, Qt, QThread, QTimer, QUrl, Signal)
from PySide6.QtCore import QMimeData
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices,
                           QIcon, QKeySequence)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QTabWidget, QTableView, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import (bibtex, cite, csl, git_backend, icons, importer, library, links,
               state, store, themes, word_addin, word_server, word_sources)
from . import version_string
from .ai_panel import AIPanel, AISetupDialog, AskLibraryDialog
from .covers import CoversView
from .editor import EntryEditor
from .icons import icon
from .jobs import ImportJob, LookupJob, SummaryDialog
from .keys import KEY_STYLES, is_valid_key
from .model import Entry
from .table_model import (ALL, COL_KEY, COL_STATUS, COL_TITLE, KEYS_MIME,
                          REVIEW, UNFILED, RefFilterProxy, RefTableModel)
from .thumbnails import Thumbnails

SETTINGS = ("kherve", "KherveRef")
MAX_RECENT = 8
SCOPE_ROLE = Qt.UserRole


# Icon-only buttons: each tooltip says what the button does and how to use it.
TOOLBAR_TIPS = {
    "act_add_pdfs": "Choose one or more PDF files to add to the library. KherveRef "
                    "reads each one and looks up its title, authors, journal and "
                    "DOI online. You can also drag PDFs onto the window.",
    "act_import_folder": "Choose a folder: every PDF and .bib file in it and its "
                         "subfolders is added. You see the list first and confirm. "
                         "You can also drag a folder onto the window.",
    "act_add_id": "Add a paper or book without its PDF: type or paste its DOI, "
                  "arXiv number, ISBN or publisher link (several, one per line) "
                  "and KherveRef fetches the details.",
    "act_delete": "Delete the selected references and their PDFs. Click a "
                  "reference first (⇧ or ⌘ to select several). Undo brings "
                  "them back.",
    "act_undo": "Undo the last change: an import, an edit, a deletion. "
                "Redo is in the Edit menu.",
    "act_redo": "Redo the change you just undid.",
    "act_export_biblatex": "Save references as a .bib file for LaTeX: the ones "
                           "selected (two or more), otherwise every reference "
                           "shown. KherveTeX doesn't need this: it reads the "
                           "library directly.",
    "act_pull": "Bring in the changes made on your other computers, from the "
                "library's Git remote (set it in Library ▸ Set remote…).",
    "act_push": "Send the changes made here to the library's Git remote, so your "
                "other computers can Pull them.",
    "act_view_list": "Show the references as a table. Click a column heading to "
                     "sort; the selected reference's details appear on the right.",
    "act_view_covers": "Show each reference as the front page of its PDF. Click to "
                       "select and see its details; double-click to open the PDF.",
}


def _tooltip_with_shortcut(a: QAction) -> None:
    """Icon-only buttons say what they do: "Add PDFs (⇧⌘P)", plus the
    longer explanation when the action has one."""
    name = a.text().replace("&", "").rstrip("…").strip()
    if not name:
        return
    keys = a.shortcut().toString(QKeySequence.NativeText)
    head = f"{name} ({keys})" if keys else name
    tip = a.toolTip()
    a.setToolTip(head if not tip or tip.replace("&", "") == a.text().replace("&", "")
                 # A fixed-width table: Qt otherwise wraps a long tip
                 # into a narrow column.
                 else f"<table width=340><tr><td><b>{html.escape(head)}</b>"
                      f"<br>{html.escape(tip)}</td></tr></table>")


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
        # What the Word panel sees: replaced (never mutated) on each change,
        # because the service reads it from its own thread.
        self._api_snapshot: tuple = (None, {})
        self._word_server = word_server.start(lambda: self._api_snapshot)
        self._thumbs.ready.connect(self._thumbnail_ready)
        self.entries: dict[str, Entry] = {}
        self.collections: list[store.Collection] = []
        self._git_job: _GitJob | None = None
        self._job: QThread | None = None
        self._progress: QProgressDialog | None = None
        self._scope = ALL
        # Undo / Redo: (snapshot before, snapshot after, description) of
        # each change made in this window; see git_backend.apply_change.
        self._undo: list[tuple[str, str, str]] = []
        self._redo: list[tuple[str, str, str]] = []

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
        QApplication.instance().installEventFilter(self)
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

        self._model = RefTableModel(self, thumb_path=self._thumbs.path,
                                    formatted=self._formatted_citation)
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
            lambda pos: self._context_menu(self._table, pos))

        self._covers = CoversView(self._thumbs, self._entry_at)
        self._covers.setModel(self._proxy)
        self._covers.setSelectionModel(self._table.selectionModel())
        self._covers.doubleClicked.connect(lambda _ix: self._open_first_file())
        self._covers.setContextMenuPolicy(Qt.CustomContextMenu)
        self._covers.customContextMenuRequested.connect(
            lambda pos: self._context_menu(self._covers, pos))
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
        self._editor.rename_requested.connect(self._rename_key)
        self._editor.copy_cite_requested.connect(self._copy_cite)

        split = QSplitter()
        split.addWidget(self._tree)
        split.addWidget(self._views)
        self._ai_panel = AIPanel(lambda: self.library)
        self._ai_panel.add_to_notes.connect(self._append_notes)
        self._ai_panel.saved.connect(self._ai_saved)
        self._ai_panel.reveal.connect(self._reveal_key)
        self._side = QTabWidget()
        self._side.setDocumentMode(True)
        self._side.addTab(self._editor, "Details")
        self._side.addTab(self._ai_panel, icon("ai"), "AI")
        split.addWidget(self._side)
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
        self.act_copy_citation = A("Copy formatted &citation", self._copy_citation,
                                   "copy", "Ctrl+Alt+C",
                                   "e.g. (Smith et al., 2020) — paste into Word")
        self.act_copy_reference = A("Copy formatted r&eference",
                                    self._copy_reference, None, "Ctrl+Alt+R",
                                    "The reference-list entry, in the chosen style")
        self.act_find_style = A("&Find a journal style…", self._find_journal_style,
                                "find", None,
                                "Search the ~10,000 journal styles used by Zotero "
                                "and Mendeley, and add the one you need")
        self.act_word_sync = QAction("Keep &Word's source list up to date", self,
                                     checkable=True)
        self.act_word_sync.setChecked(
            QSettings(*SETTINGS).value("word_sync", False, type=bool))
        self.act_word_sync.toggled.connect(self._toggle_word_sync)
        self.act_word_send = A("Send library to Word &now", self._send_to_word,
                               "word")
        self.act_word_help = A("Using KherveRef with Word…", self._word_help)
        self.act_word_open_help = A("How to &open the panel in Word…",
                                    self._open_word_panel_help)
        self.act_word_panel = A("&Install the KherveRef panel in Word",
                                self._install_word_panel, "word")
        self.act_word_panel_remove = A("Remove the panel from Word",
                                       self._remove_word_panel)
        self.act_paste = A("&Paste DOIs / BibTeX", self._paste, None,
                           QKeySequence.Paste)
        self.act_delete = A("&Delete reference…", self._delete_selected, "delete",
                            QKeySequence.Delete)
        self.act_undo = A("&Undo", self._undo_last, "revert", QKeySequence.Undo)
        self.act_redo = A("&Redo", self._redo_last, "redo", QKeySequence.Redo)
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
        self._key_style_group = QActionGroup(self)
        self._key_style_actions = []
        for sid, label in KEY_STYLES.items():
            a = QAction(label, self, checkable=True)
            a.triggered.connect(lambda _=False, s=sid: self._set_key_style(s))
            self._key_style_group.addAction(a)
            self._key_style_actions.append((sid, a))
        self.act_rekey_all = A("Rename all keys to this style…", self._rekey_all,
                               "rename")
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
        self.act_claude = A("&Connect to Claude (MCP)…", self._show_mcp_help, "claude",
                            None, "Let Claude search, add, edit and export "
                                  "your references")
        self.act_ai_summary = A("&Summarise this paper", self._ai_summarise, "ai",
                                "Ctrl+Shift+S", "Your local AI summarises the "
                                "selected paper (aim, methods, findings…)")
        self.act_ai_ask = A("&Ask the library…", self._ai_ask_library, "ai",
                            "Ctrl+Shift+A", "A question answered from the PDFs "
                            "of your references, with sources")
        self.act_ai_settings = A("Set &up local AI…",
                                 lambda: AISetupDialog(self).exec(), "settings", None,
                                 "Install Ollama and a model, and choose which "
                                 "model KherveRef uses")
        self.act_guide = A("KherveRef &User Guide", self._open_guide, "about",
                           QKeySequence.HelpContents)

        self._library_actions = [
            self.act_close, self.act_add_pdfs, self.act_import_folder,
            self.act_add_id, self.act_import_bib, self.act_import_zotero,
            self.act_new_ref,
            self.act_export_biblatex, self.act_export_bibtex, self.act_export_csl,
            self.act_paste, self.act_find, self.act_lookup_review,
            self.act_new_collection, self.act_history, self.act_remote,
            self.act_ai_ask,
            self.act_pull, self.act_push]
        self._selection_actions = [
            self.act_copy_key, self.act_copy_cite, self.act_copy_bib,
            self.act_copy_citation, self.act_copy_reference,
            self.act_delete, self.act_open_file, self.act_show_file,
            self.act_lookup, self.act_rename_key, self.act_annotations,
            self.act_ai_summary]

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
        m.addActions([self.act_undo, self.act_redo])
        m.addSeparator()
        m.addActions([self.act_copy_citation, self.act_copy_reference])
        self._style_menu = m.addMenu("Citation st&yle")
        self._rebuild_style_menu()
        m.addSeparator()
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

        m = mb.addMenu("&Word")
        m.addActions([self.act_word_panel, self.act_word_open_help,
                      self.act_word_panel_remove])
        m.addSeparator()
        m.addActions([self.act_word_sync, self.act_word_send])
        m.addSeparator()
        m.addAction(self.act_word_help)

        m = mb.addMenu("&Library")
        m.addAction(self.act_new_collection)
        m.addSeparator()
        m_keys = m.addMenu("Citation &key style")
        m_keys.addActions([a for _sid, a in self._key_style_actions])
        m_keys.addSeparator()
        m_keys.addAction(self.act_rekey_all)
        m.addSeparator()
        m.addAction(self.act_history)
        m.addSeparator()
        m.addActions([self.act_remote, self.act_pull, self.act_push])

        m = mb.addMenu("&AI")
        m.addActions([self.act_ai_summary, self.act_ai_ask])
        m.addSeparator()
        m.addAction(self.act_ai_settings)
        m.addAction(self.act_claude)

        self._help_menu = mb.addMenu("&Help")
        self._help_menu.addAction(self.act_guide)
        self._help_menu.addSeparator()
        self._help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        # Icon-only, 28 px, names in tooltips: the KherveCAD toolbar.
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setIconSize(QSize(28, 28))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for a in (self.act_add_pdfs, self.act_import_folder, self.act_add_id):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_delete)
        tb.addAction(self.act_undo)
        tb.addSeparator()
        tb.addAction(self.act_export_biblatex)
        tb.addSeparator()
        tb.addActions([self.act_pull, self.act_push])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addActions([self.act_view_list, self.act_view_covers])
        tb.addSeparator()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search authors, titles, keys, DOIs…")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icon("find"), QLineEdit.LeadingPosition)
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._search_changed)
        tb.addWidget(self._search)
        self._search.setToolTip(
            "Type to show only matching references: authors, title, journal, "
            "year, citation key or DOI. Clear it to see everything again.")
        self.addToolBar(tb)
        for a, tip in TOOLBAR_TIPS.items():
            getattr(self, a).setToolTip(tip)
        for a in tb.actions():
            _tooltip_with_shortcut(a)

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
        self._undo.clear()
        self._redo.clear()
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
        self._undo.clear()
        self._redo.clear()
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
        before = git_backend.head_tree(self.library.root)
        store.write_library_bib(self.library, self.entries.values())
        git_backend.commit_all(self.library.root, message)
        after = git_backend.head_tree(self.library.root)
        if before and after and before != after:
            self._undo.append((before, after, message))
            self._redo.clear()
        self._update_undo_actions()
        self._update_status()
        if self.act_word_sync.isChecked():
            self._send_to_word(quiet=True)

    # ----- undo / redo -----

    def _update_undo_actions(self) -> None:
        for act, stack, word in ((self.act_undo, self._undo, "Undo"),
                                 (self.act_redo, self._redo, "Redo")):
            act.setEnabled(self.library is not None and bool(stack))
            what = stack[-1][2] if stack else ""
            act.setText(f"&{word} {what[:50]}" if what else f"&{word}")
            tip = TOOLBAR_TIPS.get(f"act_{word.lower()}")
            act.setToolTip(tip or word)
            _tooltip_with_shortcut(act)

    def _undo_last(self) -> None:
        self._replay(self._undo, self._redo, "Undo")

    def _redo_last(self) -> None:
        self._replay(self._redo, self._undo, "Redo")

    def _replay(self, source, target, word: str) -> None:
        if self.library is None or not source:
            return
        self._flush_editor()
        before, after, what = source[-1]
        # Undo goes after -> before; Redo before -> after.
        start, end = (after, before) if word == "Undo" else (before, after)
        root = self.library.root
        paths = git_backend.change_paths(root, start, end)
        if not git_backend.can_apply_change(root, start, paths):
            source.clear()
            self._update_undo_actions()
            QMessageBox.information(
                self, word,
                f"“{what}” can't be {word.lower()}ne any more: the "
                "references it touched were changed since (from another "
                "window, Claude, or a sync). Library ▸ History lists every "
                "change.")
            return
        source.pop()
        if git_backend.apply_change(root, start, end, f"{word}: {what}") is None:
            QMessageBox.warning(self, word, f"Could not {word.lower()} “{what}”.")
            return
        target.append((before, after, what))
        self._reload([])
        self._update_undo_actions()
        self.statusBar().showMessage(f"{word}: {what}", 5000)

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
            menu.addAction(icon("export_bib"), "Export as .bib for LaTeX…",
                           lambda: self._export_collection(scope))
            menu.addAction(icon("delete"), "Delete collection…",
                           lambda: self._delete_collection(scope))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _export_collection(self, cid: str) -> None:
        """A .bib of one collection (and its subcollections), e.g. the
        references of one paper or chapter."""
        self._scope = cid
        self._rebuild_tree()
        self._table.selectionModel().clearSelection()
        self._export("bibtex")

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
        """Keys of the selected references, in view order. Rows are read
        from any selected cell: the Covers view selects one cell (the
        title column), the list whole rows."""
        rows = sorted({ix.row() for ix in
                       self._table.selectionModel().selectedIndexes()})
        return [self._model.entry(self._proxy.mapToSource(
            self._proxy.index(r, 0)).row()).key for r in rows]

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
        self._ai_panel.set_entries(entries)
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

    def _context_menu(self, view, pos) -> None:
        """Right-click acts on the reference under the mouse, selecting
        it first unless it is already part of the selection."""
        ix = view.indexAt(pos)
        if ix.isValid() and ix.row() not in {
                i.row() for i in view.selectionModel().selectedIndexes()}:
            view.selectionModel().select(
                ix, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            view.selectionModel().setCurrentIndex(ix, QItemSelectionModel.NoUpdate)
        self._table_menu(view.viewport().mapToGlobal(pos))

    def _table_menu(self, global_pos) -> None:
        if not self.selected_keys():
            return
        menu = QMenu(self)
        menu.addActions([self.act_open_file, self.act_show_file])
        menu.addSeparator()
        menu.addActions([self.act_copy_citation, self.act_copy_reference])
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

    def import_paths(self, paths: list[Path], collection: str = "",
                     preview: bool = True) -> None:
        if self.library is None or not paths:
            return
        from .import_preview import ImportPreviewDialog, needs_preview
        if preview and needs_preview(paths):
            dlg = ImportPreviewDialog(paths, self)
            if dlg.exec() != QDialog.Accepted:
                return
            paths = dlg.selected_files()
            if not paths:
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

    def eventFilter(self, obj, ev):  # noqa: N802 — Qt override
        """Files and folders dropped anywhere in the window are imported —
        not pasted as a path into whichever text field is under them.
        Only the collection tree handles its own drops."""
        if ev.type() not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if self.library is None or not isinstance(obj, QWidget) or \
                not (obj is self or self.isAncestorOf(obj)) or \
                obj is self._tree or self._tree.isAncestorOf(obj):
            return False
        md = ev.mimeData()
        if md.hasFormat(KEYS_MIME) or not any(u.isLocalFile() for u in md.urls()):
            return False
        if ev.type() == QEvent.Drop:
            self.dropEvent(ev)
        else:
            ev.acceptProposedAction()
        return True

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

    # ----- formatted citations / Word -----

    def citation_style(self) -> str:
        s = QSettings(*SETTINGS).value("citation_style", cite.DEFAULT_STYLE)
        return s if s in cite.all_styles() else cite.DEFAULT_STYLE

    def _set_style(self, style_id: str) -> None:
        QSettings(*SETTINGS).setValue("citation_style", style_id)
        self._rebuild_style_menu()
        self.statusBar().showMessage(
            f"Citation style: {cite.all_styles()[style_id]}", 4000)

    def _rebuild_style_menu(self) -> None:
        """Bundled styles by field, then the journals added here, then
        Find a journal style…; the current one ticked (its group too)."""
        from . import journal_styles
        m = self._style_menu
        m.clear()
        group = QActionGroup(m)
        current = self.citation_style()

        def add(menu, sid, label):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(sid == current)
            a.triggered.connect(lambda _=False, s=sid: self._set_style(s))
            group.addAction(a)

        added = journal_styles.installed()
        if added:
            m.addSection("Your journals")
            for sid, rec in sorted(added.items(), key=lambda kv: kv[1]["title"].lower()):
                add(m, sid, rec["title"])
            m.addSeparator()
        for name, styles in cite.GROUPS.items():
            sub = m.addMenu(("✓ " if current in styles else "") + name)
            for sid, label in styles.items():
                add(sub, sid, label)
        m.addSeparator()
        m.addAction(self.act_find_style)

    def _find_journal_style(self) -> None:
        from .style_dialog import JournalStyleDialog
        dlg = JournalStyleDialog(self)
        dlg.exec()
        if dlg.chosen:
            self._set_style(dlg.chosen)
        else:
            self._rebuild_style_menu()

    def _formatted_citation(self, entries) -> tuple[str, str, str]:
        text = cite.format_citation(entries, self.citation_style())
        html_ = f"<span>{text}</span>"
        rtf = r"{\rtf1\ansi\deff0 " + cite.rtf_escape(text) + "}"
        return text, html_, rtf

    def _copy_citation(self) -> None:
        entries = self.selected_entries()
        if not entries:
            return
        text, html_, rtf = self._formatted_citation(entries)
        md = QMimeData()
        md.setText(text)
        md.setHtml(html_)
        md.setData("text/rtf", rtf.encode("ascii", "replace"))
        QApplication.clipboard().setMimeData(md)
        self.statusBar().showMessage(f"Copied {text}", 4000)

    def _copy_reference(self) -> None:
        entries = self.selected_entries()
        if not entries:
            return
        refs = cite.format_reference(entries, self.citation_style())
        md = QMimeData()
        md.setText("\n".join(cite.to_text(r) for r in refs))
        md.setHtml("".join(f"<p>{r}</p>" for r in refs))
        rtf = r"{\rtf1\ansi\deff0 " + r"\par ".join(
            cite.html_to_rtf(r) for r in refs) + "}"
        md.setData("text/rtf", rtf.encode("ascii", "replace"))
        QApplication.clipboard().setMimeData(md)
        self.statusBar().showMessage(
            f"Copied {len(refs)} reference{'s' * (len(refs) != 1)} "
            f"({cite.all_styles()[self.citation_style()]})", 4000)

    def _toggle_word_sync(self, on: bool) -> None:
        QSettings(*SETTINGS).setValue("word_sync", on)
        if on:
            self._send_to_word()

    def _send_to_word(self, quiet: bool = False) -> None:
        if self.library is None:
            return
        try:
            res = word_sources.sync(self.library.root, self.entries.values())
        except Exception as e:
            if not quiet:
                QMessageBox.warning(self, "Word", f"Could not update Word's "
                                    f"source list:\n{e}")
            return
        msg = (f"Word's source list: {res['written']} reference"
               f"{'s' * (res['written'] != 1)} from {self.library.name}")
        if res.get("fallback"):
            msg = (f"Saved {res['written']} references for Word in "
                   f"{res['path']}")
            if not quiet and not QSettings(*SETTINGS).value(
                    "word_fallback_explained", False, type=bool):
                QSettings(*SETTINGS).setValue("word_fallback_explained", True)
                QMessageBox.information(
                    self, "Word's source list",
                    "macOS doesn't let apps write into Word's own folder, so "
                    f"KherveRef keeps the list in\n{res['path']}\nand updates "
                    "it after every change.\n\nIn Word: References ▸ Citations "
                    "▸ (gear) Citation Source Manager ▸ Browse…, choose that "
                    "file, then copy the references you want to your list. "
                    "The KherveRef panel (Word ▸ Install the KherveRef panel) "
                    "needs none of this.")
        elif word_sources.word_running():
            msg += " — quit and reopen Word to see changes"
        self.statusBar().showMessage(msg, 8000)

    def _install_word_panel(self) -> None:
        try:
            res = word_addin.install()
        except OSError as e:
            QMessageBox.warning(self, "Word panel", str(e))
            return
        if res.done:
            QMessageBox.information(self, "Word panel installed",
                                    "The KherveRef panel is registered with "
                                    f"Word.\n\n{word_addin.OPEN_STEPS}")
            return
        QMessageBox.information(
            self, "One step for you in Finder",
            "macOS doesn't let apps write into Word's add-in folder, but you "
            "can. Two Finder windows have just opened:\n\n"
            f"• “{res.folder.name}” — Word's add-in folder\n"
            f"• {res.manifest.parent} with {res.manifest.name} selected\n\n"
            f"Drag {res.manifest.name} into the “{res.folder.name}” window "
            f"(this is needed only once).\n\nThen:\n{word_addin.OPEN_STEPS}")

    def _open_word_panel_help(self) -> None:
        QMessageBox.information(self, "Open the KherveRef panel in Word",
                                word_addin.OPEN_STEPS + "\n\nNot listed under "
                                "Developer Add-ins? Use Word ▸ Install the "
                                "KherveRef panel in Word first.")

    def _remove_word_panel(self) -> None:
        try:
            word_addin.uninstall()
        except OSError as e:
            QMessageBox.information(self, "Word panel", str(e))
            return
        self.statusBar().showMessage("KherveRef panel removed from Word "
                                     "(restart Word)", 6000)

    def _word_help(self) -> None:
        QMessageBox.information(
            self, "Using KherveRef with Word",
            "Three ways to cite in Microsoft Word:\n\n"
            "1. Drag references from KherveRef into Word, or use Edit ▸ Copy "
            "formatted citation / reference, in the style chosen under Edit ▸ "
            "Citation style.\n\n"
            "2. Turn on Word ▸ Keep Word's source list up to date. Your library "
            "then appears in Word's own References ▸ Insert Citation, and "
            "References ▸ Bibliography builds the reference list in Word's "
            "styles. Word reads the list when it starts: quit and reopen Word "
            "after changes.\n\n"
            "3. The KherveRef panel inside Word inserts live citations and "
            "keeps the bibliography up to date in any of KherveRef's styles. "
            "Install it with Word ▸ Install the KherveRef panel in Word, then "
            "in Word: Home ▸ Add-ins ▸ More Add-ins ▸ My Add-ins tab ▸ "
            "Developer Add-ins ▸ KherveRef (Word ▸ How to open the panel in "
            "Word shows each step).")

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
        self._flush_editor()
        e = entries[0]
        new, ok = QInputDialog.getText(
            self, "Rename key",
            "New citation key — the name LaTeX cites, as \\cite{key}.\n\n"
            f"Documents that already cite “{e.key}” will need updating "
            "(Edit ▸ Undo puts it back).", text=e.key)
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
        store.rename_keys(self.library, self.entries, {old: new})
        self._commit(f"Rename key {old} to {new}")
        self._reload([new])

    def _sync_key_style_menu(self) -> None:
        style = self.library.key_style if self.library else None
        for sid, a in self._key_style_actions:
            a.setChecked(sid == style)
            a.setEnabled(self.library is not None)
        self.act_rekey_all.setEnabled(self.library is not None)

    def _set_key_style(self, style: str) -> None:
        if self.library is None:
            return
        library.set_key_style(self.library, style)
        self._commit(f"Citation key style: {KEY_STYLES[style].split(' — ')[0]}")
        self._sync_key_style_menu()
        self.statusBar().showMessage(
            "New references will be named like "
            f"{KEY_STYLES[style].split(' — ')[0]}. Library ▸ Citation key style ▸ "
            "Rename all keys… renames the existing ones.", 8000)

    def _rekey_all(self) -> None:
        if self.library is None:
            return
        self._flush_editor()
        mapping = {o: n for o, n in store.keys_in_style(
            self.entries, self.library.key_style).items() if o != n}
        if not mapping:
            QMessageBox.information(self, "Rename keys",
                                    "All keys already follow this style.")
            return
        sample = "\n".join(f"{o}  →  {n}" for o, n in list(mapping.items())[:8])
        more = f"\n… and {len(mapping) - 8} more" if len(mapping) > 8 else ""
        if QMessageBox.question(
                self, "Rename keys",
                f"Rename {len(mapping)} citation key"
                f"{'s' * (len(mapping) != 1)} to the "
                f"“{KEY_STYLES[self.library.key_style].split(' — ')[0]}” style?\n\n"
                f"{sample}{more}\n\nDocuments that cite the old keys will need "
                "updating. Edit ▸ Undo reverses this.") != QMessageBox.Yes:
            return
        store.rename_keys(self.library, self.entries, mapping)
        self._commit(f"Rename {len(mapping)} keys to "
                     f"{KEY_STYLES[self.library.key_style].split(' — ')[0]} style")
        self._reload([])

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
        name = next((c.name for c in self.collections if c.id == self._scope),
                    self.library.name)
        # BibTeX can't read file names with spaces in \bibliography{}.
        stem = re.sub(r"[^\w\-]+", "-", name).strip("-") or "references"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {len(entries)} references as {label}",
            str(Path(last) / f"{stem}.{ext}"),
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
        self._update_undo_actions()
        self._sync_key_style_menu()
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
        self._api_snapshot = ((self.library.name if self.library else None),
                              dict(self.entries))
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
        # Hover / pressed tints come from the accent: some themes' own
        # hover colour equals their toolbar colour (Catppuccin Latte).
        acc = QColor(t["accent"])

        boost = 1.8 if themes.is_dark(self._theme_name) else 1.0

        def tint(alpha: float) -> str:
            a = min(alpha * boost, 0.9)
            return f"rgba({acc.red()}, {acc.green()}, {acc.blue()}, {a:.2f})"
        # Flat, rounded, airy: the look of current desktop apps.
        self.setStyleSheet(f"""
            QToolBar#main_toolbar {{
                background: {t['surface']}; border: none;
                border-bottom: 1px solid {t['page_border']};
                padding: 3px 6px; spacing: 2px;
            }}
            QToolButton {{
                border: 1px solid transparent; border-radius: 6px;
                padding: 4px; margin: 1px; color: {t['text']};
            }}
            QToolButton:hover {{
                background: {tint(0.14)}; border: 1px solid {tint(0.35)};
            }}
            QToolButton:pressed {{ background: {tint(0.28)}; }}
            QToolButton:checked {{
                background: {tint(0.20)}; border: 1px solid {tint(0.45)};
            }}
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

    # ----- local AI -----

    def _ai_saved(self, message: str) -> None:
        """A finished AI run (summaries, answers) is part of the library's
        history like any other change, and can be undone."""
        if self.library is not None:
            self._commit(message)

    def _ai_summarise(self) -> None:
        if not self.selected_entries():
            self.statusBar().showMessage("Select the paper(s) to summarise", 4000)
            return
        self._side.setCurrentWidget(self._ai_panel)
        self._ai_panel.summarise()

    def _ai_ask_library(self) -> None:
        if self.library is None:
            return
        self._flush_editor()
        scopes = []
        sel = self.selected_entries()
        if len(sel) > 1:
            scopes.append(("The selected papers", sel))
        current = self._scope if self._scope not in (ALL, UNFILED, REVIEW) else None
        cols = sorted(self.collections, key=lambda c: c.id != current)
        for c in cols:
            ids = store.collection_and_descendants(self.collections, c.id)
            members = [e for e in self.entries.values() if ids.intersection(e.collections)]
            if members:
                scopes.append((f"Collection “{c.name}”", members))
        scopes.append(("The whole library", list(self.entries.values())))
        if current is None and len(sel) <= 1:
            scopes.insert(0, scopes.pop())          # the library first
        dlg = AskLibraryDialog(self.library, scopes, self)
        dlg.reveal.connect(self._reveal_key)
        dlg.saved.connect(self._ai_saved)
        dlg.show()

    def _reveal_key(self, key: str) -> None:
        if key in self.entries:
            self._scope = ALL
            self._search.clear()
            self._rebuild_tree()
            self._select_keys([key])
            self.raise_()

    def _append_notes(self, key: str, text: str) -> None:
        e = self.entries.get(key)
        if e is None:
            return
        self._flush_editor()
        e.notes = (e.notes.rstrip() + "\n\n" + text).strip()
        store.save_entry(self.library, e)
        self._commit(f"AI notes for {key}")
        self._model.update_entry(e)
        if self._editor.entry is not None and self._editor.entry.key == key:
            self._editor.set_entry(e)
        self.statusBar().showMessage(f"Added to the notes of {key}", 4000)

    def _open_guide(self) -> None:
        """The guide shipped with the app; the online copy otherwise."""
        local = Path(__file__).resolve().parent / "guide" / "index.html"
        if not local.exists():
            local = Path(__file__).resolve().parent.parent / "docs" / "guide" / "index.html"
        url = (QUrl.fromLocalFile(str(local)) if local.exists()
               else QUrl("https://gkerherve.github.io/KherveRef/guide/"))
        QDesktopServices.openUrl(url)

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
        QApplication.instance().removeEventFilter(self)
        self._ai_panel.shutdown()
        if self._word_server is not None:
            self._word_server.stop()
            self._word_server = None
        self._flush_editor()
        self._thumbs.stop()
        if self._job is not None:
            self._job.cancel()
            self._job.wait(10000)
        if self._git_job is not None:
            self._git_job.wait(5000)
        super().closeEvent(event)
