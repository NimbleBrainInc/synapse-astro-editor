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


def upload_asset(filename: str, base64_data: str, dest_dir: str = "public/uploads") -> dict:
    """Decode base64 bytes, write under <dest_dir>/<filename>, return the
    site-relative URL path (e.g. `/uploads/hero.png`) the agent should
    reference in markdown / JSX.

    `dest_dir` is repo-relative, defaults to `public/uploads`. Astro serves
    everything under `public/` from the site root, so a file at
    `public/uploads/hero.png` is fetchable at `/uploads/hero.png`.

    Filename is sanitized: only basename is honored (no path components),
    and characters outside `[A-Za-z0-9._-]` are rejected so the agent can't
    accidentally land bytes outside the intended directory.

    Size cap matches the platform's tool-call limit (1 MB JSON ≈ 750 KB
    binary). Caller decoding errors raise — the agent should re-encode and
    retry, or the user should resize the asset.
    """
    import base64
    import string

    if not filename:
        raise ValueError("filename is required")
    # Reject any path component — basename only.
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(
            f"filename must be a plain filename (no path components, no leading dot) — got {filename!r}",
        )
    allowed = set(string.ascii_letters + string.digits + "._-")
    if not all(c in allowed for c in filename):
        raise ValueError(
            "filename contains invalid characters; allowed: letters, digits, '.', '_', '-'",
        )

    # Decode and validate. base64.b64decode is permissive about whitespace
    # but rejects malformed payloads with binascii.Error.
    try:
        # Strip data-URL prefix if the caller forgot to.
        if base64_data.startswith("data:"):
            comma = base64_data.find(",")
            if comma == -1:
                raise ValueError("data: URL has no comma separator")
            base64_data = base64_data[comma + 1 :]
        raw = base64.b64decode(base64_data, validate=True)
    except Exception as exc:
        raise ValueError(f"base64_data is not valid base64: {exc}") from exc

    if not raw:
        raise ValueError("base64_data decoded to zero bytes")

    # Resolve dest_dir under the repo root (with the same path-escape guard
    # as everything else).
    dest = _resolve(dest_dir.rstrip("/"))
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename
    # Resolve once more to catch the filename being a clever symlink etc.
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(_root().resolve())
    except ValueError as exc:
        raise PathEscapeError(f"asset path {filename!r} escapes repo root") from exc

    target_resolved.write_bytes(raw)

    # Compute the public URL. Anything under `public/` is served from `/`;
    # anything else goes through Vite's static handling and the agent
    # references it by repo-relative path.
    root = _root().resolve()
    rel = str(target_resolved.relative_to(root))
    if rel.startswith("public/"):
        url = "/" + rel[len("public/"):]
    else:
        url = rel
    return {
        "path": rel,
        "url": url,
        "bytes": len(raw),
    }


def delete_file(path: str) -> str:
    """Delete a single file (no recursion). The caller auto-commits the
    deletion afterward; failure to find the file raises so the agent can
    react instead of silently no-op'ing."""
    p = _resolve(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.is_dir():
        raise IsADirectoryError(
            f"{path} is a directory. This tool deletes single files only — "
            f"removing whole directories is intentionally not supported.",
        )
    p.unlink()
    return f"deleted {path}"


def multi_edit_file(path: str, edits: list[dict]) -> str:
    """Apply a list of {old_string, new_string} edits to a file in one
    atomic write. Each `old_string` must match exactly once in the
    intermediate state of the file (after prior edits in this call have
    been applied) — same uniqueness rule as `edit_file`.

    Atomicity: the file is read once, all edits are applied to the
    in-memory string in order, the result is written once. If any edit
    fails the file is not touched. The caller commits the result as one
    commit (and triggers one rebuild) instead of N.
    """
    if not edits:
        raise ValueError("edits must contain at least one {old_string, new_string} pair")
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    contents = p.read_text(encoding="utf-8")
    for i, edit in enumerate(edits):
        old = edit.get("old_string")
        new = edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError(
                f"edits[{i}]: old_string and new_string must be strings",
            )
        if old == new:
            raise ValueError(
                f"edits[{i}]: old_string and new_string are identical — no-op",
            )
        count = contents.count(old)
        if count == 0:
            raise ValueError(
                f"edits[{i}]: old_string not found in {path} (after prior edits applied)",
            )
        if count > 1:
            raise ValueError(
                f"edits[{i}]: old_string matches {count} times in {path} — "
                f"narrow the match so it's unique",
            )
        contents = contents.replace(old, new, 1)
    p.write_text(contents, encoding="utf-8")
    return f"applied {len(edits)} edit(s) to {path}"


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
