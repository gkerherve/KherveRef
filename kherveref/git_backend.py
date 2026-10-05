"""Git versioning and sync for a library folder.

The library root is its own repository. Every change is committed with
the user's own git identity (read from their git config), so the
history looks the same as CLI commits. Push and pull go through the git
CLI, which already knows the user's SSH keys / credential manager / gh
login — pygit2's credential plumbing would have to reinvent that.

Every function degrades gracefully when pygit2 is missing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

try:
    import pygit2
    _OK = True
except Exception:
    pygit2 = None  # type: ignore
    _OK = False

DEFAULT_BRANCH = "main"


def is_available() -> bool:
    return _OK


def _repo(root: Path):
    if not _OK or not (Path(root) / ".git").exists():
        return None
    try:
        return pygit2.Repository(str(root))
    except Exception:
        return None


def init_repo(root: Path) -> bool:
    """Make *root* a repository (no-op if it already is one)."""
    if not _OK:
        return False
    if (Path(root) / ".git").exists():
        return True
    try:
        pygit2.init_repository(str(root), bare=False,
                               initial_head=DEFAULT_BRANCH)
        return True
    except Exception:
        return False


def _signature(repo) -> "pygit2.Signature":
    try:
        return repo.default_signature
    except Exception:
        return pygit2.Signature("KherveRef", "kherveref@local")


def has_changes(root: Path) -> bool:
    repo = _repo(root)
    if repo is None:
        return False
    try:
        return any(flags != pygit2.GIT_STATUS_IGNORED
                   for flags in repo.status().values())
    except Exception:
        return False


def commit_all(root: Path, message: str) -> Optional[str]:
    """Stage every change in the library (additions, edits, deletions)
    and commit it. Returns the short sha, or None when there was nothing
    to commit or git is unavailable."""
    if not init_repo(root):
        return None
    repo = _repo(root)
    if repo is None or (not repo.head_is_unborn and not has_changes(root)):
        return None
    try:
        index = repo.index
        index.read()
        index.add_all()
        # add_all() never drops entries for files deleted from the folder.
        workdir = Path(repo.workdir)
        for path in [e.path for e in index if not (workdir / e.path).exists()]:
            index.remove(path)
        index.write()
        tree = index.write_tree()
        parents = [] if repo.head_is_unborn else [repo.head.target]
        sig = _signature(repo)
        oid = repo.create_commit("HEAD", sig, sig, message, tree, parents)
        return str(oid)[:7]
    except Exception:
        return None


def head_tree(root: Path) -> Optional[str]:
    """Id of the tree HEAD points at: a snapshot of the whole library."""
    repo = _repo(root)
    if repo is None or repo.head_is_unborn:
        return None
    try:
        return str(repo.head.peel(pygit2.Tree).id)
    except Exception:
        return None


def _entry_id(tree, path: str) -> Optional[str]:
    try:
        return str(tree[path].id)
    except KeyError:
        return None


def change_paths(root: Path, from_tree: str, to_tree: str) -> list[str]:
    """Files that differ between two snapshots."""
    repo = _repo(root)
    if repo is None:
        return []
    diff = repo.get(from_tree).diff_to_tree(repo.get(to_tree))
    return [d.new_file.path if d.status_char() != "D" else d.old_file.path
            for d in diff.deltas]


def can_apply_change(root: Path, expected_tree: str, paths: list[str]) -> bool:
    """True when every file in *paths* is, at HEAD, exactly as it was in
    snapshot *expected_tree* — i.e. nothing touched them since."""
    repo = _repo(root)
    if repo is None or repo.head_is_unborn:
        return False
    head = repo.head.peel(pygit2.Tree)
    expected = repo.get(expected_tree)
    return all(_entry_id(head, p) == _entry_id(expected, p) for p in paths)


def apply_change(root: Path, from_tree: str, to_tree: str,
                 message: str) -> Optional[str]:
    """Replay the change between two snapshots onto the library's files
    and commit it as a new change. Undo is apply_change(after, before);
    Redo is apply_change(before, after). History is never rewritten, and
    files the change did not touch are left alone."""
    repo = _repo(root)
    if repo is None:
        return None
    try:
        workdir = Path(repo.workdir)
        diff = repo.get(from_tree).diff_to_tree(repo.get(to_tree))
        for delta in diff.deltas:
            if delta.status_char() == "D":
                (workdir / delta.old_file.path).unlink(missing_ok=True)
            else:
                dest = workdir / delta.new_file.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(repo[delta.new_file.id].data)
    except Exception:
        return None
    return commit_all(root, message)


def history(root: Path, limit: int = 100
            ) -> list[tuple[str, str, int, str]]:
    """(sha7, author, unix_time, subject) tuples, newest first."""
    repo = _repo(root)
    if repo is None or repo.head_is_unborn:
        return []
    out = []
    try:
        for c in repo.walk(repo.head.target,
                           pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME):
            if len(out) >= limit:
                break
            subject = c.message.splitlines()[0] if c.message else ""
            out.append((str(c.id)[:7], c.author.name, int(c.commit_time),
                        subject))
    except Exception:
        pass
    return out


def current_branch(root: Path) -> Optional[str]:
    repo = _repo(root)
    if repo is None or repo.head_is_unborn:
        return None
    try:
        return repo.head.shorthand
    except Exception:
        return None


def get_remote(root: Path, name: str = "origin") -> Optional[str]:
    repo = _repo(root)
    if repo is None:
        return None
    try:
        return repo.remotes[name].url
    except Exception:
        return None


def set_remote(root: Path, url: str, name: str = "origin") -> bool:
    if not init_repo(root):
        return False
    repo = _repo(root)
    if repo is None:
        return False
    try:
        if name in {r.name for r in repo.remotes}:
            repo.remotes.set_url(name, url)
        else:
            repo.remotes.create(name, url)
        return True
    except Exception:
        return False


def _git(root: Path, *args: str, timeout: int = 120) -> tuple[bool, str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return False, "git was not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timed out"
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out or ("done" if r.returncode == 0
                                      else f"git {args[0]} failed")


def push(root: Path, remote: str = "origin") -> tuple[bool, str]:
    branch = current_branch(root)
    if branch is None:
        return False, "Nothing to push yet"
    return _git(root, "push", "-u", remote, branch)


def pull(root: Path, remote: str = "origin") -> tuple[bool, str]:
    """Fetch and merge the remote branch. A merge (not a rebase) keeps
    every machine's commits intact in the history."""
    branch = current_branch(root)
    if branch is None:
        return False, "Commit something before pulling"
    return _git(root, "pull", "--no-rebase", "--no-edit", remote, branch)
