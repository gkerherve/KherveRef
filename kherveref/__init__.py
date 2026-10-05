"""KherveRef — a reference manager for KherveTeX and KhervePDF."""
from __future__ import annotations

from pathlib import Path

# "<major>.<minor>" only: the patch (.N commit count) and +sha7 are
# appended from pygit2 at runtime. See CLAUDE.md for when to bump.
__version__ = "0.14"


def _git_build_info() -> tuple[int, str] | None:
    """(commit_count, short_sha) of this checkout, or None when it
    can't be read (frozen build, no pygit2, unborn HEAD...)."""
    repo_root = Path(__file__).resolve().parent.parent
    try:
        import pygit2
        if not (repo_root / ".git").exists():
            return None
        repo = pygit2.Repository(str(repo_root))
        if repo.head_is_unborn:
            return None
        head = repo.head.target
        count = sum(1 for _ in repo.walk(head, pygit2.GIT_SORT_NONE))
        return count, str(head)[:7]
    except Exception:
        return None


def version_string() -> str:
    """"v<major>.<minor>.<commit_count>+<sha7>", or "v<major>.<minor>"
    when git info isn't available."""
    info = _git_build_info()
    if info is None:
        return f"v{__version__}"
    count, sha = info
    return f"v{__version__}.{count}+{sha}"
