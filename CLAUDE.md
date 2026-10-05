# CLAUDE.md — working conventions for KherveRef

Project: reference manager for the Kherve suite — feeds citations to
KherveTeX, opens attachments in KhervePDF, exports BibLaTeX.
Stack: Python 3.12+, PySide6, PyMuPDF, pygit2, qtawesome.
Siblings (checked out side by side in the same parent folder):
`../KherveTeX` (kherveDOC), `../KhervePDF`.

## Branching: `dev` is the working branch

All work goes on `dev` in the main checkout — never in a git worktree or
on a throwaway `claude/<name>` branch (the user watches `dev` in
PyCharm's Git Log). `main` lags behind `dev` until the user asks for a
merge; then fast-forward or merge on `main`, push, return to `dev`.

Workflow for every change:
1. Confirm the main checkout and `dev`
   (`git rev-parse --abbrev-ref HEAD` → `dev`).
2. Make the edits.
3. Run `python -m pytest tests -q` (the project venv is `.venv/`) and
   make sure everything passes.
4. `git add` the specific files changed (never bare `git add -A`).
5. Commit with a HEREDOC message explaining the **why**.
6. `git push`. If it fails, report it; do not retry destructively.

Always commit and push after any change, without being asked.

## Versioning

`kherveref/__init__.py` holds `__version__ = "<major>.<minor>"` only; the
title bar appends `.<commit_count>+<sha7>` from pygit2. Bump the
**minor** in the same commit as any user-visible change (features, bug
fixes, changes to exported BibTeX). Pure refactors, docs and test-only
commits do not bump. When in doubt, bump. Major only on request.

## Decisions already made (don't re-litigate)

- **Library = folder + Git.** `library.json`, `collections.json`,
  `entries/<key>.json` (one reference per file), `files/<key>.pdf`.
  Anything in `.kherveref/` (search index) is a rebuildable cache and is
  git-ignored. Sync is through a **Git remote**; push/pull use the git
  CLI so the user's own credentials work.
- **Internal model is CSL-JSON-like; BibLaTeX is the default export**
  (`journaltitle`, `date`, …), with classic BibTeX as an option.
  Deterministic output: same library ⇒ byte-identical `.bib`.
- **Citation keys are never renamed** once created — documents cite them.
- **KherveTeX bundles the `.bib`** inside `.ktexz`, so a document still
  compiles on a PC without KherveRef. KherveTeX reads libraries
  read-only from disk; it must not need KherveRef running.
- **Zotero import comes later**, after the KherveTeX integration.

## Architectural invariants

- Comments only when the *why* is non-obvious.
- `library.py` owns the on-disk layout; nothing else builds paths into a
  library by hand.
- Auto-commits use the user's git identity from their git config.
- Icons are qtawesome glyphs or QPainter drawings (`icons.py`,
  `appmark.py`) — never ship PNG/SVG files.
- `themes.py` is shared with KhervePDF/KherveTeX: keep the theme set in
  step with theirs.
- Tests never touch the user's real QSettings (`tests/conftest.py`).
- Any change to model, library, BibTeX or git code comes with tests in
  the same commit.
