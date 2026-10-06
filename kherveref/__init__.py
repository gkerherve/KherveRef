"""KherveRef — a reference manager for KherveTeX and KhervePDF."""
from __future__ import annotations

from pathlib import Path

# "<major>.<minor>" only: the patch (.N commit count) and +sha7 are
# appended from pygit2 at runtime. See CLAUDE.md for when to bump.
__version__ = "0.25"


# A frozen build has no .git: packaging stamps "<commit_count>+<sha7>"
# into this git-ignored file at build time (packaging/build_info.py).
BUILD_FILE = Path(__file__).resolve().parent / "BUILD"


def _stamped_build_info() -> tuple[int, str] | None:
    try:
        count, sha = BUILD_FILE.read_text(encoding="ascii").strip().split("+")
        return int(count), sha
    except (OSError, ValueError):
        return None


def _git_build_info() -> tuple[int, str] | None:
    """(commit_count, short_sha) of this checkout, else of the build's
    stamped BUILD file, or None when neither can be read."""
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").exists():
        return _stamped_build_info()
    try:
        import pygit2
        repo = pygit2.Repository(str(repo_root))
        if repo.head_is_unborn:
            return None
        head = repo.head.target
        count = sum(1 for _ in repo.walk(head, pygit2.GIT_SORT_NONE))
        return count, str(head)[:7]
    except Exception:
        return _stamped_build_info()


def version_string() -> str:
    """"v<major>.<minor>.<commit_count>+<sha7>", or "v<major>.<minor>"
    when git info isn't available."""
    info = _git_build_info()
    if info is None:
        return f"v{__version__}"
    count, sha = info
    return f"v{__version__}.{count}+{sha}"


def release_version() -> str:
    """"<major>.<minor>.<commit_count>" — the number releases, tags and
    installer file names use; the title bar shows it plus "+<sha7>"."""
    info = _git_build_info()
    return f"{__version__}.{info[0]}" if info else __version__
