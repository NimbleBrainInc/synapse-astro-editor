"""synapse-astro-editor MCP server.

Tool surface designed for non-technical users editing their own Astro site.
Each agent edit auto-commits to a draft branch; Publish squash-merges the
draft into the base branch and pushes. Per-change revert via list_pending_changes
+ revert_change.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import signal
import sys
import time
from importlib.resources import files
from typing import Any

from fastmcp import FastMCP

from . import flatten as flatten_mod
from . import npm_install as npm_mod
from . import site_profile as profile_mod
from . import ui as ui_mod
from . import workspace as ws_mod
from .astro_runtime import AstroRuntime
from .state import SESSION
from .tools import files as files_tools
from .tools import git_ops, github_api

mcp = FastMCP(
    "Astro Editor",
    instructions=(
        "Natural-language editor for an Astro website. The server clones the "
        "configured GitHub repo, runs astro build+preview locally, and serves "
        "a live preview through the platform's same-origin proxy. Edits "
        "auto-commit to the draft branch and rebuild the preview; publish "
        "squash-merges to the base branch.\n"
        "\n"
        "Before editing, read the skill://astro-editor/usage resource for "
        "tool selection, page-context usage, and the minimal-edit discipline "
        "this bundle expects."
    ),
)


SKILL_CONTENT = files("mcp_astro_editor").joinpath("SKILL.md").read_text()


@mcp.resource("skill://astro-editor/usage")
def astro_editor_skill() -> str:
    """How to use the Astro editor effectively: page-context-driven editing,
    minimal edits, build-failure recovery, publish flow."""
    return SKILL_CONTENT

_init_lock = asyncio.Lock()
_boot_task: asyncio.Task | None = None


# ─── Internal init ──────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    print(f"[boot] {msg}", file=sys.stderr, flush=True)


async def _boot_sequence() -> None:
    """Long-running: clone → npm install → astro dev → profile → initial render.
    Runs as a background task so tool calls return quickly. Updates SESSION
    phase so the UI can poll progress via get_workspace_status."""
    _log("sequence start")
    SESSION.boot_started_at = time.time()
    SESSION.boot_finished_at = 0.0
    SESSION.init_error = None

    try:
        SESSION.boot_phase = "cloning"
        _log("phase=cloning — calling ensure_workspace()")
        repo = await ws_mod.ensure_workspace()
        SESSION.repo_path = repo
        _log(f"workspace ready at {repo}")

        SESSION.boot_phase = "installing"
        _log("phase=installing — checking node_modules")
        result = await npm_mod.ensure_node_modules(repo)
        _log(f"npm install: {result}")

        SESSION.boot_phase = "scanning"
        _log("phase=scanning — site profile")
        SESSION.profile = profile_mod.scan(repo)

        SESSION.boot_phase = "starting"
        _log("phase=starting — astro dev")
        if SESSION.runtime is None:
            # Compose the base path astro should serve from so absolute URLs in
            # responses line up with the platform proxy route. If NB_PROXY_PREFIX
            # is set (e.g., "/v1/apps/synapse-astro-editor/preview"), join it
            # with the user's astro.config base (e.g., "/my-site") so
            # astro generates URLs like `/v1/apps/.../preview/my-site/foo`.
            proxy_prefix = (os.getenv("NB_PROXY_PREFIX") or "").rstrip("/")
            user_base = (SESSION.profile.base if SESSION.profile else "/").strip()
            if user_base and user_base != "/":
                user_base = "/" + user_base.strip("/")
            else:
                user_base = ""
            base_override = (proxy_prefix + user_base) or None

            runtime = AstroRuntime(repo_path=repo, base_override=base_override)
            await runtime.start()
            SESSION.runtime = runtime
            _log(f"astro base={base_override!r}")

        SESSION.boot_phase = "rendering"
        root = SESSION.profile.root_path if SESSION.profile else "/"
        _log(f"phase=rendering — initial {root}")
        try:
            await _render_and_cache(root)
        except Exception as exc:
            _log(f"initial render failed: {exc}")

        SESSION.boot_phase = "ready"
        _log("READY")
    except BaseException as exc:
        # Catch BaseException so we also see CancelledError, SystemExit, etc.
        SESSION.init_error = repr(exc)
        SESSION.boot_phase = "failed"
        import traceback
        _log(f"FAILED at phase={SESSION.boot_phase} err={exc!r}")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise
    finally:
        SESSION.boot_finished_at = time.time()
        _log(f"sequence finished, phase={SESSION.boot_phase}")


async def _ensure_boot_started() -> None:
    """Kick off _boot_sequence in a background task if one isn't running.
    Idempotent — safe to call from any tool."""
    global _boot_task
    if SESSION.boot_phase == "ready":
        return
    async with _init_lock:
        if _boot_task and not _boot_task.done():
            return
        if SESSION.boot_phase == "ready":
            return
        _boot_task = asyncio.create_task(_boot_sequence())


async def _ensure_ready() -> None:
    """Used by tools that need the workspace fully up. If not ready, raises
    so the tool errors cleanly — the UI surfaces the boot phase separately."""
    await _ensure_boot_started()
    if SESSION.boot_phase != "ready" or SESSION.repo_path is None:
        raise RuntimeError(
            f"Workspace still {SESSION.boot_phase}. "
            + (f"Error: {SESSION.init_error}" if SESSION.init_error else "Try again in a moment.")
        )


async def _render_and_cache(path: str = "/") -> int:
    """Render `path` via astro dev, flatten, cache. Returns byte size."""
    if SESSION.runtime is None:
        raise RuntimeError("Astro runtime not running.")
    html, _ = await SESSION.runtime.fetch_page(path)
    flat = await flatten_mod.flatten(html, SESSION.runtime.fetch_asset)
    SESSION.rendered_pages[path] = flat
    SESSION.last_preview_path = path
    return len(flat)


# ─── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
async def boot() -> dict[str, Any]:
    """Kick off the startup sequence (clone, npm install, start Astro, scan,
    initial render) and return immediately. The actual work runs in the
    background. Poll get_workspace_status to see progress."""
    print(
        f"[tool] boot called, current phase={SESSION.boot_phase}, "
        f"task_alive={_boot_task is not None and not _boot_task.done()}",
        file=sys.stderr,
        flush=True,
    )
    await _ensure_boot_started()
    print(
        f"[tool] boot returning, phase={SESSION.boot_phase}, "
        f"task_alive={_boot_task is not None and not _boot_task.done()}",
        file=sys.stderr,
        flush=True,
    )
    return {
        "phase": SESSION.boot_phase,
        "started_at": SESSION.boot_started_at,
        "init_error": SESSION.init_error,
    }


@mcp.tool()
async def get_workspace_status() -> dict[str, Any]:
    """Status probe: config, clone state, branch, runtime, boot phase,
    and the same-origin URL the UI should iframe for preview."""
    status = await ws_mod.status_summary()
    status["runtime"] = "running" if SESSION.runtime else "stopped"
    status["init_error"] = SESSION.init_error
    status["boot_phase"] = SESSION.boot_phase
    status["boot_started_at"] = SESSION.boot_started_at
    status["boot_finished_at"] = SESSION.boot_finished_at
    status["profile"] = SESSION.profile.to_dict() if SESSION.profile else None
    status["last_build_status"] = SESSION.last_build_status
    status["last_build_error"] = SESSION.last_build_error
    status["last_build_at"] = SESSION.last_build_at

    proxy_prefix = (os.getenv("NB_PROXY_PREFIX") or "").rstrip("/")
    if proxy_prefix and SESSION.profile:
        user_root = SESSION.profile.root_path  # e.g. "/my-site/" or "/"
        # Join: /v1/apps/.../preview + /my-site/  →  /v1/apps/.../preview/my-site/
        status["preview_url"] = (proxy_prefix + user_root).replace("//", "/")
    else:
        status["preview_url"] = None

    return status


@mcp.tool()
async def get_site_profile() -> dict:
    """Scan (or return cached scan of) the configured Astro repo."""
    await _ensure_ready()
    assert SESSION.profile is not None
    return SESSION.profile.to_dict()


@mcp.tool()
async def read_file(path: str) -> str:
    """Read a file from the repo workspace."""
    await _ensure_ready()
    return files_tools.read_file(path)


@mcp.tool()
async def edit_file(path: str, old_string: str, new_string: str) -> dict:
    """Replace the unique occurrence of old_string in the given file,
    auto-commit the change, and rebuild the preview so the iframe shows
    the edit on next reload."""
    await _ensure_ready()
    files_tools.edit_file(path, old_string, new_string)
    sha = await git_ops.auto_commit(f"edit {path}")
    rebuild_status = await _rebuild_preview()
    return {"path": path, "committed": sha is not None, "sha": sha, "preview": rebuild_status}


@mcp.tool()
async def write_file(path: str, content: str) -> dict:
    """Write a file in the repo workspace, auto-commit, and rebuild the
    preview so the iframe shows the edit on next reload."""
    await _ensure_ready()
    existed = (SESSION.repo_path / path).exists() if SESSION.repo_path else False
    files_tools.write_file(path, content)
    verb = "update" if existed else "create"
    sha = await git_ops.auto_commit(f"{verb} {path}")
    rebuild_status = await _rebuild_preview()
    return {"path": path, "committed": sha is not None, "sha": sha, "preview": rebuild_status}


@mcp.tool()
async def upload_asset(
    filename: str,
    base64_data: str,
    dest_dir: str = "public/uploads",
) -> dict:
    """Upload a binary asset (image, logo, icon) into the repo and
    auto-commit. Returns `{path, url, bytes}` — `path` is the
    repo-relative file location, `url` is the site-relative URL to use
    in markdown or JSX (e.g. `/uploads/hero.png`).

    Defaults to `public/uploads/`. Astro serves anything under `public/`
    at the site root, so the typical flow is: upload → reference by
    `url` in a `<img src=...>` or markdown `![alt](url)`.

    Size cap is the platform's tool-call JSON limit (~750 KB binary).
    For larger images, ask the user to resize before uploading.
    Filenames are sanitized — no path components, no leading dot,
    only letters/digits/dot/underscore/dash.
    """
    await _ensure_ready()
    result = files_tools.upload_asset(filename, base64_data, dest_dir)
    sha = await git_ops.auto_commit(f"add asset {result['path']}")
    rebuild_status = await _rebuild_preview()
    return {
        **result,
        "committed": sha is not None,
        "sha": sha,
        "preview": rebuild_status,
    }


@mcp.tool()
async def delete_file(path: str) -> dict:
    """Delete a single file from the repo workspace, auto-commit, and
    rebuild the preview. Use for removing blog posts, unused components,
    or any other file the user no longer wants. Directories are not
    supported — remove their contents file by file."""
    await _ensure_ready()
    files_tools.delete_file(path)
    sha = await git_ops.auto_commit(f"delete {path}")
    rebuild_status = await _rebuild_preview()
    return {"path": path, "committed": sha is not None, "sha": sha, "preview": rebuild_status}


@mcp.tool()
async def multi_edit_file(path: str, edits: list[dict]) -> dict:
    """Apply multiple {old_string, new_string} edits to a single file in
    one atomic write. Each `old_string` must match exactly once after
    prior edits in this call have been applied — same uniqueness rule
    as `edit_file`.

    Use this when you need to change several places in the same file:
    one auto-commit and one rebuild instead of N. Common cases:
    renaming a symbol used in multiple call sites, applying a consistent
    style tweak across a component, or batching frontmatter + body edits
    on a content collection entry.

    Each edit is `{"old_string": "...", "new_string": "..."}`. The list
    is applied in order; if any edit fails (no match, ambiguous match,
    or identical strings) the file is not touched.
    """
    await _ensure_ready()
    files_tools.multi_edit_file(path, edits)
    sha = await git_ops.auto_commit(f"multi-edit {path} ({len(edits)} change{'s' if len(edits) != 1 else ''})")
    rebuild_status = await _rebuild_preview()
    return {
        "path": path,
        "edits_applied": len(edits),
        "committed": sha is not None,
        "sha": sha,
        "preview": rebuild_status,
    }


async def _rebuild_preview() -> str:
    """Rebuild the astro preview after a file change.

    Why eager (rebuild on every edit) vs debounced:
      - The agent's edit and the visible-preview update are one logical step
        from the user's mental model. Coupling them surfaces build errors
        immediately at the call that caused them, so a malformed edit shows
        an error tied to that tool call rather than a later silent-stale
        preview.
      - Build is fast on warm caches (~3-8s) since astro-preview reuses the
        same node_modules + .astro cache. The bottleneck is the agent's
        own LLM latency, not our build.
      - Debouncing would require a background task + cancellation handling
        and would still need a sync rebuild before publish — adds complexity
        for marginal gain.

    Returns "rebuilt" / "skipped (runtime down)" / "failed: <message>".
    Never raises — a build failure shouldn't block the file edit from
    being committed; the user can fix the broken edit, retry, or revert.

    Also persists the result on SESSION (`last_build_*`) so the UI can
    render a banner without depending on the most recent tool result.
    Sets `last_build_status = "building"` on entry; polling on the UI
    side flips the rebuilding overlay on for agent-initiated edits the
    UI wouldn't otherwise see in flight.
    """
    if SESSION.runtime is None:
        # Runtime down isn't a build failure — leave last_build_status alone
        # so the UI doesn't flip from "ok" to "failed" on an unrelated edit.
        return "skipped (runtime down)"
    SESSION.last_build_status = "building"
    SESSION.last_build_error = None
    SESSION.last_build_at = time.time()
    try:
        await SESSION.runtime.rebuild()
        SESSION.last_build_status = "ok"
        SESSION.last_build_error = None
        SESSION.last_build_at = time.time()
        return "rebuilt"
    except Exception as exc:
        msg = str(exc)
        SESSION.last_build_status = "failed"
        SESSION.last_build_error = msg
        SESSION.last_build_at = time.time()
        # Surface the error in the response without crashing the bundle.
        return f"failed: {msg[:300]}"


@mcp.tool()
async def list_dir(path: str = ".") -> list[str]:
    """List entries in a directory within the repo workspace."""
    await _ensure_ready()
    return files_tools.list_dir(path)


@mcp.tool()
async def grep(pattern: str, path: str = ".") -> list[dict]:
    """Search for a regex pattern across the repo workspace."""
    await _ensure_ready()
    return files_tools.grep(pattern, path)


@mcp.tool()
async def render_preview(path: str = "/") -> dict:
    """Fetch a page from the internal Astro preview server, flatten its
    assets into a single HTML document, and cache it. Legacy path —
    the editor UI loads the live preview via the platform's http-proxy
    instead. Kept for hosts where http-proxy isn't available."""
    await _ensure_ready()
    bytes_ = await _render_and_cache(path)
    return {"path": path, "bytes": bytes_}


