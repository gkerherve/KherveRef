"""The AI tab of the details pane, "Ask the library" and the local-AI
settings: the window side of ai.py."""
from __future__ import annotations

import html
import re
import time

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from . import ai
from .icons import icon

SETTINGS = ("kherve", "KherveRef")
_CITE = re.compile(r"\[([A-Za-z0-9_:\-./+]+), p\. ?(\d+)\]")
# Models write LaTeX maths ($E_2$, $\gamma$); shown without the dollars.
_MATH = re.compile(r"\$([^$\n]{1,80})\$")


def client() -> ai.Ollama:
    return ai.Ollama(QSettings(*SETTINGS).value("ai_url", ai.DEFAULT_URL))


def chosen_model(c: ai.Ollama) -> str | None:
    m = QSettings(*SETTINGS).value("ai_model", "")
    return m or c.default_model()


NOT_RUNNING = (
    "<p><b>No local AI found.</b> KherveRef uses <a href='https://ollama.com'>Ollama</a>, "
    "which runs AI models on this computer — your papers never leave it.</p>"
    "<ol><li>Install Ollama from <a href='https://ollama.com/download'>ollama.com</a> "
    "and open it.</li><li>In a terminal: <code>ollama pull qwen3.5:4b</code> "
    "(about 3 GB).</li><li>Come back and press the button again.</li></ol>"
    "<p>For harder questions, Claude can also work with your library: "
    "AI ▸ Connect to Claude (MCP)…</p>")


class AIJob(QThread):
    chunk = Signal(str)
    failed = Signal(str)

    def __init__(self, make_messages, parent=None):
        super().__init__(parent)
        self._make = make_messages
        self._stop = False
        self.model = ""

    def stop(self):
        self._stop = True

    def run(self):
        try:
            c = client()
            self.model = chosen_model(c) or ""
            if not self.model:
                raise ai.AIError("NO_MODEL")
            for piece in c.chat(self.model, self._make(), lambda: self._stop):
                self.chunk.emit(piece)
        except ai.AIError as e:
            self.failed.emit(str(e))
        except Exception as e:      # never die silently in a thread
            self.failed.emit(f"{type(e).__name__}: {e}")


