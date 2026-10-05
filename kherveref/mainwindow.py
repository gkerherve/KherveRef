"""Main window: collections on the left, references in the middle,
details on the right; a start page when no library is open."""
from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import (QAction, QActionGroup, QIcon, QKeySequence,
                           QStandardItemModel)
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QStackedWidget, QTableView, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import git_backend, icons, library, themes
from . import version_string
from .icons import icon

SETTINGS = ("kherve", "KherveRef")
MAX_RECENT = 8
COLUMNS = ["Key", "Authors", "Year", "Title", "Journal"]


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


class MainWindow(QMainWindow):
    def __init__(self, theme_name: str = "Light"):
        super().__init__()
        self._theme_name = theme_name
        self._theme = themes.THEMES.get(theme_name, themes.THEMES["Light"])
        icons.set_icon_color(themes.icon_color(self._theme))
        self.library: library.Library | None = None
        self._git_job: _GitJob | None = None

        self.resize(1280, 800)
        self._build_central()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._status = QLabel()
        self.statusBar().addPermanentWidget(self._status)
        self._apply_theme_qss()
        self._refresh()

    # ----- layout -----

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

        self._collections = QTreeWidget()
        self._collections.setHeaderHidden(True)
        self._collections.setMinimumWidth(180)

        self._model = QStandardItemModel(0, len(COLUMNS), self)
        self._model.setHorizontalHeaderLabels(COLUMNS)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)

        self._details = QLabel("No reference selected.")
        self._details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._details.setWordWrap(True)
        self._details.setMargin(12)
        self._details.setMinimumWidth(240)

        split = QSplitter()
        split.addWidget(self._collections)
        split.addWidget(self._table)
        split.addWidget(self._details)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 760, 300])
        self._stack.addWidget(split)
        self.setCentralWidget(self._stack)

    def _act(self, text, slot, name=None, shortcut=None) -> QAction:
        a = QAction(icon(name) if name else QIcon(), text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        self.act_new = self._act("&New library…", self._new_library,
                                 "new_library", QKeySequence.New)
        self.act_open = self._act("&Open library…", self._open_library_dialog,
                                  "open_library", QKeySequence.Open)
        self.act_close = self._act("&Close library", self.close_library,
                                   "close", QKeySequence.Close)
        self.act_quit = self._act("&Quit", self.close, None, QKeySequence.Quit)
        self.act_history = self._act("&History…", self._show_history, "history")
        self.act_remote = self._act("Set &remote…", self._set_remote, "remote")
        self.act_pull = self._act("&Pull", lambda: self._run_git(git_backend.pull,
                                                                 "Pull"), "pull")
        self.act_push = self._act("P&ush", lambda: self._run_git(git_backend.push,
                                                                 "Push"), "push")
        self.act_about = self._act("&About KherveRef", self._about, "about")
        self._library_actions = [self.act_close, self.act_history,
                                 self.act_remote, self.act_pull, self.act_push]

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addActions([self.act_new, self.act_open])
        self._recent_menu = m_file.addMenu(icon("recent"), "Open &recent")
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m_file.addAction(self.act_close)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_view = mb.addMenu("&View")
        m_theme = m_view.addMenu("&Theme")
        group = QActionGroup(self)
        for name in themes.THEME_NAMES:
            a = QAction(name, self, checkable=True)
            a.setChecked(name == self._theme_name)
            a.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            group.addAction(a)
            m_theme.addAction(a)

        m_lib = mb.addMenu("&Library")
        m_lib.addAction(self.act_history)
        m_lib.addSeparator()
        m_lib.addActions([self.act_remote, self.act_pull, self.act_push])

        mb.addMenu("&Help").addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        tb.addActions([self.act_new, self.act_open])
        tb.addSeparator()
        tb.addActions([self.act_pull, self.act_push])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search references")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icon("find"), QLineEdit.LeadingPosition)
        self._search.setFixedWidth(260)
        tb.addWidget(self._search)
        self.addToolBar(tb)

    # ----- library lifecycle -----

    def _new_library(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose an empty folder for the new library")
        if not path:
            return
        try:
            lib = library.create_library(Path(path))
        except library.LibraryError as e:
            QMessageBox.warning(self, "New library", str(e))
            return
        git_backend.commit_all(lib.root, f"Create library {lib.name}")
        self.open_library(lib.root)

    def _open_library_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open a library folder")
        if path:
            self.open_library(Path(path))

    def open_library(self, path: Path) -> bool:
        try:
            lib = library.open_library(path)
        except library.LibraryError as e:
            QMessageBox.warning(self, "Open library", str(e))
            return False
        self.library = lib
        self._remember(lib.root)
        self._refresh()
        return True

    def reopen_last_library(self) -> None:
        last = QSettings(*SETTINGS).value("last_library", "")
        if last and library.is_library(Path(last)):
            self.open_library(Path(last))

    def close_library(self) -> None:
        self.library = None
        QSettings(*SETTINGS).setValue("last_library", "")
        self._refresh()

    def _recent(self) -> list[str]:
        val = QSettings(*SETTINGS).value("recent_libraries", []) or []
        return [val] if isinstance(val, str) else list(val)

    def _remember(self, root: Path) -> None:
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

    # ----- git -----

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
            self._refresh()

    def _run_git(self, fn, label: str) -> None:
        if not self.library or self._git_job is not None:
            return
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
        self._refresh()

    # ----- view -----

    def _refresh(self) -> None:
        lib = self.library
        for a in self._library_actions:
            a.setEnabled(lib is not None)
        self._search.setEnabled(lib is not None)
        name = lib.name if lib else "no library"
        self.setWindowTitle(f"KherveRef {version_string()} — {name}")
        if lib is None:
            self._stack.setCurrentIndex(0)
            self._status.setText("")
            return
        self._stack.setCurrentIndex(1)
        self._collections.clear()
        for text, ic in (("All references", "all_refs"), ("Unfiled", "unfiled")):
            item = QTreeWidgetItem([text])
            item.setIcon(0, icon(ic))
            self._collections.addTopLevelItem(item)
        self._collections.setCurrentItem(self._collections.topLevelItem(0))
        n = lib.entry_count()
        branch = git_backend.current_branch(lib.root) or "no commits"
        remote = git_backend.get_remote(lib.root) or "no remote"
        self._status.setText(
            f"{n} reference{'s' if n != 1 else ''}  ·  {branch}  ·  {remote}")

    def _apply_theme_qss(self) -> None:
        self._status.setStyleSheet(themes.status_label_stylesheet(self._theme))

    def _set_theme(self, name: str) -> None:
        from PySide6.QtWidgets import QApplication
        self._theme_name = name
        self._theme = themes.apply_theme(QApplication.instance(), name)
        icons.set_icon_color(themes.icon_color(self._theme))
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
        self._refresh()     # the collection tree's icons

    def _about(self) -> None:
        QMessageBox.about(
            self, "About KherveRef",
            f"<b>KherveRef</b> {version_string()}<br><br>"
            "A reference manager for KherveTeX and KhervePDF, with "
            "BibLaTeX export and Git-synced libraries.<br><br>"
            "© 2026 Gwilherm Kerherve — GPL-3.0")

    def closeEvent(self, event) -> None:
        if self._git_job is not None:
            self._git_job.wait(5000)
        super().closeEvent(event)