@mcp.tool()
async def get_preview_html(path: str | None = None) -> dict:
    """Return the cached flattened HTML for a previously rendered page.
    The UI uses this to populate the preview iframe via srcdoc."""
    target = path or SESSION.last_preview_path or "/"
    html = SESSION.rendered_pages.get(target) or ui_mod.load_preview()
    return {"path": target, "html": html}


@mcp.tool()
async def list_pending_changes() -> dict:
    """Return commits on the draft branch that haven't been published yet."""
    await _ensure_ready()
    cfg = ws_mod.load_config()
    base_ref = f"origin/{cfg.base_branch}"
    commits = await git_ops.list_commits_ahead(cfg.draft_branch, base_ref)
    return {
        "draft_branch": cfg.draft_branch,
        "base_branch": cfg.base_branch,
        "count": len(commits),
        "commits": commits,
    }


@mcp.tool()
async def list_changed_files() -> dict:
    """Return the *net* set of files the draft branch would introduce when
    merged into the base branch.

    A file edited five times in five commits counts once. A file added then
    deleted counts zero. This is the right answer to "what is about to
    ship" — different from `list_pending_changes`, which counts commits.

    Each file: {path, status} where status is "added" / "modified" /
    "deleted" / "renamed" / "type_changed" / "other".
    """
    await _ensure_ready()
    cfg = ws_mod.load_config()
    base_ref = f"origin/{cfg.base_branch}"
    files = await git_ops.list_changed_files(cfg.draft_branch, base_ref)
    return {
        "draft_branch": cfg.draft_branch,
        "base_branch": cfg.base_branch,
        "count": len(files),
        "files": files,
    }


