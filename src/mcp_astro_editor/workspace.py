"""Workspace manager — clones the configured GitHub repo into a known
location and keeps the draft branch in sync.

Configuration comes from environment (mapped from MCPB user_config):
  GITHUB_REPO_URL   HTTPS URL, e.g. https://github.com/owner/repo
  GITHUB_TOKEN      PAT with Contents: Read & Write
  DRAFT_BRANCH      branch the editor commits to (default: astro-editor/draft)
  BASE_BRANCH       production branch (default: main)

Workspace location:
  $MPAK_WORKSPACE/repo    (platform sets MPAK_WORKSPACE per-bundle)
  fallback: $TMPDIR/synapse-astro-editor/repo  (for local dev)

The first workspace-touching call performs the clone + initial checkout.
Subsequent calls are idempotent: verify remote, fetch, ensure draft branch,
pull --ff-only.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class WorkspaceError(RuntimeError):
    """Anything that prevents the workspace from being ready."""


class ConfigError(WorkspaceError):
    """Missing or invalid user_config."""


@dataclass(frozen=True)
class RepoConfig:
    repo_url: str  # https://github.com/owner/repo (no token, no .git)
    token: str  # PAT
    draft_branch: str
    base_branch: str
    owner: str
    repo: str

    @property
    def clone_url(self) -> str:
        """URL with token embedded for HTTPS auth. Never log this."""
        parsed = urlparse(self.repo_url)
        return urlunparse(parsed._replace(netloc=f"x-access-token:{self.token}@{parsed.netloc}"))

    @property
    def public_url(self) -> str:
        """Tokenless URL safe for logging."""
        return self.repo_url

    @property
    def http_extraheader(self) -> str:
        """`http.extraheader` value for git over HTTPS.

        Git's smart-HTTP protocol uses Basic auth, not Bearer — username is
        `x-access-token`, password is the PAT, joined with a colon and
        base64-encoded. Bearer is for the REST API; git endpoints reject it.
        """
        creds = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return f"Authorization: Basic {creds}"


def load_config() -> RepoConfig:
    """Read env and validate. Raises ConfigError if unusable."""
    repo_url = (os.getenv("GITHUB_REPO_URL") or "").strip()
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    if not repo_url:
        raise ConfigError("GITHUB_REPO_URL is not set")
    if not token:
        raise ConfigError("GITHUB_TOKEN is not set")

    # Normalize: strip .git suffix, strip trailing slash.
    repo_url = re.sub(r"\.git/?$", "", repo_url.rstrip("/"))

    parsed = urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"GITHUB_REPO_URL must be an HTTPS URL, got: {parsed.scheme!r}")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ConfigError(f"Cannot parse owner/repo from {repo_url!r}")
    owner, repo = parts[0], parts[1]

    return RepoConfig(
        repo_url=repo_url,
        token=token,
        draft_branch=os.getenv("DRAFT_BRANCH") or "astro-editor/draft",
        base_branch=os.getenv("BASE_BRANCH") or "main",
        owner=owner,
        repo=repo,
    )


def workspace_root() -> Path:
    base = os.getenv("MPAK_WORKSPACE") or str(Path(tempfile.gettempdir()) / "synapse-astro-editor")
    return Path(base).expanduser().resolve() / "repo"


async def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git; raise WorkspaceError on failure with token scrubbed from output."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = _scrub_token(stderr.decode("utf-8", errors="replace"))
        out = _scrub_token(stdout.decode("utf-8", errors="replace"))
        raise WorkspaceError(
            f"git {' '.join(_scrub_token(a) for a in args)} failed: {err or out}".strip()
        )
    return stdout.decode("utf-8", errors="replace")


# Match every shape a GitHub token can appear in when we shell to git:
#   https://x-access-token:TOKEN@host/...
#   bearer TOKEN  (from http.extraheader)
#   github_pat_... / ghp_... / ghs_... / ghu_... / gho_... / ghr_... (raw tokens)
_SCRUB_PATTERNS = [
    (re.compile(r"x-access-token:[^@\s]+@"), "x-access-token:***@"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=/+]+"), "Bearer ***"),
    (re.compile(r"(?i)basic\s+[A-Za-z0-9_\-\.=/+]+"), "Basic ***"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]+"), "github_pat_***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]+"), "gh*_***"),
]


def _scrub_token(s: str) -> str:
    for pattern, replacement in _SCRUB_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


async def ensure_workspace() -> Path:
    """Clone (if needed), fetch, and check out the draft branch. Return the repo path.

    Idempotent — safe to call on every tool invocation.
    """
    cfg = load_config()
    repo_dir = workspace_root()

    # Fresh clone path
    if not (repo_dir / ".git").exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"[workspace] cloning {cfg.public_url} → {repo_dir}", file=sys.stderr)
        # Clone into an empty directory. If repo_dir exists but has contents, bail.
        if repo_dir.exists() and any(repo_dir.iterdir()):
            raise WorkspaceError(f"Workspace {repo_dir} exists but is not a git repo")
        await _git("clone", cfg.clone_url, str(repo_dir))
        # Remove token from the stored remote URL.
        await _git("remote", "set-url", "origin", cfg.repo_url, cwd=repo_dir)

    # Token lives in the extraheader, not in .git/config — gets threaded
    # through on every fetch/push so it stays off disk.
    await _git(
        "-c",
        f"http.extraheader={cfg.http_extraheader}",
        "fetch",
        "origin",
        cwd=repo_dir,
    )

    # Ensure the draft branch exists locally; create from origin/draft or origin/base.
    branches = await _git("branch", "--list", cfg.draft_branch, cwd=repo_dir)
    if not branches.strip():
        # Is there a remote draft?
        remote_drafts = await _git(
            "branch", "-r", "--list", f"origin/{cfg.draft_branch}", cwd=repo_dir
        )
        if remote_drafts.strip():
            await _git(
                "checkout", "-b", cfg.draft_branch, f"origin/{cfg.draft_branch}", cwd=repo_dir
            )
        else:
            await _git(
                "checkout", "-b", cfg.draft_branch, f"origin/{cfg.base_branch}", cwd=repo_dir
            )
    else:
        await _git("checkout", cfg.draft_branch, cwd=repo_dir)

    # Fast-forward if remote has new commits.
    remote_ref = f"origin/{cfg.draft_branch}"
    remote_exists = await _git("branch", "-r", "--list", remote_ref, cwd=repo_dir)
    if remote_exists.strip():
        try:
            await _git("merge", "--ff-only", remote_ref, cwd=repo_dir)
        except WorkspaceError as exc:
            raise WorkspaceError(
                f"Draft branch has diverged from {remote_ref}. "
                f"Resolve manually before continuing. ({exc})"
            ) from exc

    return repo_dir


async def status_summary() -> dict:
    """Lightweight status probe — no clone, no checkout. Safe to call anytime."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        return {"configured": False, "error": str(exc)}

    repo_dir = workspace_root()
    cloned = (repo_dir / ".git").exists()
    branch: str | None = None
    head: str | None = None
    if cloned:
        try:
            branch = (await _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir)).strip()
            head = (await _git("rev-parse", "HEAD", cwd=repo_dir)).strip()
        except WorkspaceError:
            pass

    return {
        "configured": True,
        "repo_url": cfg.public_url,
        "owner": cfg.owner,
        "repo": cfg.repo,
        "draft_branch": cfg.draft_branch,
        "base_branch": cfg.base_branch,
        "workspace": str(repo_dir),
        "cloned": cloned,
        "current_branch": branch,
        "head": head,
    }
