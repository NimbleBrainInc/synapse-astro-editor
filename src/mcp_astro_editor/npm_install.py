"""Install Node.js dependencies for the cloned Astro repo.

Skipped if node_modules/ already exists or if there is no package.json. Uses
npm ci when a lockfile is present (deterministic, faster), npm install
otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


async def ensure_node_modules(repo_path: Path, timeout_s: float = 600.0) -> str:
    """Returns one of: "skipped", "already_installed", "installed"."""
    if not (repo_path / "package.json").exists():
        return "skipped"
    if (repo_path / "node_modules").exists():
        return "already_installed"

    has_lock = (repo_path / "package-lock.json").exists()
    cmd = ["npm", "ci"] if has_lock else ["npm", "install"]
    print(f"[npm] running `{' '.join(cmd)}` in {repo_path}", file=sys.stderr)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(repo_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CI": "1"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"npm install timed out after {timeout_s}s") from None

    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"npm install failed: {msg or stdout.decode(errors='replace')[-500:]}")
    return "installed"