@mcp.tool()
async def revert_change(sha: str) -> dict:
    """Revert a specific commit on the draft branch (creates a new revert
    commit) and rebuild the preview so the iframe shows the reverted state."""
    await _ensure_ready()
    new_sha = await git_ops.revert(sha)
    rebuild_status = await _rebuild_preview()
    return {"reverted": sha, "new_sha": new_sha, "preview": rebuild_status}


@mcp.tool()
async def revert_file_to_base(path: str) -> dict:
    """Revert a single file to whatever it looks like on the base branch,
    discarding every accumulated change to that path on the draft branch.

    Useful when the user wants to undo edits to one file without unwinding
    later edits to other files. The action is one new commit on the draft
    branch (`revert <path> to base`) — same shape as every other edit, so
    publish-time diff stays clean.

    File-state cases the underlying git op handles:
      - Modified on draft → checkout base's version.
      - Added on draft (didn't exist on base) → remove the file.
      - Deleted on draft (existed on base) → restore from base.
    """
    await _ensure_ready()
    cfg = ws_mod.load_config()
    base_ref = f"origin/{cfg.base_branch}"
    result = await git_ops.revert_file_to_base(path, base_ref)
    sha = await git_ops.auto_commit(f"revert {path} to base")
    rebuild_status = await _rebuild_preview()
    return {
        "path": path,
        "action": result["action"],
        "committed": sha is not None,
        "sha": sha,
        "preview": rebuild_status,
    }