class _Answer(QTextBrowser):
    """Markdown output, re-rendered at most ~8 times a second while
    the model streams."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.text = ""
        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(self._render)
        self.linkify = None         # optional str -> str applied before rendering

    def reset(self, text: str = ""):
        self.text = text
        self._render()

    def append_text(self, piece: str):
        self.text += piece
        if not self._timer.isActive():
            self._timer.start()

    def _render(self):
        md = _MATH.sub(lambda m: m.group(1).replace("\\", ""), self.text)
        md = self.linkify(md) if self.linkify else md
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.setMarkdown(md)
        if at_end:
            bar.setValue(bar.maximum())


def _failure_html(msg: str) -> str:
    if msg == "NO_MODEL":
        return ("<p><b>Ollama is running but has no model.</b> In a terminal: "
                "<code>ollama pull qwen3.5:4b</code>, then try again.</p>")
    if "not reachable" in msg:
        return NOT_RUNNING
    return f"<p><b>The local AI stopped:</b> {html.escape(msg)}</p>"


class AIPanel(QWidget):
    """Summarise / ask about the selected reference."""
    add_to_notes = Signal(str, str)        # key, text

    def __init__(self, get_library, parent=None):
        super().__init__(parent)
        self._lib = get_library
        self._entry = None
        self._job: AIJob | None = None
        self._answers: dict[str, str] = {}      # key -> last answer this session
        self._shown: str | None = None          # key whose output is displayed

        self._title = QLabel("Select a reference.")
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight: 600;")
        self._summarise = QPushButton(icon("ai"), "Summarise")
        self._summarise.setToolTip("A structured summary of the paper by your "
                                   "local AI (aim, methods, findings…)")
        self._summarise.clicked.connect(self.summarise)
        self._question = QLineEdit()
        self._question.setPlaceholderText("Ask something about this paper…")
        self._question.returnPressed.connect(self.ask)
        self._ask = QPushButton("Ask")
        self._ask.clicked.connect(self.ask)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self._stop_job)
        self._stop.hide()
        self._out = _Answer()
        self._out.anchorClicked.connect(lambda u: QDesktopServices.openUrl(u))
        self._status = QLabel()
        self._status.setStyleSheet("color: gray;")
        self._notes = QPushButton(icon("notes"), "Add to notes")
        self._notes.setToolTip("Append this answer to the reference's notes")
        self._notes.clicked.connect(self._save_to_notes)
        self._copy = QPushButton(icon("copy"), "Copy")
        self._copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self._out.text))

        top = QHBoxLayout()
        top.addWidget(self._summarise)
        top.addStretch(1)
        top.addWidget(self._stop)
        ask = QHBoxLayout()
        ask.addWidget(self._question, 1)
        ask.addWidget(self._ask)
        bottom = QHBoxLayout()
        bottom.addWidget(self._status, 1)
        bottom.addWidget(self._copy)
        bottom.addWidget(self._notes)
        lay = QVBoxLayout(self)
        lay.addWidget(self._title)
        lay.addLayout(top)
        lay.addLayout(ask)
        lay.addWidget(self._out, 1)
        lay.addLayout(bottom)
        self.set_entry(None)

    def set_entry(self, e) -> None:
        self._entry = e
        has = e is not None and bool(e.key)
        for w in (self._summarise, self._question, self._ask):
            w.setEnabled(has and self._job is None)
        self._title.setText(e.title if has else "Select a reference to summarise "
                            "it or ask about it.")
        key = e.key if has else None
        # Only a different reference replaces the output: re-selecting the
        # same one (or a failed run) keeps what is shown, help included.
        if self._job is None and key != self._shown:
            self._out.reset(self._answers.get(key, "") if key else "")
            self._status.setText("")
            self._shown = key
        self._update_buttons()

    def _update_buttons(self):
        done = bool(self._out.text) and self._job is None
        self._notes.setEnabled(done and self._entry is not None)
        self._copy.setEnabled(done)

    # ----- running -----

    def summarise(self) -> None:
        e, lib = self._entry, self._lib()
        if e is not None and lib is not None:
            self._run(lambda: ai.summary_messages(lib, e), "Summary")

    def ask(self) -> None:
        q = self._question.text().strip()
        e, lib = self._entry, self._lib()
        if q and e is not None and lib is not None:
            self._run(lambda: ai.question_messages(lib, e, q), q)

    def _run(self, make, label: str) -> None:
        if self._job is not None:
            return
        self._label = label
        self._started = time.monotonic()
        self._key = self._entry.key
        self._shown = self._key
        self._out.reset(f"*{html.escape(label)}…*\n\n" if label != "Summary" else "")
        self._out.text = ""
        self._status.setText("Reading the paper…")
        job = AIJob(make, self)
        job.chunk.connect(self._chunk)
        job.failed.connect(self._failed)
        job.finished.connect(self._finished)
        self._job = job
        for w in (self._summarise, self._question, self._ask):
            w.setEnabled(False)
        self._stop.show()
        self._update_buttons()
        job.start()

    def _chunk(self, piece: str):
        if self._job is not None and not self._out.text:
            self._status.setText(f"Writing… ({self._job.model})")
        self._out.append_text(piece)

    def _failed(self, msg: str):
        self._out.setHtml(_failure_html(msg))
        self._out.text = ""

    def _finished(self):
        job, self._job = self._job, None
        if self._out.text:
            self._out._render()
            prefix = "" if self._label == "Summary" else f"**Q: {self._label}**\n\n"
            self._out.text = prefix + self._out.text
            self._out._render()
            self._answers[self._key] = self._out.text
            self._status.setText(f"{job.model} · {time.monotonic() - self._started:.0f} s"
                                 " · check important facts in the paper")
        elif not self._status.text().startswith("Stopped"):
            self._status.setText("")
        self._stop.hide()
        self.set_entry(self._entry)

    def _stop_job(self):
        if self._job is not None:
            self._job.stop()
            self._status.setText("Stopped")

    def _save_to_notes(self):
        if self._entry is not None and self._out.text:
            stamp = time.strftime("%Y-%m-%d")
            self.add_to_notes.emit(self._entry.key,
                                   f"AI ({stamp}):\n{self._out.text.strip()}")

    def shutdown(self):
        if self._job is not None:
            self._job.stop()
            self._job.wait(3000)


class AskLibraryDialog(QDialog):
    """A question over many papers; answers cite [key, p. N], and those
    citations are links that select the reference in the window."""
    reveal = Signal(str)

    def __init__(self, lib, scopes: list[tuple[str, list]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ask the library")
        self.resize(760, 620)
        self._lib = lib
        self._job: AIJob | None = None
        self._scope = QComboBox()
        for label, entries in scopes:
            self._scope.addItem(f"{label} ({len(entries)})", entries)
        self._question = QLineEdit()
        self._question.setPlaceholderText(
            "e.g. Which papers measured oxygen surface exchange, and how?")
        self._question.returnPressed.connect(self.ask)
        self._go = QPushButton(icon("ai"), "Ask")
        self._go.clicked.connect(self.ask)
        self._out = _Answer()
        self._out.linkify = lambda t: _CITE.sub(
            lambda m: f"[[{m.group(1)}, p. {m.group(2)}]](kref:{m.group(1)})", t)
        self._out.anchorClicked.connect(self._link)
        self._status = QLabel("Your local AI reads the most relevant passages of "
                              "these papers' PDFs and answers with sources.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: gray;")
        row = QHBoxLayout()
        row.addWidget(self._question, 1)
        row.addWidget(self._go)
        form = QFormLayout()
        form.addRow("Search in", self._scope)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        copy = buttons.addButton("Copy answer", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._out.text))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)
        lay.addWidget(self._out, 1)
        lay.addWidget(self._status)
        lay.addWidget(buttons)

    def ask(self):
        q = self._question.text().strip()
        entries = self._scope.currentData() or []
        if not q or self._job is not None:
            return
        self._out.reset("")
        self._status.setText("Finding the relevant passages…")
        self._started = time.monotonic()

        def make():
            msgs, hits = ai.library_messages(self._lib, entries, q)
            self._used = sorted({h.key for h in hits})
            return msgs
        job = AIJob(make, self)
        job.chunk.connect(lambda p: (self._status.setText(f"Writing… ({job.model})"),
                                     self._out.append_text(p)))
        job.failed.connect(lambda m: self._out.setHtml(_failure_html(m)))
        job.finished.connect(self._finished)
        self._job = job
        self._go.setEnabled(False)
        job.start()

    def _finished(self):
        job, self._job = self._job, None
        self._go.setEnabled(True)
        if self._out.text:
            self._out._render()
            used = ", ".join(getattr(self, "_used", []))
            self._status.setText(f"{job.model} · {time.monotonic() - self._started:.0f} s"
                                 f" · from: {used} · click a source to open it")

    def _link(self, url: QUrl):
        if url.scheme() == "kref":
            self.reveal.emit(url.path() or url.toString()[len("kref:"):])
        else:
            QDesktopServices.openUrl(url)

    def reject(self):
        if self._job is not None:
            self._job.stop()
            self._job.wait(3000)
        super().reject()


class AISettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Local AI")
        self.resize(520, 260)
        s = QSettings(*SETTINGS)
        self._url = QLineEdit(s.value("ai_url", ai.DEFAULT_URL))
        self._model = QComboBox()
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.RichText)
        self._status.setOpenExternalLinks(True)
        test = QPushButton("Check connection")
        test.clicked.connect(self._check)
        form = QFormLayout()
        form.addRow("Ollama address", self._url)
        form.addRow("Model", self._model)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("KherveRef's AI runs on this computer through Ollama: "
                             "your papers are not sent anywhere."))
        lay.addLayout(form)
        lay.addWidget(test, 0, Qt.AlignLeft)
        lay.addWidget(self._status)
        lay.addWidget(buttons)
        self._check()

    def _check(self):
        c = ai.Ollama(self._url.text().strip() or ai.DEFAULT_URL)
        self._model.clear()
        try:
            models = c.models()
            version = c.version()
        except ai.AIError:
            self._status.setText(NOT_RUNNING)
            return
        current = QSettings(*SETTINGS).value("ai_model", "") or c.default_model()
        for m in models:
            self._model.addItem(f"{m.name}  ({m.size})", m.name)
        i = self._model.findData(current)
        self._model.setCurrentIndex(max(0, i))
        self._status.setText(f"Connected to Ollama {version} — {len(models)} "
                             "model(s). Bigger models answer better but slower."
                             if models else _failure_html("NO_MODEL"))

    def accept(self):
        s = QSettings(*SETTINGS)
        s.setValue("ai_url", self._url.text().strip() or ai.DEFAULT_URL)
        if self._model.currentData():
            s.setValue("ai_model", self._model.currentData())
        super().accept()
