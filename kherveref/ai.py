"""Local AI through Ollama: summarise a paper, answer questions about it
or about a whole collection.

Everything runs on this computer (Ollama's HTTP API on 127.0.0.1:11434
by default); nothing is sent elsewhere. Prompts ground the model in the
paper's own text and ask it to cite pages / citation keys, so answers
can be checked. Qt-free: the window streams through a worker thread.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterator

from . import fulltext
from .library import Library
from .model import Entry

DEFAULT_URL = "http://127.0.0.1:11434"
# Preferred when installed, in order; otherwise the first model listed.
PREFERRED = ("qwen3.5", "qwen3", "gemma3", "ministral", "llama3.2", "llama3.1",
             "mistral", "phi4", "granite4", "gpt-oss", "phi3")
PAPER_CHARS = 60_000        # ~15k tokens: most papers whole
CONTEXT_TOKENS = 32_768


class AIError(Exception):
    pass


@dataclass
class Model:
    name: str
    size: str
    thinking: bool          # supports (and would by default use) reasoning


def same_model(a: str, b: str) -> bool:
    """"phi4-mini" and "phi4-mini:latest" are one model."""
    return a.removesuffix(":latest") == b.removesuffix(":latest")


class Ollama:
    def __init__(self, url: str = DEFAULT_URL):
        self.url = url.rstrip("/")

    def _get(self, path: str, timeout: float = 3) -> dict:
        try:
            with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AIError(f"Ollama is not reachable at {self.url} ({e})") from None

    def version(self) -> str:
        return self._get("/api/version").get("version", "")

    def models(self) -> list[Model]:
        out = []
        for m in self._get("/api/tags").get("models", []):
            caps = m.get("capabilities") or []
            if caps and "completion" not in caps:
                continue        # embedding-only models cannot answer
            out.append(Model(m["name"], m.get("details", {}).get("parameter_size", ""),
                             "thinking" in caps))
        return out

    def default_model(self) -> str | None:
        names = [m.name for m in self.models()]
        for pref in PREFERRED:
            for n in names:
                if n.startswith(pref):
                    return n
        return names[0] if names else None

    def running(self) -> bool:
        try:
            self.version()
            return True
        except AIError:
            return False

    def pull(self, model: str, progress: Callable[[float, str], None],
             cancelled: Callable[[], bool] = lambda: False) -> None:
        """Download *model* (what `ollama pull` does). *progress* gets the
        fraction done (-1 while unknown) and Ollama's status text."""
        req = urllib.request.Request(
            self.url + "/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    if cancelled():
                        raise AIError("cancelled")
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise AIError(d["error"])
                    total, done = d.get("total"), d.get("completed")
                    frac = done / total if total and done is not None else -1
                    status = d.get("status", "")
                    if total:
                        status += f" — {done / 1e9:.1f} of {total / 1e9:.1f} GB" \
                            if done is not None else ""
                    progress(frac, status)
                    if d.get("status") == "success":
                        return
        except urllib.error.HTTPError as e:
            raise AIError(e.read().decode(errors="replace")[:300]) from None
        except (urllib.error.URLError, OSError) as e:
            raise AIError(f"Ollama is not reachable at {self.url} ({e})") from None

    def chat(self, model: str, messages: list[dict],
             cancelled: Callable[[], bool] = lambda: False) -> Iterator[str]:
        """Stream the reply's text."""
        thinking = any(same_model(m.name, model) and m.thinking for m in self.models())
        body = {"model": model, "messages": messages, "stream": True,
                "options": {"num_ctx": CONTEXT_TOKENS, "temperature": 0.2}}
        if thinking:
            # Answers, not pages of reasoning. gpt-oss cannot switch it off,
            # only down to "low".
            body["think"] = "low" if model.startswith("gpt-oss") else False
        req = urllib.request.Request(self.url + "/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                for line in r:
                    if cancelled():
                        return
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise AIError(d["error"])
                    piece = d.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if d.get("done"):
                        return
        except urllib.error.HTTPError as e:
            raise AIError(f"Ollama: {e.read().decode(errors='replace')[:300]}") from None
        except (urllib.error.URLError, OSError) as e:
            raise AIError(f"Ollama is not reachable at {self.url} ({e})") from None


# ------------------------------------------------------------------ #
# Prompts                                                              #
# ------------------------------------------------------------------ #

SYSTEM = ("You are a careful research assistant. Answer only from the text "
          "provided. If the text does not contain the answer, say so plainly. "
          "Use clear British English and Markdown.")


def _describe(e: Entry) -> str:
    authors = "; ".join(p.display() for p in e.authors[:12])
    bits = [f"Title: {e.title}", f"Authors: {authors}" if authors else "",
            f"Published: {e.container()} {e.year}".strip(),
            f"Citation key: {e.key}"]
    if e.abstract:
        bits.append(f"Abstract (from the record): {e.abstract}")
    return "\n".join(b for b in bits if b)


def summary_messages(lib: Library, e: Entry) -> list[dict]:
    body = fulltext.text(lib, e, PAPER_CHARS)
    if not body:
        raise AIError("This reference has no PDF with readable text to summarise"
                      + (" (it may be a scan)." if e.files else "."))
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content":
             f"{_describe(e)}\n\nFull text (page markers in brackets):\n{body}\n\n"
             "Summarise this paper for a researcher deciding whether to read "
             "it. Use exactly these headings:\n"
             "**In one sentence:** …\n### Aim\n### Methods\n### Key findings\n"
             "### Conclusions\n### Limitations\n"
             "Use short bullet points with numbers and materials where the paper "
             "gives them. Add (p. N) after facts taken from a specific page."}]