@mcp.tool()
async def undo_last_change() -> dict:
    """Pop the most recent commit off the draft branch and rebuild the
    preview. Behaves like a stack pop, not a forward revert: the commit
    leaves the branch's history rather than getting a `revert-X` commit
    layered on top.

    Why pop and not revert: the draft branch is ephemeral until publish,
    and `git revert` creates a new forward commit that reverses the target.
    With revert, calling Undo twice produces `revert-X, revert-revert-X`
    — the second Undo puts X back instead of going further down the stack.
    Pop matches the user's mental model.

    For arbitrary mid-history reverts, use `revert_change(sha)` (which
    intentionally uses `git revert` and stacks a new commit) — pop only
    works at the top.
    """
    await _ensure_ready()
    cfg = ws_mod.load_config()
    base_ref = f"origin/{cfg.base_branch}"
    commits = await git_ops.list_commits_ahead(cfg.draft_branch, base_ref)
    if not commits:
        return {"reverted": None, "message": "Nothing to undo."}
    latest = commits[-1]
    # `commits` lists commits ahead of base, oldest first. After popping
    # the top, reset target is either the previous draft commit or the
    # base ref itself when popping the only remaining commit.
    target = commits[-2]["sha"] if len(commits) > 1 else base_ref
    await git_ops.reset_hard(target)
    rebuild_status = await _rebuild_preview()
    return {
        "reverted": latest["sha"],
        "message": latest["message"],
        "preview": rebuild_status,
    }


