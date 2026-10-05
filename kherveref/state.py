"""Per-user state shared with processes that do not load Qt (the MCP
server): which libraries this user has opened, most recent first."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FILENAME = "libraries.json"


def state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "KherveRef"


def recent_libraries() -> list[Path]:
    try:
        data = json.loads((state_dir() / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(p) for p in data.get("libraries", []) if isinstance(p, str)]


def remember_library(root: Path, limit: int = 8) -> None:
    root = Path(root).resolve()
    libs = [p for p in recent_libraries() if p != root]
    d = state_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / FILENAME).write_text(json.dumps(
            {"libraries": [str(p) for p in [root] + libs[:limit - 1]]},
            indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
