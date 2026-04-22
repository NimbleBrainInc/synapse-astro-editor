"""Thin GitHub REST client for PR open/lookup.

Uses httpx directly — no gh CLI dependency. Authenticates via the PAT from
user_config.
"""

from __future__ import annotations

import httpx

from ..workspace import RepoConfig

_API = "https://api.github.com"


def _headers(cfg: RepoConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def find_open_pr(cfg: RepoConfig) -> dict | None:
    """Return the existing open PR for draft→base, or None."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{_API}/repos/{cfg.owner}/{cfg.repo}/pulls",
            params={
                "head": f"{cfg.owner}:{cfg.draft_branch}",
                "base": cfg.base_branch,
                "state": "open",
            },
            headers=_headers(cfg),
        )
        r.raise_for_status()
        items = r.json()
        return items[0] if items else None


async def create_pr(cfg: RepoConfig, title: str, body: str) -> dict:
    """Open a PR from draft_branch → base_branch."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_API}/repos/{cfg.owner}/{cfg.repo}/pulls",
            headers=_headers(cfg),
            json={
                "title": title,
                "head": cfg.draft_branch,
                "base": cfg.base_branch,
                "body": body,
                "draft": False,
            },
        )
        r.raise_for_status()
        return r.json()


async def ensure_pr(cfg: RepoConfig, title: str, body: str) -> dict:
    """Return the open PR if one exists, else create one."""
    existing = await find_open_pr(cfg)
    if existing:
        return existing
    return await create_pr(cfg, title, body)