@mcp.tool()
async def get_publish_preview() -> dict:
    """Preview what `publish` is about to do without actually publishing.
    Returns the publish mode, the base/draft branches, the file-level diff
    against the base branch, and the commit list — everything the UI
    needs to render a "Confirm Publish" dialog with paraphrased scope.

    Read-only. Always safe to call.
    """
    await _ensure_ready()
    cfg = ws_mod.load_config()
    mode = (os.getenv("PUBLISH_MODE") or "ship").lower()
    base_ref = f"origin/{cfg.base_branch}"
    # Don't `fetch` here — preview is a read-only probe and we don't want
    # to hit the remote on every modal open. The caller of `publish` will
    # fetch and recompute fresh state at ship time.
    commits = await git_ops.list_commits_ahead(cfg.draft_branch, base_ref)
    files = await git_ops.list_changed_files(cfg.draft_branch, base_ref)
    return {
        "mode": mode,
        "draft_branch": cfg.draft_branch,
        "base_branch": cfg.base_branch,
        "commit_count": len(commits),
        "file_count": len(files),
        "files": files,
        "commits": commits,
        # Human-shaped paraphrase the UI can lift directly into a dialog
        # body, so the wording stays consistent regardless of caller.
        "summary": _publish_summary(mode, files, cfg.base_branch),
    }


