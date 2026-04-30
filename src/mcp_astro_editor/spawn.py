"""Subprocess spawning helpers that translate platform-environment failures
into errors the user can act on.

The bundle calls out to several CLI tools at boot — `git`, `npm`, `npx`,
`astro` (via npx), `node`. When the host that's spawning the bundle
subprocess doesn't have one of these on PATH, Python raises
`FileNotFoundError(2, 'No such file or directory')` from
`asyncio.create_subprocess_exec`. The error code is correct but unhelpful:
it doesn't say which executable was missing, and "No such file or
directory" sounds like a config-file problem, not a missing CLI dep.

This helper wraps the spawn and re-raises as `MissingDependencyError`
with the executable name and a clear remediation hint, so the boot
phase shows something the operator can act on.
"""

from __future__ import annotations

import asyncio
from typing import Any


class MissingDependencyError(RuntimeError):
    """A required CLI tool isn't on PATH inside the bundle subprocess.

    Carries the missing executable name so the caller can surface a
    targeted error instead of a generic 'No such file or directory'.
    """

    def __init__(self, executable: str) -> None:
        self.executable = executable
        super().__init__(
            f"Required CLI '{executable}' is not installed in this environment. "
            f"This bundle shells out to it during boot — install it on the host "
            f"running the bundle subprocess (typically the platform's container image)."
        )


async def create_subprocess_exec(
    *cmd: str,
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Drop-in for `asyncio.create_subprocess_exec` that translates
    `FileNotFoundError` from a missing executable into
    `MissingDependencyError`.

    Other FileNotFoundError causes (a missing `cwd`, for instance) still
    raise as-is — Python's error message names the path in those cases,
    so they're already actionable.
    """
    if not cmd:
        raise ValueError("create_subprocess_exec requires at least one argument")
    try:
        return await asyncio.create_subprocess_exec(*cmd, **kwargs)
    except FileNotFoundError as exc:
        # `errno == 2` and `filename` matching the executable means the
        # PATH lookup failed. If `filename` points elsewhere (e.g. a
        # missing cwd directory), let the original error through.
        executable = cmd[0]
        if exc.filename in (None, executable):
            raise MissingDependencyError(executable) from exc
        raise
