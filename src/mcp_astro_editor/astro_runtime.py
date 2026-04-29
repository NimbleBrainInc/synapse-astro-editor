"""Astro build + preview subprocess manager.

The editor uses `astro build` + `astro preview`, NOT `astro dev`. Reasoning:

  - Editor UX is "edit → publish", not "save and watch HMR." Build mode
    is the right architectural fit — no Vite dev runtime, no live module
    server, no need for HMR.
  - `astro dev` injects unprefixed `<script src="/@vite/client">` and
    `<script src="/src/styles/global.css">` paths that bypass `--base`
    config. When the bundle runs behind the platform's same-origin proxy,
    those paths 404 from the platform web origin → no Tailwind, broken
    preview. `astro build` bakes URLs in at build time, all correctly
    prefixed, no runtime injection.
  - `astro preview` is a minimal static server (Sirv); the output it serves
    is what production deploys, so the preview is a true preview.

Tradeoff: a build cycle takes 5–30s vs `astro dev`'s instant boot. We
rebuild on demand (initial boot + when `rebuild()` is called after edits).

Lifecycle hardening:
  - start_new_session=True puts astro (and any node sub-children) in their
    own process group, so we can kill the whole group on shutdown
  - Pre-flight checks the port; if a stale astro/node is squatting on it
    from a prior crashed run, SIGTERM it before spawning fresh
  - stop() kills the entire process group, not just the lead pid, so vite/
    rollup workers don't get orphaned
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_PORT = 4321
STARTUP_TIMEOUT_S = 30.0
HEALTH_INTERVAL_S = 0.5
BUILD_TIMEOUT_S = 180.0


@dataclass
class AstroRuntime:
    repo_path: Path
    port: int = DEFAULT_PORT
    process: asyncio.subprocess.Process | None = None
    base_override: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        """Build the site, then spawn `astro preview` and wait for HTTP ready.

        Build runs synchronously to completion before preview starts. If the
        build fails we don't start preview — the caller's boot phase reports
        the build error and the UI shows it.
        """
        if self.process and self.process.returncode is None:
            return

        # Reap any orphan from a prior crashed run sitting on our port.
        _kill_orphans_on_port(self.port)

        await self._build()
        await self._spawn_preview()
        await self._await_healthy()

    async def rebuild(self) -> None:
        """Re-run `astro build` and restart preview. Called when the workspace
        has new edits and the cached output is stale."""
        await self._build()
        await self.stop()
        await self._spawn_preview()
        await self._await_healthy()

    async def _build(self) -> None:
        cmd = ["npx", "astro", "build"]
        if self.base_override:
            cmd += ["--base", self.base_override]
        print(f"[astro] building: {' '.join(cmd)} (cwd={self.repo_path})", file=sys.stderr)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.repo_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "FORCE_COLOR": "0", "CI": "1"},
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=BUILD_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise RuntimeError(f"astro build timed out after {BUILD_TIMEOUT_S}s") from None
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", errors="replace").strip().splitlines()[-20:]
            raise RuntimeError("astro build failed:\n" + "\n".join(tail))
        print(
            f"[astro] build ok ({len(stdout)} bytes stdout, {len(stderr)} bytes stderr)",
            file=sys.stderr,
        )

    async def _spawn_preview(self) -> None:
        cmd = ["npx", "astro", "preview", "--host", "127.0.0.1", "--port", str(self.port)]
        if self.base_override:
            cmd += ["--base", self.base_override]
        print(f"[astro] starting preview: {' '.join(cmd)} (cwd={self.repo_path})", file=sys.stderr)
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.repo_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "FORCE_COLOR": "0"},
            # Own process group so we can kill astro + its node children together.
            start_new_session=True,
        )

    async def _await_healthy(self) -> None:
        deadline = asyncio.get_event_loop().time() + STARTUP_TIMEOUT_S
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                if self.process and self.process.returncode is not None:
                    raise RuntimeError(
                        f"astro preview exited early with code {self.process.returncode}"
                    )
                try:
                    r = await client.get(self.base_url)
                    if r.status_code < 500:
                        print(f"[astro] healthy at {self.base_url}", file=sys.stderr)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(HEALTH_INTERVAL_S)
        raise TimeoutError(f"astro dev did not become healthy within {STARTUP_TIMEOUT_S}s")

    async def fetch_page(self, path: str = "/") -> tuple[str, str]:
        """Fetch a rendered page. Returns (html, content_type)."""
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("astro runtime is not running")
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text, r.headers.get("content-type", "text/html")

    async def fetch_asset(self, path: str) -> tuple[bytes, str]:
        """Fetch a single asset (CSS, JS, image). Returns (bytes, content_type)."""
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content, r.headers.get("content-type", "application/octet-stream")

    async def stop(self) -> None:
        if not self.process or self.process.returncode is not None:
            return
        _kill_process_group(self.process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
        except TimeoutError:
            _kill_process_group(self.process.pid, signal.SIGKILL)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except TimeoutError:
                pass
        print("[astro] stopped", file=sys.stderr)

    def stop_sync(self) -> None:
        """Synchronous shutdown — safe to call from atexit / signal handlers
        where we don't have an event loop to await on."""
        if not self.process or self.process.returncode is not None:
            return
        _kill_process_group(self.process.pid, signal.SIGTERM)
        # Give it a moment, then escalate.
        for _ in range(20):
            if self.process.returncode is not None:
                break
            try:
                pid, _ = os.waitpid(self.process.pid, os.WNOHANG)
                if pid != 0:
                    break
            except ChildProcessError:
                break
            import time

            time.sleep(0.1)
        else:
            _kill_process_group(self.process.pid, signal.SIGKILL)


def _kill_process_group(pid: int, sig: int) -> None:
    """SIGTERM/SIGKILL the entire process group led by `pid`. Falls back to a
    plain process kill on platforms without process groups."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _kill_orphans_on_port(port: int) -> None:
    """Pre-flight: if something's already squatting on our port from a prior
    crashed run, terminate it. We only do this if the port is bound and only
    SIGTERM (the OS will reject if it's not ours)."""
    if not _port_in_use(port):
        return
    print(f"[astro] port {port} already in use — sweeping orphans", file=sys.stderr)
    # macOS / Linux: shell out to lsof to find PIDs binding the port.
    try:
        import subprocess

        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3.0,
            stdin=subprocess.DEVNULL,
        )
        pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
        for pid in pids:
            print(f"[astro] killing orphan pid={pid} on port {port}", file=sys.stderr)
            _kill_process_group(pid, signal.SIGTERM)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"[astro] could not sweep port {port}: {exc}", file=sys.stderr)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0