def _publish_summary(mode: str, files: list[dict], base_branch: str) -> str:
    if not files:
        return "No pending changes — nothing to publish."
    file_count = len(files)
    noun = "file" if file_count == 1 else "files"
    if mode == "pr":
        return f"Push {file_count} changed {noun} as a PR against {base_branch}."
    return f"Ship {file_count} changed {noun} to {base_branch} — changes go live via your deploy."


@mcp.tool()
async def publish(message: str = "") -> dict:
    """Publish accumulated draft changes.

    Default mode `ship`: squash-merge draft into base, push base, reset draft
    to the new base HEAD. Customer's existing deploy pipeline takes over.

    Mode `pr`: push draft branch and open a PR against base (legacy review flow).
    """
    await _ensure_ready()
    cfg = ws_mod.load_config()
    mode = (os.getenv("PUBLISH_MODE") or "ship").lower()

    # Pick up any uncommitted edits as a final auto-commit before publishing.
    await git_ops.auto_commit("editor: pre-publish save")

    await git_ops.fetch()
    base_ref = f"origin/{cfg.base_branch}"
    commits = await git_ops.list_commits_ahead(cfg.draft_branch, base_ref)
    if not commits:
        return {"published": False, "reason": "no pending changes"}

    if mode == "pr":
        await git_ops.push(cfg.draft_branch)
        title = message or _summarize(commits)
        try:
            pr = await github_api.ensure_pr(
                cfg, title=title, body=_pr_body(commits)
            )
            return {
                "published": True,
                "mode": "pr",
                "pr": {"number": pr["number"], "url": pr["html_url"]},
                "commits": len(commits),
            }
        except Exception as exc:
            return {"published": True, "mode": "pr", "pr": {"error": str(exc)}}

    # mode == "ship" — squash-merge into base, push base, reset draft.
    return await _ship(cfg, commits, message)


