"""The AI tab of the details pane, "Ask the library" and the local-AI
settings: the window side of ai.py."""
from __future__ import annotations

import html
import re
import time

from PySide6.QtCore import QSettings, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from . import ai
from .icons import icon

SETTINGS = ("kherve", "KherveRef")
_CITE = re.compile(r"\[([A-Za-z0-9_:\-./+]+), p\. ?(\d+)\]")
# Models write LaTeX maths ($E_2$, $\gamma$); shown without the dollars.
_MATH = re.compile(r"\$([^$\n]{1,80})\$")


def open_link(url: QUrl, parent=None) -> None:
    """Links in AI output: the set-up guide, or a web page."""
    if url.scheme() == "kref-setup":
        AISetupDialog(parent).exec()
    else:
        QDesktopServices.openUrl(url)


def client() -> ai.Ollama:
    return ai.Ollama(QSettings(*SETTINGS).value("ai_url", ai.DEFAULT_URL))


def chosen_model(c: ai.Ollama) -> str | None:
    m = QSettings(*SETTINGS).value("ai_model", "")
    return m or c.default_model()


NOT_RUNNING = (
    "<p><b>No local AI found.</b> KherveRef uses <a href='https://ollama.com'>Ollama</a>, "
    "which runs AI models on this computer — your papers never leave it.</p>"
    "<p><a href='kref-setup:'><b>Set up the local AI…</b></a> explains how to install "
    "Ollama and installs a model for you (AI ▸ Set up local AI…).</p>"
    "<p>For harder questions, Claude can also work with your library: "
    "AI ▸ Connect to Claude (MCP)…</p>")


class AIRun(QThread):
    """Runs AI steps one after another: one per paper, then maybe an
    overview built from their answers. A step's messages are made on
    this thread (reading PDFs takes time) from the answers so far."""
    started_step = Signal(int, str)
    chunk = Signal(int, str)
    done_step = Signal(int, str)
    failed = Signal(str)

    def __init__(self, steps, parent=None):
        super().__init__(parent)
        # [(label, make_messages(previous_answers) -> messages)]
        self.steps = steps
        self._stop = False
        self.model = ""
        self.answers: list[str] = []

    def stop(self):
        self._stop = True

    def run(self):
        try:
            c = client()
            self.model = chosen_model(c) or ""
            if not self.model:
                raise ai.AIError("NO_MODEL")
            for i, (label, make) in enumerate(self.steps):
                if self._stop:
                    return
                self.started_step.emit(i, label)
                try:
                    messages = make(self.answers)
                except ai.AIError as e:
                    if "not reachable" in str(e):
                        raise
                    # A paper without readable text must not end the run.
                    self.answers.append(f"*({e})*")
                    self.done_step.emit(i, self.answers[-1])
                    continue
                text = ""
                for piece in c.chat(self.model, messages, lambda: self._stop):
                    text += piece
                    self.chunk.emit(i, piece)
                if self._stop:
                    return
                self.answers.append(text)
                self.done_step.emit(i, text)
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
        return ("<p><b>Ollama is running but has no model yet.</b> "
                "<a href='kref-setup:'>Set up the local AI…</a> installs one for you.</p>")
    if "not reachable" in msg:
        return NOT_RUNNING
    return f"<p><b>The local AI stopped:</b> {html.escape(msg)}</p>"


def _covered(answer: str) -> bool:
    return not answer.strip().startswith(ai.NOT_COVERED.rstrip(".")) \
        and not answer.strip().startswith("*(")


def _linkify(text: str) -> str:
    """[key, p. N] and [key] become links that open the reference."""
    text = _CITE.sub(lambda m: f"[[{m.group(1)}, p. {m.group(2)}]](kref:{m.group(1)})",
                     text)
    return re.sub(r"(?<!\[)\[([A-Za-z][A-Za-z0-9_:\-./+]*\d{4}[A-Za-z0-9_:\-./+]*)\]"
                  r"(?!\()", lambda m: f"[[{m.group(1)}]](kref:{m.group(1)})", text)


