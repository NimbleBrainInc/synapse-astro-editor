"""File-tree tools scoped to the workspace repo.

All paths are interpreted relative to the repo root and validated to stay
inside it (no escaping via .. or absolute paths).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..state import SESSION


class NoRepoError(RuntimeError):
    """Raised when a file tool is called before the workspace is ready."""


class PathEscapeError(ValueError):
    """Raised when a requested path resolves outside the repo root."""


SKIP_DIRS = frozenset({"node_modules", ".git", "dist", ".astro"})


def _root() -> Path:
    if SESSION.repo_path is None:
        raise NoRepoError(
            "Workspace not ready. Configure GITHUB_REPO_URL/GITHUB_TOKEN and retry."
        )
    return SESSION.repo_path


def _resolve(rel: str) -> Path:
    root = _root().resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(f"Path {rel!r} escapes repo root") from exc
    return candidate


def read_file(path: str, max_bytes: int = 200_000) -> str:
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    data = p.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace the unique occurrence of old_string in the file."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    contents = p.read_text(encoding="utf-8")
    count = contents.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {path}")
    if count > 1:
        raise ValueError(
            f"old_string is not unique in {path} ({count} matches) — narrow it"
        )
    p.write_text(contents.replace(old_string, new_string, 1), encoding="utf-8")
    return f"edited {path}"


def write_file(path: str, content: str) -> str:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {path}"


def list_dir(path: str = ".") -> list[str]:
    p = _resolve(path)
    if not p.is_dir():
        raise NotADirectoryError(path)
    root = _root().resolve()
    entries = []
    for entry in sorted(p.iterdir()):
        if entry.name in SKIP_DIRS:
            continue
        rel = str(entry.relative_to(root))
        entries.append(rel + ("/" if entry.is_dir() else ""))
    return entries


def grep(pattern: str, path: str = ".", max_results: int = 100) -> list[dict]:
    p = _resolve(path)
    regex = re.compile(pattern)
    hits: list[dict] = []
    targets = [p] if p.is_file() else [
        f for f in p.rglob("*")
        if f.is_file() and not _is_skipped(f)
    ]
    root = _root().resolve()
    for f in targets:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append({"file": str(f.relative_to(root)), "line": i, "text": line.rstrip()})
                if len(hits) >= max_results:
                    return hits
    return hits


def _is_skipped(p: Path) -> bool:
    return bool(set(p.parts) & SKIP_DIRS)