async def _ship(cfg: ws_mod.RepoConfig, commits: list[dict], message: str) -> dict:
    summary = message or _summarize(commits)
    body = _pr_body(commits)
    full_message = f"{summary}\n\n{body}"

    # Make sure local base is up to date with origin.
    await git_ops.checkout(cfg.base_branch)
    try:
        await git_ops.reset_hard(f"origin/{cfg.base_branch}")
        new_sha = await git_ops.squash_merge_into(
            cfg.base_branch, cfg.draft_branch, full_message
        )
        await git_ops.push(cfg.base_branch)
        # Reset draft so it tracks the new base — clean slate for next round.
        await git_ops.checkout(cfg.draft_branch)
        await git_ops.reset_hard(cfg.base_branch)
        await git_ops.force_push(cfg.draft_branch)
    finally:
        # Always end up on the draft branch.
        try:
            await git_ops.checkout(cfg.draft_branch)
        except Exception:
            pass

    return {
        "published": True,
        "mode": "ship",
        "branch": cfg.base_branch,
        "sha": new_sha,
        "message": summary,
        "commits": len(commits),
    }


def _summarize(commits: list[dict]) -> str:
    if len(commits) == 1:
        return commits[0]["message"]
    return f"Update via Astro Editor ({len(commits)} changes)"


def _pr_body(commits: list[dict]) -> str:
    lines = [f"- {c['message']} ({c['short_sha']})" for c in commits]
    return "Changes:\n" + "\n".join(lines)


# ─── UI resources ──────────────────────────────────────────────────────────


def _ui_csp_meta() -> dict[str, Any]:
    """Build the `_meta.ui.csp` block for our UI resources.

    The editor shell needs to frame the platform-proxied preview URL and
    open a same-origin WebSocket back to it (Vite HMR). Both target the
    platform's own browser-facing origin, which the platform injects as
    NB_PUBLIC_ORIGIN at bundle startup.

    If NB_PUBLIC_ORIGIN isn't set (e.g., the host hasn't been configured),
    we omit the declaration entirely — the host will apply its restrictive
    default and the iframe just won't load. That's the right failure mode:
    visible, fixable by setting one env var.
    """
    public_origin = (os.getenv("NB_PUBLIC_ORIGIN") or "").rstrip("/")
    if not public_origin:
        return {}
    return {
        "ui": {
            "csp": {
                "frameDomains": [public_origin],
                "connectDomains": [public_origin],
            }
        }
    }


@mcp.resource(
    "ui://astro-editor/main",
    mime_type="text/html;profile=mcp-app",
    meta=_ui_csp_meta() or None,
)
def main_ui() -> str:
    html = ui_mod.load_main_ui()
    print(f"[ui] main resource served, {len(html)} bytes", file=sys.stderr)
    return html


@mcp.resource("ui://astro-editor/preview", mime_type="text/html;profile=mcp-app")
def preview_ui() -> str:
    html = ui_mod.load_preview()
    print(f"[ui] preview resource served, {len(html)} bytes", file=sys.stderr)
    return html


# ─── Entry points ───────────────────────────────────────────────────────────

app = mcp.http_app()


# ─── Shutdown hooks ─────────────────────────────────────────────────────────


def _shutdown_runtime() -> None:
    """Synchronously stop the astro subprocess. Called on every clean exit
    path: atexit, SIGTERM, SIGINT, SIGHUP. SIGKILL still leaks (no signal
    handler can catch it) — see _kill_orphans_on_port for the next-startup
    cleanup that handles that case."""
    if SESSION.runtime is not None:
        try:
            SESSION.runtime.stop_sync()
        except Exception as exc:
            print(f"[shutdown] runtime stop failed: {exc}", file=sys.stderr)


def _signal_handler(signum: int, _frame: object) -> None:
    print(f"[shutdown] received signal {signum}, stopping runtime", file=sys.stderr)
    _shutdown_runtime()
    # Re-raise the default handler so the process actually exits.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


atexit.register(_shutdown_runtime)
for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    try:
        signal.signal(_sig, _signal_handler)
    except (ValueError, OSError):
        # SIGHUP doesn't exist on Windows; signal.signal restricted in some contexts.
        pass


if __name__ == "__main__":
    print("Astro Editor MCP starting in stdio mode…", file=sys.stderr)
    mcp.run()