class AIPanel(QWidget):
    """The AI tab: summarise / ask about the selected paper — or each of
    several selected papers. Everything is kept (ai_store) and shown
    again when the paper is selected later."""
    add_to_notes = Signal(str, str)        # key, text
    saved = Signal(str)                    # commit message after a run
    reveal = Signal(str)                   # a [key] link was clicked

    def __init__(self, get_library, parent=None):
        super().__init__(parent)
        self._lib = get_library
        self._entries: list = []
        self._run: AIRun | None = None
        self._live = ""             # markdown of the run in progress
        self._last = ("", "")       # (key, text) of the newest answer

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight: 600;")
        self._summarise = QPushButton(icon("ai"), "Summarise")
        self._summarise.clicked.connect(self.summarise)
        self._question = QLineEdit()
        self._question.returnPressed.connect(self.ask)
        self._ask = QPushButton("Ask")
        self._ask.clicked.connect(self.ask)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self._stop_run)
        self._stop.hide()
        self._out = _Answer()
        self._out.linkify = _linkify
        self._out.anchorClicked.connect(self._link)
        self._status = QLabel()
        self._status.setStyleSheet("color: gray;")
        self._status.setWordWrap(True)
        self._notes = QPushButton(icon("notes"), "Add to notes")
        self._notes.setToolTip("Append the newest answer to the paper's notes")
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
        self.set_entries([])

    # ----- selection -----

    def set_entry(self, e) -> None:
        self.set_entries([e] if e is not None else [])

    def set_entries(self, entries) -> None:
        entries = [e for e in entries if e is not None and e.key]
        if self._run is not None:
            return              # the run keeps its papers; shown when done
        self._entries = entries
        n = len(entries)
        if n == 0:
            self._title.setText("Select a paper — or several — to summarise them "
                                "or ask about them.")
        elif n == 1:
            self._title.setText(entries[0].title)
        else:
            self._title.setText(f"{n} papers selected")
        self._summarise.setText("Summarise" if n <= 1 else f"Summarise each ({n})")
        self._summarise.setToolTip(
            "A structured summary by your local AI (aim, methods, findings…), kept "
            "with the paper" if n <= 1 else
            "Summarise every selected paper in turn; each summary is kept")
        self._question.setPlaceholderText(
            "Ask something about this paper…" if n <= 1 else
            f"Ask all {n} papers — each answers, then an overview…")
        for w in (self._summarise, self._question, self._ask):
            w.setEnabled(n > 0)
        self._show_saved()

    def _show_saved(self) -> None:
        lib = self._lib()
        if lib is None or len(self._entries) != 1:
            self._out.reset(self._live if len(self._entries) > 1 else "")
            self._status.setText("")
        else:
            from . import ai_store
            md = ai_store.as_markdown(ai_store.load(lib, self._entries[0].key))
            self._out.reset(md)
            self._status.setText("" if md else "Nothing asked about this paper yet.")
            bar = self._out.verticalScrollBar()
            bar.setValue(bar.maximum())
        self._update_buttons()

    def _update_buttons(self):
        idle = self._run is None
        self._notes.setEnabled(idle and bool(self._last[1]))
        self._copy.setEnabled(idle and bool(self._out.text))

    # ----- running -----

    def summarise(self) -> None:
        lib = self._lib()
        if lib is None or not self._entries:
            return
        steps = [(e.key, (lambda _a, e=e: ai.summary_messages(lib, e)))
                 for e in self._entries]
        self._start(steps, "summary", "")

    def ask(self) -> None:
        q = self._question.text().strip()
        lib = self._lib()
        if not q or lib is None or not self._entries:
            return
        if len(self._entries) == 1:
            e = self._entries[0]
            steps = [(e.key, lambda _a: ai.question_messages(lib, e, q))]
        else:
            steps = [(e.key, (lambda _a, e=e: ai.each_paper_messages(lib, e, q)))
                     for e in self._entries]
            papers = list(self._entries)
            steps.append(("overview", lambda answers: ai.overview_messages(
                q, list(zip(papers, answers)))))
        self._question.clear()
        self._start(steps, "question", q)

    def _start(self, steps, kind: str, question: str) -> None:
        if self._run is not None:
            return
        self._kind, self._q = kind, question
        self._started = time.monotonic()
        self._papers = {e.key: e for e in self._entries}
        single = len(self._entries) == 1
        self._base = self._out.text if single else ""
        head = ("" if single else
                (f"## {html.escape(question)}\n\n" if question else "## Summaries\n\n"))
        if single:
            head = ("\n\n---\n\n" if self._base else "") + (
                f"### Q: {question}\n\n" if question else "## Summary\n\n")
        self._live = head
        self._out.reset(self._base + self._live)
        run = AIRun(steps, self)
        run.started_step.connect(self._step_started)
        run.chunk.connect(self._chunk)
        run.done_step.connect(self._step_done)
        run.failed.connect(self._failed)
        run.finished.connect(self._finished)
        self._run = run
        self._errors = []
        for w in (self._summarise, self._question, self._ask):
            w.setEnabled(False)
        self._stop.show()
        self._update_buttons()
        self._status.setText("Reading…")
        run.start()

    def _step_started(self, i: int, label: str):
        n = len(self._run.steps)
        e = self._papers.get(label)
        if n > 1:
            if label == "overview":
                self._live += "\n\n## Overview\n\n"
                self._status.setText("Writing the overview…")
            else:
                self._live += f"\n\n#### [{label}] {e.title if e else ''}\n\n"
                self._status.setText(f"Paper {i + 1} of {n - (self._kind == 'question')}: "
                                     f"{label}")
        else:
            self._status.setText("Reading the paper…")
        self._out.reset(self._base + self._live)

    def _chunk(self, _i: int, piece: str):
        if self._run is not None and self._status.text() == "Reading the paper…":
            self._status.setText(f"Writing… ({self._run.model})")
        self._live += piece
        self._out.text = self._base + self._live
        self._out.append_text("")

    def _step_done(self, i: int, text: str):
        """Keep each answer as soon as it is complete."""
        from . import ai_store
        lib = self._lib()
        label = self._run.steps[i][0]
        if lib is None or label not in self._papers or text.startswith("*("):
            if text.startswith("*("):
                self._live += text
                self._out.reset(self._base + self._live)
            return
        if self._kind == "summary":
            ai_store.save_summary(lib, label, text, self._run.model)
        elif _covered(text):
            ai_store.add_question(lib, label, self._q, text, self._run.model)
        self._last = (label, text)

    def _failed(self, msg: str):
        self._errors.append(msg)
        self._out.setHtml(_failure_html(msg))
        self._out.text = ""

    def _finished(self):
        run, self._run = self._run, None
        self._stop.hide()
        took = time.monotonic() - self._started
        done = len(run.answers)
        if done and not self._errors:
            n_papers = sum(1 for lbl, _ in run.steps[:done] if lbl in self._papers)
            what = ("summary" if self._kind == "summary" else "answer")
            msg = (f"AI {what} for {run.steps[0][0]}" if n_papers == 1 else
                   f"AI {what}s for {n_papers} papers")
            self.saved.emit(msg)
            self._status.setText(f"{run.model} · {took:.0f} s · kept with "
                                 f"{'the paper' if n_papers == 1 else 'each paper'} · "
                                 "check important facts in the papers")
        if not self._errors:
            for w in (self._summarise, self._question, self._ask):
                w.setEnabled(bool(self._entries))
            if len(self._entries) == 1:
                status = self._status.text()
                self._show_saved()
                self._status.setText(status)
            else:
                self._out.reset(self._live)
        else:
            for w in (self._summarise, self._question, self._ask):
                w.setEnabled(bool(self._entries))
        self._update_buttons()

    def _stop_run(self):
        if self._run is not None:
            self._run.stop()
            self._status.setText("Stopped — what was finished is kept")

    def _link(self, url: QUrl):
        if url.scheme() == "kref":
            self.reveal.emit(url.path() or url.toString()[len("kref:"):])
        else:
            open_link(url, self)

    def _save_to_notes(self):
        key, text = self._last
        if key and text:
            stamp = time.strftime("%Y-%m-%d")
            self.add_to_notes.emit(key, f"AI ({stamp}):\n{text.strip()}")

    def shutdown(self):
        if self._run is not None:
            self._run.stop()
            self._run.wait(3000)


