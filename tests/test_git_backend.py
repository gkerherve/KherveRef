import subprocess

import pygit2
import pytest

from kherveref import git_backend, library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    # A fixed identity so commits work on CI machines without git config.
    for k, v in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                 ("GIT_AUTHOR_EMAIL", "t@x"), ("GIT_COMMITTER_EMAIL", "t@x")):
        monkeypatch.setenv(k, v)
    return library.create_library(tmp_path / "lib")


def test_first_commit_on_main(lib):
    sha = git_backend.commit_all(lib.root, "Create library")
    assert sha and len(sha) == 7
    assert git_backend.current_branch(lib.root) == "main"
    tracked = {e.path for e in pygit2.Repository(str(lib.root)).index}
    assert {"library.json", "collections.json", ".gitignore",
            "entries/.gitkeep", "files/.gitkeep"} <= tracked


def test_nothing_to_commit_returns_none(lib):
    git_backend.commit_all(lib.root, "Create")
    assert git_backend.commit_all(lib.root, "Again") is None


def test_commits_additions_and_deletions(lib):
    git_backend.commit_all(lib.root, "Create")
    entry = lib.entries_dir / "smith2020.json"
    entry.write_text("{}\n")
    assert git_backend.commit_all(lib.root, "Add smith2020")
    entry.unlink()
    assert git_backend.commit_all(lib.root, "Delete smith2020")
    tracked = {e.path for e in pygit2.Repository(str(lib.root)).index}
    assert "entries/smith2020.json" not in tracked
    subjects = [h[3] for h in git_backend.history(lib.root)]
    assert subjects == ["Delete smith2020", "Add smith2020", "Create"]


def test_cache_is_not_committed(lib):
    lib.cache_dir.mkdir()
    (lib.cache_dir / "index.sqlite").write_bytes(b"x")
    git_backend.commit_all(lib.root, "Create")
    tracked = {e.path for e in pygit2.Repository(str(lib.root)).index}
    assert not any(p.startswith(".kherveref") for p in tracked)
    assert not git_backend.has_changes(lib.root)


def test_remote_round_trip(lib):
    assert git_backend.get_remote(lib.root) is None
    assert git_backend.set_remote(lib.root, "https://example.com/a.git")
    assert git_backend.set_remote(lib.root, "https://example.com/b.git")
    assert git_backend.get_remote(lib.root) == "https://example.com/b.git"


def test_push_and_pull_through_bare_remote(lib, tmp_path):
    bare = tmp_path / "remote.git"
    pygit2.init_repository(str(bare), bare=True, initial_head="main")
    git_backend.commit_all(lib.root, "Create")
    git_backend.set_remote(lib.root, str(bare))
    ok, msg = git_backend.push(lib.root)
    assert ok, msg

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    (other / "entries" / "doe2021.json").write_text("{}\n")
    git_backend.commit_all(other, "Add doe2021")
    assert git_backend.push(other)[0]

    ok, msg = git_backend.pull(lib.root)
    assert ok, msg
    assert (lib.entries_dir / "doe2021.json").exists()
