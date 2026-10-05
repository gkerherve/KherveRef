"""What the AI has told you, kept: each paper's summary and every
question asked about it, and the questions asked of the library.

Stored in the library (.kherveref/ai/), so it is versioned and synced
with everything else and is there the next time the paper is opened.

    .kherveref/ai/<key>.json    {"summary": {...} | null, "qa": [{...}, ...]}
    .kherveref/ai/_library.json [{"question", "scope", "mode", "answer", ...}]
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from .library import Library

LIBRARY_FILE = "_library.json"


def _dir(lib: Library) -> Path:
    return lib.data_dir / "ai"


def _path(lib: Library, key: str) -> Path:
    return _dir(lib) / f"{key}.json"


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def load(lib: Library, key: str) -> dict:
    try:
        d = json.loads(_path(lib, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"summary": d.get("summary"), "qa": list(d.get("qa", []))}


def save_summary(lib: Library, key: str, text: str, model: str) -> None:
    d = load(lib, key)
    d["summary"] = {"text": text.strip(), "model": model, "date": _now()}
    _write(_path(lib, key), d)


def add_question(lib: Library, key: str, question: str, answer: str,
                 model: str) -> None:
    d = load(lib, key)
    d["qa"].append({"question": question.strip(), "answer": answer.strip(),
                    "model": model, "date": _now()})
    _write(_path(lib, key), d)


def delete_question(lib: Library, key: str, index: int) -> None:
    d = load(lib, key)
    if 0 <= index < len(d["qa"]):
        d["qa"].pop(index)
        _write(_path(lib, key), d)


def has_any(lib: Library, key: str) -> bool:
    d = load(lib, key)
    return bool(d["summary"] or d["qa"])


def rename(lib: Library, old: str, new: str) -> None:
    src = _path(lib, old)
    if src.exists():
        os.replace(src, _path(lib, new))


def delete(lib: Library, key: str) -> None:
    _path(lib, key).unlink(missing_ok=True)


def history(lib: Library) -> list[dict]:
    try:
        return json.loads((_dir(lib) / LIBRARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def add_history(lib: Library, question: str, scope: str, mode: str,
                answer: str, model: str, sources: list[str]) -> None:
    items = history(lib)
    items.append({"question": question.strip(), "scope": scope, "mode": mode,
                  "answer": answer.strip(), "model": model, "sources": sources,
                  "date": _now()})
    _write(_dir(lib) / LIBRARY_FILE, items)


def as_markdown(record: dict) -> str:
    """A paper's kept summary and questions, newest question last."""
    parts = []
    s = record.get("summary")
    if s:
        parts.append(f"## Summary\n*{s['model']} · {s['date']}*\n\n{s['text']}")
    for qa in record.get("qa", []):
        parts.append(f"### Q: {qa['question']}\n*{qa['model']} · {qa['date']}*\n\n"
                     f"{qa['answer']}")
    return "\n\n---\n\n".join(parts)