class AskLibraryDialog(QDialog):
    """Questions over many papers. Quick: the most relevant passages
    anywhere. Thorough: every paper is asked in turn, then an overview.
    Sources are links; past questions are kept and listed."""
    reveal = Signal(str)
    saved = Signal(str)

    def __init__(self, lib, scopes: list[tuple[str, list]], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QRadioButton, QSplitter
        self._QListWidgetItem = QListWidgetItem
        self.setWindowTitle("Ask the library")
        self.resize(1000, 680)
        self._lib = lib
        self._run: AIRun | None = None
        self._scope = QComboBox()
        for label, entries in scopes:
            self._scope.addItem(f"{label} ({len(entries)})", (label, entries))
        self._scope.currentIndexChanged.connect(self._update_estimate)
        self._quick = QRadioButton("Quick — search the most relevant passages")
        self._thorough = QRadioButton("Read every paper")
        self._quick.setChecked(True)
        self._thorough.toggled.connect(self._update_estimate)
        self._question = QLineEdit()
        self._question.setPlaceholderText(
            "e.g. Which papers measured oxygen surface exchange, and how?")
        self._question.returnPressed.connect(self.ask)
        self._go = QPushButton(icon("ai"), "Ask")
        self._go.clicked.connect(self.ask)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(lambda: self._run and self._run.stop())
        self._stop.hide()
        self._out = _Answer()
        self._out.linkify = _linkify
        self._out.anchorClicked.connect(self._link)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: gray;")
        self._history = QListWidget()
        self._history.setMinimumWidth(220)
        self._history.currentRowChanged.connect(self._show_history)
        self._fill_history()

        row = QHBoxLayout()
        row.addWidget(self._question, 1)
        row.addWidget(self._go)
        row.addWidget(self._stop)
        modes = QHBoxLayout()
        modes.addWidget(self._quick)
        modes.addWidget(self._thorough)
        modes.addStretch(1)
        form = QFormLayout()
        form.addRow("Search in", self._scope)
        form.addRow("How", modes)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(form)
        rl.addLayout(row)
        rl.addWidget(self._out, 1)
        rl.addWidget(self._status)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Earlier questions</b>"))
        ll.addWidget(self._history, 1)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 760])
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        copy = buttons.addButton("Copy answer", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._out.text))
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)
        self._update_estimate()

    # ----- history -----

    def _fill_history(self):
        from . import ai_store
        self._items = list(reversed(ai_store.history(self._lib)))
        self._history.blockSignals(True)
        self._history.clear()
        for it in self._items:
            li = self._QListWidgetItem(f"{it['question']}\n{it['date']} · {it['scope']}")
            li.setToolTip(it["question"])
            self._history.addItem(li)
        self._history.setCurrentRow(-1)
        self._history.blockSignals(False)

    def _show_history(self, row: int):
        if self._run is None and 0 <= row < len(self._items):
            it = self._items[row]
            self._out.reset(f"## {it['question']}\n*{it['scope']} · {it['mode']} · "
                            f"{it['model']} · {it['date']}*\n\n{it['answer']}")
            self._status.setText("From: " + ", ".join(it.get("sources", [])))

    # ----- asking -----

    def _update_estimate(self, *_):
        n = len((self._scope.currentData() or ("", []))[1])
        self._thorough.setText(f"Read every paper ({n}) — thorough, about "
                               f"{max(1, round(n * 25 / 60))} min")
        if self._run is None:
            self._status.setText(
                "Every paper is asked in turn, then an overview is written; each "
                "paper's answer is also kept with that paper." if self._thorough.isChecked()
                else "Your local AI reads the passages of these papers' PDFs that best "
                     "match the question and answers with sources.")

    def ask(self):
        q = self._question.text().strip()
        label, entries = self._scope.currentData() or ("", [])
        if not q or self._run is not None or not entries:
            return
        self._q, self._scope_label, self._entries = q, label, list(entries)
        self._thorough_mode = self._thorough.isChecked()
        lib = self._lib
        if self._thorough_mode:
            steps = [(e.key, (lambda _a, e=e: ai.each_paper_messages(lib, e, q)))
                     for e in entries]
            steps.append(("overview", lambda answers: ai.overview_messages(
                q, list(zip(entries, answers)))))
        else:
            self._used: list[str] = []

            def make(_a):
                msgs, hits = ai.library_messages(lib, entries, q)
                self._used = sorted({h.key for h in hits})
                return msgs
            steps = [("answer", make)]
        self._live = f"## {q}\n\n"
        self._out.reset(self._live)
        self._history.setCurrentRow(-1)
        self._started = time.monotonic()
        self._titles = {e.key: e.title for e in entries}
        self._covered: list[str] = []
        self._not_covered = 0
        run = AIRun(steps, self)
        run.started_step.connect(self._step_started)
        run.chunk.connect(self._chunk)
        run.done_step.connect(self._step_done)
        run.failed.connect(lambda m: self._out.setHtml(_failure_html(m)))
        run.finished.connect(self._finished)
        self._run = run
        self._go.setEnabled(False)
        self._stop.show()
        run.start()

    def _step_started(self, i: int, label: str):
        n = len(self._run.steps)
        if not self._thorough_mode:
            self._status.setText("Finding the relevant passages…")
            return
        if label == "overview":
            self._status.setText("Writing the overview…")
            self._live += "\n\n## Overview\n\n"
        else:
            self._status.setText(f"Reading paper {i + 1} of {n - 1}: {label} — "
                                 f"{len(self._covered)} answer(s) so far")
            self._pending = f"\n\n#### [{label}] {self._titles.get(label, '')}\n\n"
            self._current = ""
        self._out.reset(self._live)

    def _chunk(self, i: int, piece: str):
        if not self._thorough_mode or self._run.steps[i][0] == "overview":
            if self._status.text().startswith("Finding"):
                self._status.setText(f"Writing… ({self._run.model})")
            self._live += piece
            self._out.text = self._live
            self._out.append_text("")
        else:
            self._current += piece       # shown once we know it is covered

    def _step_done(self, i: int, text: str):
        if not self._thorough_mode:
            return
        label = self._run.steps[i][0]
        if label == "overview":
            return
        if _covered(text):
            from . import ai_store
            self._covered.append(label)
            self._live += self._pending + text.strip()
            ai_store.add_question(self._lib, label, self._q, text, self._run.model)
        else:
            self._not_covered += 1
        self._out.reset(self._live)

    def _finished(self):
        run, self._run = self._run, None
        self._go.setEnabled(True)
        self._stop.hide()
        took = time.monotonic() - self._started
        answer = self._live.split("\n\n", 1)[1] if "\n\n" in self._live else ""
        if not answer.strip():
            return
        from . import ai_store
        if self._thorough_mode:
            sources = self._covered
            note = (f" · {self._not_covered} paper(s) don't cover it"
                    if self._not_covered else "")
            mode = "every paper"
        else:
            sources, note, mode = getattr(self, "_used", []), "", "quick"
        ai_store.add_history(self._lib, self._q, self._scope_label, mode, answer,
                             run.model, sources)
        self.saved.emit(f"AI question to the library: {self._q[:60]}")
        self._fill_history()
        self._status.setText(f"{run.model} · {took:.0f} s{note} · from: "
                             f"{', '.join(sources) or '—'} · click a source to open it")

    def _link(self, url: QUrl):
        if url.scheme() == "kref":
            self.reveal.emit(url.path() or url.toString()[len("kref:"):])
        else:
            open_link(url, self)

    def reject(self):
        if self._run is not None:
            self._run.stop()
            self._run.wait(3000)
        super().reject()


