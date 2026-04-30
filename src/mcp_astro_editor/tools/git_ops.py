"""Git wrappers — operate inside the workspace repo with token injected per-call.

Uses `git -c http.extraheader=AUTHORIZATION: bearer <TOKEN>` for network ops so
the token never gets written to .git/config.
"""

from __future__ import annotations

import asyncio
import sys

from ..spawn import create_subprocess_exec
from ..state import SESSION
from ..workspace import _scrub_token, load_config
from .files import NoRepoError


async def _git(*args: str, auth: bool = False) -> str:
    if SESSION.repo_path is None:
        raise NoRepoError("Workspace not ready.")
    cmd = ["git"]
    if auth:
        cfg = load_config()
        cmd += ["-c", f"http.extraheader={cfg.http_extraheader}"]
    cmd += list(args)
    proc = await create_subprocess_exec(
        *cmd,
        cwd=str(SESSION.repo_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = _scrub_token(stderr.decode("utf-8", errors="replace").strip())
        # Log only the subcommand, not the full argv (which may contain auth headers).
        print(f"[git] {args[0] if args else '(no args)'} failed: {msg}", file=sys.stderr)
        raise RuntimeError(msg or f"git {args[0] if args else ''} failed")
    return stdout.decode("utf-8", errors="replace")


async def status() -> str:
    return await _git("status", "--short")


async def auto_commit(message: str) -> str | None:
    """Stage all changes and commit only if there's an actual diff. Returns
    the new HEAD sha, or None if the working tree was already clean.
    Used by edit_file/write_file to commit each change as it happens."""
    await _git("add", "-A")
    staged = (await _git("diff", "--cached", "--name-only")).strip()
    if not staged:
        return None
    try:
        await _git("commit", "-m", message)
    except RuntimeError as exc:
        if "nothing to commit" in str(exc).lower():
            return None
        raise
    return (await _git("rev-parse", "HEAD")).strip()


# Backwards-compat alias for older callers.
commit_all = auto_commit


async def push(branch: str) -> str:
    return await _git("push", "-u", "origin", branch, auth=True)


async def force_push(branch: str) -> str:
    return await _git("push", "--force-with-lease", "origin", branch, auth=True)


async def current_branch() -> str:
    return (await _git("rev-parse", "--abbrev-ref", "HEAD")).strip()


async def checkout(branch: str, create: bool = False) -> str:
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    return await _git(*args)


async def reset_hard(ref: str) -> str:
    return await _git("reset", "--hard", ref)


async def list_commits_ahead(branch: str, base_ref: str) -> list[dict]:
    """Return commits on `branch` that aren't on `base_ref`, oldest first.

    Each entry: {sha, short_sha, message, when}. Empty list if branch is at base.
    """
    out = await _git(
        "log",
        f"{base_ref}..{branch}",
        "--pretty=format:%H%x1f%h%x1f%s%x1f%cI",
        "--reverse",
    )
    out = out.strip()
    if not out:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        rows.append({"sha": parts[0], "short_sha": parts[1], "message": parts[2], "when": parts[3]})
    return rows


async def list_changed_files(branch: str, base_ref: str) -> list[dict]:
    """Return files that differ between `base_ref` and `branch`, deduplicated
    across the commit range — i.e. the *net* set of files the draft would
    introduce if merged into base.

    Each entry: {path, status} where status is "added" / "modified" /
    "deleted" / "renamed" / "type_changed" / "other".

    Uses `git diff base...branch --name-status` (three-dot syntax — diffs
    base's merge-base against branch tip), so a file edited five times
    counts once and a file added then removed counts zero. That's the
    answer to "what's about to ship," not "how many commits did we make."
    """
    out = await _git("diff", f"{base_ref}...{branch}", "--name-status", "-z")
    out = out.strip("\x00")
    if not out:
        return []
    # `-z` separates entries with NUL and uses NUL between status and path
    # (and between two paths for renames). Tokens come in as
    # status, path[, path2_for_rename], status, path, ...
    tokens = out.split("\x00")
    rows: list[dict] = []
    i = 0
    while i < len(tokens):
        status_raw = tokens[i]
        if not status_raw:
            i += 1
            continue
        # Rename status is "R<percent>"; copy is "C<percent>". Path follows
        # as TWO tokens: src, dst.
        if status_raw.startswith(("R", "C")):
            if i + 2 >= len(tokens):
                break
            path = tokens[i + 2]  # destination path
            rows.append({"path": path, "status": "renamed"})
            i += 3
            continue
        if i + 1 >= len(tokens):
            break
        path = tokens[i + 1]
        status = _DIFF_STATUS.get(status_raw[:1], "other")
        rows.append({"path": path, "status": status})
        i += 2
    return rows


_DIFF_STATUS = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "T": "type_changed",
}


async def revert(sha: str) -> str:
    """Revert a single commit, creating a new revert commit on the current branch."""
    await _git("revert", "--no-edit", sha)
    return (await _git("rev-parse", "HEAD")).strip()


async def revert_file_to_base(path: str, base_ref: str) -> dict:
    """Revert a single file to its state on `base_ref`, then stage the change.
    The caller is expected to commit (or skip-if-noop) afterward.

    Three paths through git's behavior:
      - File exists at base → `checkout base -- path` overwrites the working
        copy with base's version.
      - File doesn't exist at base (it was added on draft) → `git rm` it.
      - File doesn't exist on draft (was deleted) → checkout resurrects it
        from base.

    Returns {action: "checkout"|"rm"|"noop", path}. The "noop" case is when
    the file is already at base content (unusual but possible if a previous
    edit happened to land at the same bytes).
    """
    # Probe whether the path exists in base.
    try:
        await _git("cat-file", "-e", f"{base_ref}:{path}")
        exists_in_base = True
    except RuntimeError:
        exists_in_base = False

    if exists_in_base:
        await _git("checkout", base_ref, "--", path)
        return {"action": "checkout", "path": path}

    # Not in base → it was added on the draft. Remove it.
    # `--ignore-unmatch` keeps us silent if the file was already gone.
    await _git("rm", "-f", "--ignore-unmatch", path)
    return {"action": "rm", "path": path}


async def squash_merge_into(target: str, source: str, message: str) -> str:
    """Squash-merge `source` into `target` and commit. Caller must be on `target`.
    Returns the new HEAD sha."""
    await _git("merge", "--squash", source)
    await _git("commit", "-m", message)
    return (await _git("rev-parse", "HEAD")).strip()


async def fetch(remote: str = "origin") -> str:
    return await _git("fetch", remote, auth=True)
