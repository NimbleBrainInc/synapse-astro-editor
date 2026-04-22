"""Scan an Astro repo and produce a cached site profile.

The site profile is the agent's primary intelligence — it tells the model what
content collections exist, what pages and components are present, what
integrations are wired up. Loaded into context every turn.

Prototype scope: directory listings + presence checks + base-URL detection.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SiteProfile:
    repo_path: str
    has_astro_config: bool = False
    has_content_config: bool = False
    base: str = "/"  # Astro `base` config — usually "/", but e.g. "/bayze-website"
    pages: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    layouts: list[str] = field(default_factory=list)
    content_collections: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def root_path(self) -> str:
        """Path under which the site is served. Always starts and ends with /."""
        b = self.base.strip()
        if not b or b == "/":
            return "/"
        if not b.startswith("/"):
            b = "/" + b
        if not b.endswith("/"):
            b = b + "/"
        return b

    def to_dict(self) -> dict:
        d = asdict(self)
        d["root_path"] = self.root_path
        return d


def _list_relative(root: Path, sub: str, suffixes: tuple[str, ...]) -> list[str]:
    base = root / sub
    if not base.exists():
        return []
    return sorted(
        str(p.relative_to(root))
        for p in base.rglob("*")
        if p.is_file() and p.suffix in suffixes
    )


_BASE_RE = re.compile(r"""\bbase\s*:\s*['"]([^'"]+)['"]""")


def _detect_base(repo_path: Path) -> str:
    """Best-effort scrape of `base: '...'` from astro.config.{mjs,ts,js}."""
    for ext in ("mjs", "ts", "js"):
        cfg = repo_path / f"astro.config.{ext}"
        if not cfg.exists():
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _BASE_RE.search(text)
        if m:
            return m.group(1)
    return "/"


def scan(repo_path: Path) -> SiteProfile:
    profile = SiteProfile(repo_path=str(repo_path))

    profile.has_astro_config = any((repo_path / f"astro.config.{ext}").exists()
                                    for ext in ("mjs", "ts", "js"))
    profile.has_content_config = (repo_path / "src" / "content" / "config.ts").exists() or \
                                  (repo_path / "src" / "content.config.ts").exists()
    profile.base = _detect_base(repo_path)

    profile.pages = _list_relative(repo_path, "src/pages", (".astro", ".md", ".mdx"))
    profile.components = _list_relative(repo_path, "src/components", (".astro", ".tsx", ".jsx", ".vue", ".svelte"))
    profile.layouts = _list_relative(repo_path, "src/layouts", (".astro",))

    content_dir = repo_path / "src" / "content"
    if content_dir.exists():
        profile.content_collections = sorted(
            d.name for d in content_dir.iterdir() if d.is_dir()
        )

    # TODO: parse astro.config.mjs to extract integrations
    # TODO: parse src/content/config.ts to extract collection schemas
    # TODO: detect Tailwind / MDX / image-config flavors

    if not profile.has_astro_config:
        profile.notes.append("No astro.config detected — is this an Astro repo?")

    return profile
