"""Shared session state for the Astro editor.

Single-process, single-tenant. The runtime handle, cached site profile, and
rendered-page cache live here so tools and the UI resource handler can share
them. The workspace path itself is owned by workspace.py — everything routes
through ensure_workspace().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .astro_runtime import AstroRuntime
    from .site_profile import SiteProfile


@dataclass
class Session:
    repo_path: Path | None = None
    runtime: AstroRuntime | None = None
    profile: SiteProfile | None = None
    last_preview_path: str = "/"
    rendered_pages: dict[str, str] = field(default_factory=dict)
    init_error: str | None = None
    # Boot progress state — populated by the background init task.
    boot_phase: str = "idle"   # idle | cloning | installing | starting | rendering | ready | failed
    boot_started_at: float = 0.0
    boot_finished_at: float = 0.0


SESSION = Session()