def question_messages(lib: Library, e: Entry, question: str) -> list[dict]:
    body = fulltext.text(lib, e, PAPER_CHARS)
    if not body:
        raise AIError("This reference has no PDF with readable text.")
    if len(body) >= PAPER_CHARS:
        # Too long to send whole: the passages most related to the question.
        hits = fulltext.search(question, fulltext.passages(lib, [e]), top=12)
        body = "\n".join(f"[page {p.page}]\n{p.text}" for p in hits) or body
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content":
             f"{_describe(e)}\n\nText of the paper:\n{body}\n\n"
             f"Question: {question}\n\nAnswer from the paper only, citing pages "
             "as (p. N)."}]


def library_messages(lib: Library, entries: list[Entry], question: str,
                     top: int = 10) -> tuple[list[dict], list[fulltext.Passage]]:
    """Messages for a question over many papers, and the passages used."""
    hits = fulltext.search(question, fulltext.passages(lib, entries), top=top)
    by_key = {e.key: e for e in entries}
    if not hits:
        raise AIError("None of these papers' text mentions that. Try other words, "
                      "or check the references have PDFs.")
    sources = "\n\n".join(
        f"[{p.key}, p. {p.page}] {by_key[p.key].title} ({by_key[p.key].year})\n{p.text}"
        for p in hits)
    return ([{"role": "system", "content": SYSTEM},
             {"role": "user", "content":
              f"Passages from papers in my reference library, each labelled "
              f"[citation key, page]:\n\n{sources}\n\nQuestion: {question}\n\n"
              "Answer from these passages only. After every statement cite its "
              "source exactly as given, e.g. [smith2020surface, p. 3]. If the "
              "passages don't answer the question, say which papers come closest."}],
            hits)


NOT_COVERED = "Not covered."


def each_paper_messages(lib: Library, e: Entry, question: str) -> list[dict]:
    """The question asked of one paper in a "read every paper" run:
    short, with pages, or exactly NOT_COVERED."""
    msgs = question_messages(lib, e, question)
    msgs[1]["content"] += (
        "\n\nAnswer in at most three sentences, citing pages as (p. N). If this "
        f"paper does not address the question, answer exactly: {NOT_COVERED}")
    return msgs


def overview_messages(question: str, answers: list[tuple[Entry, str]]) -> list[dict]:
    """Combine per-paper answers into one, citing [key]."""
    found = [(e, a) for e, a in answers
             if a.strip() and not a.strip().startswith(NOT_COVERED.rstrip("."))]
    listing = "\n\n".join(f"[{e.key}] {e.title} ({e.year}):\n{a.strip()}"
                           for e, a in found) or "(no paper addresses it)"
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content":
             f"I asked each paper in my library: {question}\n\nTheir answers, "
             f"labelled with citation keys:\n\n{listing}\n\nWrite a short overview "
             "that answers the question across these papers: what they agree on, "
             "where they differ, with numbers where given. Cite every statement as "
             "[citation key]. Do not add facts that are not in the answers."}]