DOWNLOAD_URL = "https://ollama.com/download"
LIBRARY_URL = "https://ollama.com/library/"

#: (model, size on disk, maker, what it is good for) — the same choices
#: as KherveNote's set-up, described for reading papers.
RECOMMENDED = (
    ("qwen3.5:4b", "3.4 GB", "Qwen — Alibaba",
     "Recommended. The best all-rounder for papers: clear, structured summaries, "
     "careful answers with page numbers, many languages, and long papers read whole."),
    ("granite4:micro-h", "1.9 GB", "Granite — IBM",
     "Small and fast, light on memory even with long texts. Plain, factual style; "
     "fewer languages. A good choice on a laptop with 8 GB."),
    ("gemma3:4b", "≈ 3.3 GB", "Gemma — Google",
     "Natural, readable writing in several languages. Similar size to Qwen."),
    ("llama3.2:3b", "≈ 2 GB", "Llama — Meta",
     "Quick, good English; weaker on long papers and other languages."),
)


def memory_gb() -> float | None:
    """This computer's memory, to suggest a model size."""
    import sys
    try:
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=5).stdout
            return int(out) / 2 ** 30
        if sys.platform.startswith("win"):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        import os
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:
        return None


def ollama_installed() -> bool:
    import shutil
    import sys
    from pathlib import Path
    if shutil.which("ollama"):
        return True
    if sys.platform == "darwin":
        return Path("/Applications/Ollama.app").exists()
    if sys.platform.startswith("win"):
        import os
        local = os.environ.get("LOCALAPPDATA", "")
        return bool(local) and (Path(local) / "Programs" / "Ollama").exists()
    return False


