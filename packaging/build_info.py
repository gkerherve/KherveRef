"""Stamp kherveref/BUILD ("<commit_count>+<sha7>") from git.

A frozen build has no .git, so this file is how the installed app knows
its own number. Rewritten on every build (KherveRef.spec calls it) and
never trusted from disk; refuses a shallow clone, which would silently
ship the wrong commit count (CI uses fetch-depth: 0).

    python packaging/build_info.py        # prints the release version
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def stamp() -> str:
    """Write kherveref/BUILD and return "<major>.<minor>.<count>"."""
    if _git("rev-parse", "--is-shallow-repository") == "true":
        raise SystemExit("shallow clone: the commit count would be wrong "
                         "(use fetch-depth: 0)")
    count = int(_git("rev-list", "--count", "HEAD"))
    sha = _git("rev-parse", "--short=7", "HEAD")
    (ROOT / "kherveref" / "BUILD").write_text(f"{count}+{sha}\n", encoding="ascii")
    sys.path.insert(0, str(ROOT))
    from kherveref import __version__
    version = f"{__version__}.{count}"
    print(f"KherveRef {version}+{sha}", flush=True)
    return version


if __name__ == "__main__":
    stamp()
