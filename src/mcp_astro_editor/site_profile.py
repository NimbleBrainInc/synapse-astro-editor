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
class CollectionInfo:
    """One Astro content collection — name + a sample of its entries.

    The schema (zod definitions, required fields, allowed types) is NOT
    parsed here; we surface the raw `src/content/config.ts` source on the
    parent profile so the agent reads the schema directly. Parsing TS into
    a structured form would lose information (zod transforms, refinements,
    custom validators) the agent would need anyway, so we don't try.
    """

    name: str
    entry_count: int
    sample_entries: list[str] = field(default_factory=list)


@dataclass
class SiteProfile:
    repo_path: str
    has_astro_config: bool = False
    has_content_config: bool = False
    base: str = "/"  # Astro `base` config — usually "/", but e.g. "/bayze-website"
    pages: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    layouts: list[str] = field(default_factory=list)
    # Plain list kept for backward compat — `collections` below carries the
    # richer per-collection info. Both populated; clients can pick either.
    content_collections: list[str] = field(default_factory=list)
    collections: list[CollectionInfo] = field(default_factory=list)
    # Raw `src/content/config.ts` (or `.mjs`/`.js`) source if present. The
    # agent reads this to learn each collection's zod schema — required
    # fields, value types, validators — and writes correct frontmatter on
    # the first try instead of probing via failed builds.
    content_config_source: str | None = None
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
        # `config.ts` itself isn't a collection — it's the schema definition.
        # The schema is surfaced separately via `content_config_source`.
        skip = {"config.ts", "config.mjs", "config.js", "config.mts"}
        for d in sorted(content_dir.iterdir(), key=lambda p: p.name):
            if not d.is_dir() or d.name in skip:
                continue
            entries = [
                p.name for p in d.iterdir()
                if p.is_file() and p.suffix in (".md", ".mdx", ".json", ".yaml", ".yml")
            ]
            entries.sort()
            profile.collections.append(
                CollectionInfo(
                    name=d.name,
                    entry_count=len(entries),
                    # Up to 5 entries — enough to demonstrate naming
                    # convention without bloating the per-turn context.
                    sample_entries=entries[:5],
                )
            )
        profile.content_collections = [c.name for c in profile.collections]

    profile.content_config_source = _read_content_config(repo_path)

    # TODO: parse astro.config.mjs to extract integrations
    # TODO: detect Tailwind / MDX / image-config flavors

    if not profile.has_astro_config:
        profile.notes.append("No astro.config detected — is this an Astro repo?")

    return profile


# Cap config source at ~8 KB. Real-world `config.ts` files are 1-3 KB; the
# cap is just defense against accidentally checked-in megabyte schemas.
_CONFIG_SOURCE_CAP = 8 * 1024


def _read_content_config(repo_path: Path) -> str | None:
    """Find and return the contents of the content collection config file."""
    candidates = [
        repo_path / "src" / "content" / "config.ts",
        repo_path / "src" / "content" / "config.mjs",
        repo_path / "src" / "content" / "config.js",
        repo_path / "src" / "content.config.ts",
        repo_path / "src" / "content.config.mjs",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > _CONFIG_SOURCE_CAP:
            return text[:_CONFIG_SOURCE_CAP] + "\n# ...truncated..."
        return text
    return None