class _PullJob(QThread):
    progress = Signal(float, str)
    failed = Signal(str)

    def __init__(self, url: str, model: str, parent=None):
        super().__init__(parent)
        self._url, self._model, self._stop = url, model, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ai.Ollama(self._url).pull(self._model, self.progress.emit,
                                      lambda: self._stop)
        except ai.AIError as e:
            self.failed.emit(str(e))


class AISetupDialog(QDialog):
    """AI ▸ Set up local AI: what Ollama is, how to install it, which
    model to install and why — with a button that installs one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up the local AI")
        self.resize(860, 700)
        self._job: _PullJob | None = None
        s = QSettings(*SETTINGS)

        intro = QLabel(
            "<p>KherveRef's AI — <b>Summarise</b>, <b>Ask</b> and <b>Ask the library</b> — "
            "uses <b>Ollama</b>, a free program that runs AI models <b>on this "
            "computer</b>. Your papers are never sent to the internet.</p>")
        intro.setWordWrap(True)
        self._status = QLabel()
        self._status.setWordWrap(True)

        step1 = QLabel(
            "<h3>1. Install Ollama</h3><p>Download it from "
            f"<a href='{DOWNLOAD_URL}'>ollama.com/download</a> (Mac, Windows, Linux) and open "
            "it once — on a Mac it then sits in the menu bar, on Windows in the "
            "taskbar, and starts with the computer. On a Mac you can also install it "
            "in Terminal with <code>brew install ollama</code>.</p>")
        step1.setWordWrap(True)
        step1.setOpenExternalLinks(True)
        get = QPushButton("Download Ollama…")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOWNLOAD_URL)))
        again = QPushButton("Check again")
        again.clicked.connect(self.refresh)
        row1 = QHBoxLayout()
        row1.addWidget(get)
        row1.addWidget(again)
        row1.addStretch(1)

        self._step2 = QLabel()
        self._step2.setWordWrap(True)
        self._step2.setOpenExternalLinks(True)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Model", "Size", "What it is good for", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._bar = QProgressBar()
        self._bar.hide()
        self._bar_label = QLabel()
        self._bar_label.setWordWrap(True)

        step3 = QLabel(
            "<h3>3. Use it</h3><p>Select a paper and open the <b>AI</b> tab next to "
            "<b>Details</b>, or use <b>AI ▸ Ask the library…</b>. Larger models (e.g. "
            "<code>qwen3.5:9b</code>) answer better if your computer has the memory; any "
            "model you install from Terminal with <code>ollama pull …</code> appears "
            "under <b>Other installed models</b> below.</p>")
        step3.setWordWrap(True)
        self._others = QComboBox()
        use_other = QPushButton("Use this")
        use_other.clicked.connect(lambda: self._others.currentData() and
                                  self._use(self._others.currentData()))
        self._url = QLineEdit(s.value("ai_url", ai.DEFAULT_URL))
        self._url.setToolTip("Only change this if Ollama runs on another computer")
        self._url.editingFinished.connect(self._save_url)
        form = QFormLayout()
        row_other = QHBoxLayout()
        row_other.addWidget(self._others, 1)
        row_other.addWidget(use_other)
        form.addRow("Other installed models", row_other)
        form.addRow("Ollama address", self._url)
        note = QLabel("<p><i>Models made for another purpose — e.g. the <b>xps-expert</b> "
                      "models built for KherveFitting — carry their own instructions and "
                      "are not meant for reading papers.</i></p>")
        note.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        for w in (intro, self._status, step1):
            lay.addWidget(w)
        lay.addLayout(row1)
        lay.addWidget(self._step2)
        lay.addWidget(self._table, 1)
        lay.addWidget(self._bar_label)
        lay.addWidget(self._bar)
        lay.addWidget(step3)
        lay.addLayout(form)
        lay.addWidget(note)
        end = QHBoxLayout()
        end.addStretch(1)
        end.addWidget(close)
        lay.addLayout(end)
        self.refresh()

    # ----- state -----

    def _client(self) -> ai.Ollama:
        return ai.Ollama(self._url.text().strip() or ai.DEFAULT_URL)

    def _save_url(self):
        QSettings(*SETTINGS).setValue("ai_url", self._url.text().strip() or ai.DEFAULT_URL)
        self.refresh()

    def current(self, installed: list[str]) -> str | None:
        chosen = QSettings(*SETTINGS).value("ai_model", "")
        if chosen in installed:
            return chosen
        return self._client().default_model() if installed else None

    def refresh(self) -> None:
        c = self._client()
        running = c.running()
        try:
            installed = [m.name for m in c.models()] if running else []
        except ai.AIError:
            installed, running = [], False
        current = self.current(installed)
        mem = memory_gb()
        if running:
            self._status.setText(
                f"<p>● <b>Ollama {c.version()} is running</b> — {len(installed)} model(s) "
                f"installed. KherveRef uses: <b>{current or 'none yet — install one below'}"
                "</b>.</p>")
        elif ollama_installed():
            self._status.setText(
                "<p>○ <b>Ollama is installed but not running.</b> Open it (on a Mac: "
                "Applications ▸ Ollama), then press <b>Check again</b>.</p>")
        else:
            self._status.setText(
                "<p>○ <b>Ollama is not installed yet.</b> Follow step 1, then press "
                "<b>Check again</b>.</p>")
        advice = ""
        if mem:
            pick = "granite4:micro-h" if mem <= 8.5 else "qwen3.5:4b"
            advice = (f" This computer has <b>{mem:.0f} GB</b> of memory: "
                      f"<b>{pick}</b> suits it best.")
        self._step2.setText(
            "<h3>2. Install a model, and choose it</h3><p>A model is the AI itself. Bigger "
            "models write better but are slower and need more memory — as a rough guide, "
            "keep the model under a third of the computer's memory." + advice +
            f" Each name links to its page on <a href='{LIBRARY_URL}'>ollama.com</a>, "
            "where larger and smaller versions are listed.</p>")
        self._table.setRowCount(0)
        for name, size, maker, good in RECOMMENDED:
            r = self._table.rowCount()
            self._table.insertRow(r)
            link = QLabel(f"<a href='{LIBRARY_URL}{name.split(':')[0]}'>{name}</a><br>"
                          f"<span style='color:gray'>{maker}</span>")
            link.setOpenExternalLinks(True)
            link.setContentsMargins(6, 4, 6, 4)
            self._table.setCellWidget(r, 0, link)
            self._table.setItem(r, 1, QTableWidgetItem(size))
            self._table.setItem(r, 2, QTableWidgetItem(good))
            self._table.setCellWidget(r, 3, self._button(name, installed, current, running))
        self._table.resizeRowsToContents()
        self._others.clear()
        listed = {n for n, *_ in RECOMMENDED}
        for n in installed:
            if n not in listed:
                self._others.addItem(n + ("  (in use)" if n == current else ""), n)

    def _button(self, name, installed, current, running) -> QPushButton:
        if name == current:
            b = QPushButton("In use ✓")
            b.setEnabled(False)
        elif name in installed:
            b = QPushButton("Use this")
            b.clicked.connect(lambda: self._use(name))
        else:
            b = QPushButton("Install")
            b.setEnabled(running and self._job is None)
            b.setToolTip("Downloads the model through Ollama"
                         if running else "Start Ollama first (step 1)")
            b.clicked.connect(lambda: self._install(name))
        return b

    def _use(self, name: str) -> None:
        QSettings(*SETTINGS).setValue("ai_model", name)
        self.refresh()

    def _install(self, name: str) -> None:
        if self._job is not None:
            return
        self._bar.show()
        self._bar.setRange(0, 0)
        self._bar_label.setText(f"Installing {name}…")
        job = _PullJob(self._client().url, name, self)

        def show(frac: float, text: str):
            if frac < 0:
                self._bar.setRange(0, 0)
            else:
                self._bar.setRange(0, 1000)
                self._bar.setValue(int(frac * 1000))
            self._bar_label.setText(f"Installing {name}: {text}")

        errors = []
        job.progress.connect(show)
        job.failed.connect(errors.append)

        def finished():
            self._job = None
            self._bar.hide()
            if errors:
                self._bar_label.setText(f"Could not install {name}: {errors[0]}")
            else:
                self._bar_label.setText(f"{name} is installed and in use.")
                QSettings(*SETTINGS).setValue("ai_model", name)
            self.refresh()
        job.finished.connect(finished)
        self._job = job
        self.refresh()
        job.start()

    def reject(self):
        self.accept()

    def accept(self):
        if self._job is not None:
            self._job.stop()
            self._job.wait(5000)
        super().accept()
